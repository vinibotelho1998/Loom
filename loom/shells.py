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
    "bash": (r"C:\Program Files\Git\bin\bash.exe",),
}


def descobrir() -> list[Shell]:
    achados: list[Shell] = []
    for sid, nome, exe, args in CANDIDATOS:
        caminho = shutil.which(exe)
        if not caminho:
            for alternativo in EXTRAS.get(sid, ()):
                if os.path.exists(alternativo):
                    caminho = alternativo
                    break
        if caminho:
            achados.append(Shell(sid, nome, caminho, args))
    return achados


def elevacao() -> dict:
    """Descobre se da pra abrir um shell elevado dentro da propria aba.

    O UAC cria o processo elevado fora do nosso ConPTY, entao ele nao caberia na aba.
    O `sudo` que vem no Windows 11 resolve isso, mas so depois de ligado nas Configuracoes,
    em Sistema, Para desenvolvedores. Sem ele, nao ha caminho honesto e a opcao fica fora.
    """
    caminho = shutil.which("sudo") or shutil.which("gsudo")
    if not caminho:
        return {"disponivel": False, "motivo": "sem sudo", "comando": ""}

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


def padrao(shells: list[Shell]) -> Shell:
    por_id = {s.id: s for s in shells}
    for preferido in ("pwsh", "powershell", "cmd"):
        if preferido in por_id:
            return por_id[preferido]
    return shells[0]
