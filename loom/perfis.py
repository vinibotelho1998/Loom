from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import ferramentas as mod_ferramentas
from .caminhos import PERFIS, RAIZ, gravar_texto

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
        bruto = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return bruto if isinstance(bruto, list) else []


def listar() -> list[Perfil]:
    achados = []
    for d in _carregar_bruto():
        if isinstance(d, dict) and {"id", "nome", "cor"} <= d.keys():
            achados.append(
                Perfil(d["id"], d["nome"], d["cor"], d.get("ferramenta", "claude"), d.get("pasta", ""))
            )
    return achados


def _gravar(perfis: list[Perfil]) -> None:
    gravar_texto(ARQUIVO, json.dumps([asdict(p) for p in perfis], indent=2, ensure_ascii=False))


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


_TAMANHO: dict[str, tuple[float, int]] = {}
VALIDADE_DO_TAMANHO = 60


def tamanho(perfil: Perfil) -> int:
    """Quanto a pasta de login daquela conta ocupa em disco.

    So a tela de Contas mostra este numero, e a pasta da conta principal passa de tres mil
    arquivos. Por isso e medido com scandir, que no Windows ja traz o tamanho junto do
    listagem, e o resultado vale por um minuto.
    """
    raiz = perfil.config_dir
    chave = str(raiz)
    lembrado = _TAMANHO.get(chave)
    agora = time.monotonic()
    if lembrado and agora - lembrado[0] < VALIDADE_DO_TAMANHO:
        return lembrado[1]
    if not raiz.is_dir():
        return 0

    total = 0
    pilha = [str(raiz)]
    while pilha:
        atual = pilha.pop()
        try:
            entradas = list(os.scandir(atual))
        except OSError:
            continue
        for entrada in entradas:
            try:
                if entrada.is_dir(follow_symlinks=False):
                    pilha.append(entrada.path)
                elif entrada.is_file(follow_symlinks=False):
                    total += entrada.stat().st_size
            except OSError:
                continue
    _TAMANHO[chave] = (agora, total)
    return total


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
    linha = atual.get("statusLine") if isinstance(atual, dict) else None
    comando = linha.get("command", "") if isinstance(linha, dict) else ""
    if not isinstance(comando, str):
        return ""
    if not _nosso_comando(comando):
        return comando
    # ja e o coletor, que leva a status line de antes no fim, entre aspas. Devolver vazio aqui
    # fazia a abertura seguinte de aba reinstalar sem ela, e a barra da pessoa sumia de vez
    casado = re.search(r'(?:coletar\.py|loom-coletor(?:\.exe)?|loom\.coletor)"?\s+\S+\s+"(.*)"\s*$', comando)
    return casado.group(1).replace('\\"', '"') if casado else ""


def _nosso_comando(comando: str) -> bool:
    return any(marca in comando for marca in ("coletar.py", "loom.coletor", "loom-coletor"))


def _chamada_coletor(python: str, projeto: str) -> str:
    """Como o Claude Code chama o coletor.

    No app instalado quem responde e o loom-coletor.exe, ao lado do executavel da
    janela: la nao existe Python nem o coletar.py do repositorio. Rodando daqui,
    continua sendo o interpretador atual com o script.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(python).with_name("loom-coletor.exe")}"'
    return f'"{python}" "{Path(projeto) / "coletar.py"}"'


def pasta_principal() -> Path:
    return Path(os.path.expanduser("~")) / ".claude"


# o gancho de inicio marca a sessao com a aba; o de aviso diz que o Claude parou pra pedir
# permissao, o que nao vai pro transcript
GANCHOS_DO_COLETOR = (("SessionStart", "--hook"), ("Notification", "--aviso"))


def _gancho_nosso(comando: str) -> bool:
    # o da conta principal passa pelo status-line.cmd, que nao tem o nome do coletor
    return _nosso_comando(comando) or (
        "status-line.cmd" in comando and any(f" {b} " in f"{comando} " for _, b in GANCHOS_DO_COLETOR)
    )


def _grupo_nosso(grupo) -> bool:
    if not isinstance(grupo, dict):
        return False
    return any(
        _gancho_nosso(str((h or {}).get("command", "")))
        for h in (grupo.get("hooks") or [])
        if isinstance(h, dict)
    )


def _pendurar_ganchos(atual: dict, chamada: str, perfil_id: str) -> None:
    """Poe os ganchos do coletor no settings, trocando o nosso antigo e mantendo os da pessoa."""
    ganchos = atual.get("hooks")
    if not isinstance(ganchos, dict):
        ganchos = atual["hooks"] = {}
    for evento, bandeira in GANCHOS_DO_COLETOR:
        existentes = ganchos.get(evento)
        grupos = [g for g in existentes if not _grupo_nosso(g)] if isinstance(existentes, list) else []
        grupos.append({"hooks": [{"type": "command", "command": f"{chamada} {bandeira} {perfil_id}"}]})
        ganchos[evento] = grupos


def _tirar_ganchos(atual: dict) -> None:
    ganchos = atual.get("hooks")
    if not isinstance(ganchos, dict):
        return
    for evento, _ in GANCHOS_DO_COLETOR:
        existentes = ganchos.get(evento)
        if not isinstance(existentes, list):
            continue
        sobram = [g for g in existentes if not _grupo_nosso(g)]
        if sobram:
            ganchos[evento] = sobram
        else:
            ganchos.pop(evento, None)
    if not ganchos:
        atual.pop("hooks", None)


def _settings_principal() -> tuple[Path, dict | None]:
    """O settings da pasta de login de sempre. None quando ele existe e nao da pra ler."""
    alvo = pasta_principal() / "settings.json"
    if not alvo.exists():
        return alvo, {}
    try:
        atual = json.loads(alvo.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return alvo, None
    return alvo, atual if isinstance(atual, dict) else None


def _atalho_da_status_line(linha) -> str | None:
    """O texto do status-line.cmd gerado por este app que a status line chama, se chama um."""
    comando = linha.get("command", "") if isinstance(linha, dict) else ""
    casado = re.match(r'\s*"([^"]+status-line\.cmd)"', comando if isinstance(comando, str) else "")
    if not casado:
        return None
    try:
        texto = Path(casado.group(1)).read_text(encoding="utf-8")
    except OSError:
        return None
    linhas = texto.splitlines()
    if len(linhas) < 2 or linhas[0] != "@echo off" or not linhas[1].startswith("rem Gerado pelo "):
        return None
    return texto


def _anterior_do_atalho(texto: str) -> str:
    linhas = texto.splitlines()
    try:
        return linhas[linhas.index(") else (") + 1].strip()
    except (ValueError, IndexError):
        return ""


def leitura_principal_ligada() -> bool:
    """Se a conta de sempre ja passa pelo coletor, o que faz o limite e a aba aparecerem."""
    _, atual = _settings_principal()
    return bool(atual) and _atalho_da_status_line(atual.get("statusLine")) is not None


def ligar_leitura_principal(python: str, projeto: str) -> bool:
    """Faz a conta de sempre passar pelo coletor, so com o clique da pessoa.

    Sem isso a aba sem conta e a Principal cadastrada nunca se ligam a conversa e o limite
    fica em Sem leitura pra sempre. A status line que ja existia continua sendo chamada, e
    vai pro caminho de fuga do atalho: se o Loom sumir, ela volta sozinha. O resto do
    settings nao e tocado, e um settings ilegivel nao e reescrito.
    """
    alvo, atual = _settings_principal()
    if atual is None:
        return False
    linha = atual.get("statusLine")
    ja_nosso = _atalho_da_status_line(linha)
    if ja_nosso is not None:
        anterior = _anterior_do_atalho(ja_nosso)
    else:
        anterior = linha.get("command", "") if isinstance(linha, dict) else ""
        anterior = anterior if isinstance(anterior, str) and not _nosso_comando(anterior) else ""
    atalho = blindar_status_line(python, projeto, anterior)
    comando = f'"{atalho}" __padrao__'
    if anterior:
        comando += ' "' + anterior.replace('"', '\\"') + '"'
    atual["statusLine"] = {**(linha if isinstance(linha, dict) else {}), "type": "command", "command": comando}
    _pendurar_ganchos(atual, f'"{atalho}"', "__padrao__")
    try:
        gravar_texto(alvo, json.dumps(atual, indent=2, ensure_ascii=False))
    except OSError:
        return False
    return True


def desligar_leitura_principal() -> bool:
    """Devolve a status line de antes e tira os ganchos do coletor da conta de sempre."""
    alvo, atual = _settings_principal()
    if not atual:
        return False
    antes = json.dumps(atual, sort_keys=True)
    linha = atual.get("statusLine")
    nosso = _atalho_da_status_line(linha)
    if nosso is not None:
        anterior = _anterior_do_atalho(nosso)
        if anterior:
            atual["statusLine"] = {**linha, "command": anterior}
        else:
            atual.pop("statusLine", None)
    _tirar_ganchos(atual)
    if json.dumps(atual, sort_keys=True) == antes:
        return False
    try:
        gravar_texto(alvo, json.dumps(atual, indent=2, ensure_ascii=False))
    except OSError:
        return False
    return True


def instalar_coletor(perfil: Perfil, python: str, projeto: str) -> bool:
    """Registra o Loom como status line daquele perfil.

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
    if not isinstance(atual, dict):
        atual = {}

    anterior = _statusline_anterior(perfil.config_dir)
    chamada = _chamada_coletor(python, projeto)
    comando = f"{chamada} {perfil.id}"
    if anterior:
        # aspas de dentro vao escapadas: sem isso o Windows partia a barra de antes no meio e o
        # coletor recebia so um pedaco dela, tipo C:\Users\Maria de "C:\Users\Maria Clara\barra.cmd"
        comando += ' "' + anterior.replace('"', '\\"') + '"'

    antes = json.dumps(atual, sort_keys=True)
    atual["statusLine"] = {"type": "command", "command": comando, "padding": 0}
    if not getattr(sys, "frozen", False):
        atual.setdefault("env", {})["PYTHONPATH"] = projeto

    _pendurar_ganchos(atual, chamada, perfil.id)

    if json.dumps(atual, sort_keys=True) == antes:
        return False
    try:
        gravar_texto(alvo, json.dumps(atual, indent=2, ensure_ascii=False))
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
        gravar_texto(alvo, json.dumps(atual, indent=2, ensure_ascii=False))
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
        gravar_texto(alvo, json.dumps(atual, indent=2, ensure_ascii=False))
    except OSError:
        return False
    return True


_IDENTIDADE: dict[str, tuple[float, dict]] = {}
CACHE_IDENTIDADE = RAIZ / "identidade.json"


def _identidade_do_disco() -> dict:
    if _IDENTIDADE:
        return {}
    try:
        salvo = json.loads(CACHE_IDENTIDADE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(salvo, dict):
        return {}
    for chave, valor in salvo.items():
        if isinstance(valor, dict) and isinstance(valor.get("carimbo"), (int, float)):
            _IDENTIDADE[chave] = (valor["carimbo"], valor.get("resumo") or {})
    return {}


def _gravar_identidade() -> None:
    try:
        RAIZ.mkdir(parents=True, exist_ok=True)
        CACHE_IDENTIDADE.write_text(
            json.dumps({k: {"carimbo": c, "resumo": r} for k, (c, r) in _IDENTIDADE.items()}),
            encoding="utf-8",
        )
    except OSError:
        pass


def identidade_guardada(perfil: Perfil) -> tuple[dict, bool]:
    """O que ja se sabe do login daquela pasta, sem perguntar nada ao CLI.

    O segundo valor diz se a resposta guardada ficou velha: a credencial mudou desde a
    ultima pergunta, o que acontece toda vez que o Claude renova o token.
    """
    credencial = perfil.config_dir / mod_ferramentas.por_id(perfil.ferramenta).login
    try:
        carimbo = credencial.stat().st_mtime
    except OSError:
        return {}, False
    _identidade_do_disco()
    lembrado = _IDENTIDADE.get(str(perfil.config_dir))
    if lembrado and lembrado[0] == carimbo:
        return lembrado[1], False
    return (lembrado[1] if lembrado else {}), True


def guardar_identidade(chave: str, carimbo: float, resumo: dict) -> None:
    _IDENTIDADE[chave] = (carimbo, resumo)
    _gravar_identidade()


def identidade(perfil: Perfil) -> dict:
    """Descobre de quem e o login daquela pasta, perguntando ao proprio CLI se preciso.

    Espera a resposta. A tela nao usa esta: ela mostra o que esta guardado e pergunta ao
    fundo, porque a espera travava a janela inteira.
    """
    guardada, velha = identidade_guardada(perfil)
    if not velha:
        return guardada
    resposta = perguntar_identidade(perfil)
    if resposta is None:
        return {}
    guardar_identidade(str(perfil.config_dir), *resposta)
    return resposta[1]


def perguntar_identidade(perfil: Perfil) -> tuple[float, dict] | None:
    """Pergunta ao proprio CLI de quem e o login, e devolve junto o carimbo da credencial.

    `claude auth status` nao gasta token nenhum, so le o que esta no disco. Mesmo assim ele
    sobe um processo Node e leva perto de um segundo, entao a resposta e guardada ate a
    credencial mudar E tambem em disco: sem isso, todo start do app esperava esse processo
    por conta antes de a primeira aba aparecer. Aqui so se pergunta; guardar e de quem chamou,
    pra duas perguntas ao mesmo tempo nao disputarem o cache.
    """
    credencial = perfil.config_dir / mod_ferramentas.por_id(perfil.ferramenta).login
    try:
        carimbo = credencial.stat().st_mtime
    except OSError:
        return None

    comando = mod_ferramentas.por_id(perfil.ferramenta).comando
    if not comando:
        return None
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
            # com pythonw nao ha console, e sem isto o cmd.exe do shell pisca uma janela preta
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        dados = json.loads(saida.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        dados = {}

    if not isinstance(dados, dict):
        dados = {}
    resumo = {
        "email": dados.get("email", ""),
        "plano": dados.get("subscriptionType") or dados.get("authMethod", ""),
        "logado": bool(dados.get("loggedIn")),
    }
    return carimbo, resumo


def blindar_status_line(python: str, projeto: str, anterior: str) -> Path:
    """Cria um atalho que sobrevive ao projeto sumir.

    O settings do Claude guarda caminho absoluto. Se a pasta do Loom for movida ou
    apagada, a status line quebraria em TODAS as sessoes, inclusive fora deste app.
    Este arquivo mora na pasta de dados, confere se o coletor ainda existe e, se nao existe,
    chama direto a status line de sempre. O pior caso deixa de ser perder a barra.
    """
    destino = RAIZ / "status-line.cmd"
    RAIZ.mkdir(parents=True, exist_ok=True)
    _gravar_atalho(destino, _texto_do_atalho(python, projeto, anterior))
    return destino


def _texto_do_atalho(python: str, projeto: str, anterior: str) -> str:
    """O status-line.cmd: chama o coletor se ele existe, senao a status line de sempre."""
    if getattr(sys, "frozen", False):
        # no app instalado o coletor e o exe ao lado da janela, e nao existe coletar.py
        entrada = Path(python).with_name("loom-coletor.exe")
        chamada = f'"{entrada}" %*'
    else:
        entrada = Path(projeto) / "coletar.py"
        chamada = f'"{python}" "{entrada}" %*'
    linhas = [
        "@echo off",
        "rem Gerado pelo Loom. Se o projeto sumir, a status line de sempre continua.",
        # os ganchos tambem passam por aqui; sem o coletor eles saem calados, porque o que um
        # gancho de inicio imprime entra na conversa do Claude
        f'if not exist "{entrada}" if "%~1"=="--hook" exit /b 0',
        f'if not exist "{entrada}" if "%~1"=="--aviso" exit /b 0',
        f'if exist "{entrada}" (',
        f"  {chamada}",
        ") else (",
        f"  {anterior}",
        ")",
    ]
    quebra = chr(13) + chr(10)
    return quebra.join(linhas) + quebra


def _gravar_atalho(destino: Path, texto: str) -> None:
    # newline vazio: sem isso o Windows converte de novo e o arquivo sai com quebra dupla
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        fh.write(texto)


def reparar_status_line_principal(python: str, projeto: str) -> bool:
    """Aponta pro coletor de agora o atalho de status line que a conta principal ja usa.

    A conta principal nao e do Loom e ele nao instala nada nela. Mas quando o settings dela
    ja chama um status-line.cmd gerado por este app, inclusive o de antes da troca de nome,
    e o atalho aponta pra um coletor que nao existe mais, ele cai calado na status line de
    sempre e o uso da conta fica sem leitura pra sempre. So o atalho e reescrito, com a
    mesma status line de sempre no caminho de fuga; o settings da conta nao e tocado.
    """
    try:
        atual = json.loads((pasta_principal() / "settings.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    linha = atual.get("statusLine") if isinstance(atual, dict) else None
    comando = linha.get("command", "") if isinstance(linha, dict) else ""
    casado = re.match(r'\s*"([^"]+status-line\.cmd)"', comando if isinstance(comando, str) else "")
    if not casado:
        return False
    atalho = Path(casado.group(1))
    try:
        texto = atalho.read_text(encoding="utf-8")
    except OSError:
        return False
    linhas = texto.splitlines()
    # so atalho gerado por nos: o de outra ferramenta com o mesmo nome fica como esta
    if len(linhas) < 2 or linhas[0] != "@echo off" or not linhas[1].startswith("rem Gerado pelo "):
        return False
    try:
        anterior = linhas[linhas.index(") else (") + 1].strip()
    except (ValueError, IndexError):
        return False
    novo = _texto_do_atalho(python, projeto, anterior)
    if texto.replace("\r\n", "\n") == novo.replace("\r\n", "\n"):
        return False
    try:
        _gravar_atalho(atalho, novo)
    except OSError:
        return False
    return True
