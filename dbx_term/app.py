from __future__ import annotations

import ctypes.wintypes
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

from .bridge import Bridge

# Tres niveis de cinza, do mais claro pro mais escuro, pra dar profundidade:
# a barra de titulo do sistema, a barra do app e o terminal.
COR_TITULO = "#252932"
COR_FUNDO = "#16181c"
COR_LINHA = "#333843"
COR_TEXTO = "#c8cfda"

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


class LoggingPage(QWebEnginePage):
    """Manda o console da tela pro terminal de quem esta desenvolvendo."""

    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f"[tela] {source.rsplit('/', 1)[-1]}:{line} {message}", flush=True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DBX Term")
        self.resize(1180, 720)

        self.view = QWebEngineView(self)
        self.view.setPage(LoggingPage(self.view))
        self.view.page().setBackgroundColor(QColor(COR_FUNDO))
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, False)

        self.bridge = Bridge(self)
        self.bridge.tituloPedido.connect(self._trocar_titulo)

        # o filtro precisa ser da aplicacao inteira: o widget que recebe o arrasto
        # e criado por dentro do motor web e nao existe ainda neste ponto
        self.setAcceptDrops(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        endereco = QUrl.fromLocalFile(str(UI_DIR / "index.html"))
        consulta = []
        if os.environ.get("DBX_TERM_DEMO"):
            # sobe algumas abas e liga a grade, pra conferir layout sem clicar em nada
            consulta.append("demo=" + os.environ["DBX_TERM_DEMO"])
        if os.environ.get("DBX_TERM_BUSCA"):
            consulta.append("busca=" + os.environ["DBX_TERM_BUSCA"])
        if os.environ.get("DBX_TERM_CONFIG"):
            consulta.append("config=" + os.environ["DBX_TERM_CONFIG"])
        if consulta:
            endereco.setQuery("&".join(consulta))
        self.view.load(endereco)
        self.setCentralWidget(self.view)

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

    def _trocar_titulo(self, texto: str) -> None:
        """A janela mostra em que conversa voce esta, que e o que a barra de tarefas precisa dizer."""
        self.setWindowTitle(f"{texto} · DBX Term" if texto else "DBX Term")

    def closeEvent(self, event):
        self.bridge.shutdown()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DBX Term")
    app.setApplicationDisplayName("DBX Term")

    icone = UI_DIR / "dbx-term.ico"
    if icone.exists():
        app.setWindowIcon(QIcon(str(icone)))
        if sys.platform == "win32":
            # sem um id proprio o Windows agrupa a janela debaixo do icone do Python
            import ctypes

            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("dbx.term.app")
            except (AttributeError, OSError):
                pass
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
