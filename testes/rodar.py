"""Roda a bateria toda.

    .venv\Scripts\python.exe testes\rodar.py
    .venv\Scripts\python.exe testes\rodar.py rapido   (pula o que abre processo)

Nenhum teste encosta na pasta de dados de verdade: cada um roda numa pasta temporaria.
"""

import sys
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent))

rapido = "rapido" in sys.argv
padrao = "test_*.py" if not rapido else "test_[cus]*.py"

testes = unittest.defaultTestLoader.discover(str(AQUI), pattern=padrao)
resultado = unittest.TextTestRunner(verbosity=2).run(testes)
raise SystemExit(0 if resultado.wasSuccessful() else 1)
