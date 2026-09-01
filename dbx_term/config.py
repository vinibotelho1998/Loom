from __future__ import annotations

import json

from .caminhos import RAIZ

ARQUIVO = RAIZ / "config.json"

PADRAO = {
    "fonte": "JetBrains Mono",
    "tamanhoFonte": 13,
    "entrelinha": 1.3,
    "espacoLetra": 0,
    "cursor": "bar",
    "cursorPisca": True,
    "historico": 20000,
    "shellPadrao": "",
    "perfilPadrao": "",
    "mostrarFps": False,
    "avisarColagemLinhas": 5,
    "confirmarFechar": True,
    "abrirCliDaConta": True,
    "mostrarUsoNaBarra": True,
    "alturaExternas": 25,
    "larguraLateral": 246,
    "confiarPastas": True,
    "esconderStatusLine": True,
    "restaurarAbas": True,
}


def carregar() -> dict:
    dados = dict(PADRAO)
    if ARQUIVO.exists():
        try:
            salvo = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            salvo = {}
        for chave, valor in salvo.items():
            if chave in PADRAO and isinstance(valor, type(PADRAO[chave])):
                dados[chave] = valor
    return dados


def salvar(novo: dict) -> dict:
    atual = carregar()
    for chave, valor in novo.items():
        if chave in PADRAO and isinstance(valor, type(PADRAO[chave])):
            atual[chave] = valor
    RAIZ.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")
    return atual
