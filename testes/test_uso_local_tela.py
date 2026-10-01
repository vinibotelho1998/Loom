"""A seção "Nesta máquina, por projeto" do painel de uso, rodada no mesmo motor JS do test_painel_uso."""

from __future__ import annotations

import copy
import unittest

from test_painel_uso import CONTRATO
from test_painel_uso import ComPainel as PainelNoMotor


def projeto(nome: str, tokens: int, custo: float, sem_preco: bool = False, **extra) -> dict:
    item = {
        "projeto": nome,
        "caminhos": [f"C:\\p\\{nome}"],
        "custo": custo,
        "tokens": tokens,
        "mensagens": 3,
        "custo7": custo,
        "tokens7": tokens // 2,
        "custoHoje": 0.0,
        "tokensHoje": 0,
        "semPreco": sem_preco,
        "ultimoDia": "2026-09-28",
    }
    item.update(extra)
    return item


def com_local(ranking: list[dict], total: int | None = None, sem_preco: bool = False) -> dict:
    dados = copy.deepcopy(CONTRATO)
    periodo = {"custo": 10.0, "tokens": 1000, "mensagens": 3, "semPreco": sem_preco}
    dados["contas"][0]["local"] = {
        "totais": {"hoje": periodo, "dias7": periodo, "dias30": periodo},
        "ranking": ranking,
        "total": len(ranking) if total is None else total,
    }
    return dados


class SecaoLocal(PainelNoMotor):
    def test_lista_os_projetos_com_custo_e_tokens(self):
        html = self.desenho(com_local([projeto("alfa", 2_000_000, 120.5), projeto("Loom", 500_000, 30.0)]))
        self.assertIn("Nesta máquina, por projeto", html)
        self.assertIn('<span class="uso-proj-nome">alfa</span><b class="uso-proj-valor">US$ 121</b>', html)
        self.assertIn("2 mi em 30 dias · 1 mi em 7", html)
        self.assertIn("Lido dos arquivos deste computador.", html)
        self.assertNotIn("≥", html)

    def test_projeto_sem_preco_mostra_o_minimo_e_avisa(self):
        html = self.desenho(com_local([projeto("alfa", 2_000_000, 120.5, sem_preco=True)], sem_preco=True))
        self.assertIn('<b class="uso-proj-valor">≥ US$ 121</b>', html)
        self.assertIn("a partir de US$ 10", html)
        self.assertIn("O valor com ≥ é o mínimo.", html)

    def test_diz_quantos_projetos_ficaram_de_fora(self):
        html = self.desenho(com_local([projeto("a", 10, 1.0)], total=4))
        self.assertIn("e mais 3 projetos com menos uso.", html)

    def test_nome_de_pasta_nao_vira_html(self):
        html = self.desenho(com_local([projeto("<img src=x onerror=1>", 10, 1.0, caminhos=["<b>x</b>"])]))
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&#60;img src=x onerror=1&#62;", html)

    def test_sem_projeto_a_secao_nao_aparece(self):
        self.assertNotIn("Nesta máquina", self.desenho(com_local([])))
        self.assertNotIn("Nesta máquina", self.desenho(CONTRATO))

    def test_relatorio_antigo_sem_local_continua_desenhando(self):
        self.assertIn("Limites", self.desenho(CONTRATO))


if __name__ == "__main__":
    unittest.main()
