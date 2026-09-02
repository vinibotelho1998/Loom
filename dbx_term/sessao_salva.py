from __future__ import annotations

import json
import os

from .caminhos import RAIZ

ARQUIVO = RAIZ / "abas.json"


def gravar(abas: list[dict]) -> None:
    """Guarda o que estava aberto pra devolver tudo no proximo start."""
    limpas = []
    for aba in abas[:20]:
        if not isinstance(aba, dict) or not aba.get("shell"):
            continue
        limpas.append(
            {
                "shell": str(aba.get("shell", "")),
                "perfil": str(aba.get("perfil", "")),
                "pasta": str(aba.get("pasta", "")),
                "conversa": str(aba.get("conversa", "")),
                "ativa": bool(aba.get("ativa")),
            }
        )
    RAIZ.mkdir(parents=True, exist_ok=True)
    # o temporario leva o numero do processo: duas janelas abertas gravam ao mesmo tempo
    # e brigariam pelo mesmo nome
    provisorio = ARQUIVO.with_name(f"{ARQUIVO.name}.{os.getpid()}.tmp")
    try:
        provisorio.write_text(json.dumps(limpas, indent=2, ensure_ascii=False), encoding="utf-8")
        provisorio.replace(ARQUIVO)
    except OSError:
        provisorio.unlink(missing_ok=True)


def ler() -> list[dict]:
    if not ARQUIVO.exists():
        return []
    try:
        dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return dados if isinstance(dados, list) else []
