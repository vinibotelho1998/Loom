"""As conversas recentes de cada conta, pra paleta de comandos achar e retomar.

A lista da lateral so olha as ultimas 24 horas e le a cauda inteira de cada transcript pra saber o
estado. A paleta quer o contrario: muitas conversas, de ate 30 dias, e so o nome, a pasta e quando.
Entao cada arquivo e lido so no comeco, onde estao a pasta e o primeiro pedido, e num pedaco curto
do fim, onde o Claude grava o titulo. Arquivo que nao mudou nao e lido de novo.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from . import sessoes as mod_sessoes

JANELA_DIAS = 30
# o comeco de um transcript do Claude traz o cwd e o primeiro pedido nas primeiras linhas
CABECA_CLAUDE = 64 * 1024
# o ai-title e regravado perto do fim conforme a conversa cresce
CAUDA_CLAUDE = 32 * 1024
# a primeira linha do rollout do Codex leva as instrucoes de base inteiras, dezenas de kB
CABECA_CODEX = 256 * 1024
# a paleta mostra as mais novas; passando disso, o resto e conversa que ninguem vai procurar
TETO = 300

_LIDOS: dict[str, tuple[float, int, dict]] = {}


def _linhas_json(bruto: str) -> list[dict]:
    registros = []
    for linha in bruto.splitlines():
        linha = linha.strip()
        if not linha.startswith("{"):
            continue
        try:
            valor = json.loads(linha)
        except ValueError:
            continue
        if isinstance(valor, dict):
            registros.append(valor)
    return registros


def _ler_cabeca(caminho: Path, tamanho: int, janela: int) -> list[dict]:
    """As linhas inteiras do comeco do arquivo; a ultima, cortada no meio, fica de fora."""
    try:
        with open(caminho, "rb") as fh:
            bruto = fh.read(janela)
    except OSError:
        return []
    if tamanho > janela:
        corte = bruto.rfind(b"\n")
        bruto = bruto[: corte if corte >= 0 else 0]
    return _linhas_json(bruto.decode("utf-8", errors="replace"))


def _ler_claude(arquivo: Path, tamanho: int) -> dict:
    cabeca = _ler_cabeca(arquivo, tamanho, CABECA_CLAUDE)
    cauda = mod_sessoes._ler_cauda(arquivo, tamanho, CAUDA_CLAUDE) if tamanho > CABECA_CLAUDE else []
    todos = cabeca + cauda
    if not todos:
        return {}
    pasta, _ramo = mod_sessoes._onde(todos)
    # o resumo antigo do Claude mora no comeco; o ai-title novo, no fim
    resumo = next((str(r.get("summary")) for r in cabeca if r.get("type") == "summary" and r.get("summary")), "")
    return {
        "titulo": mod_sessoes._titulo(todos) or resumo or mod_sessoes._primeira_fala(cabeca),
        "pasta": pasta,
        # arquivo maior que a cabeca so cresceu porque alguem respondeu
        "conversou": mod_sessoes._teve_conversa(todos) or tamanho > CABECA_CLAUDE,
        "sessao": arquivo.stem,
    }


def _texto_do_codex(registro: dict) -> str:
    carga = registro.get("payload")
    if not isinstance(carga, dict):
        return ""
    if registro.get("type") == "event_msg" and carga.get("type") == "user_message":
        return str(carga.get("message") or "")
    return ""


def _ler_codex(arquivo: Path, tamanho: int) -> dict:
    registros = _ler_cabeca(arquivo, tamanho, CABECA_CODEX)
    meta = next((r.get("payload") for r in registros if r.get("type") == "session_meta"), None)
    if not isinstance(meta, dict):
        return {}
    titulo = ""
    for registro in registros:
        texto = " ".join(_texto_do_codex(registro).split())
        # o contexto do ambiente e as instrucoes do AGENTS.md entram como fala, entre tags
        if texto and not texto.startswith("<"):
            titulo = texto[: mod_sessoes.LIMITE_DO_TITULO] + ("…" if len(texto) > mod_sessoes.LIMITE_DO_TITULO else "")
            break
    sessao = str(meta.get("id") or meta.get("session_id") or "")
    return {
        "titulo": titulo,
        "pasta": str(meta.get("cwd") or ""),
        # sem um pedido seu o rollout e so uma abertura que nao foi pra frente
        "conversou": bool(titulo),
        "sessao": sessao,
    }


def _lido(arquivo: Path, mtime: float, tamanho: int, leitor) -> dict:
    chave = str(arquivo)
    marca = _LIDOS.get(chave)
    if marca and marca[0] == mtime and marca[1] == tamanho:
        return marca[2]
    leitura = leitor(arquivo, tamanho)
    _LIDOS[chave] = (mtime, tamanho, leitura)
    if len(_LIDOS) > 4 * TETO:
        _LIDOS.clear()
    return leitura


def _candidatos(raiz: Path, padrao: str, corte: float) -> list[tuple[float, int, Path]]:
    achados = []
    if not raiz.is_dir():
        return achados
    for arquivo in raiz.glob(padrao):
        try:
            marca = arquivo.stat()
        except OSError:
            continue
        if marca.st_mtime >= corte:
            achados.append((marca.st_mtime, marca.st_size, arquivo))
    return achados


def listar(
    claude: dict[str, str],
    codex: dict[str, str] | None = None,
    dias: int = JANELA_DIAS,
    agora: float | None = None,
) -> list[dict]:
    """As conversas dos ultimos dias de cada conta, da mais nova pra mais velha.

    claude e codex levam a chave da conta, a mesma da lista de sessoes, ate a pasta de login dela.
    """
    instante = time.time() if agora is None else agora
    corte = instante - dias * 86400
    achadas: list[dict] = []
    for cli, pastas, padrao, sub, leitor in (
        ("claude", claude, "*/*.jsonl", "projects", _ler_claude),
        # o rollout mora em sessions/ano/mes/dia; o arquivado nao volta pela paleta
        ("codex", codex or {}, "*/*/*/rollout-*.jsonl", "sessions", _ler_codex),
    ):
        for chave, pasta in pastas.items():
            for mtime, tamanho, arquivo in _candidatos(Path(pasta) / sub, padrao, corte):
                leitura = _lido(arquivo, mtime, tamanho, leitor)
                if not leitura or not leitura.get("conversou") or not leitura.get("sessao"):
                    continue
                achadas.append(
                    {
                        "perfil": chave,
                        "sessao": leitura["sessao"],
                        "titulo": leitura.get("titulo", ""),
                        "pasta": leitura.get("pasta", ""),
                        "cli": cli,
                        "idade": int(max(instante - mtime, 0)),
                        "quando": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                    }
                )
    achadas.sort(key=lambda c: c["idade"])
    # a mesma conversa retomada na mesma conta fica so com a mais nova
    vistas: set[tuple[str, str]] = set()
    unicas = []
    for conversa in achadas:
        marca = (conversa["perfil"], conversa["sessao"])
        if marca in vistas:
            continue
        vistas.add(marca)
        unicas.append(conversa)
    return unicas[:TETO]
