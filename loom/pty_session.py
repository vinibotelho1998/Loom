from __future__ import annotations

import os
import shutil
import sys
import threading

from PySide6.QtCore import QObject, Signal
from winpty import PtyProcess


def default_shell() -> list[str]:
    """pwsh se existir, senao o powershell de sempre, e cmd como ultimo recurso."""
    for nome, args in (("pwsh.exe", ["-NoLogo"]), ("powershell.exe", ["-NoLogo"])):
        caminho = shutil.which(nome)
        if caminho:
            return [caminho, *args]
    return [os.environ.get("COMSPEC", "cmd.exe")]


HERANCA_SUJA = (
    "CLAUDE",
    "ANTHROPIC_",
    "QTWEBENGINE_",
    "QT_",
    "CODEX_HOME",
    "GEMINI_CONFIG_DIR",
)


CORES_LIGADAS = {
    # sem isto o CLI decide sozinho se pode usar cor e as vezes decide errado dentro do ConPTY,
    # e o Claude aparece todo em preto e branco.
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
    "FORCE_COLOR": "3",
}


def ambiente_limpo() -> dict[str, str]:
    """O terminal nao pode herdar o ambiente de quem abriu o app.

    Se o Loom for iniciado de dentro de uma sessao do Claude Code, o shell filho
    herda marcadores como CLAUDE_CODE_CHILD_SESSION e o Claude que roda ali dentro
    se acha um subprocesso, para de gravar transcript e some do medidor de uso.
    Tambem tiramos as variaveis do Qt, que sao nossas e nao dizem respeito ao shell.
    """
    return {
        chave: valor
        for chave, valor in os.environ.items()
        if not any(chave.startswith(prefixo) for prefixo in HERANCA_SUJA)
    }


# o console que o Windows cria pra um programa de console nao e trabalho de ninguem
FILHOS_DE_SERVICO = {"conhost.exe", "openconsole.exe"}


def _filhos_de(pid: int, processos: list[tuple[int, int, str]]) -> list[str]:
    """Os programas que aquele processo abriu, a partir de (pid, pai, nome) de cada um."""
    return [
        nome
        for filho, pai, nome in processos
        if pai == pid and filho != pid and nome.lower() not in FILHOS_DE_SERVICO
    ]


def _processos() -> list[tuple[int, int, str]] | None:
    """Foto de todos os processos da maquina, so leitura. None quando o Windows nao entrega."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Entrada(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entrada)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entrada)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    foto = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not foto or foto == wintypes.HANDLE(-1).value:
        return None
    achados = []
    try:
        entrada = Entrada()
        entrada.dwSize = ctypes.sizeof(Entrada)
        tem = kernel.Process32FirstW(foto, ctypes.byref(entrada))
        while tem:
            achados.append((entrada.th32ProcessID, entrada.th32ParentProcessID, entrada.szExeFile))
            tem = kernel.Process32NextW(foto, ctypes.byref(entrada))
    finally:
        kernel.CloseHandle(foto)
    return achados


def tem_filhos(pid: int | None) -> bool | None:
    """Se o shell daquela aba esta rodando algum programa. None quando nao da pra saber."""
    if not pid:
        return None
    processos = _processos()
    if processos is None:
        return None
    return bool(_filhos_de(pid, processos))


class PtySession(QObject):
    """Um shell rodando num ConPTY. Le em thread propria e entrega por sinal."""

    output = Signal(str, str)
    exited = Signal(str, int)

    def __init__(self, session_id: str, argv=None, cwd=None, env=None, cols=120, rows=30, parent=None):
        super().__init__(parent)
        self.session_id = session_id
        self._argv = argv or default_shell()
        self._cwd = cwd or os.path.expanduser("~")
        self._env = {**ambiente_limpo(), **CORES_LIGADAS, **(env or {})}
        self._proc: PtyProcess | None = None
        self._reader: threading.Thread | None = None
        self._stopping = False
        self._cols, self._rows = cols, rows

    def start(self) -> None:
        self._proc = PtyProcess.spawn(
            self._argv, cwd=self._cwd, env=self._env, dimensions=(self._rows, self._cols)
        )
        # o processo vai como argumento e nao lido de self._proc: o stop() solta a referencia
        # da sessao enquanto esta thread ainda le, as vezes antes de ela dar o primeiro passo
        self._reader = threading.Thread(
            target=self._pump, args=(self._proc,), name=f"pty-{self.session_id}", daemon=True
        )
        self._reader.start()

    def _pump(self, proc: PtyProcess) -> None:
        code = 0
        try:
            while True:
                # o padrao do winpty e 1 kB, entao um despejo grande virava centenas de sinais
                # enfileirados por segundo na thread da tela, cada um com o seu custo de travessia
                data = proc.read(65536)
                if not data:
                    break
                if self._stopping:
                    break
                self.output.emit(self.session_id, data)
        except EOFError:
            pass
        except OSError:
            pass
        finally:
            if proc is not None:
                code = proc.exitstatus if proc.exitstatus is not None else 0
            if not self._stopping:
                self.exited.emit(self.session_id, code)

    def write(self, data: str) -> None:
        if self._proc is not None and self._proc.isalive():
            self._proc.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self._cols, self._rows = max(cols, 1), max(rows, 1)
        if self._proc is not None and self._proc.isalive():
            self._proc.setwinsize(self._rows, self._cols)

    @property
    def tamanho(self) -> tuple[int, int]:
        """Colunas e linhas que o shell usa agora."""
        return self._cols, self._rows

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    @property
    def pid(self) -> int | None:
        proc = self._proc
        return getattr(proc, "pid", None) if proc is not None else None

    def stop(self) -> None:
        """Derruba o shell e solta o pseudo console.

        So o terminate matava o shell e deixava o resto de pe: a sessao continua filha da
        Bridge ate o app fechar, e com ela o PtyProcess e o ConPTY. O Windows nao derruba os
        filhos junto com o pai, entao o Claude que rodava dentro ficava vivo e sem tela. Quem
        derruba os filhos e o ConPTY ao fechar, e ele so fecha quando ninguem mais segura o PTY.
        """
        self._stopping = True
        proc, self._proc = self._proc, None
        if proc is None:
            return
        # close fecha os sockets de leitura antes de matar, e isso solta as duas threads de
        # leitura; a espera dele e so pra quem nao chama terminate logo em seguida
        proc.delayafterclose = 0
        try:
            proc.close(force=True)
        except OSError:
            # o processo pode morrer entre o isalive e o kill, e ai o Windows nega o acesso.
            # Deixar escapar aqui interrompia o shutdown no meio, com as outras abas vivas.
            pass
