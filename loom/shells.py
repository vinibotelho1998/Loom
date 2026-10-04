from __future__ import annotations

import os
import shutil
import subprocess
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


def descobrir() -> list[Shell]:
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
    if shell.id == "bash":
        # o -c sozinho sairia no fim do comando; o exec deixa no lugar um bash de login comum
        return [shell.caminho, "--login", "-i", "-c", f"{comando}; exec bash --login -i"]
    return None


def padrao(shells: list[Shell]) -> Shell:
    por_id = {s.id: s for s in shells}
    for preferido in ("pwsh", "powershell", "cmd"):
        if preferido in por_id:
            return por_id[preferido]
    return shells[0]
