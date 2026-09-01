from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from . import coletor as mod_coletor
from . import config as mod_config
from . import ferramentas as mod_ferramentas
from . import perfis as mod_perfis
from . import sessao_salva as mod_salvas
from . import sessoes as mod_sessoes
from . import uso as mod_uso
from . import shells as mod_shells
from .pty_session import PtySession

INTERVALO_FLUSH_MS = 8
RAIZ_PROJETO = Path(__file__).resolve().parent.parent


class Bridge(QObject):
    """Ponte entre a tela e os ConPTY. Sem porta de rede, tudo pelo canal do Qt.

    A saida nao vai byte a byte pra tela. O shell cospe pedaco de 4 bytes e cada
    travessia ate o JS custa caro, entao juntamos o que chegou e mandamos em bloco
    a cada poucos milissegundos.
    """

    output = Signal(str, str)
    exited = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: dict[str, PtySession] = {}
        self._buffers: dict[str, list[str]] = {}
        self._shells = mod_shells.descobrir()
        self._sem_autoabrir = False

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(INTERVALO_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

    # -- catalogo -------------------------------------------------------

    @Slot(result=str)
    def listShells(self) -> str:
        return json.dumps([s.como_dict() for s in self._shells])

    @Slot(result=str)
    def listProfiles(self) -> str:
        return json.dumps([
            {
                "id": p.id,
                "nome": p.nome,
                "cor": p.cor,
                "ferramenta": p.ferramenta,
                "ferramentaNome": mod_ferramentas.por_id(p.ferramenta).nome,
                "logado": mod_perfis.logado(p),
                "configDir": str(p.config_dir),
                "bytes": mod_perfis.tamanho(p),
                "emprestada": p.emprestada,
                "email": mod_perfis.identidade(p).get("email", ""),
                "plano": mod_perfis.identidade(p).get("plano", ""),
            }
            for p in mod_perfis.listar()
        ])

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
        self._sem_autoabrir = bool(pedido_bruto.get("conversa"))
        try:
            session_id = self.openSession(shell_id, perfil_id, cols, rows)
        finally:
            self._sem_autoabrir = False
        pedido = pedido_bruto

        pasta = pedido.get("pasta")
        conversa = pedido.get("conversa")
        if not pasta and not conversa and not pedido.get("entrar"):
            return session_id

        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        comando = ""
        if perfil is not None and conversa:
            ferramenta = mod_ferramentas.por_id(perfil.ferramenta).comando
            if ferramenta:
                comando = f"{ferramenta} --resume {conversa}"

        linhas = []
        if pasta:
            linhas.append(f'cd "{pasta}"')
        if comando:
            linhas.append(comando)
        if linhas:
            texto = chr(13).join(linhas) + chr(13)
            QTimer.singleShot(700, lambda: self.write(session_id, texto))

        if pedido.get("entrar"):
            # o /login e digitado dentro do CLI, entao precisa esperar ele terminar de subir
            QTimer.singleShot(4000, lambda: self.write(session_id, "/login" + chr(13)))
        return session_id

    @Slot(result=str)
    def getLimits(self) -> str:
        return json.dumps(
            {"porConta": mod_coletor.limites_por_perfil(), "porAba": mod_coletor.por_aba()}
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
        shell = next((s for s in self._shells if s.id == shell_id), None)
        if shell is None:
            shell = mod_shells.padrao(self._shells)

        perfil = mod_perfis.por_id(perfil_id) if perfil_id else None
        session_id = uuid.uuid4().hex[:8]

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
            argv=[shell.caminho, *shell.args],
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

    def _flush(self) -> None:
        if not self._buffers:
            return
        pendentes, self._buffers = self._buffers, {}
        for session_id, pedacos in pendentes.items():
            self.output.emit(session_id, "".join(pedacos))

    @Slot(str, int)
    def _on_exit(self, session_id: str, code: int) -> None:
        self._flush()
        self._sessions.pop(session_id, None)
        self.exited.emit(session_id, code)

    def shutdown(self) -> None:
        self._flush_timer.stop()
        for session in list(self._sessions.values()):
            session.stop()
        self._sessions.clear()
        self._buffers.clear()
