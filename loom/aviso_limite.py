"""Aviso de limite: o Windows avisa quando uma conta passa de 85% e de 95% numa janela de uso.

A barra de cima e o painel mostram o numero, mas so pra quem esta olhando pra eles. O aviso vem uma
vez por degrau e por janela: a mesma sessao de 5 horas nao avisa de novo a cada minuto, e a sessao
seguinte, que comeca do zero, arma o aviso outra vez. O que ja foi avisado fica guardado em disco,
pra tela que nasce depois de uma queda nao repetir tudo.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

from . import caminhos as mod_caminhos

DEGRAUS = (85, 95)
ARQUIVO = "avisos-limite.json"

NOMES_DAS_JANELAS = {
    "five_hour": "sessão de 5 horas",
    "seven_day": "semana",
    "5h": "sessão de 5 horas",
    "7d": "semana",
    "30d": "mês",
}


def _numero(valor) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return None
    return float(valor)


def _instante(iso) -> float | None:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


# O Codex manda o reinicio da mesma janela variando alguns segundos de uma leitura pra outra; nos
# rollouts reais a semana chegou com ate 13 s de diferenca. Comparando o numero exato, cada variacao
# parecia janela nova e o aviso de 95% saia de novo. Janelas diferentes ficam horas longe.
TOLERANCIA_DA_JANELA = 120


def _mesma_janela(uma, outra) -> bool:
    if uma is None or outra is None:
        return uma is None and outra is None
    try:
        return abs(float(uma) - float(outra)) <= TOLERANCIA_DA_JANELA
    except (TypeError, ValueError):
        return False


def leituras_do_claude(limites: dict, nomes: dict[str, str]) -> list[dict]:
    """As janelas de cada conta do Claude, como a status line leu por ultimo."""
    leituras = []
    for chave, dados in limites.items():
        faixas = dados.get("faixas") if isinstance(dados, dict) else None
        if not isinstance(faixas, dict):
            continue
        conta = nomes.get(chave) or ("Principal" if chave == "__padrao__" else chave)
        for janela, faixa in faixas.items():
            usado = _numero((faixa or {}).get("usado")) if isinstance(faixa, dict) else None
            if usado is None:
                continue
            leituras.append(
                {
                    "chave": f"claude:{chave}:{janela}",
                    "ferramenta": "Claude",
                    "conta": conta,
                    "janela": NOMES_DAS_JANELAS.get(janela, janela),
                    "usado": usado,
                    "zera": _numero(faixa.get("zera")),
                }
            )
    return leituras


def leituras_do_painel(painel: dict) -> list[dict]:
    """As janelas do Codex no relatorio do painel. As do Claude vem da status line, mais fresca."""
    leituras = []
    for conta in (painel or {}).get("contas") or []:
        if not isinstance(conta, dict) or conta.get("ferramenta") != "codex":
            continue
        for janela in conta.get("janelas") or []:
            # so o limite que a propria ferramenta informou; estimativa nao vira aviso
            if not isinstance(janela, dict) or janela.get("origem") != "real":
                continue
            usado = _numero(janela.get("usado"))
            if usado is None:
                continue
            leituras.append(
                {
                    "chave": f"{conta.get('id')}:{janela.get('tipo')}",
                    "ferramenta": "Codex",
                    "conta": conta.get("nome") or "Codex",
                    "janela": NOMES_DAS_JANELAS.get(janela.get("tipo"), str(janela.get("tipo") or "janela")),
                    "usado": usado,
                    "zera": _instante(janela.get("zeraEm")),
                }
            )
    return leituras


def contagem(segundos: float) -> str:
    """Quanto falta, do jeito do painel: 2d 3h, 1h 10m, 45m."""
    total = max(1, math.ceil(segundos / 60))
    dias, horas, minutos = total // 1440, (total // 60) % 24, total % 60
    if dias:
        return f"{dias}d {horas}h" if horas else f"{dias}d {minutos}m" if minutos else f"{dias}d"
    if horas:
        return f"{horas}h {minutos}m" if minutos else f"{horas}h"
    return f"{total}m"


def texto_do_aviso(leitura: dict, agora: float) -> dict:
    usado = round(leitura["usado"])
    titulo = f"{leitura['ferramenta']}: limite em {usado}%"
    texto = f"{leitura['conta']}, {leitura['janela']} em {usado}%."
    if leitura.get("zera") and leitura["zera"] > agora:
        texto += f" Renova em {contagem(leitura['zera'] - agora)}."
    return {"titulo": titulo, "texto": texto, "chave": leitura["chave"]}


def avisos_novos(leituras: list[dict], vistos: dict, agora: float) -> tuple[list[dict], dict]:
    """Os avisos que ainda nao foram dados, e o registro do que ja foi avisado, atualizado."""
    # janela que ja renovou sai do registro: a proxima comeca do zero e arma o aviso de novo
    vistos = {
        chave: visto
        for chave, visto in (vistos or {}).items()
        if isinstance(visto, dict) and (not visto.get("zera") or visto["zera"] > agora)
    }
    avisos = []
    for leitura in leituras:
        if leitura.get("zera") and leitura["zera"] <= agora:
            # leitura velha, de uma janela que ja renovou: o numero dela nao vale mais
            continue
        degrau = max((d for d in DEGRAUS if leitura["usado"] >= d), default=None)
        if degrau is None:
            continue
        antes = vistos.get(leitura["chave"])
        if antes and _mesma_janela(antes.get("zera"), leitura.get("zera")) and antes.get("degrau", 0) >= degrau:
            continue
        vistos[leitura["chave"]] = {"zera": leitura.get("zera"), "degrau": degrau}
        avisos.append(texto_do_aviso(leitura, agora))
    return avisos, vistos


def ler_vistos() -> dict:
    try:
        dados = json.loads((mod_caminhos.RAIZ / ARQUIVO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def gravar_vistos(vistos: dict) -> None:
    try:
        mod_caminhos.gravar_texto(mod_caminhos.RAIZ / ARQUIVO, json.dumps(vistos, ensure_ascii=False))
    except OSError:
        # sem gravar, o pior e avisar de novo depois de reabrir; nao vale derrubar a vigia
        pass
