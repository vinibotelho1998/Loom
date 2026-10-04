"""O desenho da pagina medido de verdade, no motor web, fora da tela e sem shell nenhum.

A pagina sobe sem o canal com o app: o script para no ponto em que pediria ao app as abas, e o que
vem antes disso, a folha de estilo e os elementos, ja da pra medir. Nao abre ConPTY nem janela.

O motor web roda num processo filho: na bateria, outro modulo ja criou um QCoreApplication, e o
QWebEngineView em cima dele derrubava o processo inteiro sem dizer nada.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UI = Path(__file__).resolve().parent.parent / "ui"


class PaginaSemApp:
    """A index.html num QWebEngineView fora da tela, com js() que espera a resposta."""

    def __init__(self, largura: int = 1400, altura: int = 860) -> None:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.view = QWebEngineView()
        self.view.resize(largura, altura)
        self.view.show()
        laco = QEventLoop()
        self.view.loadFinished.connect(lambda _ok: laco.quit())
        QTimer.singleShot(15000, laco.quit)
        self.view.load(QUrl.fromLocalFile(str(UI / "index.html")))
        laco.exec()

    def js(self, codigo: str):
        from PySide6.QtCore import QEventLoop, QTimer

        resposta = {}
        laco = QEventLoop()

        def pegar(valor):
            resposta["v"] = valor
            laco.quit()

        self.view.page().runJavaScript(codigo, pegar)
        QTimer.singleShot(5000, laco.quit)
        laco.exec()
        return resposta.get("v")

    def fechar(self) -> None:
        self.view.close()
        self.view.deleteLater()


def medir_varios(codigos: list[str], largura: int = 1400, altura: int = 860) -> list:
    """Roda cada trecho na pagina, num processo filho, e devolve o JSON que cada um montou."""
    pedido = json.dumps({"codigos": codigos, "largura": largura, "altura": altura})
    feito = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--filho"],
        input=pedido,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    linhas = [l for l in feito.stdout.splitlines() if l.startswith("MEDIDAS ")]
    if not linhas:
        raise AssertionError("o processo filho nao mediu nada:\n" + feito.stderr[-2000:])
    return json.loads(linhas[-1][len("MEDIDAS "):])


def _filho() -> None:
    pedido = json.loads(sys.stdin.read())
    pagina = PaginaSemApp(pedido["largura"], pedido["altura"])
    saida = []
    for codigo in pedido["codigos"]:
        bruto = pagina.js(codigo)
        saida.append(json.loads(bruto) if isinstance(bruto, str) else bruto)
    print("MEDIDAS " + json.dumps(saida), flush=True)
    pagina.fechar()
    os._exit(0)


class Layout(unittest.TestCase):
    def medir(self, codigo: str) -> dict:
        return medir_varios([codigo])[0]

    def test_a_barra_de_baixo_ocupa_a_largura_do_terminal(self):
        """Com container-type na barra, ela encolhia num traco de 24 px no meio do rodape, e o
        5h, o 7d, o contexto e o modelo ficavam escondidos pelo overflow."""
        medida = self.medir(
            """(function () {
              const barra = document.getElementById("uso-barra");
              barra.hidden = false;
              document.getElementById("medidor-5h").hidden = false;
              document.querySelector("#uso-conta b").textContent = "Trabalho";
              const coluna = document.querySelector(".coluna").getBoundingClientRect();
              const caixa = barra.getBoundingClientRect();
              const conta = document.getElementById("uso-conta").getBoundingClientRect();
              return JSON.stringify({ coluna: coluna.width, barra: caixa.width, conta: conta.width });
            })()"""
        )
        self.assertGreater(medida["coluna"], 600)
        self.assertAlmostEqual(medida["barra"], medida["coluna"], delta=1)
        # o nome da conta aparece inteiro, e nao so o recheio da caixa
        self.assertGreater(medida["conta"], 60)


class NomesDeClasse(unittest.TestCase):
    def test_a_folha_do_painel_de_uso_nao_pega_elemento_de_fora_dele(self):
        """O grafico do painel chamava cada coluna de .uso-barra, o mesmo nome da barra de baixo: a
        regra dele, com posicao absoluta e 24 px de largura, encolhia a barra inteira."""
        import re

        css = (UI / "painel-uso.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        do_painel = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
        html = (UI / "index.html").read_text(encoding="utf-8")
        da_pagina = set()
        for lista in re.findall(r'class="([^"]+)"', html):
            da_pagina.update(lista.split())
        # o proprio painel e o unico elemento dele que nasce no index.html
        # ativo e so o estado, sempre junto de uma classe propria
        self.assertEqual(do_painel & da_pagina - {"uso-lateral", "ativo"}, set())


if __name__ == "__main__":
    if "--filho" in sys.argv:
        _filho()
    else:
        unittest.main()
