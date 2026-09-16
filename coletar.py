"""Entrada do coletor para ser chamada de fora do projeto.

Existe porque a status line e os hooks do Claude Code rodam com outro diretorio e
sem o projeto no PYTHONPATH, entao `-m loom.coletor` nao acha o pacote.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loom.coletor import main  # noqa: E402

raise SystemExit(main())
