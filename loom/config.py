from __future__ import annotations

import json

from .caminhos import RAIZ, gravar_texto

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
    "confirmarFechar": True,
    "abrirCliDaConta": True,
    "mostrarUsoNaBarra": True,
    "larguraLateral": 246,
    "confiarPastas": True,
    "esconderStatusLine": True,
    "restaurarAbas": True,
    # o que cada aba mostrava volta com ela no proximo start, gravado em ~/.loom/historico
    "guardarHistorico": True,
    # a aba reaberta que rodava uma conversa ja volta dentro dela, em vez de parar no prompt
    "retomarConversas": True,
    "avisarQuandoPronta": True,
    "avisoDeLimite": True,
    "fecharAoSair": False,
    "editor": "code",
    "densidade": "confortavel",
    "desenho": "auto",
    # no Linux a barra do Loom, com as abas, fica no lugar da do sistema; ligado, volta a do sistema
    "barraDoSistema": False,
    # CPU, memoria e placa de video na barra de cima
    "mostrarRecursos": True,
    # o painel de uso a direita dos terminais nasce fechado: o botao do topo ou Ctrl+Shift+U abre, e
    # o app lembra do que a pessoa escolheu
    "painelUso": False,
    "larguraPainelUso": 310,
    # quanto da largura fica com o terminal da esquerda na tela dividida em dois
    "fracaoDivisao": 0.5,
}


# range e number chegam da tela como inteiro mesmo quando o padrao e decimal, e o contrario
# tambem acontece. Sem isto, arrastar a entrelinha ate 1 nao gravava e a tela voltava sozinha.
NUMERICOS = {"tamanhoFonte", "entrelinha", "espacoLetra", "historico",
             "larguraLateral", "larguraPainelUso", "fracaoDivisao"}

# A mesma faixa que a tela aceita. Um historico 0 vindo do campo apagado, ou do arquivo editado
# a mao, cortava na hora a rolagem de todas as abas abertas, sem volta.
FAIXAS = {
    "tamanhoFonte": (9, 22),
    "entrelinha": (1.0, 2.0),
    "espacoLetra": (0, 3),
    "historico": (1000, 200000),
    "larguraLateral": (180, 520),
    # mais estreito que isso a barra de 5h perde o texto do zera em; mais largo rouba o terminal
    "larguraPainelUso": (280, 480),
    # mais pro canto que isso um dos dois terminais fica estreito demais pro Claude escrever
    "fracaoDivisao": (0.2, 0.8),
}


# Texto que o motor le antes do Qt subir: fora da lista, o run.py escolheria um motor que nao
# existe. Vale o mesmo pro arquivo editado a mao.
ESCOLHAS = {
    "desenho": ("auto", "gpu", "software"),
}


def _valido(chave: str, valor) -> bool:
    padrao = PADRAO[chave]
    if chave in ESCOLHAS:
        return valor in ESCOLHAS[chave]
    if isinstance(padrao, bool):
        return isinstance(valor, bool)
    if chave in NUMERICOS:
        return isinstance(valor, (int, float)) and not isinstance(valor, bool) and valor == valor
    return isinstance(valor, type(padrao))


def _ajustar(chave: str, valor):
    if chave in FAIXAS:
        menor, maior = FAIXAS[chave]
        valor = min(max(valor, menor), maior)
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
        # JSON valido mas sem ser objeto, tipo null, derrubava o getConfig e a tela nao subia
        if not isinstance(salvo, dict):
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
    gravar_texto(ARQUIVO, json.dumps(atual, indent=2, ensure_ascii=False))
    return atual
