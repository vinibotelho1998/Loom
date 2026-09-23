from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Qt, Signal, Slot

from . import __version__
from . import caminhos as mod_caminhos
from . import coletor as mod_coletor
from . import config as mod_config
from . import ferramentas as mod_ferramentas
from . import perfis as mod_perfis
from . import pty_session as mod_pty
from . import sessao_salva as mod_salvas
from . import sessoes as mod_sessoes
from . import uso as mod_uso
from . import shells as mod_shells
from .pty_session import PtySession

# Entrega da saida. O ConPTY devolve pedacos minusculos, entao sem agrupar dariam quase
# cem travessias por segundo carregando 150 bytes cada, e cada travessia custa serializar
# e atravessar a ponte. Agrupamos pela TAXA: parado entrega rapido, pro eco da tecla ser
# imediato; despejando muito texto, espaca, porque ai o que importa e vazao.
FLUSH_MINIMO_MS = 12
FLUSH_MAXIMO_MS = 40
TAXA_PRA_AGRUPAR = 12 * 1024  # bytes por segundo
# tudo que a lista mostra fica sob observacao, senao a sessao volta a viver sem avisar
JANELA_DE_VIGIA = 24 * 3600
TETO_DE_VIGIA = 400
# o que cada shell ja mostrou, pra tela refeita redesenhar o terminal como estava
HISTORICO_POR_SESSAO = 512 * 1024  # caracteres
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
RAIZ_DADOS = mod_caminhos.RAIZ
# conversa vira argumento de um comando digitado no shell: so o formato de id passa
ID_DE_CONVERSA = re.compile(r"[\w-]{1,128}")

# como cada editor conhecido pula direto pra linha; o que nao esta aqui abre so o arquivo
EDITORES_COM_G = {"code", "code-insiders", "cursor", "codium", "vscodium", "windsurf"}
EDITORES_COM_DOIS_PONTOS = {"subl", "sublime_text", "zed"}
EDITORES_COM_N = {"notepad++"}


def _separar_editor(texto: str) -> tuple[str, str]:
    """O executavel e o resto do campo do editor. Aceita caminho com espaco, com ou sem aspas."""
    texto = texto.strip()
    if texto.startswith('"'):
        fim = texto.find('"', 1)
        if fim > 0:
            return texto[1:fim], texto[fim + 1 :].strip()
        return texto.strip('"'), ""
    if Path(texto).is_file():
        return texto, ""
    executavel, _, resto = texto.partition(" ")
    return executavel, resto.strip()


def linha_do_editor(editor: str, arquivo: str, linha: str) -> str | None:
    """A linha de comando que abre o arquivo no editor do campo, ou None se ele nao existe.

    Sem conferir, o cmd escondido falhava calado quando o editor nao estava instalado e nada
    abria. E o -g e do VS Code: o Notepad++ recebia e tentava abrir um arquivo chamado -g. O
    que a pessoa escreveu depois do executavel continua indo junto, antes do arquivo.
    """
    executavel, resto = _separar_editor(editor)
    if not executavel:
        return None
    achado = executavel if Path(executavel).is_file() else shutil.which(executavel)
    if not achado:
        return None
    nome = Path(achado).stem.lower()
    if linha and nome in EDITORES_COM_G:
        alvo = f'-g "{arquivo}:{linha}"'
    elif linha and nome in EDITORES_COM_DOIS_PONTOS:
        alvo = f'"{arquivo}:{linha}"'
    elif linha and nome in EDITORES_COM_N:
        alvo = f'-n{linha} "{arquivo}"'
    else:
        alvo = f'"{arquivo}"'
    return " ".join(parte for parte in (f'"{achado}"', resto, alvo) if parte)


def _e_a_pasta_pessoal(pasta: str) -> bool:
    def normal(caminho: str) -> str:
        return os.path.normcase(os.path.abspath(caminho)).rstrip("\\/")

    return normal(pasta) == normal(os.path.expanduser("~"))


# o que o Windows executa quando se abre pelo programa padrao, alem do PATHEXT da maquina
EXECUTADOS_AO_ABRIR = {
    ".bat", ".cmd", ".com", ".exe", ".msi", ".scr", ".pif", ".cpl", ".msc", ".hta", ".reg",
    ".lnk", ".url", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".py", ".pyw", ".pyz", ".jar",
}


def _roda_ao_abrir(caminho: str) -> bool:
    extensao = Path(caminho).suffix.lower()
    pathext = {e.strip().lower() for e in os.environ.get("PATHEXT", "").split(";") if e.strip()}
    return extensao in EXECUTADOS_AO_ABRIR or extensao in pathext


def _abrir_no_sistema(caminho: str) -> None:
    """Abre o arquivo com o programa que o Windows associa a ele."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(caminho))


class Bridge(QObject):
    """Ponte entre a tela e os ConPTY. Sem porta de rede, tudo pelo canal do Qt.

    A saida nao vai byte a byte pra tela. O shell cospe pedaco de 4 bytes e cada
    travessia ate o JS custa caro, entao juntamos o que chegou e mandamos em bloco
    a cada poucos milissegundos.
    """

    output = Signal(str, str)
    exited = Signal(str, int)
    mexeram = Signal()
    arquivosSoltos = Signal(str)
    notificacaoPedida = Signal(str, str, str)
    # clique na notificacao: a tela vai pra aba que avisou
    irParaAba = Signal(str)
    # o e-mail ou o plano de alguma conta chegou depois de a lista ja ter sido entregue
    perfisMudaram = Signal()
    _identidadeRespondida = Signal(str, float, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._perguntando: set[str] = set()
        self._identidadeRespondida.connect(self._guardar_identidade)
        self._sessions: dict[str, PtySession] = {}
        self._buffers: dict[str, list[str]] = {}
        # Os shells moram neste processo, nao na pagina. Se a tela cai e e refeita, cada aba
        # nova precisa achar o seu shell de volta, entao guardamos de onde ele nasceu e o fim
        # do que ele ja mostrou.
        self._origem: dict[str, dict] = {}
        self._historico: dict[str, deque[str]] = {}
        self._tamanho_historico: dict[str, int] = {}
        self._historico_cortado: set[str] = set()
        self._abas: list[dict] = []
        self.ultimo_pulso = 0.0
        self._volume_recente = 0
        self._marco_volume = time.monotonic()
        self._shells = mod_shells.descobrir()
        self._elevacao: dict | None = None
        self._sem_autoabrir = False

        # olho nos transcript: assim que o Claude escreve, a tela sabe, sem esperar ciclo
        self._vigia = QFileSystemWatcher(self)
        self._vigia.fileChanged.connect(self._mexeu)
        self._vigia.directoryChanged.connect(self._mexeu)
        self._aviso = QTimer(self)
        self._aviso.setSingleShot(True)
        self._aviso.setInterval(120)
        self._aviso.timeout.connect(self.mexeram)
        self._relistar = QTimer(self)
        self._relistar.setInterval(8000)
        self._relistar.timeout.connect(self._revisar_vigia)
        self._relistar.start()
        QTimer.singleShot(1500, self._revisar_vigia)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_MINIMO_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

    # -- catalogo -------------------------------------------------------

    @Slot(result=str)
    def listShells(self) -> str:
        return json.dumps([s.como_dict() for s in self._shells])

    @Slot(result=str)
    def elevacao(self) -> str:
        if self._elevacao is None:
            self._elevacao = mod_shells.elevacao()
        return json.dumps(self._elevacao)

    @Slot(result=str)
    def listProfiles(self) -> str:
        """Cadastro das contas. Rapido de proposito: a tela chama isto a cada menu aberto.

        O tamanho em disco NAO entra aqui. Ele custava uma varredura de milhares de arquivos
        por chamada e so a tela de Contas mostra o numero, entao virou o slot profileSize.
        De quem e o login tambem nao se pergunta aqui: vai o que esta guardado, e a pergunta
        ao CLI, quando a credencial mudou, roda ao fundo e avisa por perfisMudaram.
        """
        catalogo = {f.id: f.nome for f in mod_ferramentas.listar()}
        achados = []
        for p in mod_perfis.listar():
            identidade, velha = mod_perfis.identidade_guardada(p)
            if velha:
                if mod_ferramentas.por_id(p.ferramenta).comando:
                    self._perguntar_identidade(p)
                else:
                    identidade = {}
            achados.append(
                {
                    "id": p.id,
                    "nome": p.nome,
                    "cor": p.cor,
                    "ferramenta": p.ferramenta,
                    "ferramentaNome": catalogo.get(p.ferramenta, p.ferramenta),
                    "logado": mod_perfis.logado(p),
                    "configDir": str(p.config_dir),
                    "emprestada": p.emprestada,
                    "padrao": p.e_padrao,
                    "email": identidade.get("email", ""),
                    "plano": identidade.get("plano", ""),
                }
            )
        return json.dumps(achados)

    def _perguntar_identidade(self, perfil) -> None:
        """Pergunta ao CLI de quem e o login, fora da thread da tela.

        O `claude auth status` leva um segundo ou mais, e rodava aqui a cada menu aberto depois
        que o Claude renovava o token: a janela inteira congelava, terminais incluidos.
        """
        chave = str(perfil.config_dir)
        if chave in self._perguntando:
            return
        self._perguntando.add(chave)

        def perguntar() -> None:
            resposta = mod_perfis.perguntar_identidade(perfil)
            carimbo, resumo = resposta if resposta is not None else (0.0, None)
            try:
                self._identidadeRespondida.emit(chave, carimbo, json.dumps(resumo) if resumo is not None else "")
            except RuntimeError:
                # o app fechou enquanto o CLI respondia
                pass

        threading.Thread(target=perguntar, name="identidade", daemon=True).start()

    def _guardar_identidade(self, chave: str, carimbo: float, bruto: str) -> None:
        # de volta na thread da tela: so ela mexe no cache e no arquivo dele
        self._perguntando.discard(chave)
        if not bruto:
            return
        try:
            resumo = json.loads(bruto)
        except ValueError:
            return
        mod_perfis.guardar_identidade(chave, carimbo, resumo)
        self.perfisMudaram.emit()

    @Slot(result=str)
    def sobre(self) -> str:
        """Versao e pasta de dados de verdade, pra tela Sobre nao mostrar numero parado."""
        return json.dumps({"versao": __version__, "raiz": str(mod_caminhos.RAIZ)})

    @Slot(str, result=bool)
    def temFilhos(self, session_id: str) -> bool:
        """Se o shell daquela aba esta rodando algum programa. Na duvida, diz que sim."""
        sessao = self._sessions.get(session_id)
        if sessao is None or not sessao.alive:
            return False
        resposta = mod_pty.tem_filhos(sessao.pid)
        return True if resposta is None else resposta

    @Slot(str, result=float)
    def profileSize(self, perfil_id: str) -> float:
        perfil = mod_perfis.por_id(perfil_id)
        return float(mod_perfis.tamanho(perfil)) if perfil is not None else 0.0

    @Slot(str, str, result=str)
    def salvarColagem(self, base64_dados: str, extensao: str) -> str:
        """Grava uma imagem da area de transferencia e devolve o caminho.

        Terminal nao recebe imagem, recebe texto. O que o Claude entende e o caminho de um
        arquivo, entao colar uma imagem vira gravar o arquivo e digitar o caminho dele.
        """
        try:
            bruto = base64.b64decode(base64_dados, validate=True)
        except (ValueError, TypeError):
            return ""
        if not bruto or len(bruto) > 25 * 1024 * 1024:
            return ""

        pasta = RAIZ_DADOS / "colagens"
        pasta.mkdir(parents=True, exist_ok=True)
        # limpa o que ficou de dias anteriores pra pasta nao virar deposito
        limite = time.time() - 7 * 24 * 3600
        for antigo in pasta.glob("*"):
            try:
                if antigo.stat().st_mtime < limite:
                    antigo.unlink()
            except OSError:
                pass

        sufixo = "".join(c for c in extensao if c.isalnum()) or "png"
        alvo = pasta / f"colagem-{int(time.time() * 1000)}.{sufixo}"
        try:
            alvo.write_bytes(bruto)
        except OSError:
            return ""
        return str(alvo)

    @Slot(result=str)
    def lerAreaDeTransferencia(self) -> str:
        """Le a area de transferencia pelo lado do Qt.

        O navigator.clipboard da pagina depende de o documento estar em foco e de
        permissao, e quando qualquer uma das duas falha o Ctrl+V nao cola nada. Aqui
        nao ha essa dependencia. Devolve {"tipo": "texto"|"imagem"|"", "valor": ...},
        onde valor e o texto ou o caminho do arquivo gravado.
        """
        from PySide6.QtGui import QGuiApplication

        area = QGuiApplication.clipboard()
        if area is None:
            return json.dumps({"tipo": "", "valor": ""})

        dados = area.mimeData()
        if dados is not None and dados.hasImage():
            imagem = area.image()
            if not imagem.isNull():
                caminho = self._gravar_imagem(imagem)
                if caminho:
                    return json.dumps({"tipo": "imagem", "valor": caminho})

        # arquivo copiado no Explorer vira o caminho dele, igual ao arrastar pra dentro
        if dados is not None and dados.hasUrls():
            locais = [u.toLocalFile() for u in dados.urls() if u.isLocalFile()]
            locais = [str(Path(c)) for c in locais if c]
            if locais:
                return json.dumps({"tipo": "arquivos", "valor": locais})

        texto = area.text() or ""
        return json.dumps({"tipo": "texto" if texto else "", "valor": texto})

    def _gravar_imagem(self, imagem) -> str:
        from PySide6.QtCore import QBuffer, QIODevice

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not imagem.save(buffer, "PNG"):
            return ""
        bruto = bytes(buffer.data())
        buffer.close()
        return self.salvarColagem(base64.b64encode(bruto).decode("ascii"), "png")

    @Slot(str, str, str)
    def notificar(self, titulo: str, texto: str, aba: str) -> None:
        self.notificacaoPedida.emit(titulo or "Loom", texto or "", aba or "")

    @Slot(str, str, result=str)
    def abrirExterno(self, alvo: str, pasta: str) -> str:
        """Abre link e caminho de arquivo fora do app. Devolve o motivo quando nao da.

        URL vai pro navegador de sempre. Caminho com numero de linha, que e o que o Claude Code
        imprime o tempo todo, vai pro editor configurado. O caminho impresso costuma ser
        relativo, entao ele e resolvido a partir da pasta da sessao daquela aba.
        """
        alvo = (alvo or "").strip().strip("\"'")
        if not alvo:
            return ""

        if alvo.startswith(("http://", "https://")):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(alvo))
            return ""

        # caminho:linha ou caminho:linha:coluna, sem confundir com a letra do drive
        casado = re.match(r"^(.+?):(\d+)(?::\d+)?$", alvo)
        caminho, linha = (casado.group(1), casado.group(2)) if casado else (alvo, "")

        arquivo = Path(caminho)
        if not arquivo.is_absolute() and pasta:
            arquivo = Path(pasta) / caminho
        try:
            existe = arquivo.exists()
        except OSError:
            existe = False
        if not existe:
            return f"Não achei {caminho}"

        editor = (mod_config.carregar().get("editor") or "").strip()
        comando = linha_do_editor(editor, str(arquivo), linha) if editor else None
        if comando is None:
            if _roda_ao_abrir(str(arquivo)):
                # abrir no programa padrao e executar: o tests/x.py:42 clicado rodaria o script
                inicio = f"Não achei o editor {editor}. " if editor else ""
                return f"{inicio}Não abri {arquivo.name}: no programa padrão o Windows executaria o arquivo"
            _abrir_no_sistema(str(arquivo))
            return f"Não achei o editor {editor}, abri no programa padrão" if editor else ""

        try:
            subprocess.Popen(
                comando,
                shell=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, ValueError):
            return f"Não consegui abrir o {editor}"
        return ""

    @Slot(result=str)
    def getHome(self) -> str:
        return os.path.expanduser("~")

    @Slot(result=str)
    def listTools(self) -> str:
        # se o comando existe no PATH: a tela dizia que o Claude estava confirmado em qualquer
        # maquina, e conta de ferramenta nao instalada so mostrava o erro do PowerShell depois
        achadas = []
        for f in mod_ferramentas.listar():
            dados = f.como_dict()
            primeiro = f.comando.split()[0] if f.comando.strip() else ""
            dados["achado"] = bool(primeiro and shutil.which(primeiro))
            achadas.append(dados)
        return json.dumps(achadas)

    @Slot(str, result=str)
    def saveTool(self, bruto: str) -> str:
        try:
            dados = json.loads(bruto)
        except ValueError:
            return "{}"
        return json.dumps(mod_ferramentas.salvar(dados).como_dict())

    @Slot(result=str)
    def savedTabs(self) -> str:
        return json.dumps(mod_salvas.ler() if mod_config.carregar().get("restaurarAbas") else [])

    @Slot(str)
    def saveTabs(self, bruto: str) -> None:
        """Guarda a ordem, o apelido e a ativa. Em memoria sempre, no disco se a pessoa quis."""
        try:
            dados = json.loads(bruto)
        except ValueError:
            return
        if not isinstance(dados, list):
            return
        self._abas = [a for a in dados if isinstance(a, dict)]
        if mod_config.carregar().get("restaurarAbas"):
            mod_salvas.gravar(dados)

    @Slot(result=str)
    def liveSessions(self) -> str:
        """Shells que continuam vivos, na ordem das abas, com o que cada um ja mostrou.

        E o que a tela pede ao nascer. Vazio no start normal; depois de a tela ser refeita,
        traz tudo que estava aberto, e a aba volta ligada ao mesmo shell em vez de subir outro.
        """
        # o que esta no buffer ainda nao entrou no historico e nao pode ficar pra tras
        self._flush()
        anotadas = {a.get("sessao"): a for a in self._abas if a.get("sessao")}
        ordem = [s for s in anotadas if s in self._sessions]
        ordem += [s for s in self._sessions if s not in anotadas]

        vivas = []
        for session_id in ordem:
            sessao = self._sessions[session_id]
            if not sessao.alive:
                continue
            nota = anotadas.get(session_id, {})
            cols, rows = sessao.tamanho
            vivas.append(
                {
                    **self._origem.get(session_id, {}),
                    "id": session_id,
                    "cols": cols,
                    "rows": rows,
                    "apelido": str(nota.get("apelido", "")),
                    "ativa": bool(nota.get("ativa")),
                    "historico": self._historico_de(session_id),
                }
            )
        return json.dumps(vivas)

    @Slot()
    def pulso(self) -> None:
        """A pagina avisa que desenhou um quadro. Sem aviso por um tempo, a vigia refaz a tela."""
        self.ultimo_pulso = time.monotonic()

    def _guardar_historico(self, session_id: str, texto: str) -> None:
        fila = self._historico.setdefault(session_id, deque())
        fila.append(texto)
        total = self._tamanho_historico.get(session_id, 0) + len(texto)
        while total > HISTORICO_POR_SESSAO and len(fila) > 1:
            saiu = fila.popleft()
            total -= len(saiu)
            # o pedaco que saiu fechava a linha? entao o historico ainda comeca numa linha inteira
            if saiu.endswith("\n"):
                self._historico_cortado.discard(session_id)
            else:
                self._historico_cortado.add(session_id)
        self._tamanho_historico[session_id] = total

    def _historico_de(self, session_id: str) -> str:
        texto = "".join(self._historico.get(session_id, ()))
        if session_id in self._historico_cortado:
            # o corte caiu no meio de uma linha, as vezes no meio de uma sequencia de cor;
            # comecar na linha seguinte evita lixo no topo do terminal redesenhado
            quebra = texto.find("\n")
            if quebra >= 0:
                texto = texto[quebra + 1 :]
        return texto

    def _esquecer(self, session_id: str) -> None:
        self._buffers.pop(session_id, None)
        self._origem.pop(session_id, None)
        self._historico.pop(session_id, None)
        self._tamanho_historico.pop(session_id, None)
        self._historico_cortado.discard(session_id)

    @Slot(str, str, int, int, str, result=str)
    def openSessionWith(self, shell_id: str, perfil_id: str, cols: int, rows: int, extra: str) -> str:
        """Abre a aba e, se vier pedido, entra na pasta e retoma a conversa de antes."""
        try:
            pedido_bruto = json.loads(extra) if extra else {}
        except ValueError:
            pedido_bruto = {}
        pedido = pedido_bruto
        conversa = str(pedido.get("conversa") or "")
        # fullmatch e nao match com $: o $ aceita uma quebra de linha no fim, que viraria Enter
        if conversa and not ID_DE_CONVERSA.fullmatch(conversa):
            conversa = ""
        entrar = bool(pedido.get("entrar"))

        # a pasta vai pro proprio ConPTY. Digitar `cd` depois de 700 ms deixava o comando
        # escrito na tela, competia com o prompt sendo desenhado e errava quando o shell
        # demorava a subir. Pasta que nao existe mais cai na de sempre em vez de derrubar a aba.
        pasta = str(pedido.get("pasta") or "")
        inicio = pasta if pasta and Path(pasta).is_dir() else None

        # retomar e entrar digitam o proprio comando, entao a abertura automatica do CLI sai
        self._sem_autoabrir = bool(conversa) or entrar
        try:
            session_id = self._abrir(
                shell_id, perfil_id, cols, rows, bool(pedido_bruto.get("elevar")), inicio
            )
        finally:
            self._sem_autoabrir = False

        if not conversa and not entrar:
            return session_id

        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        if conversa:
            # aba sem conta roda na pasta de login de sempre, que e a do Claude: a conversa da
            # conta principal nao cadastrada abria a aba na pasta e nao retomava nada
            ferramenta = mod_ferramentas.por_id(perfil.ferramenta if perfil is not None else "claude")
            if ferramenta.comando:
                self._digitar(session_id, f"{ferramenta.comando} --resume {conversa}", 700)
        elif perfil is not None:
            # o clique em Entrar foi explicito, entao o CLI sobe mesmo com a abertura automatica
            # desligada; antes o /login caia no PowerShell como comando inexistente
            for espera, texto in mod_ferramentas.passos_para_entrar(mod_ferramentas.por_id(perfil.ferramenta)):
                self._digitar(session_id, texto, espera)
        return session_id

    def _digitar(self, session_id: str, texto: str, espera_ms: int) -> None:
        """Digita na aba depois de um tempo, com o Enter no fim.

        O shell ainda esta subindo o perfil dele quando a aba nasce, entao digitar na hora
        se perde no meio do prompt sendo desenhado.
        """
        QTimer.singleShot(espera_ms, lambda: self.write(session_id, texto + chr(13)))

    @Slot(result=str)
    def getLimits(self) -> str:
        # uma leitura da pasta de sessoes por chamada, nao duas: as duas visoes saem do mesmo dado
        vistas = mod_coletor.ler_todas()
        return json.dumps(
            {
                "porConta": mod_coletor.limites_por_perfil(vistas),
                "porAba": mod_coletor.por_aba(vistas),
            }
        )

    @Slot(result=str)
    def getSessions(self) -> str:
        perfis = mod_perfis.listar()
        pastas = {k: str(v) for k, v in mod_uso.pastas_de(perfis).items()}
        return json.dumps(mod_sessoes.listar(pastas, mod_coletor.ler_todas()))

    @Slot(result=str)
    def getUsage(self) -> str:
        perfis = mod_perfis.listar()
        estado: dict = {}
        dados = mod_uso.relatorio(mod_uso.pastas_de(perfis), estado)
        # parcial: algum historico grande ainda nao foi lido inteiro, e a tela precisa dizer
        # que o numero vai subir em vez de mostrar menos que o real como se fosse medido
        return json.dumps({"porConta": dados, "parcial": bool(estado.get("parcial"))})

    @Slot(str, str, result=str)
    def createProfile(self, nome: str, ferramenta: str) -> str:
        p = mod_perfis.criar(nome, ferramenta or "claude")
        return json.dumps({"id": p.id, "nome": p.nome, "cor": p.cor, "ferramenta": p.ferramenta, "logado": False})

    @Slot(str, result=str)
    def adoptMainAccount(self, nome: str) -> str:
        """Cadastra a conta que ja estava logada antes do app, sem copiar credencial.

        O perfil aponta pra propria pasta dela, entao o login continua sendo um so e
        nao ha dois clientes disputando a renovacao do token.
        """
        pasta = mod_perfis.pasta_principal()
        for existente in mod_perfis.listar():
            if existente.emprestada and Path(existente.config_dir) == pasta:
                return json.dumps({"erro": "ja existe", "id": existente.id, "nome": existente.nome})
        p = mod_perfis.criar(nome or "Principal", "claude", pasta=str(pasta))
        return json.dumps({"id": p.id, "nome": p.nome, "cor": p.cor, "emprestada": True})

    @Slot(str, str, str)
    def updateProfile(self, perfil_id: str, nome: str, cor: str) -> None:
        mod_perfis.atualizar(perfil_id, nome or None, cor or None)

    @Slot(str, bool)
    def removeProfile(self, perfil_id: str, apagar_login: bool) -> None:
        mod_perfis.remover(perfil_id, apagar_login)

    @Slot(str, result=bool)
    def forgetLogin(self, perfil_id: str) -> bool:
        return mod_perfis.esquecer_login(perfil_id)

    @Slot(result=str)
    def getConfig(self) -> str:
        return json.dumps(mod_config.carregar())

    @Slot(str, result=str)
    def saveConfig(self, bruto: str) -> str:
        try:
            novo = json.loads(bruto)
        except ValueError:
            novo = {}
        return json.dumps(mod_config.salvar(novo))

    # -- sessoes --------------------------------------------------------

    @Slot(str, str, int, int, result=str)
    def openSession(self, shell_id: str, perfil_id: str, cols: int, rows: int) -> str:
        return self._abrir(shell_id, perfil_id, cols, rows, False)

    @Slot(str, str, int, int, bool, result=str)
    def openElevated(self, shell_id: str, perfil_id: str, cols: int, rows: int, elevar: bool) -> str:
        return self._abrir(shell_id, perfil_id, cols, rows, elevar)

    def _abrir(
        self,
        shell_id: str,
        perfil_id: str,
        cols: int,
        rows: int,
        elevar: bool,
        pasta: str | None = None,
    ) -> str:
        shell = next((s for s in self._shells if s.id == shell_id), None)
        if shell is None:
            shell = mod_shells.padrao(self._shells)

        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        session_id = uuid.uuid4().hex[:8]

        argv = [shell.caminho, *shell.args]
        if elevar:
            if self._elevacao is None:
                self._elevacao = mod_shells.elevacao()
            if self._elevacao.get("disponivel"):
                argv = mod_shells.elevado(shell, self._elevacao["comando"])

        env = {"LOOM_ABA": session_id}
        if perfil is not None:
            env.update(mod_perfis.ambiente(perfil))
            env["LOOM_PERFIL"] = perfil.id
            if perfil.ferramenta == "claude":
                ajustes = mod_config.carregar()
                mod_perfis.instalar_coletor(perfil, sys.executable, str(RAIZ_PROJETO))
                mod_perfis.herdar_confianca(perfil)
                # so a pasta onde a aba nasce, que e a que a pessoa escolheu abrir. Confiar na
                # pasta pessoal a cada aba liberava tudo que fosse clonado dentro dela, com os
                # ganchos e o MCP de cada projeto, sem a pergunta de confiar
                if ajustes.get("confiarPastas") and pasta and not _e_a_pasta_pessoal(pasta):
                    mod_perfis.confiar(perfil, pasta)
        session = PtySession(
            session_id,
            argv=argv,
            cwd=pasta,
            env=env,
            cols=cols,
            rows=rows,
            parent=self,
        )
        session.output.connect(self._on_output, Qt.QueuedConnection)
        session.exited.connect(self._on_exit, Qt.QueuedConnection)
        session.start()
        self._sessions[session_id] = session
        self._origem[session_id] = {
            "shell": shell.id,
            "perfil": perfil.id if perfil is not None else "",
            "pasta": pasta or "",
            "elevado": bool(elevar),
        }

        if perfil is not None and not self._sem_autoabrir and mod_config.carregar().get("abrirCliDaConta"):
            comando = mod_ferramentas.por_id(perfil.ferramenta).comando
            if comando:
                self._digitar(session_id, comando, 700)

        return session_id

    @Slot(str, str)
    def write(self, session_id: str, data: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.write(data)

    @Slot(str, int, int)
    def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.resize(cols, rows)

    @Slot(str)
    def closeSession(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        self._esquecer(session_id)
        if session is not None:
            session.stop()

    # -- interno --------------------------------------------------------

    @Slot(str, str)
    def _on_output(self, session_id: str, data: str) -> None:
        # o que ja estava na fila quando a aba fechou nao tem mais tela: entregue, caia na
        # fila de pendentes do javascript e ficava la pra sempre
        if session_id not in self._sessions:
            return
        self._buffers.setdefault(session_id, []).append(data)

    def _ritmo(self, volume: int) -> None:
        """Ajusta o intervalo pela taxa de saida do ultimo meio segundo."""
        agora = time.monotonic()
        self._volume_recente += volume
        if agora - self._marco_volume < 0.5:
            return
        taxa = self._volume_recente / (agora - self._marco_volume)
        self._volume_recente = 0
        self._marco_volume = agora

        alvo = FLUSH_MAXIMO_MS if taxa >= TAXA_PRA_AGRUPAR else FLUSH_MINIMO_MS
        if self._flush_timer.interval() != alvo:
            self._flush_timer.setInterval(alvo)

    def _flush(self) -> None:
        if not self._buffers:
            self._ritmo(0)
            return

        pendentes, self._buffers = self._buffers, {}
        volume = 0
        for session_id, pedacos in pendentes.items():
            texto = "".join(pedacos)
            volume += len(texto)
            self._guardar_historico(session_id, texto)
            self.output.emit(session_id, texto)
        self._ritmo(volume)

    @Slot(str, int)
    def _on_exit(self, session_id: str, code: int) -> None:
        self._flush()
        session = self._sessions.pop(session_id, None)
        self._esquecer(session_id)
        if session is not None:
            # o shell saiu, mas o pseudo console continua aberto enquanto alguem segura o PTY,
            # e o que o shell deixou rodando em segundo plano segue preso nele
            session.stop()
        self.exited.emit(session_id, code)

    def _mexeu(self, _caminho: str = "") -> None:
        """Junta rajadas de escrita num aviso so, senao a tela redesenha a cada linha."""
        self._aviso.start()

    def _revisar_vigia(self) -> None:
        """Mantem sob observacao os transcript que mexeram nas ultimas horas."""
        try:
            perfis = mod_perfis.listar()
            pastas = mod_uso.pastas_de(perfis)
        except OSError:
            return

        agora = time.time()
        alvos: set[str] = set()

        # a pasta do coletor tambem: e por ela que chega o nome novo depois de um /rename,
        # e o percentual do plano. Sem isso a tela so descobre no proximo ciclo do relogio.
        mod_coletor.PASTA.mkdir(parents=True, exist_ok=True)
        alvos.add(str(mod_coletor.PASTA))

        for pasta in pastas.values():
            raiz = Path(pasta) / "projects"
            if not raiz.is_dir():
                continue
            alvos.add(str(raiz))
            # so o nivel das conversas, o mesmo que a lista le: os transcript de subagente,
            # mais fundo, gastavam o teto de vigia e disparavam releitura que nao mudava nada
            for arquivo in raiz.glob("*/*.jsonl"):
                try:
                    idade = agora - arquivo.stat().st_mtime
                except OSError:
                    continue
                if idade > JANELA_DE_VIGIA:
                    continue
                alvos.add(str(arquivo))
                # a pasta do projeto tambem, senao conversa nova nao avisa que nasceu
                alvos.add(str(arquivo.parent))
                if len(alvos) >= TETO_DE_VIGIA:
                    break
            if len(alvos) >= TETO_DE_VIGIA:
                break

        atuais = set(self._vigia.files()) | set(self._vigia.directories())
        sobrando = atuais - alvos
        if sobrando:
            self._vigia.removePaths(list(sobrando))
        faltando = alvos - atuais
        if faltando:
            self._vigia.addPaths(list(faltando))

    def sessoes_vivas(self) -> int:
        return sum(1 for s in self._sessions.values() if s.alive)

    def shutdown(self) -> None:
        self._flush_timer.stop()
        self._relistar.stop()
        for session in list(self._sessions.values()):
            session.stop()
        self._sessions.clear()
        self._buffers.clear()
        self._origem.clear()
        self._historico.clear()
        self._tamanho_historico.clear()
        self._historico_cortado.clear()
