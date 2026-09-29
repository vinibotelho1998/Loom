from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import precos as mod_precos
from .caminhos import RAIZ, gravar_texto

# A versao 2 do cache mora em arquivo proprio. O Loom de antes, instalado e aberto ao mesmo tempo,
# regrava o uso-cache.json no formato dele e jogaria fora o legado; aqui ele so e lido, na migracao.
CACHE = RAIZ / "uso-cache-v2.json"
NOME_DA_VERSAO_1 = "uso-cache.json"

CAMPOS = ("entrada", "saida", "raciocinio", "cacheEscrito", "cacheLido", "mensagens")
# A escrita de cache de 1 hora custa mais que a de 5 minutos, e o cacheEscrito e a soma das duas.
CAMPOS_GUARDADOS = CAMPOS + ("cache5m", "cache1h")
# o que entra na conta do custo, cada um com o seu preco
CAMPOS_DE_CUSTO = ("entrada", "saida", "cache5m", "cache1h", "cacheLido")
VERSAO = 2
# Os baldes por hora servem ao bloco de 5 horas e a janela de 7 dias do painel; mais velho sai.
DIAS_DE_HORAS = 8
# a coleta do painel roda fora da tela: ler e gravar o cache e de uma thread por vez
_TRAVA = threading.Lock()


def _pasta_padrao() -> Path:
    """Onde mora a conta que ja estava logada antes do Loom existir."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path(os.path.expanduser("~")) / ".claude"))


def _cache_vazio() -> dict:
    return {"versao": VERSAO, "arquivos": {}, "ultimos": {}, "dias": {}, "horas": {}, "vistoEm": {}, "legado": {}}


def _ler_cache_bruto(caminho: Path) -> dict | None:
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if (
        not isinstance(bruto, dict)
        or not isinstance(bruto.get("arquivos"), dict)
        or not isinstance(bruto.get("dias"), dict)
    ):
        return None
    return bruto


def _carregar_cache(caminho: Path | None = None) -> dict:
    alvo = CACHE if caminho is None else caminho
    bruto = _ler_cache_bruto(alvo)
    if bruto is None and alvo.name != NOME_DA_VERSAO_1:
        bruto = _ler_cache_bruto(alvo.with_name(NOME_DA_VERSAO_1))
    cache = _cache_vazio()
    if bruto is None:
        return cache
    if bruto.get("versao") != VERSAO:
        # Cache da versao 1: os dias dele nao separam o cache de 1 hora do de 5 minutos. Eles viram
        # legado e os transcript que ainda existem sao lidos de novo do zero. O dia que a releitura
        # cobrir inteiro deixa o legado; o que o Claude ja apagou continua valendo, como aproximado.
        cache["legado"] = bruto["dias"]
        return cache
    for campo in cache:
        if campo != "versao" and isinstance(bruto.get(campo), dict):
            cache[campo] = bruto[campo]
    return cache


def _gravar_cache(cache: dict, caminho: Path | None = None) -> None:
    # o cache guarda o gasto de transcript que o Claude ja apagou: perdido, nao volta
    gravar_texto(CACHE if caminho is None else caminho, json.dumps(cache))


BLOCO = 4 * 1024 * 1024
# quanto cada passada aceita ler de um arquivo so. Indexar do zero um transcript de 100 MB
# segurava a tela por segundos; assim ele entra aos poucos e a janela continua respondendo.
TETO_POR_PASSADA = 32 * 1024 * 1024
# e quanto a passada inteira aceita ler, somando os arquivos. So o teto por arquivo nao bastava:
# na primeira abertura, centenas de transcript menores que ele eram lidos de uma vez na thread da
# tela, e a janela e os terminais paravam ate acabar
TETO_TOTAL_POR_PASSADA = 48 * 1024 * 1024


def _ler_novidades(
    caminho: Path, desde: int, teto: int | None = None, filtro=None
) -> tuple[list[dict], int]:
    """Le so o que cresceu no arquivo desde a ultima passada.

    Em binario e por blocos, porque o deslocamento guardado e em bytes. A versao anterior
    carregava o resto do arquivo como texto e depois recodificava duas vezes so pra descobrir
    onde tinha parado, o que custava varias copias inteiras na memoria. `filtro` diz, pelos bytes,
    que linha vale o json.loads; sem ele, so a que traz consumo.
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
    restante = min(tamanho - desde, TETO_POR_PASSADA if teto is None else teto)
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
                    if (b'"usage"' not in linha) if filtro is None else not filtro(linha):
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


def _consumo(registro: dict) -> dict | None:
    """O que uma resposta gastou, com o dia, o instante e o cache separado em 1 hora e 5 minutos.

    Transcript antigo nao traz a separacao do cache escrito; nessa epoca so existia o de 5 minutos.
    """
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
    escrito = _numero(consumo.get("cache_creation_input_tokens"))
    partes = consumo.get("cache_creation")
    if isinstance(partes, dict) and (
        "ephemeral_1h_input_tokens" in partes or "ephemeral_5m_input_tokens" in partes
    ):
        uma_hora = _numero(partes.get("ephemeral_1h_input_tokens"))
        cinco_minutos = _numero(partes.get("ephemeral_5m_input_tokens"))
    else:
        uma_hora, cinco_minutos = 0, escrito
    entrada = _numero(consumo.get("input_tokens"))
    lido = _numero(consumo.get("cache_read_input_tokens"))
    modelo = mensagem.get("model", "desconhecido")
    return {
        "dia": str(quando.date()),
        "ts": quando.timestamp(),
        "modelo": modelo,
        "entrada": entrada,
        "saida": _numero(consumo.get("output_tokens")),
        "raciocinio": _numero(detalhe.get("thinking_tokens") if isinstance(detalhe, dict) else 0),
        "cacheEscrito": escrito,
        "cacheLido": lido,
        "mensagens": 1,
        "cache5m": cinco_minutos,
        "cache1h": uma_hora,
        # o limiar olha a entrada inteira do pedido, com o cache lido e o escrito
        "longo": entrada + escrito + lido > mod_precos.limiar(modelo),
    }


def _somar(registro: dict, alvo: Counter) -> str | None:
    consumo = _consumo(registro)
    if consumo is None:
        return None
    for campo in CAMPOS:
        alvo[campo] += consumo[campo]
    return f"{consumo['dia']}|{consumo['modelo']}"


def _identificador(registro: dict) -> str | None:
    """O que diz que duas linhas sao a mesma resposta: o id da mensagem mais o do pedido, como no ccusage."""
    mensagem = registro.get("message")
    ident = mensagem.get("id") if isinstance(mensagem, dict) else None
    pedido = registro.get("requestId")
    ident = ident if isinstance(ident, str) and ident else None
    pedido = pedido if isinstance(pedido, str) and pedido else None
    if ident and pedido:
        return f"{ident}|{pedido}"
    return ident or pedido


def _acumular(destino: dict, consumo: dict, com_zeros: bool = True) -> None:
    """Soma uma resposta no balde. A parte de contexto longo vai tambem em destino["longo"]."""
    for campo in CAMPOS_GUARDADOS:
        if consumo[campo] or com_zeros:
            destino[campo] = destino.get(campo, 0) + consumo[campo]
    if consumo["longo"]:
        longo = destino.setdefault("longo", {})
        for campo in CAMPOS_DE_CUSTO:
            if consumo[campo]:
                longo[campo] = longo.get(campo, 0) + consumo[campo]


def _acumular_hora(horas: dict, chave: str, consumo: dict) -> None:
    """Soma a resposta no balde da hora dela, guardando a primeira e a ultima dentro da hora."""
    instante = int(consumo["ts"])
    hora = instante // 3600 * 3600
    balde = horas.setdefault(f"{chave}|{hora}|{consumo['modelo']}", {})
    _acumular(balde, consumo, com_zeros=False)
    balde["ini"] = min(balde.get("ini", instante), instante)
    balde["fim"] = max(balde.get("fim", instante), instante)


def _podar_horas(horas: dict, corte: float) -> dict:
    mantidas = {}
    for chave, balde in horas.items():
        partes = chave.split("|")
        try:
            hora = int(partes[1])
        except (IndexError, ValueError):
            continue
        if hora + 3600 > corte and isinstance(balde, dict):
            mantidas[chave] = balde
    return mantidas


def _normal(pasta) -> str:
    return os.path.normcase(os.path.abspath(str(pasta)))


def _grupos(pastas: dict[str, Path]) -> dict[str, str]:
    """A pasta de cada chave de conta. A mesma pasta com dois nomes e a mesma conta.

    A conta de sempre ja foi __padrao__ e depois virou perfil cadastrado; o legado dela esta num
    nome e a releitura cai no outro.
    """
    grupos = {"__padrao__": _normal(_pasta_padrao())}
    for chave, pasta in pastas.items():
        grupos[chave] = _normal(pasta)
    return grupos


def _dia_do_grupo(chave: str, grupos: dict[str, str]) -> tuple[str, str] | None:
    partes = chave.split("|")
    if len(partes) != 3:
        return None
    return grupos.get(partes[0], partes[0]), partes[1]


def _dias_efetivos(dias: dict, legado: dict, grupos: dict[str, str]) -> tuple[dict, set]:
    """Os dias que valem: o lido agora ou, quando a releitura ainda nao cobre o dia, o legado.

    O dia coberto e o que a releitura ja achou com pelo menos as mensagens que o legado tinha. O
    legado entra marcado como aproximado. Devolve tambem os dias cobertos, que saem do legado.
    """
    if not legado:
        return dias, set()
    novas: Counter = Counter()
    velhas: Counter = Counter()
    for chave, valores in dias.items():
        dia = _dia_do_grupo(chave, grupos)
        if dia is not None and isinstance(valores, dict):
            novas[dia] += _numero(valores.get("mensagens"))
    for chave, valores in legado.items():
        dia = _dia_do_grupo(chave, grupos)
        if dia is not None and isinstance(valores, dict):
            velhas[dia] += _numero(valores.get("mensagens"))
    cobertos = {dia for dia, quantas in velhas.items() if novas.get(dia, 0) and novas[dia] >= quantas}
    efetivos = {}
    for chave, valores in dias.items():
        dia = _dia_do_grupo(chave, grupos)
        if dia in velhas and dia not in cobertos:
            continue
        efetivos[chave] = valores
    for chave, valores in legado.items():
        if isinstance(valores, dict) and _dia_do_grupo(chave, grupos) not in cobertos:
            efetivos[chave] = {**valores, "aproximado": True}
    return efetivos, cobertos


def coletar(pastas: dict[str, Path], estado: dict | None = None) -> dict:
    """Varre os transcript de cada conta e devolve o consumo agregado por conta, dia e modelo.

    A leitura e incremental: guardamos ate onde cada arquivo ja foi lido e,
    na proxima passada, so o que cresceu entra na conta. Com `estado`, diz em
    estado["parcial"] se algum arquivo ficou pela metade por causa do teto da passada.
    """
    return coletar_tudo(pastas, estado)["dias"]


def coletar_tudo(
    pastas: dict[str, Path], estado: dict | None = None, *, cache: Path | None = None, parar=None
) -> dict:
    """A passada do coletar, devolvendo tudo que o cache sabe: {"dias", "horas", "vistoEm"}.

    `horas` sao os baldes por hora dos ultimos dias e `vistoEm`, o instante da ultima resposta de
    cada conta. `cache` e o arquivo do cache, pra thread da coleta nao depender de um global que os
    testes trocam; `parar`, um threading.Event que encerra a passada entre um arquivo e outro.
    """
    with _TRAVA:
        return _coletar(pastas, estado, CACHE if cache is None else cache, parar)


def _coletar(pastas: dict[str, Path], estado: dict | None, alvo: Path, parar) -> dict:
    cache = _carregar_cache(alvo)
    arquivos = cache["arquivos"]
    dias = cache["dias"]
    horas = cache["horas"]
    vistos_em = cache["vistoEm"]
    # o id da ultima resposta lida de cada arquivo
    ultimos = cache["ultimos"]
    mudou = False
    parcial = False
    orcamento = TETO_TOTAL_POR_PASSADA
    corte_das_horas = time.time() - DIAS_DE_HORAS * 24 * 3600

    # O mais novo primeiro, das contas todas juntas: numa passada que nao da conta de tudo, o dia de
    # hoje e o bloco de 5 horas de cada conta saem certos antes do historico velho de qualquer uma.
    achados = []
    for chave_perfil, pasta in pastas.items():
        raiz = Path(pasta) / "projects"
        if not raiz.is_dir():
            continue
        for arquivo in raiz.rglob("*.jsonl"):
            try:
                marca = arquivo.stat()
            except OSError:
                continue
            achados.append((marca.st_mtime, marca.st_size, arquivo, chave_perfil))
    achados.sort(key=lambda achado: achado[0], reverse=True)

    for _, tamanho, arquivo, chave_perfil in achados:
        if parar is not None and parar.is_set():
            parcial = True
            break
        chave = str(arquivo)
        desde = arquivos.get(chave, 0)
        if tamanho == desde:
            continue
        if orcamento <= 0:
            # a passada ja leu o que podia; este fica pra proxima, e a tela diz que esta lendo
            parcial = True
            continue
        teto = min(TETO_POR_PASSADA, orcamento)
        registros, novo_fim = _ler_novidades(arquivo, desde, teto)
        base_lida = desde if novo_fim >= desde else 0
        orcamento -= novo_fim - base_lida
        # andou mas nao chegou no fim: o resto entra na proxima passada, e ate la o numero
        # da tela e menor que o real. A linha que o Claude ainda esta escrevendo nao conta.
        # Com a sobra do orcamento pequena, nem andar ele anda, e mesmo assim falta ler
        if tamanho - (desde if tamanho >= desde else 0) > teto and (
            novo_fim > desde or teto < TETO_POR_PASSADA
        ):
            parcial = True
        if not registros and novo_fim == desde:
            continue
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
            identificador = _identificador(registro)
            if identificador:
                ultimo = identificador
                if identificador in ids:
                    continue
                ids.add(identificador)
            consumo = _consumo(registro)
            if consumo is None:
                continue
            _acumular(dias.setdefault(f"{chave_perfil}|{consumo['dia']}|{consumo['modelo']}", {}), consumo)
            if consumo["ts"] >= corte_das_horas:
                _acumular_hora(horas, chave_perfil, consumo)
            if consumo["ts"] > vistos_em.get(chave_perfil, 0):
                vistos_em[chave_perfil] = int(consumo["ts"])
        arquivos[chave] = novo_fim
        if ultimo:
            ultimos[chave] = ultimo
        mudou = True

    grupos = _grupos(pastas)
    efetivos, cobertos = _dias_efetivos(dias, cache["legado"], grupos)
    # sem novidade nao ha o que gravar: antes o arquivo inteiro era reescrito a cada leitura
    if mudou:
        if cobertos:
            cache["legado"] = {
                chave: valores
                for chave, valores in cache["legado"].items()
                if _dia_do_grupo(chave, grupos) not in cobertos
            }
        cache["horas"] = horas = _podar_horas(horas, corte_das_horas)
        _gravar_cache(cache, alvo)
    if estado is not None:
        estado["parcial"] = parcial
    return {"dias": efetivos, "horas": _podar_horas(horas, corte_das_horas), "vistoEm": dict(vistos_em)}


def relatorio(pastas: dict[str, Path], estado: dict | None = None) -> dict:
    return montar_relatorio(coletar(pastas, estado))


def montar_relatorio(dias: dict) -> dict:
    """O relatorio da aba Uso, por conta.

    A conta que so o painel le, como a do DBX Term, tem dois pontos na chave e fica de fora.
    """
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
        if ":" in perfil or not isinstance(valores, dict):
            continue
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
            # as pilulas sao a legenda do grafico, e o grafico e os numeros de cima contam so
            # o que foi gerado; somando a entrada junto, a legenda nao batia com as barras
            modelos[perfil][modelo] += contagem["saida"]
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


# ---------------------------------------------------------------- custo


def fracao_de_uma_hora(dias: dict, chaves) -> float:
    """Quanto da escrita de cache foi de 1 hora, nos dias medidos com a separacao.

    O dia que veio do cache antigo so sabe o total escrito, e a conta dele usa a proporcao dos dias
    novos da mesma conta. Sem dia novo nenhum, tudo conta como 5 minutos, que e o que o ccusage faz
    com toda escrita de cache.
    """
    chaves = set(chaves)
    uma_hora = soma = 0
    for chave, valores in dias.items():
        if chave.split("|", 1)[0] not in chaves or not isinstance(valores, dict) or valores.get("aproximado"):
            continue
        uma_hora += _numero(valores.get("cache1h"))
        soma += _numero(valores.get("cache1h")) + _numero(valores.get("cache5m"))
    return uma_hora / soma if soma else 0.0


def tokens_do_balde(valores: dict) -> int:
    """Todo token que passou pelo modelo: entrada, saida, cache escrito e cache lido, como no ccusage."""
    return sum(_numero(valores.get(campo)) for campo in ("entrada", "saida", "cacheEscrito", "cacheLido"))


def custo_do_balde(modelo: str, valores: dict, fracao_1h: float = 0.0) -> tuple[float, bool]:
    """O custo de um balde e se o modelo tinha preco. O balde do legado reparte a escrita pela fracao."""
    if valores.get("aproximado"):
        escrito = _numero(valores.get("cacheEscrito"))
        uma_hora = round(escrito * fracao_1h)
        consumo = {
            "entrada": _numero(valores.get("entrada")),
            "saida": _numero(valores.get("saida")),
            "cache5m": escrito - uma_hora,
            "cache1h": uma_hora,
            "cacheLido": _numero(valores.get("cacheLido")),
        }
    else:
        consumo = {campo: _numero(valores.get(campo)) for campo in CAMPOS_DE_CUSTO}
    longo = valores.get("longo")
    return mod_precos.custo(modelo, consumo, longo if isinstance(longo, dict) else None)


def custos_por_dia(dias: dict, chaves, desde: str = "") -> dict[str, dict[str, dict]]:
    """Custo e tokens de cada dia e modelo das contas dadas, do dia `desde` em diante.

    Devolve {dia: {modelo: {"custo", "tokens", "comPreco", "aproximado"}}}, com o nome do modelo ja
    normalizado, pra versao datada e a sem data somarem juntas.
    """
    chaves = set(chaves)
    fracao = fracao_de_uma_hora(dias, chaves)
    saida: dict[str, dict[str, dict]] = {}
    for chave, valores in dias.items():
        partes = chave.split("|")
        if len(partes) != 3 or partes[0] not in chaves or not isinstance(valores, dict):
            continue
        _, dia, modelo = partes
        if dia < desde:
            continue
        valor, com_preco = custo_do_balde(modelo, valores, fracao)
        tokens = tokens_do_balde(valores)
        nome = mod_precos.normalizar(modelo) or modelo
        alvo = saida.setdefault(dia, {}).setdefault(
            nome, {"custo": 0.0, "tokens": 0, "comPreco": True, "aproximado": False}
        )
        alvo["custo"] += valor
        alvo["tokens"] += tokens
        if tokens and not com_preco:
            alvo["comPreco"] = False
        if valores.get("aproximado"):
            alvo["aproximado"] = True
    return saida


def custos_por_hora(horas: dict, chaves) -> list[dict]:
    """Os baldes por hora das contas dadas, com os modelos somados e o custo de cada hora, em ordem."""
    chaves = set(chaves)
    juntas: dict[int, dict] = {}
    for chave, valores in horas.items():
        partes = chave.split("|")
        if len(partes) != 3 or partes[0] not in chaves or not isinstance(valores, dict):
            continue
        try:
            hora = int(partes[1])
        except ValueError:
            continue
        valor, com_preco = custo_do_balde(partes[2], valores)
        tokens = tokens_do_balde(valores)
        inicio, fim = _numero(valores.get("ini")) or hora, _numero(valores.get("fim")) or hora
        alvo = juntas.setdefault(
            hora, {"hora": hora, "ini": inicio, "fim": fim, "tokens": 0, "custo": 0.0, "comPreco": True}
        )
        alvo["ini"] = min(alvo["ini"], inicio)
        alvo["fim"] = max(alvo["fim"], fim)
        alvo["tokens"] += tokens
        alvo["custo"] += valor
        if tokens and not com_preco:
            alvo["comPreco"] = False
    return [juntas[hora] for hora in sorted(juntas)]
