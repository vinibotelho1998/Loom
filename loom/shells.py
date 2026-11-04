from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Shell:
    id: str
    nome: str
    caminho: str
    args: tuple[str, ...] = ()

    def como_dict(self) -> dict:
        d = asdict(self)
        d["args"] = list(self.args)
        return d


CANDIDATOS = (
    ("pwsh", "PowerShell 7", "pwsh.exe", ("-NoLogo",)),
    ("powershell", "Windows PowerShell", "powershell.exe", ("-NoLogo",)),
    ("cmd", "Prompt de Comando", "cmd.exe", ()),
    ("bash", "Git Bash", "bash.exe", ("--login", "-i")),
    ("wsl", "WSL", "wsl.exe", ()),
)

# fora do Windows o shell e o da pessoa, e a ordem so decide quem vem antes na lista
CANDIDATOS_UNIX = (
    ("bash", "Bash", "bash", ()),
    ("zsh", "Zsh", "zsh", ()),
    ("fish", "Fish", "fish", ()),
    ("nu", "Nushell", "nu", ()),
)
# entendem -i -c; o fish tem o proprio jeito, e o nu fica de fora: quem chama volta a digitar
FAMILIA_SH = ("bash", "zsh", "sh", "dash", "ksh")

EXTRAS = {
    "bash": (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe",
    ),
}


def _bash_do_windows(caminho: str) -> bool:
    """O bash.exe do System32 e o do WindowsApps sao o lancador do WSL, nao o Git Bash."""
    pasta = os.path.normcase(os.path.dirname(caminho))
    return _do_windows(caminho) or os.path.basename(pasta) == "windowsapps"


def _git_bash() -> str | None:
    """O bash do Git de verdade.

    No PATH de fabrica o primeiro bash.exe e o do System32, que abre o WSL: a entrada Git Bash
    caia nele, com outro HOME e outro git, e sem distro a aba abria e fechava. O do Git vem
    primeiro pelo proprio git, subindo de ...\\Git\\cmd\\git.exe, depois pelos lugares de sempre.
    """
    git = shutil.which("git")
    if git:
        raiz = os.path.dirname(os.path.dirname(git))
        vizinho = os.path.join(raiz, "bin", "bash.exe")
        if os.path.exists(vizinho):
            return vizinho
    for alternativo in EXTRAS["bash"]:
        alternativo = os.path.expandvars(alternativo)
        if os.path.exists(alternativo):
            return alternativo
    caminho = shutil.which("bash.exe")
    if caminho and not _bash_do_windows(caminho):
        return caminho
    return None


def _descobrir_unix() -> list[Shell]:
    achados = [
        Shell(sid, nome, caminho, args)
        for sid, nome, exe, args in CANDIDATOS_UNIX
        if (caminho := shutil.which(exe))
    ]
    # o $SHELL da pessoa pode nao ser nenhum dos conhecidos e tem que estar na lista mesmo assim
    dela = os.environ.get("SHELL") or ""
    sid = os.path.basename(dela)
    if sid and os.path.exists(dela) and not any(s.id == sid for s in achados):
        achados.insert(0, Shell(sid, sid.capitalize(), dela))
    return achados


def descobrir() -> list[Shell]:
    if sys.platform != "win32":
        return _descobrir_unix()
    achados: list[Shell] = []
    for sid, nome, exe, args in CANDIDATOS:
        if sid == "bash":
            caminho = _git_bash()
            if caminho:
                achados.append(Shell(sid, nome, caminho, args))
            continue
        caminho = shutil.which(exe)
        if not caminho:
            for alternativo in EXTRAS.get(sid, ()):
                if os.path.exists(alternativo):
                    caminho = alternativo
                    break
        if caminho:
            achados.append(Shell(sid, nome, caminho, args))
    return achados


# Os modos do sudo do Windows, como ele mesmo grava no registro. So o ultimo roda o shell
# elevado dentro do mesmo console; os outros abrem outra janela ou nao aceitam tecla.
SUDO_DESLIGADO, SUDO_NOVA_JANELA, SUDO_SEM_ENTRADA, SUDO_NA_MESMA_JANELA = 0, 1, 2, 3
CHAVE_DO_SUDO = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Sudo"


def _modo_do_sudo() -> int | None:
    """Em que modo o sudo do Windows esta. None quando o registro nao diz."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CHAVE_DO_SUDO) as chave:
            valor, _tipo = winreg.QueryValueEx(chave, "Enabled")
    except OSError:
        return None
    return valor if isinstance(valor, int) else None


def _do_windows(caminho: str) -> bool:
    sistema = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.normcase(os.path.dirname(caminho)) == os.path.normcase(os.path.join(sistema, "System32"))


def elevacao() -> dict:
    """Descobre se da pra abrir um shell elevado dentro da propria aba.

    O UAC cria o processo elevado fora do nosso ConPTY, entao ele nao caberia na aba.
    O `sudo` que vem no Windows 11 resolve isso, mas so depois de ligado nas Configuracoes,
    em Sistema, Para desenvolvedores, e so no modo que roda na mesma janela: no modo de
    fabrica ele abre um console separado e a aba fechava com codigo 0, parecendo quebrada.
    Sem ele, nao ha caminho honesto e a opcao fica fora.
    """
    caminho = shutil.which("sudo") or shutil.which("gsudo")
    if not caminho:
        return {"disponivel": False, "motivo": "sem sudo", "comando": ""}

    if _do_windows(caminho):
        modo = _modo_do_sudo()
        if modo == SUDO_NA_MESMA_JANELA:
            return {"disponivel": True, "motivo": "", "comando": caminho}
        if modo is not None:
            # o do Windows nao serve assim, mas o gsudo instalado ao lado serve
            reserva = shutil.which("gsudo")
            if reserva:
                return {"disponivel": True, "motivo": "", "comando": reserva}
            motivo = {
                SUDO_DESLIGADO: "desligado",
                SUDO_NOVA_JANELA: "nova janela",
                SUDO_SEM_ENTRADA: "sem entrada",
            }.get(modo, "desligado")
            return {"disponivel": False, "motivo": motivo, "comando": caminho}

    try:
        prova = subprocess.run(
            [caminho, "--help"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=8,
            # a janela do app nao tem console, e sem isto o sudo abria uma janela preta
            # que piscava a cada start: o Windows 11 traz o sudo.exe mesmo desligado
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return {"disponivel": False, "motivo": "sem sudo", "comando": ""}

    texto = ((prova.stdout or "") + (prova.stderr or "")).lower()
    if "desabilitado" in texto or "disabled" in texto or "is disabled" in texto:
        return {"disponivel": False, "motivo": "desligado", "comando": caminho}
    return {"disponivel": True, "motivo": "", "comando": caminho}


def elevado(shell: Shell, sudo: str) -> list[str]:
    """Monta a linha que roda aquele shell com privilegio, na mesma aba."""
    return [sudo, shell.caminho, *shell.args]


def com_comando(shell: Shell, comando: str) -> list[str] | None:
    """A linha que sobe o shell ja rodando o comando e continua aberto quando ele acaba.

    Digitar o comando 700 ms depois de a aba nascer dependia de o shell ter subido nesse tempo:
    com o perfil do PowerShell lento, o texto caia no meio do prompt sendo desenhado e o CLI nao
    abria, calado. Pelo argv o shell so roda o comando quando esta pronto. WSL e shell que nao
    conhecemos devolvem None, e quem chama volta a digitar.
    """
    comando = (comando or "").strip()
    if not comando:
        return None
    if shell.id in ("pwsh", "powershell"):
        return [shell.caminho, *shell.args, "-NoExit", "-Command", comando]
    if shell.id == "cmd":
        return [shell.caminho, *shell.args, "/K", comando]
    if sys.platform != "win32":
        return _com_comando_unix(shell, comando)
    if shell.id == "bash":
        # o -c sozinho sairia no fim do comando; o exec deixa no lugar um bash de login comum
        return [shell.caminho, "--login", "-i", "-c", f"{comando}; exec bash --login -i"]
    return None


def _com_comando_unix(shell: Shell, comando: str) -> list[str] | None:
    """O mesmo para bash, zsh e fish.

    Sem o trap, Ctrl+C num programa que morre pelo sinal levava junto o resto da linha do -c, com
    o exec, e a aba fechava em vez de voltar pro prompt. O trap nao passa pro programa, que nasce
    com o sinal no padrao.
    """
    if shell.id == "fish":
        return [shell.caminho, *shell.args, "-i", "-C", comando]
    if shell.id in FAMILIA_SH:
        depois = shlex.join([shell.caminho, *shell.args, "-i"])
        return [shell.caminho, *shell.args, "-i", "-c", f"trap : INT; {comando}; trap - INT; exec {depois}"]
    return None


def padrao(shells: list[Shell]) -> Shell:
    por_id = {s.id: s for s in shells}
    if sys.platform != "win32":
        dela = os.path.basename(os.environ.get("SHELL") or "")
        if dela in por_id:
            return por_id[dela]
    for preferido in ("pwsh", "powershell", "cmd", "bash"):
        if preferido in por_id:
            return por_id[preferido]
    return shells[0]
