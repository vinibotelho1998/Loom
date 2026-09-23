"""O coletor que o Claude Code chama como status line e como gancho de inicio.

Roda como processo proprio de verdade, mas aqui e chamado direto, com stdin e ambiente
falsos. A status line de sempre e o CLI nunca sobem: o subprocess entra falsificado.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import unittest.mock

from base import ComPastaPropria

from loom import coletor, config, perfis


def sem_marca_de_aba(extra: dict | None = None):
    """O teste pode rodar dentro do proprio Loom, e ai o ambiente ja traz a marca da aba."""
    limpo = {k: v for k, v in os.environ.items() if not k.startswith("LOOM_")}
    limpo.update(extra or {})
    return unittest.mock.patch.dict(os.environ, limpo, clear=True)


class ResumoDaStatusLine(ComPastaPropria):
    def guardado(self, dados: dict) -> dict:
        with sem_marca_de_aba():
            coletor.guardar("c1", {"session_id": "s1", **dados})
        return json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))

    def test_percentual_do_contexto_ganha_da_conta_pelos_tokens(self):
        salvo = self.guardado(
            {
                "context_window": {
                    "used_percentage": 12,
                    "total_input_tokens": 900000,
                    "context_window_size": 1000000,
                    "remaining_percentage": 70,
                }
            }
        )
        self.assertEqual(salvo["contexto"], 12)
        self.assertEqual(
            (salvo["contextoTokens"], salvo["contextoTeto"], salvo["contextoSobra"]), (900000, 1000000, 70)
        )

    def test_custo_modelo_e_nome_saem_arrumados(self):
        salvo = self.guardado(
            {"cost": {"total_cost_usd": 1.23456}, "model": {"display_name": "Opus 5"}, "session_name": "  deploy  "}
        )
        self.assertEqual((salvo["custo"], salvo["modelo"], salvo["titulo"]), (1.23, "Opus 5", "deploy"))

    def test_faixa_fora_do_formato_fica_de_fora(self):
        salvo = self.guardado({"rate_limits": {"five_hour": 40, "seven_day": {"used_percentage": 3}}})
        self.assertEqual(list(salvo["faixas"]), ["seven_day"])

    def test_sem_nada_de_contexto_nao_inventa_numero(self):
        salvo = self.guardado({"context_window": {"context_window_size": 0}})
        self.assertNotIn("contexto", salvo)


class GanchoDeInicio(ComPastaPropria):
    def test_retomar_a_sessao_nao_apaga_o_percentual_ja_lido(self):
        """O gancho roda de novo ao retomar e ao compactar, com a barra ja lida."""
        with sem_marca_de_aba({"LOOM_ABA": "a1"}):
            coletor.guardar("c1", {"session_id": "s1", "rate_limits": {"five_hour": {"used_percentage": 42}}})
            coletor.marcar("c1", {"session_id": "s1"})
        salvo = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(salvo["faixas"]["five_hour"]["usado"], 42)
        self.assertEqual(salvo["aba"], "a1")

    def test_de_fora_do_loom_a_marca_de_antes_continua(self):
        with sem_marca_de_aba({"LOOM_ABA": "a1"}):
            coletor.marcar("c1", {"session_id": "s1"})
        with sem_marca_de_aba():
            coletor.marcar("c1", {"session_id": "s1"})
        salvo = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual((salvo["aba"], salvo["terminal"]), ("a1", "loom"))

    def test_arquivo_estragado_e_refeito(self):
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        (coletor.PASTA / "s1.json").write_text("[1, 2", encoding="utf-8")
        with sem_marca_de_aba():
            coletor.marcar("c1", {"session_id": "s1"})
        salvo = json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual((salvo["sessao"], salvo["perfil"], salvo["faixas"]), ("s1", "c1", {}))

    def test_id_com_caminho_nao_grava_fora_da_pasta(self):
        with sem_marca_de_aba():
            coletor.marcar("c1", {"session_id": "..\\..\\fora"})
        self.assertFalse((self.raiz / "fora.json").exists())
        self.assertFalse(coletor.PASTA.exists() and any(coletor.PASTA.iterdir()))

    def test_entrada_que_nao_e_json_nao_derruba_o_gancho(self):
        entrada, saida = io.StringIO("isso nao e json"), io.StringIO()
        antigo = sys.stdin, sys.stdout, sys.argv
        try:
            sys.stdin, sys.stdout, sys.argv = entrada, saida, ["coletar.py", "--hook", "c1"]
            with sem_marca_de_aba():
                self.assertEqual(coletor.main(), 0)
        finally:
            sys.stdin, sys.stdout, sys.argv = antigo
        self.assertEqual(saida.getvalue(), "")


class ConfiarNaPastaDaSessao(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.conta = perfis.criar("Uma")
        self.cadastro = self.conta.config_dir / ".claude.json"

    def confiadas(self) -> list[str]:
        if not self.cadastro.exists():
            return []
        dados = json.loads(self.cadastro.read_text(encoding="utf-8"))
        return [p for p, d in dados.get("projects", {}).items() if d.get("hasTrustDialogAccepted")]

    def test_dentro_do_loom_a_pasta_da_sessao_fica_confiada(self):
        with sem_marca_de_aba({"LOOM_ABA": "a1"}):
            coletor.confiar_na_pasta(self.conta.id, {"workspace": {"current_dir": "C:\\proj\\"}, "cwd": "C:\\outra"})
        self.assertEqual(self.confiadas(), ["C:/proj"])

    def test_sem_workspace_vale_o_cwd(self):
        with sem_marca_de_aba({"LOOM_ABA": "a1"}):
            coletor.confiar_na_pasta(self.conta.id, {"cwd": "C:\\outra"})
        self.assertEqual(self.confiadas(), ["C:/outra"])

    def test_sessao_de_outro_terminal_nao_confia_em_nada(self):
        with sem_marca_de_aba():
            coletor.confiar_na_pasta(self.conta.id, {"cwd": "C:\\proj"})
        self.assertEqual(self.confiadas(), [])

    def test_opcao_desligada_nao_confia_em_nada(self):
        config.salvar({"confiarPastas": False})
        with sem_marca_de_aba({"LOOM_ABA": "a1"}):
            coletor.confiar_na_pasta(self.conta.id, {"cwd": "C:\\proj"})
        self.assertEqual(self.confiadas(), [])

    def test_conta_de_sempre_e_conta_que_nao_existe_ficam_de_fora(self):
        with sem_marca_de_aba({"LOOM_ABA": "a1"}), unittest.mock.patch.object(perfis, "confiar") as confiar:
            coletor.confiar_na_pasta("__padrao__", {"cwd": "C:\\proj"})
            coletor.confiar_na_pasta("sumiu", {"cwd": "C:\\proj"})
        confiar.assert_not_called()


class BarraDeSempre(ComPastaPropria):
    def rodar_com(self, **retorno):
        chamadas = []

        def falso_run(comando, **opcoes):
            chamadas.append((comando, opcoes))
            if "erro" in retorno:
                raise retorno["erro"]
            return subprocess.CompletedProcess(comando, 0, stdout=retorno.get("stdout"), stderr="")

        return chamadas, unittest.mock.patch.object(coletor.subprocess, "run", falso_run)

    def test_repassa_o_json_e_devolve_o_que_ela_imprimiu(self):
        chamadas, run = self.rodar_com(stdout="minha barra")
        with sem_marca_de_aba(), run:
            self.assertEqual(coletor.encadear('{"a": 1}', "minha-barra.cmd"), "minha barra")
        comando, opcoes = chamadas[0]
        self.assertEqual((comando, opcoes["input"], opcoes["shell"]), ("minha-barra.cmd", '{"a": 1}', True))

    def test_dentro_do_loom_com_a_opcao_ligada_nem_roda(self):
        chamadas, run = self.rodar_com(stdout="x")
        with sem_marca_de_aba({"LOOM_ABA": "a1"}), run:
            self.assertEqual(coletor.encadear("{}", "minha-barra.cmd"), "")
        self.assertEqual(chamadas, [])

    def test_dentro_do_loom_com_a_opcao_desligada_ela_aparece(self):
        config.salvar({"esconderStatusLine": False})
        _, run = self.rodar_com(stdout="x")
        with sem_marca_de_aba({"LOOM_ABA": "a1"}), run:
            self.assertEqual(coletor.encadear("{}", "minha-barra.cmd"), "x")

    def test_barra_que_trava_ou_some_vira_vazio(self):
        for erro in (subprocess.TimeoutExpired("x", 8), FileNotFoundError("x")):
            _, run = self.rodar_com(erro=erro)
            with sem_marca_de_aba(), run:
                self.assertEqual(coletor.encadear("{}", "minha-barra.cmd"), "")

    def test_saida_nula_vira_vazio(self):
        _, run = self.rodar_com(stdout=None)
        with sem_marca_de_aba(), run:
            self.assertEqual(coletor.encadear("{}", "minha-barra.cmd"), "")


class QualLeituraVale(ComPastaPropria):
    def leitura(self, sessao: str, idade: float, **campos) -> dict:
        return {"sessao": sessao, "lido": time.time() - idade, **campos}

    def test_cada_aba_fica_com_a_leitura_mais_nova(self):
        vistas = {
            "velha": self.leitura("velha", 60, aba="a1"),
            "nova": self.leitura("nova", 5, aba="a1"),
            "outra": self.leitura("outra", 30, aba="a2"),
            "de-fora": self.leitura("de-fora", 1, aba=""),
        }
        por_aba = coletor.por_aba(vistas)
        self.assertEqual({a: d["sessao"] for a, d in por_aba.items()}, {"a1": "nova", "a2": "outra"})

    def test_entre_duas_leituras_de_verdade_vale_a_mais_nova(self):
        faixas = {"five_hour": {"usado": 1}}
        vistas = {
            "velha": self.leitura("velha", 60, perfil="c1", faixas=faixas),
            "nova": self.leitura("nova", 5, perfil="c1", faixas=faixas),
        }
        self.assertEqual(coletor.limites_por_perfil(vistas)["c1"]["sessao"], "nova")

    def test_leitura_de_verdade_mais_velha_ganha_de_marca_sem_numero(self):
        vistas = {
            "marca": self.leitura("marca", 1, perfil="c1", faixas={}),
            "lida": self.leitura("lida", 300, perfil="c1", faixas={"five_hour": {"usado": 9}}),
        }
        self.assertEqual(coletor.limites_por_perfil(vistas)["c1"]["sessao"], "lida")

    def test_sessao_sem_conta_conta_como_a_de_sempre(self):
        vistas = {"s": self.leitura("s", 1, perfil="")}
        self.assertEqual(list(coletor.limites_por_perfil(vistas)), ["__padrao__"])


if __name__ == "__main__":
    unittest.main()
