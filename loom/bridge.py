from __future__ import annotations

import base64
import json
import logging
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
from . import aviso_limite as mod_aviso
from . import caminhos as mod_caminhos
from . import coletor as mod_coletor
from . import config as mod_config
from . import ferramentas as mod_ferramentas
from . import historico_salvo as mod_historico
from . import motor as mod_motor
from . import painel_uso as mod_painel_uso
from . import perfis as mod_perfis
from . import pty_session as mod_pty
from . import queda as mod_queda
from . import sessao_salva as mod_salvas
from . import sessoes as mod_sessoes
from . import sessoes_codex as mod_sessoes_codex
from . import uso as mod_uso
from . import shells as mod_shells
from .cliente_nucleo import SessaoRemota
from .pty_session import PtySession

log = logging.getLogger(__name__)

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
# o aviso de limite olha as leituras de uso uma vez por minuto; a primeira, logo depois de subir
AVISO_DE_LIMITE_MS = 60000
PRIMEIRO_AVISO_MS = 20000
# varredura de sessoes que passa disto sem responder travou: a proxima sobe mesmo assim, senao uma
# leitura presa num arquivo deixava a lista parada ate fechar o app
VARREDURA_PRESA = 30.0
# o que cada shell ja mostrou, pra tela refeita redesenhar o terminal como estava
HISTORICO_POR_SESSAO = 512 * 1024  # caracteres
# O historico de cada aba vai pro disco ao fechar o app e, das que mudaram, neste intervalo: uma
# queda do app ou do Windows perde no maximo isso.
GRAVAR_HISTORICO_MS = 60000
# O historico guardado entra na memoria da aba nova em pedacos. Inteiro, ele era um pedaco so do
# tamanho do teto, e a primeira saida do shell novo empurrava ele todo pra fora de uma vez.
PEDACO_DA_SEMENTE = 8192
# o fim que a pagina escreveu depois do historico guardado: zera o estado, linha de Restaurado
TETO_DA_EMENDA = 512
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
RAIZ_DADOS = mod_caminhos.RAIZ
# conversa vira argumento de um comando digitado no shell: so o formato de id passa
ID_DE_CONVERSA = re.compile(r"[\w-]{1,128}")
# o shell nem chegou a subir: a aba mostra o fim com este codigo, e o Enter tenta de novo
CODIGO_SEM_SHELL = -1
# o que a pagina recebe enquanto a prova do sudo roda; a resposta de verdade vem por elevacaoMudou
ELEVACAO_PENDENTE = {"disponivel": False, "motivo": "verificando", "comando": ""}

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
    # o mesmo arrasto, com o ponto da pagina onde ele caiu: na grade, e o painel debaixo do mouse
    arquivosSoltosEm = Signal(str, float, float)
    notificacaoPedida = Signal(str, str, str)
    # clique na notificacao: a tela vai pra aba que avisou
    irParaAba = Signal(str)
    # o e-mail ou o plano de alguma conta chegou depois de a lista ja ter sido entregue
    perfisMudaram = Signal()
    _identidadeRespondida = Signal(str, float, str)
    # a prova do sudo roda fora da thread da tela, e a resposta chega depois de a pagina perguntar
    elevacaoMudou = Signal(str)
    _elevacaoRespondida = Signal(str)
    # a lista de sessoes, varrida fora da thread da tela a cada pedirSessoes
    sessoesProntas = Signal(str)
    _sessoesVarridas = Signal(str)
    # o relatorio do painel de uso, coletado fora da thread da tela
    painelUsoPronto = Signal(str)
    _usoColetado = Signal(str)

    def __init__(self, parent=None, nucleo=None):
        super().__init__(parent)
        # com o nucleo, os shells moram em outro processo e sobrevivem a esta tela
        self._nucleo = nucleo
        self._perguntando: set[str] = set()
        self._identidadeRespondida.connect(self._guardar_identidade)
        self._provando_sudo = False
        self._elevacaoRespondida.connect(self._guardar_elevacao)
        self._varrendo = False
        self._varrendo_desde = 0.0
        self._ultima_lista = "[]"
        self._sessoesVarridas.connect(self._entregar_sessoes)
        # Toda ligacao a um metodo da ponte nasce aqui, antes de o canal ler a ponte, e todo
        # metodo ligado e slot declarado. Ligado depois, um metodo sem @Slot vira slot novo na
        # hora, a numeracao dos sinais muda por baixo da pagina e ela passa a descartar todos,
        # a saida dos terminais inclusive.
        self._usoColetado.connect(self._receber_uso, Qt.QueuedConnection)
        self._sessions: dict[str, PtySession] = {}
        self._buffers: dict[str, list[str]] = {}
        # Os shells moram neste processo, nao na pagina. Se a tela cai e e refeita, cada aba
        # nova precisa achar o seu shell de volta, entao guardamos de onde ele nasceu e o fim
        # do que ele ja mostrou.
        self._origem: dict[str, dict] = {}
        self._historico: dict[str, deque[str]] = {}
        self._tamanho_historico: dict[str, int] = {}
        self._historico_cortado: set[str] = set()
        # quem mostrou algo desde a ultima ida ao disco
        self._historico_mudou: set[str] = set()
        self._abas: list[dict] = []
        self.ultimo_pulso = 0.0
        self._volume_recente = 0
        self._marco_volume = time.monotonic()
        self._shells = mod_shells.descobrir()
        self._elevacao: dict | None = None
        self._sem_autoabrir = False
        if nucleo is not None:
            self._abas = list(nucleo.abas_iniciais)
            for estado in nucleo.estado_inicial:
                self._adotar(estado)
            nucleo.terminar_adocao()

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

        self._limites = QTimer(self)
        self._limites.setInterval(AVISO_DE_LIMITE_MS)
        self._limites.timeout.connect(self._vigiar_limites)
        self._limites.start()
        QTimer.singleShot(PRIMEIRO_AVISO_MS, self, self._vigiar_limites)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_MINIMO_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

        self._relogio_do_historico = QTimer(self)
        self._relogio_do_historico.setInterval(GRAVAR_HISTORICO_MS)
        self._relogio_do_historico.timeout.connect(self._gravar_historicos_mudados)
        self._relogio_do_historico.start()

    # -- catalogo -------------------------------------------------------

    @Slot(result=str)
    def listShells(self) -> str:
        return json.dumps([s.como_dict() for s in self._shells])

    @Slot(result=str)
    def elevacao(self) -> str:
        """Se da pra abrir elevado na aba. Com a prova ainda rodando, diz que esta verificando.

        A prova roda o sudo --help, com ate 8 s de espera, e rodava aqui, na thread da tela, com a
        pagina subindo: a janela inteira parava. A resposta de verdade chega por elevacaoMudou.
        """
        if self._elevacao is None:
            self.provar_sudo()
            return json.dumps(ELEVACAO_PENDENTE)
        return json.dumps(self._elevacao)

    def provar_sudo(self) -> None:
        """Prova o sudo fora da thread da tela, uma vez so.

        O app chama ao subir, e a resposta fica guardada antes de a pagina perguntar.
        """
        if self._elevacao is not None or self._provando_sudo:
            return
        self._provando_sudo = True

        def provar() -> None:
            resposta = mod_shells.elevacao()
            try:
                self._elevacaoRespondida.emit(json.dumps(resposta))
            except RuntimeError:
                # o app fechou enquanto o sudo respondia
                pass

        threading.Thread(target=provar, name="sudo", daemon=True).start()

    @Slot(str)
    def _guardar_elevacao(self, bruto: str) -> None:
        # de volta na thread da tela, que e quem le a resposta guardada
        self._provando_sudo = False
        self._elevacao = json.loads(bruto)
        self.elevacaoMudou.emit(bruto)

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

    def _ocupadas(self) -> list[str]:
        """As abas cujo shell roda algum programa, com uma foto so dos processos pra todas.

        A elevada sempre conta: o que roda nela fica fora do alcance da foto, e o proprio sudo
        ja e filho do shell. Sem foto, na duvida, todas contam.
        """
        vivas = [(sid, s) for sid, s in self._sessions.items() if s.alive]
        processos = mod_pty._processos()
        ocupadas = []
        for session_id, sessao in vivas:
            if processos is None or not sessao.pid or self._origem.get(session_id, {}).get("elevado"):
                ocupadas.append(session_id)
            elif mod_pty._filhos_de(sessao.pid, processos):
                ocupadas.append(session_id)
        return ocupadas

    @Slot(result=str)
    def abasComPrograma(self) -> str:
        """Ids das abas com programa rodando no shell.

        E o que diz se o Claude daquela aba ainda existe: saindo dele com /exit ou Ctrl+C a
        aba seguia com o titulo, o ready e o contexto de uma conversa que ja tinha acabado.
        """
        return json.dumps(self._ocupadas())

    def sessoes_ocupadas(self) -> int:
        return len(self._ocupadas())

    @Slot(str, str, str, result=bool)
    def retomarNaAba(self, session_id: str, perfil_id: str, conversa: str) -> bool:
        """Retoma a conversa na propria aba, quando o shell dela esta parado no prompt.

        Retomar abria outra aba, e quem reabria o app com cinco conversas ficava com dez.
        Com programa rodando, ou sem dar pra saber, devolve False e a tela abre outra aba.
        """
        if not ID_DE_CONVERSA.fullmatch(conversa or ""):
            return False
        sessao = self._sessions.get(session_id)
        if sessao is None or not sessao.alive or self._origem.get(session_id, {}).get("elevado"):
            return False
        if mod_pty.tem_filhos(sessao.pid) is not False:
            return False
        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        ferramenta = mod_ferramentas.por_id(perfil.ferramenta if perfil is not None else "claude")
        if not ferramenta.comando:
            return False
        self._digitar(session_id, mod_ferramentas.comando_de_retomar(ferramenta, conversa), 0)
        return True

    @Slot(result=bool)
    def leituraPrincipal(self) -> bool:
        return mod_perfis.leitura_principal_ligada()

    @Slot(result=bool)
    def ligarLeituraPrincipal(self) -> bool:
        return mod_perfis.ligar_leitura_principal(sys.executable, str(RAIZ_PROJETO))

    @Slot(result=bool)
    def desligarLeituraPrincipal(self) -> bool:
        return mod_perfis.desligar_leitura_principal()

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
            # relativo, diz onde procurou: sem isso nao dava pra saber se o erro era do caminho
            # ou da pasta da aba
            if not Path(caminho).is_absolute() and pasta:
                return f"Não achei {caminho} em {pasta}"
            return f"Não achei {caminho}"

        editor = (mod_config.carregar().get("editor") or "").strip()
        comando = linha_do_editor(editor, str(arquivo), linha) if editor else None
        onde_trocar = " Escolha o editor em Configurações, Terminal, Editor dos links."
        if comando is None:
            if _roda_ao_abrir(str(arquivo)):
                # abrir no programa padrao e executar: o tests/x.py:42 clicado rodaria o script
                inicio = f"Não achei o editor {editor}. " if editor else ""
                return (
                    f"{inicio}Não abri {arquivo.name}: no programa padrão o Windows executaria o arquivo."
                    + onde_trocar
                )
            _abrir_no_sistema(str(arquivo))
            return f"Não achei o editor {editor}, abri no programa padrão." + onde_trocar if editor else ""

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
        """As abas guardadas e, com guardarHistorico ligado, o que cada uma mostrava."""
        ajustes = mod_config.carregar()
        if not ajustes.get("restaurarAbas"):
            return "[]"
        abas = mod_salvas.ler()
        if ajustes.get("guardarHistorico"):
            for aba in abas:
                if not isinstance(aba, dict):
                    continue
                salvo = mod_historico.ler(str(aba.get("sessao") or ""))
                if salvo is not None:
                    aba["historico"] = salvo["texto"]
                    aba["cols"] = salvo["cols"]
                    aba["rows"] = salvo["rows"]
                    aba["historicoEm"] = salvo["em"]
        return json.dumps(abas)

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
        if self._nucleo is not None:
            # a tela que nascer depois de uma queda acha as abas na mesma ordem e com o mesmo nome
            self._nucleo.guardar_abas(self._abas)
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
                    "conversa": str(nota.get("conversa") or ""),
                    "titulo": str(nota.get("titulo") or ""),
                    "historico": self._historico_de(session_id),
                }
            )
        return json.dumps(vivas)

    @Slot()
    def pulso(self) -> None:
        """A pagina avisa que desenhou um quadro. Sem aviso por um tempo, a vigia refaz a tela."""
        self.ultimo_pulso = time.monotonic()

    def _guardar_historico(self, session_id: str, texto: str) -> None:
        self._historico_mudou.add(session_id)
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
        self._historico_mudou.discard(session_id)

    def _pode_guardar_historico(self) -> bool:
        # sem restaurar abas, nada volta no proximo start, e o historico em disco seria so rastro
        ajustes = mod_config.carregar()
        return bool(ajustes.get("guardarHistorico") and ajustes.get("restaurarAbas"))

    @staticmethod
    def _tamanho_de(sessao) -> tuple[int, int]:
        try:
            cols, rows = sessao.tamanho
            return int(cols), int(rows)
        except (TypeError, ValueError):
            return 120, 30

    def _gravar_historicos(self, ids) -> list[str]:
        """Grava o historico das abas vivas daquela lista. Devolve as que foram pro disco."""
        gravados = []
        agora = time.time()
        for session_id in ids:
            sessao = self._sessions.get(session_id)
            if sessao is None or not sessao.alive:
                continue
            texto = self._historico_de(session_id)
            if not texto:
                continue
            cols, rows = self._tamanho_de(sessao)
            if mod_historico.gravar(session_id, texto, cols, rows, agora):
                gravados.append(session_id)
        return gravados

    @Slot()
    def _gravar_historicos_mudados(self) -> None:
        """So as abas que mostraram algo desde a ultima vez: as paradas ja estao no disco."""
        mudados, self._historico_mudou = self._historico_mudou, set()
        if not mudados or not self._pode_guardar_historico():
            return
        gravados = set(self._gravar_historicos(sorted(mudados)))
        # a que nao foi por falha de disco tenta de novo na proxima volta
        self._historico_mudou |= {s for s in mudados - gravados if s in self._sessions}

    def _semear(self, session_id: str, anterior: str, emenda: str) -> None:
        """A aba reaberta continua o historico da aba de antes.

        A pagina ja desenhou o historico guardado; aqui ele entra na memoria do shell novo, com o
        fim que a pagina escreveu depois dele, pra tela refeita e o proximo start mostrarem a mesma
        coisa. O arquivo passa a ter o nome do shell novo, e a continuidade vale por varias aberturas.
        """
        if not anterior or session_id not in self._sessions or not self._pode_guardar_historico():
            return
        salvo = mod_historico.ler(anterior)
        if salvo is None:
            return
        texto = salvo["texto"] + emenda
        for inicio in range(0, len(texto), PEDACO_DA_SEMENTE):
            self._guardar_historico(session_id, texto[inicio : inicio + PEDACO_DA_SEMENTE])
        mod_historico.renomear(anterior, session_id)

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
        anterior = str(pedido.get("anterior") or "")
        emenda = pedido.get("emenda") if isinstance(pedido.get("emenda"), str) else ""

        # a pasta vai pro proprio ConPTY. Digitar `cd` depois de 700 ms deixava o comando
        # escrito na tela, competia com o prompt sendo desenhado e errava quando o shell
        # demorava a subir. Pasta que nao existe mais cai na de sempre em vez de derrubar a aba.
        pasta = str(pedido.get("pasta") or "")
        inicio = pasta if pasta and Path(pasta).is_dir() else None

        # O comando que o shell ja nasce rodando. Retomar e entrar trazem o proprio, entao a
        # abertura automatica do CLI sai. A aba reaberta que tinha conversa tambem nao abre: nascia
        # um Claude em branco em cada uma, e a conversa de antes so voltava por outra aba
        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        comando = ""
        depois: list[tuple[int, str]] = []
        if conversa:
            # aba sem conta roda na pasta de login de sempre, que e a do Claude: a conversa da
            # conta principal nao cadastrada abria a aba na pasta e nao retomava nada
            # sem conta, a conversa do Codex da pasta padrao vem marcada pela tela; o resto e do Claude
            sem_conta = "codex" if pedido.get("cli") == "codex" else "claude"
            ferramenta = mod_ferramentas.por_id(perfil.ferramenta if perfil is not None else sem_conta)
            comando = mod_ferramentas.comando_de_retomar(ferramenta, conversa)
        elif entrar and perfil is not None:
            # o clique em Entrar foi explicito, entao o CLI sobe mesmo com a abertura automatica
            # desligada; antes o /login caia no PowerShell como comando inexistente. So o primeiro
            # passo vai pelo shell: o /login e de dentro do CLI e continua digitado depois
            passos = mod_ferramentas.passos_para_entrar(mod_ferramentas.por_id(perfil.ferramenta))
            if passos:
                comando, depois = passos[0][1], passos[1:]

        self._sem_autoabrir = bool(conversa) or entrar or bool(pedido.get("semCli"))
        try:
            session_id = self._abrir(
                shell_id, perfil_id, cols, rows, bool(pedido_bruto.get("elevar")), inicio, comando=comando
            )
        finally:
            self._sem_autoabrir = False

        if session_id in self._sessions:
            for espera, texto in depois:
                self._digitar(session_id, texto, espera)
            if conversa and session_id in self._origem:
                # a conversa retomada do Codex nasceu antes da aba; e por aqui que ela acha a aba
                self._origem[session_id]["conversa"] = conversa
        self._semear(session_id, anterior, emenda[:TETO_DA_EMENDA])
        return session_id

    def _adotar(self, estado: dict) -> None:
        """Liga a esta tela um shell que ja estava vivo no nucleo, com o que ele ja mostrou.

        E o caminho da tela que nasce depois de uma queda: a pagina pede as sessoes vivas, como
        pede depois de ser refeita, e cada aba volta ligada ao mesmo shell.
        """
        session_id = str(estado.get("id") or "")
        if not session_id or session_id in self._sessions:
            return
        origem = estado.get("origem") if isinstance(estado.get("origem"), dict) else {}
        sessao = SessaoRemota(
            self._nucleo,
            session_id,
            cols=estado.get("cols") or 120,
            rows=estado.get("rows") or 30,
            origem=origem,
            pid=estado.get("pid"),
            viva=True,
            parent=self,
        )
        sessao.output.connect(self._on_output, Qt.QueuedConnection)
        sessao.exited.connect(self._on_exit, Qt.QueuedConnection)
        self._sessions[session_id] = sessao
        self._origem[session_id] = dict(origem)
        historico = str(estado.get("historico") or "")
        if historico:
            self._guardar_historico(session_id, historico)
        self._nucleo.registrar(sessao)

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

    @Slot()
    def _vigiar_limites(self) -> None:
        """Avisa pelo Windows quando uma conta passa de 85% ou de 95% numa janela de uso."""
        if not mod_config.carregar().get("avisoDeLimite"):
            return
        nomes = {p.id: p.nome for p in mod_perfis.listar()}
        leituras = mod_aviso.leituras_do_claude(mod_coletor.limites_por_perfil(mod_coletor.ler_todas()), nomes)
        coleta = getattr(self, "_coleta_uso", None)
        if coleta is not None:
            leituras += mod_aviso.leituras_do_painel(coleta.get("painel") or {})
        avisos, vistos = mod_aviso.avisos_novos(leituras, mod_aviso.ler_vistos(), time.time())
        if not avisos:
            return
        mod_aviso.gravar_vistos(vistos)
        for aviso in avisos:
            log.info("aviso de limite: %s", aviso["texto"])
            self.notificacaoPedida.emit(aviso["titulo"], aviso["texto"], "")

    @Slot()
    def pedirSessoes(self) -> None:
        """Varre os transcript fora da thread da tela; a lista chega por sessoesProntas.

        A cauda de um transcript chega a 32 MB quando a ultima linha e o resultado de um Edit num
        arquivo grande, e essa leitura rodava aqui a cada 1,2 s com uma conversa trabalhando: a
        janela inteira parava, terminais incluidos. O pedido que chega com uma varredura andando
        fica com a resposta dela.
        """
        if self._varrendo and time.monotonic() - self._varrendo_desde < VARREDURA_PRESA:
            return
        # o cadastro e a pasta do coletor sao pequenos e ficam aqui: o cache do coletor tambem e
        # mexido pelo getLimits, nesta thread
        try:
            perfis = mod_perfis.listar()
            pastas = {k: str(v) for k, v in mod_uso.pastas_de(perfis).items()}
            vistas = mod_coletor.ler_todas()
        except Exception:  # noqa: BLE001 - sem resposta a tela fica esperando pra sempre
            # perfis.json no meio de uma gravacao, disco ocupado: a tela recebe a ultima lista boa
            # e pede de novo no proximo relogio, em vez de ficar com a lista parada
            log.warning("nao consegui preparar a varredura de sessoes", exc_info=True)
            self.sessoesProntas.emit(self._ultima_lista)
            return
        # O Codex vem a parte, com as abas vivas tiradas aqui, na thread da tela, que e quem mexe nelas.
        # Erro no Codex fica so no Codex: a lista do Claude sai do mesmo jeito.
        pastas_codex: dict[str, str] = {}
        abas_codex: list[dict] = []
        try:
            pastas_codex = mod_sessoes_codex.pastas_do_codex(perfis)
            notas = {a.get("sessao"): a for a in self._abas if a.get("sessao")}
            for session_id, sessao in self._sessions.items():
                if not sessao.alive:
                    continue
                aba = mod_sessoes_codex.aba_pra_parear(
                    session_id, self._origem.get(session_id, {}), notas.get(session_id, {}), pastas_codex
                )
                if aba is not None:
                    abas_codex.append(aba)
        except Exception:  # noqa: BLE001
            log.warning("nao consegui preparar a varredura do Codex", exc_info=True)
            pastas_codex = {}
        self._varrendo = True
        self._varrendo_desde = time.monotonic()

        anterior = self._ultima_lista

        def varrer() -> None:
            lista = anterior
            try:
                sessoes = mod_sessoes.listar(pastas, vistas)
                if pastas_codex:
                    try:
                        do_codex = mod_sessoes_codex.parear(mod_sessoes_codex.listar(pastas_codex), abas_codex)
                        sessoes = mod_sessoes_codex.juntar(sessoes, do_codex)
                    except Exception:  # noqa: BLE001 - rollout ilegivel nao apaga a lista do Claude
                        log.warning("nao consegui ler as sessoes do Codex", exc_info=True)
                lista = json.dumps(sessoes)
            finally:
                # com erro vai a ultima lista boa: sem resposta a tela esperava pra sempre e a lista
                # parava de andar, e a lista vazia apagava o estado de todos os cartoes. O erro
                # segue pro log pelo gancho das threads
                try:
                    self._sessoesVarridas.emit(lista)
                except RuntimeError:
                    # o app fechou no meio da varredura
                    pass

        threading.Thread(target=varrer, name="sessoes", daemon=True).start()

    @Slot(str)
    def _entregar_sessoes(self, lista: str) -> None:
        self._varrendo = False
        self._ultima_lista = lista
        self.sessoesProntas.emit(lista)

    # -- conversas da paleta --------------------------------------------
    # A paleta de comandos acha conversas de ate 30 dias, alem das 24 horas da lateral. A leitura vai
    # pra uma thread e o slot devolve na hora a ultima lista pronta, com coletando ligado enquanto a
    # nova nao chega, e a pagina pergunta de novo sozinha. Sem sinal novo, nada muda na ligacao dos
    # sinais que ja existem.

    # lista mais nova que isso volta direto, sem varrer o disco de novo a cada abertura da paleta
    CONVERSAS_FRESCAS = 20.0

    @Slot(result=str)
    def listarConversas(self) -> str:
        from . import conversas as mod_conversas

        estado = getattr(self, "_conversas", None)
        if estado is None:
            estado = self._conversas = {"lista": [], "em": 0.0, "linha": None}
        linha = estado["linha"]
        andando = linha is not None and linha.is_alive()
        if not andando and time.monotonic() - estado["em"] > self.CONVERSAS_FRESCAS:
            try:
                perfis = mod_perfis.listar()
                claude = {k: str(v) for k, v in mod_uso.pastas_de(perfis).items()}
                codex = {p.id: str(p.config_dir) for p in perfis if p.ferramenta == "codex"}
            except Exception:  # noqa: BLE001 - a paleta abre mesmo sem a lista nova
                log.warning("nao consegui preparar a lista de conversas", exc_info=True)
                return json.dumps({"coletando": False, "conversas": estado["lista"]})

            def varrer() -> None:
                try:
                    estado["lista"] = mod_conversas.listar(claude, codex)
                except Exception:  # noqa: BLE001 - fica a ultima lista boa
                    log.warning("nao consegui listar as conversas", exc_info=True)
                finally:
                    estado["em"] = time.monotonic()

            linha = estado["linha"] = threading.Thread(target=varrer, name="conversas", daemon=True)
            linha.start()
            andando = True
        return json.dumps({"coletando": andando, "conversas": estado["lista"]})

    @Slot(result=str)
    def ladosDaDivisao(self) -> str:
        """Que shell estava em cada lado da tela dividida, pela anotacao das abas em memoria.

        A anotacao vem inteira da pagina e fica aqui e no nucleo, entao a tela refeita reencontra a
        divisao junto com as abas. O abas.json do disco nao guarda isso: o app reaberto volta inteiro.
        """
        lados = {}
        for aba in self._abas:
            lado = aba.get("lado")
            if lado in ("esq", "dir") and aba.get("sessao"):
                lados[lado] = str(aba["sessao"])
        return json.dumps(lados)

    # -- uso e painel de uso --------------------------------------------
    # A aba Uso e o painel de uso leem o mesmo historico, numa thread so e fora da tela: a primeira
    # leitura dos transcript do Claude e dos rollouts do Codex leva segundos. Os dois slots devolvem
    # na hora o ultimo relatorio pronto e pedem uma coleta nova. O painel recebe o novo pelo
    # painelUsoPronto; a aba Uso pergunta de novo sozinha enquanto o relatorio vier parcial.

    # relatorio da aba Uso mais velho que isso volta como parcial, e a tela pergunta de novo em 1,5 s
    USO_VELHO = 60.0

    def _estado_do_uso(self) -> dict:
        """O estado da coleta de uso, criado no primeiro pedido, sempre na thread da tela."""
        estado = getattr(self, "_coleta_uso", None)
        if estado is None:
            estado = self._coleta_uso = {
                "trava": threading.Lock(),
                "pedido": None,
                "linha": None,
                "parar": threading.Event(),
                "painel": dict(mod_painel_uso.VAZIO),
                "uso": None,
                "usoEm": 0.0,
            }
        return estado

    @Slot(result=str)
    def getUsage(self) -> str:
        """O relatorio da aba Uso, no formato de sempre, e uma leitura nova ao fundo.

        A leitura dos transcript rodava aqui, na thread da tela, e segurava a janela e os terminais.
        Parcial diz que o numero ainda vai mudar: vem assim enquanto nao ha relatorio, quando o que
        ha e velho ou quando algum historico grande ainda nao foi lido inteiro, e a tela pergunta de
        novo em 1,5 s em vez de 30.
        """
        self._pedir_coleta_de_uso(mod_painel_uso.pedido_do_uso(mod_uso.pastas_de(mod_perfis.listar())))
        estado = self._estado_do_uso()
        relatorio = estado["uso"]
        if relatorio is None:
            return json.dumps({"porConta": {}, "parcial": True})
        velho = time.monotonic() - estado["usoEm"] > self.USO_VELHO
        return json.dumps(
            {"porConta": relatorio.get("porConta", {}), "parcial": bool(relatorio.get("parcial")) or velho}
        )

    @Slot(result=str)
    def getPainelUso(self) -> str:
        """O ultimo relatorio do painel de uso, na hora, e uma coleta nova ao fundo.

        O novo chega pelo painelUsoPronto. Antes da primeira coleta terminar vem o vazio com
        coletando ligado, como combinado com a tela.
        """
        estado = self._estado_do_uso()
        try:
            pedido = mod_painel_uso.pedido_do_painel(mod_perfis.listar())
        except OSError:
            log.warning("nao consegui montar o pedido do painel de uso", exc_info=True)
            return json.dumps(estado["painel"])
        self._pedir_coleta_de_uso(pedido)
        return json.dumps({**estado["painel"], "coletando": True})

    def _pedir_coleta_de_uso(self, pedido: dict) -> None:
        """Poe o pedido na fila e sobe a thread da coleta, se ela nao estiver rodando.

        O pedido que chega com uma coleta andando se junta ao que ja esperava e roda logo depois
        dela, uma vez so.
        """
        estado = self._estado_do_uso()
        with estado["trava"]:
            if estado["parar"].is_set():
                return
            estado["pedido"] = mod_painel_uso.juntar_pedidos(estado["pedido"], pedido)
            if estado["linha"] is not None:
                return
            linha = estado["linha"] = threading.Thread(target=self._coletar_uso, name="coleta-de-uso", daemon=True)
        linha.start()

    def _coletar_uso(self) -> None:
        estado = self._coleta_uso
        while True:
            with estado["trava"]:
                pedido = estado["pedido"]
                estado["pedido"] = None
                if pedido is None or estado["parar"].is_set():
                    estado["linha"] = None
                    return
            try:
                resultado = mod_painel_uso.coletar(pedido, estado["parar"])
            except Exception:  # noqa: BLE001 - a thread nao pode morrer e deixar a fila parada
                log.warning("a coleta de uso falhou", exc_info=True)
                resultado = {}
            with estado["trava"]:
                resultado["mais"] = estado["pedido"] is not None
            try:
                self._usoColetado.emit(json.dumps(resultado))
            except RuntimeError:
                # o app fechou no meio da coleta
                with estado["trava"]:
                    estado["linha"] = None
                return

    @Slot(str)
    def _receber_uso(self, bruto: str) -> None:
        # de volta na thread da tela: so ela troca o que os slots devolvem
        estado = self._estado_do_uso()
        try:
            resultado = json.loads(bruto)
        except ValueError:
            return
        if not isinstance(resultado, dict):
            return
        if isinstance(resultado.get("uso"), dict):
            estado["uso"] = resultado["uso"]
            estado["usoEm"] = time.monotonic()
        painel = resultado.get("painel")
        if isinstance(painel, dict):
            painel["coletando"] = bool(resultado.get("mais"))
            estado["painel"] = painel
            self.painelUsoPronto.emit(json.dumps(painel))

    def _parar_coleta_de_uso(self, espera: float = 5.0) -> None:
        """A coleta que esta andando para no proximo arquivo, e a fila e descartada."""
        estado = getattr(self, "_coleta_uso", None)
        if estado is None:
            return
        estado["parar"].set()
        with estado["trava"]:
            estado["pedido"] = None
            linha = estado["linha"]
        if linha is not None and linha is not threading.current_thread():
            linha.join(espera)

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

    @Slot(result=str)
    def desenho(self) -> str:
        """O motor de desenho desta subida, se foi por queda, e o da proxima vez que abrir."""
        return json.dumps(mod_motor.situacao(os.environ, mod_config.carregar()))

    @Slot(result=str)
    def voltarParaGpu(self) -> str:
        """As quedas ate aqui deixam de contar, e o automatico volta a placa na proxima subida."""
        try:
            mod_queda.voltar_para_gpu()
        except OSError:
            log.warning("nao consegui gravar a volta pra placa de video", exc_info=True)
        return self.desenho()

    @Slot()
    def gpuPerdida(self) -> None:
        """A pagina perdeu o contexto WebGL duas vezes em 2 s: a placa nao esta aguentando.

        Entra como queda, e no automatico a proxima subida ja vem por software.
        """
        log.warning("a pagina perdeu o contexto WebGL em sequencia; anotado como queda")
        mod_queda.registrar("contexto")

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
        comando: str = "",
    ) -> str:
        """Sobe o shell da aba. Com comando, ele ja nasce rodando o comando, pelo argv.

        Sem comando pedido, a conta com abertura automatica ligada traz o CLI dela. So a aba
        elevada e o shell que nao sabe rodar comando na subida voltam a digitar depois de 700 ms.
        """
        shell = next((s for s in self._shells if s.id == shell_id), None)
        if shell is None:
            shell = mod_shells.padrao(self._shells)

        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        session_id = uuid.uuid4().hex[:8]

        if not comando and perfil is not None and not self._sem_autoabrir and mod_config.carregar().get("abrirCliDaConta"):
            comando = mod_ferramentas.por_id(perfil.ferramenta).comando

        argv = [shell.caminho, *shell.args]
        elevada = False
        if elevar:
            if self._elevacao is None:
                self._elevacao = mod_shells.elevacao()
            if self._elevacao.get("disponivel"):
                argv = mod_shells.elevado(shell, self._elevacao["comando"])
                elevada = True
        digitar = ""
        if comando:
            montado = None if elevada else mod_shells.com_comando(shell, comando)
            if montado is not None:
                argv = montado
            else:
                digitar = comando

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
        origem = {
            "shell": shell.id,
            "perfil": perfil.id if perfil is not None else "",
            "pasta": pasta or "",
            "elevado": bool(elevar),
            # o rollout do Codex nao guarda a marca da aba: o pareamento olha se a conversa nasceu
            # depois de a aba abrir
            "aberta": time.time(),
        }
        if self._nucleo is not None:
            session = SessaoRemota(
                self._nucleo, session_id, argv=argv, cwd=pasta, env=env, cols=cols, rows=rows, origem=origem, parent=self
            )
        else:
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
        try:
            session.start()
        except OSError:
            # A falha escapava do slot, a tela recebia id vazio e a aba ficava viva sem shell,
            # engolindo tecla. Vai o id, e o fim chega logo depois dele, pra tela saber de quem e.
            log.exception("o shell %s nao subiu na aba %s", argv[0], session_id)
            session.deleteLater()
            QTimer.singleShot(0, self, lambda: self.exited.emit(session_id, CODIGO_SEM_SHELL))
            return session_id
        self._sessions[session_id] = session
        self._origem[session_id] = origem

        if digitar:
            self._digitar(session_id, digitar, 700)

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
        # aba fechada pela pessoa nao volta, e o que ela mostrava tambem nao fica no disco
        mod_historico.apagar(session_id)
        if session is not None:
            session.stop()
            # filha da ponte, ficava na memoria ate o app fechar, uma por aba que ja fechou
            session.deleteLater()

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
            session.deleteLater()
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

    def _guardar_ao_fechar(self) -> None:
        """Grava o historico de toda aba viva antes de os shells cairem, e limpa o que sobrou.

        Desligado, o que ja estava no disco sai junto: quem desligou nao quer o terminal guardado.
        """
        try:
            # o que esta no buffer ainda nao entrou no historico
            self._flush()
            if self._pode_guardar_historico():
                gravados = self._gravar_historicos(list(self._sessions))
                citados = {str(a.get("sessao")) for a in self._abas if a.get("sessao")}
                mod_historico.limpar(set(gravados) | citados)
            else:
                mod_historico.limpar(set(), idade_minima=0)
        except Exception:  # noqa: BLE001 - guardar e bonus; travar aqui deixaria os shells vivos
            log.warning("nao consegui guardar o historico das abas ao fechar", exc_info=True)

    def shutdown(self) -> None:
        self._parar_coleta_de_uso()
        self._flush_timer.stop()
        self._relistar.stop()
        self._limites.stop()
        self._relogio_do_historico.stop()
        self._guardar_ao_fechar()
        if self._nucleo is not None:
            # quem derruba os shells e o nucleo, que sai junto quando esta tela sair
            self._nucleo.encerrar()
        else:
            for session in list(self._sessions.values()):
                session.stop()
        self._sessions.clear()
        self._buffers.clear()
        self._origem.clear()
        self._historico.clear()
        self._tamanho_historico.clear()
        self._historico_cortado.clear()
        self._historico_mudou.clear()
