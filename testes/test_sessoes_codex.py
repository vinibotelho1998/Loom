"""Sessoes do Codex na lista lateral: leitura dos rollouts e pareamento com a aba."""

from __future__ import annotations

import json
import os
import time
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from base import ComPastaPropria
from test_ponte import ComPonteNaPastaTemporaria, SessaoFalsa, esperar_a_ponte

from loom import bridge as mod_bridge
from loom import perfis, sessoes_codex, shells


def carimbo(segundos_atras: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos_atras)).isoformat().replace("+00:00", "Z")


def meta(cwd: str, atras: float = 60, fonte="cli", ident="t1", **extra) -> dict:
    return {
        "timestamp": carimbo(atras),
        "type": "session_meta",
        "payload": {"id": ident, "timestamp": carimbo(atras), "cwd": cwd, "originator": "codex_cli_rs", "source": fonte, **extra},
    }


def evento(atras: float, tipo: str, **carga) -> dict:
    return {"timestamp": carimbo(atras), "type": "event_msg", "payload": {"type": tipo, **carga}}


def item(atras: float, tipo: str, **carga) -> dict:
    return {"timestamp": carimbo(atras), "type": "response_item", "payload": {"type": tipo, **carga}}


def pedido(atras: float, texto: str) -> list[dict]:
    # o Codex grava o contexto do ambiente como fala do usuario antes do pedido de verdade
    return [
        item(atras, "message", role="user", content=[{"type": "input_text", "text": "<environment_context>x</environment_context>"}]),
        item(atras, "message", role="user", content=[{"type": "input_text", "text": texto}]),
        evento(atras, "user_message", message=texto),
    ]


def contexto_do_turno(atras: float, modelo: str) -> dict:
    return {"timestamp": carimbo(atras), "type": "turn_context", "payload": {"cwd": "x", "model": modelo}}


def chamada(atras: float, call_id: str, comando: str, nome: str = "exec_command") -> dict:
    return item(atras, "function_call", name=nome, call_id=call_id, arguments=json.dumps({"cmd": comando}))


def saida(atras: float, call_id: str) -> dict:
    return item(atras, "function_call_output", call_id=call_id, output="ok")


class ComCodex(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.codex = self.casa / ".codex"
        self.projeto = str(self.raiz / "proj")

    def rollout(self, registros: list[dict], nome: str = "rollout-a.jsonl", casa: Path | None = None) -> Path:
        alvo = (casa or self.codex) / "sessions" / "2026" / "10" / "03" / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in registros) + "\n", encoding="utf-8")
        return alvo

    def listar(self, casa: Path | None = None, chave: str = "") -> list[dict]:
        return sessoes_codex.listar({chave: str(casa or self.codex)})

    def uma(self, registros: list[dict]) -> dict:
        self.rollout(registros)
        achadas = self.listar()
        self.assertEqual(len(achadas), 1)
        return achadas[0]


class Estado(ComCodex):
    def test_turno_completo_espera_voce_no_formato_do_claude(self):
        s = self.uma(
            [
                meta(self.projeto, 120),
                contexto_do_turno(100, "gpt-6.1-sol"),
                *pedido(100, "conserta o login"),
                evento(
                    90,
                    "token_count",
                    info={"last_token_usage": {"total_tokens": 51680}, "model_context_window": 258400},
                ),
                item(80, "message", role="assistant", content=[{"type": "output_text", "text": "feito"}]),
                evento(80, "task_complete", last_agent_message="feito"),
            ]
        )
        self.assertEqual(s["estado"], "aguardando")
        self.assertEqual(s["bruto"], "respondeu")
        self.assertEqual(s["cli"], "codex")
        self.assertEqual(s["perfil"], "")
        self.assertEqual(s["sessao"], "t1")
        self.assertEqual(s["titulo"], "conserta o login", "o contexto do ambiente nao e pedido de ninguem")
        self.assertEqual(s["pasta"], self.projeto)
        self.assertEqual(s["modelo"], "gpt-6.1-sol")
        self.assertEqual(s["contexto"], 20.0)
        self.assertEqual(s["aba"], "")
        self.assertEqual(s["terminal"], "")
        self.assertTrue(80 <= s["idade"] < 90)

    def test_comando_sem_saida_esta_rodando(self):
        s = self.uma([meta(self.projeto), *pedido(30, "roda"), chamada(5, "c1", "npm test")])
        self.assertEqual(s["estado"], "rodando")
        self.assertEqual(s["ferramenta"], "exec_command")
        self.assertEqual(s["alvo"], "npm test")

    def test_saida_na_ponta_e_a_vez_do_modelo(self):
        s = self.uma([meta(self.projeto), *pedido(30, "roda"), chamada(10, "c1", "dir"), saida(5, "c1")])
        self.assertEqual(s["estado"], "pensando")

    def test_em_paralelo_basta_uma_sem_saida(self):
        s = self.uma(
            [meta(self.projeto), *pedido(30, "roda"), chamada(10, "c1", "lento"), chamada(10, "c2", "rapido"), saida(5, "c2")]
        )
        self.assertEqual(s["estado"], "rodando")
        self.assertEqual(s["alvo"], "lento")

    def test_pedido_de_aprovacao_espera_sua_licenca(self):
        s = self.uma(
            [
                meta(self.projeto),
                *pedido(30, "apaga"),
                chamada(10, "c1", "rm -rf build"),
                evento(9, "exec_approval_request", call_id="c1", command=["bash", "-lc", "rm -rf build"]),
            ]
        )
        self.assertEqual(s["estado"], "aguardando")
        self.assertEqual(s["espera"], "permissao")
        self.assertEqual(s["alvo"], "rm -rf build")

    def test_pergunta_ao_usuario_espera_resposta(self):
        argumentos = json.dumps({"questions": [{"title": "Aplico agora?"}]})
        s = self.uma(
            [
                meta(self.projeto),
                *pedido(30, "ve isso"),
                item(5, "function_call", name="request_user_input", call_id="q1", arguments=argumentos),
            ]
        )
        self.assertEqual(s["estado"], "aguardando")
        self.assertEqual(s["espera"], "pergunta")
        self.assertEqual(s["alvo"], "Aplico agora?")

    def test_turno_interrompido_devolve_a_bola(self):
        s = self.uma([meta(self.projeto), *pedido(30, "faz"), chamada(20, "c1", "x"), evento(10, "turn_aborted", reason="interrupted")])
        self.assertEqual(s["estado"], "aguardando")

    def test_comentario_no_meio_do_turno_ainda_e_trabalho(self):
        s = self.uma(
            [
                meta(self.projeto),
                evento(30, "task_started"),
                *pedido(30, "faz"),
                item(5, "message", role="assistant", phase="commentary", content=[{"type": "output_text", "text": "vou olhar"}]),
            ]
        )
        self.assertEqual(s["estado"], "pensando")

    def test_resposta_sem_fase_no_turno_aberto_ainda_e_trabalho(self):
        s = self.uma(
            [
                meta(self.projeto),
                evento(30, "task_started"),
                *pedido(30, "faz"),
                item(5, "message", role="assistant", content=[{"type": "output_text", "text": "vou olhar"}]),
            ]
        )
        self.assertEqual(s["estado"], "pensando")

    def test_idade_vem_da_ultima_mensagem_e_nao_da_data_do_arquivo(self):
        """O Codex grava ajuste e contagem fora de turno; pela data do arquivo a sessao parecia viva."""
        s = self.uma(
            [
                meta(self.projeto, 3000),
                *pedido(2900, "faz"),
                chamada(2800, "c1", "x"),
                evento(1, "thread_settings_applied", thread_settings={"model": "gpt-6"}),
                evento(1, "token_count", info=None),
            ]
        )
        self.assertEqual(s["estado"], "parada")
        self.assertGreaterEqual(s["idade"], 2800)

    def test_nome_do_indice_vence_o_primeiro_pedido(self):
        self.codex.mkdir(parents=True, exist_ok=True)
        (self.codex / "session_index.jsonl").write_text(
            json.dumps({"id": "t1", "thread_name": "velho"}) + "\n" + json.dumps({"id": "t1", "thread_name": "Login novo"}) + "\n",
            encoding="utf-8",
        )
        s = self.uma([meta(self.projeto), *pedido(30, "conserta o login"), evento(20, "task_complete")])
        self.assertEqual(s["titulo"], "Login novo")

    def test_pasta_de_rascunho_do_desktop_vira_o_projeto(self):
        rascunho = r"C:\Users\Vini\Documents\Codex\2026-10-02\c-users-vini-documents-github-redator"
        s = self.uma([meta(rascunho, fonte="vscode"), *pedido(30, "oi"), evento(20, "task_complete")])
        self.assertEqual(s["pasta"], rascunho)
        self.assertEqual(s["projeto"], "redator")

    def test_subagente_e_revisor_nao_viram_sessao(self):
        self.rollout(
            [meta(self.projeto, fonte={"subagent": {"other": "guardian"}}, thread_source="guardian_review"), *pedido(30, "x")],
            "rollout-b.jsonl",
        )
        self.rollout([meta(self.projeto, ident="t2", thread_source="subagent"), *pedido(30, "x")], "rollout-c.jsonl")
        self.assertEqual(self.listar(), [])

    def test_rollout_velho_fica_de_fora(self):
        arquivo = self.rollout([meta(self.projeto), *pedido(30, "x"), evento(20, "task_complete")])
        velho = time.time() - sessoes_codex.JANELA_VIVA - 60
        os.utime(arquivo, (velho, velho))
        self.assertEqual(self.listar(), [])

    def test_sem_fala_nenhuma_nao_e_conversa(self):
        self.rollout([meta(self.projeto), contexto_do_turno(10, "gpt-6")])
        self.assertEqual(self.listar(), [])

    def test_modelo_do_ultimo_turno_mesmo_longe_da_cauda(self):
        """Na conversa longa a cauda nao tem o turn_context, e o da cabeca era de outro modelo."""
        enchimento = [item(50, "reasoning", summary=[], encrypted_content="x" * 2000) for _ in range(300)]
        s = self.uma(
            [
                meta(self.projeto, 200),
                contexto_do_turno(190, "gpt-6-astra"),
                *pedido(190, "faz"),
                contexto_do_turno(100, "gpt-6.1-sol"),
                *enchimento,
                chamada(5, "c1", "dir"),
            ]
        )
        self.assertEqual(s["modelo"], "gpt-6.1-sol")

    def test_cauda_so_com_uma_linha_enorme_cresce_a_janela(self):
        enorme = item(4, "function_call_output", call_id="c1", output="y" * (sessoes_codex.CAUDA * 2))
        s = self.uma([meta(self.projeto), *pedido(30, "faz"), chamada(5, "c1", "dir"), enorme])
        self.assertEqual(s["estado"], "pensando")


class Pastas(ComCodex):
    def test_perfil_codex_e_a_padrao_que_ninguem_cobre(self):
        self.codex.mkdir(parents=True)
        perfil = perfis.criar("Trabalho", "codex")
        perfis.criar("Claude", "claude")
        pastas = sessoes_codex.pastas_do_codex(perfis.listar())
        self.assertEqual(pastas, {perfil.id: str(perfil.config_dir), "": str(self.codex)})

    def test_padrao_cadastrada_como_perfil_nao_conta_duas_vezes(self):
        self.codex.mkdir(parents=True)
        perfil = perfis.criar("Minha", "codex", pasta=str(self.codex))
        self.assertEqual(sessoes_codex.pastas_do_codex(perfis.listar()), {perfil.id: str(self.codex)})

    def test_sem_pasta_padrao_no_disco_nao_entra(self):
        self.assertEqual(sessoes_codex.pastas_do_codex([]), {})


class Pareamento(ComCodex):
    def sessoes(self, *rollouts) -> list[dict]:
        for i, registros in enumerate(rollouts):
            self.rollout(registros, f"rollout-{i}.jsonl")
        return self.listar()

    def aba(self, ident: str, aberta: float, perfil: str = "", pasta: str = "", nota: dict | None = None, conversa=""):
        origem = {"perfil": perfil, "pasta": pasta or self.projeto, "aberta": aberta}
        if conversa:
            origem["conversa"] = conversa
        return sessoes_codex.aba_pra_parear(ident, origem, nota or {}, {"pc": str(self.raiz / "pc")})

    def test_conversa_que_nasceu_depois_da_aba_na_mesma_pasta_e_dela(self):
        lista = self.sessoes([meta(self.projeto, 60), *pedido(50, "x")])
        pareadas = sessoes_codex.parear(lista, [self.aba("aba1", time.time() - 120)])
        self.assertEqual(pareadas[0]["aba"], "aba1")
        self.assertEqual(pareadas[0]["terminal"], "loom")
        self.assertEqual([c for c in pareadas[0] if c.startswith("_")], [], "campo interno nao vai pra tela")

    def test_conversa_de_antes_da_aba_nao_e_dela(self):
        lista = self.sessoes([meta(self.projeto, 600), *pedido(500, "x")])
        pareadas = sessoes_codex.parear(lista, [self.aba("aba1", time.time() - 120)])
        self.assertEqual(pareadas[0]["aba"], "")
        self.assertEqual(pareadas[0]["terminal"], "")

    def test_outra_pasta_nao_casa(self):
        lista = self.sessoes([meta(str(self.raiz / "outro"), 60), *pedido(50, "x")])
        pareadas = sessoes_codex.parear(lista, [self.aba("aba1", time.time() - 120)])
        self.assertEqual(pareadas[0]["aba"], "")

    def test_pasta_que_a_tela_soube_depois_do_cd_casa(self):
        outra = str(self.raiz / "outro")
        lista = self.sessoes([meta(outra, 60), *pedido(50, "x")])
        aba = self.aba("aba1", time.time() - 120, nota={"pasta": outra})
        self.assertEqual(sessoes_codex.parear(lista, [aba])[0]["aba"], "aba1")

    def test_mais_recente_pra_mais_recente_sem_repetir(self):
        agora = time.time()
        lista = self.sessoes(
            [meta(self.projeto, 100, ident="velha"), *pedido(90, "x")],
            [meta(self.projeto, 30, ident="nova"), *pedido(20, "y")],
        )
        # as duas abas sao anteriores as duas conversas: so a ordem decide
        abas = [self.aba("a1", agora - 300), self.aba("a2", agora - 200)]
        pareadas = {s["sessao"]: s["aba"] for s in sessoes_codex.parear(lista, abas)}
        self.assertEqual(pareadas, {"nova": "a2", "velha": "a1"})

    def test_duas_conversas_e_uma_aba_so_uma_fica_com_ela(self):
        agora = time.time()
        lista = self.sessoes(
            [meta(self.projeto, 100, ident="velha"), *pedido(90, "x")],
            [meta(self.projeto, 30, ident="nova"), *pedido(20, "y")],
        )
        pareadas = {s["sessao"]: s["aba"] for s in sessoes_codex.parear(lista, [self.aba("a1", agora - 200)])}
        self.assertEqual(pareadas, {"nova": "a1", "velha": ""})

    def test_conversa_do_desktop_nunca_casa(self):
        lista = self.sessoes([meta(self.projeto, 60, fonte="vscode"), *pedido(50, "x")])
        self.assertEqual(sessoes_codex.parear(lista, [self.aba("aba1", time.time() - 120)])[0]["aba"], "")

    def test_conversa_retomada_acha_a_aba_pelo_id(self):
        lista = self.sessoes([meta(str(self.raiz / "longe"), 5000), *pedido(10, "x")])
        aba = self.aba("aba1", time.time() - 60, conversa="t1")
        self.assertEqual(sessoes_codex.parear(lista, [aba])[0]["aba"], "aba1")

    def test_aba_de_outra_conta_de_codex_nao_casa(self):
        lista = self.sessoes([meta(self.projeto, 60), *pedido(50, "x")])
        aba = self.aba("aba1", time.time() - 120, perfil="pc")
        self.assertEqual(sessoes_codex.parear(lista, [aba])[0]["aba"], "")

    def test_aba_do_perfil_codex_casa_com_a_pasta_dele(self):
        pc = self.raiz / "pc"
        self.rollout([meta(self.projeto, 60), *pedido(50, "x")], casa=pc)
        lista = self.listar(pc, "pc")
        aba = self.aba("aba1", time.time() - 120, perfil="pc")
        self.assertEqual(sessoes_codex.parear(lista, [aba])[0]["aba"], "aba1")

    def test_aba_de_perfil_do_claude_nao_e_candidata(self):
        self.assertIsNone(sessoes_codex.aba_pra_parear("a", {"perfil": "claude1"}, {}, {"pc": "x"}))


class NaPonte(ComPonteNaPastaTemporaria):
    def setUp(self) -> None:
        super().setUp()
        self.ponte._shells = [shells.Shell("pwsh", "PowerShell 7", r"C:\pwsh\pwsh.exe", ("-NoLogo",))]
        self._falsos = [
            unittest.mock.patch.object(mod_bridge, "PtySession", side_effect=lambda *a, **k: SessaoFalsa(*a, **k)),
            unittest.mock.patch.object(mod_bridge.mod_perfis, "herdar_confianca", return_value=0),
            unittest.mock.patch.object(mod_bridge.mod_perfis, "instalar_coletor", return_value=None),
        ]
        for falso in self._falsos:
            falso.start()

    def tearDown(self) -> None:
        for falso in self._falsos:
            falso.stop()
        super().tearDown()

    def pedir(self) -> list[dict]:
        chegou: list[str] = []
        self.ponte.sessoesProntas.connect(chegou.append)
        self.ponte.pedirSessoes()
        esperar_a_ponte(self.ponte, chegou)
        return json.loads(chegou[0])

    def test_sessao_do_codex_entra_na_lista_pareada_com_a_aba(self):
        perfil = perfis.criar("Codex", "codex")
        aba = self.ponte.openSession("pwsh", perfil.id, 80, 24)
        self.assertIn("aberta", self.ponte._origem[aba])
        alvo = perfil.config_dir / "sessions" / "2026" / "10" / "03" / "rollout-x.jsonl"
        alvo.parent.mkdir(parents=True, exist_ok=True)
        registros = [meta(os.path.expanduser("~"), 0, ident="cx"), *pedido(0, "oi"), chamada(0, "c1", "dir")]
        alvo.write_text("\n".join(json.dumps(r) for r in registros) + "\n", encoding="utf-8")
        with unittest.mock.patch.object(mod_bridge.mod_sessoes, "listar", return_value=[{"sessao": "c1", "estado": "aguardando", "idade": 3}]):
            lista = self.pedir()
        do_codex = [s for s in lista if s.get("cli") == "codex"]
        self.assertEqual([s["sessao"] for s in lista], ["c1", "cx"])
        self.assertEqual(do_codex[0]["aba"], aba)
        self.assertEqual(do_codex[0]["terminal"], "loom")
        self.assertEqual(do_codex[0]["perfil"], perfil.id)

    def test_erro_no_codex_nao_derruba_a_lista_do_claude(self):
        perfis.criar("Codex", "codex")
        with (
            unittest.mock.patch.object(mod_bridge.mod_sessoes, "listar", return_value=[{"sessao": "c1"}]),
            unittest.mock.patch.object(mod_bridge.mod_sessoes_codex, "listar", side_effect=RuntimeError("rollout ruim")),
        ):
            self.assertEqual(self.pedir(), [{"sessao": "c1"}])

    def test_erro_ao_preparar_o_codex_nao_derruba_a_lista_do_claude(self):
        with (
            unittest.mock.patch.object(mod_bridge.mod_sessoes, "listar", return_value=[{"sessao": "c1"}]),
            unittest.mock.patch.object(mod_bridge.mod_sessoes_codex, "pastas_do_codex", side_effect=OSError("disco")),
        ):
            self.assertEqual(self.pedir(), [{"sessao": "c1"}])


if __name__ == "__main__":
    unittest.main()
