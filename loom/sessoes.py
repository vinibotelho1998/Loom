from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

CAUDA = 96 * 1024
CAUDA_MAXIMA = 32 * 1024 * 1024
JANELA_VIVA = 24 * 3600
SEGUNDOS_ATIVO = 90
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
    """As ultimas linhas inteiras do transcript.

    Uma linha sozinha pode passar da cauda: o Edit num arquivo grande grava o arquivo inteiro
    no resultado. Com a janela fixa, a leitura caia no meio dessa linha, descartava ela e nao
    sobrava nada, e a sessao sumia da lista ate o modelo escrever a linha seguinte.
    """
    try:
        tamanho = caminho.stat().st_size
    except OSError:
        return []
    janela = CAUDA
    while True:
        registros = _ler_cauda(caminho, tamanho, janela)
        if registros or janela >= tamanho or janela >= CAUDA_MAXIMA:
            return registros
        janela = min(janela * 4, CAUDA_MAXIMA)


def _ler_cauda(caminho: Path, tamanho: int, janela: int) -> list[dict]:
    try:
        with open(caminho, "rb") as fh:
            if tamanho > janela:
                fh.seek(tamanho - janela)
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
    if not isinstance(entrada, dict):
        entrada = {}
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
    carimbo = ""

    for registro in reversed(registros):
        tipo = registro.get("type")
        if tipo not in ("assistant", "user"):
            continue
        carimbo = registro.get("timestamp") or carimbo
        mensagem = registro.get("message") or {}
        if not isinstance(mensagem, dict):
            continue
        conteudo = mensagem.get("content")
        # bloco fora do formato derrubava a lista de sessoes inteira, nao so esta
        blocos = [b for b in conteudo if isinstance(b, dict)] if isinstance(conteudo, list) else []

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

    return {"bruto": bruto, "ferramenta": ferramenta, "alvo": alvo, "modelo": modelo, "quando": carimbo}


def _decidir(bruto: str, idade: float) -> str:
    """Cruza o que estava escrito com ha quanto tempo nada e escrito.

    Um transcript parado em tool_use ha meia hora nao esta rodando coisa nenhuma,
    esta abandonado. Terminou de responder e ninguem falou mais, esta esperando voce.
    """
    if idade >= SEGUNDOS_PARADA:
        return "parada"
    if bruto in ("rodando", "pensando"):
        return bruto if idade < SEGUNDOS_ATIVO else "aguardando"
    if bruto in ("respondeu", "aguardando"):
        return "aguardando"
    return "parada"


def _idade(mtime: float) -> float:
    return max(time.time() - mtime, 0)


# ferramentas que poem um subagente pra trabalhar; enquanto ele trabalha, a conversa de cima
# fica calada e so o transcript dele cresce
FERRAMENTAS_DE_SUBAGENTE = ("Agent", "Task")


def _idade_dos_subagentes(arquivo: Path) -> float | None:
    """Ha quanto tempo algum subagente desta conversa escreveu, ou None se nao ha nenhum.

    Com o subagente trabalhando mais de um minuto e meio, a conversa principal nao escreve
    nada, a regra da idade virava ready e saia o aviso de que terminou com ele ainda rodando.
    """
    pasta = arquivo.with_suffix("") / "subagents"
    mais_novo = 0.0
    try:
        for sub in pasta.glob("*.jsonl"):
            try:
                mais_novo = max(mais_novo, sub.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return None
    return _idade(mais_novo) if mais_novo else None


def _idade_da_conversa(leitura: dict, mtime: float) -> float:
    """Ha quanto tempo alguem de fato falou nessa conversa.

    Nao serve olhar a data do arquivo: o Claude escreve nele coisas que nao sao conversa,
    como o titulo e o modo, e isso deixava a sessao parecendo recente pra sempre, presa em
    pensando. O que vale e o carimbo da ultima mensagem.
    """
    carimbo = leitura.get("quando")
    if isinstance(carimbo, str) and carimbo:
        try:
            quando = datetime.fromisoformat(carimbo.replace("Z", "+00:00"))
            return max(datetime.now(quando.tzinfo).timestamp() - quando.timestamp(), 0)
        except ValueError:
            pass
    return _idade(mtime)


# arquivo que nao cresceu nao muda de estado, entao nao precisa ser lido de novo
_LEMBRADO: dict[str, tuple[float, int, dict]] = {}


PASTAS_DE_SERVICO = (".claude", ".loom", ".codex", ".gemini")


def _de_servico(caminho: str) -> bool:
    partes = caminho.replace("\\", "/").lower().split("/")
    return any(p in PASTAS_DE_SERVICO for p in partes)


def _teve_conversa(registros: list[dict]) -> bool:
    """Houve pelo menos uma resposta do modelo aqui dentro?

    A tela de confiar na pasta cria sessao que morre antes de virar conversa, e o Claude
    troca de id ao passar por ela. Sem esse corte, cada abertura vira uma linha na lista.
    """
    return any(r.get("type") == "assistant" for r in registros)


LIMITE_DO_TITULO = 60


def _primeira_fala(registros: list[dict]) -> str:
    """A primeira coisa que a pessoa escreveu, pra dar nome a conversa nova.

    O Claude so batiza a conversa depois que ela cresce. Ate la, o que voce pediu
    identifica melhor do que qualquer rotulo generico.
    """
    for registro in registros:
        if registro.get("type") != "user" or registro.get("isMeta"):
            continue
        mensagem = registro.get("message")
        conteudo = mensagem.get("content") if isinstance(mensagem, dict) else None
        texto = ""
        if isinstance(conteudo, str):
            texto = conteudo
        elif isinstance(conteudo, list):
            if any(b.get("type") == "tool_result" for b in conteudo if isinstance(b, dict)):
                continue
            texto = " ".join(
                b.get("text", "") for b in conteudo if isinstance(b, dict) and b.get("type") == "text"
            )
        texto = " ".join(texto.split())
        # linha de sistema e comando local nao sao pedido de ninguem
        if not texto or texto.startswith("<") or texto.startswith("/"):
            continue
        return texto[:LIMITE_DO_TITULO] + ("…" if len(texto) > LIMITE_DO_TITULO else "")
    return ""


def _titulo(registros: list[dict]) -> str:
    """O nome que o proprio Claude escreveu no transcript."""
    for registro in reversed(registros):
        if registro.get("type") == "ai-title" and registro.get("aiTitle"):
            return str(registro["aiTitle"]).strip()
    return ""


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
        leitura["titulo"] = _titulo(registros)
        leitura["sugestao"] = _primeira_fala(registros)
        leitura["conversou"] = _teve_conversa(registros) or tamanho > CAUDA
    _LEMBRADO[chave] = (mtime, tamanho, leitura)
    if len(_LEMBRADO) > 400:
        _LEMBRADO.clear()
    return leitura


def _colapsar(achadas: list[dict]) -> list[dict]:
    """Junta so o que e mesmo repeticao da mesma conversa.

    Duas sessoes com o mesmo nome, na mesma conta e na mesma pasta, sao a mesma conversa
    retomada. Fica a mais recente. Conversa sem nome nao e agrupada com ninguem: agrupar por
    pasta escondia conversa de verdade, que foi o que aconteceu antes.
    """
    grupos: dict[tuple[str, str, str], list[dict]] = {}
    soltas = []
    for s in achadas:
        if not s.get("titulo"):
            soltas.append(s)
            continue
        grupos.setdefault((s["perfil"], s["pasta"].lower(), s["titulo"].lower()), []).append(s)

    sobreviventes = list(soltas)
    for membros in grupos.values():
        membros.sort(key=lambda s: s["idade"])
        sobreviventes.append(membros[0])
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
        # conversa e so projects/<pasta>/<id>.jsonl. Descendo mais, o rglob pegava o transcript
        # de cada subagente, em <id>/subagents, e cada um virava uma sessao de fora na lista
        for arquivo in raiz.glob("*/*.jsonl"):
            # um stat so por arquivo: eram dois no mesmo laco, e isso roda a cada ciclo
            try:
                marca = arquivo.stat()
            except OSError:
                continue
            mtime, tamanho = marca.st_mtime, marca.st_size
            if agora - mtime > JANELA_VIVA:
                continue

            leitura = _estado_de(arquivo, mtime, tamanho)
            if not leitura:
                continue
            idade = _idade_da_conversa(leitura, mtime)
            if leitura["bruto"] == "rodando" and leitura["ferramenta"] in FERRAMENTAS_DE_SUBAGENTE:
                do_subagente = _idade_dos_subagentes(arquivo)
                if do_subagente is not None:
                    idade = min(idade, do_subagente)
            estado = _decidir(leitura["bruto"], idade)
            marca = (vistas or {}).get(arquivo.stem, {})
            achadas.append(
                {
                    "perfil": chave,
                    "sessao": arquivo.stem,
                    # A status line traz o nome de agora, entao ela vem primeiro: renomear a
                    # conversa aparece na hora, e sessao retomada nasce com transcript novo
                    # e sem ai-title. Depois o transcript, e por ultimo o que voce pediu primeiro.
                    "titulo": marca.get("titulo") or leitura.get("titulo") or leitura.get("sugestao", ""),
                    "conversou": leitura.get("conversou", False),
                    "pasta": leitura.get("pasta", ""),
                    "ramo": leitura.get("ramo", ""),
                    "estado": estado,
                    # o que a ponta do transcript diz, sem a regra da idade. A tela cruza isso com
                    # a saida do terminal da aba, que sabe se o Claude continua desenhando
                    "bruto": leitura["bruto"],
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

    # transcript sem cwd nao diz onde a pessoa estava, e sessao que nunca virou conversa
    # e so a marca de uma abertura que nao foi pra frente
    achadas = [s for s in achadas if s["pasta"] and s.get("conversou")]
    achadas = _colapsar(achadas)

    peso = {"aguardando": 0, "pensando": 1, "rodando": 2, "parada": 3}
    achadas.sort(key=lambda s: (peso.get(s["estado"], 3), s["idade"]))

    vivas = [s for s in achadas if s["estado"] != "parada"]
    paradas = [s for s in achadas if s["estado"] == "parada"][:12]
    return vivas + paradas
