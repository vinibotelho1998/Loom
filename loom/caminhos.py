from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

RAIZ = Path(os.path.expanduser("~")) / ".loom"
PERFIS = RAIZ / "perfis"
PASTA_DA_TELA = Path(__file__).resolve().parent.parent / "ui"


def marca_da_instalacao() -> str:
    """Dez letras que mudam com a pasta de onde o Loom roda.

    O Loom rodando do codigo e o instalado nao podem se confundir: nem no canal entre
    instancias, nem na marca de que a tela esta aberta.
    """
    return hashlib.sha1(str(PASTA_DA_TELA).lower().encode("utf-8")).hexdigest()[:10]


def completar_path(ambiente=os.environ) -> None:
    """Poe no PATH as pastas do usuario, onde o Claude e o Codex costumam morar.

    Aberto pelo menu do sistema, o app herda o PATH curto da sessao grafica, sem o que o .bashrc
    acrescenta. A aba acharia o CLI depois de ler o .bashrc, mas a tela de contas diria que ele
    nao esta instalado. Ficam no fim, sem passar na frente de nada que ja estava.
    """
    if sys.platform == "win32":
        return
    casa = Path(os.path.expanduser("~"))
    partes = [p for p in ambiente.get("PATH", "").split(os.pathsep) if p]
    for pasta in (casa / ".local" / "bin", casa / "bin"):
        if str(pasta) not in partes and pasta.is_dir():
            partes.append(str(pasta))
    ambiente["PATH"] = os.pathsep.join(partes)


def garantir() -> None:
    PERFIS.mkdir(parents=True, exist_ok=True)


def gravar_texto(alvo: Path, texto: str) -> None:
    """Grava o arquivo inteiro ou nao mexe nele.

    O write_text direto zera o arquivo antes de escrever. App fechado no meio, ou disco
    cheio, deixava o arquivo vazio, a leitura seguinte caia no padrao e a proxima gravacao
    confirmava a perda: a lista de contas ou os ajustes voltavam do zero. O temporario leva
    o numero do processo, como no abas.json, porque o coletor roda em processo proprio.
    """
    alvo.parent.mkdir(parents=True, exist_ok=True)
    provisorio = alvo.with_name(f"{alvo.name}.{os.getpid()}.tmp")
    try:
        provisorio.write_text(texto, encoding="utf-8")
        for tentativa in range(3):
            try:
                provisorio.replace(alvo)
                return
            except PermissionError:
                # no Windows, quem esta lendo o arquivo naquele instante segura a troca
                if tentativa == 2:
                    raise
                time.sleep(0.05)
    except OSError:
        provisorio.unlink(missing_ok=True)
        raise
