from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .caminhos import RAIZ, gravar_texto

ARQUIVO = RAIZ / "ferramentas.json"


@dataclass
class Ferramenta:
    """Um CLI que guarda login em pasta propria.

    `env` e a variavel de ambiente que aponta a pasta de config daquele CLI.
    `login` e o arquivo que, se existir na pasta, quer dizer que ali tem alguem logado.
    `uso` diz se sabemos ler consumo daquela ferramenta.
    """

    id: str
    nome: str
    env: str
    login: str
    comando: str = ""
    uso: str = ""
    padrao: str = ""

    def como_dict(self) -> dict:
        return asdict(self)


EMBUTIDAS = [
    Ferramenta("claude", "Claude Code", "CLAUDE_CONFIG_DIR", ".credentials.json", "claude", "claude", "~/.claude"),
    Ferramenta("codex", "Codex CLI", "CODEX_HOME", "auth.json", "codex", "", "~/.codex"),
    Ferramenta("gemini", "Gemini CLI", "GEMINI_CONFIG_DIR", "oauth_creds.json", "gemini", "", "~/.gemini"),
]


def listar() -> list[Ferramenta]:
    if not ARQUIVO.exists():
        gravar(EMBUTIDAS)
        return list(EMBUTIDAS)
    try:
        bruto = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return list(EMBUTIDAS)
    if not isinstance(bruto, list):
        return list(EMBUTIDAS)
    conhecidas = {f.id: f for f in EMBUTIDAS}
    achadas = []
    for d in bruto:
        if not isinstance(d, dict) or not {"id", "nome", "env", "login"} <= d.keys():
            continue
        # campo que o arquivo nao tem vem do embutido, senao um cadastro antigo perde
        # informacao nova, tipo a pasta padrao da ferramenta
        base = conhecidas.get(d["id"])
        valores = {}
        for campo in Ferramenta.__annotations__:
            valores[campo] = d.get(campo) or (getattr(base, campo, "") if base else "")
        achadas.append(Ferramenta(**valores))
    return achadas or list(EMBUTIDAS)


def gravar(ferramentas: list[Ferramenta]) -> None:
    gravar_texto(ARQUIVO, json.dumps([f.como_dict() for f in ferramentas], indent=2, ensure_ascii=False))


def por_id(ferramenta_id: str) -> Ferramenta:
    for f in listar():
        if f.id == ferramenta_id:
            return f
    return listar()[0]


def salvar(dados: dict) -> Ferramenta:
    ferramentas = listar()
    alvo = None
    for f in ferramentas:
        if f.id == dados.get("id"):
            f.nome = dados.get("nome", f.nome) or f.nome
            f.env = dados.get("env", f.env)
            f.login = dados.get("login", f.login)
            f.comando = dados.get("comando", f.comando)
            alvo = f
    if alvo is None:
        alvo = Ferramenta(
            id=dados.get("id") or dados.get("nome", "cli").lower().replace(" ", "-"),
            nome=dados.get("nome", "CLI"),
            env=dados.get("env", ""),
            login=dados.get("login", ""),
            comando=dados.get("comando", ""),
        )
        ferramentas.append(alvo)
    gravar(ferramentas)
    return alvo
