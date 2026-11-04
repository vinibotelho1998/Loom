"""Correcoes da auditoria: cada teste reproduz um defeito que existia.

Todos foram conferidos ao contrario: com o codigo antigo no lugar, o teste falha.
Nenhum abre processo: o PTY, o sudo e a status line de fora entram falsificados.
"""

from __future__ import annotations

import json
import subprocess
import unittest.mock
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path

from base import ComPastaPropria

from loom import coletor, config, perfis, sessoes, shells, uso
from loom.bridge import Bridge
from loom.pty_session import PtySession


def agora_menos(segundos: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos)).isoformat().replace("+00:00", "Z")


class ProcFalso:
    """Faz o papel do PtyProcess do pywinpty sem subir processo nenhum."""

    def __init__(self, erro: OSError | None = None):
        self.delayafterclose = 0.1
        self.fechado_com = None
        self.erro = erro

    def isalive(self) -> bool:
        return True

    def terminate(self, force=False):
        if self.erro:
            raise self.erro
        return True

    def close(self, force=False):
        self.fechado_com = force
        if self.erro:
            raise self.erro


class EncerrarAba(ComPastaPropria):
    def test_parar_solta_o_pseudo_console(self):
        """Enquanto alguem segura o PTY, o ConPTY fica aberto e o Claude de dentro vivo."""
        sessao = PtySession("aba1", argv=["x"])
        falso = ProcFalso()
        guardado = weakref.ref(falso)
        sessao._proc = falso
        del falso

        sessao.stop()

        self.assertIsNone(guardado(), "a sessao continuava segurando o PtyProcess")
        self.assertFalse(sessao.alive)

    def test_parar_fecha_os_canais_de_leitura(self):
        sessao = PtySession("aba1", argv=["x"])
        falso = ProcFalso()
        sessao._proc = falso

        sessao.stop()

        self.assertIs(falso.fechado_com, True)
        self.assertEqual(falso.delayafterclose, 0, "a espera do close travava a tela a toa")

    def test_erro_do_windows_ao_matar_nao_escapa(self):
        """Escapando, o shutdown parava no meio e as outras abas ficavam vivas."""
        sessao = PtySession("aba1", argv=["x"])
        sessao._proc = ProcFalso(erro=PermissionError(5, "Acesso negado"))

        sessao.stop()

        self.assertFalse(sessao.alive)

    def test_shell_que_saiu_sozinho_tambem_solta_o_pty(self):
        ponte = unittest.mock.MagicMock()
        sessao = unittest.mock.MagicMock()
        ponte._sessions = {"aba1": sessao}

        Bridge._on_exit(ponte, "aba1", 0)

        sessao.stop.assert_called_once_with()
        ponte.exited.emit.assert_called_once_with("aba1", 0)

    def test_saida_de_aba_ja_fechada_nao_vai_pra_tela(self):
        ponte = unittest.mock.MagicMock()
        ponte._sessions = {}
        ponte._buffers = {}

        Bridge._on_output(ponte, "fechada", "resto do shell")

        self.assertEqual(ponte._buffers, {})


class SudoSemJanela(ComPastaPropria):
    def test_prova_do_sudo_nao_abre_console(self):
        """O app nao tem console; sem a flag o sudo.exe abria uma janela preta a cada start."""
        chamadas = []

        def falso_run(argumentos, **opcoes):
            chamadas.append(opcoes)
            return subprocess.CompletedProcess(argumentos, 0, stdout="uso: sudo", stderr="")

        with (
            unittest.mock.patch.object(shells.shutil, "which", return_value=r"C:\Windows\System32\sudo.exe"),
            unittest.mock.patch.object(shells.subprocess, "run", falso_run),
            # o registro de verdade nao entra no teste: sem o modo gravado, vale a prova
            unittest.mock.patch.object(shells, "_modo_do_sudo", return_value=None),
        ):
            resposta = shells.elevacao()

        self.assertTrue(resposta["disponivel"])
        self.assertEqual(chamadas[0].get("creationflags"), getattr(subprocess, "CREATE_NO_WINDOW", 0))


class ColetorBlindado(ComPastaPropria):
    def rodar(self, payload: dict) -> str:
        import io
        import sys

        entrada = io.StringIO(json.dumps(payload))
        saida = io.StringIO()
        antigo = sys.stdin, sys.stdout, sys.argv
        try:
            sys.stdin, sys.stdout = entrada, saida
            sys.argv = ["coletar.py", "conta1", "minha-barra"]
            with unittest.mock.patch.object(coletor, "encadear", return_value="BARRA-DE-SEMPRE"):
                coletor.main()
        finally:
            sys.stdin, sys.stdout, sys.argv = antigo
        return saida.getvalue()

    def test_id_com_caminho_nao_grava_fora_da_pasta(self):
        coletor.guardar("c1", {"session_id": "../../fora"})
        self.assertFalse((self.raiz / "fora.json").exists())
        self.assertFalse(coletor.PASTA.exists() and any(coletor.PASTA.iterdir()))

    def test_id_normal_continua_gravando(self):
        coletor.guardar("c1", {"session_id": "3f2a9c1e-7b4d-4e7a-9d2b-0c1e2f3a4b5c"})
        self.assertTrue((coletor.PASTA / "3f2a9c1e-7b4d-4e7a-9d2b-0c1e2f3a4b5c.json").exists())

    def test_temporario_nao_sobra_quando_a_troca_falha(self):
        """Cada chamada e um processo com pid novo: o que sobrava ficava pra sempre na pasta."""
        with unittest.mock.patch.object(Path, "replace", side_effect=PermissionError(5, "em uso")):
            with self.assertRaises(OSError):
                coletor.guardar("c1", {"session_id": "s1"})
        self.assertEqual(list(coletor.PASTA.glob("*.tmp")), [])

    def test_arquivo_fora_do_formato_nao_derruba_a_leitura(self):
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        (coletor.PASTA / "estranho.json").write_text("[1, 2]", encoding="utf-8")
        coletor.guardar("c1", {"session_id": "s1"})

        self.assertEqual(list(coletor.ler_todas()), ["s1"])

    def test_status_line_de_sempre_sai_mesmo_com_campo_estranho(self):
        """O custo veio como texto e o round derrubava o processo antes de encadear."""
        saida = self.rodar({"session_id": "s1", "cost": {"total_cost_usd": "abc"}})
        self.assertEqual(saida, "BARRA-DE-SEMPRE")


class ListaDeSessoes(ComPastaPropria):
    def conta(self) -> Path:
        return self.raiz / "conta"

    def conversa(self, idade: int = 10, pedido: str = "faz algo") -> list[dict]:
        return [self.pedido(agora_menos(idade + 5), pedido), self.resposta(agora_menos(idade))]

    def test_transcript_de_subagente_nao_vira_sessao(self):
        projeto = self.conta() / "projects" / "proj"
        self.escrever_transcript(projeto / "s1.jsonl", self.conversa())
        # o pedido do subagente e outro texto, senao a lista juntaria os dois pelo nome
        self.escrever_transcript(
            projeto / "s1" / "subagents" / "agent-a1.jsonl", self.conversa(pedido="procura os testes")
        )

        achadas = sessoes.listar({"conta": str(self.conta())})

        self.assertEqual([s["sessao"] for s in achadas], ["s1"])

    def test_linha_final_maior_que_a_cauda_nao_some_com_a_sessao(self):
        """Edit em arquivo grande grava o arquivo inteiro no resultado da ferramenta."""
        enorme = {
            "type": "user",
            "timestamp": agora_menos(3),
            "cwd": r"C:\proj",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "x" * (sessoes.CAUDA * 2)}],
            },
        }
        self.escrever_transcript(
            self.conta() / "projects" / "proj" / "s1.jsonl", self.conversa(30) + [enorme]
        )

        achadas = sessoes.listar({"conta": str(self.conta())})

        self.assertEqual(len(achadas), 1, "a sessao sumia da lista")
        self.assertEqual(achadas[0]["estado"], "pensando")

    def test_bloco_fora_do_formato_nao_derruba_a_lista(self):
        estranha = self.resposta(agora_menos(5))
        estranha["message"]["content"] = ["texto solto", {"type": "tool_use", "name": "Bash", "input": "dir"}]
        self.escrever_transcript(
            self.conta() / "projects" / "proj" / "s1.jsonl", [self.pedido(agora_menos(9), "oi"), estranha]
        )

        achadas = sessoes.listar({"conta": str(self.conta())})

        self.assertEqual(achadas[0]["ferramenta"], "Bash")


class MedidorTolerante(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.arquivo = self.casa / "projects" / "p" / "conversa.jsonl"
        self.pastas = {"c1": self.casa}

    def linha(self, ident: str, saida: int = 100, **consumo) -> str:
        registro = self.resposta(datetime.now().astimezone().isoformat(), "ok", output_tokens=saida, **consumo)
        registro["message"]["id"] = ident
        return json.dumps(registro)

    def test_token_nulo_conta_zero_em_vez_de_derrubar(self):
        self.escrever_transcript(self.arquivo, [])
        self.arquivo.write_text(self.linha("m1", cache_creation_input_tokens=None) + "\n", encoding="utf-8")

        hoje = uso.relatorio(self.pastas)["c1"]["hoje"]

        self.assertEqual(hoje["saida"], 100)
        self.assertEqual(hoje["cacheEscrito"], 0)

    def test_resposta_partida_entre_duas_leituras_conta_uma_vez(self):
        """Os blocos da mesma resposta chegam com segundos de distancia, cada um com o consumo."""
        self.escrever_transcript(self.arquivo, [])
        self.arquivo.write_text(self.linha("m1") + "\n", encoding="utf-8")
        uso.relatorio(self.pastas)

        with open(self.arquivo, "a", encoding="utf-8") as fh:
            fh.write(self.linha("m1") + "\n")
        segunda = uso.relatorio(self.pastas)["c1"]["hoje"]

        self.assertEqual(segunda["saida"], 100)
        self.assertEqual(segunda["mensagens"], 1)

    def test_resposta_nova_depois_da_partida_ainda_conta(self):
        """Controle: o id guardado so segura a repeticao, nao a resposta seguinte."""
        self.escrever_transcript(self.arquivo, [])
        self.arquivo.write_text(self.linha("m1") + "\n", encoding="utf-8")
        uso.relatorio(self.pastas)

        with open(self.arquivo, "a", encoding="utf-8") as fh:
            fh.write(self.linha("m2", saida=40) + "\n")

        self.assertEqual(uso.relatorio(self.pastas)["c1"]["hoje"]["saida"], 140)

    def test_cache_de_versao_anterior_ainda_serve(self):
        uso.CACHE.parent.mkdir(parents=True, exist_ok=True)
        uso.CACHE.write_text(json.dumps({"arquivos": {}, "dias": {}}), encoding="utf-8")
        self.escrever_transcript(self.arquivo, [])
        self.arquivo.write_text(self.linha("m1") + "\n", encoding="utf-8")

        self.assertEqual(uso.relatorio(self.pastas)["c1"]["hoje"]["saida"], 100)


class GravacaoInteira(ComPastaPropria):
    @staticmethod
    def disco_cheio_no_meio():
        original = Path.write_text

        def metade(caminho, texto, *args, **kwargs):
            original(caminho, texto[: len(texto) // 2], *args, **kwargs)
            raise OSError(28, "Nao ha espaco suficiente no disco")

        return unittest.mock.patch.object(Path, "write_text", metade)

    def test_falha_ao_gravar_nao_apaga_as_contas(self):
        """Arquivo pela metade virava lista vazia, e a proxima conta criada sobrescrevia as outras."""
        um = perfis.criar("Trabalho")
        perfis.criar("Pessoal")

        with self.disco_cheio_no_meio():
            with self.assertRaises(OSError):
                perfis.atualizar(um.id, nome="Outro nome")

        self.assertEqual([p.nome for p in perfis.listar()], ["Trabalho", "Pessoal"])
        self.assertEqual(list(perfis.ARQUIVO.parent.glob("*.tmp")), [])

    def test_falha_ao_gravar_nao_apaga_os_ajustes(self):
        config.salvar({"tamanhoFonte": 18})

        with self.disco_cheio_no_meio():
            with self.assertRaises(OSError):
                config.salvar({"editor": "notepad"})

        self.assertEqual(config.carregar()["tamanhoFonte"], 18)

    def test_json_valido_de_outro_formato_nao_derruba(self):
        config.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        config.ARQUIVO.write_text("null", encoding="utf-8")
        perfis.ARQUIVO.write_text('{"id": "x"}', encoding="utf-8")

        self.assertEqual(config.carregar(), config.PADRAO)
        self.assertEqual(perfis.listar(), [])


if __name__ == "__main__":
    unittest.main()
