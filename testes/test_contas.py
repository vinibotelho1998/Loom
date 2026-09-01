"""Contas, ferramentas e o ambiente que cada aba recebe."""

from __future__ import annotations

import json
import os
import unittest
import unittest.mock
from pathlib import Path

from base import ComPastaPropria

from dbx_term import ferramentas, perfis


class Ferramentas(ComPastaPropria):
    def test_cadastro_antigo_ganha_campo_novo(self):
        """Arquivo salvo por uma versao velha nao pode perder a pasta padrao."""
        ferramentas.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        ferramentas.ARQUIVO.write_text(
            json.dumps([{"id": "claude", "nome": "Claude Code", "env": "CLAUDE_CONFIG_DIR", "login": ".credentials.json"}]),
            encoding="utf-8",
        )
        claude = ferramentas.por_id("claude")
        self.assertEqual(claude.padrao, "~/.claude")
        self.assertEqual(claude.comando, "claude")

    def test_ferramenta_desconhecida_cai_na_primeira(self):
        self.assertEqual(ferramentas.por_id("nao-existe").id, "claude")


class Contas(ComPastaPropria):
    def test_criar_e_listar(self):
        criada = perfis.criar("Trabalho")
        listadas = perfis.listar()
        self.assertEqual([p.id for p in listadas], [criada.id])
        self.assertEqual(listadas[0].nome, "Trabalho")
        self.assertTrue(criada.config_dir.is_dir())

    def test_conta_normal_tem_pasta_propria(self):
        conta = perfis.criar("Uma")
        self.assertFalse(conta.emprestada)
        self.assertEqual(conta.config_dir.parent, perfis.PERFIS)

    def test_conta_emprestada_aponta_pra_pasta_existente(self):
        alvo = self.casa / ".claude"
        conta = perfis.criar("Principal", pasta=str(alvo))
        self.assertTrue(conta.emprestada)
        self.assertEqual(conta.config_dir, alvo)

    def test_ambiente_da_conta_normal_define_a_variavel(self):
        conta = perfis.criar("Uma")
        ambiente = perfis.ambiente(conta)
        self.assertEqual(ambiente["CLAUDE_CONFIG_DIR"], str(conta.config_dir))

    def test_ambiente_da_pasta_padrao_NAO_define_a_variavel(self):
        """O cadastro do Claude fica ao lado da pasta, nao dentro.

        Definir a variavel apontando pra propria pasta padrao faz o Claude procurar o
        cadastro no lugar errado e pedir login de quem ja estava logado.
        """
        padrao = Path(os.path.expanduser(ferramentas.por_id("claude").padrao))
        conta = perfis.criar("Principal", pasta=str(padrao))
        self.assertTrue(conta.e_padrao)
        self.assertEqual(perfis.ambiente(conta), {})

    def test_remover_nao_apaga_pasta_emprestada(self):
        alvo = self.casa / ".claude"
        (alvo / "marca.txt").write_text("nao me apague", encoding="utf-8")
        conta = perfis.criar("Principal", pasta=str(alvo))

        perfis.remover(conta.id, apagar_login=True)

        self.assertEqual(perfis.listar(), [])
        self.assertTrue((alvo / "marca.txt").exists(), "pasta emprestada nao e nossa pra apagar")

    def test_remover_apaga_pasta_propria_quando_pedido(self):
        conta = perfis.criar("Uma")
        (conta.config_dir / "marca.txt").write_text("x", encoding="utf-8")
        pasta = conta.config_dir

        perfis.remover(conta.id, apagar_login=True)

        self.assertFalse(pasta.exists())

    def test_remover_sem_pedir_mantem_o_login(self):
        conta = perfis.criar("Uma")
        pasta = conta.config_dir
        perfis.remover(conta.id, apagar_login=False)
        self.assertTrue(pasta.exists(), "sem marcar a opcao, o login continua no disco")

    def test_renomear_e_recolorir(self):
        conta = perfis.criar("Antigo")
        perfis.atualizar(conta.id, nome="Novo", cor="#123456")
        atual = perfis.por_id(conta.id)
        self.assertEqual((atual.nome, atual.cor), ("Novo", "#123456"))

    def test_logado_olha_o_arquivo_da_ferramenta(self):
        conta = perfis.criar("Uma")
        self.assertFalse(perfis.logado(conta))
        (conta.config_dir / ".credentials.json").write_text("{}", encoding="utf-8")
        self.assertTrue(perfis.logado(conta))

    def test_deslogar_apaga_so_a_credencial(self):
        conta = perfis.criar("Uma")
        (conta.config_dir / ".credentials.json").write_text("{}", encoding="utf-8")
        (conta.config_dir / "settings.json").write_text("{}", encoding="utf-8")

        self.assertTrue(perfis.esquecer_login(conta.id))

        self.assertFalse((conta.config_dir / ".credentials.json").exists())
        self.assertTrue((conta.config_dir / "settings.json").exists())

    def test_deslogar_nao_mexe_em_conta_emprestada(self):
        alvo = self.casa / ".claude"
        (alvo / ".credentials.json").write_text("{}", encoding="utf-8")
        conta = perfis.criar("Principal", pasta=str(alvo))

        self.assertFalse(perfis.esquecer_login(conta.id))
        self.assertTrue((alvo / ".credentials.json").exists())


class Coletor(ComPastaPropria):
    def test_instalar_encadeia_a_status_line_que_ja_existia(self):
        conta = perfis.criar("Uma")
        (conta.config_dir / "settings.json").write_text(
            json.dumps({"statusLine": {"type": "command", "command": "minha-barra"}}), encoding="utf-8"
        )

        perfis.instalar_coletor(conta, "py.exe", r"C:\proj")

        salvo = json.loads((conta.config_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("coletar.py", salvo["statusLine"]["command"])
        self.assertIn("minha-barra", salvo["statusLine"]["command"])

    def test_instalar_duas_vezes_nao_duplica_nem_se_encadeia_sozinho(self):
        conta = perfis.criar("Uma")
        perfis.instalar_coletor(conta, "py.exe", r"C:\proj")
        primeiro = json.loads((conta.config_dir / "settings.json").read_text(encoding="utf-8"))

        mudou = perfis.instalar_coletor(conta, "py.exe", r"C:\proj")
        segundo = json.loads((conta.config_dir / "settings.json").read_text(encoding="utf-8"))

        self.assertFalse(mudou)
        self.assertEqual(primeiro, segundo)
        self.assertEqual(len(segundo["hooks"]["SessionStart"]), 1)

    def test_nao_mexe_no_settings_de_conta_emprestada(self):
        alvo = self.casa / ".claude"
        conta = perfis.criar("Principal", pasta=str(alvo))
        self.assertFalse(perfis.instalar_coletor(conta, "py.exe", r"C:\proj"))
        self.assertFalse((alvo / "settings.json").exists())


class BarraResiliente(ComPastaPropria):
    """A status line do usuario nao pode cair junto com o projeto."""

    def test_com_o_projeto_no_lugar_chama_o_coletor(self):
        projeto = self.raiz / "projeto"
        projeto.mkdir()
        (projeto / "coletar.py").write_text("", encoding="utf-8")

        atalho = perfis.blindar_status_line("py.exe", str(projeto), "minha-barra")
        conteudo = atalho.read_text(encoding="utf-8", newline="")

        self.assertIn("coletar.py", conteudo)
        self.assertIn("minha-barra", conteudo)
        quebra_dupla = chr(13) + chr(13) + chr(10)
        self.assertNotIn(quebra_dupla, conteudo, "quebra dupla estraga arquivo cmd")

    def test_sem_o_projeto_ainda_chama_a_barra_de_sempre(self):
        sumido = self.raiz / "nao-existe"
        atalho = perfis.blindar_status_line("py.exe", str(sumido), "minha-barra")

        linhas = atalho.read_text(encoding="utf-8").splitlines()
        ramo_else = linhas[linhas.index(") else (") + 1]
        self.assertIn("minha-barra", ramo_else, "o caminho de fuga precisa chamar a barra original")


class Confianca(ComPastaPropria):
    def test_herda_so_o_que_ja_estava_aceito(self):
        origem = self.casa / ".claude.json"
        origem.write_text(
            json.dumps(
                {
                    "projects": {
                        "C:/liberado": {"hasTrustDialogAccepted": True},
                        "C:/nao-liberado": {"hasTrustDialogAccepted": False},
                    }
                }
            ),
            encoding="utf-8",
        )
        conta = perfis.criar("Uma")

        with unittest.mock.patch("os.path.expanduser", return_value=str(self.casa)):
            herdadas = perfis.herdar_confianca(conta)

        self.assertEqual(herdadas, 1)
        salvo = json.loads((conta.config_dir / ".claude.json").read_text(encoding="utf-8"))
        self.assertTrue(salvo["projects"]["C:/liberado"]["hasTrustDialogAccepted"])
        self.assertNotIn("C:/nao-liberado", salvo["projects"])


if __name__ == "__main__":
    unittest.main()
