"""Atalho de janela: mesmo ponto de entrada, sem abrir console.

O Windows usa a extensao .pyw pra rodar com pythonw.exe, que nao cria janela preta.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "run.py"), run_name="__main__")
