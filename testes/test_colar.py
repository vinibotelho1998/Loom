"""Ctrl+V na aba do terminal.

Quem le a area de transferencia e o lado do Qt, e nao o navigator.clipboard da pagina:
aquele exige documento em foco e permissao, e quando uma das duas falhava o Ctrl+V nao
colava nada. Estes testes espiam o paste do terminal pra ver o que de fato entrou.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from test_selecao import NaTela  # noqa: E402

ESPIAR = """
(function () {
  if (!abaAtiva) return "sem aba";
  window.__colado = [];
  const t = abaAtiva.term;
  if (!t.__espiado) {
    const original = t.paste.bind(t);
    t.paste = (txt) => { window.__colado.push(txt); original(txt); };
    t.__espiado = true;
  }
  return "espiando";
})()
"""

COLAR = """(function () { colarAqui(abaAtiva); return "chamou"; })()"""

VER = "JSON.stringify(window.__colado)"


class Colar(unittest.TestCase):
    def colar(self, tela) -> list[str]:
        self.assertEqual(tela.js(ESPIAR), "espiando")
        tela.js(COLAR)
        tela.esperar(1200)
        return json.loads(tela.js(VER) or "[]")

    def test_texto_vai_inteiro_pro_terminal(self):
        with NaTela() as tela:
            QApplication.instance().clipboard().setText("echo texto-colado-ok")

            self.assertEqual(self.colar(tela), ["echo texto-colado-ok"])

    def test_imagem_vira_arquivo_e_o_terminal_recebe_o_caminho(self):
        """Terminal nao recebe imagem. O que o CLI entende e o caminho do arquivo."""
        with NaTela() as tela:
            imagem = QImage(40, 30, QImage.Format.Format_RGB32)
            imagem.fill(QColor("#3c5a80"))
            QApplication.instance().clipboard().setImage(imagem)

            colado = self.colar(tela)

            self.assertEqual(len(colado), 1)
            caminho = Path(colado[0].strip('"'))
            self.assertEqual(caminho.suffix, ".png")
            self.assertTrue(caminho.is_file(), "o arquivo tem que existir de verdade")
            self.assertGreater(caminho.stat().st_size, 0)

    def test_area_vazia_nao_cola_nada(self):
        """Controle negativo: sem nada copiado o terminal nao pode receber lixo."""
        with NaTela() as tela:
            QApplication.instance().clipboard().clear()

            self.assertEqual(self.colar(tela), [])


if __name__ == "__main__":
    unittest.main()
