"""O que a conta gastou e este computador nao explica: leituras de percentual, uso por balde, piso e painel.

Os testes puros usam numeros redondos. Os de painel escrevem rollout, transcript e leitura da status
line na pasta temporaria da base.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

from test_dados_do_painel import (
    ComPainel,
    carimbo,
    contagem_codex,
    limite_semanal,
    registro_codex,
    resposta_claude,
    turno,
)

from loom import caminhos, coletor, painel_uso, perfis, uso, uso_codex, uso_fora

SEMANA = 10080
# um multiplo do passo, pros baldes dos testes puros ficarem em lugares previsiveis
T0 = 1_200_000.0
DIA = 86400.0


def janela_com(minutos: int, zera: float, leituras: list[tuple[float, float]]) -> dict:
    janelas: dict = {}
    for pct, instante in leituras:
        uso_fora.registrar_leitura(janelas, minutos, zera, pct, instante)
    (janela,) = janelas.values()
    return janela


def balde(instante: float) -> str:
    return str(int(instante // uso_fora.PASSO))


def agora_do_teste() -> float:
    return datetime.fromisoformat(carimbo(0).replace("Z", "+00:00")).timestamp()


def instante_de(segundos_atras: float) -> float:
    return datetime.fromisoformat(carimbo(segundos_atras).replace("Z", "+00:00")).timestamp()


# ----------------------------------------------------------------------- puros


class LeiturasDoPercentual(unittest.TestCase):
    def test_guarda_a_primeira_e_a_ultima_vez_de_cada_valor(self):
        janelas: dict = {}
        self.assertTrue(uso_fora.registrar_leitura(janelas, SEMANA, 1000.0, 5.0, 100))
        self.assertTrue(uso_fora.registrar_leitura(janelas, SEMANA, 1000.0, 5.0, 300))
        self.assertTrue(uso_fora.registrar_leitura(janelas, SEMANA, 1000.0, 5.0, 50))
        self.assertFalse(uso_fora.registrar_leitura(janelas, SEMANA, 1000.0, 5.0, 200))
        (janela,) = janelas.values()
        self.assertEqual(janela["leituras"], {"5": [50.0, 300.0]})

    def test_o_horario_de_reinicio_que_varia_em_segundos_e_a_mesma_janela(self):
        janelas: dict = {}
        uso_fora.registrar_leitura(janelas, SEMANA, 1000.0, 5.0, 100)
        uso_fora.registrar_leitura(janelas, SEMANA, 1030.0, 6.0, 200)
        self.assertEqual(len(janelas), 1)
        uso_fora.registrar_leitura(janelas, SEMANA, 5000.0, 1.0, 300)
        uso_fora.registrar_leitura(janelas, 300, 1000.0, 1.0, 300)
        self.assertEqual(len(janelas), 3)

    def test_leitura_fora_do_formato_nao_entra(self):
        janelas: dict = {}
        casos = [
            (True, 1000.0, 5.0, 1.0),
            (0, 1000.0, 5.0, 1.0),
            (SEMANA, None, 5.0, 1.0),
            (SEMANA, 1000.0, None, 1.0),
            (SEMANA, 1000.0, -1.0, 1.0),
            (SEMANA, 1000.0, 300.0, 1.0),
            (SEMANA, 1000.0, float("nan"), 1.0),
            (SEMANA, 1000.0, 5.0, None),
            (SEMANA, 1000.0, "5", 1.0),
        ]
        for minutos, zera, usado, instante in casos:
            self.assertFalse(uso_fora.registrar_leitura(janelas, minutos, zera, usado, instante), (minutos, zera, usado))
        self.assertEqual(janelas, {})

    def test_sem_acompanhar_a_ultima_so_valor_novo_ou_mais_antigo_muda(self):
        janelas: dict = {}
        self.assertTrue(uso_fora.registrar_leitura(janelas, 300, 1000.0, 5.0, 100, acompanhar_ultima=False))
        self.assertFalse(uso_fora.registrar_leitura(janelas, 300, 1000.0, 5.0, 900, acompanhar_ultima=False))
        self.assertTrue(uso_fora.registrar_leitura(janelas, 300, 1000.0, 5.0, 40, acompanhar_ultima=False))
        (janela,) = janelas.values()
        self.assertEqual(janela["leituras"], {"5": [40.0, 100.0]})

    def test_juntar_pega_a_primeira_mais_antiga_e_a_ultima_mais_nova(self):
        uma = janela_com(SEMANA, 1000.0, [(5.0, 100), (5.0, 200)])
        outra = janela_com(SEMANA, 1010.0, [(5.0, 50), (5.0, 150), (6.0, 400)])
        juntas = uso_fora.juntar_janelas([{"a": uma}, {"b": outra}, "lixo", {"c": "lixo"}])
        (janela,) = juntas.values()
        self.assertEqual(janela["leituras"], {"5": [50.0, 200.0], "6": [400.0, 400.0]})

    def test_a_janela_velha_demais_sai_do_que_se_guarda(self):
        agora = 100 * DIA
        velha = janela_com(SEMANA, agora - 70 * DIA, [(5.0, 1.0)])
        nova = janela_com(SEMANA, agora - 10 * DIA, [(5.0, 1.0)])
        mantidas = uso_fora.podar_janelas({"v": velha, "n": nova, "x": "lixo"}, agora)
        self.assertEqual(list(mantidas), ["n"])

    def test_abertura_e_o_reinicio_menos_a_duracao(self):
        janela = janela_com(SEMANA, 1_000_000.0, [(1.0, 5.0)])
        self.assertEqual(uso_fora.abertura_da_janela(janela), 1_000_000.0 - SEMANA * 60)
        self.assertIsNone(uso_fora.abertura_da_janela({"min": SEMANA}))


class UsoDaquiNoTempo(unittest.TestCase):
    def test_soma_no_balde_de_5_minutos_e_poda_o_velho(self):
        tempo: dict = {}
        uso_fora.somar_tempo(tempo, T0 + 10, 100)
        uso_fora.somar_tempo(tempo, T0 + 299, 50)
        uso_fora.somar_tempo(tempo, T0 + 300, 7)
        uso_fora.somar_tempo(tempo, T0, 0)
        self.assertEqual(tempo, {balde(T0): 150, balde(T0 + 300): 7})
        agora = T0 + 100 * DIA
        uso_fora.somar_tempo(tempo, agora, 1)
        self.assertEqual(list(uso_fora.podar_tempo(tempo, agora)), [balde(agora)])

    def test_baldes_da_conta_separa_por_prefixo(self):
        tempo = {"a|10": 5, "b|10": 7, "a|11": 1}
        self.assertEqual(uso_fora.baldes_da_conta(tempo, {"a"}), {"10": 5, "11": 1})
        self.assertEqual(uso_fora.baldes_da_conta(tempo, {"a", "b"}), {"10": 12, "11": 1})
        self.assertEqual(uso_fora.baldes_da_conta({"10": 5, "11": 1}), {"10": 5, "11": 1})

    def test_entre_conta_o_balde_da_ponta_inteiro_e_ignora_o_resto(self):
        linha = uso_fora.LinhaDoTempo({balde(T0): 10, balde(T0 + 300): 20, balde(T0 + 900): 40, "lixo": 5})
        self.assertEqual(linha.entre(T0 + 100, T0 + 350), 30)
        self.assertEqual(linha.entre(T0 + 350, T0 + 100), 30)
        self.assertEqual(linha.entre(T0 + 600, T0 + 800), 0)
        self.assertEqual(linha.entre(T0 - 5000, T0 + 5000), 70)

    def test_primeiro_depois_acha_o_primeiro_balde_a_partir_do_instante(self):
        linha = uso_fora.LinhaDoTempo({balde(T0 + 900): 40, balde(T0 + 1500): 1})
        self.assertEqual(linha.primeiro_depois(T0), T0 + 900)
        self.assertEqual(linha.primeiro_depois(T0 + 1000), T0 + 900)
        self.assertEqual(linha.primeiro_depois(T0 + 1200), T0 + 1500)
        self.assertIsNone(linha.primeiro_depois(T0 + 5000))


class PisoDoQueVeioDeFora(unittest.TestCase):
    ZERA = T0 + SEMANA * 60

    def aberta(self) -> float:
        return uso_fora.abertura_da_janela({"min": SEMANA, "zera": self.ZERA})

    def test_janela_que_ja_estava_alta_quando_o_uso_daqui_comecou(self):
        janela = janela_com(SEMANA, self.ZERA, [(48.0, T0 + 2 * DIA), (49.0, T0 + 2 * DIA + 600)])
        linha = uso_fora.LinhaDoTempo({balde(T0 + 2 * DIA): 30_000, balde(T0 + 2 * DIA + 600): 5_000_000})
        resumo = uso_fora.resumir(janela, linha, 5_000_000, self.aberta(), True)
        self.assertEqual(resumo["foraMinimo"], 47.0)
        self.assertEqual((resumo["chegouEm"], resumo["final"]), (48.0, 49.0))
        self.assertEqual(resumo["primeiroUsoAqui"], T0 + 2 * DIA - (T0 + 2 * DIA) % uso_fora.PASSO)
        self.assertEqual(resumo["abertaEm"], T0)
        self.assertEqual(resumo["saltos"][0]["pts"], 47.0)

    def test_sem_proporcao_medida_nao_afirma_nada_mas_guarda_os_fatos(self):
        janela = janela_com(SEMANA, self.ZERA, [(48.0, T0 + 2 * DIA)])
        resumo = uso_fora.resumir(janela, uso_fora.LinhaDoTempo({}), None, self.aberta(), True)
        self.assertIsNone(resumo["foraMinimo"])
        self.assertEqual((resumo["chegouEm"], resumo["saltos"]), (48.0, []))

    def test_trabalho_continuo_nao_gera_consumo_de_fora(self):
        leituras = [(float(pct), T0 + 600 * pct) for pct in range(1, 11)]
        janela = janela_com(SEMANA, self.ZERA, leituras)
        linha = uso_fora.LinhaDoTempo({balde(instante): 6_000_000 for _, instante in leituras})
        razao = uso_fora.proporcao([janela], linha, SEMANA, lambda _: None, True)
        self.assertEqual(razao, 12_000_000)
        resumo = uso_fora.resumir(janela, linha, razao, None, True)
        self.assertEqual(resumo["foraMinimo"], 0.0)

    def test_salto_depois_de_pausa_com_pouco_uso_daqui_vira_consumo_de_fora(self):
        janela = janela_com(SEMANA, self.ZERA, [(10.0, T0), (20.0, T0 + 16 * 3600)])
        linha = uso_fora.LinhaDoTempo({balde(T0 + 8 * 3600): 1_000_000})
        resumo = uso_fora.resumir(janela, linha, 6_000_000, None, True)
        self.assertEqual(resumo["foraMinimo"], 8.7)
        (salto,) = resumo["saltos"]
        self.assertEqual((salto["pts"], salto["delta"], salto["tokens"]), (8.7, 10.0, 1_000_000))

    def test_subir_um_ponto_sem_uso_nenhum_cabe_no_arredondamento(self):
        janela = janela_com(SEMANA, self.ZERA, [(10.0, T0), (11.0, T0 + 16 * 3600)])
        resumo = uso_fora.resumir(janela, uso_fora.LinhaDoTempo({}), 6_000_000, None, True)
        self.assertEqual(resumo["foraMinimo"], 0.0)

    def test_o_uso_daqui_que_explica_a_subida_tira_o_consumo_de_fora(self):
        janela = janela_com(SEMANA, self.ZERA, [(10.0, T0), (20.0, T0 + 16 * 3600)])
        linha = uso_fora.LinhaDoTempo({balde(T0 + 8 * 3600): 60_000_000})
        self.assertEqual(uso_fora.resumir(janela, linha, 6_000_000, None, True)["foraMinimo"], 0.0)

    def test_intervalo_apertado_afirma_mais_que_o_largo(self):
        janela = janela_com(
            SEMANA, self.ZERA, [(10.0, T0), (10.0, T0 + 3600), (20.0, T0 + 3600 + 16 * 3600)]
        )
        linha = uso_fora.LinhaDoTempo({balde(T0 + 1800): 5_000_000, balde(T0 + 3600 + 16 * 3600): 100_000})
        apertado = uso_fora.resumir(janela, linha, 6_000_000, None, True)["foraMinimo"]
        largo = uso_fora.resumir(janela, linha, 6_000_000, None, False)["foraMinimo"]
        self.assertEqual((apertado, largo), (9.0, 7.3))

    def test_proporcao_vale_a_menor_das_janelas_com_pontos_suficientes(self):
        def continua(inicio: float, por_balde: int, pontos: int):
            leituras = [(float(p), inicio + 600 * p) for p in range(1, pontos + 1)]
            return janela_com(SEMANA, inicio + 1e6, leituras), {balde(i): por_balde for _, i in leituras}

        larga, tokens_larga = continua(T0, 6_000_000, 10)
        estreita, tokens_estreita = continua(T0 + 30 * DIA, 2_000_000, 6)
        curta, tokens_curta = continua(T0 + 60 * DIA, 100_000, 3)
        linha = uso_fora.LinhaDoTempo({**tokens_larga, **tokens_estreita, **tokens_curta})
        razao = uso_fora.proporcao([larga, estreita, curta], linha, SEMANA, lambda _: None, True)
        self.assertEqual(razao, 4_000_000)

    def test_sem_trabalho_continuo_suficiente_nao_ha_proporcao(self):
        janela = janela_com(SEMANA, self.ZERA, [(10.0, T0), (20.0, T0 + 16 * 3600)])
        linha = uso_fora.LinhaDoTempo({balde(T0 + 8 * 3600): 1_000_000})
        self.assertIsNone(uso_fora.proporcao([janela], linha, SEMANA, lambda _: None, True))
        self.assertIsNone(uso_fora.proporcao([], linha, SEMANA, lambda _: None, True))

    def test_janela_de_outra_duracao_nao_entra_na_proporcao(self):
        leituras = [(float(p), T0 + 600 * p) for p in range(1, 11)]
        cinco_horas = janela_com(300, T0 + 18000, leituras)
        linha = uso_fora.LinhaDoTempo({balde(i): 6_000_000 for _, i in leituras})
        self.assertIsNone(uso_fora.proporcao([cinco_horas], linha, SEMANA, lambda _: None, True))
        self.assertEqual(uso_fora.proporcao([cinco_horas], linha, 300, lambda _: None, True), 12_000_000)


# ----------------------------------------------------------- arquivos do claude


class LeiturasDoClaude(ComPainel):
    def faixas(self, cinco: float, semana: float, zera_cinco: float = 2_000_000_000, zera_semana: float = 2_000_300_000):
        return {
            "five_hour": {"usado": cinco, "zera": zera_cinco},
            "seven_day": {"usado": semana, "zera": zera_semana},
        }

    def test_grava_as_duas_janelas_e_so_regrava_quando_aparece_valor_novo(self):
        with unittest.mock.patch.object(caminhos, "gravar_texto", wraps=caminhos.gravar_texto) as gravar:
            uso_fora.guardar_leituras_do_claude("p1", self.faixas(12, 30), 1_999_990_000.0)
            uso_fora.guardar_leituras_do_claude("p1", self.faixas(12, 30), 1_999_990_100.0)
            self.assertEqual(gravar.call_count, 1)
            uso_fora.guardar_leituras_do_claude("p1", self.faixas(13, 30), 1_999_990_200.0)
            self.assertEqual(gravar.call_count, 2)
        janelas = uso_fora.ler_leituras_do_claude(["p1"])
        por_minutos = {j["min"]: j["leituras"] for j in janelas.values()}
        self.assertEqual(por_minutos[300], {"12": [1_999_990_000.0, 1_999_990_000.0], "13": [1_999_990_200.0] * 2})
        self.assertEqual(por_minutos[SEMANA], {"30": [1_999_990_000.0, 1_999_990_000.0]})

    def test_perfil_com_cara_de_caminho_nao_grava_fora_da_pasta(self):
        uso_fora.guardar_leituras_do_claude("../fora", self.faixas(1, 1), 1.0)
        uso_fora.guardar_leituras_do_claude("", self.faixas(1, 1), 1.0)
        uso_fora.guardar_leituras_do_claude(None, self.faixas(1, 1), 1.0)
        self.assertFalse((self.dados / "leituras-limite").exists())
        self.assertFalse((self.dados.parent / "fora.json").exists())

    def test_arquivo_estragado_e_refeito_sem_derrubar_nada(self):
        pasta = self.dados / "leituras-limite"
        pasta.mkdir(parents=True)
        (pasta / "p1.json").write_text("{isto nao e json", encoding="utf-8")
        uso_fora.guardar_leituras_do_claude("p1", self.faixas(5, 6), 1_999_990_000.0)
        self.assertEqual(len(uso_fora.ler_leituras_do_claude(["p1"])), 2)

    def test_junta_as_contas_pedidas_e_ignora_o_que_nao_existe(self):
        uso_fora.guardar_leituras_do_claude("p1", self.faixas(5, 6), 100.0)
        uso_fora.guardar_leituras_do_claude("p2", self.faixas(5, 7), 50.0)
        junto = uso_fora.ler_leituras_do_claude(["p1", "p2", "nunca-existiu", "../x"])
        cinco = next(j for j in junto.values() if j["min"] == 300)
        self.assertEqual(cinco["leituras"], {"5": [50.0, 100.0]})

    def test_a_status_line_grava_o_historico_junto_com_a_sessao(self):
        coletor.guardar(
            "p1",
            {
                "session_id": "s1",
                "rate_limits": {
                    "five_hour": {"used_percentage": 12, "resets_at": 2_000_000_000},
                    "seven_day": {"used_percentage": 30, "resets_at": 2_000_300_000},
                },
            },
        )
        self.assertTrue((coletor.PASTA / "s1.json").exists())
        self.assertEqual(len(uso_fora.ler_leituras_do_claude(["p1"])), 2)

    def test_falha_no_historico_nao_tira_a_sessao_da_barra(self):
        with unittest.mock.patch.object(uso_fora, "guardar_leituras_do_claude", side_effect=OSError("disco cheio")):
            coletor.guardar("p1", {"session_id": "s1", "rate_limits": {"five_hour": {"used_percentage": 1, "resets_at": 9}}})
        self.assertTrue((coletor.PASTA / "s1.json").exists())


# ----------------------------------------------------------------- coletores


class ColetoresGuardamOQueOCalculoPrecisa(ComPainel):
    def test_codex_guarda_as_leituras_por_janela_e_o_uso_por_balde(self):
        zera = instante_de(0) + 2 * DIA
        self.rollout(
            "a",
            [
                turno(carimbo(900), "t1", "gpt-6-sol"),
                registro_codex(carimbo(890), "r1", "t1", entrada=1000, lida=0, saida=0),
                contagem_codex(carimbo(889), 1000, 0, 0, limite_semanal(40.0, zera)),
                registro_codex(carimbo(300), "r2", "t1", entrada=500, lida=0, saida=0),
                contagem_codex(carimbo(299), 1500, 0, 0, limite_semanal(41.0, zera + 20)),
            ],
        )
        lido = self.ler_codex()
        (janela,) = lido["janelas"].values()
        self.assertEqual(janela["min"], SEMANA)
        self.assertEqual(sorted(janela["leituras"], key=float), ["40", "41"])
        self.assertAlmostEqual(janela["leituras"]["40"][0], instante_de(889), delta=1)
        self.assertEqual(sum(lido["tempo"].values()), 1500)

    def test_codex_com_uso_pelos_totais_tambem_vai_pro_balde(self):
        self.rollout(
            "velho",
            [turno(carimbo(7200), "t1", "gpt-6-sol"), contagem_codex(carimbo(7100), 100, 0, 10)],
            idade=3600,
        )
        self.assertEqual(sum(self.ler_codex()["tempo"].values()), 110)

    def test_claude_guarda_o_uso_por_balde_de_cada_conta(self):
        self.escrever_transcript(
            self.casa / ".claude" / "projects" / "a" / "s.jsonl",
            [
                resposta_claude(carimbo(900), "m1", "r1", input_tokens=100, output_tokens=0),
                resposta_claude(carimbo(300), "m2", "r2", input_tokens=40, output_tokens=2),
            ],
        )
        lido = uso.coletar_tudo({"__padrao__": self.casa / ".claude"}, cache=self.dados / "uso-cache-v2.json")
        self.assertEqual(sum(lido["tempo"].values()), 142)
        self.assertTrue(all(chave.startswith("__padrao__|") for chave in lido["tempo"]))
        self.assertEqual(sum(uso_fora.baldes_da_conta(lido["tempo"], {"__padrao__"}).values()), 142)

    def test_migracao_nao_perde_o_dia_que_so_o_legado_guarda(self):
        antigo = {
            "versao": 4,
            "arquivos": {},
            "ultimos": {},
            "dias": {"__padrao__|2026-09-20|claude-opus-5-5": {"entrada": 7, "mensagens": 1}},
            "horas": {},
            "vistoEm": {},
            "legado": {"__padrao__|2026-08-01|claude-opus-5-5": {"entrada": 99, "mensagens": 9}},
        }
        self.dados.mkdir(parents=True, exist_ok=True)
        (self.dados / "uso-cache-v2.json").write_text(json.dumps(antigo), encoding="utf-8")
        cache = uso._carregar_cache(self.dados / "uso-cache-v2.json")
        self.assertEqual(
            set(cache["legado"]),
            {"__padrao__|2026-08-01|claude-opus-5-5", "__padrao__|2026-09-20|claude-opus-5-5"},
        )
        self.assertEqual(cache["tempo"], {})


# --------------------------------------------------------------------- painel


def conta_do_painel(teste: ComPainel, ferramenta: str) -> dict:
    painel = painel_uso.coletar(painel_uso.pedido_do_painel(perfis.listar(), casa=teste.casa))["painel"]
    return next(conta for conta in json.loads(json.dumps(painel))["contas"] if conta["ferramenta"] == ferramenta)


class RelogioDosTestes(unittest.TestCase):
    def test_o_carimbo_nunca_cai_na_beira_de_um_balde(self):
        """Na beira, o teste de baixo falhava em uma posicao do relogio a cada 300, e travava a release."""
        self.assertEqual(instante_de(0) % uso_fora.PASSO, uso_fora.PASSO / 2)


class ForaDoComputadorNoPainelDoCodex(ComPainel):
    def antiga_com_trabalho_continuo(self) -> None:
        """Uma janela de 10 pontos em 90 minutos de trabalho com 6 mi de tokens por resposta."""
        base = instante_de(0)
        zera = base - 4 * DIA
        linhas = [turno(carimbo(10 * DIA), "t1", "gpt-6-sol")]
        for pct in range(1, 11):
            segundos = 10 * DIA - 600 * pct
            linhas.append(registro_codex(carimbo(segundos), f"a{pct}", "t1", entrada=6_000_000, lida=0, saida=0))
            linhas.append(contagem_codex(carimbo(segundos - 1), 6_000_000 * pct, 0, 0, limite_semanal(float(pct), zera)))
        self.rollout("antiga", linhas)

    def atual_que_ja_chegou_alta(self, usado: float = 40.0) -> None:
        zera = instante_de(0) + 4 * DIA
        self.rollout(
            "atual",
            [
                turno(carimbo(DIA), "t2", "gpt-6-sol"),
                registro_codex(carimbo(DIA - 10), "b1", "t2", entrada=10_000, lida=0, saida=0),
                contagem_codex(carimbo(DIA - 11), 10_000, 0, 0, limite_semanal(usado, zera)),
            ],
        )

    def test_janela_que_chegou_em_40_por_cento_sem_uso_daqui_mostra_o_piso(self):
        self.antiga_com_trabalho_continuo()
        self.atual_que_ja_chegou_alta()
        (janela,) = conta_do_painel(self, "codex")["janelas"]
        fora = janela["fora"]
        self.assertTrue(fora["calibrado"])
        self.assertEqual(fora["foraMinimo"], 39.0)
        self.assertEqual((fora["chegouEm"], fora["final"]), (40.0, 40.0))
        self.assertIsNotNone(fora["abertaEm"])
        self.assertIsNotNone(fora["primeiroUsoAqui"])
        self.assertEqual(fora["tokensPorPonto"], 12_000_000)
        (salto,) = fora["saltos"]
        self.assertEqual(salto["pts"], 39.0)

    def test_a_janela_anterior_vem_junto_com_o_que_se_sabe_dela(self):
        self.antiga_com_trabalho_continuo()
        self.atual_que_ja_chegou_alta()
        (janela,) = conta_do_painel(self, "codex")["janelas"]
        (anterior,) = janela["fora"]["anteriores"]
        self.assertEqual((anterior["final"], anterior["foraMinimo"]), (10.0, 0.0))

    def test_sem_nenhuma_janela_com_trabalho_continuo_so_aparecem_os_fatos(self):
        self.atual_que_ja_chegou_alta()
        (janela,) = conta_do_painel(self, "codex")["janelas"]
        fora = janela["fora"]
        self.assertFalse(fora["calibrado"])
        self.assertIsNone(fora["foraMinimo"])
        self.assertEqual(fora["chegouEm"], 40.0)

    def test_janela_vista_so_pelo_uso_daqui_nao_acusa_ninguem(self):
        self.antiga_com_trabalho_continuo()
        zera = instante_de(0) + 4 * DIA
        linhas = [turno(carimbo(DIA), "t2", "gpt-6-sol")]
        for pct in range(1, 11):
            segundos = DIA - 600 * pct
            linhas.append(registro_codex(carimbo(segundos), f"c{pct}", "t2", entrada=6_000_000, lida=0, saida=0))
            linhas.append(contagem_codex(carimbo(segundos - 1), 6_000_000 * pct, 0, 0, limite_semanal(float(pct), zera)))
        self.rollout("atual", linhas)
        (janela,) = conta_do_painel(self, "codex")["janelas"]
        self.assertEqual(janela["fora"]["foraMinimo"], 0.0)
        self.assertEqual(janela["fora"]["saltos"], [])

    def test_conta_sem_leitura_de_limite_nao_ganha_o_campo(self):
        self.rollout(
            "a",
            [turno(carimbo(300), "t1", "gpt-6-sol"), registro_codex(carimbo(290), "r1", "t1", entrada=5, lida=0, saida=0)],
        )
        self.assertEqual(conta_do_painel(self, "codex")["janelas"], [])


class ForaDoComputadorNoPainelDoClaude(ComPainel):
    def faixas(self, cinco: float, zera: float) -> dict:
        return {"five_hour": {"usado": cinco, "zera": zera}}

    def conversa(self, respostas: list[dict]) -> None:
        self.escrever_transcript(self.casa / ".claude" / "projects" / "p" / "s.jsonl", respostas)

    def sessao_ao_vivo(self, cinco: float, zera: float) -> None:
        coletor.guardar(
            "__padrao__",
            {"session_id": "ao-vivo", "rate_limits": {"five_hour": {"used_percentage": cinco, "resets_at": zera}}},
        )

    def test_sessao_de_5_horas_que_ja_estava_alta_quando_o_claude_chegou(self):
        agora = instante_de(0)
        zera = agora + 3600
        self.conversa([resposta_claude(carimbo(3 * 3600), "m1", "r1", input_tokens=10_000, output_tokens=0)])
        uso_fora.guardar_leituras_do_claude("__padrao__", self.faixas(30, zera), agora - 3 * 3600)
        self.sessao_ao_vivo(30, zera)
        (janela, *_) = [j for j in conta_do_painel(self, "claude")["janelas"] if j["tipo"] == "5h"]
        fora = janela["fora"]
        self.assertEqual(fora["chegouEm"], 30.0)
        self.assertIsNotNone(fora["abertaEm"])
        self.assertFalse(fora["calibrado"])
        self.assertIsNone(fora["foraMinimo"])

    def test_com_historico_de_trabalho_continuo_a_conta_mostra_o_piso(self):
        agora = instante_de(0)
        zera_antiga = agora - 3 * DIA
        respostas = []
        for pct in range(1, 11):
            instante = zera_antiga - 4 * 3600 + 600 * pct
            respostas.append(
                resposta_claude(carimbo(agora - instante), f"o{pct}", f"q{pct}", input_tokens=6_000_000, output_tokens=0)
            )
            uso_fora.guardar_leituras_do_claude("__padrao__", self.faixas(float(pct), zera_antiga), instante)
        zera = agora + 3600
        respostas.append(resposta_claude(carimbo(3 * 3600), "m1", "r1", input_tokens=10_000, output_tokens=0))
        self.conversa(respostas)
        uso_fora.guardar_leituras_do_claude("__padrao__", self.faixas(30, zera), agora - 3 * 3600)
        self.sessao_ao_vivo(30, zera)
        (janela, *_) = [j for j in conta_do_painel(self, "claude")["janelas"] if j["tipo"] == "5h"]
        fora = janela["fora"]
        self.assertTrue(fora["calibrado"])
        self.assertEqual(fora["foraMinimo"], 29.0)

    def test_a_semana_do_claude_nao_tem_abertura_conhecida(self):
        agora = instante_de(0)
        zera = agora + 2 * DIA
        uso_fora.guardar_leituras_do_claude(
            "__padrao__", {"seven_day": {"usado": 20, "zera": zera}}, agora - 3600
        )
        coletor.guardar(
            "__padrao__",
            {"session_id": "ao-vivo", "rate_limits": {"seven_day": {"used_percentage": 20, "resets_at": zera}}},
        )
        (janela,) = [j for j in conta_do_painel(self, "claude")["janelas"] if j["tipo"] == "7d"]
        self.assertIsNone(janela["fora"]["abertaEm"])
        self.assertEqual(janela["fora"]["chegouEm"], 20.0)


if __name__ == "__main__":
    unittest.main()
