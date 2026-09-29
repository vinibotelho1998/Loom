"""Motor de desenho da tela: com a placa de video ou por software.

O Chromium e o Qt Quick leem o motor das variaveis de ambiente uma vez so, quando sobem. Por
isso a escolha e feita pelo run.py antes de qualquer import do Qt, e vale ate o Loom fechar.

- gpu: o padrao do Qt, sem flag nenhuma. As que o run.py passava sairam todas: desligar a lista
  de drivers ruins do Chromium, textura compartilhada sem copia e quadros sem limite so abriam
  mais caminho pro ANGLE D3D11, que e onde a tela cai, e ele cai dentro do processo do Loom,
  levando junto todos os shells.
- software: tira todo D3D do processo. Sem GPU nao ha WebGL, e o xterm desenha pelo DOM.
- auto: gpu, menos nos 7 dias depois de uma queda da tela, em que sobe em software.

Nada de Qt nos imports, pelo mesmo motivo do run.py.
"""

from __future__ import annotations

import logging
from datetime import datetime

from . import queda as mod_queda

log = logging.getLogger(__name__)

MODOS = ("auto", "gpu", "software")
SOFTWARE = {
    "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
    # so o --disable-gpu tira o ANGLE da pagina, mas o Qt Quick continuaria compondo por D3D11
    "QT_QUICK_BACKEND": "software",
}

# o que o preparar_motor decidiu nesta subida, pra tela explicar depois
decisao: dict = {}


def escolha_de(config: dict) -> str:
    valor = config.get("desenho")
    return valor if valor in MODOS else "auto"


def escolher(escolha: str, queda_recente: bool) -> str:
    """O motor que sobe, a partir do ajuste e de ter caido nos ultimos dias."""
    if escolha in ("gpu", "software"):
        return escolha
    return "software" if queda_recente else "gpu"


def modo_do_ambiente(ambiente) -> str:
    """O motor que o processo usa de fato, lido das variaveis que o Chromium vai ler."""
    flags = str(ambiente.get("QTWEBENGINE_CHROMIUM_FLAGS", "")).split()
    return "software" if "--disable-gpu" in flags else "gpu"


def preparar_motor(ambiente, config: dict, agora: float | None = None) -> dict:
    """Anota a queda da ultima vez, se houve, e escreve no ambiente o motor desta subida.

    Variavel ja definida por fora continua mandando: e o jeito de testar um motor sem mexer
    no ajuste, e ela nao pode ser trocada calada.
    """
    escolha = escolha_de(config)
    mod_queda.conferir_ao_subir(agora)
    queda = mod_queda.recente(agora) if escolha == "auto" else None
    modo = escolher(escolha, queda is not None)
    if modo == "software":
        for chave, valor in SOFTWARE.items():
            ambiente.setdefault(chave, valor)
    em_uso = modo_do_ambiente(ambiente)
    decisao.clear()
    decisao.update(
        {
            "escolha": escolha,
            "modo": em_uso,
            "porQueda": queda is not None and em_uso == "software",
            "queda": queda or {},
            "porFora": em_uso != modo,
        }
    )
    if decisao["porQueda"]:
        log.warning("desenho por software ate %s, por causa da queda de %s", queda["ate"], queda["quando"])
    else:
        log.info("desenho: %s, ajuste %s%s", em_uso, escolha, ", pela variavel de ambiente" if decisao["porFora"] else "")
    return dict(decisao)


def _dia_e_hora(texto) -> str:
    try:
        quando = datetime.fromisoformat(str(texto))
    except (TypeError, ValueError):
        return ""
    return f"{quando:%d/%m} às {quando:%H:%M}"


def _dia(texto) -> str:
    try:
        return f"{datetime.fromisoformat(str(texto)):%d/%m}"
    except (TypeError, ValueError):
        return ""


def situacao(ambiente, config: dict, agora: float | None = None) -> dict:
    """O que a tela de Configuracoes mostra: o motor de agora, o porque e o da proxima subida.

    O motor so troca quando o Loom abre de novo, entao os dois podem ser diferentes: depois de
    Voltar a placa, ou quando a tela caiu nesta mesma subida.
    """
    escolha = escolha_de(config)
    queda = mod_queda.recente(agora)
    causa = queda or decisao.get("queda") or {}
    return {
        "escolha": escolha,
        "emUso": modo_do_ambiente(ambiente),
        "proxima": escolher(escolha, queda is not None),
        "porQueda": bool(decisao.get("porQueda")),
        "porFora": bool(decisao.get("porFora")),
        "quedaEm": _dia_e_hora(causa.get("quando")),
        "ateEm": _dia(causa.get("ate")),
    }
