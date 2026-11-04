"""O terminal no Linux, com o mesmo jeito de usar do PtyProcess do pywinpty.

So com o que vem no Python, sem dependencia nova. O resto do Loom nao sabe em que sistema esta:
le, escreve, redimensiona e fecha pelos mesmos nomes.

Tres coisas aqui nao tem paralelo no Windows e mudam o desenho:

- Fechar um descritor nao acorda quem esta lendo ele em outra thread, e o kernel so entrega o
  hangup quando o ultimo leitor solta. Por isso a leitura espera por poll, junto com um pipe de
  acordar, e o mestre fica sem bloqueio.
- O shell tem que ser dono do terminal, com sessao propria. E isso que faz o Ctrl+C chegar, e faz
  o kernel desligar o terminal quando ele sai, mesmo com um programa em segundo plano segurando
  o escravo: sem isso, a aba ficaria aberta com o shell morto.
- A escrita pode bloquear quando o programa em modo raw, como o Claude, nao le a entrada e o
  buffer do kernel enche. Ela roda numa thread propria: colar um texto grande num programa
  travado nao pode parar a tela.
"""

from __future__ import annotations

import codecs
import errno
import fcntl
import logging
import os
import pty
import queue
import select
import signal
import struct
import subprocess
import termios
import threading
import time

log = logging.getLogger(__name__)

# quem nao saiu com o hangup depois disso leva SIGKILL; um programa que trata o sinal ganha tempo
# de gravar o que precisa, como o Claude salvando a conversa
PRAZO_DO_HANGUP = 3.0
# fora da faixa de 0 a 255: o shell morto por sinal aparece como 128 mais o sinal, como no bash
BASE_DO_SINAL = 128


class ErroDoPty(OSError):
    """O shell nao subiu ou o PTY nao respondeu."""


def _ligar_terminal() -> None:
    # roda no filho, entre o fork e o exec: depois do setsid, o escravo vira o terminal de controle.
    # Sem isto nao ha Ctrl+C, job control nem SIGHUP ao fechar a aba
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _medidas(linhas: int, colunas: int) -> bytes:
    return struct.pack("HHHH", max(linhas, 1), max(colunas, 1), 0, 0)


class PtyProcess:
    """Um processo rodando num pseudo terminal do kernel."""

    def __init__(self, processo: subprocess.Popen, mestre: int) -> None:
        self._processo = processo
        self.pid = processo.pid
        self._mestre = mestre
        self._trava = threading.Lock()
        self._fechado = False
        # um caractere de varios bytes pode chegar partido entre duas leituras
        self._decodificador = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._acordar_leitura, self._acordar_escrita = os.pipe()
        self._sondagem = select.poll()
        self._sondagem.register(mestre, select.POLLIN | select.POLLHUP)
        self._sondagem.register(self._acordar_leitura, select.POLLIN)
        self._fila: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        threading.Thread(target=self._escrever_da_fila, name=f"pty-esc-{self.pid}", daemon=True).start()

    @classmethod
    def spawn(cls, argv, cwd=None, env=None, dimensions=(24, 80)) -> "PtyProcess":
        linhas, colunas = dimensions
        mestre, escravo = pty.openpty()
        try:
            atributos = termios.tcgetattr(escravo)
            # sem isto o backspace apaga um byte de cada vez, e acento vira lixo na linha digitada
            atributos[0] |= termios.IUTF8
            termios.tcsetattr(escravo, termios.TCSANOW, atributos)
            fcntl.ioctl(escravo, termios.TIOCSWINSZ, _medidas(linhas, colunas))
            processo = subprocess.Popen(
                [str(a) for a in argv],
                cwd=cwd,
                env=env,
                stdin=escravo,
                stdout=escravo,
                stderr=escravo,
                start_new_session=True,
                preexec_fn=_ligar_terminal,
                close_fds=True,
            )
        except BaseException:
            os.close(mestre)
            raise
        finally:
            # fica so com o filho: enquanto o pai segurar o escravo, o mestre nunca da fim de arquivo
            os.close(escravo)
        os.set_blocking(mestre, False)
        return cls(processo, mestre)

    def _acordar(self) -> None:
        try:
            os.write(self._acordar_escrita, b"x")
        except OSError:
            pass

    def read(self, tamanho: int = 1024) -> str:
        """O proximo pedaco da saida. EOFError quando acabou; vazio nao e fim."""
        while True:
            with self._trava:
                if self._fechado:
                    raise EOFError("o terminal foi fechado")
            eventos = dict(self._sondagem.poll())
            if not eventos:
                continue
            with self._trava:
                if self._fechado:
                    raise EOFError("o terminal foi fechado")
                try:
                    pedaco = os.read(self._mestre, tamanho)
                except BlockingIOError:
                    continue
                except OSError as erro:
                    # no Linux, o fim do lado escravo chega como EIO
                    if erro.errno == errno.EIO:
                        raise EOFError("o shell saiu") from None
                    raise
            if not pedaco:
                raise EOFError("o shell saiu")
            return self._decodificador.decode(pedaco)

    def _escrever_da_fila(self) -> None:
        while True:
            dados = self._fila.get()
            if dados is None:
                return
            if not self._escrever_tudo(dados):
                return

    def _escrever_tudo(self, dados: bytes) -> bool:
        vista = memoryview(dados)
        sondagem = select.poll()
        with self._trava:
            if self._fechado:
                return False
            sondagem.register(self._mestre, select.POLLOUT)
        while vista:
            # com prazo: o programa pode nunca ler a entrada, e esta thread tem que perceber o fechar
            if not sondagem.poll(500):
                with self._trava:
                    if self._fechado:
                        return False
                continue
            with self._trava:
                if self._fechado:
                    return False
                try:
                    escrito = os.write(self._mestre, vista[:4096])
                except BlockingIOError:
                    continue
                except OSError:
                    return False
            vista = vista[escrito:]
        return True

    def write(self, dados: str) -> None:
        self._fila.put(dados.encode("utf-8", "surrogatepass"))

    def setwinsize(self, linhas: int, colunas: int) -> None:
        with self._trava:
            if self._fechado:
                return
            # o kernel manda SIGWINCH pro programa da frente, e e assim que o Claude se redesenha
            fcntl.ioctl(self._mestre, termios.TIOCSWINSZ, _medidas(linhas, colunas))

    def isalive(self) -> bool:
        return self._processo.poll() is None

    @property
    def exitstatus(self) -> int | None:
        codigo = self._processo.poll()
        if codigo is None:
            return None
        return BASE_DO_SINAL - codigo if codigo < 0 else codigo

    def close(self, force: bool = True) -> None:
        """Solta o terminal: o kernel entrega o hangup pro shell e pro programa da frente."""
        with self._trava:
            if self._fechado:
                return
            self._fechado = True
            mestre, self._mestre = self._mestre, -1
            try:
                # o programa da frente mora em outro grupo que o do shell: o Claude, por exemplo
                grupos = {self.pid, os.tcgetpgrp(mestre)}
            except OSError:
                grupos = {self.pid}
            os.close(mestre)
        self._fila.put(None)
        self._acordar()
        if force and (self.isalive() or self._grupo_vivo(self.pid)):
            threading.Thread(target=self._derrubar, args=(grupos,), name=f"pty-fecha-{self.pid}", daemon=True).start()

    def _derrubar(self, grupos: set[int]) -> None:
        self._sinalizar(grupos, signal.SIGHUP)
        # O prazo vale pro grupo e nao so pro shell: ele sai na hora, e matar logo em seguida tirava
        # do Claude o tempo de gravar a conversa
        fim = time.monotonic() + PRAZO_DO_HANGUP
        while time.monotonic() < fim:
            # o poll colhe o shell morto: zumbi ainda conta como vivo no grupo dele
            self._processo.poll()
            if not any(self._grupo_vivo(g) for g in grupos):
                return
            time.sleep(0.05)
        log.warning("o shell %s deixou programa de pe depois do hangup; matando", self.pid)
        self._sinalizar(grupos, signal.SIGKILL)
        self._processo.poll()

    @staticmethod
    def _alvo_valido(grupo: int) -> bool:
        # O terminal sem programa na frente responde grupo 0, e killpg de 0 e o grupo de quem chama:
        # fechar a aba mandaria o sinal pro proprio Loom. O 1 e o init, e o nosso nunca e alvo
        return grupo > 1 and grupo != os.getpgrp()

    @classmethod
    def _grupo_vivo(cls, grupo: int) -> bool:
        if not cls._alvo_valido(grupo):
            return False
        try:
            os.killpg(grupo, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @classmethod
    def _sinalizar(cls, grupos: set[int], sinal: int) -> None:
        for grupo in grupos:
            if not cls._alvo_valido(grupo):
                continue
            try:
                os.killpg(grupo, sinal)
            except OSError:
                pass

    def __del__(self) -> None:
        # as threads seguram este objeto enquanto usam os descritores: aqui ninguem mais usa
        for descritor in (getattr(self, "_acordar_leitura", -1), getattr(self, "_acordar_escrita", -1)):
            try:
                if descritor >= 0:
                    os.close(descritor)
            except OSError:
                pass
