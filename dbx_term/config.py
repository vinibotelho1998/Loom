from __future__ import annotations

import json

from .caminhos import RAIZ

ARQUIVO = RAIZ / "config.json"

PADRAO = {
    "fonte": "Hack",
    "tamanhoFonte": 14,
    "entrelinha": 1.25,
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
    "mostrarSessoesDeFora": True,
    "avisarQuandoPronta": True,
    "fecharAoSair": False,
    "editor": "code",
    "densidade": "confortavel",
}


# range e number chegam da tela como inteiro mesmo quando o padrao e decimal, e o contrario
# tambem acontece. Sem isto, arrastar a entrelinha ate 1 nao gravava e a tela voltava sozinha.
NUMERICOS = {"tamanhoFonte", "entrelinha", "espacoLetra", "historico", "avisarColagemLinhas",
             "alturaExternas", "larguraLateral"}


def _valido(chave: str, valor) -> bool:
    padrao = PADRAO[chave]
    if isinstance(padrao, bool):
        return isinstance(valor, bool)
    if chave in NUMERICOS:
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    return isinstance(valor, type(padrao))


def _ajustar(chave: str, valor):
    if chave in NUMERICOS and isinstance(PADRAO[chave], int):
        return int(valor)
    if chave in NUMERICOS and isinstance(PADRAO[chave], float):
        return float(valor)
    return valor


def carregar() -> dict:
    dados = dict(PADRAO)
    if ARQUIVO.exists():
        try:
            salvo = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            salvo = {}
        for chave, valor in salvo.items():
            if chave in PADRAO and _valido(chave, valor):
                dados[chave] = _ajustar(chave, valor)
    return dados


def salvar(novo: dict) -> dict:
    atual = carregar()
    for chave, valor in novo.items():
        if chave in PADRAO and _valido(chave, valor):
            atual[chave] = _ajustar(chave, valor)
    RAIZ.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")
    return atual
