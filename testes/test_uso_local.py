"""Uso desta maquina por projeto, no Claude e no Codex.

Transcript e rollout escritos pelo proprio teste na pasta temporaria da base.
"""

from __future__ import annotations

import json
import unittest
from datetime import date

from test_dados_do_painel import (
    ComPainel,
    carimbo,
    contagem_codex,
    linha_codex,
    registro_codex,
    resposta_claude,
    turno,
)

from loom import painel_uso, perfis, uso

ALFA = "C:\\Users\\Vini\\Documents\\GitHub\\Alfa"
LOOM = "C:\\Users\\Vini\\Documents\\GitHub\\Pessoal\\Loom"


def caminho(*partes: str) -> str:
    return "\\".join(partes)


def com_cwd(registro: dict, pasta: str) -> dict:
    registro["cwd"] = pasta
    return registro


def sessao_codex(quando: str, pasta: str) -> dict:
    return linha_codex("session_meta", quando, {"id": "s", "cwd": pasta})


class ProjetoDoClaude(ComPainel):
    def conversa(self, nome: str, registros: list[dict]) -> None:
        self.escrever_transcript(self.casa / ".claude" / "projects" / nome / "s.jsonl", registros)

    def ler(self) -> dict:
        return uso.coletar_tudo({"__padrao__": self.casa / ".claude"}, cache=self.dados / "uso-cache-v2.json")

    def test_cada_pasta_de_trabalho_vira_um_projeto(self):
        self.conversa(
            "a",
            [
                com_cwd(resposta_claude(carimbo(300), "m1", "r1", input_tokens=100, output_tokens=50), ALFA),
                com_cwd(resposta_claude(carimbo(200), "m2", "r2", input_tokens=10, output_tokens=5), ALFA + "\\"),
                com_cwd(resposta_claude(carimbo(100), "m3", "r3", input_tokens=1, output_tokens=2), LOOM),
            ],
        )
        lista = uso.custos_por_projeto(self.ler()["projetos"], {"__padrao__"})
        self.assertEqual(set(lista), {ALFA, LOOM})
        self.assertEqual((lista[ALFA]["tokens"], lista[ALFA]["mensagens"]), (165, 2))
        self.assertEqual((lista[LOOM]["tokens"], lista[LOOM]["mensagens"]), (3, 1))
        self.assertEqual(uso.nome_do_projeto(ALFA), "Alfa")

    def test_sem_cwd_vale_o_nome_da_pasta_do_transcript(self):
        self.conversa("C--Users-Vini-Documents-x", [resposta_claude(carimbo(60), "m1", "r1")])
        (projeto,) = uso.custos_por_projeto(self.ler()["projetos"], {"__padrao__"})
        self.assertEqual(projeto, "C--Users-Vini-Documents-x")

    def test_ler_de_novo_nao_soma_o_projeto_duas_vezes(self):
        self.conversa("a", [com_cwd(resposta_claude(carimbo(300), "m1", "r1", output_tokens=50), ALFA)])
        self.ler()
        lista = uso.custos_por_projeto(self.ler()["projetos"], {"__padrao__"})
        self.assertEqual(lista[ALFA]["mensagens"], 1)

    def test_total_dos_projetos_bate_com_o_total_dos_dias(self):
        self.conversa(
            "a",
            [
                com_cwd(resposta_claude(carimbo(300), "m1", "r1", output_tokens=50), ALFA),
                com_cwd(resposta_claude(carimbo(200), "m2", "r2", output_tokens=70), LOOM),
            ],
        )
        lido = self.ler()
        por_projeto = sum(p["tokens"] for p in uso.custos_por_projeto(lido["projetos"], {"__padrao__"}).values())
        por_dia = sum(uso.tokens_do_balde(v) for v in lido["dias"].values())
        self.assertEqual(por_projeto, por_dia)

    def test_cache_da_versao_2_vira_legado_e_o_projeto_vem_da_releitura(self):
        antigo = {
            "versao": 2,
            "arquivos": {"x": 10},
            "ultimos": {},
            "dias": {"__padrao__|2026-09-01|claude-opus-5-5": {"entrada": 5, "saida": 5, "mensagens": 1}},
            "horas": {},
            "vistoEm": {},
            "legado": {},
        }
        self.dados.mkdir(parents=True, exist_ok=True)
        (self.dados / "uso-cache-v2.json").write_text(json.dumps(antigo), encoding="utf-8")
        carregado = uso._carregar_cache(self.dados / "uso-cache-v2.json")
        self.assertEqual(carregado["arquivos"], {})
        self.assertEqual(carregado["projetos"], {})
        self.assertIn("__padrao__|2026-09-01|claude-opus-5-5", carregado["legado"])
        self.conversa("a", [com_cwd(resposta_claude(carimbo(300), "m1", "r1", output_tokens=50), ALFA)])
        self.assertIn(ALFA, uso.custos_por_projeto(self.ler()["projetos"], {"__padrao__"}))

    def test_dia_antigo_demais_sai_do_cache_de_projetos(self):
        projetos = {"__padrao__|2020-01-01|p|m": {"saida": 1}, "__padrao__|2026-09-01|p|m": {"saida": 1}}
        mantidos = uso._podar_projetos(projetos, "2026-08-01")
        self.assertEqual(list(mantidos), ["__padrao__|2026-09-01|p|m"])


class ProjetoDoCodex(ComPainel):
    def test_cwd_do_session_meta_e_do_turno_vira_o_projeto(self):
        self.rollout(
            "a",
            [
                sessao_codex(carimbo(400), ALFA),
                turno(carimbo(300), "t1", "gpt-6-sol"),
                registro_codex(carimbo(290), "r1", "t1", entrada=100, lida=0, saida=5),
            ],
        )
        self.rollout(
            "b",
            [
                turno(carimbo(200), "t2", "gpt-6-sol"),
                registro_codex(carimbo(190), "r2", "t2", entrada=10, lida=0, saida=1),
            ],
        )
        self.rollout(
            "c",
            [
                linha_codex("turn_context", carimbo(100), {"turn_id": "t3", "model": "gpt-6-sol", "cwd": LOOM}),
                registro_codex(carimbo(90), "r3", "t3", entrada=7, lida=0, saida=3),
            ],
        )
        lista = uso.custos_por_projeto(self.ler_codex()["projetos"], {"padrao"})
        self.assertEqual(set(lista), {ALFA, LOOM, "desconhecido"})
        self.assertEqual(lista[ALFA]["tokens"], 105)
        self.assertEqual(lista[LOOM]["tokens"], 10)
        self.assertEqual(lista["desconhecido"]["tokens"], 11)

    def test_consumo_tirado_dos_totais_tambem_leva_o_projeto(self):
        self.rollout(
            "velho",
            [
                sessao_codex(carimbo(7300), ALFA),
                turno(carimbo(7200), "t1", "gpt-6-sol"),
                contagem_codex(carimbo(7100), 100, 0, 10),
            ],
            idade=3600,
        )
        lista = uso.custos_por_projeto(self.ler_codex()["projetos"], {"padrao"})
        self.assertEqual(lista[ALFA]["tokens"], 110)

    def test_ler_de_novo_nao_soma_o_projeto_duas_vezes(self):
        self.rollout(
            "a",
            [
                sessao_codex(carimbo(400), ALFA),
                turno(carimbo(300), "t1", "gpt-6-sol"),
                registro_codex(carimbo(290), "r1", "t1", entrada=100, lida=0, saida=5),
            ],
        )
        self.ler_codex()
        self.assertEqual(uso.custos_por_projeto(self.ler_codex()["projetos"], {"padrao"})[ALFA]["mensagens"], 1)


class ConsumoAnteriorAoPrimeiroRegistro(ComPainel):
    """Rollout que comecou so com token_count e depois passou a trazer registro por resposta."""

    def linhas(self, com_registro_da_ultima: bool = True) -> list[dict]:
        linhas = [
            turno(carimbo(900), "t1", "gpt-6-sol"),
            contagem_codex(carimbo(800), 100, 0, 10),
            contagem_codex(carimbo(700), 300, 0, 30),
        ]
        if com_registro_da_ultima:
            linhas.append(registro_codex(carimbo(699), "r1", "t1", entrada=200, lida=0, saida=20))
        return linhas

    def test_o_que_veio_antes_do_primeiro_registro_conta_e_a_resposta_do_registro_nao_dobra(self):
        self.rollout("misto", self.linhas())
        (dia,) = self.ler_codex()["dias"].values()
        self.assertEqual((dia["entrada"] + dia["saida"], dia["mensagens"]), (330, 2))

    def test_registro_de_outra_resposta_nao_come_o_consumo_anterior(self):
        linhas = self.linhas(com_registro_da_ultima=False)
        linhas.append(registro_codex(carimbo(699), "r1", "t1", entrada=5, lida=0, saida=1))
        self.rollout("misto", linhas)
        (dia,) = self.ler_codex()["dias"].values()
        self.assertEqual((dia["entrada"] + dia["saida"], dia["mensagens"]), (336, 3))


class NomeDoProjeto(unittest.TestCase):
    def test_pasta_de_rascunho_do_codex_volta_ao_projeto_de_onde_veio(self):
        base = caminho("C:", "Users", "Vini", "Documents", "Codex", "2026-09-10") + "\\"
        self.assertEqual(uso.nome_do_projeto(base + "c-users-vini-documents-github-alfa"), "alfa")
        self.assertEqual(uso.nome_do_projeto(base + "c-users-vini-desktop-beta"), "beta")

    def test_titulo_de_conversa_do_codex_vira_conversas_avulsas(self):
        base = caminho("C:", "Users", "Vini", "Documents", "Codex", "2026-09-28") + "\\"
        self.assertEqual(uso.nome_do_projeto(base + "pode"), uso.AVULSAS)

    def test_rascunho_temporario_do_claude_e_agrupado(self):
        pasta = caminho("C:", "Users", "Vini", "AppData", "Local", "Temp", "claude", "C--Users-Vini", "abc", "scratchpad")
        self.assertEqual(uso.nome_do_projeto(pasta), uso.TEMPORARIO)

    def test_projeto_comum_e_a_ultima_pasta(self):
        self.assertEqual(uso.nome_do_projeto(ALFA), "Alfa")
        self.assertEqual(uso.nome_do_projeto("/home/x/proj/"), "proj")


class ProjetosNoPainel(ComPainel):
    def painel(self) -> dict:
        return painel_uso.coletar(painel_uso.pedido_do_painel(perfis.listar(), casa=self.casa))["painel"]

    def test_cada_conta_leva_o_ranking_local_do_claude_e_do_codex(self):
        self.escrever_transcript(
            self.casa / ".claude" / "projects" / "p" / "s.jsonl",
            [
                com_cwd(resposta_claude(carimbo(1200), "m1", "r1", output_tokens=500), ALFA),
                com_cwd(resposta_claude(carimbo(600), "m2", "r2", output_tokens=5), LOOM),
            ],
        )
        self.rollout(
            "a",
            [
                sessao_codex(carimbo(400), LOOM),
                turno(carimbo(300), "t1", "gpt-6-sol"),
                registro_codex(carimbo(290), "r1", "t1", entrada=100, lida=0, saida=5),
                contagem_codex(carimbo(289), 100, 0, 5),
            ],
        )
        contas = {c["ferramenta"]: c for c in json.loads(json.dumps(self.painel()))["contas"]}
        claude = contas["claude"]["local"]
        self.assertEqual([p["projeto"] for p in claude["ranking"]], ["Alfa", "Loom"])
        self.assertGreater(claude["ranking"][0]["tokens"], claude["ranking"][1]["tokens"])
        self.assertEqual(claude["total"], 2)
        self.assertEqual(claude["totais"]["hoje"]["mensagens"], 2)
        codex = contas["codex"]["local"]
        self.assertEqual([p["projeto"] for p in codex["ranking"]], ["Loom"])
        self.assertEqual(codex["ranking"][0]["tokens"], 105)

    def test_os_periodos_separam_hoje_sete_e_trinta_dias(self):
        projetos = {
            "c|2026-10-01|a|claude-opus-5-5": {"entrada": 10, "mensagens": 1},
            "c|2026-09-20|a|claude-opus-5-5": {"entrada": 20, "mensagens": 1},
        }
        local = painel_uso._projetos_da_conta(projetos, {"c"}, date(2026, 10, 1))
        self.assertEqual(local["totais"]["hoje"]["tokens"], 10)
        self.assertEqual(local["totais"]["dias7"]["tokens"], 10)
        self.assertEqual(local["totais"]["dias30"]["tokens"], 30)
        item = local["ranking"][0]
        self.assertEqual((item["tokensHoje"], item["tokens7"], item["tokens"]), (10, 10, 30))

    def test_pasta_movida_de_lugar_soma_num_projeto_so(self):
        antigo = caminho("C:", "Users", "Vini", "Documents", "GitHub", "Lumus-Player")
        novo = caminho("C:", "Users", "Vini", "Documents", "GitHub", "Pessoal", "Lumus-Player")
        projetos = {
            f"c|2026-10-01|{antigo}|claude-opus-5-5": {"entrada": 10, "mensagens": 1},
            f"c|2026-10-01|{novo}|claude-opus-5-5": {"entrada": 30, "mensagens": 2},
        }
        local = painel_uso._projetos_da_conta(projetos, {"c"}, date(2026, 10, 1))
        (item,) = local["ranking"]
        self.assertEqual((item["projeto"], item["tokens"], item["mensagens"]), ("Lumus-Player", 40, 3))
        self.assertEqual(item["caminhos"], sorted([antigo, novo]))
        self.assertEqual(local["total"], 1)

    def test_modelo_sem_preco_marca_o_projeto_e_nao_inventa_custo(self):
        projetos = {"c|2026-10-01|a|modelo-que-nao-existe": {"entrada": 10, "saida": 5, "mensagens": 1}}
        local = painel_uso._projetos_da_conta(projetos, {"c"}, date(2026, 10, 1))
        (item,) = local["ranking"]
        self.assertTrue(item["semPreco"])
        self.assertEqual((item["custo"], item["tokens"]), (0.0, 15))
        self.assertTrue(local["totais"]["hoje"]["semPreco"])

    def test_sem_nenhum_projeto_o_ranking_vem_vazio(self):
        local = painel_uso._projetos_da_conta({}, {"c"}, date(2026, 10, 1))
        self.assertEqual(local["ranking"], [])
        self.assertEqual(local["total"], 0)


if __name__ == "__main__":
    unittest.main()
