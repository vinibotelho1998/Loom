from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import ferramentas as mod_ferramentas
from .caminhos import PERFIS, RAIZ

ARQUIVO = RAIZ / "perfis.json"

CORES = ["#4f9cf9", "#63c98a", "#d9b25f", "#b088f0", "#5fc2c9", "#f08189"]


@dataclass
class Perfil:
    id: str
    nome: str
    cor: str
    ferramenta: str = "claude"
    pasta: str = ""

    @property
    def config_dir(self) -> Path:
        """Pasta de login desta conta.

        Normalmente e uma pasta so dela. Quando `pasta` esta preenchida, o perfil aponta
        pra uma pasta que ja existe, tipo a conta que voce ja usava antes deste app.
        Assim ele aproveita o login que ja esta la, sem copiar credencial nem relogar.
        """
        return Path(self.pasta) if self.pasta else PERFIS / self.id

    @property
    def emprestada(self) -> bool:
        return bool(self.pasta)

    @property
    def e_padrao(self) -> bool:
        """Se esta conta e a que a ferramenta usaria sozinha, sem variavel nenhuma."""
        padrao = mod_ferramentas.por_id(self.ferramenta).padrao
        if not padrao:
            return False
        try:
            return self.config_dir.resolve() == Path(os.path.expanduser(padrao)).resolve()
        except OSError:
            return False


def _carregar_bruto() -> list[dict]:
    if not ARQUIVO.exists():
        return []
    try:
        return json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []


def listar() -> list[Perfil]:
    achados = []
    for d in _carregar_bruto():
        if {"id", "nome", "cor"} <= d.keys():
            achados.append(
                Perfil(d["id"], d["nome"], d["cor"], d.get("ferramenta", "claude"), d.get("pasta", ""))
            )
    return achados


def _gravar(perfis: list[Perfil]) -> None:
    RAIZ.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(
        json.dumps([asdict(p) for p in perfis], indent=2, ensure_ascii=False), encoding="utf-8"
    )


def criar(nome: str, ferramenta: str = "claude", cor: str | None = None, pasta: str = "") -> Perfil:
    perfis = listar()
    perfil = Perfil(
        id=uuid.uuid4().hex[:8],
        nome=nome.strip() or "sem nome",
        cor=cor or CORES[len(perfis) % len(CORES)],
        ferramenta=ferramenta or "claude",
        pasta=pasta,
    )
    perfil.config_dir.mkdir(parents=True, exist_ok=True)
    perfis.append(perfil)
    _gravar(perfis)
    return perfil


def atualizar(perfil_id: str, nome: str | None = None, cor: str | None = None) -> Perfil | None:
    perfis = listar()
    alvo = None
    for p in perfis:
        if p.id == perfil_id:
            if nome is not None and nome.strip():
                p.nome = nome.strip()
            if cor:
                p.cor = cor
            alvo = p
    if alvo is not None:
        _gravar(perfis)
    return alvo


def remover(perfil_id: str, apagar_login: bool = False) -> None:
    """Tira o perfil da lista. A pasta de config so vai junto se for pedido,
    porque nela mora o login e apagar isso por engano custa um login novo."""
    alvo = por_id(perfil_id)
    _gravar([p for p in listar() if p.id != perfil_id])
    # pasta emprestada nunca e apagada: ela nao e nossa
    if apagar_login and alvo is not None and not alvo.emprestada:
        pasta = alvo.config_dir
        if pasta.is_dir() and pasta.parent == PERFIS:
            shutil.rmtree(pasta, ignore_errors=True)


def esquecer_login(perfil_id: str) -> bool:
    """Derruba so a credencial, mantendo historico e ajustes daquele perfil."""
    alvo = por_id(perfil_id)
    if alvo is None or alvo.emprestada:
        return False
    arquivo = alvo.config_dir / mod_ferramentas.por_id(alvo.ferramenta).login
    if arquivo.exists():
        arquivo.unlink()
        return True
    return False


def por_id(perfil_id: str) -> Perfil | None:
    for p in listar():
        if p.id == perfil_id:
            return p
    return None


def logado(perfil: Perfil) -> bool:
    return (perfil.config_dir / mod_ferramentas.por_id(perfil.ferramenta).login).exists()


def tamanho(perfil: Perfil) -> int:
    if not perfil.config_dir.is_dir():
        return 0
    return sum(f.stat().st_size for f in perfil.config_dir.rglob("*") if f.is_file())


def ambiente(perfil: Perfil) -> dict[str, str]:
    """O que precisa ir no ambiente do shell pra aquela conta valer.

    Quando o perfil aponta pra pasta padrao da ferramenta, a variavel NAO e definida.
    Parece detalhe, mas nao e: o Claude guarda o cadastro num arquivo ao lado da pasta,
    e nao dentro dela. Definindo a variavel pra propria pasta padrao, ele procura o cadastro
    la dentro, nao acha, e trata como instalacao nova, pedindo login de quem ja estava logado.
    """
    ferramenta = mod_ferramentas.por_id(perfil.ferramenta)
    if not ferramenta.env:
        return {}
    if ferramenta.padrao:
        padrao = Path(os.path.expanduser(ferramenta.padrao)).resolve()
        try:
            if perfil.config_dir.resolve() == padrao:
                return {}
        except OSError:
            pass
    perfil.config_dir.mkdir(parents=True, exist_ok=True)
    return {ferramenta.env: str(perfil.config_dir)}


def _statusline_anterior(pasta: Path) -> str:
    alvo = pasta / "settings.json"
    if not alvo.exists():
        return ""
    try:
        atual = json.loads(alvo.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""
    linha = atual.get("statusLine") or {}
    comando = linha.get("command", "")
    return "" if "coletar.py" in comando or "dbx_term.coletor" in comando else comando


def pasta_principal() -> Path:
    return Path(os.path.expanduser("~")) / ".claude"


def instalar_coletor(perfil: Perfil, python: str, projeto: str) -> bool:
    """Registra o DBX Term como status line daquele perfil.

    E assim que descobrimos quanto do plano ja foi gasto: o numero nao existe em disco,
    o Claude Code so o entrega pra status line. Se o perfil ja tinha uma, ela continua
    rodando em seguida e imprimindo o que sempre imprimiu.
    """
    if perfil.emprestada:
        # a pasta ja e de alguem: mexer no settings dela nao e nosso papel
        return False
    perfil.config_dir.mkdir(parents=True, exist_ok=True)
    alvo = perfil.config_dir / "settings.json"
    try:
        atual = json.loads(alvo.read_text(encoding="utf-8")) if alvo.exists() else {}
    except (ValueError, OSError):
        atual = {}

    anterior = _statusline_anterior(perfil.config_dir)
    entrada = str(Path(projeto) / "coletar.py")
    comando = f'"{python}" "{entrada}" {perfil.id}'
    if anterior:
        comando += f' "{anterior}"'

    antes = json.dumps(atual, sort_keys=True)
    atual["statusLine"] = {"type": "command", "command": comando, "padding": 0}
    atual.setdefault("env", {})["PYTHONPATH"] = projeto

    marcador = f'"{python}" "{entrada}" --hook {perfil.id}'
    ganchos = atual.setdefault("hooks", {})

    def nosso(grupo) -> bool:
        if not isinstance(grupo, dict):
            return False
        return any(
            "coletar.py" in (h or {}).get("command", "") or "dbx_term.coletor" in (h or {}).get("command", "")
            for h in (grupo.get("hooks") or [])
            if isinstance(h, dict)
        )

    inicio = [g for g in ganchos.get("SessionStart", []) if not nosso(g)]
    inicio.append({"hooks": [{"type": "command", "command": marcador}]})
    ganchos["SessionStart"] = inicio

    if json.dumps(atual, sort_keys=True) == antes:
        return False
    try:
        alvo.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    return True


def herdar_confianca(perfil: Perfil) -> int:
    """Traz para o perfil as pastas que a conta principal ja marcou como confiaveis.

    Cada perfil tem o proprio .claude.json, entao sem isto o Claude repete a pergunta de
    confiar na pasta em todo projeto, mesmo nos que voce ja liberou. Nao inventa confianca:
    so copia o que ja estava aceito, e nunca rebaixa o que o perfil ja tem.
    """
    if perfil.emprestada:
        return 0
    origem = Path(os.path.expanduser("~")) / ".claude.json"
    if not origem.exists():
        return 0
    try:
        principal = json.loads(origem.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return 0

    confiadas = {
        nome: dados
        for nome, dados in (principal.get("projects") or {}).items()
        if isinstance(dados, dict) and dados.get("hasTrustDialogAccepted")
    }
    if not confiadas:
        return 0

    perfil.config_dir.mkdir(parents=True, exist_ok=True)
    alvo = perfil.config_dir / ".claude.json"
    try:
        atual = json.loads(alvo.read_text(encoding="utf-8")) if alvo.exists() else {}
    except (ValueError, OSError):
        atual = {}

    projetos = atual.setdefault("projects", {})
    novas = 0
    for nome in confiadas:
        entrada = projetos.setdefault(nome, {})
        if not entrada.get("hasTrustDialogAccepted"):
            entrada["hasTrustDialogAccepted"] = True
            novas += 1
    if not novas:
        return 0

    atual.setdefault("hasCompletedOnboarding", True)
    try:
        alvo.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return 0
    return novas


def confiar(perfil: Perfil, pasta: str) -> bool:
    """Marca uma pasta como confiavel no cadastro daquele perfil.

    E o mesmo efeito de responder "Yes, I trust this folder" uma vez. Existe porque cada
    perfil tem o proprio .claude.json e a pergunta voltaria em toda conta nova.
    So vale pra pasta que voce mesmo abriu numa aba deste app.
    """
    if not pasta or perfil.emprestada:
        return False
    caminho = str(pasta).replace("\\", "/").rstrip("/")
    if not caminho:
        return False

    perfil.config_dir.mkdir(parents=True, exist_ok=True)
    alvo = perfil.config_dir / ".claude.json"
    try:
        atual = json.loads(alvo.read_text(encoding="utf-8")) if alvo.exists() else {}
    except (ValueError, OSError):
        atual = {}

    entrada = atual.setdefault("projects", {}).setdefault(caminho, {})
    if entrada.get("hasTrustDialogAccepted"):
        return False
    entrada["hasTrustDialogAccepted"] = True
    atual.setdefault("hasCompletedOnboarding", True)
    try:
        alvo.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    return True


_IDENTIDADE: dict[str, tuple[float, dict]] = {}


def identidade(perfil: Perfil) -> dict:
    """Descobre de quem e o login daquela pasta, perguntando ao proprio CLI.

    `claude auth status` nao gasta token nenhum, so le o que esta no disco. O resultado
    fica guardado ate a credencial mudar, senao seria um processo novo a cada desenho da tela.
    """
    credencial = perfil.config_dir / mod_ferramentas.por_id(perfil.ferramenta).login
    try:
        carimbo = credencial.stat().st_mtime
    except OSError:
        return {}

    chave = str(perfil.config_dir)
    lembrado = _IDENTIDADE.get(chave)
    if lembrado and lembrado[0] == carimbo:
        return lembrado[1]

    comando = mod_ferramentas.por_id(perfil.ferramenta).comando
    if not comando:
        return {}
    ambiente = {**os.environ}
    variavel = mod_ferramentas.por_id(perfil.ferramenta).env
    if variavel:
        ambiente[variavel] = str(perfil.config_dir)
    try:
        saida = subprocess.run(
            [comando, "auth", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=ambiente,
            shell=True,
        )
        dados = json.loads(saida.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        dados = {}

    resumo = {
        "email": dados.get("email", ""),
        "plano": dados.get("subscriptionType") or dados.get("authMethod", ""),
        "logado": bool(dados.get("loggedIn")),
    }
    _IDENTIDADE[chave] = (carimbo, resumo)
    return resumo


def blindar_status_line(python: str, projeto: str, anterior: str) -> Path:
    """Cria um atalho que sobrevive ao projeto sumir.

    O settings do Claude guarda caminho absoluto. Se a pasta do DBX Term for movida ou
    apagada, a status line quebraria em TODAS as sessoes, inclusive fora deste app.
    Este arquivo mora na pasta de dados, confere se o coletor ainda existe e, se nao existe,
    chama direto a status line de sempre. O pior caso deixa de ser perder a barra.
    """
    entrada = Path(projeto) / "coletar.py"
    destino = RAIZ / "status-line.cmd"
    RAIZ.mkdir(parents=True, exist_ok=True)

    linhas = [
        "@echo off",
        "rem Gerado pelo DBX Term. Se o projeto sumir, a status line de sempre continua.",
        f'if exist "{entrada}" (',
        f'  "{python}" "{entrada}" %*',
        ") else (",
        f"  {anterior}",
        ")",
    ]
    # newline vazio: sem isso o Windows converte de novo e o arquivo sai com quebra dupla
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        quebra = chr(13) + chr(10)
        fh.write(quebra.join(linhas) + quebra)
    return destino
