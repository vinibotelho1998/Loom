from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .caminhos import RAIZ, gravar_texto

CACHE = RAIZ / "uso-cache.json"

CAMPOS = ("entrada", "saida", "raciocinio", "cacheEscrito", "cacheLido", "mensagens")


def _pasta_padrao() -> Path:
    """Onde mora a conta que ja estava logada antes do Loom existir."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path(os.path.expanduser("~")) / ".claude"))


def _carregar_cache() -> dict:
    if not CACHE.exists():
        return {"arquivos": {}, "dias": {}}
    try:
        bruto = json.loads(CACHE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"arquivos": {}, "dias": {}}
    if (
        not isinstance(bruto, dict)
        or not isinstance(bruto.get("arquivos"), dict)
        or not isinstance(bruto.get("dias"), dict)
    ):
        return {"arquivos": {}, "dias": {}}
    return bruto


def _gravar_cache(cache: dict) -> None:
    # o cache guarda o gasto de transcript que o Claude ja apagou: perdido, nao volta
    gravar_texto(CACHE, json.dumps(cache))


BLOCO = 4 * 1024 * 1024
# quanto cada passada aceita ler de um arquivo so. Indexar do zero um transcript de 100 MB
# segurava a tela por segundos; assim ele entra aos poucos e a janela continua respondendo.
TETO_POR_PASSADA = 32 * 1024 * 1024


def _ler_novidades(caminho: Path, desde: int) -> tuple[list[dict], int]:
    """Le so o que cresceu no arquivo desde a ultima passada.

    Em binario e por blocos, porque o deslocamento guardado e em bytes. A versao anterior
    carregava o resto do arquivo como texto e depois recodificava duas vezes so pra descobrir
    onde tinha parado, o que custava varias copias inteiras na memoria.
    """
    try:
        tamanho = caminho.stat().st_size
    except OSError:
        return [], desde
    if tamanho < desde:
        desde = 0
    if tamanho == desde:
        return [], desde

    linhas: list[dict] = []
    fim = desde
    restante = min(tamanho - desde, TETO_POR_PASSADA)
    # sobra sempre comeca no byte `fim`: e o pedaco lido que ainda nao terminou em quebra
    sobra = b""
    try:
        with open(caminho, "rb") as fh:
            fh.seek(desde)
            while restante > 0:
                pedaco = fh.read(min(restante, BLOCO))
                if not pedaco:
                    break
                restante -= len(pedaco)
                sobra += pedaco
                corte = sobra.rfind(b"\n")
                if corte == -1:
                    continue
                for linha in sobra[:corte].split(b"\n"):
                    if b'"usage"' not in linha:
                        continue
                    try:
                        linhas.append(json.loads(linha.decode("utf-8", errors="replace")))
                    except ValueError:
                        continue
                fim += corte + 1
                sobra = sobra[corte + 1 :]
    except OSError:
        return linhas, fim
    return linhas, fim


def _numero(valor) -> int:
    """Token que vem nulo ou em outro formato conta zero.

    Somar None dava TypeError, e como o deslocamento nao andava, aquela mesma linha
    derrubava a tela de Uso em toda leitura seguinte.
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return 0
    return int(valor)


def _somar(registro: dict, alvo: Counter) -> str | None:
    mensagem = registro.get("message") or {}
    if not isinstance(mensagem, dict):
        return None
    consumo = mensagem.get("usage")
    if not isinstance(consumo, dict) or not consumo or mensagem.get("role") != "assistant":
        return None
    carimbo = registro.get("timestamp")
    if not isinstance(carimbo, str) or not carimbo:
        return None
    try:
        quando = datetime.fromisoformat(carimbo.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
    detalhe = consumo.get("output_tokens_details")
    alvo["entrada"] += _numero(consumo.get("input_tokens"))
    alvo["saida"] += _numero(consumo.get("output_tokens"))
    alvo["cacheEscrito"] += _numero(consumo.get("cache_creation_input_tokens"))
    alvo["cacheLido"] += _numero(consumo.get("cache_read_input_tokens"))
    alvo["raciocinio"] += _numero(detalhe.get("thinking_tokens") if isinstance(detalhe, dict) else 0)
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
    # o id da ultima resposta lida de cada arquivo. Cache antigo nao tem e comeca vazio.
    ultimos = cache.get("ultimos")
    if not isinstance(ultimos, dict):
        ultimos = {}
    mudou = False

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
            ids = set()
            # Cada bloco da resposta vira uma linha com o mesmo id e o mesmo consumo, e elas
            # chegam ao disco com segundos de distancia. Lendo com a conversa em andamento, o
            # primeiro bloco caia numa passada e o resto na seguinte, e a resposta contava duas vezes.
            if desde and ultimos.get(chave):
                ids.add(ultimos[chave])
            ultimo = None
            for registro in registros:
                if not isinstance(registro, dict):
                    continue
                # a mesma resposta pode aparecer duas vezes quando a sessao e retomada
                # ou quando o Claude reescreve o bloco. Contar duas vezes inflaria o gasto.
                mensagem = registro.get("message")
                identificador = (mensagem.get("id") if isinstance(mensagem, dict) else None) or registro.get(
                    "requestId"
                )
                if identificador:
                    ultimo = identificador
                    if identificador in ids:
                        continue
                    ids.add(identificador)
                parcial = Counter()
                chave_dia = _somar(registro, parcial)
                if chave_dia:
                    vistos[chave_dia].update(parcial)
            for chave_dia, contagem in vistos.items():
                destino = dias.setdefault(f"{chave_perfil}|{chave_dia}", {})
                for campo in CAMPOS:
                    destino[campo] = destino.get(campo, 0) + contagem[campo]
            arquivos[chave] = novo_fim
            if ultimo:
                ultimos[chave] = ultimo
            mudou = True

    # sem novidade nao ha o que gravar: antes o arquivo inteiro era reescrito a cada leitura
    if mudou:
        _gravar_cache({"arquivos": arquivos, "dias": dias, "ultimos": ultimos})
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
    # o gasto do dia quebrado por modelo, que e o que a barra empilhada do grafico precisa
    dia_por_modelo: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))

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
            dia_por_modelo[perfil][dia_texto][modelo] += contagem["saida"]

    saida = {}
    for perfil, blocos in por_perfil.items():
        saida[perfil] = {
            "hoje": dict(blocos["hoje"]),
            "semana": dict(blocos["semana"]),
            "mes": dict(blocos["mes"]),
            "total": dict(blocos["total"]),
            "ultimoDia": blocos["ultimoDia"],
            "serie": [
                {"dia": d, **dict(c), "porModelo": dict(dia_por_modelo[perfil].get(d, {}))}
                for d, c in sorted(serie[perfil].items())
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
