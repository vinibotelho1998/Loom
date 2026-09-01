"""Leitura do estado das sessoes: o que o painel lateral mostra."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from base import ComPastaPropria

from dbx_term import sessoes


def agora_menos(segundos: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos)).isoformat().replace("+00:00", "Z")


class Estado(ComPastaPropria):
    def montar(self, registros, nome="s1"):
        pasta = self.raiz / "conta"
        arquivo = self.escrever_transcript(pasta / "projects" / "proj" / f"{nome}.jsonl", registros)
        sessoes._LEMBRADO.clear()
        return {"conta": str(pasta)}, arquivo

    def test_texto_na_ponta_e_espera_por_voce(self):
        pastas, _ = self.montar([self.pedido(agora_menos(20), "oi"), self.resposta(agora_menos(10))])
        achadas = sessoes.listar(pastas)
        self.assertEqual(achadas[0]["estado"], "aguardando")

    def test_ferramenta_na_ponta_e_trabalho_em_curso(self):
        registros = [
            self.pedido(agora_menos(20), "faz"),
            {
                "type": "assistant",
                "timestamp": agora_menos(5),
                "cwd": r"C:\proj",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "dir"}}],
                },
            },
        ]
        pastas, _ = self.montar(registros)
        achada = sessoes.listar(pastas)[0]
        self.assertEqual(achada["estado"], "rodando")
        self.assertEqual(achada["ferramenta"], "Bash")
        self.assertEqual(achada["alvo"], "dir")

    def test_resultado_de_ferramenta_e_a_vez_do_modelo(self):
        registros = [
            self.resposta(agora_menos(30)),
            {
                "type": "user",
                "timestamp": agora_menos(3),
                "cwd": r"C:\proj",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
            },
        ]
        pastas, _ = self.montar(registros)
        self.assertEqual(sessoes.listar(pastas)[0]["estado"], "pensando")

    def test_trabalho_abandonado_ha_muito_tempo_nao_e_trabalho(self):
        registros = [
            self.pedido(agora_menos(4000), "faz"),
            {
                "type": "assistant",
                "timestamp": agora_menos(3600),
                "cwd": r"C:\proj",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "x"}}],
                },
            },
        ]
        pastas, _ = self.montar(registros)
        self.assertEqual(sessoes.listar(pastas)[0]["estado"], "parada")

    def test_idade_vem_da_mensagem_e_nao_do_arquivo(self):
        """O Claude escreve titulo e modo no arquivo, e isso nao e conversa.

        Sem esse cuidado a sessao ficava presa em pensando pra sempre.
        """
        registros = [
            self.pedido(agora_menos(5000), "oi"),
            self.resposta(agora_menos(4000)),
            {"type": "ai-title", "aiTitle": "Nome qualquer", "sessionId": "s1"},
        ]
        pastas, arquivo = self.montar(registros)
        arquivo.touch()  # arquivo recem tocado, conversa velha
        self.assertEqual(sessoes.listar(pastas)[0]["estado"], "parada")


class Nome(ComPastaPropria):
    def montar(self, registros):
        pasta = self.raiz / "conta"
        self.escrever_transcript(pasta / "projects" / "proj" / "s1.jsonl", registros)
        sessoes._LEMBRADO.clear()
        return {"conta": str(pasta)}

    def test_usa_o_titulo_gravado_pelo_claude(self):
        pastas = self.montar(
            [
                self.pedido(agora_menos(60), "primeira coisa"),
                self.resposta(agora_menos(50)),
                {"type": "ai-title", "aiTitle": "Nome bonito", "sessionId": "s1"},
            ]
        )
        self.assertEqual(sessoes.listar(pastas)[0]["titulo"], "Nome bonito")

    def test_sem_titulo_usa_o_que_a_pessoa_pediu(self):
        pastas = self.montar([self.pedido(agora_menos(60), "arruma o deploy"), self.resposta(agora_menos(50))])
        self.assertEqual(sessoes.listar(pastas)[0]["titulo"], "arruma o deploy")

    def test_a_marca_da_status_line_ganha_do_transcript(self):
        """Renomear aparece na hora, e sessao retomada nasce sem titulo no arquivo."""
        pastas = self.montar(
            [
                self.pedido(agora_menos(60), "oi"),
                self.resposta(agora_menos(50)),
                {"type": "ai-title", "aiTitle": "Nome antigo", "sessionId": "s1"},
            ]
        )
        vistas = {"s1": {"titulo": "Nome de agora", "aba": "", "terminal": ""}}
        self.assertEqual(sessoes.listar(pastas, vistas)[0]["titulo"], "Nome de agora")

    def test_comando_local_nao_vira_nome(self):
        pastas = self.montar(
            [
                {
                    "type": "user",
                    "timestamp": agora_menos(60),
                    "cwd": r"C:\proj",
                    "message": {"role": "user", "content": [{"type": "text", "text": "/status"}]},
                },
                self.pedido(agora_menos(55), "o que importa"),
                self.resposta(agora_menos(50)),
            ]
        )
        self.assertEqual(sessoes.listar(pastas)[0]["titulo"], "o que importa")


class Filtros(ComPastaPropria):
    def test_sessao_sem_resposta_do_modelo_nao_entra(self):
        """E o que a tela de confiar na pasta deixa pra tras."""
        pasta = self.raiz / "conta"
        self.escrever_transcript(pasta / "projects" / "p" / "vazia.jsonl", [self.pedido(agora_menos(30), "oi")])
        sessoes._LEMBRADO.clear()
        self.assertEqual(sessoes.listar({"conta": str(pasta)}), [])

    def test_conversas_diferentes_na_mesma_pasta_continuam_as_duas(self):
        """Agrupar por pasta escondia conversa de verdade."""
        pasta = self.raiz / "conta"
        for nome, titulo in (("a", "Primeira"), ("b", "Segunda")):
            self.escrever_transcript(
                pasta / "projects" / "p" / f"{nome}.jsonl",
                [
                    self.pedido(agora_menos(300), "oi"),
                    self.resposta(agora_menos(200)),
                    {"type": "ai-title", "aiTitle": titulo, "sessionId": nome},
                ],
            )
        sessoes._LEMBRADO.clear()
        achadas = sessoes.listar({"conta": str(pasta)})
        self.assertEqual(sorted(s["titulo"] for s in achadas), ["Primeira", "Segunda"])

    def test_mesma_conversa_repetida_fica_so_a_mais_nova(self):
        pasta = self.raiz / "conta"
        for nome, idade in (("velha", 900), ("nova", 300)):
            self.escrever_transcript(
                pasta / "projects" / "p" / f"{nome}.jsonl",
                [
                    self.pedido(agora_menos(idade + 50), "oi"),
                    self.resposta(agora_menos(idade)),
                    {"type": "ai-title", "aiTitle": "Mesmo nome", "sessionId": nome},
                ],
            )
        sessoes._LEMBRADO.clear()
        achadas = sessoes.listar({"conta": str(pasta)})
        self.assertEqual(len(achadas), 1)
        self.assertLess(achadas[0]["idade"], 600)

    def test_pasta_de_servico_nao_vira_o_lugar_da_sessao(self):
        """Uma conversa que passou pela pasta de memoria virava memory na lista."""
        pasta = self.raiz / "conta"
        registros = [
            {**self.pedido(agora_menos(300), "oi"), "cwd": r"C:\Users\eu\Projetos\meu-app"},
            {**self.resposta(agora_menos(250)), "cwd": r"C:\Users\eu\Projetos\meu-app"},
            {**self.resposta(agora_menos(200), texto="fim"), "cwd": r"C:\Users\eu\.claude\projects\x\memory"},
        ]
        self.escrever_transcript(pasta / "projects" / "p" / "s.jsonl", registros)
        sessoes._LEMBRADO.clear()
        self.assertTrue(sessoes.listar({"conta": str(pasta)})[0]["pasta"].endswith("meu-app"))


if __name__ == "__main__":
    unittest.main()
