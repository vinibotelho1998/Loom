"""Um Loom so por pessoa e por instalacao.

Os shells moram no processo que os abriu. Uma segunda janela nasceria com shells novos e
nunca acharia as conversas que estao rodando na primeira, que e justamente o que se queria
de volta ao abrir o Loom de novo. Entao a segunda instancia so chama a primeira, que refaz a
tela e vem pra frente, e sai.
"""

from __future__ import annotations

import getpass
import hashlib
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
PEDIDO = b"mostrar"
RESPOSTA = b"ok"


def nome() -> str:
    """O nome do canal entre instancias.

    Leva a pasta da instalacao: o Loom rodando do codigo e o instalado nao se enxergam,
    senao abrir o de desenvolvimento com o instalado aberto so traria o instalado pra frente.
    """
    marca = hashlib.sha1(str(UI_DIR).lower().encode("utf-8")).hexdigest()[:10]
    try:
        dono = getpass.getuser()
    except OSError:
        dono = "loom"
    return f"loom-{dono}-{marca}"


def chamar_quem_ja_esta_aberto(espera_ms: int = 3000) -> bool:
    """Pede ao Loom que ja esta aberto pra refazer a tela e vir pra frente.

    Devolve False se nao ha outro aberto ou se ele nao respondeu, travado de vez; nesses
    casos esta instancia sobe normalmente.
    """
    conexao = QLocalSocket()
    conexao.connectToServer(nome())
    if not conexao.waitForConnected(500):
        return False
    if sys.platform == "win32":
        # o Windows so deixa a outra janela tomar o foco se quem esta na frente liberar
        import ctypes

        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except (AttributeError, OSError):
            pass
    conexao.write(PEDIDO + b"\n")
    conexao.flush()
    # Aqui ainda nao ha laco de eventos rodando, e sem ele o waitForReadyRead sozinho perdia
    # a resposta em tres de cada quatro vezes: o Loom aberto voltava pra frente e esta
    # instancia, achando que ninguem atendeu, subia uma segunda janela mesmo assim.
    resposta = b""
    prazo = time.monotonic() + espera_ms / 1000
    while time.monotonic() < prazo and not resposta.endswith(b"\n"):
        conexao.waitForReadyRead(50)
        QCoreApplication.processEvents()
        resposta += bytes(conexao.readAll())
    conexao.disconnectFromServer()
    return resposta.startswith(RESPOSTA)
