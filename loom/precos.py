"""Preco por token de cada modelo, pra estimar em dolar o uso que o Loom le em disco.

A tabela e um recorte da do LiteLLM (licenca MIT, Copyright (c) 2023 Berri AI), guardada em
loom/dados/precos.json so com os modelos do Claude e da OpenAI. Um ~/.loom/precos.json, se existir,
vale por cima dela campo a campo: da pra corrigir um preco ou por um modelo novo sem esperar versao
nova do Loom. O formato e o mesmo, {"modelos": {nome: campos}}, ou os modelos direto no topo, com os
nomes de campo do LiteLLM. Nada aqui fala com a rede.

A faixa de contexto longo segue o LiteLLM: o pedido cuja entrada, somando o cache lido e o escrito,
passa do limiar e cobrado inteiro na faixa de cima, saida junto, quando o modelo tem essa faixa.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from . import caminhos as mod_caminhos

TABELA = Path(__file__).resolve().parent / "dados" / "precos.json"
NOME_DO_ARQUIVO_DE_CIMA = "precos.json"

# O limiar que separa o consumo de contexto longo, por familia. Ele so separa: quem decide se ha
# preco diferente e a tabela, pelos campos terminados em _above_200k_tokens ou _above_272k_tokens.
LIMIAR_ANTHROPIC = 200_000
LIMIAR_OPENAI = 272_000
FAIXAS = ("_above_200k_tokens", "_above_272k_tokens")

ENTRADA = "input_cost_per_token"
SAIDA = "output_cost_per_token"
ESCRITA_5M = "cache_creation_input_token_cost"
ESCRITA_1H = "cache_creation_input_token_cost_above_1hr"
LEITURA = "cache_read_input_token_cost"

# De onde sai o preco de cada campo do medidor, na ordem. Campo que a tabela nao traz cai no
# seguinte: escrita de cache sem preco proprio e entrada comum, que e o que ela e.
ORDEM_DOS_PRECOS = {
    "entrada": (ENTRADA,),
    "saida": (SAIDA,),
    "cache5m": (ESCRITA_5M, ENTRADA),
    "cache1h": (ESCRITA_1H, ESCRITA_5M, ENTRADA),
    "cacheLido": (LEITURA, ENTRADA),
}

_DATA = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")
_COLCHETE = re.compile(r"\[[^\]]*\]$")
_PREFIXO_DE_NUVEM = re.compile(r"^(?:(?:us|eu|apac|au|jp|global)\.)?(?:anthropic|openai)\.")
_VERSAO_DE_NUVEM = re.compile(r"-v\d+(?::\d+)?$")

_TRAVA = threading.Lock()
_BASE: dict[str, dict] | None = None
_JUNTA: tuple[tuple, dict] | None = None


def _sem_enfeite(modelo) -> str:
    """O nome sem prefixo de provedor, sem o [1m] e sem sufixo de versao de nuvem."""
    nome = str(modelo or "").strip().lower()
    nome = _COLCHETE.sub("", nome)
    nome = nome.rsplit("/", 1)[-1]
    nome = _PREFIXO_DE_NUVEM.sub("", nome)
    nome = nome.split("@", 1)[0]
    return _VERSAO_DE_NUVEM.sub("", nome).strip()


def normalizar(modelo) -> str:
    """O nome do modelo como a tabela conhece: sem data, sem [1m] e sem prefixo de provedor."""
    return _DATA.sub("", _sem_enfeite(modelo))


def limiar(modelo) -> int:
    return LIMIAR_ANTHROPIC if normalizar(modelo).startswith("claude") else LIMIAR_OPENAI


def _ler_modelos(caminho: Path) -> dict[str, dict]:
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(bruto, dict):
        return {}
    modelos = bruto["modelos"] if isinstance(bruto.get("modelos"), dict) else bruto
    limpos: dict[str, dict] = {}
    for nome, campos in modelos.items():
        if not isinstance(nome, str) or not isinstance(campos, dict):
            continue
        valores = {
            campo: float(valor)
            for campo, valor in campos.items()
            if isinstance(campo, str)
            and isinstance(valor, (int, float))
            and not isinstance(valor, bool)
            and valor >= 0
        }
        if valores:
            limpos[nome.strip().lower()] = valores
    return limpos


def _tabela() -> dict[str, dict]:
    """A tabela do Loom com a do usuario por cima, relida quando o arquivo dele muda."""
    global _BASE, _JUNTA
    por_cima = mod_caminhos.RAIZ / NOME_DO_ARQUIVO_DE_CIMA
    try:
        situacao = por_cima.stat()
        marca = (str(por_cima), situacao.st_mtime_ns, situacao.st_size)
    except OSError:
        marca = (str(por_cima), None, None)
    with _TRAVA:
        if _BASE is None:
            _BASE = _ler_modelos(TABELA)
        if _JUNTA is not None and _JUNTA[0] == marca:
            return _JUNTA[1]
        junta = {nome: dict(campos) for nome, campos in _BASE.items()}
        if marca[1] is not None:
            for nome, campos in _ler_modelos(por_cima).items():
                junta[nome] = {**junta.get(nome, {}), **campos}
        _JUNTA = (marca, junta)
        return junta


def recarregar() -> None:
    """Esquece as tabelas lidas. A proxima consulta le tudo de novo."""
    global _BASE, _JUNTA
    with _TRAVA:
        _BASE = None
        _JUNTA = None


def preco(modelo) -> dict | None:
    """Os campos de preco do modelo, ou None quando a tabela nao conhece ele."""
    tabela = _tabela()
    for nome in (normalizar(modelo), _sem_enfeite(modelo)):
        campos = tabela.get(nome)
        if campos and ENTRADA in campos and SAIDA in campos:
            return campos
    return None


def _por_token(campos: dict, campo_do_medidor: str, faixa: str) -> float:
    for chave in ORDEM_DOS_PRECOS[campo_do_medidor]:
        if faixa and chave + faixa in campos:
            return campos[chave + faixa]
        if chave in campos:
            return campos[chave]
    return 0.0


def _quantidade(valor) -> int:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return 0
    return max(0, int(valor))


def custo(modelo, consumo: dict, longo: dict | None = None) -> tuple[float, bool]:
    """Quanto custou o consumo do modelo, e se a tabela tinha preco pra ele.

    `consumo` traz os tokens com os nomes do medidor: entrada sem cache, saida, escrita de cache
    de 5 minutos e de 1 hora e leitura de cache. `longo` e a parte desse consumo que veio de pedido
    acima do limiar de contexto longo. Sem preco o custo e zero, e o segundo valor diz isso: quem
    mostra avisa em vez de inventar numero.
    """
    campos = preco(modelo)
    if campos is None:
        return 0.0, False
    longo = longo or {}
    faixa = next((f for f in FAIXAS if any(c.endswith(f) for c in campos)), "")
    total = 0.0
    for campo in ORDEM_DOS_PRECOS:
        quanto = _quantidade(consumo.get(campo))
        acima = min(quanto, _quantidade(longo.get(campo)))
        total += (quanto - acima) * _por_token(campos, campo, "")
        total += acima * _por_token(campos, campo, faixa)
    return total, True
