"""O lado da tela do canal com o nucleo.

A ponte pede e recebe os shells daqui como pedia da PtySession: SessaoRemota tem os mesmos sinais e
os mesmos metodos. A diferenca e que o shell mora no nucleo, e sobrevive a tela que cai.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

from . import protocolo as mod_protocolo

log = logging.getLogger(__name__)

# a tela so desenha depois de saber o que ja esta vivo; o nucleo responde em milissegundos
ESPERA_DO_ESTADO_MS = 5000
# encerrar mata os shells: o pedido tem que sair antes de a tela fechar
ESPERA_DO_ENCERRAR_MS = 2000


class ClienteDoNucleo(QObject):
    """A conexao com o nucleo. Entrega a saida e o fim de cada shell pra sessao certa."""

    mostrar = Signal()
    caiu = Signal()

    def __init__(self, nome: str, token: str, parent=None):
        super().__init__(parent)
        self._nome = nome
        self._token = token
        self._conexao = QLocalSocket(self)
        self._leitor = mod_protocolo.Leitor()
        self._sessoes: dict[str, SessaoRemota] = {}
        self._tem_estado = False
        self._encerrando = False
        # o que o nucleo tinha quando esta tela nasceu
        self.estado_inicial: list[dict] = []
        self.abas_iniciais: list[dict] = []

    def conectar(self, espera_ms: int = ESPERA_DO_ESTADO_MS) -> bool:
        self._conexao.connectToServer(self._nome)
        if not self._conexao.waitForConnected(espera_ms):
            return False
        self._enviar({"tipo": "ola", "token": self._token})
        fim = time.monotonic() + espera_ms / 1000
        while not self._tem_estado and time.monotonic() < fim:
            # ainda nao ha laco de eventos rodando, e sem processar eventos a resposta se perdia,
            # como ja acontecia no canal entre instancias
            QCoreApplication.processEvents()
            if self._conexao.waitForReadyRead(50):
                self._ler()
        if not self._tem_estado:
            self._conexao.abort()
            return False
        # daqui em diante o laco de eventos entrega; o que ja chegou foi lido acima
        self._conexao.readyRead.connect(self._ler)
        self._conexao.disconnected.connect(self._desconectou)
        return True

    def registrar(self, sessao: "SessaoRemota") -> None:
        self._sessoes[sessao.session_id] = sessao

    def abrir(self, sessao: "SessaoRemota", argv, cwd, env, cols: int, rows: int, origem: dict) -> None:
        self.registrar(sessao)
        self._enviar(
            {
                "tipo": "abrir",
                "id": sessao.session_id,
                "argv": list(argv or []),
                "cwd": cwd or "",
                "env": dict(env or {}),
                "cols": cols,
                "rows": rows,
                "origem": dict(origem or {}),
            }
        )

    def escrever(self, session_id: str, texto: str) -> None:
        self._enviar({"tipo": "escrever", "id": session_id, "texto": texto})

    def medida(self, session_id: str, cols: int, rows: int) -> None:
        self._enviar({"tipo": "medida", "id": session_id, "cols": cols, "rows": rows})

    def fechar(self, session_id: str) -> None:
        self._sessoes.pop(session_id, None)
        self._enviar({"tipo": "fechar", "id": session_id})

    def guardar_abas(self, abas: list[dict]) -> None:
        self._enviar({"tipo": "abas", "abas": abas})

    def encerrar(self) -> None:
        """A pessoa fechou o Loom: o nucleo derruba os shells e sai quando a tela sair."""
        self._encerrando = True
        self._enviar({"tipo": "encerrar"})
        mod_protocolo.esvaziar(self._conexao, ESPERA_DO_ENCERRAR_MS)

    def _enviar(self, mensagem: dict) -> None:
        if self._conexao.state() != QLocalSocket.LocalSocketState.ConnectedState:
            return
        self._conexao.write(mod_protocolo.empacotar(mensagem))

    @Slot()
    def _ler(self) -> None:
        for mensagem in self._leitor.alimentar(bytes(self._conexao.readAll())):
            self._atender(mensagem)
        if self._leitor.estourou:
            log.warning("mensagem grande demais vinda do nucleo; conexao derrubada")
            self._conexao.abort()

    def _atender(self, mensagem: dict) -> None:
        tipo = mensagem["tipo"]
        session_id = str(mensagem.get("id") or "")
        if tipo == "saida":
            sessao = self._sessoes.get(session_id)
            if sessao is not None:
                sessao.output.emit(session_id, str(mensagem.get("texto") or ""))
        elif tipo == "saiu":
            sessao = self._sessoes.pop(session_id, None)
            if sessao is not None:
                sessao._viva = False
                try:
                    codigo = int(mensagem.get("codigo", 0))
                except (TypeError, ValueError):
                    codigo = 0
                sessao.exited.emit(session_id, codigo)
        elif tipo == "aberta":
            sessao = self._sessoes.get(session_id)
            if sessao is not None:
                try:
                    sessao._pid = int(mensagem.get("pid") or 0) or None
                except (TypeError, ValueError):
                    sessao._pid = None
        elif tipo == "estado":
            sessoes = mensagem.get("sessoes")
            abas = mensagem.get("abas")
            self.estado_inicial = [s for s in sessoes if isinstance(s, dict)] if isinstance(sessoes, list) else []
            self.abas_iniciais = [a for a in abas if isinstance(a, dict)] if isinstance(abas, list) else []
            self._tem_estado = True
        elif tipo == "mostrar":
            self.mostrar.emit()

    @Slot()
    def _desconectou(self) -> None:
        if self._encerrando:
            return
        log.error("o canal com o nucleo caiu com %d shells ligados a esta tela", len(self._sessoes))
        self.caiu.emit()


class SessaoRemota(QObject):
    """Um shell que mora no nucleo, com a cara da PtySession pra ponte."""

    output = Signal(str, str)
    exited = Signal(str, int)

    def __init__(
        self,
        cliente: ClienteDoNucleo,
        session_id: str,
        argv=None,
        cwd=None,
        env=None,
        cols: int = 120,
        rows: int = 30,
        origem: dict | None = None,
        pid: int | None = None,
        viva: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._cliente = cliente
        self.session_id = session_id
        self._argv = argv
        self._cwd = cwd
        self._env = env
        self._cols, self._rows = max(int(cols), 1), max(int(rows), 1)
        self._origem = origem or {}
        self._pid = pid or None
        self._viva = viva

    def start(self) -> None:
        self._viva = True
        self._cliente.abrir(self, self._argv, self._cwd, self._env, self._cols, self._rows, self._origem)

    def write(self, data: str) -> None:
        if self._viva:
            self._cliente.escrever(self.session_id, data)

    def resize(self, cols: int, rows: int) -> None:
        self._cols, self._rows = max(cols, 1), max(rows, 1)
        if self._viva:
            self._cliente.medida(self.session_id, self._cols, self._rows)

    @property
    def tamanho(self) -> tuple[int, int]:
        return self._cols, self._rows

    @property
    def alive(self) -> bool:
        return self._viva

    @property
    def pid(self) -> int | None:
        return self._pid

    def stop(self) -> None:
        if not self._viva:
            return
        self._viva = False
        self._cliente.fechar(self.session_id)
