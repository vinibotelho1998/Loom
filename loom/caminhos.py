from __future__ import annotations

import os
import time
from pathlib import Path

RAIZ = Path(os.path.expanduser("~")) / ".loom"
PERFIS = RAIZ / "perfis"


def garantir() -> None:
    PERFIS.mkdir(parents=True, exist_ok=True)


def gravar_texto(alvo: Path, texto: str) -> None:
    """Grava o arquivo inteiro ou nao mexe nele.

    O write_text direto zera o arquivo antes de escrever. App fechado no meio, ou disco
    cheio, deixava o arquivo vazio, a leitura seguinte caia no padrao e a proxima gravacao
    confirmava a perda: a lista de contas ou os ajustes voltavam do zero. O temporario leva
    o numero do processo, como no abas.json, porque o coletor roda em processo proprio.
    """
    alvo.parent.mkdir(parents=True, exist_ok=True)
    provisorio = alvo.with_name(f"{alvo.name}.{os.getpid()}.tmp")
    try:
        provisorio.write_text(texto, encoding="utf-8")
        for tentativa in range(3):
            try:
                provisorio.replace(alvo)
                return
            except PermissionError:
                # no Windows, quem esta lendo o arquivo naquele instante segura a troca
                if tentativa == 2:
                    raise
                time.sleep(0.05)
    except OSError:
        provisorio.unlink(missing_ok=True)
        raise
