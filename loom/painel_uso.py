"""O relatorio do painel de uso, no formato combinado com a tela.

Uma entrada por conta, do Claude e do Codex, com as janelas de limite, o bloco de 5 horas aberto, o
custo estimado de hoje e dos ultimos 30 dias e a serie por dia. As regras de bloco, custo e
repeticao sao as do ccusage (MIT, Copyright (c) ryoppippi); o desenho que a tela segue e o do
CodexBar. Ritmo, projecao da janela, "zera em" e "atualizado ha" a tela calcula.

O trabalho tem duas metades. O pedido e montado na thread da tela, porque encosta no cadastro de
contas e na leitura da status line, que a tela tambem usa; e rapido. A coleta roda numa thread
propria: e ela que le os transcript do Claude e os rollouts do Codex.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import caminhos as mod_caminhos
from . import coletor as mod_coletor
from . import perfis as mod_perfis
from . import uso as mod_uso
from . import uso_codex as mod_uso_codex
from .caminhos import gravar_texto

log = logging.getLogger(__name__)

VAZIO = {"geradoEm": None, "coletando": True, "contas": []}
BLOCO = 5 * 3600
DIAS_DA_SERIE = 30
# quantos projetos o ranking local do painel mostra
PROJETOS_NO_PAINEL = 8
MODELOS_NO_PAINEL = 8
# do menor esforco pro maior; o que nao esta aqui vai depois, em ordem alfabetica, e o sem registro por ultimo
ORDEM_DO_ESFORCO = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
# Uma coleta segue passada atras de passada ate ler tudo, que fora da tela nao ha pressa de
# devolver a vez; o teto so segura um historico absurdo.
PASSADAS = 64
CACHE_CODEX = "uso-codex.json"
# O ultimo limite lido de cada conta. O coletor apaga a leitura da status line em 12 horas, e a
# janela semanal vale 7 dias.
MEMORIA = "painel-uso.json"
JANELAS_DO_CLAUDE = (("five_hour", "5h", "Sessao", 300), ("seven_day", "7d", "Semana", 10080))
# a chave da conta vai no nome do balde, separado por barra vertical
ID_VALIDO = re.compile(r"^[\w-]{1,64}$")


# ---------------------------------------------------------------- pedido, na thread da tela


def _normal(pasta) -> str:
    return os.path.normcase(os.path.abspath(str(pasta)))


def _ler_json(caminho: Path):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _identidade(perfil) -> dict:
    """O e-mail e o plano ja guardados da conta, sem perguntar nada ao CLI."""
    try:
        guardada, _ = mod_perfis.identidade_guardada(perfil)
    except (OSError, AttributeError, TypeError, ValueError):
        return {}
    return guardada if isinstance(guardada, dict) else {}


def _conta(ferramenta: str, ident: str, nome: str, identidade: dict, chaves: list[str], pasta) -> dict:
    return {
        "id": f"{ferramenta}:{ident}",
        "ferramenta": ferramenta,
        "nome": nome,
        "plano": str(identidade.get("plano") or ""),
        "email": str(identidade.get("email") or ""),
        "chaves": chaves,
        "pasta": Path(pasta),
    }


def _contas_do_dbx_term(raiz: Path, cobertas: set[str]) -> list[dict]:
    """As contas do Claude que o DBX Term cadastrou e o Loom nao mede. Entram so pra leitura.

    O DBX Term e o antecessor do Loom, com o mesmo formato de cadastro. Conta dele que aponta pra
    uma pasta que o Loom ja mede nao entra de novo, senao o gasto contaria duas vezes.
    """
    registro = _ler_json(raiz / "perfis.json")
    if not isinstance(registro, list):
        return []
    identidades = _ler_json(raiz / "identidade.json")
    identidades = identidades if isinstance(identidades, dict) else {}
    contas = []
    for dados in registro:
        if not isinstance(dados, dict) or dados.get("ferramenta", "claude") != "claude":
            continue
        ident = str(dados.get("id") or "")
        if not ID_VALIDO.match(ident):
            continue
        propria = dados.get("pasta")
        pasta = Path(propria) if isinstance(propria, str) and propria else raiz / "perfis" / ident
        if _normal(pasta) in cobertas or not pasta.is_dir():
            continue
        resumo = {}
        for chave, guardada in identidades.items():
            if _normal(chave) != _normal(pasta) or not isinstance(guardada, dict):
                continue
            if isinstance(guardada.get("resumo"), dict):
                resumo = guardada["resumo"]
        nome = f"{dados.get('nome') or ident} · DBX Term"
        contas.append(_conta("claude", f"dbx-term-{ident}", nome, resumo, [f"dbx-term:{ident}"], pasta))
        cobertas.add(_normal(pasta))
    return contas


def contas_do_painel(perfis: list, casa: Path | None = None) -> list[dict]:
    """As contas que o painel mostra, na ordem: Claude do Loom, a de sempre, DBX Term e Codex.

    `casa` e a pasta pessoal; os testes passam uma temporaria.
    """
    if casa is None:
        casa = Path(os.path.expanduser("~"))
        padrao_claude = mod_uso._pasta_padrao()
        padrao_codex = Path(os.environ.get("CODEX_HOME") or casa / ".codex")
    else:
        padrao_claude = casa / ".claude"
        padrao_codex = casa / ".codex"

    contas: list[dict] = []
    cobertas: set[str] = set()
    for perfil in perfis:
        if perfil.ferramenta != "claude" or not ID_VALIDO.match(perfil.id):
            continue
        chaves = [perfil.id]
        # a conta de sempre cadastrada como perfil: o gasto de antes do cadastro esta em __padrao__
        if _normal(perfil.config_dir) == _normal(padrao_claude):
            chaves.append("__padrao__")
        contas.append(_conta("claude", perfil.id, perfil.nome, _identidade(perfil), chaves, perfil.config_dir))
        cobertas.add(_normal(perfil.config_dir))
    if _normal(padrao_claude) not in cobertas and padrao_claude.is_dir():
        principal = mod_perfis.Perfil("__padrao__", "Principal", "", "claude", str(padrao_claude))
        contas.insert(
            0, _conta("claude", "padrao", "Principal", _identidade(principal), ["__padrao__"], padrao_claude)
        )
        cobertas.add(_normal(padrao_claude))
    contas.extend(_contas_do_dbx_term(casa / ".dbx-term", cobertas))

    do_codex: set[str] = set()
    for perfil in perfis:
        if perfil.ferramenta != "codex" or not ID_VALIDO.match(perfil.id):
            continue
        contas.append(_conta("codex", perfil.id, perfil.nome, _identidade(perfil), [perfil.id], perfil.config_dir))
        do_codex.add(_normal(perfil.config_dir))
    if _normal(padrao_codex) not in do_codex and padrao_codex.is_dir():
        contas.append(_conta("codex", "padrao", "Codex", {}, ["padrao"], padrao_codex))
    return contas


def pedido_do_uso(pastas: dict) -> dict:
    """O que a aba Uso de sempre precisa: as pastas do Claude e o arquivo do cache."""
    return {"uso": True, "pastasUso": dict(pastas), "cacheUso": Path(mod_uso.CACHE), "raiz": Path(mod_caminhos.RAIZ)}


def pedido_do_painel(perfis: list, casa: Path | None = None) -> dict:
    """O que a coleta do painel precisa: as contas e a ultima leitura de limite de cada uma."""
    try:
        limites = mod_coletor.limites_por_perfil(mod_coletor.ler_todas())
    except OSError:
        limites = {}
    return {
        "painel": True,
        "contas": contas_do_painel(perfis, casa),
        "limites": limites,
        "cacheUso": Path(mod_uso.CACHE),
        "raiz": Path(mod_caminhos.RAIZ),
    }


def juntar_pedidos(antigo: dict | None, novo: dict) -> dict:
    """O pedido que chega com outro na fila vira um so, com o que os dois pedem."""
    if antigo is None:
        return dict(novo)
    junto = {**antigo, **novo}
    junto["uso"] = bool(antigo.get("uso") or novo.get("uso"))
    junto["painel"] = bool(antigo.get("painel") or novo.get("painel"))
    return junto


# ---------------------------------------------------------------- coleta, na thread propria


def _parou(parar) -> bool:
    return parar is not None and parar.is_set()


def _texto_do_erro(falha: Exception) -> str:
    detalhe = getattr(falha, "strerror", None) or str(falha) or type(falha).__name__
    return f"Não consegui ler o uso desta conta: {detalhe}"


def coletar(pedido: dict, parar=None) -> dict:
    """Roda a coleta pedida e devolve {"uso": relatorio da aba Uso, "painel": relatorio do painel}.

    So vem o que foi pedido. `parar` e um threading.Event que encerra entre um arquivo e outro.
    """
    agora = time.time()
    contas = (pedido.get("contas") or []) if pedido.get("painel") else []
    pastas = dict(pedido.get("pastasUso") or {}) if pedido.get("uso") else {}
    for conta in contas:
        if conta["ferramenta"] == "claude":
            pastas.setdefault(conta["chaves"][0], conta["pasta"])

    lido = {"dias": {}, "horas": {}, "vistoEm": {}, "projetos": {}, "esforcos": {}}
    estado: dict = {}
    erro = None
    if pastas or pedido.get("uso"):
        try:
            for _ in range(PASSADAS):
                lido = mod_uso.coletar_tudo(pastas, estado, cache=Path(pedido["cacheUso"]), parar=parar)
                if not estado.get("parcial") or _parou(parar):
                    break
        except OSError as falha:
            log.warning("nao consegui ler o uso do Claude", exc_info=True)
            erro = _texto_do_erro(falha)
            estado["parcial"] = True

    saida: dict = {}
    if pedido.get("uso"):
        saida["uso"] = {"porConta": mod_uso.montar_relatorio(lido["dias"]), "parcial": bool(estado.get("parcial"))}
    if pedido.get("painel"):
        saida["painel"] = montar(pedido, lido, agora, parar, erro)
    return saida


def montar(pedido: dict, lido: dict, agora: float, parar=None, erro_do_claude: str | None = None) -> dict:
    """O relatorio do painel, com o que o medidor do Claude ja leu e o Codex lido aqui."""
    raiz = Path(pedido["raiz"])
    memoria = _ler_memoria(raiz / MEMORIA)
    antes = json.dumps(memoria, sort_keys=True)
    contas = []
    for conta in pedido.get("contas") or []:
        try:
            if conta["ferramenta"] == "claude":
                item = _conta_do_claude(conta, lido, pedido.get("limites") or {}, memoria, agora)
                item["erro"] = erro_do_claude
            else:
                item = _conta_do_codex(conta, raiz / CACHE_CODEX, agora, parar)
        except Exception as falha:  # noqa: BLE001 - a conta que falha sai com o erro e as outras seguem
            log.warning("nao consegui montar a conta %s do painel", conta.get("id"), exc_info=True)
            item = _conta_vazia(conta, agora, _texto_do_erro(falha))
        contas.append(item)
    if json.dumps(memoria, sort_keys=True) != antes:
        try:
            gravar_texto(raiz / MEMORIA, json.dumps({"versao": 1, "limites": memoria}))
        except OSError:
            log.warning("nao consegui guardar o ultimo limite lido", exc_info=True)
    return {"geradoEm": _iso(agora), "coletando": False, "contas": contas}


# ---------------------------------------------------------------- cada conta


def _iso(instante: float | None) -> str | None:
    if instante is None:
        return None
    try:
        return datetime.fromtimestamp(instante).astimezone().isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


def _real(valor) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return 0.0
    return float(valor)


def _percentual(valor) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return min(100.0, max(0.0, float(valor)))


def _instante(valor) -> float | None:
    """Epoch em segundos, venha em segundos, em milissegundos ou em texto ISO."""
    if isinstance(valor, str) and valor:
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor <= 0:
        return None
    return valor / 1000 if valor > 1e11 else float(valor)


def _ler_memoria(caminho: Path) -> dict:
    bruto = _ler_json(caminho)
    limites = bruto.get("limites") if isinstance(bruto, dict) else None
    return {k: v for k, v in limites.items() if isinstance(v, dict)} if isinstance(limites, dict) else {}


def _leitura_de_limite(conta: dict, limites: dict, memoria: dict) -> dict | None:
    """A leitura de limite mais nova da conta: a da status line ou a guardada da ultima vez."""
    melhor = None
    candidatas = [limites.get(chave) for chave in conta["chaves"]] + [memoria.get(conta["id"])]
    for dados in candidatas:
        if not isinstance(dados, dict) or not isinstance(dados.get("faixas"), dict) or not dados["faixas"]:
            continue
        if melhor is None or _real(dados.get("lido")) > _real(melhor.get("lido")):
            melhor = {"lido": _real(dados.get("lido")), "faixas": dados["faixas"]}
    if melhor is not None:
        memoria[conta["id"]] = melhor
    return melhor


def _janela(tipo: str, rotulo: str, usado, zera, minutos, origem: str, lido) -> dict:
    return {
        "tipo": tipo,
        "rotulo": rotulo,
        "usado": usado,
        "zeraEm": _iso(zera),
        "duracaoMin": minutos,
        "origem": origem,
        "lidoEm": _iso(lido) if lido else None,
    }


def _janelas_do_claude(leitura: dict | None, agora: float) -> list[dict]:
    if not leitura:
        return []
    janelas = []
    for faixa, tipo, rotulo, minutos in JANELAS_DO_CLAUDE:
        dados = leitura["faixas"].get(faixa)
        if not isinstance(dados, dict):
            continue
        usado = _percentual(dados.get("usado"))
        zera = _instante(dados.get("zera"))
        # a janela ja virou: o numero lido era o da anterior e nao diz nada da de agora
        if (zera is not None and zera <= agora) or (usado is None and zera is None):
            continue
        janelas.append(_janela(tipo, rotulo, usado, zera, minutos, "real", leitura["lido"]))
    return janelas


def _tipo_da_janela(minutos: int) -> tuple[str, str]:
    if minutos == 300:
        return "5h", "Sessao"
    if minutos == 10080:
        return "7d", "Semana"
    if minutos % 1440 == 0:
        tipo = f"{minutos // 1440}d"
    elif minutos % 60 == 0:
        tipo = f"{minutos // 60}h"
    else:
        tipo = f"{minutos}m"
    return tipo, f"Janela de {tipo}"


def _janelas_do_codex(limites: dict | None, agora: float) -> list[dict]:
    if not isinstance(limites, dict):
        return []
    lido = _real(limites.get("ts"))
    janelas = []
    for dados in limites.get("janelas") or []:
        if not isinstance(dados, dict) or not isinstance(dados.get("minutos"), int) or dados["minutos"] <= 0:
            continue
        usado = _percentual(dados.get("usado"))
        zera = _instante(dados.get("zera"))
        if (zera is not None and zera <= agora) or (usado is None and zera is None):
            continue
        tipo, rotulo = _tipo_da_janela(dados["minutos"])
        janelas.append(_janela(tipo, rotulo, usado, zera, dados["minutos"], "real", lido))
    return sorted(janelas, key=lambda janela: janela["duracaoMin"])


def _fechar_bloco(inicio, fim, primeiro, ultimo, tokens, custo, agora, estimado) -> dict:
    """O bloco no formato da tela, com o ritmo e a projecao do ccusage.

    Ritmo e tokens por minuto entre a primeira e a ultima resposta do bloco; a projecao soma ao
    custo o mesmo ritmo de gasto ate o fim do bloco. Com uma resposta so, nao ha ritmo.
    """
    minutos = (ultimo - primeiro) / 60
    por_minuto = projecao = None
    if minutos > 0:
        por_minuto = round(tokens / minutos, 1)
        projecao = round(custo + custo / minutos * max(0.0, (fim - agora) / 60), 2)
    return {
        "inicio": _iso(inicio),
        "fim": _iso(fim),
        "tokens": int(round(tokens)),
        "custo": round(custo, 4),
        "porMinuto": por_minuto,
        "projecaoCusto": projecao,
        "estimado": estimado,
    }


def _bloco(horas: list[dict], zera_real: float | None, agora: float) -> tuple[dict | None, float, float]:
    """O bloco de 5 horas aberto agora, com o instante em que ele fecha e o da ultima resposta.

    Com a janela real da ferramenta, o bloco e ela: termina quando ela zera. A hora que comeca antes
    da janela entra pela parte que cai dentro dela. Sem janela real, vale a regra do ccusage: o bloco
    comeca na hora cheia da primeira resposta e dura 5 horas; a resposta que chega depois do fim, ou
    depois de 5 horas sem nada, abre outro. Como o bloco comeca em hora cheia e dura horas inteiras,
    somar balde por hora da o mesmo que somar resposta por resposta.
    """
    if zera_real is not None:
        inicio = zera_real - BLOCO
        tokens = custo = 0.0
        primeiro = ultimo = None
        for balde in horas:
            if balde["ini"] >= inicio:
                fracao, comeco = 1.0, balde["ini"]
            else:
                vao = balde["fim"] - balde["ini"]
                fracao = (balde["fim"] - inicio) / vao if vao > 0 else 0.0
                comeco = inicio
            if fracao <= 0:
                continue
            tokens += balde["tokens"] * fracao
            custo += balde["custo"] * fracao
            primeiro = comeco if primeiro is None else min(primeiro, comeco)
            ultimo = balde["fim"] if ultimo is None else max(ultimo, balde["fim"])
        if not tokens:
            return None, 0.0, 0.0
        return _fechar_bloco(inicio, zera_real, primeiro, ultimo, tokens, custo, agora, False), zera_real, ultimo

    atual = None
    for balde in horas:
        if atual is None or balde["ini"] - atual["inicio"] > BLOCO or balde["ini"] - atual["ultimo"] > BLOCO:
            atual = {"inicio": balde["hora"], "primeiro": balde["ini"], "ultimo": balde["fim"]}
            atual.update(tokens=0, custo=0.0)
        atual["ultimo"] = max(atual["ultimo"], balde["fim"])
        atual["tokens"] += balde["tokens"]
        atual["custo"] += balde["custo"]
    if atual is None:
        return None, 0.0, 0.0
    fim = atual["inicio"] + BLOCO
    if not (agora - atual["ultimo"] < BLOCO and agora < fim):
        return None, 0.0, 0.0
    bloco = _fechar_bloco(
        atual["inicio"], fim, atual["primeiro"], atual["ultimo"], atual["tokens"], atual["custo"], agora, True
    )
    return bloco, fim, atual["ultimo"]


def _resumo(por_dia: dict, hoje) -> tuple[dict, dict, list]:
    """Custo e tokens de hoje e dos 30 dias, e a serie dia a dia, com zero no dia sem uso."""
    dias = [(hoje - timedelta(days=atras)).isoformat() for atras in range(DIAS_DA_SERIE - 1, -1, -1)]
    serie = []
    soma_custo = 0.0
    soma_tokens = 0
    sem_preco = aproximado = False
    for dia in dias:
        modelos = por_dia.get(dia, {})
        custo = sum((m["custo"] for m in modelos.values()), 0.0)
        tokens = sum(m["tokens"] for m in modelos.values())
        sem_preco = sem_preco or any(m["tokens"] and not m["comPreco"] for m in modelos.values())
        aproximado = aproximado or any(m["aproximado"] for m in modelos.values())
        soma_custo += custo
        soma_tokens += tokens
        # o modelo que mais custou primeiro, como o detalhe do dia no CodexBar
        usados = sorted((nome for nome, m in modelos.items() if m["tokens"]), key=lambda n: (-modelos[n]["custo"], n))
        por_modelo = {nome: round(modelos[nome]["custo"], 4) for nome in usados}
        serie.append({"dia": dia, "custo": round(custo, 4), "tokens": tokens, "porModelo": por_modelo})
    custo = {
        "hoje": serie[-1]["custo"],
        "dias30": round(soma_custo, 4),
        "semPreco": sem_preco,
        "aproximado": aproximado,
    }
    tokens = {"hoje": serie[-1]["tokens"], "dias30": soma_tokens}
    return custo, tokens, serie if soma_tokens else []


def _por_nome(lista: dict[str, dict]) -> dict[str, dict]:
    """Junta os caminhos que sao o mesmo projeto: a pasta movida de lugar deixa dois caminhos.

    A chave de saida e o nome em minusculas; o nome mostrado e o do caminho que mais gastou.
    """
    juntos: dict[str, dict] = {}
    for caminho, valores in lista.items():
        nome = mod_uso.nome_do_projeto(caminho)
        alvo = juntos.setdefault(
            nome.lower(),
            {"nome": nome, "maior": -1, "custo": 0.0, "tokens": 0, "mensagens": 0, "comPreco": True,
             "ultimoDia": "", "caminhos": []},
        )
        if valores["tokens"] > alvo["maior"]:
            alvo["nome"], alvo["maior"] = nome, valores["tokens"]
        alvo["custo"] += valores["custo"]
        alvo["tokens"] += valores["tokens"]
        alvo["mensagens"] += valores["mensagens"]
        alvo["comPreco"] = alvo["comPreco"] and valores["comPreco"]
        alvo["ultimoDia"] = max(alvo["ultimoDia"], valores["ultimoDia"])
        alvo["caminhos"].append(caminho)
    return juntos


def _ordem_do_esforco(nivel: str) -> tuple:
    if nivel in ORDEM_DO_ESFORCO:
        return (0, ORDEM_DO_ESFORCO.index(nivel), nivel)
    return (2, 0, nivel) if nivel == mod_uso.SEM_ESFORCO else (1, 0, nivel)


def _modelos_da_conta(esforcos: dict, chaves, hoje) -> dict:
    """O que esta maquina gastou em cada modelo nos 30 dias, com o esforco usado em cada um.

    O esforco e o que o Claude Code ou o Codex gravou na resposta. A fatia de cada nivel e em tokens e
    em respostas. Sem preco na tabela, o custo do modelo e so o piso e o item sai marcado.
    """
    trinta = mod_uso.custos_por_modelo(esforcos, chaves, (hoje - timedelta(days=DIAS_DA_SERIE - 1)).isoformat())
    sete = mod_uso.custos_por_modelo(esforcos, chaves, (hoje - timedelta(days=6)).isoformat())
    total = sum(m["tokens"] for m in trinta.values())
    ranking = []
    for nome, dados in trinta.items():
        # o <synthetic> do Claude e mensagem do proprio programa, sem token e sem modelo por tras
        if not dados["tokens"]:
            continue
        niveis = [
            {
                "nivel": nivel,
                "tokens": parte["tokens"],
                "mensagens": parte["mensagens"],
                "custo": round(parte["custo"], 4),
            }
            for nivel, parte in sorted(dados["esforcos"].items(), key=lambda par: _ordem_do_esforco(par[0]))
            if parte["tokens"] or parte["mensagens"]
        ]
        ranking.append(
            {
                "modelo": nome,
                "tokens": dados["tokens"],
                "mensagens": dados["mensagens"],
                "custo": round(dados["custo"], 4),
                "semPreco": bool(dados["tokens"]) and not dados["comPreco"],
                "tokens7": sete[nome]["tokens"] if nome in sete else 0,
                "parte": round(dados["tokens"] / total, 4) if total else 0.0,
                "esforcos": niveis,
            }
        )
    ranking.sort(key=lambda item: (-item["tokens"], item["modelo"]))
    return {"ranking": ranking[:MODELOS_NO_PAINEL], "total": len(ranking)}


def _projetos_da_conta(projetos: dict, chaves, hoje) -> dict:
    """O que esta maquina gastou, por projeto: os totais de hoje, 7 e 30 dias e o ranking dos 30.

    O limite da conta soma todos os aparelhos; isto sai so dos arquivos daqui. O ranking vai pelo
    custo e, onde nao ha preco, pelos tokens.
    """
    inicio = {
        "hoje": hoje.isoformat(),
        "dias7": (hoje - timedelta(days=6)).isoformat(),
        "dias30": (hoje - timedelta(days=DIAS_DA_SERIE - 1)).isoformat(),
    }
    por_periodo = {
        nome: _por_nome(mod_uso.custos_por_projeto(projetos, chaves, desde)) for nome, desde in inicio.items()
    }
    totais = {
        nome: {
            "custo": round(sum(p["custo"] for p in lista.values()), 4),
            "tokens": sum(p["tokens"] for p in lista.values()),
            "mensagens": sum(p["mensagens"] for p in lista.values()),
            "semPreco": any(p["tokens"] and not p["comPreco"] for p in lista.values()),
        }
        for nome, lista in por_periodo.items()
    }
    ranking = []
    for chave, trinta in por_periodo["dias30"].items():
        sete = por_periodo["dias7"].get(chave)
        dia = por_periodo["hoje"].get(chave)
        ranking.append(
            {
                "projeto": trinta["nome"],
                "caminhos": sorted(trinta["caminhos"]),
                "custo": round(trinta["custo"], 4),
                "tokens": trinta["tokens"],
                "mensagens": trinta["mensagens"],
                "custo7": round(sete["custo"], 4) if sete else 0.0,
                "tokens7": sete["tokens"] if sete else 0,
                "custoHoje": round(dia["custo"], 4) if dia else 0.0,
                "tokensHoje": dia["tokens"] if dia else 0,
                "semPreco": bool(trinta["tokens"]) and not trinta["comPreco"],
                "ultimoDia": trinta["ultimoDia"],
            }
        )
    ranking.sort(key=lambda item: (-item["custo"], -item["tokens"], item["projeto"]))
    return {"totais": totais, "ranking": ranking[:PROJETOS_NO_PAINEL], "total": len(ranking)}


def _conta_pronta(
    conta: dict,
    janelas: list,
    bloco,
    dias: dict,
    chaves,
    agora: float,
    atualizado: float,
    projetos: dict | None = None,
    esforcos: dict | None = None,
) -> dict:
    hoje = datetime.fromtimestamp(agora).date()
    desde = (hoje - timedelta(days=DIAS_DA_SERIE - 1)).isoformat()
    custo, tokens, serie = _resumo(mod_uso.custos_por_dia(dias, chaves, desde), hoje)
    return {
        "id": conta["id"],
        "ferramenta": conta["ferramenta"],
        "nome": conta["nome"],
        "plano": conta["plano"],
        "email": conta["email"],
        "atualizadoEm": _iso(atualizado) if atualizado else None,
        "janelas": janelas,
        "bloco": bloco,
        "custo": custo,
        "tokens": tokens,
        "serie": serie,
        "local": _projetos_da_conta(projetos or {}, chaves, hoje),
        "modelos": _modelos_da_conta(esforcos or {}, chaves, hoje),
        "erro": None,
    }


def _conta_vazia(conta: dict, agora: float, erro: str) -> dict:
    vazia = _conta_pronta(conta, [], None, {}, set(), agora, 0)
    vazia["erro"] = erro
    return vazia


def _zera_da_sessao(janelas: list[dict]) -> float | None:
    """Quando a janela real de 5 horas zera, se ela veio na leitura."""
    for janela in janelas:
        if janela["tipo"] == "5h" and janela["zeraEm"]:
            return datetime.fromisoformat(janela["zeraEm"]).timestamp()
    return None


def _conta_do_claude(conta: dict, lido: dict, limites: dict, memoria: dict, agora: float) -> dict:
    chaves = set(conta["chaves"])
    leitura = _leitura_de_limite(conta, limites, memoria)
    janelas = _janelas_do_claude(leitura, agora)
    zera = _zera_da_sessao(janelas)
    bloco, fim, ultimo = _bloco(mod_uso.custos_por_hora(lido["horas"], chaves), zera, agora)
    if zera is None and bloco is not None and not any(janela["tipo"] == "5h" for janela in janelas):
        # sem a leitura da status line, a sessao de 5 horas sai da regra do bloco do ccusage
        janelas.insert(0, _janela("5h", "Sessao", None, fim, 300, "estimada", ultimo))
    visto = max((_real(lido["vistoEm"].get(chave)) for chave in chaves), default=0.0)
    atualizado = max(visto, leitura["lido"] if leitura else 0.0)
    return _conta_pronta(conta, janelas, bloco, lido["dias"], chaves, agora, atualizado, lido.get("projetos"), lido.get("esforcos"))


def _conta_do_codex(conta: dict, cache: Path, agora: float, parar) -> dict:
    chave = conta["chaves"][0]
    estado: dict = {}
    lido = {"dias": {}, "horas": {}, "limites": None, "vistoEm": 0}
    for _ in range(PASSADAS):
        lido = mod_uso_codex.coletar(conta["pasta"], chave, estado, cache=cache, parar=parar)
        if not estado.get("parcial") or _parou(parar):
            break
    limites = lido.get("limites") if isinstance(lido.get("limites"), dict) else None
    janelas = _janelas_do_codex(limites, agora)
    bloco, _, _ = _bloco(mod_uso.custos_por_hora(lido["horas"], {chave}), _zera_da_sessao(janelas), agora)
    plano = (limites or {}).get("plano") or conta["plano"]
    atualizado = max(_real(lido.get("vistoEm")), _real((limites or {}).get("ts")))
    return _conta_pronta(
        {**conta, "plano": plano},
        janelas,
        bloco,
        lido["dias"],
        {chave},
        agora,
        atualizado,
        lido.get("projetos"),
        lido.get("esforcos"),
    )
