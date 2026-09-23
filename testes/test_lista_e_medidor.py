"""O que a lista de sessoes e o medidor de uso decidem a partir do transcript.

Tudo roda na pasta temporaria da base, com transcript escrito pelo proprio teste.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

from base import ComPastaPropria

from loom import sessoes, uso


def agora_menos(segundos: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos)).isoformat().replace("+00:00", "Z")


class AvisoDeHistoricoPelaMetade(ComPastaPropria):
    """O aviso de leitura pela metade tem que dizer a verdade nos dois sentidos."""

    def setUp(self) -> None:
        super().setUp()
        self.arquivo = self.casa / "projects" / "p" / "conversa.jsonl"
        self.pastas = {"c1": self.casa}

    def resposta_de_agora(self, ident: str) -> dict:
        registro = self.resposta(datetime.now().astimezone().isoformat(), "ok")
        registro["message"]["id"] = ident
        return registro

    def resultado_de_subagente(self) -> dict:
        # o resultado do Agent traz o consumo do subagente: passa pelo filtro de "usage",
        # mas e fala do usuario e nao entra no gasto
        return {
            "type": "user",
            "timestamp": datetime.now().astimezone().isoformat(),
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "feito"}]},
            "toolUseResult": {"usage": {"input_tokens": 3, "output_tokens": 4}},
        }

    def test_arquivo_lido_inteiro_nao_fica_marcado_pela_metade(self):
        """O contador de cada resposta tinha o mesmo nome do aviso e tomava o lugar dele.

        Qualquer leitura com resposta nova saia como pela metade, e a tela avisava que o
        numero ia subir e voltava a perguntar em 1,5 s em vez de 30.
        """
        self.escrever_transcript(self.arquivo, [self.resposta_de_agora("m1")])
        estado: dict = {}
        uso.relatorio(self.pastas, estado)
        self.assertIs(estado["parcial"], False)

    def test_pela_metade_continua_avisado_mesmo_terminando_em_fala_do_usuario(self):
        anterior = uso.TETO_POR_PASSADA
        uso.TETO_POR_PASSADA = 1500
        try:
            registros = [self.resposta_de_agora("m0")]
            registros += [self.resultado_de_subagente() for _ in range(30)]
            self.escrever_transcript(self.arquivo, registros)
            estado: dict = {}
            uso.relatorio(self.pastas, estado)
        finally:
            uso.TETO_POR_PASSADA = anterior
        self.assertIs(estado["parcial"], True)

    def test_sem_novidade_nao_ha_aviso(self):
        self.escrever_transcript(self.arquivo, [self.resposta_de_agora("m1")])
        uso.relatorio(self.pastas)
        estado: dict = {}
        uso.relatorio(self.pastas, estado)
        self.assertIs(estado["parcial"], False)


class ContasDoRelatorio(ComPastaPropria):
    def linha(self, quando: str, saida: int, modelo: str = "claude-opus-5", ident: str = "") -> dict:
        return self.resposta(quando, ident or quando, modelo=modelo, output_tokens=saida)

    def test_semana_e_mes_contam_do_primeiro_dia_de_cada_um(self):
        hoje = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        segunda = hoje - timedelta(days=hoje.weekday())
        domingo_antes = segunda - timedelta(days=1)
        registros = [
            self.linha(hoje.astimezone().isoformat(), 1, ident="hoje"),
            self.linha(segunda.astimezone().isoformat(), 10, ident="segunda"),
            self.linha(domingo_antes.astimezone().isoformat(), 100, ident="domingo"),
            self.linha((hoje - timedelta(days=400)).astimezone().isoformat(), 1000, ident="velha"),
        ]
        self.escrever_transcript(self.casa / "projects" / "p" / "s.jsonl", registros)

        conta = uso.relatorio({"c1": self.casa})["c1"]

        # na segunda-feira, a segunda e o proprio hoje
        self.assertEqual(conta["hoje"]["saida"], 11 if segunda == hoje else 1)
        # o domingo de antes fecha a semana passada, nao abre esta
        self.assertEqual(conta["semana"]["saida"], 11)
        inicio_mes = hoje.replace(day=1)
        mes = sum(v for d, v in ((hoje, 1), (segunda, 10), (domingo_antes, 100)) if d >= inicio_mes)
        self.assertEqual(conta["mes"]["saida"], mes)
        self.assertEqual(conta["total"]["saida"], 1111)
        self.assertEqual(conta["ultimoDia"], hoje.date().isoformat())

    def test_fora_dos_trinta_dias_nao_entra_no_grafico_nem_na_legenda(self):
        hoje = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        registros = [
            self.linha(hoje.astimezone().isoformat(), 5, "claude-opus-5"),
            self.linha((hoje - timedelta(days=45)).astimezone().isoformat(), 50, "claude-sonnet-5"),
        ]
        self.escrever_transcript(self.casa / "projects" / "p" / "s.jsonl", registros)

        conta = uso.relatorio({"c1": self.casa})["c1"]

        self.assertEqual([p["dia"] for p in conta["serie"]], [hoje.date().isoformat()])
        self.assertEqual(conta["modelos"], [{"modelo": "claude-opus-5", "tokens": 5}])

    def test_legenda_vem_do_modelo_que_mais_gerou_pro_que_menos(self):
        agora = datetime.now().astimezone().isoformat()
        registros = [
            self.linha(agora, 5, "claude-haiku-5", "a"),
            self.linha(agora, 70, "claude-opus-5", "b"),
            self.linha(agora, 20, "claude-sonnet-5", "c"),
        ]
        self.escrever_transcript(self.casa / "projects" / "p" / "s.jsonl", registros)

        modelos = uso.relatorio({"c1": self.casa})["c1"]["modelos"]

        self.assertEqual([m["modelo"] for m in modelos], ["claude-opus-5", "claude-sonnet-5", "claude-haiku-5"])

    def test_chave_de_cache_estranha_nao_derruba_o_relatorio(self):
        uso.CACHE.parent.mkdir(parents=True, exist_ok=True)
        uso.CACHE.write_text(
            json.dumps({"arquivos": {}, "dias": {"sem-barras": {"saida": 1}, "c1|ontem|x": {"saida": 2}}}),
            encoding="utf-8",
        )
        self.assertEqual(uso.relatorio({}), {})

    def test_cache_corrompido_recomeca_do_zero(self):
        uso.CACHE.parent.mkdir(parents=True, exist_ok=True)
        uso.CACHE.write_text("{nao e json", encoding="utf-8")
        self.escrever_transcript(
            self.casa / "projects" / "p" / "s.jsonl", [self.linha(datetime.now().astimezone().isoformat(), 9)]
        )
        self.assertEqual(uso.relatorio({"c1": self.casa})["c1"]["hoje"]["saida"], 9)


class ResumoDaFerramenta(ComPastaPropria):
    def test_comando_longo_mostra_so_o_comeco_da_primeira_linha(self):
        nome, alvo = sessoes._resumo({"name": "Bash", "input": {"command": "  cd app\nnpm test"}})
        self.assertEqual((nome, alvo), ("Bash", "cd app"))
        comando = "git log --oneline " + "x" * 200
        _, alvo = sessoes._resumo({"name": "PowerShell", "input": {"command": comando}})
        self.assertEqual(alvo, comando[:70])

    def test_arquivo_mostra_so_o_nome(self):
        _, alvo = sessoes._resumo({"name": "Edit", "input": {"file_path": "C:\\proj\\src\\app.py"}})
        self.assertEqual(alvo, "app.py")

    def test_ferramenta_desconhecida_nao_mostra_alvo(self):
        self.assertEqual(sessoes._resumo({"name": "Inventada", "input": {"x": "y"}}), ("Inventada", ""))

    def test_entrada_fora_do_formato_nao_derruba(self):
        self.assertEqual(sessoes._resumo({"name": "Bash", "input": "dir"}), ("Bash", ""))
        self.assertEqual(sessoes._resumo({"name": "Bash", "input": {"command": "   "}}), ("Bash", ""))


class RegraDaIdade(ComPastaPropria):
    def test_cada_estado_com_a_idade(self):
        casos = [
            ("rodando", 10, "rodando"),
            ("pensando", 10, "pensando"),
            ("rodando", sessoes.SEGUNDOS_ATIVO, "aguardando"),
            ("respondeu", 10, "aguardando"),
            ("respondeu", sessoes.SEGUNDOS_PARADA - 1, "aguardando"),
            ("respondeu", sessoes.SEGUNDOS_PARADA, "parada"),
            ("rodando", sessoes.SEGUNDOS_PARADA, "parada"),
            ("parada", 10, "parada"),
        ]
        for bruto, idade, esperado in casos:
            self.assertEqual(sessoes._decidir(bruto, idade), esperado, (bruto, idade))

    def test_carimbo_ilegivel_usa_a_data_do_arquivo(self):
        mtime = time.time() - 500
        idade = sessoes._idade_da_conversa({"quando": "ontem de tarde"}, mtime)
        self.assertAlmostEqual(idade, 500, delta=5)


class PrimeiraFala(ComPastaPropria):
    def fala(self, conteudo, **extra) -> dict:
        return {"type": "user", "message": {"role": "user", "content": conteudo}, **extra}

    def test_texto_simples_tambem_serve(self):
        self.assertEqual(sessoes._primeira_fala([self.fala("  arruma   o\nbuild  ")]), "arruma o build")

    def test_pedido_longo_e_cortado_com_reticencias(self):
        nome = sessoes._primeira_fala([self.fala("a" * 100)])
        self.assertEqual(nome, "a" * sessoes.LIMITE_DO_TITULO + "…")

    def test_meta_linha_de_sistema_e_resultado_de_ferramenta_nao_sao_pedido(self):
        registros = [
            self.fala("contexto injetado", isMeta=True),
            self.fala("<command-name>/clear</command-name>"),
            self.fala([{"type": "tool_result", "content": "saida"}]),
            self.fala([{"type": "text", "text": "o que eu pedi"}]),
        ]
        self.assertEqual(sessoes._primeira_fala(registros), "o que eu pedi")

    def test_mensagem_fora_do_formato_nao_derruba_a_lista(self):
        """O estado ja pulava a mensagem que nao e objeto, mas o nome quebrava nela logo depois."""
        conta = self.raiz / "conta"
        self.escrever_transcript(
            conta / "projects" / "proj" / "s1.jsonl",
            [
                {"type": "user", "timestamp": agora_menos(20), "cwd": r"C:\proj", "message": "texto solto"},
                self.pedido(agora_menos(15), "o pedido de verdade"),
                self.resposta(agora_menos(10)),
            ],
        )
        achadas = sessoes.listar({"conta": str(conta)})
        self.assertEqual([s["titulo"] for s in achadas], ["o pedido de verdade"])


class OndeASessaoEsta(ComPastaPropria):
    def test_so_pasta_de_servico_fica_com_a_ultima(self):
        registros = [
            {"cwd": r"C:\Users\eu\.claude", "gitBranch": "a"},
            {"cwd": r"C:\Users\eu\.loom", "gitBranch": "b"},
        ]
        self.assertEqual(sessoes._onde(registros), (r"C:\Users\eu\.loom", "b"))

    def test_ramo_vem_da_pasta_escolhida(self):
        registros = [
            {"cwd": r"C:\app", "gitBranch": "main"},
            {"cwd": r"C:\app", "gitBranch": "feat/x"},
            {"cwd": r"C:\outra", "gitBranch": "dev"},
        ]
        self.assertEqual(sessoes._onde(registros), (r"C:\app", "feat/x"))


class OrdemDaLista(ComPastaPropria):
    def conversa(self, nome: str, idade: int, ultimo: dict | None = None) -> None:
        registros = [self.pedido(agora_menos(idade + 30), f"pedido {nome}"), self.resposta(agora_menos(idade + 20))]
        if ultimo is not None:
            registros.append(ultimo)
        self.escrever_transcript(self.raiz / "conta" / "projects" / "p" / f"{nome}.jsonl", registros)

    def ferramenta(self, idade: int) -> dict:
        return {
            "type": "assistant",
            "timestamp": agora_menos(idade),
            "cwd": r"C:\proj",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "x"}}]},
        }

    def resultado(self, idade: int) -> dict:
        return {
            "type": "user",
            "timestamp": agora_menos(idade),
            "cwd": r"C:\proj",
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
        }

    def test_quem_espera_vem_antes_de_quem_trabalha_e_paradas_no_fim(self):
        self.conversa("rodando", 5, self.ferramenta(5))
        self.conversa("parada", sessoes.SEGUNDOS_PARADA + 100)
        self.conversa("pensando", 5, self.resultado(5))
        self.conversa("pronta", 5)

        estados = [s["estado"] for s in sessoes.listar({"conta": str(self.raiz / "conta")})]

        self.assertEqual(estados, ["aguardando", "pensando", "rodando", "parada"])

    def test_paradas_sao_limitadas_e_as_vivas_nao(self):
        for i in range(15):
            self.conversa(f"velha{i}", sessoes.SEGUNDOS_PARADA + 100 + i)
        self.conversa("pronta", 5)

        achadas = sessoes.listar({"conta": str(self.raiz / "conta")})

        self.assertEqual(len(achadas), 13)
        self.assertEqual(achadas[0]["estado"], "aguardando")
        # das paradas ficam as mais novas
        self.assertEqual(achadas[1]["sessao"], "velha0")

    def test_arquivo_sem_mexer_ha_mais_de_um_dia_nao_entra(self):
        self.conversa("antiga", 10)
        arquivo = self.raiz / "conta" / "projects" / "p" / "antiga.jsonl"
        velho = time.time() - sessoes.JANELA_VIVA - 60
        os.utime(arquivo, (velho, velho))
        self.assertEqual(sessoes.listar({"conta": str(self.raiz / "conta")}), [])
