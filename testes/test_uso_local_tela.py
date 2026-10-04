"""As seções de detalhe do painel de uso, rodadas no mesmo motor JS do test_painel_uso: por projeto, o que
veio de fora e por modelo e esforço."""

from __future__ import annotations

import copy
import re
import unittest

from test_painel_uso import CONTRATO, local
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
        self.assertIn("Por projeto, nesta máquina", html)
        self.assertIn('<span class="uso-proj-nome">alfa</span><b class="uso-proj-valor">US$ 121</b>', html)
        self.assertIn("2 mi em 30 dias · 1 mi em 7 dias", html)
        # a explicacao fica no icone de informacao do titulo
        self.assertIn('<span class="uso-info" title="Lido dos arquivos deste computador.', html)
        self.assertNotIn("≥", html)
        self.assertNotIn("uso-mais", html, "dois projetos cabem sem botao")

    def test_barra_do_projeto_mede_o_custo_que_esta_ao_lado(self):
        """O projeto mais caro gasta menos tokens: a barra segue o valor mostrado, e nao os tokens."""
        html = self.desenho(com_local([projeto("caro", 100_000, 100.0), projeto("barato", 900_000, 25.0)]))
        self.assertEqual(re.findall(r'<div class="uso-proj-barra"><i style="width:(\d+)%"></i></div>', html), ["100", "25"])

    def test_com_projeto_sem_preco_a_barra_volta_pros_tokens(self):
        html = self.desenho(com_local([projeto("a", 100_000, 100.0), projeto("b", 400_000, 0.0, sem_preco=True)]))
        self.assertEqual(re.findall(r'<div class="uso-proj-barra"><i style="width:(\d+)%"></i></div>', html), ["25", "100"])

    def test_lista_longa_mostra_cinco_e_abre_o_resto_pela_vista(self):
        ranking = [projeto(f"p{i}", 1000 - i, 10.0 - i) for i in range(8)]
        fechada = self.desenho(com_local(ranking, total=8))
        self.assertEqual(fechada.count('<li class="uso-proj"'), 5)
        self.assertIn('<button class="uso-mais" data-abrir="projetos|claude:84092a89" aria-expanded="false">Mostrar mais 3 projetos</button>', fechada)
        aberta = self.desenho(com_local(ranking, total=8), {"abertos": {"projetos|claude:84092a89": True}})
        self.assertEqual(aberta.count('<li class="uso-proj"'), 8)
        self.assertIn('aria-expanded="true">Mostrar menos</button>', aberta)

    def test_projeto_sem_preco_mostra_o_minimo_e_avisa(self):
        html = self.desenho(com_local([projeto("alfa", 2_000_000, 120.5, sem_preco=True)], sem_preco=True))
        self.assertIn('<b class="uso-proj-valor">≥ US$ 121</b>', html)
        # o aviso grande de preco faltando sai uma vez so, no custo; a lista leva so a nota do sinal
        self.assertIn('<p class="uso-nota">Valor com ≥ é o mínimo: soma só os modelos que têm preço.</p>', html)

    def test_projeto_sem_preco_e_sem_nada_somado_diz_sem_preco(self):
        html = self.desenho(com_local([projeto("alfa", 2_000_000, 0.0, sem_preco=True)], sem_preco=True))
        self.assertIn('<b class="uso-proj-valor">sem preço</b>', html)

    def test_diz_quantos_projetos_ficaram_de_fora(self):
        html = self.desenho(com_local([projeto("a", 10, 1.0)], total=4))
        self.assertIn("E mais 3 projetos com menos uso nos 30 dias.", html)

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
        self.assertNotIn("Por projeto", self.desenho(CONTRATO))


def com_fora(**campos) -> dict:
    """O contrato do painel com o que veio de fora na semana do Claude, que e a segunda janela."""
    fora = {
        "foraMinimo": 46.7,
        "calibrado": True,
        "final": 100.0,
        "chegouEm": 48.0,
        "primeiraLeituraEm": "2026-09-28T20:39:00-03:00",
        "abertaEm": "2026-09-26T13:58:00-03:00",
        "primeiroUsoAqui": "2026-09-28T20:35:00-03:00",
        "tokensPorPonto": 2_080_528,
        "saltos": [
            {"de": "2026-09-26T13:58:00-03:00", "ate": "2026-09-28T20:39:00-03:00", "pts": 46.7, "tokens": 302_514}
        ],
        "anteriores": [],
    }
    fora.update(campos)
    dados = copy.deepcopy(CONTRATO)
    dados["contas"][0]["janelas"][1]["fora"] = fora
    return dados


class SecaoDeFora(PainelNoMotor):
    def test_mostra_o_piso_do_que_veio_de_fora_e_o_detalhe_no_tooltip(self):
        html = self.desenho(com_fora())
        self.assertIn("Fora deste computador: <b>no mínimo 47%</b>", html)
        self.assertIn("ao menos 47% com 303 mil tokens daqui", html)
        self.assertIn("Soma de outro aparelho, da versão web e de outras pessoas na conta.", html)

    def test_diz_quando_a_janela_abriu_bem_antes_do_primeiro_uso_daqui(self):
        html = self.desenho(com_fora())
        aberta = f"{local('2026-09-26T13:58:00-03:00'):%H:%M}"
        primeiro = f"{local('2026-09-28T20:35:00-03:00'):%H:%M}"
        self.assertIn(f"Janela aberta em", html)
        self.assertIn(f"às {aberta}. O primeiro uso daqui foi em", html)
        self.assertIn(f"às {primeiro}.", html)

    def test_janela_aberta_junto_com_o_primeiro_uso_nao_vira_fato(self):
        html = self.desenho(com_fora(primeiroUsoAqui="2026-09-26T14:10:00-03:00"))
        self.assertNotIn("Janela aberta em", html)

    def test_abaixo_de_um_por_cento_diz_que_nao_detectou_nada(self):
        html = self.desenho(com_fora(foraMinimo=0.4, saltos=[]))
        self.assertIn("Nada de fora detectado nesta janela", html)
        self.assertNotIn("no mínimo", html.split("uso-fora")[1])

    def test_sem_base_de_calibracao_diz_isso_e_nao_inventa_numero(self):
        html = self.desenho(com_fora(foraMinimo=None, calibrado=False, saltos=[]))
        self.assertIn("Ainda sem base para separar o que veio de fora", html)
        self.assertNotIn("Fora deste computador:", html)

    def test_lista_as_janelas_anteriores_com_o_que_se_sabe_de_cada_uma(self):
        anteriores = [
            {"zeraEm": "2026-09-26T21:58:00-03:00", "final": 85.0, "foraMinimo": 69.9},
            {"zeraEm": "2026-09-19T11:25:00-03:00", "final": 44.0, "foraMinimo": 0.0},
            {"zeraEm": "2026-09-16T18:26:00-03:00", "final": 47.0, "foraMinimo": None},
        ]
        html = self.desenho(com_fora(anteriores=anteriores))
        # as janelas anteriores sao detalhe: ficam fechadas, e abrem pela vista
        self.assertIn('<details class="uso-detalhes"><summary data-abrir="anteriores|claude:84092a89|7d">', html)
        self.assertIn("</svg>Janelas anteriores</summary>", html)
        aberta = self.desenho(com_fora(anteriores=anteriores), {"abertos": {"anteriores|claude:84092a89|7d": True}})
        self.assertIn('<details class="uso-detalhes" open><summary data-abrir="anteriores|claude:84092a89|7d">', aberta)
        self.assertIn("85% usado · fora, no mínimo 70%", html)
        self.assertIn("44% usado · sem consumo de fora", html)
        self.assertIn("47% usado · --", html)

    def test_anteriores_sem_nenhum_piso_nao_aparecem(self):
        anteriores = [{"zeraEm": "2026-09-26T21:58:00-03:00", "final": 85.0, "foraMinimo": None}]
        self.assertNotIn("uso-fora-antes", self.desenho(com_fora(anteriores=anteriores)))

    def test_dado_fora_do_formato_nao_derruba_a_tela(self):
        html = self.desenho(com_fora(foraMinimo="x", saltos="lixo", anteriores="lixo", abertaEm=5, primeiroUsoAqui=[]))
        self.assertIn("Ainda sem base", html)
        self.assertIn("Limites", html)

    def test_sem_o_campo_a_janela_continua_igual(self):
        self.assertNotIn("uso-fora", self.desenho(CONTRATO))
        dados = copy.deepcopy(CONTRATO)
        dados["contas"][0]["janelas"][1]["fora"] = None
        self.assertNotIn("uso-fora", self.desenho(dados))


def modelo(nome: str, tokens: int, parte: float, esforcos: list[tuple], **extra) -> dict:
    item = {
        "modelo": nome,
        "tokens": tokens,
        "mensagens": sum(n[2] for n in esforcos),
        "custo": 10.0,
        "semPreco": False,
        "tokens7": tokens // 2,
        "parte": parte,
        "esforcos": [{"nivel": n[0], "tokens": n[1], "mensagens": n[2], "custo": 1.0} for n in esforcos],
    }
    item.update(extra)
    return item


def com_modelos(ranking: list[dict], total: int | None = None) -> dict:
    dados = copy.deepcopy(CONTRATO)
    dados["contas"][0]["modelos"] = {"ranking": ranking, "total": len(ranking) if total is None else total}
    return dados


class SecaoPorModelo(PainelNoMotor):
    def test_mostra_cada_modelo_com_a_fatia_de_cada_esforco(self):
        html = self.desenho(
            com_modelos([modelo("claude-opus-5-5", 3_000_000, 0.75, [("high", 1_000_000, 3), ("xhigh", 3_000_000, 9)])])
        )
        self.assertIn("Por modelo e esforço", html)
        # o nome amigavel na linha e o id cru na dica
        self.assertIn('<span class="uso-proj-nome" title="claude-opus-5-5">Opus 5.5</span>', html)
        self.assertIn("3 mi em 30 dias · 75% do total", html)
        self.assertRegex(html, r'<span class="uso-esforco" title="high: 1 mi tokens em 3 respostas"><i class="uso-esforco-cor" style="background:[^"]+"></i><b>high</b> 25%</span>')
        self.assertIn("<b>xhigh</b> 75%", html)
        self.assertIn("Esforço é o que o Claude Code ou o Codex gravou", html)

    def test_barra_do_modelo_e_cortada_pelos_niveis_na_ordem_do_esforco(self):
        niveis = [("low", 5, 1), ("high", 250, 3), ("xhigh", 745, 9), ("sem registro", 0, 2)]
        html = self.desenho(com_modelos([modelo("m", 1000, 0.5, niveis), modelo("n", 500, 0.25, [("high", 500, 1)])]))
        inicio = html.index('<li class="uso-proj">')
        primeira = html[inicio:html.index("</li>", inicio)]
        # a fatia de 0,5% vira so legenda, e nivel sem token nao vira fatia
        fatias = re.findall(r'<i class="uso-fatia( neutra)?" style="flex-grow:([\d.]+)(;background:[^"]+)?"></i>', primeira)
        self.assertEqual([f[1] for f in fatias], ["25", "74.5"])
        self.assertIn('<span class="uso-fatias" style="width:100%">', primeira, "o maior modelo enche a barra")
        self.assertIn('<span class="uso-fatias" style="width:50%">', html, "o outro mede a fatia dele contra o maior")
        self.assertIn('<i class="uso-esforco-cor neutra"></i><b>sem registro</b> 0%', primeira)
        # a legenda repete o tom da fatia, e cada nivel tem o seu
        tons = re.findall(r'<i class="uso-esforco-cor" style="background:([^"]+)"></i><b>(\w+)</b>', primeira)
        self.assertEqual([n for _, n in tons], ["low", "high", "xhigh"])
        self.assertEqual(len({c for c, _ in tons}), 3)
        for cor, _ in tons[1:]:
            self.assertIn("background:" + cor + '"></i>', primeira.split('<div class="uso-esforcos">')[0])

    def test_aceita_o_ultra_do_codex(self):
        html = self.desenho(com_modelos([modelo("gpt-6-sol", 100, 1.0, [("ultra", 100, 4)])]))
        self.assertIn("<b>ultra</b> 100%", html)

    def test_modelo_sem_preco_mostra_o_minimo(self):
        html = self.desenho(com_modelos([modelo("gpt-6.1-sol", 100, 1.0, [("ultra", 100, 1)], semPreco=True)]))
        self.assertIn('<b class="uso-proj-valor">≥ US$ 10</b>', html)
        self.assertIn("Valor com ≥ é o mínimo", html)

    def test_modelo_sem_preco_e_sem_nada_somado_diz_sem_preco_e_nao_a_partir_de_zero(self):
        html = self.desenho(com_modelos([modelo("gpt-6.1-sol", 100, 1.0, [("ultra", 100, 1)], semPreco=True, custo=0.0)]))
        self.assertIn('<b class="uso-proj-valor">sem preço</b>', html)
        self.assertNotIn("≥ US$ 0", html)

    def test_parte_minuscula_diz_menos_de_um_por_cento(self):
        html = self.desenho(com_modelos([modelo("x", 1, 0.001, [("high", 1, 1)])]))
        self.assertIn("&#60;1% do total", html)

    def test_nome_de_modelo_e_de_nivel_nao_viram_html(self):
        html = self.desenho(com_modelos([modelo("<b>m</b>", 1, 1.0, [("<i>n</i>", 1, 1)])]))
        self.assertNotIn("<b>m</b>", html)
        self.assertNotIn("<i>n</i>", html)

    def test_diz_quantos_modelos_ficaram_de_fora(self):
        html = self.desenho(com_modelos([modelo("a", 1, 1.0, [("high", 1, 1)])], total=3))
        self.assertIn("E mais 2 modelos com menos uso nos 30 dias.", html)

    def test_sem_modelo_a_secao_nao_aparece(self):
        self.assertNotIn("Por modelo e esforço", self.desenho(com_modelos([])))
        self.assertNotIn("Por modelo e esforço", self.desenho(CONTRATO))


if __name__ == "__main__":
    unittest.main()
