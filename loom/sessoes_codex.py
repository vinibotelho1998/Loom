"""Sessoes do Codex na lista lateral, lidas dos rollouts que ele grava em disco.

O Codex grava cada conversa em <CODEX_HOME>/sessions/AAAA/MM/DD/rollout-*.jsonl. A primeira linha e o
session_meta, com o id, a pasta e quem abriu; o resto e a conversa, linha a linha, com o carimbo de
cada registro. Como no Claude, a ponta do arquivo diz em que pe a conversa esta, entao aqui so se le a
cabeca uma vez e a cauda quando o arquivo cresce. Nada aqui fala com a rede nem le o auth.json.

O resultado sai no mesmo formato que sessoes.listar devolve pro Claude, pra tela pintar igual, com
`cli: "codex"` a mais. O campo `ferramenta` continua sendo a ferramenta em uso, como no Claude: a
linha de estado do cartao pinta ele quando a sessao esta rodando.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from . import sessoes as mod_sessoes
from . import uso as mod_uso

CAUDA = 96 * 1024
CAUDA_MAXIMA = 32 * 1024 * 1024
# a cabeca guarda o session_meta, que passa de 20 KB com as instrucoes, e o primeiro pedido
CABECA = 256 * 1024
PRIMEIRA_LINHA_MAXIMA = 4 * 1024 * 1024
JANELA_VIVA = mod_sessoes.JANELA_VIVA
# o Codex sobe depois do shell da aba; o relogio e o mesmo, mas o carimbo do rollout e arredondado
FOLGA_DA_ABERTURA = 2.0
# Pasta padrao do Codex, quando nao ha CODEX_HOME. Os testes trocam por uma temporaria.
CASA_PADRAO: Path | None = None

# Quem abriu a conversa num terminal. O Codex Desktop grava "vscode" e nunca roda numa aba daqui:
# pareado pela pasta, ele colava numa aba aberta no mesmo projeto.
FONTES_DE_TERMINAL = ("cli", "exec")
# ferramentas que param o turno pra perguntar a voce
FERRAMENTAS_DE_PERGUNTA = ("request_user_input", "request_user_input_async", "ask_user", "request_permissions")
# registros que nao sao conversa: o Codex grava eles fora de turno, ao trocar ajuste ou ao subir, e
# contar a idade por eles deixava a sessao parecendo recente
FORA_DA_CONVERSA = (
    "session_meta",
    "turn_context",
    "world_state",
    "token_usage_record",
    "inter_agent_communication_metadata",
)
EVENTOS_FORA_DA_CONVERSA = ("token_count", "thread_settings_applied")
TIPOS_DE_CHAMADA = ("function_call", "custom_tool_call", "local_shell_call")
TIPOS_DE_SAIDA = ("function_call_output", "custom_tool_call_output")

_LEMBRADO: dict[str, tuple[float, int, dict]] = {}
_CABECAS: dict[str, tuple[int, dict]] = {}
_INDICES: dict[str, tuple[float, int, dict]] = {}


def casa_padrao() -> Path:
    if CASA_PADRAO is not None:
        return Path(CASA_PADRAO)
    return Path(os.environ.get("CODEX_HOME") or Path(os.path.expanduser("~")) / ".codex")


def _normal(caminho) -> str:
    texto = str(caminho or "").strip()
    if not texto:
        return ""
    # o Codex no Windows as vezes grava o caminho canonico, com o prefixo de caminho longo
    if texto.startswith("\\\\?\\"):
        texto = texto[4:]
    return os.path.normcase(os.path.abspath(texto)).rstrip("\\/")


def pastas_do_codex(perfis: list, padrao: Path | None = None) -> dict[str, str]:
    """As pastas de Codex a varrer: a de cada perfil codex e a padrao, se existe e ninguem cobre.

    A chave e o id do perfil, ou vazio pra pasta padrao. Mesma regra do painel de uso.
    """
    padrao = Path(padrao) if padrao is not None else casa_padrao()
    pastas: dict[str, str] = {}
    cobertas: set[str] = set()
    for perfil in perfis:
        if getattr(perfil, "ferramenta", "") != "codex":
            continue
        pastas[perfil.id] = str(perfil.config_dir)
        cobertas.add(_normal(perfil.config_dir))
    if _normal(padrao) not in cobertas and padrao.is_dir():
        pastas[""] = str(padrao)
    return pastas


# -- leitura do arquivo -------------------------------------------------


def _linhas(bruto: bytes) -> list[dict]:
    registros = []
    for linha in bruto.decode("utf-8", errors="replace").splitlines():
        linha = linha.strip()
        if not linha.startswith("{"):
            continue
        try:
            registro = json.loads(linha)
        except ValueError:
            continue
        if isinstance(registro, dict):
            registros.append(registro)
    return registros


def _ler_cabeca(caminho: Path) -> list[dict]:
    """As primeiras linhas inteiras. A ultima, cortada pela janela, fica de fora.

    O session_meta vem primeiro e sozinho: o rollout de subagente e de revisor, que sao quase todos,
    para ali, sem ler o resto da janela.
    """
    try:
        with open(caminho, "rb") as fh:
            primeira = fh.readline(PRIMEIRA_LINHA_MAXIMA)
            registros = _linhas(primeira)
            if not registros or _subagente(_carga(registros[0])):
                return registros
            bruto = fh.read(max(CABECA - len(primeira), 0))
    except OSError:
        return []
    corte = bruto.rfind(b"\n")
    return registros + _linhas(bruto[: corte + 1] if corte >= 0 else b"")


def _subagente(meta: dict) -> bool:
    # Subagente e revisor tem rollout proprio, com a fonte num objeto. Eles trabalham pra conversa
    # de cima e nao sao conversa de ninguem.
    return isinstance(meta.get("source"), dict) or meta.get("thread_source") not in (None, "user")


# o Codex grava o JSON sem espaco, mas o espaco depois dos dois pontos tambem e JSON valido
_MARCA_DE_MODELO = re.compile(rb'"type": ?"(?:turn_context|thread_settings_applied)"')


def _ultima_marca(bruto: bytes) -> int:
    posicao = -1
    for achado in _MARCA_DE_MODELO.finditer(bruto):
        posicao = achado.start()
    return posicao


def _ultimo_modelo(caminho: Path, tamanho: int) -> str:
    """O modelo do ultimo turno, procurado de tras pra frente quando a cauda nao tem o turn_context.

    Numa conversa longa o turno passa de megabytes, e o modelo do primeiro turno, da cabeca, pode ser
    outro: troca de modelo no meio da conversa ficava invisivel. A busca e por bytes, e so a linha
    achada vira JSON. Roda uma vez por arquivo; depois as caudas novas trazem o turn_context.
    """
    janela = CAUDA * 4
    while True:
        try:
            with open(caminho, "rb") as fh:
                inicio = max(tamanho - janela, 0)
                fh.seek(inicio)
                bruto = fh.read(tamanho - inicio)
        except OSError:
            return ""
        posicao = _ultima_marca(bruto)
        comeco = bruto.rfind(b"\n", 0, posicao) if posicao >= 0 else -1
        if posicao >= 0 and (comeco >= 0 or inicio == 0):
            fim = bruto.find(b"\n", posicao)
            linha = bruto[comeco + 1 : fim if fim >= 0 else len(bruto)]
            registros = _linhas(linha)
            return _modelo_do_registro(registros[0]) if registros else ""
        if inicio == 0 or janela >= CAUDA_MAXIMA:
            return ""
        janela = min(janela * 4, CAUDA_MAXIMA)


def _ler_cauda(caminho: Path, tamanho: int, janela: int) -> list[dict]:
    try:
        with open(caminho, "rb") as fh:
            if tamanho > janela:
                fh.seek(tamanho - janela)
                fh.readline()
            bruto = fh.read()
    except OSError:
        return []
    return _linhas(bruto)


def _instante(carimbo) -> float | None:
    if not isinstance(carimbo, str) or not carimbo:
        return None
    try:
        return datetime.fromisoformat(carimbo.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


# -- o que cada registro quer dizer ---------------------------------------


def _carga(registro: dict) -> dict:
    carga = registro.get("payload")
    return carga if isinstance(carga, dict) else {}


def _texto(conteudo) -> str:
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        return " ".join(
            str(b.get("text", "")) for b in conteudo if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def _fala_do_usuario(registro: dict) -> str | None:
    """O texto que a pessoa escreveu, ou None se o registro nao e fala dela.

    O Codex manda como fala do usuario o contexto do ambiente e as instrucoes do AGENTS.md; isso nao e
    pedido de ninguem e nao pode virar titulo.
    """
    tipo = registro.get("type")
    carga = _carga(registro)
    if tipo == "event_msg" and carga.get("type") == "user_message":
        texto = carga.get("message")
    elif tipo == "event_msg" and carga.get("type") == "item_completed":
        item = carga.get("item") if isinstance(carga.get("item"), dict) else {}
        if item.get("type") != "UserMessage":
            return None
        texto = _texto(item.get("content"))
    elif tipo == "response_item" and carga.get("type") == "message" and carga.get("role") == "user":
        texto = _texto(carga.get("content"))
    else:
        return None
    texto = " ".join(str(texto or "").split())
    if texto.startswith("<") or texto.startswith("# AGENTS.md"):
        return ""
    return texto


def _encurtar(texto: str, corte: int) -> str:
    texto = texto.strip()
    return texto.splitlines()[0][:corte] if texto else ""


def _base(caminho: str) -> str:
    caminho = caminho.strip().strip('"')
    return os.path.basename(caminho.replace("\\", "/").rstrip("/")) or caminho


_ARQUIVO_DO_PATCH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_CMD_DO_EXEC = re.compile(r"\bcmd\s*:\s*\"((?:[^\"\\]|\\.)*)\"")


def _comando(valor) -> str:
    """A linha de comando, sem o shell na frente: ["pwsh", "-Command", "dir"] vira dir."""
    if isinstance(valor, list):
        partes = [str(p) for p in valor]
        if len(partes) >= 3 and partes[-2].lower() in ("-lc", "-c", "-command", "/c"):
            return partes[-1]
        return " ".join(partes)
    return str(valor or "")


def _resumo_da_chamada(carga: dict) -> tuple[str, str]:
    """O nome da ferramenta e o alvo curto, como o cartao do Claude mostra."""
    tipo = carga.get("type")
    if tipo == "local_shell_call":
        acao = carga.get("action") if isinstance(carga.get("action"), dict) else {}
        return "shell", _encurtar(_comando(acao.get("command")), 70)
    nome = str(carga.get("name") or "")
    bruto = carga.get("input") if tipo == "custom_tool_call" else carga.get("arguments")
    argumentos = {}
    if isinstance(bruto, str) and bruto.lstrip().startswith("{"):
        try:
            argumentos = json.loads(bruto)
        except ValueError:
            argumentos = {}
    elif isinstance(bruto, dict):
        argumentos = bruto
    if not isinstance(argumentos, dict):
        argumentos = {}
    texto = bruto if isinstance(bruto, str) else ""

    if nome == "apply_patch":
        patch = argumentos.get("input") if isinstance(argumentos.get("input"), str) else texto
        achado = _ARQUIVO_DO_PATCH.search(patch or "")
        return nome, _base(achado.group(1)) if achado else ""
    if nome == "exec" and tipo == "custom_tool_call":
        # o Codex Desktop roda um trecho de JavaScript que chama as ferramentas; o comando vem dentro
        achado = _CMD_DO_EXEC.search(texto)
        if achado:
            try:
                return nome, _encurtar(json.loads(f'"{achado.group(1)}"'), 70)
            except ValueError:
                return nome, _encurtar(achado.group(1), 70)
        return nome, ""
    if "command" in argumentos or "cmd" in argumentos:
        return nome, _encurtar(_comando(argumentos.get("command") or argumentos.get("cmd")), 70)
    if nome in FERRAMENTAS_DE_PERGUNTA:
        perguntas = argumentos.get("questions")
        primeira = perguntas[0] if isinstance(perguntas, list) and perguntas and isinstance(perguntas[0], dict) else {}
        return nome, _encurtar(str(primeira.get("title") or primeira.get("question") or ""), 70)
    for campo in ("path", "file_path"):
        if isinstance(argumentos.get(campo), str):
            return nome, _base(argumentos[campo])
    if isinstance(argumentos.get("task_name"), str):
        return nome, _encurtar(argumentos["task_name"], 50)
    return nome, ""


def _fala_do_modelo(registro: dict) -> dict | None:
    """A resposta em texto do modelo, com a fase quando o Codex grava: comentario ou resposta final."""
    tipo = registro.get("type")
    carga = _carga(registro)
    if tipo == "response_item" and carga.get("type") == "message" and carga.get("role") == "assistant":
        return carga
    if tipo == "event_msg" and carga.get("type") == "agent_message":
        return carga
    return None


def _modelo_do_registro(registro: dict) -> str:
    tipo = registro.get("type")
    carga = _carga(registro)
    if tipo == "turn_context" and isinstance(carga.get("model"), str):
        return carga["model"]
    if tipo == "event_msg" and carga.get("type") == "thread_settings_applied":
        ajustes = carga.get("thread_settings") if isinstance(carga.get("thread_settings"), dict) else {}
        if isinstance(ajustes.get("model"), str):
            return ajustes["model"]
    return ""


def _contexto_do_registro(registro: dict) -> float | None:
    """Quanto da janela de contexto a conversa ocupa, em percentual, pelo token_count."""
    carga = _carga(registro)
    if registro.get("type") != "event_msg" or carga.get("type") != "token_count":
        return None
    info = carga.get("info") if isinstance(carga.get("info"), dict) else {}
    ultimo = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
    janela = info.get("model_context_window")
    total = ultimo.get("total_tokens")
    if isinstance(total, bool) or isinstance(janela, bool):
        return None
    if not isinstance(total, (int, float)) or not isinstance(janela, (int, float)) or janela <= 0:
        return None
    return round(min(total, janela) * 100 / janela, 1)


def _conta_pra_idade(registro: dict) -> bool:
    tipo = registro.get("type")
    if tipo in FORA_DA_CONVERSA:
        return False
    if tipo == "event_msg" and _carga(registro).get("type") in EVENTOS_FORA_DA_CONVERSA:
        return False
    return True


def _turno_aberto(registros: list[dict]) -> bool | None:
    """Se o ultimo turno da cauda comecou e nao terminou. None se a cauda nao diz.

    Rollout novo grava task_started e task_complete; o antigo nao grava nenhum dos dois.
    """
    for registro in reversed(registros):
        if registro.get("type") != "event_msg":
            continue
        evento = _carga(registro).get("type")
        if evento in ("task_started", "turn_started"):
            return True
        if evento in ("task_complete", "turn_complete", "turn_aborted"):
            return False
    return None


def _ler_estado(registros: list[dict]) -> dict:
    """Le a cauda de tras pra frente ate achar o que a conversa estava fazendo.

    Chamada de ferramenta sem a saida dela quer dizer ferramenta rodando; com chamadas em paralelo,
    basta uma sem saida. Saida na ponta quer dizer que a vez e do modelo. task_complete e o turno
    interrompido devolvem a bola pra voce. Pedido de aprovacao na ponta e o Codex parado esperando sua
    licenca, e a ferramenta de perguntar e uma pergunta esperando resposta.
    """
    bruto = ""
    ferramenta = alvo = ""
    respondidas: set[str] = set()
    viu_saida = False
    aberto = _turno_aberto(registros)

    for registro in reversed(registros):
        tipo = registro.get("type")
        carga = _carga(registro)
        evento = carga.get("type") if tipo == "event_msg" else None
        item = carga.get("type") if tipo == "response_item" else None

        if item in TIPOS_DE_SAIDA:
            respondidas.add(str(carga.get("call_id") or ""))
            viu_saida = True
            continue
        if item in TIPOS_DE_CHAMADA:
            if str(carga.get("call_id") or "") in respondidas:
                # chamada ja respondida: segue olhando se alguma do mesmo lote ainda roda
                continue
            ferramenta, alvo = _resumo_da_chamada(carga)
            bruto = "pergunta" if ferramenta in FERRAMENTAS_DE_PERGUNTA else "rodando"
            break
        if viu_saida:
            # passou do lote de ferramentas e todas voltaram: a vez e do modelo
            bruto = "pensando"
            break

        if evento and evento.endswith("_approval_request"):
            ferramenta = "exec" if evento.startswith("exec") else ("apply_patch" if "patch" in evento else evento)
            comando = carga.get("command")
            alvo = _encurtar(_comando(comando), 70) if comando else ""
            bruto = "permissao"
            break
        if evento in ("request_user_input", "request_permissions"):
            bruto = "pergunta"
            break
        if evento in ("task_complete", "turn_complete", "turn_aborted"):
            bruto = "respondeu"
            break
        if evento in ("task_started", "turn_started"):
            bruto = "pensando"
            break
        fala = _fala_do_modelo(registro)
        if fala is not None:
            fase = fala.get("phase")
            if fase == "commentary":
                bruto = "pensando"
            elif fase == "final_answer":
                bruto = "respondeu"
            else:
                # sem fase, o rollout antigo: resposta no meio de um turno aberto ainda nao e o fim
                bruto = "pensando" if aberto else "respondeu"
            break
        if item == "reasoning" or evento in ("agent_reasoning", "agent_reasoning_raw_content"):
            bruto = "pensando"
            break
        if tipo != "event_msg" or evento != "item_completed":
            fala_dele = _fala_do_usuario(registro)
            if fala_dele:
                bruto = "pensando"
                break

    if not bruto and viu_saida:
        bruto = "pensando"
    return {"bruto": bruto or "parada", "ferramenta": ferramenta, "alvo": alvo}


def _ler_cabeca_do_arquivo(arquivo: Path, tamanho: int) -> dict:
    """O que nao muda no arquivo: id, pasta, quem abriu, quando, o primeiro pedido e o primeiro modelo."""
    chave = str(arquivo)
    guardada = _CABECAS.get(chave)
    # arquivo que diminuiu foi reescrito, e a cabeca guardada nao vale mais
    if guardada and (guardada[0] == tamanho or (guardada[0] < tamanho and guardada[1].get("completa"))):
        return guardada[1]
    registros = _ler_cabeca(arquivo)
    cabeca: dict = {}
    if registros and registros[0].get("type") == "session_meta":
        meta = _carga(registros[0])
        fonte = meta.get("source")
        git = meta.get("git") if isinstance(meta.get("git"), dict) else {}
        pasta = meta.get("cwd") if isinstance(meta.get("cwd"), str) else ""
        cabeca = {
            "id": str(meta.get("id") or meta.get("session_id") or ""),
            "pasta": pasta.strip().rstrip("\\/") or pasta.strip(),
            "sub": _subagente(meta),
            "fonte": fonte if isinstance(fonte, str) else "",
            "inicio": _instante(meta.get("timestamp")) or _instante(registros[0].get("timestamp")),
            "ramo": str(git.get("branch") or ""),
            "primeira": "",
            "modelo": "",
            "conversou": False,
            # cabeca lida antes de o arquivo ter a primeira fala e lida de novo quando ele crescer
            "completa": False,
        }
        for registro in registros[1:]:
            fala = _fala_do_usuario(registro)
            if fala and not cabeca["primeira"]:
                cabeca["primeira"] = fala
                cabeca["conversou"] = True
            cabeca["modelo"] = cabeca["modelo"] or _modelo_do_registro(registro)
        # do subagente nao se usa mais nada, e ler de novo a cada passada seria custo a toa
        cabeca["completa"] = bool(cabeca["primeira"]) or cabeca["sub"] or tamanho > CABECA
    _CABECAS[chave] = (tamanho, cabeca)
    if len(_CABECAS) > 2000:
        _CABECAS.clear()
    return cabeca


def _ler_cauda_do_arquivo(arquivo: Path, tamanho: int) -> list[dict]:
    """A cauda, crescendo a janela enquanto ela nao tiver nada que diga o estado.

    A ultima linha do Codex pode ser a saida enorme de um comando ou a compactacao da conversa, com
    megabytes; com a janela fixa nao sobrava registro inteiro e a sessao sumia da lista.
    """
    janela = CAUDA
    while True:
        registros = _ler_cauda(arquivo, tamanho, janela)
        if _ler_estado(registros)["bruto"] != "parada" or janela >= tamanho or janela >= CAUDA_MAXIMA:
            return registros
        janela = min(janela * 4, CAUDA_MAXIMA)


def _leitura(arquivo: Path, mtime: float, tamanho: int) -> dict:
    chave = str(arquivo)
    lembrada = _LEMBRADO.get(chave)
    if lembrada and lembrada[0] == mtime and lembrada[1] == tamanho:
        return lembrada[2]
    cabeca = _ler_cabeca_do_arquivo(arquivo, tamanho)
    if not cabeca or cabeca.get("sub") or not cabeca.get("id"):
        leitura: dict = {}
    else:
        registros = _ler_cauda_do_arquivo(arquivo, tamanho)
        leitura = dict(_ler_estado(registros))
        anterior = lembrada[2] if lembrada else {}
        modelo = next((m for m in map(_modelo_do_registro, reversed(registros)) if m), "")
        if not modelo and not anterior.get("modelo") and tamanho > CAUDA:
            modelo = _ultimo_modelo(arquivo, tamanho)
        contexto = next((c for c in map(_contexto_do_registro, reversed(registros)) if c is not None), None)
        quando = next((r.get("timestamp") for r in reversed(registros) if _conta_pra_idade(r) and r.get("timestamp")), "")
        ultima_fala = next((f for f in map(_fala_do_usuario, registros) if f), "")
        leitura.update(
            {
                # a cauda de uma conversa longa nao tem o turn_context: fica o que ja se sabia
                "modelo": modelo or anterior.get("modelo") or cabeca.get("modelo", ""),
                "contexto": contexto if contexto is not None else anterior.get("contexto"),
                "quando": quando or anterior.get("quando", ""),
                "sugestao": cabeca.get("primeira") or ultima_fala or anterior.get("sugestao", ""),
                "conversou": bool(
                    cabeca.get("conversou") or ultima_fala or leitura["bruto"] != "parada" or anterior.get("conversou")
                ),
            }
        )
    _LEMBRADO[chave] = (mtime, tamanho, leitura)
    if len(_LEMBRADO) > 2000:
        _LEMBRADO.clear()
    return leitura


def _nomes(casa: Path) -> dict[str, str]:
    """O nome de cada conversa, do session_index.jsonl que o Codex mantem. O ultimo nome vale."""
    arquivo = casa / "session_index.jsonl"
    chave = str(arquivo)
    try:
        marca = arquivo.stat()
    except OSError:
        return {}
    guardado = _INDICES.get(chave)
    if guardado and guardado[0] == marca.st_mtime and guardado[1] == marca.st_size:
        return guardado[2]
    nomes: dict[str, str] = {}
    try:
        with open(arquivo, "rb") as fh:
            bruto = fh.read()
    except OSError:
        return {}
    for registro in _linhas(bruto):
        ident, nome = registro.get("id"), registro.get("thread_name")
        if isinstance(ident, str) and isinstance(nome, str) and nome.strip():
            nomes[ident] = nome.strip()
    _INDICES[chave] = (marca.st_mtime, marca.st_size, nomes)
    return nomes


def _rollouts_recentes(casa: Path, desde: float):
    """(caminho, mtime, tamanho) dos rollouts mexidos depois de `desde`, em todas as pastas de data.

    Conversa retomada continua no arquivo da data em que nasceu, entao nao da pra olhar so as pastas
    dos ultimos dias. O scandir no Windows ja traz data e tamanho na listagem, sem um stat por arquivo.
    """
    pilha = [casa / "sessions"]
    while pilha:
        pasta = pilha.pop()
        try:
            entradas = list(os.scandir(pasta))
        except OSError:
            continue
        for entrada in entradas:
            try:
                if entrada.is_dir(follow_symlinks=False):
                    pilha.append(Path(entrada.path))
                    continue
                if not entrada.name.endswith(".jsonl"):
                    continue
                marca = entrada.stat()
            except OSError:
                continue
            if marca.st_mtime >= desde:
                yield Path(entrada.path), marca.st_mtime, marca.st_size


def _titulo_curto(texto: str) -> str:
    limite = mod_sessoes.LIMITE_DO_TITULO
    return texto[:limite] + ("…" if len(texto) > limite else "")


def _idade(leitura: dict, mtime: float) -> float:
    quando = _instante(leitura.get("quando"))
    if quando is None:
        return max(time.time() - mtime, 0)
    return max(time.time() - quando, 0)


def listar(pastas: dict[str, str]) -> list[dict]:
    """Uma linha por conversa de Codex vista no disco, no formato de sessoes.listar.

    `pastas` vem de pastas_do_codex: id do perfil, ou vazio pra pasta padrao, e a pasta do Codex. Os
    campos que so a status line do Claude traz, como as faixas de limite, saem vazios. Pra parear com
    a aba, cada linha leva `_casa`, `_terminal` e `_pasta`, que o parear tira antes de ir pra tela.
    """
    achadas = []
    agora = time.time()
    for chave, pasta in pastas.items():
        casa = Path(pasta)
        if not (casa / "sessions").is_dir():
            continue
        nomes = _nomes(casa)
        for arquivo, mtime, tamanho in _rollouts_recentes(casa, agora - JANELA_VIVA):
            cabeca = _ler_cabeca_do_arquivo(arquivo, tamanho)
            leitura = _leitura(arquivo, mtime, tamanho)
            if not leitura or not cabeca.get("pasta") or not leitura.get("conversou"):
                continue
            idade = _idade(leitura, mtime)
            estado = mod_sessoes._decidir(
                "rodando" if leitura["bruto"] == "permissao" else leitura["bruto"], idade
            )
            espera = ""
            bruto = leitura["bruto"]
            if bruto == "pergunta":
                espera = "pergunta"
            elif bruto == "permissao":
                # como no Claude: a ponta e a ferramenta parada esperando licenca
                bruto = "rodando"
                if estado != "parada":
                    estado, espera = "aguardando", "permissao"
            ident = cabeca["id"]
            achadas.append(
                {
                    "perfil": chave,
                    "sessao": ident,
                    # o nome que a conversa ganhou no Codex, senao o que voce pediu primeiro
                    "titulo": nomes.get(ident) or _titulo_curto(leitura.get("sugestao", "")),
                    "conversou": True,
                    "pasta": cabeca["pasta"],
                    # a pasta de rascunho do Codex Desktop vira o nome do projeto, como no painel de uso
                    "projeto": mod_uso.nome_do_projeto(cabeca["pasta"]),
                    "ramo": cabeca.get("ramo", ""),
                    "estado": estado,
                    "espera": espera,
                    "bruto": bruto,
                    "ferramenta": leitura["ferramenta"],
                    "alvo": leitura["alvo"],
                    "modelo": leitura.get("modelo", ""),
                    "idade": int(idade),
                    "quando": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                    "inicio": cabeca.get("inicio") or 0,
                    "terminal": "",
                    "aba": "",
                    "contexto": leitura.get("contexto"),
                    "faixas": {},
                    "conta": chave,
                    "cli": "codex",
                    "_casa": _normal(casa),
                    "_terminal": cabeca.get("fonte") in FONTES_DE_TERMINAL,
                    "_pasta": _normal(cabeca["pasta"]),
                }
            )

    peso = {"aguardando": 0, "pensando": 1, "rodando": 2, "parada": 3}
    achadas.sort(key=lambda s: (peso.get(s["estado"], 3), s["idade"]))
    vivas = [s for s in achadas if s["estado"] != "parada"]
    paradas = [s for s in achadas if s["estado"] == "parada"][:12]
    return vivas + paradas


# -- pareamento com a aba -------------------------------------------------


def aba_pra_parear(session_id: str, origem: dict, nota: dict, perfis_codex: dict[str, str]) -> dict | None:
    """O que o pareamento precisa saber de uma aba viva, ou None se o Codex dela nao escreve aqui.

    A aba de perfil codex grava na pasta do perfil; a sem perfil, na pasta padrao. A de perfil do
    Claude roda o Claude, e casar com ela tirava o cartao da conversa do Claude.
    """
    perfil = str(origem.get("perfil") or "")
    if perfil:
        casa = perfis_codex.get(perfil)
        if not casa:
            return None
    else:
        casa = str(casa_padrao())
    # a pasta onde o shell nasceu e a ultima que a tela soube dele, que vem do prompt depois de um cd
    pastas = {_normal(origem.get("pasta") or os.path.expanduser("~"))}
    if isinstance(nota.get("pasta"), str) and nota["pasta"]:
        pastas.add(_normal(nota["pasta"]))
    conversas = {str(c) for c in (origem.get("conversa"), nota.get("conversa")) if c}
    aberta = origem.get("aberta")
    return {
        "id": session_id,
        "casa": _normal(casa),
        "pastas": pastas,
        "conversas": conversas,
        # shell adotado de uma versao que nao anotava a hora: vale qualquer conversa depois dele
        "aberta": float(aberta) if isinstance(aberta, (int, float)) and not isinstance(aberta, bool) else 0.0,
    }


def parear(sessoes: list[dict], abas: list[dict]) -> list[dict]:
    """Casa cada conversa de Codex com a aba onde ela roda e tira os campos internos.

    O rollout nao guarda variavel de ambiente, entao nao ha a marca da aba como no Claude. Primeiro vale
    a conversa que a aba retomou ou que ja estava nela. Depois, a aba com a mesma pasta de Codex e a
    mesma pasta de trabalho, aberta antes de a conversa nascer: a conversa mais recente fica com a aba
    mais recente, e nenhuma aba recebe duas. Conversa do Codex Desktop nunca casa, ela nao roda em aba.
    """
    usadas: set[str] = set()
    candidatas = [s for s in sessoes if s.get("_terminal")]
    for s in candidatas:
        for aba in abas:
            if aba["id"] not in usadas and aba["casa"] == s["_casa"] and s["sessao"] in aba["conversas"]:
                s["aba"], s["terminal"] = aba["id"], "loom"
                usadas.add(aba["id"])
                break
    soltas = sorted((s for s in candidatas if not s.get("aba")), key=lambda s: s.get("inicio") or 0, reverse=True)
    for s in soltas:
        opcoes = [
            aba
            for aba in abas
            if aba["id"] not in usadas
            and aba["casa"] == s["_casa"]
            and s["_pasta"] in aba["pastas"]
            and (s.get("inicio") or 0) >= aba["aberta"] - FOLGA_DA_ABERTURA
        ]
        if not opcoes:
            continue
        aba = max(opcoes, key=lambda a: a["aberta"])
        s["aba"], s["terminal"] = aba["id"], "loom"
        usadas.add(aba["id"])
    return limpar(sessoes)


def limpar(sessoes: list[dict]) -> list[dict]:
    for s in sessoes:
        for campo in [c for c in s if c.startswith("_")]:
            del s[campo]
    return sessoes


def juntar(do_claude: list[dict], do_codex: list[dict]) -> list[dict]:
    """As duas listas numa so, na ordem de urgencia, com as mesmas 12 paradas no maximo."""
    peso = {"aguardando": 0, "pensando": 1, "rodando": 2, "parada": 3}
    todas = list(do_claude) + list(do_codex)
    if not do_codex:
        return list(do_claude)
    todas.sort(key=lambda s: (peso.get(s.get("estado"), 3), s.get("idade") or 0))
    vivas = [s for s in todas if s.get("estado") != "parada" and s.get("estado") in peso]
    paradas = [s for s in todas if not (s.get("estado") != "parada" and s.get("estado") in peso)][:12]
    return vivas + paradas
