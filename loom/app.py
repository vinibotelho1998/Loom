from __future__ import annotations

import ctypes.wintypes
import json
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

from . import config as mod_config
from . import instancia as mod_instancia
from . import perfis as mod_perfis
from . import vigia as mod_vigia
from .bridge import Bridge

# Tres niveis de cinza, do mais claro pro mais escuro, pra dar profundidade:
# a barra de titulo do sistema, a barra do app e o terminal.
COR_TITULO = "#252932"
COR_FUNDO = "#16181c"
COR_LINHA = "#333843"
COR_TEXTO = "#c8cfda"

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

# Tela refeita mais que isto dentro de um minuto e sinal de que o motor nao se recupera,
# e insistir so pisca a janela
TETO_DE_RECUPERACAO = 3


class LoggingPage(QWebEnginePage):
    """Manda o console da tela pro terminal de quem esta desenvolvendo."""

    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f"[tela] {source.rsplit('/', 1)[-1]}:{line} {message}", flush=True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # nome fixo de proposito: mostrar a conversa da aba ali em cima fazia o titulo
        # trocar toda hora, o que incomodava mais do que ajudava
        self.setWindowTitle("Loominal")
        self.resize(1180, 720)

        self.bridge = Bridge(self)
        self.bridge.notificacaoPedida.connect(self._notificar)
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
        self._vigia.recomecar(time.monotonic())

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
        print(f"[tela] refazendo a tela sozinho: {motivo}", flush=True)
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
        print(f"[tela] motor da pagina caiu: {status} {codigo}", flush=True)
        # fora do sinal: o motor ainda esta desmontando a pagina que morreu
        QTimer.singleShot(300, self.recuperar_tela)

    def escutar_outras_instancias(self) -> None:
        """Abrir o Loom de novo com ele aberto traz esta janela, com a tela refeita.

        Os shells moram neste processo. Uma segunda janela nasceria com shells novos e nunca
        acharia as conversas que estao rodando aqui, que era justamente o que se queria de volta.
        """
        nome = mod_instancia.nome()
        servidor = QLocalServer(self)
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
            conexao.readyRead.connect(lambda c=conexao: self._atender(c))
            conexao.disconnected.connect(conexao.deleteLater)

    def _atender(self, conexao: QLocalSocket) -> None:
        pedido = bytes(conexao.readAll()).strip()
        if pedido != mod_instancia.PEDIDO:
            conexao.disconnectFromServer()
            return
        self._recuperacoes.clear()
        self.recuperar_tela()
        self._trazer_pra_frente()
        conexao.write(b"ok\n")
        conexao.flush()

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
                self.bridge.arquivosSoltos.emit(json.dumps(caminhos))
                return True
        return super().eventFilter(obj, evento)

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
        bandeja.messageClicked.connect(self._trazer_pra_frente)
        bandeja.show()
        return bandeja

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

    def _notificar(self, titulo: str, texto: str) -> None:
        """Sessao que terminou e esta esperando avisa mesmo com a janela atras de tudo."""
        if self.bandeja is not None:
            self.bandeja.showMessage(
                titulo, texto, QSystemTrayIcon.MessageIcon.Information, 6000
            )
        app = QApplication.instance()
        if app is not None:
            # pisca na barra de tarefas, que e o aviso que se ve sem olhar pro canto
            app.alert(self, 3000)

    def closeEvent(self, event):
        """Fechar a aba pergunta; fechar a janela derrubava tudo calado."""
        vivos = self.bridge.sessoes_vivas()
        if vivos and mod_config.carregar().get("confirmarFechar"):
            quantos = f"{vivos} shells ainda abertos" if vivos > 1 else "1 shell ainda aberto"
            resposta = QMessageBox.question(
                self,
                "Fechar o Loom",
                f"{quantos}. Fechar a janela encerra tudo que estiver rodando dentro.",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Close,
                QMessageBox.StandardButton.Cancel,
            )
            if resposta != QMessageBox.StandardButton.Close:
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
    app.setApplicationDisplayName("Loom")

    if mod_instancia.chamar_quem_ja_esta_aberto():
        return 0

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
