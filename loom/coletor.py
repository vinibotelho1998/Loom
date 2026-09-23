"""Coletor de estado da sessao.

O Claude Code nao grava em disco quanto do plano ja foi gasto, mas entrega esse numero
para a status line junto com o id da sessao. Entao o Loom se registra como status line
daquele perfil, guarda o que chegou e repassa a bola para a status line que ja existia,
sem tirar nada de ninguem.

De quebra isso resolve a identidade da sessao: o processo do Claude herda do shell as marcas
LOOM_ABA e LOOM_PERFIL, entao sabemos qual sessao nasceu aqui dentro e em qual conta.
Sessao sem marca e de outro terminal.

Uso: python -m loom.coletor <perfil> [comando da status line anterior]
Le o JSON da sessao no stdin e escreve a saida da status line original no stdout.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .caminhos import RAIZ

PASTA = RAIZ / "sessoes"
FAIXAS = ("five_hour", "seven_day")
VALIDADE = 12 * 3600
# o id vira nome de arquivo: barra, dois pontos ou ".." levariam a gravacao pra fora da pasta
ID_VALIDO = re.compile(r"^[\w-]{1,128}$")


def _arquivo_da_sessao(sessao) -> Path | None:
    if not isinstance(sessao, str) or not ID_VALIDO.match(sessao):
        return None
    return PASTA / f"{sessao}.json"


def _gravar(alvo: Path, dados: dict) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    provisorio = alvo.with_name(f"{alvo.name}.{os.getpid()}.tmp")
    try:
        provisorio.write_text(json.dumps(dados), encoding="utf-8")
        provisorio.replace(alvo)
    except OSError:
        # o replace falha no Windows quando a janela esta lendo o arquivo naquele instante.
        # Cada chamada e um processo novo, com pid novo, entao o que sobrava nunca era limpo.
        provisorio.unlink(missing_ok=True)
        raise


def guardar(perfil: str, dados: dict) -> None:
    sessao = dados.get("session_id")
    alvo = _arquivo_da_sessao(sessao)
    if alvo is None:
        return

    resumo = {
        "sessao": sessao,
        "perfil": perfil,
        "aba": os.environ.get("LOOM_ABA", ""),
        "terminal": "loom" if os.environ.get("LOOM_ABA") else "",
        "lido": time.time(),
        "faixas": {},
    }

    limites = dados.get("rate_limits") or {}
    for chave in FAIXAS:
        faixa = limites.get(chave)
        if isinstance(faixa, dict):
            resumo["faixas"][chave] = {
                "usado": faixa.get("used_percentage"),
                "zera": faixa.get("resets_at"),
            }

    janela = dados.get("context_window") or {}
    if janela.get("used_percentage") is not None:
        resumo["contexto"] = janela["used_percentage"]
    elif janela.get("context_window_size"):
        # conversa recem comecada nao traz percentual, so os tokens. Sem isto o ctx some da tela.
        usados = janela.get("total_input_tokens") or 0
        resumo["contexto"] = round(usados * 100 / janela["context_window_size"], 1)
    for origem, destino in (
        ("total_input_tokens", "contextoTokens"),
        ("context_window_size", "contextoTeto"),
        ("remaining_percentage", "contextoSobra"),
    ):
        if janela.get(origem) is not None:
            resumo[destino] = janela[origem]

    custo = (dados.get("cost") or {}).get("total_cost_usd")
    if custo is not None:
        resumo["custo"] = round(custo, 2)
    modelo = (dados.get("model") or {}).get("display_name")
    if modelo:
        resumo["modelo"] = modelo
    nome = dados.get("session_name")
    if nome:
        resumo["titulo"] = str(nome).strip()

    _gravar(alvo, resumo)


def esconder_aqui() -> bool:
    """Dentro do Loom a barra de baixo mostra limite, contexto e modelo.

    Fora dele nada muda: a status line de sempre continua sendo chamada. E com a barra de
    baixo desligada ela tambem continua, senao a pessoa ficava sem nenhuma das duas.
    """
    if not os.environ.get("LOOM_ABA"):
        return False
    try:
        from . import config as mod_config
    except ImportError:
        return False
    ajustes = mod_config.carregar()
    return bool(ajustes.get("esconderStatusLine")) and bool(ajustes.get("mostrarUsoNaBarra"))


def encadear(bruto: str, anterior: str) -> str:
    """Roda a status line que ja estava configurada e devolve o que ela imprimiu."""
    if not anterior or esconder_aqui():
        return ""
    try:
        saida = subprocess.run(
            anterior,
            shell=True,
            input=bruto,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return ""
    return saida.stdout or ""


def marcar(perfil: str, dados: dict, aviso: bool = False) -> None:
    """Grava a identidade da sessao assim que ela nasce.

    A status line so e chamada quando o modelo responde, e ate la a sessao apareceria
    como se fosse de outro terminal. O hook de inicio fecha essa janela.

    Com `aviso`, e o gancho de notificacao do Claude: ele parou pra pedir permissao ou
    esta esperando voce. Isso nao vai pro transcript, entao fica anotado aqui com a hora,
    e a lista trata a sessao como esperando enquanto o transcript nao andar depois dele.
    """
    sessao = dados.get("session_id")
    alvo = _arquivo_da_sessao(sessao)
    if alvo is None:
        return
    atual = {}
    if alvo.exists():
        try:
            atual = json.loads(alvo.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            atual = {}
        if not isinstance(atual, dict):
            atual = {}
    atual.update(
        {
            "sessao": sessao,
            "perfil": perfil,
            "aba": os.environ.get("LOOM_ABA", atual.get("aba", "")),
            "terminal": "loom" if os.environ.get("LOOM_ABA") else atual.get("terminal", ""),
            "lido": time.time(),
        }
    )
    atual.setdefault("faixas", {})
    if aviso:
        atual["aviso"] = {"quando": time.time(), "texto": str(dados.get("message") or "")[:160]}
    _gravar(alvo, atual)


def confiar_na_pasta(perfil: str, dados: dict) -> None:
    """Deixa a pasta desta sessao ja confiada pras proximas vezes.

    O dialogo de confiar aparece antes deste gancho rodar, entao aqui nao adianta pra
    sessao de agora. Adianta pra proxima vez que voce abrir o Claude na mesma pasta.
    """
    if perfil == "__padrao__" or not os.environ.get("LOOM_ABA"):
        return
    pasta = (dados.get("workspace") or {}).get("current_dir") or dados.get("cwd") or ""
    if not pasta:
        return
    try:
        from . import config as mod_config
        from . import perfis as mod_perfis
    except ImportError:
        return
    if not mod_config.carregar().get("confiarPastas"):
        return
    alvo = mod_perfis.por_id(perfil)
    if alvo is not None:
        mod_perfis.confiar(alvo, pasta)


def main() -> int:
    # a status line imprime icone e barra, e o console do Windows nasce em cp1252.
    # a entrada tambem: sem isto o nome da sessao com acento chega quebrado.
    for fluxo in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    argumentos = sys.argv[1:]
    aviso = "--aviso" in argumentos
    if aviso:
        argumentos.remove("--aviso")
    so_marcar = aviso or "--hook" in argumentos
    if "--hook" in argumentos:
        argumentos.remove("--hook")
    # o settings da pasta pode chamar o coletor com um perfil fixo enquanto a aba roda
    # em outra conta. Quem sabe a verdade e o ambiente da aba, entao ele decide.
    perfil = os.environ.get("LOOM_PERFIL") or (argumentos[0] if argumentos else "__padrao__")
    anterior = argumentos[1] if len(argumentos) > 1 else ""
    bruto = sys.stdin.read()

    if so_marcar:
        try:
            dados = json.loads(bruto)
            marcar(perfil, dados, aviso)
            if not aviso:
                confiar_na_pasta(perfil, dados)
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        return 0

    try:
        dados = json.loads(bruto)
    except ValueError:
        dados = {}
    if isinstance(dados, dict) and dados:
        try:
            guardar(perfil, dados)
        except (OSError, TypeError, ValueError, AttributeError):
            # campo em formato inesperado derrubava o processo antes do encadear, e a
            # status line de sempre sumia junto. Guardar e extra; a barra dela vem primeiro.
            pass
    sys.stdout.write(encadear(bruto, anterior))
    return 0


# arquivo que nao mudou ja foi lido; reparsear tudo a cada ciclo era o custo de graca
_LIDAS: dict[str, tuple[int, int, dict]] = {}


def ler_todas() -> dict[str, dict]:
    """Tudo que o coletor viu nas ultimas horas, por id de sessao."""
    if not PASTA.is_dir():
        return {}
    agora = time.time()
    achadas = {}
    vivos: set[str] = set()
    try:
        entradas = list(os.scandir(PASTA))
    except OSError:
        return {}

    for entrada in entradas:
        if not entrada.name.endswith(".json"):
            continue
        try:
            marca = entrada.stat()
        except OSError:
            continue
        vivos.add(entrada.path)
        lembrado = _LIDAS.get(entrada.path)
        if lembrado and lembrado[0] == marca.st_mtime_ns and lembrado[1] == marca.st_size:
            dados = lembrado[2]
        else:
            try:
                dados = json.loads(Path(entrada.path).read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            # um arquivo so fora do formato derrubava a leitura inteira, e com ela a barra
            # e a lista de sessoes, ate alguem apagar o arquivo na mao
            if not isinstance(dados, dict) or not isinstance(dados.get("lido", 0), (int, float)):
                continue
            _LIDAS[entrada.path] = (marca.st_mtime_ns, marca.st_size, dados)

        if agora - dados.get("lido", 0) > VALIDADE:
            _LIDAS.pop(entrada.path, None)
            vivos.discard(entrada.path)
            try:
                Path(entrada.path).unlink()
            except OSError:
                pass
            continue
        achadas[dados.get("sessao", Path(entrada.path).stem)] = dados

    for caminho in [c for c in _LIDAS if c not in vivos]:
        _LIDAS.pop(caminho, None)
    return achadas


def por_aba(vistas: dict[str, dict] | None = None) -> dict[str, dict]:
    """A leitura mais recente de cada aba do app, pra tela reagir sem esperar a varredura."""
    melhor: dict[str, dict] = {}
    for dados in (vistas if vistas is not None else ler_todas()).values():
        aba = dados.get("aba")
        if not aba:
            continue
        atual = melhor.get(aba)
        if atual is None or dados["lido"] > atual["lido"]:
            melhor[aba] = dados
    return melhor


def limites_por_perfil(vistas: dict[str, dict] | None = None) -> dict[str, dict]:
    """A leitura mais recente de cada conta, que e o que a barra de cima mostra.

    Sessao marcada so pelo hook ainda nao tem percentual, entao ela nao pode passar
    na frente de uma leitura de verdade e apagar o numero da barra.
    """
    melhor: dict[str, dict] = {}
    for dados in (vistas if vistas is not None else ler_todas()).values():
        chave = dados.get("perfil") or "__padrao__"
        atual = melhor.get(chave)
        tem = bool(dados.get("faixas"))
        if atual is None:
            melhor[chave] = dados
            continue
        tinha = bool(atual.get("faixas"))
        if tem and not tinha:
            melhor[chave] = dados
        elif tem == tinha and dados["lido"] > atual["lido"]:
            melhor[chave] = dados
    return melhor


if __name__ == "__main__":
    raise SystemExit(main())
