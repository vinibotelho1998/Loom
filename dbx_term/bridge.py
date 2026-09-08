from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Qt, Signal, Slot

from . import caminhos as mod_caminhos
from . import coletor as mod_coletor
from . import config as mod_config
from . import ferramentas as mod_ferramentas
from . import perfis as mod_perfis
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
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
RAIZ_DADOS = mod_caminhos.RAIZ


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
    notificacaoPedida = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: dict[str, PtySession] = {}
        self._buffers: dict[str, list[str]] = {}
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
        """
        catalogo = {f.id: f.nome for f in mod_ferramentas.listar()}
        achados = []
        for p in mod_perfis.listar():
            identidade = mod_perfis.identidade(p)
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

    @Slot(str, str)
    def notificar(self, titulo: str, texto: str) -> None:
        self.notificacaoPedida.emit(titulo or "DBX Term", texto or "")

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
        if not editor:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(arquivo)))
            return ""

        destino = f"{arquivo}:{linha}" if linha else str(arquivo)
        try:
            subprocess.Popen(
                f'{editor} -g "{destino}"',
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
        return json.dumps([f.como_dict() for f in mod_ferramentas.listar()])

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
        try:
            dados = json.loads(bruto)
        except ValueError:
            return
        if isinstance(dados, list):
            mod_salvas.gravar(dados)

    @Slot(str, str, int, int, str, result=str)
    def openSessionWith(self, shell_id: str, perfil_id: str, cols: int, rows: int, extra: str) -> str:
        """Abre a aba e, se vier pedido, entra na pasta e retoma a conversa de antes."""
        try:
            pedido_bruto = json.loads(extra) if extra else {}
        except ValueError:
            pedido_bruto = {}
        pedido = pedido_bruto
        conversa = pedido.get("conversa")

        # a pasta vai pro proprio ConPTY. Digitar `cd` depois de 700 ms deixava o comando
        # escrito na tela, competia com o prompt sendo desenhado e errava quando o shell
        # demorava a subir. Pasta que nao existe mais cai na de sempre em vez de derrubar a aba.
        pasta = str(pedido.get("pasta") or "")
        inicio = pasta if pasta and Path(pasta).is_dir() else None

        self._sem_autoabrir = bool(conversa)
        try:
            session_id = self._abrir(
                shell_id, perfil_id, cols, rows, bool(pedido_bruto.get("elevar")), inicio
            )
        finally:
            self._sem_autoabrir = False

        if not conversa and not pedido.get("entrar"):
            return session_id

        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        comando = ""
        if perfil is not None and conversa:
            ferramenta = mod_ferramentas.por_id(perfil.ferramenta).comando
            if ferramenta:
                comando = f"{ferramenta} --resume {conversa}"

        if comando:
            QTimer.singleShot(700, lambda: self.write(session_id, comando + chr(13)))

        if pedido.get("entrar"):
            # o /login e digitado dentro do CLI, entao precisa esperar ele terminar de subir
            QTimer.singleShot(4000, lambda: self.write(session_id, "/login" + chr(13)))
        return session_id

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
        dados = mod_uso.relatorio(mod_uso.pastas_de(perfis))
        return json.dumps(dados)

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

        env = {"DBX_TERM_ABA": session_id}
        if perfil is not None:
            env.update(mod_perfis.ambiente(perfil))
            env["DBX_TERM_PERFIL"] = perfil.id
            if perfil.ferramenta == "claude":
                ajustes = mod_config.carregar()
                mod_perfis.instalar_coletor(perfil, sys.executable, str(RAIZ_PROJETO))
                mod_perfis.herdar_confianca(perfil)
                if ajustes.get("confiarPastas"):
                    mod_perfis.confiar(perfil, os.path.expanduser("~"))
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

        if perfil is not None and not self._sem_autoabrir and mod_config.carregar().get("abrirCliDaConta"):
            comando = mod_ferramentas.por_id(perfil.ferramenta).comando
            if comando:
                # o shell ainda esta subindo o perfil dele, entao digitar na hora
                # se perde no meio do prompt sendo desenhado.
                QTimer.singleShot(700, lambda: self.write(session_id, comando + chr(13)))

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
        self._buffers.pop(session_id, None)
        if session is not None:
            session.stop()

    # -- interno --------------------------------------------------------

    @Slot(str, str)
    def _on_output(self, session_id: str, data: str) -> None:
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
            self.output.emit(session_id, texto)
        self._ritmo(volume)

    @Slot(str, int)
    def _on_exit(self, session_id: str, code: int) -> None:
        self._flush()
        self._sessions.pop(session_id, None)
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
            for arquivo in raiz.rglob("*.jsonl"):
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
