"""O nucleo do Loom: o processo que e dono dos shells.

A tela, com o motor web dentro, pode cair: o ANGLE do Chromium ja derrubou o Loom inteiro, e com ele
todo shell aberto e toda conversa do Claude que rodava ali. Agora quem abre os shells e este
processo, que sobe primeiro e sobe a tela como outro processo. Se ela cai, ele sobe outra, e a nova
acha as abas vivas e o que cada uma ja mostrou.

Aqui nao entra QtGui nem motor web: quanto menos coisa, menos motivo pra cair.
"""

from __future__ import annotations

import ctypes
import logging
import os
import secrets
import sys
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QProcessEnvironment, Qt, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import __version__
from . import instancia as mod_instancia
from . import protocolo as mod_protocolo
from . import pty_session as mod_pty

log = logging.getLogger(__name__)

# o que cada shell ja mostrou, pra tela nova redesenhar o terminal como estava
HISTORICO_POR_SESSAO = 512 * 1024  # caracteres
# Bytes esperando a tela ler. Passou disso, ela travou: o canal cai e a tela sabe que caiu, em vez
# de o nucleo guardar saida sem fim na memoria. O historico de cada aba continua aqui.
TETO_PENDENTE = 16 * 1024 * 1024
# a tela que cai sobe de novo na hora; se cair de novo, espera um pouco antes da proxima
ESPERAS_DE_RELANCE = (0, 2, 10)  # segundos
# mais que isso em dez minutos e a tela nao se aguenta de pe; o nucleo espera alguem chamar
TETO_DE_RELANCES = 4
JANELA_DE_RELANCES = 600.0
# a tela pediu pra encerrar e nao saiu: o nucleo sai assim mesmo, depois disso
PRAZO_PRA_TELA_SAIR = 15000  # ms
# uma segunda instancia chamando um nucleo que ainda esta abrindo o canal
PRAZO_PRA_CHAMAR = 10.0

_trava = None


def nome_do_canal() -> str:
    return mod_instancia.nome() + "-nucleo"


def comando_da_tela() -> list[str]:
    """Como subir a tela: o mesmo exe com --tela, ou o mesmo python com o run.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--tela"]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "run.py"), "--tela"]


def comando_do_nucleo() -> list[str]:
    """Como subir o Loom inteiro de novo: o mesmo exe sem argumento, ou o run.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "run.py")]


def segurar_trava(nome: str) -> bool:
    """A trava do nucleo deste usuario e desta instalacao. False enquanto outro nucleo a segura."""
    global _trava
    if sys.platform != "win32":
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k.WaitForSingleObject.restype = ctypes.c_uint32
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    if _trava is None:
        _trava = k.CreateMutexW(None, False, "Local\\" + nome)
        if not _trava:
            return True
    # o nucleo que morreu larga a trava, e ela chega aqui como abandonada, que conta como livre
    return k.WaitForSingleObject(_trava, 0) in (0x0, 0x80)


def chamar_o_nucleo(nome: str, prazo: float = PRAZO_PRA_CHAMAR) -> bool:
    """Pede ao nucleo aberto pra trazer a janela, ou subir a tela se ela nao estiver de pe."""
    if sys.platform == "win32":
        # o Windows so deixa a janela de outro processo tomar o foco se quem esta na frente liberar
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except (AttributeError, OSError):
            pass
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        conexao = QLocalSocket()
        conexao.connectToServer(nome)
        if conexao.waitForConnected(500):
            conexao.write(mod_protocolo.empacotar({"tipo": "mostrar"}))
            entregue = mod_protocolo.esvaziar(conexao, 2000)
            conexao.disconnectFromServer()
            return entregue
        # o nucleo segura a trava mas ainda nao abriu o canal: esta subindo agora
        time.sleep(0.2)
    return False


class Historico:
    """O fim do que um shell mostrou, ate o teto, comecando numa linha inteira."""

    def __init__(self, teto: int = HISTORICO_POR_SESSAO) -> None:
        self._fila: deque[str] = deque()
        self._total = 0
        self._cortado = False
        self._teto = teto

    def guardar(self, texto: str) -> None:
        self._fila.append(texto)
        self._total += len(texto)
        while self._total > self._teto and len(self._fila) > 1:
            saiu = self._fila.popleft()
            self._total -= len(saiu)
            # o pedaco que saiu fechava a linha? entao o historico ainda comeca numa linha inteira
            self._cortado = not saiu.endswith("\n")

    def texto(self) -> str:
        texto = "".join(self._fila)
        if self._cortado:
            # o corte caiu no meio de uma linha, as vezes no meio de uma sequencia de cor;
            # comecar na linha seguinte evita lixo no topo do terminal redesenhado
            quebra = texto.find("\n")
            if quebra >= 0:
                texto = texto[quebra + 1 :]
        return texto


def _inteiro(valor, padrao: int) -> int:
    try:
        return max(1, int(valor))
    except (TypeError, ValueError):
        return padrao


class Nucleo(QObject):
    """Os shells, o que cada um ja mostrou e de onde veio. So a thread principal mexe aqui."""

    saida = Signal(str, str)
    saiu = Signal(str, int)
    aberta = Signal(str, int)

    def __init__(self, fabrica=None, parent=None):
        super().__init__(parent)
        self._fabrica = fabrica or mod_pty.PtySession
        self._sessoes: dict = {}
        self._origem: dict[str, dict] = {}
        self._historico: dict[str, Historico] = {}
        # a ordem, o apelido e a ativa, como a tela guardou por ultimo
        self.abas: list[dict] = []

    def abrir(self, pedido: dict) -> None:
        session_id = str(pedido.get("id") or "")
        if not session_id or session_id in self._sessoes:
            return
        argv = [str(a) for a in pedido.get("argv") or []]
        env = {str(k): str(v) for k, v in (pedido.get("env") or {}).items()}
        sessao = self._fabrica(
            session_id,
            argv=argv or None,
            cwd=pedido.get("cwd") or None,
            env=env,
            cols=_inteiro(pedido.get("cols"), 120),
            rows=_inteiro(pedido.get("rows"), 30),
            parent=self,
        )
        sessao.output.connect(self._ao_mostrar, Qt.QueuedConnection)
        sessao.exited.connect(self._ao_sair, Qt.QueuedConnection)
        try:
            sessao.start()
        except OSError:
            log.exception("o shell %s nao subiu na aba %s", argv[:1], session_id)
            sessao.deleteLater()
            # fora daqui: quem pediu ainda nem terminou de mandar
            QTimer.singleShot(0, self, lambda: self.saiu.emit(session_id, -1))
            return
        self._sessoes[session_id] = sessao
        origem = pedido.get("origem")
        self._origem[session_id] = dict(origem) if isinstance(origem, dict) else {}
        self._historico[session_id] = Historico()
        self.aberta.emit(session_id, int(sessao.pid or 0))

    def escrever(self, session_id: str, texto: str) -> None:
        sessao = self._sessoes.get(session_id)
        if sessao is not None:
            sessao.write(texto)

    def medida(self, session_id: str, cols, rows) -> None:
        sessao = self._sessoes.get(session_id)
        if sessao is not None:
            sessao.resize(_inteiro(cols, 120), _inteiro(rows, 30))

    def fechar(self, session_id: str) -> None:
        sessao = self._sessoes.pop(session_id, None)
        self._esquecer(session_id)
        if sessao is not None:
            sessao.stop()
            sessao.deleteLater()

    def encerrar_tudo(self) -> None:
        for session_id in list(self._sessoes):
            self.fechar(session_id)

    def estado(self) -> list[dict]:
        vivas = []
        for session_id, sessao in self._sessoes.items():
            if not sessao.alive:
                continue
            cols, rows = sessao.tamanho
            vivas.append(
                {
                    "id": session_id,
                    "pid": int(sessao.pid or 0),
                    "cols": cols,
                    "rows": rows,
                    "origem": self._origem.get(session_id, {}),
                    "historico": self._historico[session_id].texto() if session_id in self._historico else "",
                }
            )
        return vivas

    def vivas(self) -> int:
        return sum(1 for s in self._sessoes.values() if s.alive)

    def _esquecer(self, session_id: str) -> None:
        self._origem.pop(session_id, None)
        self._historico.pop(session_id, None)

    @Slot(str, str)
    def _ao_mostrar(self, session_id: str, texto: str) -> None:
        historico = self._historico.get(session_id)
        # o que ja estava na fila quando a aba fechou nao tem mais pra onde ir
        if historico is None:
            return
        historico.guardar(texto)
        self.saida.emit(session_id, texto)

    @Slot(str, int)
    def _ao_sair(self, session_id: str, codigo: int) -> None:
        sessao = self._sessoes.pop(session_id, None)
        self._esquecer(session_id)
        if sessao is None:
            return
        # o shell saiu, mas o pseudo console segue aberto enquanto alguem segura o PTY, e o que o
        # shell deixou rodando em segundo plano fica preso nele
        sessao.stop()
        sessao.deleteLater()
        self.saiu.emit(session_id, codigo)


class ServidorDoNucleo(QObject):
    """O canal local da tela. So o mesmo usuario conecta, e so quem traz o token fala."""

    pedidoDeMostrar = Signal()
    encerrarPedido = Signal()
    telaConectou = Signal()

    def __init__(self, nucleo: Nucleo, nome: str, token: str, parent=None):
        super().__init__(parent)
        self._nucleo = nucleo
        self._token = token
        self._tela: QLocalSocket | None = None
        self._leitores: dict = {}
        self._servidor = QLocalServer(self)
        self._servidor.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._servidor.listen(nome):
            # sobrou o nome de um nucleo que morreu sem fechar; liberado, a segunda tentativa pega
            QLocalServer.removeServer(nome)
            if not self._servidor.listen(nome):
                raise OSError(f"o canal {nome} nao abriu: {self._servidor.errorString()}")
        self._servidor.newConnection.connect(self._conectou)
        nucleo.saida.connect(self._enviar_saida)
        nucleo.saiu.connect(self._enviar_saiu)
        nucleo.aberta.connect(self._enviar_aberta)

    def tem_tela(self) -> bool:
        return self._tela is not None and self._tela.state() == QLocalSocket.LocalSocketState.ConnectedState

    def mostrar_na_tela(self) -> bool:
        if not self.tem_tela():
            return False
        self._enviar({"tipo": "mostrar"})
        return True

    @Slot()
    def _conectou(self) -> None:
        while self._servidor.hasPendingConnections():
            conexao = self._servidor.nextPendingConnection()
            self._leitores[conexao] = mod_protocolo.Leitor()
            # slot do servidor e nao funcao solta: quando ele morre, o Qt desliga sozinho, e um aviso
            # atrasado da conexao nao chama mais um objeto que ja foi destruido
            conexao.readyRead.connect(self._ler)
            conexao.disconnected.connect(self._desconectou)

    @Slot()
    def _ler(self) -> None:
        self._processar(self.sender())

    def _processar(self, conexao) -> None:
        leitor = self._leitores.get(conexao)
        if leitor is None:
            return
        for mensagem in leitor.alimentar(bytes(conexao.readAll())):
            self._atender(conexao, mensagem)
        if leitor.estourou:
            log.warning("mensagem grande demais no canal do nucleo; conexao derrubada")
            conexao.abort()

    def _atender(self, conexao: QLocalSocket, mensagem: dict) -> None:
        tipo = mensagem["tipo"]
        if tipo == "mostrar":
            # uma segunda instancia chamando: so traz a janela, entao nao precisa de token
            self.pedidoDeMostrar.emit()
            return
        if conexao is not self._tela:
            if tipo == "ola" and secrets.compare_digest(str(mensagem.get("token", "")), self._token):
                antiga, self._tela = self._tela, conexao
                if antiga is not None:
                    antiga.disconnectFromServer()
                self._enviar({"tipo": "estado", "sessoes": self._nucleo.estado(), "abas": self._nucleo.abas})
                self.telaConectou.emit()
            # sem o ola certo, nada passa: nem abrir shell, nem ler a saida de ninguem
            return
        session_id = str(mensagem.get("id") or "")
        if tipo == "abrir":
            self._nucleo.abrir(mensagem)
        elif tipo == "escrever":
            self._nucleo.escrever(session_id, str(mensagem.get("texto") or ""))
        elif tipo == "medida":
            self._nucleo.medida(session_id, mensagem.get("cols"), mensagem.get("rows"))
        elif tipo == "fechar":
            self._nucleo.fechar(session_id)
        elif tipo == "abas":
            abas = mensagem.get("abas")
            self._nucleo.abas = [a for a in abas if isinstance(a, dict)] if isinstance(abas, list) else []
        elif tipo == "encerrar":
            self.encerrarPedido.emit()

    @Slot()
    def _desconectou(self) -> None:
        conexao = self.sender()
        if conexao is None:
            return
        self._leitores.pop(conexao, None)
        if conexao is self._tela:
            self._tela = None
        conexao.deleteLater()

    def _enviar(self, mensagem: dict) -> None:
        conexao = self._tela
        if conexao is None:
            return
        if conexao.bytesToWrite() > TETO_PENDENTE:
            log.warning("a tela parou de ler o canal; conexao derrubada, os shells seguem aqui")
            self._tela = None
            conexao.abort()
            return
        conexao.write(mod_protocolo.empacotar(mensagem))

    @Slot(str, str)
    def _enviar_saida(self, session_id: str, texto: str) -> None:
        self._enviar({"tipo": "saida", "id": session_id, "texto": texto})

    @Slot(str, int)
    def _enviar_saiu(self, session_id: str, codigo: int) -> None:
        self._enviar({"tipo": "saiu", "id": session_id, "codigo": codigo})

    @Slot(str, int)
    def _enviar_aberta(self, session_id: str, pid: int) -> None:
        self._enviar({"tipo": "aberta", "id": session_id, "pid": pid})


class Supervisor:
    """Decide o que fazer quando a tela sai. Sem Qt: o relogio vem de fora, e o teste manda nele."""

    FIM = "fim"
    ESPERAR = "esperar"

    def __init__(self, relogio=time.monotonic) -> None:
        self._relogio = relogio
        self._relances: list[float] = []
        # a tela pediu pra fechar tudo: quando ela sair, o nucleo sai junto
        self.encerrado = False

    def depois_que_a_tela_saiu(self, codigo: int, caiu: bool):
        """FIM, ESPERAR, ou quantos segundos esperar antes de subir a tela de novo."""
        if self.encerrado:
            return self.FIM
        if not caiu and codigo == 0:
            # saiu por conta propria sem pedir pra encerrar: logoff ou instalador fechando o Loom
            return self.FIM
        agora = self._relogio()
        self._relances = [t for t in self._relances if agora - t < JANELA_DE_RELANCES]
        if len(self._relances) >= TETO_DE_RELANCES:
            return self.ESPERAR
        espera = ESPERAS_DE_RELANCE[min(len(self._relances), len(ESPERAS_DE_RELANCE) - 1)]
        self._relances.append(agora)
        return float(espera)

    def pediu_pra_mostrar(self) -> None:
        # a pessoa chamou o Loom: o teto de tentativas era pra recuperacao sozinha, nao pra ela
        self._relances.clear()


class Hospedeiro(QObject):
    """Junta o nucleo, o canal e a tela, que roda como outro processo."""

    def __init__(self, comando: list[str], nome: str, token: str, fabrica=None, relogio=time.monotonic, parent=None):
        super().__init__(parent)
        self._comando = list(comando)
        self._nome = nome
        self._token = token
        self.nucleo = Nucleo(fabrica, parent=self)
        self.servidor = ServidorDoNucleo(self.nucleo, nome, token, parent=self)
        self.supervisor = Supervisor(relogio)
        self._processo: QProcess | None = None
        self._relance = QTimer(self)
        self._relance.setSingleShot(True)
        self._relance.timeout.connect(self.subir_tela)
        self.servidor.pedidoDeMostrar.connect(self._mostrar)
        self.servidor.encerrarPedido.connect(self._encerrar_pedido)

    def tela_de_pe(self) -> bool:
        return self._processo is not None and self._processo.state() != QProcess.ProcessState.NotRunning

    @Slot()
    def subir_tela(self) -> None:
        if self.tela_de_pe():
            return
        processo = QProcess(self)
        ambiente = QProcessEnvironment.systemEnvironment()
        ambiente.insert("LOOM_NUCLEO", self._nome)
        ambiente.insert("LOOM_NUCLEO_TOKEN", self._token)
        processo.setProcessEnvironment(ambiente)
        # a tela escreve no log; um pipe que ninguem le encheria e travaria a tela no primeiro print
        processo.setStandardOutputFile(QProcess.nullDevice())
        processo.setStandardErrorFile(QProcess.nullDevice())
        processo.setProgram(self._comando[0])
        processo.setArguments(self._comando[1:])
        processo.finished.connect(self._tela_saiu)
        processo.errorOccurred.connect(self._erro_da_tela)
        self._processo = processo
        processo.start()
        log.info("tela subindo: %s", " ".join(self._comando))

    @Slot(int, QProcess.ExitStatus)
    def _tela_saiu(self, codigo: int, status: QProcess.ExitStatus) -> None:
        processo = self.sender()
        if processo is not None and processo is not self._processo:
            return
        if self._processo is not None:
            self._processo.deleteLater()
            self._processo = None
        caiu = status == QProcess.ExitStatus.CrashExit or codigo != 0
        decisao = self.supervisor.depois_que_a_tela_saiu(codigo, caiu)
        if caiu:
            log.warning("a tela caiu com o codigo %s, com %d shells vivos; decisao: %s", codigo, self.nucleo.vivas(), decisao)
        if decisao == Supervisor.FIM:
            self.encerrar()
        elif decisao != Supervisor.ESPERAR:
            self._relance.start(int(decisao * 1000))

    @Slot(QProcess.ProcessError)
    def _erro_da_tela(self, erro: QProcess.ProcessError) -> None:
        # FailedToStart nao emite finished; Crash emite e sera tratado por ele.
        if erro == QProcess.ProcessError.FailedToStart:
            log.error("a tela nao conseguiu iniciar")
            self._tela_saiu(-1, QProcess.ExitStatus.CrashExit)

    @Slot()
    def _mostrar(self) -> None:
        self.supervisor.pediu_pra_mostrar()
        if not self.servidor.mostrar_na_tela():
            self._relance.stop()
            self.subir_tela()

    @Slot()
    def _encerrar_pedido(self) -> None:
        # a pessoa fechou o Loom de proposito: os shells vao junto, como sempre foi
        self.supervisor.encerrado = True
        self.nucleo.encerrar_tudo()
        QTimer.singleShot(PRAZO_PRA_TELA_SAIR, self, self.encerrar)

    def encerrar(self) -> None:
        self.nucleo.encerrar_tudo()
        app = QCoreApplication.instance()
        if app is not None:
            app.quit()


def main_nucleo() -> int:
    app = QCoreApplication(sys.argv)
    app.setApplicationName("Loom")
    nome = nome_do_canal()
    if not segurar_trava(nome):
        # ja ha um nucleo: ele traz a janela, ou sobe a tela se ela nao estiver de pe
        return 0 if chamar_o_nucleo(nome) else 1
    log.info("nucleo do Loom %s subindo, pid %s", __version__, os.getpid())
    try:
        hospedeiro = Hospedeiro(comando_da_tela(), nome, secrets.token_hex(16))
    except OSError:
        log.exception("o canal do nucleo nao abriu; a tela sobe sozinha, com os shells dentro dela")
        ambiente = QProcessEnvironment.systemEnvironment()
        ambiente.insert("LOOM_SEM_NUCLEO", "1")
        processo = QProcess()
        processo.setProcessEnvironment(ambiente)
        processo.setProgram(comando_da_tela()[0])
        processo.setArguments(comando_da_tela()[1:])
        processo.startDetached()
        return 0
    hospedeiro.subir_tela()
    return app.exec()
