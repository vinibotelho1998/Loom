"""Cadastro de cada conta: pastas confiadas, status line, ganchos e de quem e o login.

A pasta pessoal e a da conta principal apontam pra pasta temporaria da base, e o CLI que
diz de quem e o login entra falsificado. Nada toca o cadastro de verdade.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest.mock

from base import ComPastaPropria

from loom import ferramentas, perfis


class ConfiarNumaPasta(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.conta = perfis.criar("Uma")
        self.cadastro = self.conta.config_dir / ".claude.json"

    def ler(self) -> dict:
        return json.loads(self.cadastro.read_text(encoding="utf-8"))

    def test_caminho_do_windows_vira_o_formato_do_claude(self):
        self.assertTrue(perfis.confiar(self.conta, "C:\\Users\\eu\\app\\"))
        self.assertTrue(self.ler()["projects"]["C:/Users/eu/app"]["hasTrustDialogAccepted"])

    def test_pasta_ja_confiada_nao_reescreve(self):
        perfis.confiar(self.conta, "C:\\app")
        antes = self.cadastro.read_text(encoding="utf-8")
        self.assertFalse(perfis.confiar(self.conta, "C:/app/"))
        self.assertEqual(self.cadastro.read_text(encoding="utf-8"), antes)

    def test_o_resto_do_cadastro_continua_como_estava(self):
        self.cadastro.write_text(
            json.dumps(
                {
                    "hasCompletedOnboarding": False,
                    "userID": "abc",
                    "projects": {"C:/app": {"allowedTools": ["Bash"]}},
                }
            ),
            encoding="utf-8",
        )
        perfis.confiar(self.conta, "C:\\app")
        salvo = self.ler()
        self.assertEqual(salvo["userID"], "abc")
        self.assertIs(salvo["hasCompletedOnboarding"], False)
        self.assertEqual(salvo["projects"]["C:/app"], {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True})

    def test_pasta_vazia_ou_conta_emprestada_nao_confia(self):
        self.assertFalse(perfis.confiar(self.conta, ""))
        self.assertFalse(perfis.confiar(self.conta, "/"))
        emprestada = perfis.criar("Principal", pasta=str(self.casa / ".claude"))
        self.assertFalse(perfis.confiar(emprestada, "C:\\app"))
        self.assertFalse((self.casa / ".claude" / ".claude.json").exists())
        self.assertFalse(self.cadastro.exists())


class HerdarDaContaPrincipal(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.conta = perfis.criar("Uma")
        self.cadastro = self.conta.config_dir / ".claude.json"
        self.casa_falsa = unittest.mock.patch("os.path.expanduser", return_value=str(self.casa))
        self.casa_falsa.start()

    def tearDown(self) -> None:
        self.casa_falsa.stop()
        super().tearDown()

    def principal(self, projetos: dict) -> None:
        (self.casa / ".claude.json").write_text(json.dumps({"projects": projetos}), encoding="utf-8")

    def test_o_que_a_conta_ja_tinha_nao_e_mexido_nem_contado(self):
        self.principal({"C:/a": {"hasTrustDialogAccepted": True}, "C:/b": {"hasTrustDialogAccepted": True}})
        self.cadastro.write_text(
            json.dumps({"projects": {"C:/a": {"hasTrustDialogAccepted": True, "allowedTools": ["Read"]}}}),
            encoding="utf-8",
        )
        self.assertEqual(perfis.herdar_confianca(self.conta), 1)
        salvo = json.loads(self.cadastro.read_text(encoding="utf-8"))
        self.assertEqual(salvo["projects"]["C:/a"]["allowedTools"], ["Read"])
        self.assertTrue(salvo["projects"]["C:/b"]["hasTrustDialogAccepted"])

    def test_nada_novo_nao_grava(self):
        self.principal({"C:/a": {"hasTrustDialogAccepted": True}})
        self.assertEqual(perfis.herdar_confianca(self.conta), 1)
        antes = self.cadastro.stat().st_mtime_ns
        self.assertEqual(perfis.herdar_confianca(self.conta), 0)
        self.assertEqual(self.cadastro.stat().st_mtime_ns, antes)

    def test_principal_ilegivel_ou_ausente_nao_derruba(self):
        self.assertEqual(perfis.herdar_confianca(self.conta), 0)
        (self.casa / ".claude.json").write_text("{quebrado", encoding="utf-8")
        self.assertEqual(perfis.herdar_confianca(self.conta), 0)
        self.assertFalse(self.cadastro.exists())

    def test_conta_emprestada_nao_herda(self):
        self.principal({"C:/a": {"hasTrustDialogAccepted": True}})
        emprestada = perfis.criar("Principal", pasta=str(self.casa / ".claude"))
        self.assertEqual(perfis.herdar_confianca(emprestada), 0)


class StatusLineEGanchos(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.conta = perfis.criar("Uma")
        self.settings = self.conta.config_dir / "settings.json"

    def ler(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_gancho_da_pessoa_fica_e_o_nosso_antigo_e_trocado(self):
        self.settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"type": "command", "command": "meu-gancho.cmd"}]},
                            {"hooks": [{"type": "command", "command": '"py" "C:\\velho\\coletar.py" --hook x'}]},
                            "gancho estranho",
                        ],
                        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "checa.cmd"}]}],
                    }
                }
            ),
            encoding="utf-8",
        )

        self.assertTrue(perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj"))

        ganchos = self.ler()["hooks"]
        comandos = [h["command"] for g in ganchos["SessionStart"] if isinstance(g, dict) for h in g["hooks"]]
        self.assertEqual(comandos[0], "meu-gancho.cmd")
        self.assertEqual(len([c for c in comandos if "coletar.py" in c]), 1)
        self.assertIn(f'--hook {self.conta.id}', comandos[-1])
        self.assertNotIn("velho", " ".join(comandos))
        self.assertEqual(ganchos["PreToolUse"][0]["hooks"][0]["command"], "checa.cmd")

    def test_no_app_instalado_chama_o_exe_e_nao_mexe_no_pythonpath(self):
        with unittest.mock.patch.object(sys, "frozen", True, create=True):
            perfis.instalar_coletor(self.conta, "C:\\Program Files\\Loom\\Loom.exe", "ignorado")
        salvo = self.ler()
        self.assertTrue(
            salvo["statusLine"]["command"].startswith('"C:\\Program Files\\Loom\\loom-coletor.exe" ')
        )
        self.assertNotIn("env", salvo)

    def test_rodando_do_codigo_leva_o_projeto_no_pythonpath(self):
        perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj")
        salvo = self.ler()
        self.assertEqual(salvo["env"]["PYTHONPATH"], "C:\\proj")
        self.assertEqual(salvo["statusLine"]["command"], f'"py.exe" "C:\\proj\\coletar.py" {self.conta.id}')

    def test_settings_estragado_e_preservado(self):
        self.settings.write_text("{quebrado", encoding="utf-8")
        self.assertFalse(perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj"))
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{quebrado")


class DeQuemEOLogin(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.conta = perfis.criar("Uma")
        (self.conta.config_dir / ".credentials.json").write_text("{}", encoding="utf-8")

    def cli(self, stdout: str = "", erro: Exception | None = None):
        chamadas = []

        def falso_run(argumentos, **opcoes):
            chamadas.append((argumentos, opcoes))
            if erro is not None:
                raise erro
            return subprocess.CompletedProcess(argumentos, 0, stdout=stdout, stderr="")

        return chamadas, unittest.mock.patch.object(perfis.subprocess, "run", falso_run)

    def test_pergunta_na_pasta_da_conta_e_sem_janela(self):
        chamadas, run = self.cli(json.dumps({"loggedIn": True, "email": "a@b.c", "subscriptionType": "max"}))
        with run:
            carimbo, resumo = perfis.perguntar_identidade(self.conta)
        self.assertEqual(resumo, {"email": "a@b.c", "plano": "max", "logado": True})
        self.assertEqual(carimbo, (self.conta.config_dir / ".credentials.json").stat().st_mtime)
        argumentos, opcoes = chamadas[0]
        self.assertEqual(argumentos, ["claude", "auth", "status"])
        self.assertEqual(opcoes["env"]["CLAUDE_CONFIG_DIR"], str(self.conta.config_dir))
        self.assertEqual(opcoes["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_sem_assinatura_o_plano_e_o_jeito_de_entrar(self):
        _, run = self.cli(json.dumps({"loggedIn": True, "authMethod": "api_key"}))
        with run:
            self.assertEqual(perfis.perguntar_identidade(self.conta)[1]["plano"], "api_key")

    def test_resposta_ilegivel_ou_cli_ausente_nao_inventa_login(self):
        for stdout, erro in (("nao e json", None), ("[1]", None), ("", FileNotFoundError("claude"))):
            _, run = self.cli(stdout, erro)
            with run:
                _, resumo = perfis.perguntar_identidade(self.conta)
            self.assertEqual(resumo, {"email": "", "plano": "", "logado": False}, stdout)

    def test_sem_credencial_nem_pergunta(self):
        (self.conta.config_dir / ".credentials.json").unlink()
        chamadas, run = self.cli("{}")
        with run:
            self.assertIsNone(perfis.perguntar_identidade(self.conta))
        self.assertEqual(chamadas, [])

    def test_resposta_fica_guardada_ate_a_credencial_mudar(self):
        chamadas, run = self.cli(json.dumps({"loggedIn": True, "email": "a@b.c"}))
        with run:
            self.assertEqual(perfis.identidade(self.conta)["email"], "a@b.c")
            self.assertEqual(perfis.identidade(self.conta)["email"], "a@b.c")
        self.assertEqual(len(chamadas), 1)
        self.assertIn("a@b.c", perfis.CACHE_IDENTIDADE.read_text(encoding="utf-8"))

        credencial = self.conta.config_dir / ".credentials.json"
        os.utime(credencial, (credencial.stat().st_atime, credencial.stat().st_mtime + 60))
        guardada, velha = perfis.identidade_guardada(self.conta)
        self.assertTrue(velha)
        self.assertEqual(guardada["email"], "a@b.c")


class CadastroDaConta(ComPastaPropria):
    def test_nome_em_branco_e_cor_que_da_a_volta(self):
        contas = [perfis.criar("  ") for _ in range(len(perfis.CORES) + 1)]
        self.assertEqual(contas[0].nome, "sem nome")
        self.assertEqual([c.cor for c in contas], perfis.CORES + perfis.CORES[:1])

    def test_renomear_pra_branco_mantem_o_nome(self):
        conta = perfis.criar("Trabalho")
        perfis.atualizar(conta.id, nome="   ", cor="")
        atual = perfis.por_id(conta.id)
        self.assertEqual((atual.nome, atual.cor), ("Trabalho", conta.cor))

    def test_mexer_em_conta_que_nao_existe_nao_grava(self):
        perfis.criar("Uma")
        antes = perfis.ARQUIVO.read_text(encoding="utf-8")
        self.assertIsNone(perfis.atualizar("sumiu", nome="x"))
        perfis.remover("sumiu", apagar_login=True)
        self.assertEqual(perfis.ARQUIVO.read_text(encoding="utf-8").replace(" ", ""), antes.replace(" ", ""))
        self.assertFalse(perfis.esquecer_login("sumiu"))

    def test_ferramenta_sem_variavel_nao_mexe_no_ambiente(self):
        ferramentas.salvar({"id": "meu", "nome": "Meu", "env": "", "login": "t.json", "comando": "meu"})
        conta = perfis.criar("Minha", "meu")
        self.assertEqual(perfis.ambiente(conta), {})
        self.assertFalse(conta.e_padrao)

    def test_conta_na_pasta_padrao_de_outra_ferramenta_e_a_padrao_dela(self):
        with unittest.mock.patch(
            "os.path.expanduser", side_effect=lambda caminho: caminho.replace("~", str(self.casa), 1)
        ):
            codex = perfis.criar("Codex", "codex", pasta=str(self.casa / ".codex"))
            self.assertTrue(codex.e_padrao)
            self.assertEqual(perfis.ambiente(codex), {})
            outra = perfis.criar("Outra", "codex")
            self.assertFalse(outra.e_padrao)
            self.assertEqual(perfis.ambiente(outra), {"CODEX_HOME": str(outra.config_dir)})


if __name__ == "__main__":
    unittest.main()
