"""O atalho de status line da conta principal volta a apontar pro coletor de agora.

Com o coletor sumido, o atalho caia calado na status line de sempre e a barra de uso da
conta principal ficava sem leitura. Tudo aqui roda em pasta temporaria.
"""

from __future__ import annotations

import json
import sys

from base import ComPastaPropria

from loom import perfis

LEGADO_DBX_TERM = (
    "@echo off\r\n"
    "rem Gerado pelo DBX Term. Se o projeto sumir, a status line de sempre continua.\r\n"
    'if exist "C:\\sumiu\\dbx-term\\coletar.py" (\r\n'
    '  "C:\\sumiu\\dbx-term\\.venv\\Scripts\\python.exe" "C:\\sumiu\\dbx-term\\coletar.py" %*\r\n'
    ") else (\r\n"
    '  python "C:/Users/eu/.claude/statusline.py"\r\n'
    ")\r\n"
)


class ReparoDaBarra(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.principal = self.casa / ".claude"
        self._pasta_antes = perfis.pasta_principal
        perfis.pasta_principal = lambda: self.principal
        self.atalho = self.raiz / "dbx-term" / "status-line.cmd"
        self.atalho.parent.mkdir(parents=True)
        self.projeto = self.raiz / "Loom"
        self.projeto.mkdir()
        (self.projeto / "coletar.py").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        perfis.pasta_principal = self._pasta_antes
        super().tearDown()

    def settings(self, comando: str | None) -> str:
        dados = {"theme": "dark"}
        if comando is not None:
            dados["statusLine"] = {"type": "command", "command": comando}
        texto = json.dumps(dados, indent=2)
        (self.principal / "settings.json").write_text(texto, encoding="utf-8")
        return texto

    def comando_do_atalho(self) -> str:
        return f'"{self.atalho}" __padrao__ "python \\"C:/Users/eu/.claude/statusline.py\\""'

    def reparar(self) -> bool:
        return perfis.reparar_status_line_principal("py.exe", str(self.projeto))

    def test_atalho_do_dbx_term_passa_a_chamar_o_coletor_do_loom(self):
        self.atalho.write_text(LEGADO_DBX_TERM, encoding="utf-8", newline="")
        antes = self.settings(self.comando_do_atalho())

        self.assertTrue(self.reparar())

        novo = self.atalho.read_text(encoding="utf-8", newline="")
        self.assertIn(str(self.projeto / "coletar.py"), novo)
        self.assertNotIn("sumiu", novo)
        linhas = novo.splitlines()
        # o caminho de fuga continua sendo a status line de sempre
        self.assertEqual(linhas[linhas.index(") else (") + 1].strip(), 'python "C:/Users/eu/.claude/statusline.py"')
        self.assertNotIn("\r\r\n", novo, "quebra dupla estraga arquivo cmd")
        # o settings da conta principal nao e tocado
        self.assertEqual((self.principal / "settings.json").read_text(encoding="utf-8"), antes)

    def test_no_app_instalado_chama_o_exe_ao_lado_da_janela(self):
        self.atalho.write_text(LEGADO_DBX_TERM, encoding="utf-8", newline="")
        self.settings(self.comando_do_atalho())
        sys.frozen = True
        try:
            self.assertTrue(perfis.reparar_status_line_principal(r"C:\Program Files\Loom\Loom.exe", "ignorado"))
        finally:
            del sys.frozen
        novo = self.atalho.read_text(encoding="utf-8")
        self.assertIn(r'"C:\Program Files\Loom\loom-coletor.exe" %*', novo)

    def test_atalho_ja_certo_nao_e_regravado(self):
        self.atalho.write_text(LEGADO_DBX_TERM, encoding="utf-8", newline="")
        self.settings(self.comando_do_atalho())
        self.assertTrue(self.reparar())
        self.assertFalse(self.reparar())

    def test_atalho_de_outra_ferramenta_fica_como_esta(self):
        alheio = "@echo off\r\nrem outra coisa\r\n) else (\r\n  minha-barra\r\n)\r\n"
        self.atalho.write_text(alheio, encoding="utf-8", newline="")
        self.settings(self.comando_do_atalho())

        self.assertFalse(self.reparar())
        self.assertEqual(self.atalho.read_text(encoding="utf-8", newline=""), alheio)

    def test_status_line_que_nao_e_atalho_fica_como_esta(self):
        self.settings('python "C:/Users/eu/.claude/statusline.py"')
        self.assertFalse(self.reparar())

    def test_sem_status_line_nada_acontece(self):
        self.settings(None)
        self.assertFalse(self.reparar())

    def test_sem_settings_nada_acontece(self):
        self.assertFalse(self.reparar())


class LeituraDaPrincipal(ComPastaPropria):
    """Ligar a Principal no Loom mexe no settings da pessoa, e Desligar tem que devolver como era."""

    def setUp(self) -> None:
        super().setUp()
        self.principal = self.casa / ".claude"
        self._pasta_antes = perfis.pasta_principal
        perfis.pasta_principal = lambda: self.principal
        self.projeto = self.raiz / "Loom"
        self.projeto.mkdir()
        (self.projeto / "coletar.py").write_text("", encoding="utf-8")
        self.alvo = self.principal / "settings.json"

    def tearDown(self) -> None:
        perfis.pasta_principal = self._pasta_antes
        super().tearDown()

    def ler(self) -> dict:
        return json.loads(self.alvo.read_text(encoding="utf-8"))

    def test_ligar_e_desligar_devolve_o_settings_como_era(self):
        gancho_da_pessoa = {"hooks": [{"type": "command", "command": "meu-gancho.cmd"}]}
        original = {
            "theme": "dark",
            "statusLine": {"type": "command", "command": "minha-barra.cmd", "padding": 2},
            "hooks": {"SessionStart": [gancho_da_pessoa]},
        }
        self.alvo.write_text(json.dumps(original), encoding="utf-8")

        self.assertTrue(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        ligado = self.ler()
        self.assertTrue(perfis.leitura_principal_ligada())
        self.assertIn("status-line.cmd", ligado["statusLine"]["command"])
        self.assertEqual(ligado["statusLine"]["padding"], 2)
        self.assertEqual(ligado["hooks"]["SessionStart"][0], gancho_da_pessoa)
        self.assertEqual(len(ligado["hooks"]["SessionStart"]), 2)
        self.assertIn("--aviso __padrao__", ligado["hooks"]["Notification"][0]["hooks"][0]["command"])
        # a barra de antes fica no caminho de fuga do atalho
        atalho = (perfis.RAIZ / "status-line.cmd").read_text(encoding="utf-8")
        self.assertIn("  minha-barra.cmd", atalho.splitlines())

        # ligar de novo nao empilha ganchos nem perde a barra de antes
        self.assertTrue(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        self.assertEqual(len(self.ler()["hooks"]["SessionStart"]), 2)

        self.assertTrue(perfis.desligar_leitura_principal())
        self.assertEqual(self.ler(), original)
        self.assertFalse(perfis.leitura_principal_ligada())
        self.assertFalse(perfis.desligar_leitura_principal(), "nada do Loom sobrou pra tirar")

    def test_sem_barra_antes_desligar_tira_a_status_line(self):
        self.alvo.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        self.assertTrue(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        self.assertTrue(perfis.desligar_leitura_principal())
        self.assertEqual(self.ler(), {"theme": "dark"})

    def test_settings_ilegivel_nao_e_reescrito(self):
        self.alvo.write_text("{ quebrado", encoding="utf-8")
        self.assertFalse(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        self.assertEqual(self.alvo.read_text(encoding="utf-8"), "{ quebrado")
