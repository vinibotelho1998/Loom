from __future__ import annotations

import os
import shutil
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


def padrao(shells: list[Shell]) -> Shell:
    por_id = {s.id: s for s in shells}
    for preferido in ("pwsh", "powershell", "cmd"):
        if preferido in por_id:
            return por_id[preferido]
    return shells[0]
