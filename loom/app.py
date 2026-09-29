from __future__ import annotations

import ctypes.wintypes
import json
import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__
from . import config as mod_config
from . import instancia as mod_instancia
from . import motor as mod_motor
from . import perfis as mod_perfis
from . import queda as mod_queda
from . import registro as mod_registro
from . import vigia as mod_vigia
from .bridge import Bridge

log = logging.getLogger(__name__)

# Tres niveis de cinza, do mais claro pro mais escuro, pra dar profundidade:
# a barra de titulo do sistema, a barra do app e o terminal.
COR_TITULO = "#252932"
COR_FUNDO = "#16181c"
COR_LINHA = "#333843"
COR_TEXTO = "#c8cfda"

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

# a barra de tarefas mostra a versao que esta rodando, lida do pacote
TITULO = f"Loom {__version__}"

# Tela refeita mais que isto dentro de um minuto e sinal de que o motor nao se recupera,
# e insistir so pisca a janela
TETO_DE_RECUPERACAO = 3
# a janela que estava minimizada tem este prazo pra pagina voltar a pulsar depois de aparecer
ESPERA_DO_PULSO_MS = 5000

log_da_pagina = logging.getLogger("loom.tela")
NIVEL_DO_CONSOLE = {
    QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: logging.INFO,
    QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: logging.WARNING,
    QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: logging.ERROR,
}


class LoggingPage(QWebEnginePage):
    """Manda o console da tela pro log, com o nivel que a pagina deu.

    No exe sem console o print nao ia a lugar nenhum, e a perda de contexto do WebGL, que o
    xterm avisa pelo console, sumia junto.
    """

    def javaScriptConsoleMessage(self, level, message, line, source):
        log_da_pagina.log(
            NIVEL_DO_CONSOLE.get(level, logging.INFO), "%s:%s %s", source.rsplit("/", 1)[-1], line, message
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # nome fixo de proposito: mostrar a conversa da aba ali em cima fazia o titulo
        # trocar toda hora, o que incomodava mais do que ajudava. Sem nome de exibicao na
        # aplicacao, o Qt nao acrescenta sufixo e a barra mostra so o nome com a versao
        self.setWindowTitle(TITULO)
        self.resize(1180, 720)

        self.bridge = Bridge(self)
        # a prova do sudo leva ate 8 s; comeca ja, fora da thread da tela, e fica pronta antes de a
        # pagina perguntar se da pra abrir elevado
        self.bridge.provar_sudo()
        self.bridge.notificacaoPedida.connect(self._notificar)
        # a aba do ultimo aviso, pra o clique no balao levar ate ela
        self._aba_avisada = ""
        self.bandeja = self._montar_bandeja()
        self._recuperacoes: list[float] = []
        self._servidor: QLocalServer | None = None

        # o filtro precisa ser da aplicacao inteira: o widget que recebe o arrasto
        # e criado por dentro do motor web e nao existe ainda neste ponto
        self.setAcceptDrops(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        # por tras da pagina fica uma cor que ela nunca pinta: e por ela que a vigia percebe,
        # lendo a tela, quando a pagina sumiu e so sobrou o fundo da janela
        self.setStyleSheet(f"QMainWindow {{ background: {mod_vigia.COR_SENTINELA}; }}")
        self._vigia = mod_vigia.Vigia(time.monotonic())
        self._relogio_da_vigia = QTimer(self)
        self._relogio_da_vigia.setInterval(mod_vigia.INTERVALO_MS)
        self._relogio_da_vigia.timeout.connect(self._vigiar)
        self._relogio_da_vigia.start()

        self.view = None
        self.channel = None
        self._montar_tela()

    def _montar_tela(self) -> None:
        """Monta o motor web e carrega a tela. Os shells ficam no Bridge, fora daqui.

        Por isso a tela pode ser jogada fora e montada de novo sem derrubar nada: a pagina
        nova pede ao Bridge os shells vivos e cada aba volta ligada ao seu.
        """
        view = QWebEngineView(self)
        view.setPage(LoggingPage(view))
        view.page().setBackgroundColor(QColor(COR_FUNDO))
        # o fundo do proprio widget tambem, porque e ele que aparece quando o motor para
        fundo = view.palette()
        fundo.setColor(QPalette.ColorRole.Window, QColor(mod_vigia.COR_SENTINELA))
        view.setPalette(fundo)
        settings = view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, False)
        # sem estes dois o navigator.clipboard nem existe, e o Ctrl+V da pagina nao le nada
        settings.setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.JavascriptCanPaste, True)
        # quando o motor da pagina morre, a janela ficava preta pra sempre com os shells vivos
        # por baixo, sem nenhum jeito de voltar a eles
        view.page().renderProcessTerminated.connect(self._pagina_caiu)

        channel = QWebChannel(view)
        channel.registerObject("bridge", self.bridge)
        view.page().setWebChannel(channel)

        endereco = QUrl.fromLocalFile(str(UI_DIR / "index.html"))
        consulta = []
        if os.environ.get("LOOM_DEMO"):
            # sobe algumas abas e liga a grade, pra conferir layout sem clicar em nada
            consulta.append("demo=" + os.environ["LOOM_DEMO"])
        if os.environ.get("LOOM_BUSCA"):
            consulta.append("busca=" + os.environ["LOOM_BUSCA"])
        if os.environ.get("LOOM_CONFIG"):
            consulta.append("config=" + os.environ["LOOM_CONFIG"])
        if consulta:
            endereco.setQuery("&".join(consulta))
        view.load(endereco)

        antiga = self.view
        self.view, self.channel = view, channel
        # setCentralWidget apaga a tela anterior, e o canal dela vai junto por ser filho
        self.setCentralWidget(view)
        if antiga is not None:
            view.setFocus()
        self._montada_em = time.monotonic()
        self._vigia.recomecar(self._montada_em)

    def recuperar_tela(self) -> bool:
        """Joga a tela fora e monta outra, com as mesmas abas ligadas aos mesmos shells.

        Serve pra tela preta: quando o driver de video reinicia, o motor web as vezes nao
        volta a desenhar e so uma tela nova resolve. Devolve False quando ja tentou demais.
        """
        agora = time.monotonic()
        self._recuperacoes = [t for t in self._recuperacoes if agora - t < 60]
        if len(self._recuperacoes) >= TETO_DE_RECUPERACAO:
            return False
        self._recuperacoes.append(agora)
        self._montar_tela()
        return True

    def _vigiar(self) -> None:
        visivel = self.isVisible() and not self.isMinimized()
        motivo = self._vigia.tique(
            time.monotonic(), visivel, self.bridge.ultimo_pulso, self._so_fundo_na_tela
        )
        if not motivo:
            return
        log.warning("refazendo a tela sozinho: %s", motivo)
        self.recuperar_tela()
        # no teto de tentativas a tela nao e refeita; mesmo assim espera uma carencia inteira
        # antes de julgar de novo, em vez de insistir a cada volta do relogio
        self._vigia.recomecar(time.monotonic())

    def _so_fundo_na_tela(self) -> bool | None:
        """Le a janela direto da tela e diz se ali aparece so o fundo, sem a pagina.

        None quando nao da pra ler com seguranca: janela atras de outra, coberta ou pequena.
        """
        if self.view is None:
            return None
        pixels = mod_vigia.ler_janela(int(self.winId()))
        return None if pixels is None else mod_vigia.so_fundo(pixels)

    def _pagina_caiu(self, status, codigo: int) -> None:
        pagina = self.view.page() if self.view is not None else None
        if self.sender() is not pagina:
            # a pagina da tela que acabou de ser trocada terminando: refazer por ela jogaria
            # fora a tela nova, e a troca soltaria mais uma pagina velha, sem fim
            return
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            return
        log.error("motor da pagina caiu: %s, codigo %s", status, codigo)
        # fora do sinal: o motor ainda esta desmontando a pagina que morreu
        QTimer.singleShot(300, self.recuperar_tela)

    def escutar_outras_instancias(self) -> None:
        """Abrir o Loom de novo com ele aberto traz esta janela pra frente.

        Os shells moram neste processo. Uma segunda janela nasceria com shells novos e nunca
        acharia as conversas que estao rodando aqui, que era justamente o que se queria de volta.
        """
        nome = mod_instancia.nome()
        servidor = QLocalServer(self)
        # so a propria pessoa conecta; sem isto o canal aceitava qualquer usuario da maquina
        servidor.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not servidor.listen(nome):
            # sobrou o nome de um Loom que morreu sem fechar; liberado, a segunda tentativa pega
            QLocalServer.removeServer(nome)
            if not servidor.listen(nome):
                return
        servidor.newConnection.connect(self._outra_instancia)
        self._servidor = servidor

    def _outra_instancia(self) -> None:
        while self._servidor is not None and self._servidor.hasPendingConnections():
            conexao = self._servidor.nextPendingConnection()
            acumulado = bytearray()
            conexao.readyRead.connect(lambda c=conexao, a=acumulado: self._atender(c, a))
            conexao.disconnected.connect(conexao.deleteLater)

    def _atender(self, conexao: QLocalSocket, acumulado: bytearray) -> None:
        pedido = mod_instancia.ler_pedido(acumulado, bytes(conexao.readAll()))
        if pedido is None:
            # linha ainda aberta: espera o resto, menos quando ja passou do que um pedido tem
            if len(acumulado) > mod_instancia.TETO_DO_PEDIDO:
                conexao.disconnectFromServer()
            return
        if pedido != mod_instancia.PEDIDO:
            conexao.disconnectFromServer()
            return
        self._mostrar_a_quem_chamou()
        conexao.write(mod_instancia.RESPOSTA + b"\n")
        conexao.flush()

    def _mostrar_a_quem_chamou(self) -> None:
        """Traz a janela pra frente, e so refaz a tela se a pagina parou de pulsar.

        Refazia sempre, e cada nova abertura do Loom destruia e recriava os contextos WebGL de
        todas as abas, justamente o caminho em que o ANGLE derruba o processo inteiro.
        """
        agora = time.monotonic()
        a_vista = self.isVisible() and not self.isMinimized()
        self._trazer_pra_frente()
        if a_vista:
            # a pagina estava sendo desenhada: sem pulso pelo prazo da vigia, ela parou
            self._refazer_se_parou(agora - mod_vigia.SEM_PULSO)
        else:
            # minimizada a pagina para de desenhar de proposito e o pulso velho nao prova nada;
            # a prova e ela voltar a pulsar depois de aparecer
            QTimer.singleShot(ESPERA_DO_PULSO_MS, lambda: self._refazer_se_parou(agora))

    def _refazer_se_parou(self, desde: float) -> None:
        if self.bridge.ultimo_pulso >= desde:
            return
        if time.monotonic() - self._montada_em < mod_vigia.CARENCIA:
            # tela recem montada ainda carregando; se ela nao pegar, a vigia refaz
            return
        log.warning("a pagina nao pulsava quando chamaram o Loom de novo; refazendo a tela")
        # o pedido da pessoa passa por cima do teto, como o Recarregar da bandeja
        self._recuperacoes.clear()
        self.recuperar_tela()

    def eventFilter(self, obj, evento):
        """Pega o arquivo arrastado antes do motor web.

        Dentro da pagina, um arquivo solto chega sem caminho nenhum, por seguranca do
        navegador. Do lado do Qt o caminho vem inteiro, entao interceptamos aqui e
        mandamos pra tela ja resolvido.
        """
        tipo = evento.type()
        if tipo in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if evento.mimeData().hasUrls():
                evento.acceptProposedAction()
                return True
        elif tipo == QEvent.Type.Drop:
            # toLocalFile devolve com barra pra frente; o shell do Windows quer a de sempre
            caminhos = [
                str(Path(url.toLocalFile()))
                for url in evento.mimeData().urls()
                if url.isLocalFile() and url.toLocalFile()
            ]
            if caminhos:
                evento.acceptProposedAction()
                ponto = self._ponto_na_pagina(obj, evento)
                if ponto is None:
                    self.bridge.arquivosSoltos.emit(json.dumps(caminhos))
                else:
                    # na grade o arquivo vai pro painel debaixo do mouse, e nao pro ativo
                    self.bridge.arquivosSoltosEm.emit(json.dumps(caminhos), *ponto)
                return True
        return super().eventFilter(obj, evento)

    def _ponto_na_pagina(self, obj, evento) -> tuple[float, float] | None:
        """Onde o arquivo caiu, em pixel da pagina. None quando o drop nao veio da pagina.

        Quem recebe o drop e o widget interno do motor web; a posicao vem relativa a ele e e
        levada pra tela inteira antes de virar pixel da pagina.
        """
        from PySide6.QtWidgets import QWidget

        view = self.view
        if view is None or not isinstance(obj, QWidget) or not (obj is view or view.isAncestorOf(obj)):
            return None
        try:
            ponto = obj.mapTo(view, evento.position().toPoint())
        except (AttributeError, RuntimeError):
            return None
        zoom = view.zoomFactor() or 1.0
        return ponto.x() / zoom, ponto.y() / zoom

    def _pintar_barra_de_titulo(self) -> None:
        """Pinta a barra de titulo do Windows na cor do app.

        Ela nao e desenhada por nos, e sim pelo sistema, entao sem isto fica um retangulo
        preto por cima de uma janela cinza. O Windows 11 aceita a cor pelo DWM.
        """
        if sys.platform != "win32":
            return
        import ctypes

        def cor(hexa: str) -> int:
            # o DWM quer BGR, e nao RGB
            r, g, b = int(hexa[1:3], 16), int(hexa[3:5], 16), int(hexa[5:7], 16)
            return b << 16 | g << 8 | r

        DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 34, 35, 36
        try:
            handle = ctypes.wintypes.HWND(int(self.winId()))
            for atributo, valor in (
                (DWMWA_CAPTION_COLOR, cor(COR_TITULO)),
                (DWMWA_BORDER_COLOR, cor(COR_LINHA)),
                (DWMWA_TEXT_COLOR, cor(COR_TEXTO)),
            ):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    handle, atributo, ctypes.byref(ctypes.c_int(valor)), ctypes.sizeof(ctypes.c_int)
                )
        except (AttributeError, OSError, ValueError):
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._pintar_barra_de_titulo()

    def _montar_bandeja(self):
        """Icone ao lado do relogio. Serve pra avisar e pra trazer a janela de volta."""
        icone = UI_DIR / "loom.ico"
        if not icone.exists() or not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        bandeja = QSystemTrayIcon(QIcon(str(icone)), self)
        bandeja.setToolTip("Loom")
        menu = QMenu()
        menu.addAction("Mostrar a janela", self._trazer_pra_frente)
        menu.addAction("Recarregar a tela", self._recarregar_pela_bandeja)
        menu.addSeparator()
        menu.addAction("Sair", self.close)
        bandeja.setContextMenu(menu)
        bandeja.activated.connect(
            lambda motivo: self._trazer_pra_frente()
            if motivo == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        bandeja.messageClicked.connect(self._abrir_aviso)
        bandeja.show()
        return bandeja

    def _abrir_aviso(self) -> None:
        """O clique no balao leva a aba que avisou, e nao so traz a janela."""
        if self._aba_avisada:
            self.bridge.irParaAba.emit(self._aba_avisada)
        self._trazer_pra_frente()

    def _trazer_pra_frente(self) -> None:
        # showNormal tirava a janela de maximizada; aqui so sai do minimizado
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def _recarregar_pela_bandeja(self) -> None:
        # pedido da pessoa passa por cima do teto, que existe so pra recuperacao sozinha
        self._recuperacoes.clear()
        self.recuperar_tela()
        self._trazer_pra_frente()

    def _notificar(self, titulo: str, texto: str, aba: str = "") -> None:
        """Sessao que terminou e esta esperando avisa mesmo com a janela atras de tudo."""
        self._aba_avisada = aba
        if self.bandeja is not None:
            self.bandeja.showMessage(
                titulo, texto, QSystemTrayIcon.MessageIcon.Information, 6000
            )
        app = QApplication.instance()
        if app is not None:
            # pisca na barra de tarefas, que e o aviso que se ve sem olhar pro canto
            app.alert(self, 3000)

    def _pergunta_ao_fechar(self) -> str:
        """O texto da confirmacao de fechar a janela, ou vazio quando nao ha o que perguntar.

        Perguntava sempre que havia uma aba, ou seja, sempre: com tres PowerShell parados no
        prompt, a pessoa aprendia a clicar em Fechar tudo sem ler. Com as abas voltando no
        proximo start, so pergunta quando alguma tem programa rodando. Sem voltar, pergunta
        sempre, porque ai as abas se perdem.
        """
        ajustes = mod_config.carregar()
        if not ajustes.get("confirmarFechar"):
            return ""
        vivas = self.bridge.sessoes_vivas()
        if not vivas:
            return ""
        if not ajustes.get("restaurarAbas"):
            quantas = f"{vivas} abas abertas" if vivas > 1 else "1 aba aberta"
            return f"{quantas}. Fechar a janela encerra todas, e elas não voltam na próxima vez."
        ocupadas = self.bridge.sessoes_ocupadas()
        if not ocupadas:
            return ""
        quantas = f"{ocupadas} abas com programa rodando" if ocupadas > 1 else "1 aba com programa rodando"
        return f"{quantas}. Fechar a janela encerra tudo que estiver rodando dentro."

    def closeEvent(self, event):
        """Fechar a aba pergunta; fechar a janela derrubava tudo calado."""
        pergunta = self._pergunta_ao_fechar()
        if pergunta:
            # botoes com texto proprio: os padrao do Qt saiam Cancel e Close, sem tradutor
            caixa = QMessageBox(
                QMessageBox.Icon.Question,
                "Fechar o Loom",
                pergunta,
                parent=self,
            )
            cancelar = caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
            fechar = caixa.addButton("Fechar tudo", QMessageBox.ButtonRole.DestructiveRole)
            caixa.setDefaultButton(cancelar)
            caixa.setEscapeButton(cancelar)
            caixa.exec()
            if caixa.clickedButton() is not fechar:
                event.ignore()
                return
        if self.bandeja is not None:
            self.bandeja.hide()
        self._relogio_da_vigia.stop()
        self.bridge.shutdown()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Loom")
    # sem nome de exibicao de proposito: no Windows o Qt pendura " - <nome>" em todo titulo que
    # nao termina nele, e a caixa de fechar ganhava o nome e a versao pendurados no fim

    if mod_instancia.chamar_quem_ja_esta_aberto():
        return 0
    # dois starts quase juntos: o outro pegou a trava e ainda esta montando a janela
    if not mod_instancia.segurar_trava() and mod_instancia.esperar_quem_esta_subindo():
        return 0

    mod_registro.ligar_qt()
    log.info("Loom %s subindo, pid %s", __version__, os.getpid())
    # a marca some quando o Loom fecha direito; se sobrar, o proximo sabe que a tela caiu
    mod_queda.marcar_subida(mod_motor.modo_do_ambiente(os.environ))
    app.aboutToQuit.connect(mod_queda.apagar_marca)

    # a barra de uso da conta principal vem do atalho de status line; se ele ficou apontando
    # pra um coletor que nao existe mais, a barra fica sem leitura ate alguem notar
    mod_perfis.reparar_status_line_principal(sys.executable, str(UI_DIR.parent))

    icone = UI_DIR / "loom.ico"
    if icone.exists():
        app.setWindowIcon(QIcon(str(icone)))
        if sys.platform == "win32":
            # sem um id proprio o Windows agrupa a janela debaixo do icone do Python
            import ctypes

            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Loom")
            except (AttributeError, OSError):
                pass
    window = MainWindow()
    window.escutar_outras_instancias()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
