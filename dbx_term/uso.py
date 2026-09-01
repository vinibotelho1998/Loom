from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .caminhos import RAIZ

CACHE = RAIZ / "uso-cache.json"

CAMPOS = ("entrada", "saida", "raciocinio", "cacheEscrito", "cacheLido", "mensagens")


def _pasta_padrao() -> Path:
    """Onde mora a conta que ja estava logada antes do DBX Term existir."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path(os.path.expanduser("~")) / ".claude"))


def _carregar_cache() -> dict:
    if not CACHE.exists():
        return {"arquivos": {}, "dias": {}}
    try:
        bruto = json.loads(CACHE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"arquivos": {}, "dias": {}}
    if not isinstance(bruto.get("arquivos"), dict) or not isinstance(bruto.get("dias"), dict):
        return {"arquivos": {}, "dias": {}}
    return bruto


def _gravar_cache(cache: dict) -> None:
    RAIZ.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")


def _ler_novidades(caminho: Path, desde: int) -> tuple[list[dict], int]:
    """Le so o que cresceu no arquivo desde a ultima passada."""
    try:
        tamanho = caminho.stat().st_size
    except OSError:
        return [], desde
    if tamanho < desde:
        desde = 0
    if tamanho == desde:
        return [], desde
    linhas = []
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(desde)
            bruto = fh.read()
            fim = desde + len(bruto.encode("utf-8", errors="replace"))
    except OSError:
        return [], desde
    corte = bruto.rfind("\n")
    if corte == -1:
        return [], desde
    fim = desde + len(bruto[: corte + 1].encode("utf-8", errors="replace"))
    for linha in bruto[:corte].splitlines():
        if '"usage"' not in linha:
            continue
        try:
            linhas.append(json.loads(linha))
        except ValueError:
            continue
    return linhas, fim


def _somar(registro: dict, alvo: Counter) -> str | None:
    mensagem = registro.get("message") or {}
    consumo = mensagem.get("usage")
    if not consumo or mensagem.get("role") != "assistant":
        return None
    carimbo = registro.get("timestamp")
    if not carimbo:
        return None
    try:
        quando = datetime.fromisoformat(carimbo.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
    alvo["entrada"] += consumo.get("input_tokens", 0)
    alvo["saida"] += consumo.get("output_tokens", 0)
    alvo["cacheEscrito"] += consumo.get("cache_creation_input_tokens", 0)
    alvo["cacheLido"] += consumo.get("cache_read_input_tokens", 0)
    alvo["raciocinio"] += (consumo.get("output_tokens_details") or {}).get("thinking_tokens", 0)
    alvo["mensagens"] += 1
    return f"{quando.date()}|{mensagem.get('model', 'desconhecido')}"


def coletar(pastas: dict[str, Path]) -> dict:
    """Varre os transcript de cada conta e devolve o consumo agregado.

    A leitura e incremental: guardamos ate onde cada arquivo ja foi lido e,
    na proxima passada, so o que cresceu entra na conta.
    """
    cache = _carregar_cache()
    arquivos = cache["arquivos"]
    dias = cache["dias"]

    for chave_perfil, pasta in pastas.items():
        raiz = Path(pasta) / "projects"
        if not raiz.is_dir():
            continue
        for arquivo in raiz.rglob("*.jsonl"):
            chave = str(arquivo)
            desde = arquivos.get(chave, 0)
            registros, novo_fim = _ler_novidades(arquivo, desde)
            if not registros and novo_fim == desde:
                continue
            vistos = defaultdict(Counter)
            for registro in registros:
                parcial = Counter()
                chave_dia = _somar(registro, parcial)
                if chave_dia:
                    vistos[chave_dia].update(parcial)
            for chave_dia, contagem in vistos.items():
                destino = dias.setdefault(f"{chave_perfil}|{chave_dia}", {})
                for campo in CAMPOS:
                    destino[campo] = destino.get(campo, 0) + contagem[campo]
            arquivos[chave] = novo_fim

    _gravar_cache({"arquivos": arquivos, "dias": dias})
    return dias


def relatorio(pastas: dict[str, Path]) -> dict:
    dias = coletar(pastas)

    hoje = datetime.now().date()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    inicio_mes = hoje.replace(day=1)
    trinta = hoje - timedelta(days=29)

    por_perfil: dict[str, dict] = {}
    serie: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    modelos: dict[str, Counter] = defaultdict(Counter)

    for chave, valores in dias.items():
        partes = chave.split("|")
        if len(partes) != 3:
            continue
        perfil, dia_texto, modelo = partes
        try:
            dia = datetime.strptime(dia_texto, "%Y-%m-%d").date()
        except ValueError:
            continue

        alvo = por_perfil.setdefault(
            perfil, {"hoje": Counter(), "semana": Counter(), "mes": Counter(), "total": Counter(), "ultimoDia": ""}
        )
        contagem = Counter({campo: valores.get(campo, 0) for campo in CAMPOS})
        alvo["total"].update(contagem)
        if dia == hoje:
            alvo["hoje"].update(contagem)
        if dia >= inicio_semana:
            alvo["semana"].update(contagem)
        if dia >= inicio_mes:
            alvo["mes"].update(contagem)
        if dia_texto > alvo["ultimoDia"]:
            alvo["ultimoDia"] = dia_texto

        if dia >= trinta:
            serie[perfil][dia_texto].update(contagem)
            modelos[perfil][modelo] += contagem["saida"] + contagem["entrada"]

    saida = {}
    for perfil, blocos in por_perfil.items():
        saida[perfil] = {
            "hoje": dict(blocos["hoje"]),
            "semana": dict(blocos["semana"]),
            "mes": dict(blocos["mes"]),
            "total": dict(blocos["total"]),
            "ultimoDia": blocos["ultimoDia"],
            "serie": [
                {"dia": d, **dict(c)} for d, c in sorted(serie[perfil].items())
            ],
            "modelos": [
                {"modelo": m, "tokens": v} for m, v in sorted(modelos[perfil].items(), key=lambda x: -x[1])
            ],
        }
    return saida


def pastas_de(perfis: list, incluir_padrao: bool = True) -> dict[str, Path]:
    pastas: dict[str, Path] = {}
    emprestadas = {str(p.config_dir) for p in perfis if getattr(p, "emprestada", False)}
    # se a conta principal ja foi cadastrada como perfil, ela nao pode contar duas vezes
    if incluir_padrao and str(_pasta_padrao()) not in emprestadas:
        pastas["__padrao__"] = _pasta_padrao()
    for p in perfis:
        if p.ferramenta == "claude":
            pastas[p.id] = p.config_dir
    return pastas
