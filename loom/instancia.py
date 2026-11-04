"""Um Loom so por pessoa e por instalacao.

Os shells moram no processo que os abriu. Uma segunda janela nasceria com shells novos e
nunca acharia as conversas que estao rodando na primeira, que e justamente o que se queria
de volta ao abrir o Loom de novo. Entao a segunda instancia so chama a primeira, que vem pra
frente, e sai.

O canal so aparece depois que a janela e montada. Dois starts quase juntos, um clique duplo no
atalho, nao achavam ninguem no canal e subiam duas janelas; por isso ha tambem uma trava, um
mutex com o mesmo nome, pega logo no comeco por quem vai subir.
"""

from __future__ import annotations

import ctypes
import getpass
import os
import sys
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket

from . import caminhos as mod_caminhos

PEDIDO = b"mostrar"
RESPOSTA = b"ok"
# um pedido de verdade tem sete letras; mais que isto sem quebra de linha nao e o Loom chamando
TETO_DO_PEDIDO = 256
# o Loom que segura a trava leva uns segundos pra montar a janela e abrir o canal
PRAZO_DE_SUBIDA = 20.0

# o mutex fica aberto ate o processo sair, e o Windows o solta sozinho quando ele sai
_trava = None
# fora do Windows a trava e um flock, que o kernel tambem solta quando o processo morre
_travas_em_arquivo: dict[str, int] = {}


def nome() -> str:
    """O nome do canal entre instancias.

    Leva a pasta da instalacao: o Loom rodando do codigo e o instalado nao se enxergam,
    senao abrir o de desenvolvimento com o instalado aberto so traria o instalado pra frente.
    """
    try:
        dono = getpass.getuser()
    except OSError:
        dono = "loom"
    return f"loom-{dono}-{mod_caminhos.marca_da_instalacao()}"


def ler_pedido(acumulado: bytearray, chegou: bytes) -> bytes | None:
    """Junta o que chegou e devolve o pedido quando a linha fecha; None enquanto nao fecha.

    O pipe pode entregar a linha em pedacos, e lendo de uma vez so o primeiro pedaco virava um
    pedido estranho e a conexao era derrubada.
    """
    acumulado += chegou
    fim = acumulado.find(b"\n")
    if fim < 0:
        return None
    pedido = bytes(acumulado[:fim]).strip()
    del acumulado[: fim + 1]
    return pedido


def _kernel32():
    # instancia propria da dll, pra nao mexer nos tipos do ctypes.windll de todo o processo
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k.WaitForSingleObject.restype = ctypes.c_uint32
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return k


def trava_em_arquivo(nome_da_trava: str) -> bool:
    """A trava fora do Windows: um flock num arquivo da pasta de dados. False se outro a segura.

    Morrer de qualquer jeito solta o flock, sem arquivo velho pra limpar: e por isso que nao e
    arquivo de pid. O descritor nao passa pros filhos, senao um shell aberto seguraria a trava
    depois de o Loom sair.
    """
    if nome_da_trava in _travas_em_arquivo:
        return True
    import fcntl

    try:
        mod_caminhos.RAIZ.mkdir(parents=True, exist_ok=True)
        descritor = os.open(mod_caminhos.RAIZ / f"{nome_da_trava}.trava", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError:
        # sem trava fica como era antes dela, so com o canal
        return True
    try:
        fcntl.flock(descritor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descritor)
        return False
    _travas_em_arquivo[nome_da_trava] = descritor
    return True


def segurar_trava() -> bool:
    """Pega a trava deste usuario e desta instalacao. False enquanto outro Loom a segura.

    Um Loom que morreu larga a trava pro Windows, e quem pede depois a recebe como abandonada,
    que aqui conta como livre.
    """
    global _trava
    if sys.platform != "win32":
        return trava_em_arquivo(nome())
    k = _kernel32()
    if _trava is None:
        _trava = k.CreateMutexW(None, False, "Local\\" + nome())
        if not _trava:
            # sem mutex fica como era antes dele, so com o canal
            return True
    WAIT_OBJECT_0, WAIT_ABANDONED = 0x0, 0x80
    return k.WaitForSingleObject(_trava, 0) in (WAIT_OBJECT_0, WAIT_ABANDONED)


def esperar_quem_esta_subindo(prazo: float = PRAZO_DE_SUBIDA) -> bool:
    """Outro Loom segura a trava e ainda nao atende no canal: ele esta subindo agora.

    True quando ele atendeu e esta instancia deve sair. False quando a trava ficou livre, porque
    ele morreu no meio da subida, ou quando o prazo acabou com ele travado de vez; nos dois casos
    esta sobe, como subia antes de haver trava.
    """
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        if chamar_quem_ja_esta_aberto(espera_ms=1000):
            return True
        if segurar_trava():
            return False
        time.sleep(0.2)
    return False


def chamar_quem_ja_esta_aberto(espera_ms: int = 3000) -> bool:
    """Pede ao Loom que ja esta aberto pra vir pra frente.

    Devolve False se nao ha outro aberto ou se ele nao respondeu, travado de vez; nesses
    casos esta instancia sobe normalmente.
    """
    conexao = QLocalSocket()
    conexao.connectToServer(nome())
    if not conexao.waitForConnected(500):
        return False
    if sys.platform == "win32":
        # o Windows so deixa a outra janela tomar o foco se quem esta na frente liberar
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
