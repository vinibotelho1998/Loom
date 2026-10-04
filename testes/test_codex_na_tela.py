"""A conversa do Codex aparece como do Codex na tela, e o modelo dele com nome de gente.

A sessao do Codex da pasta padrao, sem conta cadastrada, caia na conta de sempre, que e do Claude:
o cartao dizia Claude Code, a conta saia Principal e retomar subia o Claude com um id do Codex.
"""

from __future__ import annotations

import json

import test_pagina as pagina
from test_persistencia import AbrirComComando


class NomeDoModelo(pagina.ComPagina):
    def test_modelos_do_codex_ganham_nome_de_gente(self):
        self.assertEqual(self.rodar('nomeDeModelo("gpt-6.1-sol")'), "GPT-6.1 Sol")
        self.assertEqual(self.rodar('nomeDeModelo("gpt-6-astra")'), "GPT-6 Astra")
        self.assertEqual(self.rodar('nomeDeModelo("codex-auto-review")'), "Codex Auto Review")

    def test_o_resto_continua_como_era(self):
        self.assertEqual(self.rodar('nomeDeModelo("claude-sonnet-4-5-20250929")'), "Sonnet 4.5")
        self.assertEqual(self.rodar('nomeDeModelo("Opus 5.5")'), "Opus 5.5")
        self.assertEqual(self.rodar('nomeDeModelo("desconhecido")'), "desconhecido")


class SessaoDoCodex(pagina.ComPagina):
    def setUp(self) -> None:
        self.definir(
            'estado.perfis = ['
            '{id: "p1", nome: "Principal", cor: "#4f9cf9", ferramenta: "claude", ferramentaNome: "Claude Code", padrao: true},'
            '{id: "c1", nome: "Cdx", cor: "#d9b25f", ferramenta: "codex", ferramentaNome: "Codex CLI"}];'
        )

    def test_codex_sem_conta_nao_vira_a_conta_de_sempre_do_claude(self):
        self.assertEqual(self.rodar('contaDaSessao({sessao: "x", cli: "codex", perfil: ""})'), "")
        self.assertEqual(self.rodar('contaDaSessao({sessao: "x", perfil: ""})'), "p1")

    def test_o_nome_da_ferramenta_e_o_do_codex(self):
        self.assertEqual(self.rodar('nomeDaFerramenta({cli: "codex"}, estado.perfis[0])'), "Codex")
        self.assertEqual(self.rodar('nomeDaFerramenta({cli: "codex"}, estado.perfis[1])'), "Codex CLI")
        self.assertEqual(self.rodar('nomeDaFerramenta({}, estado.perfis[0])'), "Claude Code")
        self.assertEqual(self.rodar("nomeDaFerramenta({}, null)"), "Claude Code")

    def test_sem_titulo_vale_o_projeto_e_nao_a_pasta_de_rascunho(self):
        titulo = self.rodar(
            'tituloDaConversa({projeto: "redator", pasta: "C:/Users/v/Documents/Codex/2026-10-03/x-y-z"}, "Codex")'
        )
        self.assertEqual(titulo, "redator")

    def test_retomar_o_codex_sem_conta_pede_o_codex(self):
        self.definir(
            "var pedidos = []; var antes = novaAba;"
            "novaAba = function (shell, perfil, pedido) { pedidos.push([perfil, pedido]); };"
        )
        try:
            self.definir('retomarSessao({sessao: "abc", cli: "codex", perfil: "", pasta: "C:/p", estado: "aguardando"})')
        finally:
            self.definir("novaAba = antes;")
        self.assertEqual(self.rodar("pedidos"), [["", {"pasta": "C:/p", "conversa": "abc", "cli": "codex"}]])


class PonteRetomaOCodex(AbrirComComando):
    def test_conversa_do_codex_sem_conta_retoma_no_codex(self):
        self.ponte.openSessionWith("pwsh", "", 80, 24, json.dumps({"conversa": "3f2a", "cli": "codex"}))
        self.assertEqual(self.argv()[-1], "codex resume 3f2a")

    def test_sem_a_marca_continua_sendo_o_claude(self):
        self.ponte.openSessionWith("pwsh", "", 80, 24, json.dumps({"conversa": "3f2a", "cli": "outro"}))
        self.assertEqual(self.argv()[-1], "claude --resume 3f2a")


if __name__ == "__main__":
    import unittest

    unittest.main()
