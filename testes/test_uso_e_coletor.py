"""Medidor de consumo, coletor de limite e o que fica guardado entre sessoes."""

from __future__ import annotations

import io
import json
import sys
import unittest
import unittest.mock
from datetime import datetime, timedelta

from base import ComPastaPropria

from dbx_term import coletor, config, sessao_salva, uso


def hoje_as(hora: int = 12) -> str:
    return datetime.now().replace(hour=hora, minute=0, second=0, microsecond=0).astimezone().isoformat()


def dias_atras(dias: int) -> str:
    return (datetime.now() - timedelta(days=dias)).replace(hour=12).astimezone().isoformat()


class Medidor(ComPastaPropria):
    def montar(self, registros, nome="s1"):
        conta = self.raiz / "conta"
        self.escrever_transcript(conta / "projects" / "proj" / f"{nome}.jsonl", registros)
        return {"conta": conta}

    def test_soma_o_consumo_do_dia(self):
        pastas = self.montar(
            [
                self.resposta(hoje_as(9), "a", output_tokens=100),
                self.resposta(hoje_as(10), "b", output_tokens=50),
            ]
        )
        relatorio = uso.relatorio(pastas)
        self.assertEqual(relatorio["conta"]["hoje"]["saida"], 150)
        self.assertEqual(relatorio["conta"]["hoje"]["mensagens"], 2)

    def test_mensagem_repetida_nao_conta_duas_vezes(self):
        """Retomar sessao reescreve mensagem, e sem deduplicar o gasto dobra."""
        igual = self.resposta(hoje_as(9), "a", output_tokens=100)
        pastas = self.montar([igual, dict(igual)])
        self.assertEqual(uso.relatorio(pastas)["conta"]["hoje"]["saida"], 100)

    def test_pedido_do_usuario_nao_entra_no_gasto(self):
        pastas = self.montar([self.pedido(hoje_as(9), "oi"), self.resposta(hoje_as(10), output_tokens=7)])
        self.assertEqual(uso.relatorio(pastas)["conta"]["hoje"]["saida"], 7)

    def test_dia_antigo_sai_do_hoje_mas_fica_no_total(self):
        pastas = self.montar([self.resposta(dias_atras(10), "a", output_tokens=90)])
        relatorio = uso.relatorio(pastas)["conta"]
        self.assertEqual(relatorio["hoje"].get("saida", 0), 0)
        self.assertEqual(relatorio["total"]["saida"], 90)

    def test_leitura_incremental_nao_soma_de_novo(self):
        pastas = self.montar([self.resposta(hoje_as(9), "a", output_tokens=100)])
        uso.relatorio(pastas)
        segunda = uso.relatorio(pastas)
        self.assertEqual(segunda["conta"]["hoje"]["saida"], 100)

    def test_conta_emprestada_nao_conta_a_mesma_pasta_duas_vezes(self):
        """Se a pasta de sempre virou conta cadastrada, ela nao pode entrar duas vezes."""

        class FalsoPerfil:
            id = "x"
            ferramenta = "claude"
            emprestada = True
            config_dir = uso._pasta_padrao()

        pastas = uso.pastas_de([FalsoPerfil()])
        self.assertNotIn("__padrao__", pastas)
        self.assertEqual(list(pastas), ["x"])

    def test_conta_com_pasta_propria_convive_com_a_de_sempre(self):
        class FalsoPerfil:
            id = "x"
            ferramenta = "claude"
            emprestada = False
            config_dir = self.raiz / "outra"

        pastas = uso.pastas_de([FalsoPerfil()])
        self.assertIn("__padrao__", pastas)
        self.assertIn("x", pastas)


class ColetorDeLimite(ComPastaPropria):
    def rodar(self, payload: dict, ambiente: dict | None = None, argumentos: list[str] | None = None) -> str:
        entrada = io.StringIO(json.dumps(payload, ensure_ascii=False))
        saida = io.StringIO()
        antigo_in, antigo_out, antigo_argv = sys.stdin, sys.stdout, sys.argv
        with unittest.mock.patch.dict("os.environ", ambiente or {}, clear=False):
            sys.stdin, sys.stdout = entrada, saida
            sys.argv = ["coletar.py"] + (argumentos or ["conta1"])
            try:
                coletor.main()
            finally:
                sys.stdin, sys.stdout, sys.argv = antigo_in, antigo_out, antigo_argv
        return saida.getvalue()

    def test_guarda_percentual_e_virada(self):
        self.rodar(
            {
                "session_id": "s1",
                "rate_limits": {
                    "five_hour": {"used_percentage": 42, "resets_at": 1788286200},
                    "seven_day": {"used_percentage": 7, "resets_at": 1788786000},
                },
            }
        )
        guardado = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(guardado["faixas"]["five_hour"]["usado"], 42)
        self.assertEqual(guardado["faixas"]["seven_day"]["zera"], 1788786000)

    def test_acento_no_nome_da_sessao_sobrevive(self):
        self.rodar({"session_id": "s1", "session_name": "Integração de agente"})
        guardado = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(guardado["titulo"], "Integração de agente")

    def test_contexto_zerado_e_calculado_pelos_tokens(self):
        """Conversa nova nao traz percentual, so os tokens."""
        self.rodar(
            {
                "session_id": "s1",
                "context_window": {"total_input_tokens": 250000, "context_window_size": 1000000},
            }
        )
        guardado = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(guardado["contexto"], 25.0)

    def test_marca_a_aba_quando_a_sessao_nasce_aqui(self):
        self.rodar({"session_id": "s1", "cwd": r"C:\p"}, {"DBX_TERM_ABA": "aba7"}, ["conta1", "--hook"])
        guardado = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(guardado["aba"], "aba7")
        self.assertEqual(guardado["terminal"], "dbx-term")

    def test_sessao_de_fora_nao_ganha_marca_de_aba(self):
        self.rodar({"session_id": "s1", "cwd": r"C:\p"}, {}, ["conta1", "--hook"])
        guardado = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(guardado["terminal"], "")

    def test_ambiente_da_aba_manda_mais_que_o_argumento(self):
        """O settings da pasta chama sempre com o mesmo perfil fixo."""
        self.rodar({"session_id": "s1"}, {"DBX_TERM_PERFIL": "conta-certa", "DBX_TERM_ABA": "a"}, ["conta-errada"])
        guardado = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(guardado["perfil"], "conta-certa")

    def test_leitura_vazia_nao_apaga_a_boa(self):
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        import time

        (coletor.PASTA / "boa.json").write_text(
            json.dumps({"sessao": "boa", "perfil": "c", "lido": time.time() - 60, "faixas": {"five_hour": {"usado": 9}}}),
            encoding="utf-8",
        )
        (coletor.PASTA / "nova.json").write_text(
            json.dumps({"sessao": "nova", "perfil": "c", "lido": time.time(), "faixas": {}}), encoding="utf-8"
        )
        self.assertEqual(coletor.limites_por_perfil()["c"]["faixas"]["five_hour"]["usado"], 9)


class Configuracao(ComPastaPropria):
    def test_valor_de_tipo_errado_e_ignorado(self):
        config.salvar({"tamanhoFonte": "grande"})
        self.assertEqual(config.carregar()["tamanhoFonte"], config.PADRAO["tamanhoFonte"])

    def test_chave_desconhecida_nao_entra(self):
        config.salvar({"inventada": 1})
        self.assertNotIn("inventada", config.carregar())

    def test_arquivo_corrompido_cai_no_padrao(self):
        config.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        config.ARQUIVO.write_text("{isso nao e json", encoding="utf-8")
        self.assertEqual(config.carregar(), config.PADRAO)


class AbasSalvas(ComPastaPropria):
    def test_grava_e_devolve(self):
        sessao_salva.gravar([{"shell": "cmd", "perfil": "c1", "pasta": r"C:\x", "conversa": "s1", "ativa": True}])
        lidas = sessao_salva.ler()
        self.assertEqual(len(lidas), 1)
        self.assertEqual(lidas[0]["shell"], "cmd")
        self.assertTrue(lidas[0]["ativa"])

    def test_entrada_sem_shell_e_descartada(self):
        sessao_salva.gravar([{"perfil": "c1"}, {"shell": "cmd"}])
        self.assertEqual(len(sessao_salva.ler()), 1)


if __name__ == "__main__":
    unittest.main()
