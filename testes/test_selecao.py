"""Botao de selecionar toda a saida.

O selectAll do xterm marca o buffer inteiro. Num app de tela cheia como o Claude Code
isso e quase tudo linha em branco, e o resultado e um retangulo azul gigante com pouco
texto dentro. O botao tem que marcar so a faixa que realmente tem texto.

O teste monta um terminal proprio dentro da pagina em vez de usar a aba do shell, senao
o prompt do PowerShell entraria na conta e o controle negativo nunca ficaria vazio.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


class NaTela:
    """Sobe a janela de verdade e deixa rodar javascript dentro dela."""

    def __enter__(self):
        from loom.app import MainWindow

        QApplication.instance() or QApplication([])
        self.janela = MainWindow()
        self.janela.show()
        self.esperar(3500)
        return self

    def __exit__(self, *_):
        self.janela.bridge.shutdown()
        self.janela.close()
        return False

    def esperar(self, ms: int) -> None:
        laco = QEventLoop()
        QTimer.singleShot(ms, laco.quit)
        laco.exec()

    def js(self, codigo: str, espera: int = 2000):
        caixa = []
        laco = QEventLoop()

        def guardar(valor):
            caixa.append(valor)
            laco.quit()

        self.janela.view.page().runJavaScript(codigo, 0, guardar)
        QTimer.singleShot(espera, laco.quit)
        laco.exec()
        return caixa[0] if caixa else None

    def escrever(self, miolo: str):
        """A barra vertical vira quebra de linha do lado do javascript."""
        codigo = PREPARAR.replace("CONTEUDO", json.dumps(miolo))
        return self.js(codigo)

    def medir(self):
        bruto = self.js(MEDIR)
        assert bruto, "o javascript da pagina nao respondeu"
        return json.loads(bruto)


PREPARAR = """
(function () {
  const QL = String.fromCharCode(13) + String.fromCharCode(10);
  if (!window.__provaTerm) {
    const caixa = document.createElement("div");
    caixa.style.cssText = "position:absolute;left:-9999px;width:800px;height:400px";
    document.body.appendChild(caixa);
    window.__provaTerm = new Terminal({ rows: 24, cols: 80, allowProposedApi: true });
    window.__provaTerm.open(caixa);
  }
  const t = window.__provaTerm;
  t.reset();
  t.write(CONTEUDO.replace(/\|/g, QL));
  return "escreveu";
})()
"""

MEDIR = """
(function () {
  const QUEBRA = String.fromCharCode(10);
  const falsa = { term: window.__provaTerm, ehPainel: false };
  selecionarTudo(falsa);
  const t = falsa.term;
  const s = t.getSelection();
  const linhas = s.split(QUEBRA);
  return JSON.stringify({
    tem: t.hasSelection(),
    primeira: linhas[0].trim(),
    ultima: linhas[linhas.length - 1].trim(),
    total: linhas.length,
  });
})()
"""


class SelecionarTudo(unittest.TestCase):
    def test_marca_do_primeiro_ao_ultimo_texto_e_ignora_o_branco_das_pontas(self):
        with NaTela() as tela:
            self.assertEqual(tela.escrever("||alfa|||omega|||"), "escreveu")
            tela.esperar(400)
            dados = tela.medir()

            self.assertTrue(dados["tem"])
            self.assertEqual(dados["primeira"], "alfa", "nao pode sobrar branco antes do texto")
            self.assertEqual(dados["ultima"], "omega", "nao pode sobrar branco depois do texto")
            self.assertEqual(dados["total"], 4, "alfa, duas vazias do meio e omega")

    def test_tela_so_com_branco_nao_marca_nada(self):
        """Controle negativo: sem texto o botao tem que sair de mao vazia."""
        with NaTela() as tela:
            self.assertEqual(tela.escrever("||||"), "escreveu")
            tela.esperar(400)

            self.assertFalse(tela.medir()["tem"], "branco puro nao e saida pra selecionar")


if __name__ == "__main__":
    unittest.main()
