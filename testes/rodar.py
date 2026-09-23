r"""Roda a bateria toda.

    .venv\Scripts\python.exe testes\rodar.py
    .venv\Scripts\python.exe testes\rodar.py rapido   (pula o que abre processo)

Nenhum teste de dados encosta na pasta de verdade: cada um roda numa pasta temporaria.
Os de janela sobem a tela inteira, e a tela abre a aba padrao com um shell de verdade.
"""

import sys
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent))

# Filtrar pelo nome do arquivo e nao por padrao de letra: o antigo test_[cus]* pegava
# test_colar e test_selecao, que sobem a janela, e com ela um shell e a pasta de dados real.
ABREM_PROCESSO = {"test_terminal", "test_colar", "test_selecao", "test_arrastar"}

rapido = "rapido" in sys.argv
if rapido:
    testes = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromName(arquivo.stem)
        for arquivo in sorted(AQUI.glob("test_*.py"))
        if arquivo.stem not in ABREM_PROCESSO
    )
else:
    testes = unittest.defaultTestLoader.discover(str(AQUI), pattern="test_*.py")
resultado = unittest.TextTestRunner(verbosity=2).run(testes)
raise SystemExit(0 if resultado.wasSuccessful() else 1)
