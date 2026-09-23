from __future__ import annotations

import json
import os

from .caminhos import RAIZ

ARQUIVO = RAIZ / "abas.json"


_ULTIMO: str | None = None


def gravar(abas: list[dict]) -> None:
    """Guarda o que estava aberto pra devolver tudo no proximo start."""
    global _ULTIMO
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
                # o nome da conversa, pro cartao reaberto dizer qual rodava ali
                "titulo": str(aba.get("titulo", ""))[:80],
                "apelido": str(aba.get("apelido", "")),
                "ativa": bool(aba.get("ativa")),
            }
        )
    # conteudo igual nao volta pro disco: era uma gravacao atomica por ciclo de sessoes
    texto = json.dumps(limpas, indent=2, ensure_ascii=False)
    if texto == _ULTIMO:
        return
    RAIZ.mkdir(parents=True, exist_ok=True)
    # o temporario leva o numero do processo: duas janelas abertas gravam ao mesmo tempo
    # e brigariam pelo mesmo nome
    provisorio = ARQUIVO.with_name(f"{ARQUIVO.name}.{os.getpid()}.tmp")
    try:
        provisorio.write_text(texto, encoding="utf-8")
        provisorio.replace(ARQUIVO)
        _ULTIMO = texto
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
