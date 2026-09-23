"""O que vai pro disco e volta: gravacao inteira, ajustes, abas guardadas e ferramentas.

Tudo na pasta temporaria da base. Falha de disco e arquivo em uso entram falsificados.
"""

from __future__ import annotations

import json
import unittest.mock
from pathlib import Path

from base import ComPastaPropria

from loom import caminhos, config, ferramentas, sessao_salva


class TrocaDoArquivoEmUso(ComPastaPropria):
    """No Windows, quem esta lendo o arquivo naquele instante segura a troca."""

    def trocar_falhando(self, vezes: int):
        original = Path.replace
        tentativas = []

        def replace(caminho, alvo):
            tentativas.append(caminho)
            if len(tentativas) <= vezes:
                raise PermissionError(32, "em uso por outro processo")
            return original(caminho, alvo)

        return tentativas, unittest.mock.patch.object(Path, "replace", replace)

    def test_leitor_que_solta_logo_nao_perde_a_gravacao(self):
        alvo = self.raiz / "dados" / "x.json"
        tentativas, troca = self.trocar_falhando(2)
        with troca, unittest.mock.patch.object(caminhos.time, "sleep"):
            caminhos.gravar_texto(alvo, "novo")
        self.assertEqual(alvo.read_text(encoding="utf-8"), "novo")
        self.assertEqual(len(tentativas), 3)

    def test_arquivo_preso_de_vez_avisa_e_nao_deixa_temporario(self):
        alvo = self.raiz / "dados" / "x.json"
        alvo.parent.mkdir(parents=True)
        alvo.write_text("antigo", encoding="utf-8")
        _, troca = self.trocar_falhando(99)
        with troca, unittest.mock.patch.object(caminhos.time, "sleep"):
            with self.assertRaises(PermissionError):
                caminhos.gravar_texto(alvo, "novo")
        self.assertEqual(alvo.read_text(encoding="utf-8"), "antigo")
        self.assertEqual(list(alvo.parent.glob("*.tmp")), [])

    def test_pasta_que_nao_existe_e_criada(self):
        alvo = self.raiz / "a" / "b" / "c.json"
        caminhos.gravar_texto(alvo, "ok")
        self.assertEqual(alvo.read_text(encoding="utf-8"), "ok")


class AjustesEditadosAMao(ComPastaPropria):
    def escrever(self, texto: str) -> None:
        config.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        config.ARQUIVO.write_text(texto, encoding="utf-8")

    def test_nan_no_arquivo_nao_vira_ajuste(self):
        """O json do Python aceita NaN, e NaN passava por qualquer faixa sem ser contido."""
        self.escrever('{"entrelinha": NaN, "historico": NaN}')
        lido = config.carregar()
        self.assertEqual(lido["entrelinha"], config.PADRAO["entrelinha"])
        self.assertEqual(lido["historico"], config.PADRAO["historico"])

    def test_infinito_fica_no_teto_da_faixa(self):
        self.escrever('{"historico": Infinity, "tamanhoFonte": -Infinity}')
        lido = config.carregar()
        self.assertEqual((lido["historico"], lido["tamanhoFonte"]), (200000, 9))

    def test_texto_livre_passa_mas_tipo_trocado_nao(self):
        salvo = config.salvar({"editor": "notepad++", "cursor": 3, "confirmarFechar": "sim"})
        self.assertEqual(salvo["editor"], "notepad++")
        self.assertEqual(salvo["cursor"], config.PADRAO["cursor"])
        self.assertIs(salvo["confirmarFechar"], True)

    def test_salvar_um_campo_mantem_os_outros(self):
        config.salvar({"tamanhoFonte": 18})
        config.salvar({"editor": "zed"})
        lido = config.carregar()
        self.assertEqual((lido["tamanhoFonte"], lido["editor"]), (18, "zed"))


class AbasGuardadas(ComPastaPropria):
    def test_so_as_vinte_primeiras_sao_guardadas(self):
        sessao_salva.gravar([{"shell": "pwsh", "apelido": str(i)} for i in range(30)])
        lidas = sessao_salva.ler()
        self.assertEqual(len(lidas), 20)
        self.assertEqual(lidas[-1]["apelido"], "19")

    def test_so_os_campos_conhecidos_vao_pro_disco(self):
        sessao_salva.gravar([{"shell": "pwsh", "ativa": 1, "senha": "x"}, "solta", None])
        (unica,) = sessao_salva.ler()
        self.assertIs(unica["ativa"], True)
        self.assertEqual(unica["pasta"], "")
        self.assertNotIn("senha", unica)

    def test_falha_ao_gravar_deixa_tentar_de_novo(self):
        """Sem isso, a mesma lista nunca mais iria pro disco: parecia ja gravada."""
        abas = [{"shell": "pwsh", "apelido": "build"}]
        with unittest.mock.patch.object(Path, "replace", side_effect=PermissionError(32, "em uso")):
            sessao_salva.gravar(abas)
        self.assertFalse(sessao_salva.ARQUIVO.exists())
        self.assertEqual(list(sessao_salva.ARQUIVO.parent.glob("*.tmp")), [])

        sessao_salva.gravar(abas)
        self.assertEqual(sessao_salva.ler()[0]["apelido"], "build")

    def test_arquivo_ilegivel_ou_de_outro_formato_devolve_vazio(self):
        sessao_salva.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        for texto in ("{quebrado", '{"shell": "pwsh"}', "null"):
            sessao_salva.ARQUIVO.write_text(texto, encoding="utf-8")
            self.assertEqual(sessao_salva.ler(), [], texto)


class CadastroDeFerramentas(ComPastaPropria):
    def test_primeira_leitura_grava_as_embutidas(self):
        self.assertFalse(ferramentas.ARQUIVO.exists())
        self.assertEqual([f.id for f in ferramentas.listar()], ["claude", "codex", "gemini"])
        self.assertTrue(ferramentas.ARQUIVO.exists())

    def test_editar_antes_de_existir_o_arquivo_nao_muda_o_padrao_embutido(self):
        """A lista devolvida sem arquivo era a propria lista de fabrica, e editar mexia nela.

        Dai em diante, no mesmo processo, o padrao de fabrica e o campo que faltasse no arquivo
        vinham com a edicao de outra vez.
        """
        ferramentas.salvar({"id": "codex", "comando": "codex --full-auto"})
        self.assertEqual([f.comando for f in ferramentas.EMBUTIDAS], ["claude", "codex", "gemini"])
        ferramentas.ARQUIVO.write_text("{quebrado", encoding="utf-8")
        self.assertEqual(ferramentas.por_id("codex").comando, "codex")

    def test_editar_uma_existente_nao_perde_o_nome_nem_as_outras(self):
        ferramentas.salvar({"id": "codex", "nome": "", "comando": "codex --full-auto"})
        codex = ferramentas.por_id("codex")
        self.assertEqual((codex.nome, codex.comando), ("Codex CLI", "codex --full-auto"))
        self.assertEqual(len(ferramentas.listar()), 3)

    def test_ferramenta_nova_ganha_id_pelo_nome(self):
        nova = ferramentas.salvar({"nome": "Meu CLI", "env": "MEU_HOME", "login": "token.json", "comando": "meu"})
        self.assertEqual(nova.id, "meu-cli")
        relida = ferramentas.por_id("meu-cli")
        self.assertEqual((relida.env, relida.login, relida.comando), ("MEU_HOME", "token.json", "meu"))

    def test_entrada_incompleta_e_pulada_e_nada_valido_volta_as_embutidas(self):
        ferramentas.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        ferramentas.ARQUIVO.write_text(
            json.dumps([{"id": "x", "nome": "X"}, "solta", {"id": "y", "nome": "Y", "env": "E", "login": "l"}]),
            encoding="utf-8",
        )
        self.assertEqual([f.id for f in ferramentas.listar()], ["y"])

        ferramentas.ARQUIVO.write_text(json.dumps([{"id": "x"}]), encoding="utf-8")
        self.assertEqual([f.id for f in ferramentas.listar()], ["claude", "codex", "gemini"])

    def test_arquivo_quebrado_nao_derruba(self):
        ferramentas.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        for texto in ("{quebrado", '{"id": "claude"}'):
            ferramentas.ARQUIVO.write_text(texto, encoding="utf-8")
            self.assertEqual(ferramentas.listar()[0].id, "claude", texto)


if __name__ == "__main__":
    unittest.main()
