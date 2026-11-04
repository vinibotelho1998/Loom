"""Log em arquivo: o que antes sumia no exe sem console agora fica em ~/.loom/loom.log.

Cada teste liga o log numa pasta temporaria e desliga no fim, devolvendo os ganchos do processo
como estavam. O console fica de fora, pra saida da bateria nao encher de linhas de log.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import shutil
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import base  # noqa: E402,F401
from base import so_no_windows  # noqa: E402

from loom import registro  # noqa: E402


class ComLog(unittest.TestCase):
    def setUp(self) -> None:
        self.pasta = Path(tempfile.mkdtemp(prefix="loom-log-"))
        self.arquivo = self.pasta / "loom.log"
        self.antes = (sys.excepthook, threading.excepthook, faulthandler.is_enabled())
        # o desligar tem que rodar antes de apagar a pasta, senao o arquivo ainda esta aberto
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.addCleanup(registro.desligar)

    def ligar(self) -> logging.Logger:
        with unittest.mock.patch.object(registro.sys, "stderr", None):
            return registro.configurar(self.arquivo)

    def manipulador(self) -> registro.ArquivoQueGira:
        return next(m for m in logging.getLogger("loom").handlers if isinstance(m, registro.ArquivoQueGira))

    def texto(self, arquivo: Path | None = None) -> str:
        return (arquivo or self.arquivo).read_text(encoding="utf-8")


class ArquivoDeLog(ComLog):
    def test_linha_vai_pro_arquivo_com_nivel_e_quem_escreveu(self):
        self.ligar()
        logging.getLogger("loom.app").warning("refazendo a tela sozinho: %s", "a pagina parou")
        self.assertRegex(self.texto(), r"WARNING loom\.app: refazendo a tela sozinho: a pagina parou\n$")

    def test_gira_em_um_mega_e_guarda_tres(self):
        self.ligar()
        manipulador = self.manipulador()
        self.assertEqual((manipulador.maxBytes, manipulador.backupCount), (1024 * 1024, 3))
        manipulador.maxBytes = 2000
        log = logging.getLogger("loom")
        for i in range(40):
            log.info("linha %03d %s", i, "x" * 400)
        copias = sorted(p.name for p in self.pasta.iterdir())
        self.assertEqual(copias, ["loom.log", "loom.log.1", "loom.log.2", "loom.log.3"])
        self.assertIn("linha 039", self.texto())

    def test_faulthandler_segue_pro_arquivo_novo_depois_de_girar(self):
        """Sem religar, uma falha nativa depois da troca ia pra um descritor ja fechado."""
        self.ligar()
        manipulador = self.manipulador()
        antigo = manipulador.stream
        with unittest.mock.patch.object(registro.faulthandler, "enable", wraps=faulthandler.enable) as ligou:
            manipulador.doRollover()
        self.assertTrue(antigo.closed)
        self.assertIs(ligou.call_args.args[0], manipulador.stream)
        self.assertFalse(manipulador.stream.closed)
        self.assertTrue(faulthandler.is_enabled())

    @so_no_windows
    def test_arquivo_preso_por_outro_loom_nao_perde_linha_nem_copia(self):
        """O Loom instalado e o do codigo escrevem no mesmo log, e o Windows nao renomeia o
        arquivo que o outro segura aberto. A linha que pedia a troca se perdia."""
        self.ligar()
        (self.pasta / "loom.log.1").write_text("de antes\n", encoding="utf-8")
        manipulador = self.manipulador()
        manipulador.maxBytes = 200
        log = logging.getLogger("loom")
        log.info("primeira %s", "x" * 250)
        outro = open(self.arquivo, encoding="utf-8")
        try:
            log.info("segunda com o outro segurando")
            log.info("terceira ainda adiada")
        finally:
            outro.close()
        texto = self.texto()
        self.assertIn("segunda com o outro segurando", texto)
        self.assertIn("terceira ainda adiada", texto)
        self.assertEqual(self.texto(self.pasta / "loom.log.1"), "de antes\n")
        self.assertFalse((self.pasta / "loom.log.girando").exists())

        # passado o adiamento, com o arquivo solto, gira como sempre
        with unittest.mock.patch.object(registro.time, "monotonic", return_value=manipulador._adiado_ate + 1):
            log.info("quarta depois do adiamento")
        self.assertIn("terceira ainda adiada", self.texto(self.pasta / "loom.log.1"))
        self.assertEqual(self.texto(self.pasta / "loom.log.2"), "de antes\n")
        self.assertIn("quarta depois do adiamento", self.texto())

    def test_sem_pasta_pro_log_o_loom_sobe_mesmo_assim(self):
        (self.pasta / "arquivo").write_text("", encoding="utf-8")
        with unittest.mock.patch.object(registro.sys, "stderr", None):
            log = registro.configurar(self.pasta / "arquivo" / "loom.log")
        self.assertEqual(log.name, "loom")
        self.assertEqual([m for m in log.handlers if isinstance(m, registro.ArquivoQueGira)], [])
        self.assertIs(sys.excepthook, self.antes[0])


class ErrosQueSumiam(ComLog):
    def test_erro_sem_tratamento_vai_pro_log(self):
        self.ligar()
        try:
            raise ValueError("quebrou no slot")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        texto = self.texto()
        self.assertIn("CRITICAL loom: erro sem tratamento", texto)
        self.assertIn("ValueError: quebrou no slot", texto)

    def test_erro_numa_thread_vai_pro_log(self):
        self.ligar()

        def quebra() -> None:
            raise RuntimeError("quebrou na thread")

        fio = threading.Thread(target=quebra, name="identidade")
        fio.start()
        fio.join(5)
        texto = self.texto()
        self.assertIn("erro sem tratamento na thread identidade", texto)
        self.assertIn("RuntimeError: quebrou na thread", texto)

    def test_avisos_do_qt_vao_pro_log(self):
        from test_tela import garantir_app
        from PySide6.QtCore import qWarning

        garantir_app()
        self.ligar()
        registro.ligar_qt()
        qWarning("aviso do Qt de teste")
        self.assertIn("WARNING loom.qt: aviso do Qt de teste", self.texto())

    def test_console_da_pagina_vai_pro_log_com_o_nivel(self):
        from PySide6.QtWebEngineCore import QWebEnginePage

        from loom import app as mod_app

        self.ligar()
        niveis = QWebEnginePage.JavaScriptConsoleMessageLevel
        mod_app.LoggingPage.javaScriptConsoleMessage(None, niveis.ErrorMessageLevel, "boom", 12, "file:///C:/x/ui/app.js")
        mod_app.LoggingPage.javaScriptConsoleMessage(
            None, niveis.InfoMessageLevel, "webglcontextlost event received", 3, "file:///C:/x/ui/vendor/addon-webgl.js"
        )
        texto = self.texto()
        self.assertIn("ERROR loom.tela: app.js:12 boom", texto)
        self.assertIn("INFO loom.tela: addon-webgl.js:3 webglcontextlost event received", texto)

    def test_desligar_devolve_os_ganchos_como_eram(self):
        self.ligar()
        self.assertTrue(faulthandler.is_enabled())
        self.assertIsNot(sys.excepthook, self.antes[0])
        registro.desligar()
        self.assertEqual((sys.excepthook, threading.excepthook, faulthandler.is_enabled()), self.antes)
        self.assertEqual(
            [m for m in logging.getLogger("loom").handlers if isinstance(m, registro.ArquivoQueGira)], []
        )

    def test_ligar_de_novo_nao_duplica_as_linhas(self):
        self.ligar()
        self.ligar()
        logging.getLogger("loom").info("uma vez so")
        self.assertEqual(self.texto().count("uma vez so"), 1)


if __name__ == "__main__":
    unittest.main()
