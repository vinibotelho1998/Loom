from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(os.path.expanduser("~")) / ".loom"
PERFIS = RAIZ / "perfis"


def garantir() -> None:
    PERFIS.mkdir(parents=True, exist_ok=True)
