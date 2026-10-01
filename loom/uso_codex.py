"""Uso do Codex CLI, lido dos rollouts que ele grava em disco.

Cada resposta do modelo vira um token_usage_record com o consumo dela e o response_id. Rollout de
antes desse registro so tem o evento token_count, com o total acumulado da conversa: ai o consumo de
cada resposta e a diferenca entre dois totais seguidos. O limite do plano vem no proprio token_count,
em rate_limits, com o percentual usado de cada janela e quando ela zera. O modelo vem do
turn_context do turno. Nada aqui le o auth.json nem fala com a rede.

A leitura e incremental como a do Claude, e o resultado sai no mesmo formato de balde do medidor,
pra conta de custo ser uma so.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import precos as mod_precos
from . import uso as mod_uso
from .uso import SEM_ESFORCO
from .caminhos import gravar_texto

VERSAO = 3
# A versao 2 guarda o projeto de cada resposta e a 3, o esforco; o cache da 1 e jogado fora e os rollouts, relidos.
# Arquivo parado ha mais que isso nao ganha mais registro: o consumo que ficou pendente, tirado da
# diferenca dos totais, passa a valer. Antes disso ele espera, porque no rollout novo o token_count
# as vezes chega antes do registro da mesma resposta, e contaria duas vezes.
QUIETO = 10 * 60
# o modelo de cada turno, pra resposta que chega numa passada e o turn_context na anterior
TURNOS_GUARDADOS = 64
PENDENTES_GUARDADOS = 5000
CAMPOS_DO_TOTAL = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_MARCAS = (b"token_usage_record", b"token_count", b"turn_context", b"session_meta")
_TRAVA = threading.Lock()


def _interessa(linha: bytes) -> bool:
    # o tipo vem no comeco da linha; o resto do rollout e conversa, e json.loads nela e custo a toa
    cabeca = linha[:256]
    return any(marca in cabeca for marca in _MARCAS)


def _inteiro(valor) -> int:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return 0
    return max(0, int(valor))


def _instante(carimbo) -> float | None:
    if not isinstance(carimbo, str) or not carimbo:
        return None
    try:
        return datetime.fromisoformat(carimbo.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _consumo(bruto, instante: float | None, modelo: str) -> dict | None:
    """Uma resposta do Codex no formato do medidor do Claude.

    Na OpenAI a entrada ja inclui o cache lido; a entrada sem cache e a total menos a lida. O
    raciocinio ja esta dentro da saida.
    """
    if not isinstance(bruto, dict) or instante is None:
        return None
    total = _inteiro(bruto.get("input_tokens"))
    lido = min(_inteiro(bruto.get("cached_input_tokens")), total)
    escrito = min(_inteiro(bruto.get("cache_write_input_tokens")), total - lido)
    saida = _inteiro(bruto.get("output_tokens"))
    if not total and not saida:
        return None
    try:
        dia = str(datetime.fromtimestamp(instante).date())
    except (ValueError, OverflowError, OSError):
        return None
    return {
        "dia": dia,
        "ts": instante,
        "modelo": modelo,
        "entrada": total - lido - escrito,
        "saida": saida,
        "raciocinio": _inteiro(bruto.get("reasoning_output_tokens")),
        "cacheEscrito": escrito,
        "cacheLido": lido,
        "mensagens": 1,
        "cache5m": escrito,
        "cache1h": 0,
        "longo": total > mod_precos.limiar(modelo),
    }


def _diferenca(atual: dict, anterior) -> dict | None:
    """O consumo entre dois token_count seguidos. Total que diminuiu recomecou do zero."""
    if isinstance(anterior, dict) and not any(atual[c] < _inteiro(anterior.get(c)) for c in CAMPOS_DO_TOTAL):
        delta = {c: atual[c] - _inteiro(anterior.get(c)) for c in CAMPOS_DO_TOTAL}
    else:
        delta = dict(atual)
    return delta if any(delta.values()) else None


def _somar(
    conta: dict, chave: str, consumo: dict, corte: float, projeto: str = "desconhecido", esforco: str = SEM_ESFORCO
) -> None:
    mod_uso._acumular(conta["dias"].setdefault(f"{chave}|{consumo['dia']}|{consumo['modelo']}", {}), consumo)
    mod_uso._acumular(
        conta["projetos"].setdefault(f"{chave}|{consumo['dia']}|{projeto}|{consumo['modelo']}", {}), consumo
    )
    mod_uso._acumular(
        conta["esforcos"].setdefault(f"{chave}|{consumo['dia']}|{consumo['modelo']}|{esforco}", {}), consumo
    )
    if consumo["ts"] >= corte:
        mod_uso._acumular_hora(conta["horas"], chave, consumo)
    if consumo["ts"] > conta.get("vistoEm", 0):
        conta["vistoEm"] = int(consumo["ts"])


def _anotar_limite(conta: dict, bruto, instante: float | None) -> None:
    """Guarda a leitura de limite mais nova. Os arquivos vem do mais novo pro mais velho."""
    if not isinstance(bruto, dict) or instante is None:
        return
    if bruto.get("limit_id") not in (None, "codex"):
        return
    atual = conta.get("limites") if isinstance(conta.get("limites"), dict) else None
    if atual is not None and atual.get("ts", 0) >= instante:
        return
    janelas = []
    for lado in ("primary", "secondary"):
        janela = bruto.get(lado)
        if not isinstance(janela, dict):
            continue
        usado = janela.get("used_percent")
        usado = float(usado) if isinstance(usado, (int, float)) and not isinstance(usado, bool) else None
        zera = janela.get("resets_at")
        if isinstance(zera, bool) or not isinstance(zera, (int, float)):
            # versao antiga do Codex dizia quanto faltava, e nao quando
            falta = janela.get("resets_in_seconds")
            zera = instante + falta if isinstance(falta, (int, float)) and not isinstance(falta, bool) else None
        minutos = janela.get("window_minutes")
        minutos = minutos if isinstance(minutos, int) and not isinstance(minutos, bool) and minutos > 0 else None
        if usado is None and zera is None:
            continue
        janelas.append({"minutos": minutos, "usado": usado, "zera": zera})
    plano = bruto.get("plan_type")
    conta["limites"] = {
        "ts": instante,
        "janelas": janelas,
        "plano": plano if isinstance(plano, str) else (atual or {}).get("plano", ""),
    }


def _assentar(conta: dict, chave: str, arquivo: dict, corte: float) -> None:
    """O consumo tirado dos totais passa a valer: o arquivo parou e nunca teve registro."""
    for item in arquivo.get("pendente") or []:
        if isinstance(item, dict):
            consumo = _consumo(item, item.get("ts"), str(item.get("modelo") or "desconhecido"))
            if consumo is not None:
                _somar(
                    conta, chave, consumo, corte, arquivo.get("cwd") or "desconhecido", item.get("esforco") or SEM_ESFORCO
                )
    arquivo["pendente"] = []
    arquivo["modo"] = "contagem"


def _fechar_contagem(carga: dict, conta: dict, chave: str, arquivo: dict, corte: float) -> None:
    """Passa a valer o consumo tirado dos totais que veio antes do primeiro registro do arquivo."""
    pendente = [item for item in arquivo.get("pendente") or [] if isinstance(item, dict)]
    usado = carga.get("usage") if isinstance(carga.get("usage"), dict) else {}
    desta = _inteiro(usado.get("input_tokens")) + _inteiro(usado.get("output_tokens"))
    if pendente and desta and _inteiro(pendente[-1].get("input_tokens")) + _inteiro(
        pendente[-1].get("output_tokens")
    ) == desta:
        pendente.pop()
    arquivo["pendente"] = pendente
    _assentar(conta, chave, arquivo, corte)


def _processar(registros: list, conta: dict, chave: str, arquivo: dict, corte: float) -> None:
    turnos = arquivo.setdefault("turnos", {})
    esforcos_dos_turnos = arquivo.setdefault("esforcosDosTurnos", {})
    ids = set()
    if arquivo.get("ultimo"):
        ids.add(arquivo["ultimo"])
    for registro in registros:
        if not isinstance(registro, dict):
            continue
        tipo = registro.get("type")
        carga = registro.get("payload")
        if not isinstance(carga, dict):
            continue
        if tipo in ("session_meta", "turn_context"):
            pasta = carga.get("cwd")
            if isinstance(pasta, str) and pasta.strip():
                arquivo["cwd"] = pasta.strip().rstrip("\\/") or pasta.strip()
        if tipo == "turn_context":
            nivel = carga.get("effort")
            if isinstance(nivel, str) and nivel.strip():
                arquivo["esforco"] = nivel.strip().lower()
                if isinstance(carga.get("turn_id"), str) and carga["turn_id"]:
                    esforcos_dos_turnos[carga["turn_id"]] = arquivo["esforco"]
                    while len(esforcos_dos_turnos) > TURNOS_GUARDADOS:
                        del esforcos_dos_turnos[next(iter(esforcos_dos_turnos))]
            modelo = carga.get("model")
            if isinstance(modelo, str) and modelo:
                arquivo["modelo"] = modelo
                turno = carga.get("turn_id")
                if isinstance(turno, str) and turno:
                    turnos[turno] = modelo
                    while len(turnos) > TURNOS_GUARDADOS:
                        del turnos[next(iter(turnos))]
        elif tipo == "token_usage_record":
            # daqui em diante o consumo vem dos registros. O que os totais mostraram antes do primeiro
            # registro e consumo de verdade e entra, menos o ultimo item quando ele e a propria
            # resposta deste registro, que o token_count anunciou um instante antes.
            if arquivo.get("modo") != "registro":
                _fechar_contagem(carga, conta, chave, arquivo, corte)
            arquivo["modo"] = "registro"
            arquivo["pendente"] = []
            resposta = carga.get("response_id")
            if isinstance(resposta, str) and resposta:
                if resposta in ids:
                    continue
                ids.add(resposta)
                arquivo["ultimo"] = resposta
            turno = carga.get("turn_id")
            modelo = (turnos.get(turno) if isinstance(turno, str) else None) or arquivo.get("modelo") or "desconhecido"
            consumo = _consumo(carga.get("usage"), _instante(registro.get("timestamp")), modelo)
            if consumo is not None:
                nivel = (esforcos_dos_turnos.get(turno) if isinstance(turno, str) else None) or arquivo.get("esforco")
                _somar(conta, chave, consumo, corte, arquivo.get("cwd") or "desconhecido", nivel or SEM_ESFORCO)
        elif tipo == "event_msg" and carga.get("type") == "token_count":
            instante = _instante(registro.get("timestamp"))
            _anotar_limite(conta, carga.get("rate_limits"), instante)
            if arquivo.get("modo") == "registro" or instante is None:
                continue
            info = carga.get("info")
            total = info.get("total_token_usage") if isinstance(info, dict) else None
            if not isinstance(total, dict):
                continue
            atual = {c: _inteiro(total.get(c)) for c in CAMPOS_DO_TOTAL}
            delta = _diferenca(atual, arquivo.get("total"))
            arquivo["total"] = atual
            pendente = arquivo.setdefault("pendente", [])
            if delta and len(pendente) < PENDENTES_GUARDADOS:
                pendente.append(
                    {
                        "ts": instante,
                        "modelo": arquivo.get("modelo") or "desconhecido",
                        "esforco": arquivo.get("esforco"),
                        **delta,
                    }
                )


def _rollouts(pasta: Path) -> list[tuple[float, int, Path]]:
    """Os rollouts da pasta, o mais novo primeiro.

    O Codex move a conversa arquivada pra archived_sessions com o mesmo nome; a chave e o nome, e
    ela nao conta duas vezes.
    """
    achados: dict[str, tuple[float, int, Path]] = {}
    for sub in ("sessions", "archived_sessions"):
        raiz = pasta / sub
        if not raiz.is_dir():
            continue
        for caminho in raiz.rglob("*.jsonl"):
            try:
                marca = caminho.stat()
            except OSError:
                continue
            atual = achados.get(caminho.name)
            if atual is None or marca.st_size > atual[1]:
                achados[caminho.name] = (marca.st_mtime, marca.st_size, caminho)
    return sorted(achados.values(), key=lambda achado: achado[0], reverse=True)


def _conta_vazia() -> dict:
    return {"arquivos": {}, "dias": {}, "horas": {}, "projetos": {}, "esforcos": {}, "limites": None, "vistoEm": 0}


def _carregar(caminho: Path) -> dict:
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        bruto = None
    if not isinstance(bruto, dict) or bruto.get("versao") != VERSAO or not isinstance(bruto.get("contas"), dict):
        return {"versao": VERSAO, "contas": {}}
    return bruto


def _conta_do_cache(dados: dict, chave: str) -> dict:
    conta = dados["contas"].get(chave)
    limpa = _conta_vazia()
    if isinstance(conta, dict):
        for campo in ("arquivos", "dias", "horas", "projetos", "esforcos"):
            if isinstance(conta.get(campo), dict):
                limpa[campo] = conta[campo]
        if isinstance(conta.get("limites"), dict):
            limpa["limites"] = conta["limites"]
        limpa["vistoEm"] = _inteiro(conta.get("vistoEm"))
    dados["contas"][chave] = limpa
    return limpa


def _ler(pasta: Path, chave: str, conta: dict, parar) -> tuple[bool, bool]:
    arquivos = conta["arquivos"]
    mudou = parcial = False
    orcamento = mod_uso.TETO_TOTAL_POR_PASSADA
    agora = time.time()
    corte = agora - mod_uso.DIAS_DE_HORAS * 24 * 3600
    for mtime, tamanho, caminho in _rollouts(pasta):
        if parar is not None and parar.is_set():
            parcial = True
            break
        nome = caminho.name
        arquivo = arquivos.get(nome) if isinstance(arquivos.get(nome), dict) else {}
        desde = _inteiro(arquivo.get("fim"))
        quieto = agora - mtime > QUIETO
        if tamanho == desde:
            if arquivo.get("pendente") and quieto:
                _assentar(conta, chave, arquivo, corte)
                arquivos[nome] = arquivo
                mudou = True
            continue
        if orcamento <= 0:
            parcial = True
            continue
        teto = min(mod_uso.TETO_POR_PASSADA, orcamento)
        registros, novo_fim = mod_uso._ler_novidades(caminho, desde, teto, filtro=_interessa)
        if novo_fim < desde:
            # o arquivo foi reescrito do zero: o que se sabia dele nao vale mais
            arquivo, desde = {}, 0
        orcamento -= novo_fim - desde
        cortado = tamanho - desde > teto
        if cortado and (novo_fim > desde or teto < mod_uso.TETO_POR_PASSADA):
            parcial = True
        if not registros and novo_fim == desde:
            continue
        _processar(registros, conta, chave, arquivo, corte)
        if arquivo.get("pendente") and quieto and not cortado:
            _assentar(conta, chave, arquivo, corte)
        arquivo["fim"] = novo_fim
        arquivos[nome] = arquivo
        mudou = True
    return mudou, parcial


def coletar(pasta: Path, chave: str, estado: dict | None = None, *, cache: Path, parar=None) -> dict:
    """Uma passada incremental nos rollouts de uma pasta do Codex.

    Devolve {"dias", "horas", "limites", "vistoEm"} daquela conta: os baldes no formato do medidor,
    com a chave da conta na frente, a leitura de limite mais nova e o instante da ultima resposta.
    Com `estado`, diz em estado["parcial"] se ficou algo pra proxima passada.
    """
    with _TRAVA:
        dados = _carregar(cache)
        conta = _conta_do_cache(dados, chave)
        mudou, parcial = _ler(Path(pasta), chave, conta, parar)
        if mudou:
            conta["horas"] = mod_uso._podar_horas(conta["horas"], time.time() - mod_uso.DIAS_DE_HORAS * 24 * 3600)
            corte_dos_dias = str(datetime.now().date() - timedelta(days=mod_uso.DIAS_DE_PROJETOS))
            conta["projetos"] = mod_uso._podar_projetos(conta["projetos"], corte_dos_dias)
            conta["esforcos"] = mod_uso._podar_projetos(conta["esforcos"], corte_dos_dias)
            gravar_texto(cache, json.dumps(dados))
    if estado is not None:
        estado["parcial"] = parcial
    return {
        "dias": conta["dias"],
        "horas": conta["horas"],
        "projetos": conta["projetos"],
        "esforcos": conta["esforcos"],
        "limites": conta["limites"],
        "vistoEm": conta["vistoEm"],
    }
