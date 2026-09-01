"""O terminal de verdade: um shell sobe, responde e obedece ao tamanho.

Estes sao os unicos testes que abrem processo. Se algum falhar, o app nao serve pra nada,
porque tudo o mais e enfeite em volta disto.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from dbx_term import shells  # noqa: E402
from dbx_term.pty_session import CORES_LIGADAS, PtySession, ambiente_limpo  # noqa: E402

LIMPAR = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")


class Ambiente(unittest.TestCase):
    def test_marcas_do_claude_nao_passam_pro_shell(self):
        """Herdar CLAUDE_CODE_CHILD_SESSION faz o Claude de dentro parar de gravar transcript."""
        with unittest.mock.patch.dict(
            os.environ,
            {"CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1", "ANTHROPIC_API_KEY": "x", "PATH": os.environ["PATH"]},
        ):
            limpo = ambiente_limpo()
        self.assertEqual([c for c in limpo if c.startswith(("CLAUDE", "ANTHROPIC_"))], [])
        self.assertIn("PATH", limpo)

    def test_cor_e_cravada_no_ambiente(self):
        """Dentro do ConPTY o CLI as vezes decide que nao pode usar cor."""
        self.assertEqual(CORES_LIGADAS["TERM"], "xterm-256color")
        self.assertEqual(CORES_LIGADAS["FORCE_COLOR"], "3")


class Shells(unittest.TestCase):
    def test_acha_pelo_menos_um_shell(self):
        achados = shells.descobrir()
        self.assertTrue(achados, "sem shell nenhum o app nao abre aba")
        for s in achados:
            self.assertTrue(Path(s.caminho).exists(), f"{s.nome} aponta pra caminho que nao existe")

    def test_escolhe_um_padrao(self):
        self.assertIn(shells.padrao(shells.descobrir()).id, {"pwsh", "powershell", "cmd", "bash", "wsl"})


class TerminalDeVerdade(unittest.TestCase):
    """Sobe um shell num ConPTY e conversa com ele."""

    def conversar(self, passos, segundos=9):
        app = QCoreApplication.instance() or QCoreApplication([])
        recebido = []
        sessao = PtySession("teste", cols=100, rows=28, env={"DBX_TERM_TESTE": "1"})
        sessao.output.connect(lambda _sid, dado: recebido.append(dado))
        sessao.start()

        for atraso, acao in passos:
            QTimer.singleShot(atraso, acao(sessao))
        QTimer.singleShot(int(segundos * 1000), app.quit)
        app.exec()
        sessao.stop()
        return LIMPAR.sub("", "".join(recebido))

    def test_o_shell_responde_ao_que_e_digitado(self):
        saida = self.conversar([(1500, lambda s: lambda: s.write("echo DBX-MARCA-OK\r"))], segundos=5)
        self.assertIn("DBX-MARCA-OK", saida)

    def test_redimensionar_chega_no_processo_filho(self):
        passos = [
            (1500, lambda s: lambda: s.write('"L=$([Console]::WindowWidth)"\r')),
            (3200, lambda s: lambda: s.resize(60, 20)),
            (4600, lambda s: lambda: s.write('"D=$([Console]::WindowWidth)"\r')),
        ]
        saida = self.conversar(passos, segundos=7)
        self.assertIn("L=100", saida, "o shell devia nascer com a largura pedida")
        self.assertIn("D=60", saida, "redimensionar a aba tem que chegar no processo")

    def test_variavel_da_aba_chega_no_shell(self):
        saida = self.conversar([(1500, lambda s: lambda: s.write('"V=$env:DBX_TERM_TESTE"\r'))], segundos=5)
        self.assertIn("V=1", saida)

    def test_encerrar_derruba_o_processo(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        sessao = PtySession("teste", cols=80, rows=24)
        sessao.start()
        QTimer.singleShot(1200, app.quit)
        app.exec()
        self.assertTrue(sessao.alive)
        sessao.stop()
        QTimer.singleShot(600, app.quit)
        app.exec()
        self.assertFalse(sessao.alive)


if __name__ == "__main__":
    unittest.main()
