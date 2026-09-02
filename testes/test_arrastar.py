"""Arrastar arquivo pra dentro da janela.

Dentro da pagina o arquivo chega sem caminho nenhum, por seguranca do navegador, entao
quem resolve isso e o filtro do lado do Qt. Se ele parar de funcionar, arrastar nao faz nada
e nao aparece erro em lugar nenhum, que e o pior tipo de falha.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def soltar_na_janela(dados: QMimeData) -> tuple[bool, list[str]]:
    from dbx_term.app import MainWindow

    QApplication.instance() or QApplication([])
    janela = MainWindow()
    recebidos: list[str] = []
    janela.bridge.arquivosSoltos.connect(recebidos.append)

    evento = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        dados,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tratou = janela.eventFilter(janela, evento)
    janela.bridge.shutdown()
    return tratou, recebidos


class ArrastarArquivo(unittest.TestCase):
    def test_o_caminho_chega_inteiro_e_com_a_barra_do_windows(self):
        dados = QMimeData()
        dados.setUrls([QUrl.fromLocalFile("C:/Users/eu/pasta com espaco/arquivo.txt")])

        tratou, recebidos = soltar_na_janela(dados)

        self.assertTrue(tratou, "o filtro precisa consumir o drop antes do motor web")
        self.assertEqual(len(recebidos), 1)
        esperado = str(Path("C:/Users/eu/pasta com espaco/arquivo.txt"))
        self.assertEqual(json.loads(recebidos[0]), [esperado])

    def test_varios_arquivos_de_uma_vez(self):
        dados = QMimeData()
        dados.setUrls([QUrl.fromLocalFile("C:/a/um.txt"), QUrl.fromLocalFile("C:/a/dois.txt")])

        _, recebidos = soltar_na_janela(dados)

        self.assertEqual(len(json.loads(recebidos[0])), 2)

    def test_arrasto_sem_arquivo_nao_e_consumido(self):
        dados = QMimeData()
        dados.setText("so um texto")

        _, recebidos = soltar_na_janela(dados)

        self.assertEqual(recebidos, [], "texto solto nao e arquivo e nao vira caminho")


if __name__ == "__main__":
    unittest.main()
