"""Painel de uso: precos, medidor com cache de 1 hora e 5 minutos, Codex, relatorio e ponte.

Tudo com transcript, rollout e cadastro escritos pelo proprio teste na pasta temporaria da base.
"""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
import unittest.mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from base import ComPastaPropria
from test_usabilidade import ComPonte

from loom import bridge as mod_bridge
from loom import caminhos, coletor, painel_uso, perfis, precos, uso, uso_codex


def carimbo(segundos_atras: float = 0) -> str:
    """O instante em UTC com Z no fim, como o Claude e o Codex gravam.

    Contado a partir de uma ancora que e agora, menos nos primeiros minutos depois da meia-noite:
    ali "segundos atras" caia em ontem e os testes do que e de hoje falhavam so por causa do relogio.
    """
    agora = datetime.now().astimezone()
    ancora = max(agora, agora.replace(hour=1, minute=0, second=0, microsecond=0))
    return (ancora.astimezone(timezone.utc) - timedelta(seconds=segundos_atras)).isoformat().replace("+00:00", "Z")


def dias_atras(dias: int) -> str:
    return (datetime.now().astimezone().replace(hour=12, minute=0) - timedelta(days=dias)).isoformat()


def resposta_claude(
    quando: str, ident: str, pedido: str | None = "req", modelo: str = "claude-opus-5-5", **consumo
) -> dict:
    uso_bruto = {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 0}
    uso_bruto["cache_read_input_tokens"] = 0
    uso_bruto.update(consumo)
    registro = {
        "type": "assistant",
        "timestamp": quando,
        "message": {"id": ident, "role": "assistant", "model": modelo, "usage": uso_bruto},
    }
    if pedido is not None:
        registro["requestId"] = pedido
    return registro


def linha_codex(tipo: str, quando: str, carga: dict) -> dict:
    return {"timestamp": quando, "type": tipo, "payload": carga}


def turno(quando: str, turno_id: str, modelo: str) -> dict:
    return linha_codex("turn_context", quando, {"turn_id": turno_id, "model": modelo})


def registro_codex(quando: str, resposta: str, turno_id: str, entrada: int, lida: int, saida: int) -> dict:
    consumo = {
        "input_tokens": entrada,
        "cached_input_tokens": lida,
        "cache_write_input_tokens": 0,
        "output_tokens": saida,
        "reasoning_output_tokens": 0,
        "total_tokens": entrada + saida,
    }
    return linha_codex(
        "token_usage_record", quando, {"response_id": resposta, "turn_id": turno_id, "usage": consumo}
    )


def contagem_codex(quando: str, entrada: int, lida: int, saida: int, limites: dict | None = None) -> dict:
    total = {"input_tokens": entrada, "cached_input_tokens": lida, "output_tokens": saida}
    total["total_tokens"] = entrada + saida
    return linha_codex(
        "event_msg", quando, {"type": "token_count", "info": {"total_token_usage": total}, "rate_limits": limites}
    )


def limite_semanal(usado: float, zera: float) -> dict:
    return {
        "limit_id": "codex",
        "primary": {"used_percent": usado, "window_minutes": 10080, "resets_at": int(zera)},
        "secondary": None,
        "plan_type": "pro",
    }


CHAVES_DA_CONTA = {"id", "ferramenta", "nome", "plano", "email", "atualizadoEm"}
CHAVES_DA_CONTA |= {"janelas", "bloco", "custo", "tokens", "serie", "local", "erro"}
CHAVES_DA_JANELA = {"tipo", "rotulo", "usado", "zeraEm", "duracaoMin", "origem", "lidoEm"}
CHAVES_DO_BLOCO = {"inicio", "fim", "tokens", "custo", "porMinuto", "projecaoCusto", "estimado"}


def com_fuso(texto: str) -> bool:
    return datetime.fromisoformat(texto).tzinfo is not None


class ComPainel(ComPastaPropria):
    """A base, com a pasta do Codex e a do DBX Term dentro da casa temporaria."""

    def setUp(self) -> None:
        super().setUp()
        precos.recarregar()
        self.dados = caminhos.RAIZ

    def tearDown(self) -> None:
        precos.recarregar()
        super().tearDown()

    def rollout(self, nome: str, linhas: list[dict], idade: float = 0) -> Path:
        alvo = self.casa / ".codex" / "sessions" / "2026" / "09" / "28" / f"rollout-{nome}.jsonl"
        self.escrever_transcript(alvo, linhas)
        if idade:
            antes = time.time() - idade
            os.utime(alvo, (antes, antes))
        return alvo

    def ler_codex(self, chave: str = "padrao") -> dict:
        return uso_codex.coletar(self.casa / ".codex", chave, cache=self.dados / "uso-codex.json")


# ---------------------------------------------------------------- precos


class PrecoDosModelos(ComPainel):
    def test_nome_do_modelo_sem_data_colchete_e_prefixo(self):
        casos = {
            "claude-opus-5-5[1m]": "claude-opus-5-5",
            "anthropic/claude-sonnet-4-5-20250929": "claude-sonnet-4-5",
            "us.anthropic.claude-opus-4-5-20251101-v1:0": "claude-opus-4-5",
            "openai/gpt-5.4-2026-03-05": "gpt-5.4",
            "GPT-6-SOL": "gpt-6-sol",
        }
        for bruto, esperado in casos.items():
            self.assertEqual(precos.normalizar(bruto), esperado, bruto)
        self.assertTrue(precos.custo("claude-opus-5-5[1m]", {"saida": 1})[1])

    def test_escrita_de_uma_hora_custa_mais_que_a_de_cinco_minutos(self):
        milhao = 1_000_000
        tudo, com_preco = precos.custo(
            "claude-opus-5-5",
            {"entrada": milhao, "saida": milhao, "cache5m": milhao, "cache1h": milhao, "cacheLido": milhao},
        )
        self.assertTrue(com_preco)
        # tabela: 4 de entrada, 20 de saida, 5 e 8 na escrita de cache, 0,2 na leitura, por milhao
        self.assertAlmostEqual(tudo, 37.2, places=6)
        self.assertAlmostEqual(precos.custo("claude-opus-5-5", {"cache1h": milhao})[0], 8.0, places=6)
        self.assertAlmostEqual(precos.custo("claude-opus-5-5", {"cache5m": milhao})[0], 5.0, places=6)

    def test_modelo_fora_da_tabela_nao_inventa_preco(self):
        self.assertEqual(precos.custo("codex-auto-review", {"entrada": 1000, "saida": 10}), (0.0, False))
        self.assertEqual(precos.custo("<synthetic>", {}), (0.0, False))

    def test_arquivo_da_pasta_do_loom_vale_por_cima_campo_a_campo(self):
        (self.dados).mkdir(parents=True, exist_ok=True)
        (self.dados / "precos.json").write_text(
            json.dumps(
                {
                    "modelos": {
                        "claude-opus-5-5": {"output_cost_per_token": 1e-4},
                        "modelo-novo": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6},
                    }
                }
            ),
            encoding="utf-8",
        )
        milhao = 1_000_000
        self.assertAlmostEqual(precos.custo("claude-opus-5-5", {"saida": milhao})[0], 100.0, places=6)
        # o que o arquivo nao traz continua vindo da tabela do Loom
        self.assertAlmostEqual(precos.custo("claude-opus-5-5", {"entrada": milhao})[0], 4.0, places=6)
        self.assertEqual(precos.custo("modelo-novo", {"entrada": milhao, "saida": milhao}), (3.0, True))

    def test_pedido_acima_do_limiar_usa_a_faixa_de_contexto_longo(self):
        consumo = {"entrada": 300_000, "saida": 1_000}
        normal, _ = precos.custo("gpt-6-sol", consumo)
        longo, _ = precos.custo("gpt-6-sol", consumo, longo=consumo)
        self.assertAlmostEqual(normal, 300_000 * 2e-6 + 1_000 * 1e-5, places=9)
        self.assertAlmostEqual(longo, 300_000 * 4e-6 + 1_000 * 1.5e-5, places=9)


# ---------------------------------------------------------------- medidor do Claude


class MedidorComCacheSeparado(ComPainel):
    def setUp(self) -> None:
        super().setUp()
        self.conta = self.raiz / "conta"
        self.pastas = {"c1": self.conta}

    def transcript(self, registros: list[dict], nome: str = "s") -> Path:
        return self.escrever_transcript(self.conta / "projects" / "p" / f"{nome}.jsonl", registros)

    def test_escrita_de_cache_de_uma_hora_e_de_cinco_minutos_ficam_separadas(self):
        self.transcript(
            [
                resposta_claude(
                    carimbo(60),
                    "m1",
                    cache_creation_input_tokens=400,
                    cache_creation={"ephemeral_1h_input_tokens": 300, "ephemeral_5m_input_tokens": 100},
                )
            ]
        )
        lido = uso.coletar_tudo(self.pastas)
        (dia,) = lido["dias"].values()
        self.assertEqual((dia["cacheEscrito"], dia["cache1h"], dia["cache5m"]), (400, 300, 100))
        (hora,) = lido["horas"].values()
        self.assertEqual((hora["cache1h"], hora["cache5m"]), (300, 100))
        custo = uso.custos_por_dia(lido["dias"], {"c1"})
        (modelo,) = next(iter(custo.values())).values()
        esperado = 10 * 4e-6 + 20 * 2e-5 + 300 * 8e-6 + 100 * 5e-6
        self.assertAlmostEqual(modelo["custo"], esperado, places=12)

    def test_balde_por_hora_so_dos_ultimos_oito_dias(self):
        self.transcript([resposta_claude(carimbo(60), "hoje"), resposta_claude(dias_atras(10), "velha")])
        lido = uso.coletar_tudo(self.pastas)
        self.assertEqual(len(lido["dias"]), 2)
        self.assertEqual(len(lido["horas"]), 1)
        (hora,) = lido["horas"].values()
        self.assertGreater(hora["fim"], time.time() - 3600)

    def test_migracao_guarda_o_dia_que_o_claude_apagou_como_aproximado(self):
        """O cache antigo fica intocado; o dia com transcript e relido, o apagado vira aproximado."""
        hoje = date.today().isoformat()
        apagado = (date.today() - timedelta(days=3)).isoformat()
        antigo = self.dados / "uso-cache.json"
        antigo.parent.mkdir(parents=True, exist_ok=True)
        conteudo_antigo = json.dumps(
            {
                "arquivos": {},
                "dias": {
                    f"c1|{hoje}|claude-opus-5-5": {"entrada": 10, "saida": 20, "cacheEscrito": 400, "mensagens": 1},
                    f"c1|{apagado}|claude-opus-5-5": {"entrada": 1, "saida": 700, "cacheEscrito": 90, "mensagens": 3},
                },
            }
        )
        antigo.write_text(conteudo_antigo, encoding="utf-8")
        self.transcript(
            [
                resposta_claude(
                    carimbo(60),
                    "m1",
                    cache_creation_input_tokens=400,
                    cache_creation={"ephemeral_1h_input_tokens": 400, "ephemeral_5m_input_tokens": 0},
                )
            ]
        )
        novo = self.dados / "uso-cache-v2.json"

        dias = uso.coletar_tudo(self.pastas, cache=novo)["dias"]

        self.assertEqual(dias[f"c1|{hoje}|claude-opus-5-5"]["cache1h"], 400)
        self.assertNotIn("aproximado", dias[f"c1|{hoje}|claude-opus-5-5"])
        self.assertTrue(dias[f"c1|{apagado}|claude-opus-5-5"]["aproximado"])
        conta = uso.montar_relatorio(dias)["c1"]
        self.assertEqual((conta["total"]["saida"], conta["total"]["mensagens"]), (720, 4))
        self.assertEqual(antigo.read_text(encoding="utf-8"), conteudo_antigo)
        # e o legado do dia apagado continua la na proxima abertura, que ja le a versao 2
        de_novo = uso.coletar_tudo(self.pastas, cache=novo)["dias"]
        self.assertTrue(de_novo[f"c1|{apagado}|claude-opus-5-5"]["aproximado"])
        custo = uso.custos_por_dia(dias, {"c1"})
        self.assertTrue(custo[apagado]["claude-opus-5-5"]["aproximado"])
        # o dia aproximado reparte a escrita pela proporcao dos dias novos, aqui toda de 1 hora
        self.assertAlmostEqual(custo[apagado]["claude-opus-5-5"]["custo"], 1 * 4e-6 + 700 * 2e-5 + 90 * 8e-6, places=12)

    def test_repeticao_e_pelo_id_da_mensagem_mais_o_do_pedido(self):
        self.transcript(
            [
                resposta_claude(carimbo(90), "m1", "r1", output_tokens=100),
                resposta_claude(carimbo(89), "m1", "r1", output_tokens=100),
                resposta_claude(carimbo(88), "m1", "r2", output_tokens=7),
            ]
        )
        (dia,) = uso.coletar_tudo(self.pastas)["dias"].values()
        self.assertEqual((dia["saida"], dia["mensagens"]), (107, 2))

    def test_conta_so_do_painel_fica_fora_da_aba_uso(self):
        hoje = date.today().isoformat()
        dias = {
            f"c1|{hoje}|claude-opus-5-5": {"saida": 5, "mensagens": 1},
            f"dbx-term:x|{hoje}|claude-opus-5": {"saida": 9, "mensagens": 1},
        }
        self.assertEqual(set(uso.montar_relatorio(dias)), {"c1"})


# ---------------------------------------------------------------- Codex


class UsoDoCodex(ComPainel):
    def test_registro_por_resposta_com_o_modelo_do_turno_e_sem_repetir(self):
        self.rollout(
            "a",
            [
                turno(carimbo(300), "t1", "gpt-6-sol"),
                registro_codex(carimbo(290), "r1", "t1", entrada=1000, lida=400, saida=50),
                registro_codex(carimbo(290), "r1", "t1", entrada=1000, lida=400, saida=50),
                contagem_codex(carimbo(289), 1000, 400, 50),
            ],
        )
        lido = self.ler_codex()
        (chave, dia), = lido["dias"].items()
        self.assertTrue(chave.endswith("|gpt-6-sol"))
        self.assertEqual((dia["entrada"], dia["cacheLido"], dia["saida"], dia["mensagens"]), (600, 400, 50, 1))
        (modelo,) = next(iter(uso.custos_por_dia(lido["dias"], {"padrao"}).values())).values()
        self.assertAlmostEqual(modelo["custo"], 600 * 2e-6 + 400 * 2e-7 + 50 * 1e-5, places=12)

    def test_rollout_antigo_sem_registro_conta_pela_diferenca_dos_totais(self):
        linhas = [
            turno(carimbo(7200), "t1", "gpt-6-sol"),
            contagem_codex(carimbo(7100), 100, 0, 10),
            contagem_codex(carimbo(7090), 100, 0, 10),
            contagem_codex(carimbo(7000), 250, 50, 30),
        ]
        self.rollout("velho", linhas, idade=3600)
        (dia,) = self.ler_codex()["dias"].values()
        self.assertEqual((dia["entrada"], dia["cacheLido"], dia["saida"], dia["mensagens"]), (200, 50, 30, 2))

    def test_limite_semanal_e_plano_vem_do_token_count(self):
        zera = time.time() + 2 * 24 * 3600
        self.rollout(
            "a",
            [
                turno(carimbo(300), "t1", "gpt-6-sol"),
                registro_codex(carimbo(290), "r1", "t1", entrada=100, lida=0, saida=5),
                contagem_codex(carimbo(289), 100, 0, 5, limite_semanal(49.0, zera)),
            ],
        )
        conta = self.painel_do_codex()
        self.assertEqual(conta["plano"], "pro")
        (janela,) = conta["janelas"]
        self.assertEqual((janela["tipo"], janela["rotulo"], janela["origem"]), ("7d", "Semana", "real"))
        self.assertEqual((janela["usado"], janela["duracaoMin"]), (49.0, 10080))
        self.assertAlmostEqual(datetime.fromisoformat(janela["zeraEm"]).timestamp(), zera, delta=1)

    def test_modelo_sem_preco_conta_zero_e_marca_sem_preco(self):
        self.rollout(
            "a",
            [
                turno(carimbo(400), "t1", "codex-auto-review"),
                registro_codex(carimbo(390), "r1", "t1", entrada=5000, lida=0, saida=100),
                turno(carimbo(300), "t2", "gpt-6-sol"),
                registro_codex(carimbo(290), "r2", "t2", entrada=1000, lida=0, saida=0),
            ],
        )
        conta = self.painel_do_codex()
        self.assertTrue(conta["custo"]["semPreco"])
        self.assertAlmostEqual(conta["custo"]["hoje"], 1000 * 2e-6, places=6)
        self.assertEqual(conta["tokens"]["hoje"], 6100)
        self.assertEqual(conta["serie"][-1]["porModelo"], {"gpt-6-sol": 0.002, "codex-auto-review": 0.0})

    def painel_do_codex(self) -> dict:
        pedido = painel_uso.pedido_do_painel([], casa=self.casa)
        contas = {conta["id"]: conta for conta in painel_uso.coletar(pedido)["painel"]["contas"]}
        return contas["codex:padrao"]


# ---------------------------------------------------------------- relatorio do painel


class RelatorioDoPainel(ComPainel):
    def leitura_de_limite(self, perfil: str, cinco_horas: float, semana: float) -> None:
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        faixas = {
            "five_hour": {"usado": 12, "zera": int(cinco_horas)},
            "seven_day": {"usado": 16, "zera": int(semana)},
        }
        (coletor.PASTA / "s1.json").write_text(
            json.dumps({"sessao": "s1", "perfil": perfil, "lido": time.time() - 30, "faixas": faixas}), encoding="utf-8"
        )

    def conversa_da_principal(self, registros: list[dict]) -> None:
        self.escrever_transcript(self.casa / ".claude" / "projects" / "p" / "s.jsonl", registros)

    def painel(self) -> dict:
        return painel_uso.coletar(painel_uso.pedido_do_painel(perfis.listar(), casa=self.casa))["painel"]

    def test_formato_combinado_com_a_tela(self):
        agora = time.time()
        self.leitura_de_limite("__padrao__", agora + 2 * 3600, agora + 3 * 24 * 3600)
        self.conversa_da_principal(
            [resposta_claude(carimbo(1200), "m1", "r1", output_tokens=500), resposta_claude(carimbo(600), "m2", "r2")]
        )
        self.rollout(
            "a",
            [
                turno(carimbo(300), "t1", "gpt-6-sol"),
                registro_codex(carimbo(290), "r1", "t1", entrada=100, lida=0, saida=5),
                contagem_codex(carimbo(289), 100, 0, 5, limite_semanal(85.0, agora + 86400)),
            ],
        )

        relatorio = json.loads(json.dumps(self.painel()))

        self.assertEqual(set(relatorio), {"geradoEm", "coletando", "contas"})
        self.assertTrue(com_fuso(relatorio["geradoEm"]))
        self.assertIs(relatorio["coletando"], False)
        self.assertEqual([c["id"] for c in relatorio["contas"]], ["claude:padrao", "codex:padrao"])
        for conta in relatorio["contas"]:
            self.assertEqual(set(conta), CHAVES_DA_CONTA)
            self.assertTrue(com_fuso(conta["atualizadoEm"]))
            self.assertIsNone(conta["erro"])
            self.assertEqual(set(conta["custo"]), {"hoje", "dias30", "semPreco", "aproximado"})
            self.assertEqual(set(conta["tokens"]), {"hoje", "dias30"})
            self.assertEqual(len(conta["serie"]), 30)
            self.assertEqual([p["dia"] for p in conta["serie"]], sorted(p["dia"] for p in conta["serie"]))
            self.assertEqual(conta["serie"][-1]["dia"], date.today().isoformat())
            for ponto in conta["serie"]:
                self.assertEqual(set(ponto), {"dia", "custo", "tokens", "porModelo"})
            for janela in conta["janelas"]:
                self.assertEqual(set(janela), CHAVES_DA_JANELA)
                self.assertTrue(com_fuso(janela["zeraEm"]) and com_fuso(janela["lidoEm"]))
            self.assertEqual(set(conta["bloco"]), CHAVES_DO_BLOCO)
            self.assertTrue(com_fuso(conta["bloco"]["inicio"]) and com_fuso(conta["bloco"]["fim"]))

        claude, codex = relatorio["contas"]
        self.assertEqual(
            [(j["tipo"], j["rotulo"], j["usado"], j["duracaoMin"], j["origem"]) for j in claude["janelas"]],
            [("5h", "Sessao", 12.0, 300, "real"), ("7d", "Semana", 16.0, 10080, "real")],
        )
        self.assertEqual(claude["tokens"]["hoje"], 30 + 510)
        self.assertEqual(claude["serie"][-1]["porModelo"], {"claude-opus-5-5": claude["custo"]["hoje"]})
        self.assertEqual((claude["custo"]["semPreco"], claude["custo"]["aproximado"]), (False, False))
        self.assertEqual(sum(p["tokens"] for p in claude["serie"]), claude["tokens"]["dias30"])
        self.assertEqual([(j["tipo"], j["usado"]) for j in codex["janelas"]], [("7d", 85.0)])

    def duas_respostas_na_ultima_meia_hora(self) -> None:
        self.conversa_da_principal([resposta_claude(carimbo(1800), "m1", "r1"), resposta_claude(carimbo(60), "m2", "r2")])

    def test_sem_leitura_da_status_line_a_sessao_vem_do_bloco_do_ccusage(self):
        self.duas_respostas_na_ultima_meia_hora()

        (conta,) = self.painel()["contas"]

        bloco = conta["bloco"]
        self.assertTrue(bloco["estimado"])
        inicio = datetime.fromisoformat(bloco["inicio"])
        self.assertEqual((inicio.minute, inicio.second), (0, 0))
        self.assertEqual(datetime.fromisoformat(bloco["fim"]) - inicio, timedelta(hours=5))
        self.assertEqual(bloco["tokens"], 60)
        self.assertGreater(bloco["porMinuto"], 0)
        (janela,) = conta["janelas"]
        self.assertEqual((janela["tipo"], janela["origem"], janela["usado"]), ("5h", "estimada", None))
        self.assertEqual(janela["zeraEm"], bloco["fim"])

    def test_com_a_janela_real_o_bloco_e_ela(self):
        agora = time.time()
        self.leitura_de_limite("__padrao__", agora + 2 * 3600, agora + 3 * 24 * 3600)
        self.duas_respostas_na_ultima_meia_hora()

        (conta,) = self.painel()["contas"]

        bloco = conta["bloco"]
        self.assertFalse(bloco["estimado"])
        self.assertAlmostEqual(datetime.fromisoformat(bloco["fim"]).timestamp(), agora + 2 * 3600, delta=1)
        self.assertAlmostEqual(datetime.fromisoformat(bloco["inicio"]).timestamp(), agora - 3 * 3600, delta=1)
        self.assertEqual([j["origem"] for j in conta["janelas"]], ["real", "real"])

    def test_conta_do_dbx_term_entra_so_de_pasta_propria_e_existente(self):
        dbx = self.casa / ".dbx-term"
        propria = dbx / "perfis" / "cdacecf8"
        (propria / "projects").mkdir(parents=True)
        (dbx / "perfis.json").write_text(
            json.dumps(
                [
                    {"id": "cdacecf8", "nome": "Conta B", "cor": "#fff", "ferramenta": "claude", "pasta": ""},
                    {"id": "c9c8b7ca", "nome": "A de sempre", "ferramenta": "claude", "pasta": str(self.casa / ".claude")},
                    {"id": "sumida00", "nome": "Sumiu", "cor": "#fff", "ferramenta": "claude", "pasta": ""},
                ]
            ),
            encoding="utf-8",
        )
        (dbx / "identidade.json").write_text(
            json.dumps({str(propria): {"carimbo": 1.0, "resumo": {"email": "b@exemplo.com", "plano": "pro"}}}),
            encoding="utf-8",
        )

        contas = painel_uso.contas_do_painel([], casa=self.casa)

        self.assertEqual([c["id"] for c in contas], ["claude:padrao", "claude:dbx-term-cdacecf8"])
        dbx_term = contas[1]
        self.assertEqual(dbx_term["nome"], "Conta B · DBX Term")
        self.assertEqual((dbx_term["plano"], dbx_term["email"]), ("pro", "b@exemplo.com"))
        self.assertEqual(dbx_term["chaves"], ["dbx-term:cdacecf8"])


# ---------------------------------------------------------------- ponte


def esperar(condicao, limite: float = 10.0) -> bool:
    from PySide6.QtCore import QCoreApplication

    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        QCoreApplication.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


class PainelNaPonte(ComPonte):
    def test_painel_responde_na_hora_e_o_novo_chega_pelo_sinal(self):
        pronto = {"geradoEm": "2026-09-28T21:30:00-03:00", "coletando": False, "contas": []}
        chegou: list[tuple[dict, bool]] = []
        self.ponte.painelUsoPronto.connect(
            lambda texto: chegou.append((json.loads(texto), threading.current_thread() is threading.main_thread()))
        )

        def devagar(pedido, parar=None):
            time.sleep(1.0)
            return {"painel": dict(pronto)}

        pedido = {"painel": True, "contas": [], "limites": {}, "cacheUso": uso.CACHE, "raiz": caminhos.RAIZ}
        with (
            unittest.mock.patch.object(mod_bridge.mod_painel_uso, "pedido_do_painel", return_value=pedido),
            unittest.mock.patch.object(mod_bridge.mod_painel_uso, "coletar", side_effect=devagar),
        ):
            inicio = time.monotonic()
            primeiro = json.loads(self.ponte.getPainelUso())
            gasto = time.monotonic() - inicio
            self.assertTrue(esperar(lambda: chegou))

        self.assertLess(gasto, 0.3)
        self.assertEqual(primeiro, {"geradoEm": None, "coletando": True, "contas": []})
        self.assertEqual(chegou, [(pronto, True)])
        self.assertEqual(json.loads(self.ponte.getPainelUso())["geradoEm"], pronto["geradoEm"])

    def test_getusage_nao_le_na_thread_da_tela_e_mantem_o_formato(self):
        self.escrever_transcript(self.casa / "projects" / "p" / "s.jsonl", [resposta_claude(carimbo(60), "m1")])
        with unittest.mock.patch.object(uso, "pastas_de", return_value={"c1": self.casa}):
            inicio = time.monotonic()
            primeira = json.loads(self.ponte.getUsage())
            gasto = time.monotonic() - inicio
            self.assertTrue(esperar(lambda: self.ponte._estado_do_uso()["uso"] is not None))
            segunda = json.loads(self.ponte.getUsage())

        self.assertEqual(primeira, {"porConta": {}, "parcial": True})
        self.assertLess(gasto, 0.3)
        self.assertEqual(set(segunda), {"porConta", "parcial"})
        self.assertIs(segunda["parcial"], False)
        self.assertEqual(segunda["porConta"]["c1"]["hoje"]["mensagens"], 1)


if __name__ == "__main__":
    unittest.main()
