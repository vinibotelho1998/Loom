from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

from .bridge import Bridge

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
        self.view.page().setBackgroundColor(QColor("#000000"))
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, False)

        self.bridge = Bridge(self)
        self.bridge.tituloPedido.connect(self._trocar_titulo)
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
