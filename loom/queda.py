"""Queda da tela: ao subir, o Loom descobre se o anterior morreu sem fechar.

O Qt WebEngine roda a GPU do Chromium dentro do proprio processo do app. Uma falha no driver de
video ali derruba o Loom inteiro, e com ele todos os shells, sem nenhum aviso antes. Quem sobe
depois so consegue saber pela marca que o anterior deixou: ela e gravada ao subir e apagada no
aboutToQuit, entao marca que sobrou e sinal de que o Loom anterior nao chegou a fechar.

Nem toda marca que sobra e queda da tela:
- queda de energia ou o computador desligado na marra tambem deixam a marca, mas ai o boot e
  outro. O Loom foi junto com a maquina, e a placa de video nao tem culpa.
- a marca de um Loom que continua aberto e dele: quem sobe agora e a segunda instancia, que so
  vai chamar a primeira pra frente.

Tudo aqui roda antes do Qt subir, porque decide com que motor ele sobe; nada de Qt nos imports.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import caminhos as mod_caminhos

log = logging.getLogger(__name__)

DIAS_SEM_PLACA = 7
# o bastante pra enxergar um padrao sem o arquivo crescer pra sempre
TETO_DE_QUEDAS = 50
# relogio acertado pela rede depois do boot mexe uns segundos na conta de quando ele ligou
FOLGA_DO_BOOT = 120.0
CHAVE_DO_BOOT = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"
AINDA_RODANDO = 259


# Os caminhos sao lidos na hora, e nao guardados no import: a marca e o historico decidem o
# motor do Loom de verdade, e um teste que esquecesse de trocar a pasta mexeria nos dois.
def arquivo() -> Path:
    return mod_caminhos.RAIZ / "quedas.json"


def arquivo_da_marca() -> Path:
    return mod_caminhos.RAIZ / f"aberto-{mod_caminhos.marca_da_instalacao()}.json"


def _kernel32():
    # instancia propria da dll: mexer nos tipos do ctypes.windll compartilhado mudaria as
    # chamadas de quem mais usa o kernel32 no processo
    import ctypes.wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD)]
    k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(w.FILETIME)] * 4
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.GetTickCount64.restype = ctypes.c_ulonglong
    return w, k


def criado_em(pid: int) -> int | None:
    """Quando o processo nasceu, ou None se ele nao existe mais.

    O numero do processo volta a ser usado pelo Windows. So o numero diria que o Loom caido
    continua vivo sempre que outro programa herdasse o mesmo pid.
    """
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return 0
    w, k = _kernel32()
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    processo = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not processo:
        return None
    try:
        codigo = w.DWORD()
        if not k.GetExitCodeProcess(processo, ctypes.byref(codigo)) or codigo.value != AINDA_RODANDO:
            return None
        nasceu, saiu, nucleo, usuario = w.FILETIME(), w.FILETIME(), w.FILETIME(), w.FILETIME()
        if not k.GetProcessTimes(processo, ctypes.byref(nasceu), ctypes.byref(saiu),
                                 ctypes.byref(nucleo), ctypes.byref(usuario)):
            return None
        return nasceu.dwHighDateTime << 32 | nasceu.dwLowDateTime
    finally:
        k.CloseHandle(processo)


def _id_do_boot() -> int | None:
    """O contador que o Windows soma a cada partida. None quando nao da pra ler."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CHAVE_DO_BOOT) as chave:
            valor, _ = winreg.QueryValueEx(chave, "BootId")
        return int(valor)
    except (OSError, TypeError, ValueError):
        return None


def boot_atual() -> dict:
    """Qual partida da maquina e esta: o contador do Windows e a hora em que ela ligou."""
    if sys.platform == "win32":
        _, k = _kernel32()
        ligada_ha = k.GetTickCount64() / 1000
    else:
        ligada_ha = time.monotonic()
    return {"id": _id_do_boot(), "em": round(time.time() - ligada_ha, 1)}


def mesmo_boot(antes, agora: dict) -> bool:
    """Se a marca foi gravada nesta mesma partida da maquina.

    Na duvida diz que nao: contar queda de energia como queda da tela deixaria o Loom sem placa
    de video por uma semana sem motivo.
    """
    if not isinstance(antes, dict):
        return False
    if antes.get("id") is not None and agora.get("id") is not None:
        return antes["id"] == agora["id"]
    try:
        return abs(float(antes["em"]) - float(agora["em"])) < FOLGA_DO_BOOT
    except (KeyError, TypeError, ValueError):
        return False


def _ler(caminho: Path) -> dict | None:
    """O JSON do arquivo; None se ele nao existe, vazio se existe e nao da pra ler."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _apagar(caminho: Path) -> None:
    try:
        caminho.unlink(missing_ok=True)
    except OSError:
        log.warning("nao consegui apagar %s", caminho)


def _quando(agora: float | None = None) -> datetime:
    return datetime.fromtimestamp(time.time() if agora is None else agora).astimezone()


def _epoca(texto) -> float | None:
    try:
        return datetime.fromisoformat(str(texto)).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _dono_vivo(marca: dict) -> bool:
    pid = marca.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False
    nasceu = criado_em(pid)
    return nasceu is not None and nasceu == marca.get("criado")


def marcar_subida(desenho: str = "") -> None:
    """Grava a marca deste Loom. Ela some no aboutToQuit; se sobrar, o proximo sabe que este caiu."""
    dados = {
        "pid": os.getpid(),
        "criado": criado_em(os.getpid()),
        "boot": boot_atual(),
        "desde": _quando().isoformat(timespec="seconds"),
        "desenho": desenho,
    }
    try:
        mod_caminhos.gravar_texto(arquivo_da_marca(), json.dumps(dados))
    except OSError:
        log.warning("nao consegui gravar a marca de tela aberta", exc_info=True)


def apagar_marca() -> None:
    """Fechou direito: a marca sai. So a deste processo; a de outro Loom continua valendo."""
    marca = _ler(arquivo_da_marca())
    if marca and marca.get("pid") == os.getpid():
        _apagar(arquivo_da_marca())


def conferir_ao_subir(agora: float | None = None) -> dict | None:
    """Anota a queda se o Loom anterior desta instalacao morreu sem fechar. Devolve a anotada."""
    caminho = arquivo_da_marca()
    marca = _ler(caminho)
    if marca is None:
        return None
    if _dono_vivo(marca):
        return None
    _apagar(caminho)
    if not mesmo_boot(marca.get("boot"), boot_atual()):
        log.info(
            "o Loom aberto desde %s nao fechou, mas a maquina ligou de novo depois: "
            "queda de energia ou desligamento, nao queda da tela", marca.get("desde", "?")
        )
        return None
    log.warning("o Loom aberto desde %s morreu sem fechar: queda da tela", marca.get("desde", "?"))
    return registrar("tela", agora)


def _historico() -> dict:
    dados = _ler(arquivo()) or {}
    quedas = dados.get("quedas")
    validas = [
        q for q in (quedas if isinstance(quedas, list) else [])
        if isinstance(q, dict) and _epoca(q.get("quando")) is not None
    ]
    voltou = dados.get("voltouEm")
    return {"quedas": validas, "voltouEm": voltou if isinstance(voltou, str) else ""}


def registrar(tipo: str, agora: float | None = None) -> dict:
    """Anota uma queda com data e hora. O tipo diz quem percebeu: a marca ou a pagina."""
    queda = {"quando": _quando(agora).isoformat(timespec="seconds"), "tipo": tipo}
    dados = _historico()
    dados["quedas"] = (dados["quedas"] + [queda])[-TETO_DE_QUEDAS:]
    try:
        mod_caminhos.gravar_texto(arquivo(), json.dumps(dados, indent=2, ensure_ascii=False))
    except OSError:
        log.warning("nao consegui anotar a queda", exc_info=True)
    return queda


def recente(agora: float | None = None) -> dict | None:
    """A ultima queda dos ultimos 7 dias, com o dia em que o prazo acaba. None se nao ha.

    Queda de antes de a pessoa pedir pra voltar a placa de video nao conta mais.
    """
    agora = time.time() if agora is None else agora
    dados = _historico()
    limite = max(agora - DIAS_SEM_PLACA * 86400, _epoca(dados["voltouEm"]) or 0.0)
    contam = [q for q in dados["quedas"] if _epoca(q["quando"]) > limite]
    if not contam:
        return None
    ultima = max(contam, key=lambda q: _epoca(q["quando"]))
    fim = datetime.fromisoformat(ultima["quando"]) + timedelta(days=DIAS_SEM_PLACA)
    return {**ultima, "ate": fim.isoformat(timespec="seconds")}


def voltar_para_gpu(agora: float | None = None) -> None:
    """A pessoa pediu a placa de volta: as quedas ate aqui deixam de contar pro modo automatico.

    O historico fica, porque e ele que mostra se a tela esta caindo de novo.
    """
    dados = _historico()
    dados["voltouEm"] = _quando(agora).isoformat(timespec="seconds")
    mod_caminhos.gravar_texto(arquivo(), json.dumps(dados, indent=2, ensure_ascii=False))
