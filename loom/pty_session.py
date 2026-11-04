from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

if sys.platform == "win32":
    from winpty import PtyProcess, WinptyError
else:
    # o mesmo jeito de usar, com o PTY do kernel e sem dependencia nova
    from .pty_posix import ErroDoPty as WinptyError
    from .pty_posix import PtyProcess

log = logging.getLogger(__name__)

# Depois que a leitura para, o intervalo entre uma olhada e outra no processo. Comeca curto,
# porque no fim normal ele morre em milissegundos, e cresce ate o teto quando ele segue vivo.
ESPERA_DO_FIM = 0.05
ESPERA_MAXIMA_DO_FIM = 1.0
# no fim normal o processo some em milissegundos; vivo depois disso, foi a leitura que quebrou
PRAZO_DO_FIM = 2.0
# o shell saiu, mas o Windows nao disse com que codigo
CODIGO_DESCONHECIDO = -1


def default_shell() -> list[str]:
    """pwsh se existir, senao o powershell de sempre, e cmd como ultimo recurso.

    Fora do Windows, o shell da pessoa, o mesmo que um terminal qualquer abriria.
    """
    if sys.platform != "win32":
        return [os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"]
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
    # aberto de dentro de outro terminal, o CLI acharia que roda nele e pediria o que so ele tem
    "TERM_PROGRAM",
    "TERM_SESSION_ID",
    "GHOSTTY_",
    "KONSOLE_",
    "KITTY_",
    "WEZTERM_",
    "ALACRITTY_",
    "VTE_",
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
    limpo = {
        chave: valor
        for chave, valor in os.environ.items()
        if not any(chave.startswith(prefixo) for prefixo in HERANCA_SUJA)
    }
    if getattr(sys, "frozen", False):
        for chave in limpo:
            if chave.upper() == "PATH":
                limpo[chave] = path_sem_a_pasta(limpo[chave], Path(sys.executable).parent)
        _sem_as_bibliotecas_do_app(limpo)
    return limpo


def _sem_as_bibliotecas_do_app(ambiente: dict[str, str]) -> None:
    """Devolve o LD_LIBRARY_PATH de antes do exe congelado subir.

    O PyInstaller aponta a variavel pra propria _internal, e o que vale pra ele valia pra todo
    programa rodado na aba: o Qt do Loom vinha antes do do sistema, e o Dolphin nao abria.
    """
    original = ambiente.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        ambiente["LD_LIBRARY_PATH"] = original
    else:
        ambiente.pop("LD_LIBRARY_PATH", None)


def _normal(caminho: str) -> str:
    return os.path.normcase(os.path.normpath(caminho.strip().strip('"')))


def path_sem_a_pasta(path: str, pasta: Path) -> str:
    """O PATH sem as entradas que moram dentro daquela pasta.

    O exe congelado poe a propria _internal e a do PySide6 na frente do PATH, e todo shell da aba
    herdava isso: as DLLs do Qt do Loom vinham antes das do sistema pra qualquer programa rodado
    ali, e um python ou um app Qt do usuario carregava a versao errada.
    """
    base = _normal(str(pasta))
    ficam = []
    for entrada in path.split(os.pathsep):
        if entrada.strip():
            alvo = _normal(entrada)
            if alvo == base or alvo.startswith(base.rstrip(os.sep) + os.sep):
                continue
        ficam.append(entrada)
    return os.pathsep.join(ficam)


# o console que o Windows cria pra um programa de console nao e trabalho de ninguem
FILHOS_DE_SERVICO = {"conhost.exe", "openconsole.exe"}


def _filhos_de(pid: int, processos: list[tuple[int, int, str]]) -> list[str]:
    """Os programas que aquele processo abriu, a partir de (pid, pai, nome) de cada um."""
    return [
        nome
        for filho, pai, nome in processos
        if pai == pid and filho != pid and nome.lower() not in FILHOS_DE_SERVICO
    ]


def _linha_do_proc(texto: str) -> tuple[int, int, str] | None:
    """(pid, pai, nome) de uma linha de /proc/<pid>/stat. None pro processo ja morto.

    O nome vem entre parenteses e pode ter espaco e parentese dentro: o que vale e o ultimo. Um
    zumbi ja acabou e so espera o pai ler o codigo, e nao conta como programa rodando.
    """
    fim = texto.rfind(")")
    inicio = texto.find("(")
    if inicio < 0 or fim < inicio:
        return None
    campos = texto[fim + 1 :].split()
    if len(campos) < 2 or campos[0] in ("Z", "X"):
        return None
    try:
        return int(texto[:inicio]), int(campos[1]), texto[inicio + 1 : fim]
    except ValueError:
        return None


def _processos_do_proc(raiz: str = "/proc") -> list[tuple[int, int, str]] | None:
    try:
        nomes = os.listdir(raiz)
    except OSError:
        return None
    achados = []
    for nome in nomes:
        if not nome.isdigit():
            continue
        try:
            with open(os.path.join(raiz, nome, "stat"), encoding="utf-8", errors="replace") as arquivo:
                linha = _linha_do_proc(arquivo.read())
        except OSError:
            # o processo morreu entre a lista e a leitura
            continue
        if linha:
            achados.append(linha)
    return achados


def _processos() -> list[tuple[int, int, str]] | None:
    """Foto de todos os processos da maquina, so leitura. None quando o sistema nao entrega."""
    if sys.platform != "win32":
        return _processos_do_proc()
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


def _vivo(proc) -> bool:
    try:
        return bool(proc.isalive())
    except (OSError, WinptyError):
        # sem conseguir perguntar, nao ha mais o que vigiar; esperar aqui seria pra sempre
        return False


def _codigo_de_saida(proc) -> int:
    """O codigo com que o shell saiu.

    O exitstatus pergunta ao winpty, e o erro dele saia de dentro do finally da leitura: o aviso
    de fim nunca era dado e a aba ficava com o shell morto, esperando tecla.
    """
    try:
        codigo = proc.exitstatus
    except (OSError, WinptyError):
        return CODIGO_DESCONHECIDO
    return codigo if isinstance(codigo, int) else 0


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
        try:
            self._proc = PtyProcess.spawn(
                self._argv, cwd=self._cwd, env=self._env, dimensions=(self._rows, self._cols)
            )
        except WinptyError as erro:
            # o erro nativo do winpty nao e OSError; quem abre a aba so precisa saber que nao subiu
            raise OSError(str(erro)) from erro
        # o processo vai como argumento e nao lido de self._proc: o stop() solta a referencia
        # da sessao enquanto esta thread ainda le, as vezes antes de ela dar o primeiro passo
        self._reader = threading.Thread(
            target=self._pump, args=(self._proc,), name=f"pty-{self.session_id}", daemon=True
        )
        self._reader.start()

    def _pump(self, proc: PtyProcess) -> None:
        try:
            while True:
                # o padrao do winpty e 1 kB, entao um despejo grande virava centenas de sinais
                # enfileirados por segundo na thread da tela, cada um com o seu custo de travessia
                data = proc.read(65536)
                if not data:
                    # Vazio nao e fim. O winpty manda o marcador 0011Ignore quando a leitura dele
                    # volta sem nada, e o read entrega isso como texto vazio; o fim de verdade chega
                    # como EOFError. Parar aqui avisava o fim com o shell vivo, e a ponte matava o
                    # shell com o Claude dentro.
                    continue
                if self._stopping:
                    break
                self._avisar(self.output, self.session_id, data)
        except EOFError:
            pass
        except OSError:
            pass
        finally:
            self._fim(proc)

    def _fim(self, proc: PtyProcess) -> None:
        """Avisa que o shell acabou, mas so quando ele acabou de fato.

        A leitura para um instante antes ou depois de o processo morrer, e nem sempre quer dizer
        que ele morreu: um erro no soquete do winpty tambem para. Avisar o fim com ele vivo fazia
        a ponte matar o shell. Daqui em diante a thread so vigia o processo, ate ele sair ou a aba
        fechar.
        """
        espera, desde, anotado = ESPERA_DO_FIM, time.monotonic(), False
        while not self._stopping and _vivo(proc):
            if not anotado and time.monotonic() - desde >= PRAZO_DO_FIM:
                log.warning("a leitura do shell da aba %s parou com ele vivo; a aba espera ele sair", self.session_id)
                anotado = True
            time.sleep(espera)
            espera = min(espera * 2, ESPERA_MAXIMA_DO_FIM)
        if not self._stopping:
            self._avisar(self.exited, self.session_id, _codigo_de_saida(proc))

    def _avisar(self, sinal, *args) -> None:
        try:
            sinal.emit(*args)
        except RuntimeError:
            # a ponte ja apagou a sessao depois do stop; o que sobrou da leitura nao tem mais pra
            # quem ir, e o erro so sujaria o log
            pass

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
        # O terminate do winpty dorme 0,1 s depois de cada tentativa de matar, e sao duas: com a
        # tela parada, e no fechar do app somando aba por aba. O Windows mata do mesmo jeito sem a
        # espera; se o processo ainda nao sumiu na conferencia, o close reclama com um erro que ja
        # e ignorado logo abaixo.
        proc.delayafterterminate = 0
        try:
            proc.close(force=True)
        except OSError:
            # o processo pode morrer entre o isalive e o kill, e ai o Windows nega o acesso.
            # Deixar escapar aqui interrompia o shutdown no meio, com as outras abas vivas.
            pass
