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


def com_esforco(registro: dict, esforco: str | None = None, por_turno: str | None = None) -> dict:
    if esforco is not None:
        registro["effort"] = esforco
    if por_turno is not None:
        registro["perTurnEffort"] = por_turno
    return registro


class EsforcoDoClaude(ComPainel):
    def ler(self, registros: list[dict]) -> dict:
        self.escrever_transcript(self.casa / ".claude" / "projects" / "a" / "s.jsonl", registros)
        return uso.coletar_tudo({"__padrao__": self.casa / ".claude"}, cache=self.dados / "uso-cache-v2.json")

    def test_cada_resposta_vai_pro_esforco_que_o_claude_gravou(self):
        lido = self.ler(
            [
                com_esforco(resposta_claude(carimbo(300), "m1", "r1", input_tokens=100, output_tokens=0), "xhigh"),
                com_esforco(resposta_claude(carimbo(200), "m2", "r2", input_tokens=10, output_tokens=0), "xhigh"),
                com_esforco(resposta_claude(carimbo(100), "m3", "r3", input_tokens=1, output_tokens=0), "medium"),
            ]
        )
        (modelo,) = uso.custos_por_modelo(lido["esforcos"], {"__padrao__"}).values()
        self.assertEqual({n: (p["tokens"], p["mensagens"]) for n, p in modelo["esforcos"].items()},
                         {"xhigh": (110, 2), "medium": (1, 1)})
        self.assertEqual((modelo["tokens"], modelo["mensagens"]), (111, 3))

    def test_sem_effort_vale_o_do_turno_e_sem_nenhum_vira_sem_registro(self):
        self.assertEqual(uso.esforco_do_registro({"effort": " High "}), "high")
        self.assertEqual(uso.esforco_do_registro({"perTurnEffort": "max"}), "max")
        self.assertEqual(uso.esforco_do_registro({"effort": "medium", "perTurnEffort": "max"}), "medium")
        self.assertEqual(uso.esforco_do_registro({"effort": None}), uso.SEM_ESFORCO)
        self.assertEqual(uso.esforco_do_registro({"effort": 3}), uso.SEM_ESFORCO)

    def test_total_por_esforco_bate_com_o_total_dos_dias(self):
        lido = self.ler(
            [
                com_esforco(resposta_claude(carimbo(300), "m1", "r1", output_tokens=50), "high"),
                resposta_claude(carimbo(200), "m2", "r2", output_tokens=70),
            ]
        )
        modelo = next(iter(uso.custos_por_modelo(lido["esforcos"], {"__padrao__"}).values()))
        self.assertEqual(set(modelo["esforcos"]), {"high", uso.SEM_ESFORCO})
        self.assertEqual(modelo["tokens"], sum(uso.tokens_do_balde(v) for v in lido["dias"].values()))

    def test_ler_de_novo_nao_soma_o_esforco_duas_vezes(self):
        registros = [com_esforco(resposta_claude(carimbo(300), "m1", "r1", output_tokens=50), "high")]
        self.ler(registros)
        lido = uso.coletar_tudo({"__padrao__": self.casa / ".claude"}, cache=self.dados / "uso-cache-v2.json")
        (modelo,) = uso.custos_por_modelo(lido["esforcos"], {"__padrao__"}).values()
        self.assertEqual(modelo["mensagens"], 1)


class EsforcoDoCodex(ComPainel):
    def contexto(self, quando: str, turno_id: str, modelo: str, esforco: str) -> dict:
        return linha_codex("turn_context", quando, {"turn_id": turno_id, "model": modelo, "effort": esforco})

    def test_o_esforco_de_cada_turno_vai_pra_resposta_dele(self):
        self.rollout(
            "a",
            [
                self.contexto(carimbo(500), "t1", "gpt-6-sol", "ultra"),
                registro_codex(carimbo(490), "r1", "t1", entrada=100, lida=0, saida=0),
                self.contexto(carimbo(300), "t2", "gpt-6-sol", "medium"),
                registro_codex(carimbo(290), "r2", "t2", entrada=10, lida=0, saida=0),
                registro_codex(carimbo(280), "r3", "t1", entrada=1, lida=0, saida=0),
            ],
        )
        (modelo,) = uso.custos_por_modelo(self.ler_codex()["esforcos"], {"padrao"}).values()
        self.assertEqual({n: p["tokens"] for n, p in modelo["esforcos"].items()}, {"ultra": 101, "medium": 10})

    def test_sem_esforco_no_rollout_vira_sem_registro(self):
        self.rollout(
            "a",
            [
                turno(carimbo(300), "t1", "gpt-6-sol"),
                registro_codex(carimbo(290), "r1", "t1", entrada=7, lida=0, saida=0),
            ],
        )
        (modelo,) = uso.custos_por_modelo(self.ler_codex()["esforcos"], {"padrao"}).values()
        self.assertEqual(set(modelo["esforcos"]), {uso.SEM_ESFORCO})

    def test_consumo_tirado_dos_totais_leva_o_esforco_do_turno(self):
        self.rollout(
            "velho",
            [
                self.contexto(carimbo(7200), "t1", "gpt-6-sol", "high"),
                contagem_codex(carimbo(7100), 100, 0, 10),
            ],
            idade=3600,
        )
        (modelo,) = uso.custos_por_modelo(self.ler_codex()["esforcos"], {"padrao"}).values()
        self.assertEqual({n: p["tokens"] for n, p in modelo["esforcos"].items()}, {"high": 110})

    def test_consumo_anterior_ao_primeiro_registro_tambem_leva_o_esforco(self):
        self.rollout(
            "misto",
            [
                self.contexto(carimbo(900), "t1", "gpt-6-sol", "xhigh"),
                contagem_codex(carimbo(800), 100, 0, 10),
                contagem_codex(carimbo(700), 300, 0, 30),
                registro_codex(carimbo(699), "r1", "t1", entrada=200, lida=0, saida=20),
            ],
        )
        (modelo,) = uso.custos_por_modelo(self.ler_codex()["esforcos"], {"padrao"}).values()
        self.assertEqual({n: p["tokens"] for n, p in modelo["esforcos"].items()}, {"xhigh": 330})


class ModelosNoPainel(ComPainel):
    def test_ordena_os_niveis_do_menor_pro_maior_e_deixa_o_sem_registro_no_fim(self):
        modelo = "claude-opus-5-5"
        esforcos = {
            f"c|2026-10-01|{modelo}|max": {"entrada": 10, "mensagens": 1},
            f"c|2026-10-01|{modelo}|sem registro": {"entrada": 10, "mensagens": 1},
            f"c|2026-10-01|{modelo}|low": {"entrada": 10, "mensagens": 1},
            f"c|2026-10-01|{modelo}|xhigh": {"entrada": 20, "mensagens": 1},
        }
        resultado = painel_uso._modelos_da_conta(esforcos, {"c"}, date(2026, 10, 1))
        (item,) = resultado["ranking"]
        self.assertEqual([n["nivel"] for n in item["esforcos"]], ["low", "xhigh", "max", "sem registro"])
        self.assertEqual((item["tokens"], item["mensagens"], item["parte"]), (50, 4, 1.0))

    def test_parte_de_cada_modelo_no_total_e_marca_de_sem_preco(self):
        esforcos = {
            "c|2026-10-01|claude-opus-5-5|high": {"entrada": 30, "mensagens": 1},
            "c|2026-10-01|modelo-sem-preco|high": {"entrada": 10, "mensagens": 1},
        }
        ranking = painel_uso._modelos_da_conta(esforcos, {"c"}, date(2026, 10, 1))["ranking"]
        self.assertEqual([m["modelo"] for m in ranking], ["claude-opus-5-5", "modelo-sem-preco"])
        self.assertEqual([m["parte"] for m in ranking], [0.75, 0.25])
        self.assertEqual([m["semPreco"] for m in ranking], [False, True])

    def test_so_entram_os_ultimos_30_dias_e_os_7_ficam_a_parte(self):
        esforcos = {
            "c|2026-10-01|claude-opus-5-5|high": {"entrada": 10, "mensagens": 1},
            "c|2026-09-20|claude-opus-5-5|high": {"entrada": 20, "mensagens": 1},
            "c|2026-08-01|claude-opus-5-5|high": {"entrada": 40, "mensagens": 1},
        }
        (item,) = painel_uso._modelos_da_conta(esforcos, {"c"}, date(2026, 10, 1))["ranking"]
        self.assertEqual((item["tokens"], item["tokens7"]), (30, 10))

    def test_modelo_sem_nenhum_token_nao_entra_no_ranking(self):
        esforcos = {
            "c|2026-10-01|<synthetic>|sem registro": {"entrada": 0, "saida": 0, "mensagens": 2},
            "c|2026-10-01|claude-opus-5-5|high": {"entrada": 5, "mensagens": 1},
        }
        resultado = painel_uso._modelos_da_conta(esforcos, {"c"}, date(2026, 10, 1))
        self.assertEqual([m["modelo"] for m in resultado["ranking"]], ["claude-opus-5-5"])
        self.assertEqual(resultado["total"], 1)

    def test_sem_dado_o_ranking_vem_vazio(self):
        self.assertEqual(painel_uso._modelos_da_conta({}, {"c"}, date(2026, 10, 1)), {"ranking": [], "total": 0})


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
