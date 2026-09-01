from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

CAUDA = 96 * 1024
JANELA_VIVA = 24 * 3600
SEGUNDOS_ATIVO = 90
SEGUNDOS_FRESCO = 120
SEGUNDOS_PARADA = 20 * 60

ALVO_DA_FERRAMENTA = {
    "Bash": ("command", 70),
    "PowerShell": ("command", 70),
    "Read": ("file_path", 0),
    "Edit": ("file_path", 0),
    "Write": ("file_path", 0),
    "NotebookEdit": ("file_path", 0),
    "Glob": ("pattern", 40),
    "Grep": ("pattern", 40),
    "Task": ("description", 50),
    "Agent": ("description", 50),
    "WebFetch": ("url", 50),
    "WebSearch": ("query", 50),
    "Skill": ("skill", 0),
}


def _tail(caminho: Path) -> list[dict]:
    try:
        tamanho = caminho.stat().st_size
        with open(caminho, "rb") as fh:
            if tamanho > CAUDA:
                fh.seek(tamanho - CAUDA)
                fh.readline()
            bruto = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    registros = []
    for linha in bruto.splitlines():
        linha = linha.strip()
        if not linha.startswith("{"):
            continue
        try:
            registros.append(json.loads(linha))
        except ValueError:
            continue
    return registros


def _resumo(bloco: dict) -> tuple[str, str]:
    nome = bloco.get("name", "")
    entrada = bloco.get("input") or {}
    campo, corte = ALVO_DA_FERRAMENTA.get(nome, ("", 0))
    alvo = str(entrada.get(campo, "")) if campo else ""
    if not campo:
        alvo = ""
    elif corte == 0:
        alvo = os.path.basename(alvo.replace("\\", "/").rstrip("/")) or alvo
    else:
        alvo = alvo.strip().splitlines()[0][:corte] if alvo.strip() else ""
    return nome, alvo


def _ler_estado(registros: list[dict]) -> dict:
    """Le a cauda de tras pra frente ate achar o que a sessao estava fazendo.

    tool_use na ponta quer dizer ferramenta rodando. tool_result na ponta quer dizer
    que a ferramenta voltou e agora e a vez do modelo. Texto na ponta quer dizer
    que ele terminou e a bola esta com voce.
    """
    bruto = "parada"
    ferramenta = ""
    alvo = ""
    modelo = ""

    for registro in reversed(registros):
        tipo = registro.get("type")
        if tipo not in ("assistant", "user"):
            continue
        mensagem = registro.get("message") or {}
        conteudo = mensagem.get("content")
        blocos = conteudo if isinstance(conteudo, list) else []

        if tipo == "assistant":
            modelo = modelo or mensagem.get("model", "")
            usos = [b for b in blocos if b.get("type") == "tool_use"]
            if usos:
                bruto = "rodando"
                ferramenta, alvo = _resumo(usos[-1])
            else:
                bruto = "respondeu"
            break

        if any(b.get("type") == "tool_result" for b in blocos):
            bruto = "pensando"
            break
        bruto = "pensando"
        break

    return {"bruto": bruto, "ferramenta": ferramenta, "alvo": alvo, "modelo": modelo}


def _decidir(bruto: str, idade: float) -> str:
    """Cruza o que estava escrito com ha quanto tempo nada e escrito.

    Um transcript parado em tool_use ha meia hora nao esta rodando coisa nenhuma,
    esta abandonado. E um texto recem escrito ainda e uma resposta fresca, nao uma espera.
    """
    if idade >= SEGUNDOS_PARADA:
        return "parada"
    if bruto in ("rodando", "pensando"):
        return bruto if idade < SEGUNDOS_ATIVO else "aguardando"
    if bruto == "respondeu":
        return "respondeu" if idade < SEGUNDOS_FRESCO else "aguardando"
    return "parada"


def _idade(mtime: float) -> float:
    return max(time.time() - mtime, 0)


# arquivo que nao cresceu nao muda de estado, entao nao precisa ser lido de novo
_LEMBRADO: dict[str, tuple[float, int, dict]] = {}


PASTAS_DE_SERVICO = (".claude", ".dbx-term", ".codex", ".gemini")


def _de_servico(caminho: str) -> bool:
    partes = caminho.replace("\\", "/").lower().split("/")
    return any(p in PASTAS_DE_SERVICO for p in partes)


def _onde(registros: list[dict]) -> tuple[str, str]:
    """Descobre em que projeto a sessao esta.

    Nao serve pegar o ultimo cwd: uma sessao que passou pela pasta de memoria do proprio
    Claude no fim ficaria rotulada como memory. Vale o lugar onde ela mais trabalhou,
    ignorando pasta de servico de ferramenta.
    """
    contagem: dict[str, int] = {}
    ramos: dict[str, str] = {}
    for registro in registros:
        caminho = registro.get("cwd")
        if not caminho or _de_servico(caminho):
            continue
        contagem[caminho] = contagem.get(caminho, 0) + 1
        if registro.get("gitBranch"):
            ramos[caminho] = registro["gitBranch"]
    if not contagem:
        ultimo = next((r for r in reversed(registros) if r.get("cwd")), {})
        return ultimo.get("cwd", ""), ultimo.get("gitBranch", "")
    escolhido = max(contagem, key=lambda c: contagem[c])
    return escolhido, ramos.get(escolhido, "")


def _estado_de(arquivo: Path, mtime: float, tamanho: int) -> dict:
    chave = str(arquivo)
    marca = _LEMBRADO.get(chave)
    if marca and marca[0] == mtime and marca[1] == tamanho:
        return marca[2]
    registros = _tail(arquivo)
    if not registros:
        leitura = {}
    else:
        leitura = _ler_estado(registros)
        leitura["pasta"], leitura["ramo"] = _onde(registros)
    _LEMBRADO[chave] = (mtime, tamanho, leitura)
    if len(_LEMBRADO) > 400:
        _LEMBRADO.clear()
    return leitura


def _colapsar(achadas: list[dict]) -> list[dict]:
    """Junta as sobras de uma mesma conversa.

    O Claude troca o id da sessao quando passa pela tela de confiar na pasta, e a sessao
    velha fica no disco pra sempre, parada, com a mesma conta e a mesma pasta. Mostrar as duas
    faz parecer que ha duas conversas onde ha uma. Dentro de cada conta e pasta ficam as que
    ainda tem sinal de vida, e das inertes so a mais recente.
    """
    grupos: dict[tuple[str, str], list[dict]] = {}
    for s in achadas:
        grupos.setdefault((s["perfil"], s["pasta"].lower()), []).append(s)

    sobreviventes = []
    for membros in grupos.values():
        membros.sort(key=lambda s: s["idade"])
        vivos = [s for s in membros if s["idade"] < SEGUNDOS_ATIVO]
        sobreviventes.extend(vivos or membros[:1])
    return sobreviventes


def listar(pastas: dict[str, str], vistas: dict[str, dict] | None = None) -> list[dict]:
    """Uma linha por sessao de Claude vista no disco, da mais urgente pra menos.

    Nao ha chamada de rede nenhuma: o proprio Claude Code escreve o transcript
    durante a conversa, entao acompanhar o fim do arquivo ja diz em que pe ela esta.
    """
    achadas = []
    agora = time.time()

    for chave, pasta in pastas.items():
        raiz = Path(pasta) / "projects"
        if not raiz.is_dir():
            continue
        for arquivo in raiz.rglob("*.jsonl"):
            try:
                mtime = arquivo.stat().st_mtime
            except OSError:
                continue
            if agora - mtime > JANELA_VIVA:
                continue

            try:
                tamanho = arquivo.stat().st_size
            except OSError:
                continue
            leitura = _estado_de(arquivo, mtime, tamanho)
            if not leitura:
                continue
            idade = _idade(mtime)
            estado = _decidir(leitura["bruto"], idade)
            marca = (vistas or {}).get(arquivo.stem, {})
            achadas.append(
                {
                    "perfil": chave,
                    "sessao": arquivo.stem,
                    "pasta": leitura.get("pasta", ""),
                    "ramo": leitura.get("ramo", ""),
                    "estado": estado,
                    "ferramenta": leitura["ferramenta"],
                    "alvo": leitura["alvo"],
                    # o coletor traz o nome bonito, tipo Opus 5; o transcript so tem o id
                    "modelo": marca.get("modelo") or leitura["modelo"],
                    "idade": int(idade),
                    "quando": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                    "terminal": marca.get("terminal", ""),
                    "aba": marca.get("aba", ""),
                    "contexto": marca.get("contexto"),
                    "faixas": marca.get("faixas", {}),
                    "conta": marca.get("perfil", ""),
                }
            )

    # transcript sem cwd nao diz onde a pessoa estava, entao nao ajuda ninguem na lista
    achadas = [s for s in achadas if s["pasta"]]
    achadas = _colapsar(achadas)

    peso = {"aguardando": 0, "respondeu": 1, "pensando": 2, "rodando": 3, "parada": 4}
    achadas.sort(key=lambda s: (peso.get(s["estado"], 3), s["idade"]))

    vivas = [s for s in achadas if s["estado"] != "parada"]
    paradas = [s for s in achadas if s["estado"] == "parada"][:6]
    return vivas + paradas
