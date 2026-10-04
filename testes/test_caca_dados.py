"""Bugs da camada de dados achados lendo transcript e cache de verdade.

Cada caso reproduz o formato que o Claude Code grava hoje, conferido nos arquivos reais.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from base import ComPastaPropria
from test_dados_do_painel import ComPainel, carimbo, contagem_codex, linha_codex, registro_codex, turno

from loom import aviso_limite, coletor, painel_uso, sessoes, uso


def agora_menos(segundos: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos)).isoformat().replace("+00:00", "Z")


def bloco_do_modelo(quando: str, ident: str, bloco: dict, fim, saida: int = 4) -> dict:
    """Uma linha do transcript como o Claude grava: um bloco so, com o consumo do streaming."""
    return {
        "type": "assistant",
        "timestamp": quando,
        "cwd": r"C:\proj",
        "requestId": f"req-{ident}",
        "message": {
            "id": ident,
            "role": "assistant",
            "model": "claude-opus-5",
            "stop_reason": fim,
            "content": [bloco],
            "usage": {
                "input_tokens": 32,
                "output_tokens": saida,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 200,
            },
        },
    }


PENSOU = {"type": "thinking", "thinking": "..."}
TEXTO = {"type": "text", "text": "Vou rodar os testes."}
FERRAMENTA = {"type": "tool_use", "name": "Bash", "input": {"command": "dir"}}


class RespostaAindaSaindo(ComPastaPropria):
    """O bloco de raciocinio ou de texto chega antes do resto da resposta, sem stop_reason."""

    def estado_com(self, ponta: dict) -> dict:
        pasta = self.raiz / "conta"
        self.escrever_transcript(
            pasta / "projects" / "proj" / "s1.jsonl", [self.pedido(agora_menos(20), "roda os testes"), ponta]
        )
        sessoes._LEMBRADO.clear()
        return sessoes.listar({"conta": str(pasta)})[0]

    def test_raciocinio_sem_fim_na_ponta_e_o_modelo_trabalhando(self):
        achada = self.estado_com(bloco_do_modelo(agora_menos(5), "m1", PENSOU, None))
        self.assertEqual((achada["bruto"], achada["estado"]), ("pensando", "pensando"))

    def test_texto_antes_da_ferramenta_e_o_modelo_trabalhando(self):
        achada = self.estado_com(bloco_do_modelo(agora_menos(5), "m1", TEXTO, None))
        self.assertEqual(achada["estado"], "pensando")
        achada = self.estado_com(bloco_do_modelo(agora_menos(5), "m1", TEXTO, "tool_use"))
        self.assertEqual(achada["estado"], "pensando")

    def test_resposta_terminada_continua_esperando_voce(self):
        """Controle: o texto com end_turn e a resposta pronta, e sem o campo tambem, como antes."""
        achada = self.estado_com(bloco_do_modelo(agora_menos(5), "m1", TEXTO, "end_turn"))
        self.assertEqual((achada["bruto"], achada["estado"]), ("respondeu", "aguardando"))
        antiga = bloco_do_modelo(agora_menos(5), "m1", TEXTO, None)
        del antiga["message"]["stop_reason"]
        self.assertEqual(self.estado_com(antiga)["estado"], "aguardando")

    def test_resposta_cortada_ha_tempo_nao_fica_presa_em_pensando(self):
        """Sessao morta no meio da resposta sai de pensando pela regra da idade."""
        achada = self.estado_com(bloco_do_modelo(agora_menos(600), "m1", TEXTO, None))
        self.assertEqual(achada["estado"], "aguardando")


class SaidaDaUltimaLinha(ComPastaPropria):
    """Cada bloco da resposta leva o consumo do streaming; so a ultima linha tem a saida inteira."""

    def setUp(self) -> None:
        super().setUp()
        self.pasta = self.raiz / "conta"
        self.arquivo = self.pasta / "projects" / "proj" / "s1.jsonl"
        self.cache = self.raiz / "dados" / "uso-cache-v3.json"

    def ler(self) -> dict:
        return uso.coletar_tudo({"c1": self.pasta}, cache=self.cache)

    def total(self, lido: dict, campo: str) -> int:
        return sum(v.get(campo, 0) for v in lido["dias"].values())

    def linhas(self) -> list[dict]:
        quando = datetime.now().astimezone().isoformat()
        return [
            bloco_do_modelo(quando, "m1", PENSOU, None, saida=4),
            bloco_do_modelo(quando, "m1", TEXTO, None, saida=4),
            bloco_do_modelo(quando, "m1", FERRAMENTA, "tool_use", saida=1431),
        ]

    def test_resposta_inteira_numa_passada_conta_a_saida_final(self):
        self.escrever_transcript(self.arquivo, self.linhas())
        lido = self.ler()
        self.assertEqual(self.total(lido, "saida"), 1431)
        self.assertEqual(self.total(lido, "mensagens"), 1)
        self.assertEqual(self.total(lido, "entrada"), 32)
        self.assertEqual(self.total(lido, "cacheLido"), 1000)
        projetos = sum(v.get("saida", 0) for v in lido["projetos"].values())
        esforcos = sum(v.get("saida", 0) for v in lido["esforcos"].values())
        horas = sum(v.get("saida", 0) for v in lido["horas"].values())
        self.assertEqual((projetos, esforcos, horas), (1431, 1431, 1431))

    def test_resposta_partida_entre_passadas_conta_a_saida_final_uma_vez(self):
        primeira, *resto = self.linhas()
        self.escrever_transcript(self.arquivo, [primeira])
        self.assertEqual(self.total(self.ler(), "saida"), 4)
        with open(self.arquivo, "a", encoding="utf-8") as fh:
            for linha in resto:
                fh.write(json.dumps(linha) + "\n")
        lido = self.ler()
        self.assertEqual(self.total(lido, "saida"), 1431)
        self.assertEqual(self.total(lido, "mensagens"), 1)
        self.assertEqual(self.total(lido, "cacheLido"), 1000)
        # e ler de novo sem novidade nao soma nada
        self.assertEqual(self.total(self.ler(), "saida"), 1431)

    def test_conta_da_resposta_sai_do_cache_quando_o_arquivo_para(self):
        self.escrever_transcript(self.arquivo, self.linhas())
        self.ler()
        self.assertIn(str(self.arquivo), json.loads(self.cache.read_text(encoding="utf-8"))["contadosUltimos"])
        antes = time.time() - 2 * uso.RESPOSTA_FECHADA
        os.utime(self.arquivo, (antes, antes))
        self.assertEqual(self.total(self.ler(), "saida"), 1431)
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["contadosUltimos"], {})

    def test_linha_repetida_com_o_mesmo_consumo_nao_soma(self):
        """Controle: a repeticao da mesma resposta, que ja era descartada, continua descartada."""
        quando = datetime.now().astimezone().isoformat()
        linha = bloco_do_modelo(quando, "m1", TEXTO, "end_turn", saida=50)
        self.escrever_transcript(self.arquivo, [linha, linha, bloco_do_modelo(quando, "m2", TEXTO, "end_turn", 7)])
        lido = self.ler()
        self.assertEqual((self.total(lido, "saida"), self.total(lido, "mensagens")), (57, 2))

    def test_cache_da_versao_5_vira_legado_e_a_releitura_corrige_a_saida(self):
        dia = datetime.now().astimezone().date().isoformat()
        antigo = uso._cache_vazio()
        antigo["versao"] = 5
        antigo["dias"] = {f"c1|{dia}|claude-opus-5": {"saida": 4, "mensagens": 1}}
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        # o arquivo da versao 5 tem outro nome, e a migracao le dele
        self.cache.with_name("uso-cache-v2.json").write_text(json.dumps(antigo), encoding="utf-8")
        self.escrever_transcript(self.arquivo, self.linhas())
        lido = self.ler()
        self.assertEqual(self.total(lido, "saida"), 1431)
        self.assertFalse(any(v.get("aproximado") for v in lido["dias"].values()))


class LeituraDaStatusLine(ComPastaPropria):
    """Cada chamada da status line regravava o arquivo da sessao do zero."""

    def chamar(self, perfil: str = "c1", **dados) -> dict:
        limpo = {k: v for k, v in os.environ.items() if not k.startswith("LOOM_")}
        with unittest.mock.patch.dict(os.environ, limpo, clear=True):
            coletor.guardar(perfil, {"session_id": "s1", **dados})
        return json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))

    def limites(self, cinco: float, sete: float, zera=1791089400) -> dict:
        return {
            "five_hour": {"used_percentage": cinco, "resets_at": zera},
            "seven_day": {"used_percentage": sete, "resets_at": zera + 3600},
        }

    def test_chamada_sem_limite_nao_apaga_o_percentual_da_sessao(self):
        self.chamar(rate_limits=self.limites(12, 97))
        salvo = self.chamar(context_window={"used_percentage": 30})
        self.assertEqual(salvo["faixas"]["five_hour"]["usado"], 12)
        self.assertEqual(salvo["faixas"]["seven_day"]["usado"], 97)
        self.assertEqual(coletor.limites_por_perfil()["c1"]["faixas"]["seven_day"]["usado"], 97)

    def test_leitura_nova_troca_a_guardada(self):
        """Controle: a faixa que chegou agora vale, e a de outra conta nao e herdada."""
        self.chamar(rate_limits=self.limites(12, 97))
        self.assertEqual(self.chamar(rate_limits=self.limites(14, 98))["faixas"]["five_hour"]["usado"], 14)
        self.assertEqual(self.chamar(perfil="c2")["faixas"], {})

    def test_faixa_guardada_nao_passa_na_frente_da_leitura_nova_de_outra_sessao(self):
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        agora = time.time()
        calada = {"sessao": "a", "perfil": "c1", "lido": agora, "lidoFaixas": agora - 3 * 3600,
                  "faixas": {"five_hour": {"usado": 10, "zera": 1}}}
        nova = {"sessao": "b", "perfil": "c1", "lido": agora - 60, "faixas": {"five_hour": {"usado": 60, "zera": 1}}}
        for dados in (calada, nova):
            (coletor.PASTA / f"{dados['sessao']}.json").write_text(json.dumps(dados), encoding="utf-8")
        self.assertEqual(coletor.limites_por_perfil()["c1"]["faixas"]["five_hour"]["usado"], 60)

    def test_gancho_renova_o_lido_mas_nao_a_hora_da_faixa(self):
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        velha = {"sessao": "s1", "perfil": "c1", "lido": 1000.0, "faixas": {"five_hour": {"usado": 10, "zera": 1}}}
        (coletor.PASTA / "s1.json").write_text(json.dumps(velha), encoding="utf-8")
        limpo = {k: v for k, v in os.environ.items() if not k.startswith("LOOM_")}
        with unittest.mock.patch.dict(os.environ, limpo, clear=True):
            coletor.marcar("c1", {"session_id": "s1"}, True)
        salvo = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertGreater(salvo["lido"], 1000.0)
        self.assertEqual(salvo["lidoFaixas"], 1000.0)

    def test_painel_fica_com_a_leitura_de_limite_mais_nova_de_verdade(self):
        conta = {"id": "claude:c1", "chaves": ["c1"]}
        agora = time.time()
        marcada = {"lido": agora, "lidoFaixas": agora - 7200, "faixas": {"five_hour": {"usado": 10}}}
        memoria = {"claude:c1": {"lido": agora - 600, "faixas": {"five_hour": {"usado": 70}}}}
        leitura = painel_uso._leitura_de_limite(conta, {"c1": marcada}, memoria)
        self.assertEqual(leitura["faixas"]["five_hour"]["usado"], 70)

    def test_status_line_nao_apaga_o_pedido_de_permissao(self):
        limpo = {k: v for k, v in os.environ.items() if not k.startswith("LOOM_")}
        with unittest.mock.patch.dict(os.environ, limpo, clear=True):
            coletor.marcar("c1", {"session_id": "s1", "message": "Claude needs your permission to use Bash"}, True)
        salvo = self.chamar(rate_limits=self.limites(12, 97))
        self.assertIn("permission", salvo["aviso"]["texto"])
        self.assertIsInstance(salvo["aviso"]["quando"], float)

    def test_reinicio_em_iso_ou_milissegundos_vira_segundos(self):
        salvo = self.chamar(rate_limits={
            "five_hour": {"used_percentage": 50, "resets_at": "2026-10-04T05:30:00Z"},
            "seven_day": {"used_percentage": 90, "resets_at": 1791079200000},
        })
        esperado = datetime(2026, 10, 4, 5, 30, tzinfo=timezone.utc).timestamp()
        self.assertEqual(salvo["faixas"]["five_hour"]["zera"], esperado)
        self.assertEqual(salvo["faixas"]["seven_day"]["zera"], 1791079200)
        # e em segundos, que e o formato de hoje, passa igual
        self.assertEqual(self.chamar(rate_limits=self.limites(1, 2))["faixas"]["five_hour"]["zera"], 1791089400)


class RolloutQueNasceDeOutro(ComPainel):
    """O rollout do revisor guardian abre com o total herdado da conversa de origem."""

    def herdado(self, ultima: dict) -> dict:
        linha = contagem_codex(carimbo(800), 2245555, 1616384, 1883)
        linha["payload"]["info"]["last_token_usage"] = ultima
        return linha

    def test_total_herdado_nao_conta_de_novo(self):
        zerada = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 76593}
        self.rollout("guardian", [
            linha_codex("session_meta", carimbo(900), {"id": "g", "parent_thread_id": "p"}),
            self.herdado(zerada),
            turno(carimbo(700), "t1", "gpt-6-sol"),
            registro_codex(carimbo(600), "r1", "t1", entrada=107000, lida=90000, saida=747),
        ])
        dias = list(self.ler_codex()["dias"].values())
        soma = tuple(sum(d.get(c, 0) for d in dias) for c in ("entrada", "cacheLido", "saida", "mensagens"))
        self.assertEqual(soma, (17000, 90000, 747, 1))

    def test_primeiro_total_vale_o_que_a_ultima_resposta_gastou(self):
        """Controle: rollout sem registro ainda conta a primeira resposta inteira."""
        ultima = {"input_tokens": 300, "cached_input_tokens": 100, "output_tokens": 30}
        linha = contagem_codex(carimbo(800), 300, 100, 30)
        linha["payload"]["info"]["last_token_usage"] = ultima
        self.rollout("novo", [turno(carimbo(900), "t1", "gpt-6-sol"), linha], idade=3600)
        (dia,) = self.ler_codex()["dias"].values()
        self.assertEqual((dia["entrada"], dia["cacheLido"], dia["saida"]), (200, 100, 30))


class AvisoDeLimiteDoCodex(unittest.TestCase):
    """O reinicio da semana do Codex chega variando segundos entre um token_count e outro."""

    def leitura(self, zera: float) -> dict:
        return {"chave": "codex:padrao:7d", "ferramenta": "Codex", "conta": "Codex", "janela": "semana",
                "usado": 96.0, "zera": zera}

    def test_reinicio_que_varia_segundos_nao_repete_o_aviso(self):
        agora = 1791000000.0
        avisos, vistos = aviso_limite.avisos_novos([self.leitura(1791318911.0)], {}, agora)
        self.assertEqual(len(avisos), 1)
        for zera in (1791318913.0, 1791318911.0, 1791318924.0):
            avisos, vistos = aviso_limite.avisos_novos([self.leitura(zera)], vistos, agora + 60)
            self.assertEqual(avisos, [], zera)

    def test_semana_seguinte_avisa_de_novo(self):
        """Controle: janela que de fato virou arma o aviso outra vez."""
        agora = 1791000000.0
        _, vistos = aviso_limite.avisos_novos([self.leitura(1791318911.0)], {}, agora)
        avisos, _ = aviso_limite.avisos_novos([self.leitura(1791318911.0 + 7 * 86400)], vistos, agora + 60)
        self.assertEqual(len(avisos), 1)


if __name__ == "__main__":
    unittest.main()
