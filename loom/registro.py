"""Log do Loom em ~/.loom/loom.log.

No exe sem console os print nao iam a lugar nenhum, e a tela caindo nao deixava rastro: nem o
erro do Python, nem o aviso do Qt, nem o console da pagina. Aqui tudo vai pro mesmo arquivo, que
gira em 1 MB e guarda tres anteriores.

O faulthandler escreve no mesmo arquivo, direto no descritor dele, sem passar pelo Python. E ele
que deixa as pilhas das threads quando o processo morre numa falha nativa, como a do ANGLE, em
que nenhum except chega a rodar.

configurar roda antes do Qt subir, pra anotar a escolha do motor; o gancho das mensagens do Qt
fica em ligar_qt, chamado depois.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import caminhos as mod_caminhos

TAMANHO = 1024 * 1024
COPIAS = 3
# outro Loom segurando o arquivo: nao adianta tentar girar a cada linha
ADIAMENTO = 300.0
# o numero do processo separa as linhas do Loom instalado, do que roda do codigo e da segunda
# instancia que so chama a primeira, que escrevem todos no mesmo arquivo
FORMATO = "%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"

# o que foi trocado ao ligar, pra desligar devolver como era
_ligado: dict = {}


class ArquivoQueGira(RotatingFileHandler):
    """O arquivo que gira no tamanho, levando o faulthandler junto e aguentando o Windows."""

    def __init__(self, arquivo: Path) -> None:
        super().__init__(arquivo, maxBytes=TAMANHO, backupCount=COPIAS, encoding="utf-8")
        self.com_faulthandler = False
        self._adiado_ate = 0.0

    def shouldRollover(self, record) -> bool:
        if time.monotonic() < self._adiado_ate:
            return False
        return super().shouldRollover(record)

    def doRollover(self) -> None:
        # O faulthandler escreve no descritor deste arquivo. Solto antes de fechar e religado no
        # novo: sem isso, uma falha depois da troca ia pra um descritor ja fechado, ou pior, pro
        # mesmo numero reaproveitado por outro arquivo qualquer.
        if self.com_faulthandler:
            faulthandler.disable()
        try:
            if self.stream:
                self.stream.close()
                self.stream = None
            self._girar()
            self.stream = self._open()
        finally:
            if self.com_faulthandler and self.stream is not None:
                faulthandler.enable(self.stream, all_threads=True)

    def _girar(self) -> None:
        base = self.baseFilename
        girando = base + ".girando"
        try:
            os.replace(base, girando)
        except OSError:
            # O Loom instalado e o que roda do codigo escrevem no mesmo log, e o Windows nao
            # renomeia arquivo aberto por outro processo. Segue escrevendo no mesmo e tenta mais
            # tarde, sem mexer nas copias: girar pela metade apagava uma copia por tentativa, e a
            # linha que pediu a troca se perdia.
            self._adiado_ate = time.monotonic() + ADIAMENTO
            return
        try:
            for i in range(self.backupCount - 1, 0, -1):
                velho = f"{base}.{i}"
                if os.path.exists(velho):
                    os.replace(velho, f"{base}.{i + 1}")
            os.replace(girando, f"{base}.1")
        except OSError:
            self._adiado_ate = time.monotonic() + ADIAMENTO


def arquivo() -> Path:
    return mod_caminhos.RAIZ / "loom.log"


def _excecao_solta(tipo, valor, rastro) -> None:
    if not issubclass(tipo, KeyboardInterrupt):
        logging.getLogger("loom").critical("erro sem tratamento", exc_info=(tipo, valor, rastro))
    anterior = _ligado.get("sys")
    # o gancho padrao so repetiria no console o que o log ja mostrou la
    if anterior is not None and anterior is not sys.__excepthook__:
        anterior(tipo, valor, rastro)


def _excecao_na_thread(args) -> None:
    if not issubclass(args.exc_type, SystemExit):
        nome = args.thread.name if args.thread is not None else "?"
        logging.getLogger("loom").error(
            "erro sem tratamento na thread %s", nome, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )
    anterior = _ligado.get("threading")
    if anterior is not None and anterior is not threading.__excepthook__:
        anterior(args)


def configurar(destino: Path | None = None) -> logging.Logger:
    """Liga o log em arquivo e os ganchos que mandam pra ele o que antes sumia. Uma vez so."""
    log = logging.getLogger("loom")
    if _ligado.get("manipuladores"):
        return log
    destino = destino or arquivo()
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        manipulador = ArquivoQueGira(destino)
    except OSError:
        # sem log o Loom sobe do mesmo jeito; o que nao pode e deixar de abrir por causa dele
        return log
    manipulador.setFormatter(logging.Formatter(FORMATO))
    log.setLevel(logging.INFO)
    log.addHandler(manipulador)
    log.propagate = False
    manipuladores = [manipulador]
    if sys.stderr is not None:
        # rodando do codigo, num console: o que antes saia no print continua saindo ali
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(FORMATO))
        log.addHandler(console)
        manipuladores.append(console)

    _ligado.update(
        {
            "manipuladores": manipuladores,
            "faulthandler": faulthandler.is_enabled(),
            "sys": sys.excepthook,
            "threading": threading.excepthook,
        }
    )
    faulthandler.enable(manipulador.stream, all_threads=True)
    manipulador.com_faulthandler = True
    sys.excepthook = _excecao_solta
    threading.excepthook = _excecao_na_thread
    return log


def ligar_qt() -> None:
    """Manda os avisos do Qt pro log. Com a aplicacao ja criada, porque importa o Qt."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    niveis = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    do_qt = logging.getLogger("loom.qt")

    def mensagem(tipo, _contexto, texto) -> None:
        do_qt.log(niveis.get(tipo, logging.INFO), "%s", texto)

    # a funcao fica guardada: solta, o Python a recolheria com o Qt ainda chamando
    _ligado["qt_gancho"] = mensagem
    _ligado["qt_anterior"] = qInstallMessageHandler(mensagem)


def desligar() -> None:
    """Devolve tudo como estava antes de configurar. Serve aos testes e a quem configurar de novo."""
    if "qt_gancho" in _ligado:
        from PySide6.QtCore import qInstallMessageHandler

        qInstallMessageHandler(_ligado.get("qt_anterior"))
    if not _ligado.get("manipuladores"):
        _ligado.clear()
        return
    faulthandler.disable()
    if _ligado["faulthandler"] and sys.stderr is not None:
        faulthandler.enable()
    sys.excepthook = _ligado["sys"]
    threading.excepthook = _ligado["threading"]
    log = logging.getLogger("loom")
    for manipulador in _ligado["manipuladores"]:
        log.removeHandler(manipulador)
        manipulador.close()
    log.propagate = True
    log.setLevel(logging.NOTSET)
    _ligado.clear()
