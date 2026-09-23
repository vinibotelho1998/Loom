"""Regras que so tinham o caminho feliz coberto, ou nenhum teste.

Leitura do transcript, cache da lista de sessoes, pedido de permissao, soma do consumo, o
settings da conta, retomar na propria aba, abas com programa, a pergunta ao fechar e as contas
pequenas da pagina. Nada abre janela nem shell: pasta temporaria, relogio, processo e arquivo
falsos, e a pagina roda no QJSEngine.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
import unittest.mock
from collections import Counter
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from base import ComPastaPropria  # noqa: E402
from test_usabilidade import ComPonte  # noqa: E402

from loom import app as mod_app  # noqa: E402
from loom import bridge as mod_bridge  # noqa: E402
from loom import coletor, config, ferramentas, perfis, sessao_salva, sessoes, uso  # noqa: E402


def agora_menos(segundos: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos)).isoformat().replace("+00:00", "Z")


def ferramenta(nome: str, entrada: dict, quando: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": quando,
        "cwd": r"C:\proj",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": nome, "input": entrada}]},
    }


# ---------------------------------------------------------------- lista de sessoes


class CaudaDoTranscript(ComPastaPropria):
    def test_arquivo_que_nao_existe_nao_derruba(self):
        self.assertEqual(sessoes._tail(self.raiz / "sumiu.jsonl"), [])

    def test_linha_quebrada_ou_que_nao_e_objeto_fica_de_fora(self):
        alvo = self.raiz / "t.jsonl"
        alvo.write_text('lixo\n{"type": "user"}\n{quebrado\n[1, 2]\n  {"type": "assistant"}  \n', encoding="utf-8")
        self.assertEqual(sessoes._tail(alvo), [{"type": "user"}, {"type": "assistant"}])

    def test_janela_para_de_crescer_no_teto(self):
        """Uma linha so, maior que o teto, nao pode fazer a leitura crescer pra sempre."""
        alvo = self.raiz / "t.jsonl"
        alvo.write_text('{"a": "' + "x" * 5000 + '"}', encoding="utf-8")
        janelas = []
        ler_de_verdade = sessoes._ler_cauda

        def contando(caminho, tamanho, janela):
            janelas.append(janela)
            return ler_de_verdade(caminho, tamanho, janela)

        with unittest.mock.patch.multiple(sessoes, CAUDA=100, CAUDA_MAXIMA=1600), unittest.mock.patch.object(
            sessoes, "_ler_cauda", contando
        ):
            self.assertEqual(sessoes._tail(alvo), [])
        self.assertEqual(janelas, [100, 400, 1600])


class LeituraDoEstado(unittest.TestCase):
    def test_registro_que_nao_e_conversa_e_pulado(self):
        """Titulo, resumo e marca de sistema chegam depois da resposta e nao mudam o estado."""
        registros = [
            {"type": "assistant", "timestamp": "t1", "message": {"model": "claude-opus-5", "content": [{"type": "text"}]}},
            {"type": "ai-title", "aiTitle": "deploy"},
            {"type": "system", "timestamp": "t9"},
            {"type": "summary"},
        ]
        leitura = sessoes._ler_estado(registros)
        self.assertEqual((leitura["bruto"], leitura["modelo"], leitura["quando"]), ("respondeu", "claude-opus-5", "t1"))

    def test_pedido_novo_da_pessoa_e_a_vez_do_modelo(self):
        registros = [
            {"type": "assistant", "message": {"content": [{"type": "text"}]}},
            {"type": "user", "timestamp": "t2", "message": {"content": "roda os testes"}},
        ]
        self.assertEqual(sessoes._ler_estado(registros)["bruto"], "pensando")

    def test_mensagem_fora_do_formato_passa_pra_anterior(self):
        registros = [
            {"type": "assistant", "timestamp": "t1", "message": {"content": [{"type": "text"}]}},
            {"type": "user", "timestamp": "t2", "message": "texto solto"},
        ]
        leitura = sessoes._ler_estado(registros)
        self.assertEqual((leitura["bruto"], leitura["quando"]), ("respondeu", "t1"))

    def test_sem_conversa_nenhuma_fica_parada(self):
        self.assertEqual(sessoes._ler_estado([{"type": "system"}])["bruto"], "parada")

    def test_varias_ferramentas_na_mesma_resposta_mostra_a_ultima(self):
        registros = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "C:/a/b.py"}},
                        {"type": "tool_use", "name": "Grep", "input": {"pattern": "def x"}},
                    ]
                },
            }
        ]
        leitura = sessoes._ler_estado(registros)
        self.assertEqual((leitura["ferramenta"], leitura["alvo"]), ("Grep", "def x"))


class PedidoDePermissao(unittest.TestCase):
    def test_so_vale_com_ferramenta_na_ponta_e_aviso_depois_do_transcript(self):
        rodando = {"bruto": "rodando"}
        self.assertTrue(sessoes._pedindo_licenca(rodando, {"aviso": {"quando": 100}}, 100))
        self.assertFalse(sessoes._pedindo_licenca(rodando, {"aviso": {"quando": 99.9}}, 100))
        self.assertFalse(sessoes._pedindo_licenca({"bruto": "respondeu"}, {"aviso": {"quando": 200}}, 100))

    def test_aviso_fora_do_formato_nao_conta(self):
        rodando = {"bruto": "rodando"}
        for marca in ({}, {"aviso": "sim"}, {"aviso": {"quando": "200"}}, {"aviso": {}}):
            self.assertFalse(sessoes._pedindo_licenca(rodando, marca, 100), marca)


class PermissaoNaLista(ComPastaPropria):
    def test_sessao_abandonada_continua_parada_mesmo_com_aviso(self):
        """Pedido de permissao de meia hora atras nao e alguem esperando: a sessao foi largada."""
        conta = self.raiz / "conta"
        arquivo = self.escrever_transcript(
            conta / "projects" / "proj" / "s1.jsonl",
            [self.pedido(agora_menos(3000), "faz"), ferramenta("Edit", {"file_path": "a.py"}, agora_menos(2500))],
        )
        achada = sessoes.listar({"c": str(conta)}, {"s1": {"aviso": {"quando": arquivo.stat().st_mtime + 1}}})[0]
        self.assertEqual((achada["estado"], achada["espera"]), ("parada", ""))


class CacheDaLista(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.arquivo = self.escrever_transcript(
            self.raiz / "t.jsonl", [self.pedido(agora_menos(20), "oi"), self.resposta(agora_menos(10))]
        )
        self.lidas = 0
        ler_de_verdade = sessoes._tail

        def contando(caminho):
            self.lidas += 1
            return ler_de_verdade(caminho)

        remendo = unittest.mock.patch.object(sessoes, "_tail", contando)
        remendo.start()
        self.addCleanup(remendo.stop)

    def test_arquivo_parado_nao_e_lido_de_novo(self):
        primeira = sessoes._estado_de(self.arquivo, 10.0, 500)
        segunda = sessoes._estado_de(self.arquivo, 10.0, 500)
        self.assertEqual(self.lidas, 1)
        self.assertIs(primeira, segunda)

    def test_tamanho_ou_data_diferente_le_de_novo(self):
        sessoes._estado_de(self.arquivo, 10.0, 500)
        sessoes._estado_de(self.arquivo, 10.0, 501)
        sessoes._estado_de(self.arquivo, 11.0, 501)
        self.assertEqual(self.lidas, 3)

    def test_cache_cheio_e_esvaziado(self):
        sessoes._LEMBRADO.update({f"x{i}": (0.0, 0, {}) for i in range(400)})
        sessoes._estado_de(self.arquivo, 10.0, 500)
        self.assertEqual(sessoes._LEMBRADO, {})

    def test_leitura_traz_pasta_ramo_e_se_houve_conversa(self):
        leitura = sessoes._estado_de(self.arquivo, 10.0, 500)
        self.assertEqual((leitura["pasta"], leitura["conversou"], leitura["sugestao"]), (r"C:\proj", True, "oi"))

    def test_arquivo_grande_conta_como_conversa_mesmo_sem_resposta_na_cauda(self):
        self.escrever_transcript(self.arquivo, [self.pedido(agora_menos(20), "oi")])
        self.assertFalse(sessoes._estado_de(self.arquivo, 1.0, 10)["conversou"])
        self.assertTrue(sessoes._estado_de(self.arquivo, 2.0, sessoes.CAUDA + 1)["conversou"])


class ConversaRepetida(unittest.TestCase):
    def sessao(self, **campos) -> dict:
        return {"perfil": "c", "pasta": r"C:\proj", "titulo": "deploy", "idade": 10, **campos}

    def test_nome_e_pasta_em_outra_caixa_ainda_sao_a_mesma_conversa(self):
        sobram = sessoes._colapsar(
            [self.sessao(idade=50), self.sessao(pasta=r"c:\PROJ", titulo="Deploy", idade=5)]
        )
        self.assertEqual([s["idade"] for s in sobram], [5])

    def test_mesmo_nome_em_outra_conta_ou_pasta_fica(self):
        sobram = sessoes._colapsar(
            [self.sessao(), self.sessao(perfil="outra"), self.sessao(pasta=r"C:\outro")]
        )
        self.assertEqual(len(sobram), 3)


class IdadeDosSubagentes(ComPastaPropria):
    def test_sem_subagente_nao_ha_idade(self):
        self.assertIsNone(sessoes._idade_dos_subagentes(self.raiz / "s1.jsonl"))
        (self.raiz / "s1" / "subagents").mkdir(parents=True)
        self.assertIsNone(sessoes._idade_dos_subagentes(self.raiz / "s1.jsonl"))

    def test_vale_o_subagente_que_escreveu_por_ultimo(self):
        pasta = self.raiz / "s1" / "subagents"
        pasta.mkdir(parents=True)
        agora = time.time()
        for nome, idade in (("a", 500), ("b", 30), ("c", 900)):
            alvo = pasta / f"{nome}.jsonl"
            alvo.write_text("{}", encoding="utf-8")
            os.utime(alvo, (agora - idade, agora - idade))
        (pasta / "nota.txt").write_text("x", encoding="utf-8")
        idade = sessoes._idade_dos_subagentes(self.raiz / "s1.jsonl")
        self.assertAlmostEqual(idade, 30, delta=5)


# ---------------------------------------------------------------- medidor de uso


class SomaDeUmaResposta(ComPastaPropria):
    def somado(self, registro: dict) -> tuple[str | None, Counter]:
        alvo = Counter()
        return uso._somar(registro, alvo), alvo

    def test_todos_os_campos_entram(self):
        registro = self.resposta(
            "2026-05-04T12:00:00Z", cache_creation_input_tokens=7, cache_read_input_tokens=3
        )
        chave, alvo = self.somado(registro)
        dia = datetime.fromisoformat("2026-05-04T12:00:00+00:00").astimezone().date()
        self.assertEqual(chave, f"{dia}|claude-opus-5")
        self.assertEqual(
            dict(alvo),
            {"entrada": 10, "saida": 20, "cacheEscrito": 7, "cacheLido": 3, "raciocinio": 5, "mensagens": 1},
        )

    def test_dia_e_o_do_relogio_local_e_nao_o_de_greenwich(self):
        registro = self.resposta("2026-05-04T23:30:00-03:00")
        dia = datetime.fromisoformat("2026-05-04T23:30:00-03:00").astimezone().date()
        self.assertEqual(self.somado(registro)[0].split("|")[0], str(dia))

    def test_sem_modelo_vira_desconhecido(self):
        registro = self.resposta("2026-05-04T12:00:00Z")
        del registro["message"]["model"]
        self.assertTrue(self.somado(registro)[0].endswith("|desconhecido"))

    def test_o_que_nao_e_resposta_nao_soma(self):
        valido = self.resposta("2026-05-04T12:00:00Z")
        casos = []
        for mexer in (
            lambda r: r["message"].update(role="user"),
            lambda r: r["message"].update(usage={}),
            lambda r: r["message"].update(usage="muito"),
            lambda r: r.update(timestamp=""),
            lambda r: r.update(timestamp="ontem"),
            lambda r: r.update(message="texto"),
        ):
            registro = json.loads(json.dumps(valido))
            mexer(registro)
            casos.append(self.somado(registro))
        for chave, alvo in casos:
            self.assertIsNone(chave)
            self.assertEqual(sum(alvo.values()), 0)

    def test_detalhe_de_raciocinio_fora_do_formato_conta_zero(self):
        registro = self.resposta("2026-05-04T12:00:00Z", output_tokens_details=[1, 2], output_tokens=True)
        _, alvo = self.somado(registro)
        self.assertEqual((alvo["raciocinio"], alvo["saida"], alvo["mensagens"]), (0, 0, 1))


class PastasDoMedidor(ComPastaPropria):
    def test_so_conta_de_claude_entra(self):
        claude = perfis.criar("Uma")
        perfis.criar("Outra", ferramenta="codex")
        with unittest.mock.patch.object(uso, "_pasta_padrao", return_value=self.casa / ".claude"):
            pastas = uso.pastas_de(perfis.listar())
        self.assertEqual(set(pastas), {"__padrao__", claude.id})

    def test_sem_a_de_sempre_quando_pedido(self):
        claude = perfis.criar("Uma")
        with unittest.mock.patch.object(uso, "_pasta_padrao", return_value=self.casa / ".claude"):
            self.assertEqual(set(uso.pastas_de(perfis.listar(), incluir_padrao=False)), {claude.id})


class TranscriptQueSumiu(ComPastaPropria):
    def test_gasto_de_transcript_apagado_continua_no_relatorio(self):
        """O Claude apaga transcript velho; o gasto que ja foi lido nao pode sumir junto."""
        hoje = datetime.now().astimezone().replace(hour=12).astimezone(timezone.utc)
        conta = self.raiz / "conta"
        arquivo = self.escrever_transcript(
            conta / "projects" / "p" / "s.jsonl", [self.resposta(hoje.isoformat().replace("+00:00", "Z"))]
        )
        uso.coletar({"c": conta})
        arquivo.unlink()
        dias = uso.coletar({"c": conta})
        self.assertEqual(sum(v["saida"] for v in dias.values()), 20)


# ---------------------------------------------------------------- coletor


class AvisoDoClaude(ComPastaPropria):
    def marcado(self, dados: dict, aviso: bool) -> dict:
        limpo = {k: v for k, v in os.environ.items() if not k.startswith("LOOM_")}
        with unittest.mock.patch.dict(os.environ, limpo, clear=True):
            coletor.marcar("c1", {"session_id": "s1", **dados}, aviso)
        return json.loads((coletor.PASTA / "s1.json").read_text(encoding="utf-8"))

    def test_aviso_guarda_a_hora_e_o_texto_curto(self):
        antes = time.time()
        salvo = self.marcado({"message": "x" * 500}, aviso=True)
        self.assertGreaterEqual(salvo["aviso"]["quando"], antes)
        self.assertEqual(salvo["aviso"]["texto"], "x" * 160)

    def test_aviso_nao_apaga_a_leitura_da_status_line(self):
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        (coletor.PASTA / "s1.json").write_text(
            json.dumps({"sessao": "s1", "faixas": {"five_hour": {"usado": 40}}, "contexto": 12, "lido": 1}),
            encoding="utf-8",
        )
        salvo = self.marcado({"message": None}, aviso=True)
        self.assertEqual((salvo["faixas"], salvo["contexto"], salvo["aviso"]["texto"]), ({"five_hour": {"usado": 40}}, 12, ""))

    def test_gancho_de_inicio_nao_inventa_aviso(self):
        self.assertNotIn("aviso", self.marcado({"message": "oi"}, aviso=False))


class ArgumentosDoColetor(ComPastaPropria):
    def rodar(self, argumentos: list[str], entrada: dict) -> tuple[unittest.mock.Mock, unittest.mock.Mock]:
        limpo = {k: v for k, v in os.environ.items() if not k.startswith("LOOM_")}
        with unittest.mock.patch.dict(os.environ, limpo, clear=True), unittest.mock.patch.object(
            coletor.sys, "argv", ["coletar.py", *argumentos]
        ), unittest.mock.patch.object(coletor.sys, "stdin", unittest.mock.Mock(read=lambda: json.dumps(entrada))), \
                unittest.mock.patch.object(coletor, "marcar") as marcar, \
                unittest.mock.patch.object(coletor, "confiar_na_pasta") as confiar, \
                unittest.mock.patch.object(coletor, "encadear", return_value="") as encadear, \
                unittest.mock.patch.object(coletor.sys, "stdout", unittest.mock.Mock()):
            self.assertEqual(coletor.main(), 0)
        self.encadear = encadear
        return marcar, confiar

    def test_aviso_marca_e_nao_confia_na_pasta(self):
        marcar, confiar = self.rodar(["--aviso", "c1"], {"session_id": "s1"})
        marcar.assert_called_once_with("c1", {"session_id": "s1"}, True)
        confiar.assert_not_called()
        self.encadear.assert_not_called()

    def test_gancho_de_inicio_marca_e_confia(self):
        marcar, confiar = self.rodar(["--hook", "c1"], {"session_id": "s1"})
        marcar.assert_called_once_with("c1", {"session_id": "s1"}, False)
        confiar.assert_called_once_with("c1", {"session_id": "s1"})

    def test_status_line_repassa_a_de_antes(self):
        with unittest.mock.patch.object(coletor, "guardar") as guardar:
            self.rodar(["c1", "minha-barra.cmd"], {"session_id": "s1"})
        guardar.assert_called_once_with("c1", {"session_id": "s1"})
        self.assertEqual(self.encadear.call_args.args[1], "minha-barra.cmd")


# ---------------------------------------------------------------- settings da conta


class SettingsForaDoFormato(ComPastaPropria):
    """O settings.json da conta pode ser editado a mao. JSON valido mas de outro formato
    derrubava a abertura da aba inteira, porque o coletor e instalado ao abrir."""

    def setUp(self) -> None:
        super().setUp()
        self.conta = perfis.criar("Uma")
        self.settings = self.conta.config_dir / "settings.json"

    def test_json_de_outro_formato_e_refeito_com_o_coletor(self):
        for texto in ("null", "[]", '"texto"', '{"statusLine": "barra.cmd"}', '{"statusLine": {"command": 3}}'):
            self.settings.write_text(texto, encoding="utf-8")
            self.assertTrue(perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj"), texto)
            salvo = json.loads(self.settings.read_text(encoding="utf-8"))
            self.assertIn("coletar.py", salvo["statusLine"]["command"], texto)

    def test_status_line_da_pessoa_continua_encadeada(self):
        self.settings.write_text(json.dumps({"statusLine": {"command": "minha-barra.cmd"}}), encoding="utf-8")
        perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj")
        comando = json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"]["command"]
        self.assertTrue(comando.endswith(f'{self.conta.id} "minha-barra.cmd"'))
        # cada aba nova reinstala: a segunda vez nao pode perder a barra nem encadear o coletor nele mesmo
        self.assertFalse(perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj"))
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"]["command"], comando)

    def test_trocar_do_codigo_pro_instalado_leva_a_barra_junto(self):
        anterior = 'npx ccstatus --tema "escuro"'
        self.settings.write_text(json.dumps({"statusLine": {"command": anterior}}), encoding="utf-8")
        perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj")
        with unittest.mock.patch.object(perfis.sys, "frozen", True, create=True):
            perfis.instalar_coletor(self.conta, "C:\\Loom\\Loom.exe", "ignorado")
        comando = json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"]["command"]
        self.assertEqual(comando, f'"C:\\Loom\\loom-coletor.exe" {self.conta.id} "npx ccstatus --tema \\"escuro\\""')

    def test_coletor_sem_barra_de_antes_continua_sem(self):
        perfis.instalar_coletor(self.conta, "py.exe", "C:\\proj")
        self.assertEqual(perfis._statusline_anterior(self.conta.config_dir), "")


def argumentos_do_windows(linha: str) -> list[str]:
    """Parte a linha de comando pela regra do proprio Windows, sem subir processo nenhum."""
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    quantos = ctypes.c_int()
    vetor = shell32.CommandLineToArgvW(linha, ctypes.byref(quantos))
    try:
        return [vetor[i] for i in range(quantos.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(vetor, ctypes.c_void_p))


@unittest.skipUnless(sys.platform == "win32", "regra de linha de comando do Windows")
class BarraComAspasChegaInteira(ComPastaPropria):
    """O coletor le a status line de antes num argumento so: aspas dentro dela partiam ele no meio."""

    def setUp(self) -> None:
        super().setUp()
        self.conta = perfis.criar("Uma")
        self.settings = self.conta.config_dir / "settings.json"

    def comando(self) -> str:
        return json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"]["command"]

    def test_caminho_com_espaco_entre_aspas_chega_inteiro_no_coletor(self):
        anterior = '"C:\\Users\\Maria Clara\\barra.cmd" --tema "escuro"'
        self.settings.write_text(json.dumps({"statusLine": {"command": anterior}}), encoding="utf-8")
        perfis.instalar_coletor(self.conta, "C:\\py\\python.exe", "C:\\proj")
        # depois do script vem o perfil e a barra de antes, que e o que o coletor le
        self.assertEqual(argumentos_do_windows(self.comando())[2:], [self.conta.id, anterior])

    def test_reinstalar_ao_abrir_outra_aba_nao_mexe_na_barra(self):
        anterior = 'npx ccstatus --tema "escuro"'
        self.settings.write_text(json.dumps({"statusLine": {"command": anterior}}), encoding="utf-8")
        perfis.instalar_coletor(self.conta, "C:\\py\\python.exe", "C:\\proj")
        primeiro = self.comando()
        self.assertFalse(perfis.instalar_coletor(self.conta, "C:\\py\\python.exe", "C:\\proj"))
        self.assertEqual(self.comando(), primeiro)
        self.assertEqual(perfis._statusline_anterior(self.conta.config_dir), anterior)


class GanchosDoColetor(unittest.TestCase):
    def test_tirar_deixa_os_da_pessoa_e_apaga_o_que_esvaziou(self):
        atual = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"command": '"py" "C:\\p\\coletar.py" --hook x'}]},
                    {"hooks": [{"command": "meu.cmd"}]},
                ],
                "Notification": [{"hooks": [{"command": '"C:\\d\\status-line.cmd" --aviso __padrao__'}]}],
                "PreToolUse": [{"hooks": [{"command": '"py" "C:\\p\\coletar.py" --hook x'}]}],
            }
        }
        perfis._tirar_ganchos(atual)
        self.assertEqual(
            atual["hooks"],
            {
                "SessionStart": [{"hooks": [{"command": "meu.cmd"}]}],
                # evento que nao e nosso nao e tocado, mesmo com cara de coletor
                "PreToolUse": [{"hooks": [{"command": '"py" "C:\\p\\coletar.py" --hook x'}]}],
            },
        )

    def test_sem_nada_sobrando_a_chave_de_ganchos_sai(self):
        atual = {"hooks": {"SessionStart": [{"hooks": [{"command": "x loom.coletor --hook a"}]}]}, "model": "opus"}
        perfis._tirar_ganchos(atual)
        self.assertEqual(atual, {"model": "opus"})

    def test_atalho_de_status_line_so_e_nosso_com_a_bandeira_de_gancho(self):
        self.assertTrue(perfis._gancho_nosso('"C:\\d\\status-line.cmd" --hook __padrao__'))
        self.assertFalse(perfis._gancho_nosso('"C:\\d\\status-line.cmd" __padrao__'))
        self.assertFalse(perfis._gancho_nosso('"C:\\d\\status-line.cmd" --hookzinho'))
        self.assertFalse(perfis._grupo_nosso("texto"))
        self.assertFalse(perfis._grupo_nosso({"hooks": None}))
        self.assertFalse(perfis._grupo_nosso({"hooks": ["x", None]}))

    def test_status_line_de_antes_sai_do_atalho(self):
        texto = perfis._texto_do_atalho("py.exe", "C:\\proj", "minha-barra.cmd --curta")
        self.assertEqual(perfis._anterior_do_atalho(texto), "minha-barra.cmd --curta")
        self.assertEqual(perfis._anterior_do_atalho(perfis._texto_do_atalho("py.exe", "C:\\proj", "")), "")
        self.assertEqual(perfis._anterior_do_atalho("@echo off\r\nrem nada\r\n"), "")


class TamanhoGuardado(ComPastaPropria):
    def test_medida_vale_por_um_minuto(self):
        conta = perfis.criar("Uma")
        (conta.config_dir / "a.bin").write_bytes(b"x" * 10)
        with unittest.mock.patch.object(perfis.time, "monotonic", return_value=1000.0):
            self.assertEqual(perfis.tamanho(conta), 10)
        (conta.config_dir / "b.bin").write_bytes(b"x" * 5)
        with unittest.mock.patch.object(perfis.time, "monotonic", return_value=1000.0 + perfis.VALIDADE_DO_TAMANHO - 1):
            self.assertEqual(perfis.tamanho(conta), 10)
        with unittest.mock.patch.object(perfis.time, "monotonic", return_value=1000.0 + perfis.VALIDADE_DO_TAMANHO):
            self.assertEqual(perfis.tamanho(conta), 15)


class LeituraDaPrincipalForaDoPadrao(ComPastaPropria):
    """Ligar e desligar a leitura da conta de sempre com settings que nao e o do caminho feliz."""

    def setUp(self) -> None:
        super().setUp()
        self.principal = self.casa / ".claude"
        remendo = unittest.mock.patch.object(perfis, "pasta_principal", return_value=self.principal)
        remendo.start()
        self.addCleanup(remendo.stop)
        self.projeto = self.raiz / "Loom"
        self.projeto.mkdir()
        (self.projeto / "coletar.py").write_text("", encoding="utf-8")
        self.alvo = self.principal / "settings.json"

    def ler(self) -> dict:
        return json.loads(self.alvo.read_text(encoding="utf-8"))

    def fuga_do_atalho(self) -> str:
        linhas = (perfis.RAIZ / "status-line.cmd").read_text(encoding="utf-8").splitlines()
        return linhas[linhas.index(") else (") + 1]

    def test_barra_com_aspas_vai_escapada_e_volta_igual(self):
        anterior = 'npx ccstatus --tema "escuro"'
        original = {"statusLine": {"type": "command", "command": anterior}}
        self.alvo.write_text(json.dumps(original), encoding="utf-8")
        self.assertTrue(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        self.assertTrue(self.ler()["statusLine"]["command"].endswith(' __padrao__ "npx ccstatus --tema \\"escuro\\""'))
        self.assertEqual(self.fuga_do_atalho(), "  " + anterior)
        self.assertTrue(perfis.desligar_leitura_principal())
        self.assertEqual(self.ler(), original)

    def test_atalho_de_outra_ferramenta_vira_a_barra_de_antes(self):
        outro = self.raiz / "outro" / "status-line.cmd"
        outro.parent.mkdir()
        outro.write_text("@echo off\r\nrem feito por outra ferramenta\r\n", encoding="utf-8")
        original = {"statusLine": {"type": "command", "command": f'"{outro}" x'}}
        self.alvo.write_text(json.dumps(original), encoding="utf-8")
        self.assertFalse(perfis.leitura_principal_ligada())
        self.assertTrue(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        self.assertEqual(self.fuga_do_atalho(), f'  "{outro}" x')
        self.assertTrue(perfis.desligar_leitura_principal())
        self.assertEqual(self.ler(), original)

    def test_settings_que_nao_e_objeto_nao_e_reescrito(self):
        self.alvo.write_text("[1, 2]", encoding="utf-8")
        self.assertFalse(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        self.assertFalse(perfis.desligar_leitura_principal())
        self.assertFalse(perfis.leitura_principal_ligada())
        self.assertEqual(self.alvo.read_text(encoding="utf-8"), "[1, 2]")

    def test_sem_settings_ligar_cria_e_desligar_limpa(self):
        self.assertFalse(perfis.desligar_leitura_principal())
        self.assertTrue(perfis.ligar_leitura_principal("py.exe", str(self.projeto)))
        self.assertTrue(perfis.leitura_principal_ligada())
        self.assertTrue(perfis.desligar_leitura_principal())
        self.assertEqual(self.ler(), {})

    def test_atalho_apagado_deixa_de_contar_como_ligado(self):
        self.alvo.write_text("{}", encoding="utf-8")
        perfis.ligar_leitura_principal("py.exe", str(self.projeto))
        (perfis.RAIZ / "status-line.cmd").unlink()
        self.assertFalse(perfis.leitura_principal_ligada())


class BarraDeBaixoDesligada(ComPastaPropria):
    def escondida(self, **ambiente) -> bool:
        limpo = {k: v for k, v in os.environ.items() if not k.startswith("LOOM_")}
        with unittest.mock.patch.dict(os.environ, {**limpo, **ambiente}, clear=True):
            return coletor.esconder_aqui()

    def test_sem_a_barra_de_baixo_a_status_line_de_sempre_volta(self):
        """Com as duas desligadas a pessoa ficava sem limite, contexto e modelo em lugar nenhum."""
        self.assertTrue(self.escondida(LOOM_ABA="a1"))
        config.salvar({"mostrarUsoNaBarra": False})
        self.assertFalse(self.escondida(LOOM_ABA="a1"))

    def test_fora_do_loom_nunca_esconde(self):
        self.assertFalse(self.escondida())


class PerguntaEComandoLocal(unittest.TestCase):
    def test_plano_esperando_aprovacao_e_pergunta(self):
        registros = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {}}]}}
        ]
        leitura = sessoes._ler_estado(registros)
        self.assertEqual((leitura["bruto"], leitura["ferramenta"]), ("pergunta", "ExitPlanMode"))

    def test_saida_de_comando_local_devolve_a_vez(self):
        for fala in ("  <local-command-stdout>Set model</local-command-stdout>", "<local-command-stderr>x</local-command-stderr>"):
            registros = [
                {"type": "assistant", "message": {"content": [{"type": "text"}]}},
                {"type": "user", "message": {"content": fala}},
            ]
            self.assertEqual(sessoes._ler_estado(registros)["bruto"], "respondeu", fala)

    def test_fala_que_so_cita_o_esc_continua_sendo_pedido(self):
        registros = [
            {"type": "user", "message": {"content": [{"type": "text", "text": "o [Request interrupted by user] voltou"}]}}
        ]
        self.assertEqual(sessoes._ler_estado(registros)["bruto"], "pensando")

    def test_pergunta_espera_ate_a_sessao_ser_largada(self):
        self.assertEqual(sessoes._decidir("pergunta", sessoes.SEGUNDOS_ATIVO * 5), "aguardando")
        self.assertEqual(sessoes._decidir("pergunta", sessoes.SEGUNDOS_PARADA), "parada")


class TituloDaAbaGuardada(ComPastaPropria):
    def test_nome_da_conversa_vai_pro_disco_cortado(self):
        sessao_salva.gravar([{"shell": "pwsh", "titulo": "x" * 200}, {"shell": "cmd"}])
        self.assertEqual([a["titulo"] for a in sessao_salva.ler()], ["x" * 80, ""])


# ---------------------------------------------------------------- ponte e janela


class RetomarNaPropriaAba(ComPonte):
    def setUp(self) -> None:
        super().setUp()
        self.sessao = unittest.mock.MagicMock(alive=True, pid=10)
        self.ponte._sessions["a1"] = self.sessao
        self.ponte._origem["a1"] = {"shell": "pwsh", "elevado": False}

    def retomar(self, conversa: str = "3f2a-9c1e", perfil: str = "", filhos=False, aba: str = "a1") -> bool:
        with unittest.mock.patch.object(mod_bridge.mod_pty, "tem_filhos", return_value=filhos):
            return self.ponte.retomarNaAba(aba, perfil, conversa)

    def test_shell_parado_no_prompt_retoma_ali_mesmo(self):
        self.assertTrue(self.retomar())
        self.assertEqual(self.digitado, [("a1", "claude --resume 3f2a-9c1e", 0)])

    def test_conta_de_outra_ferramenta_usa_o_comando_dela(self):
        conta = perfis.criar("Uma", "codex")
        self.assertTrue(self.retomar(perfil=conta.id))
        self.assertEqual(self.digitado, [("a1", "codex --resume 3f2a-9c1e", 0)])

    def test_programa_rodando_ou_sem_saber_fica_pra_outra_aba(self):
        """Digitar por cima de um Claude rodando mandaria o comando pra dentro da conversa dele."""
        for filhos in (True, None):
            self.assertFalse(self.retomar(filhos=filhos), filhos)
        self.assertEqual(self.digitado, [])

    def test_aba_elevada_morta_ou_que_nao_existe_nao_retoma(self):
        self.ponte._origem["a1"]["elevado"] = True
        self.assertFalse(self.retomar())
        self.ponte._origem["a1"]["elevado"] = False
        self.sessao.alive = False
        self.assertFalse(self.retomar())
        self.assertFalse(self.retomar(aba="nunca-existiu"))
        self.assertEqual(self.digitado, [])

    def test_conversa_com_cara_de_comando_nao_e_digitada(self):
        for conversa in ("x; del *", "3f2a\n", "", "a b", "x" * 129):
            self.assertFalse(self.retomar(conversa=conversa), conversa)
        self.assertEqual(self.digitado, [])

    def test_ferramenta_sem_comando_nao_digita(self):
        sem = ferramentas.Ferramenta("x", "X", "", "login.json")
        with unittest.mock.patch.object(mod_bridge.mod_ferramentas, "por_id", return_value=sem):
            self.assertFalse(self.retomar())
        self.assertEqual(self.digitado, [])


class AbasComProgramaNoShell(ComPonte):
    PROCESSOS = [
        (10, 1, "pwsh.exe"),
        (11, 10, "claude.exe"),
        (20, 1, "pwsh.exe"),
        (21, 20, "conhost.exe"),
        (30, 1, "pwsh.exe"),
    ]

    def setUp(self) -> None:
        super().setUp()
        for sid, pid, elevado, viva in (
            ("com-claude", 10, False, True),
            ("so-console", 20, False, True),
            ("elevada", 30, True, True),
            ("morta", 10, False, False),
            ("sem-pid", None, False, True),
        ):
            self.ponte._sessions[sid] = unittest.mock.MagicMock(alive=viva, pid=pid)
            self.ponte._origem[sid] = {"elevado": elevado}

    def ocupadas(self, processos) -> tuple[list[str], int]:
        with unittest.mock.patch.object(mod_bridge.mod_pty, "_processos", return_value=processos) as foto:
            ids = json.loads(self.ponte.abasComPrograma())
            quantas = self.ponte.sessoes_ocupadas()
        self.fotos = foto.call_count
        return sorted(ids), quantas

    def test_conta_quem_roda_programa_a_elevada_e_a_sem_pid(self):
        self.assertEqual(self.ocupadas(self.PROCESSOS), (["com-claude", "elevada", "sem-pid"], 3))
        self.assertEqual(self.fotos, 2, "uma foto por consulta, e nao uma por aba")

    def test_sem_foto_dos_processos_toda_aba_viva_conta(self):
        self.assertEqual(self.ocupadas(None), (["com-claude", "elevada", "sem-pid", "so-console"], 4))


class AbaReabertaSemCli(ComPonte):
    def test_aba_reaberta_nao_sobe_um_claude_em_branco(self):
        durante = []

        def abrir(*args):
            durante.append(self.ponte._sem_autoabrir)
            return "s1"

        with unittest.mock.patch.object(self.ponte, "_abrir", side_effect=abrir):
            self.ponte.openSessionWith("cmd", "", 80, 24, json.dumps({"semCli": True}))
            self.ponte.openSessionWith("cmd", "", 80, 24, json.dumps({}))
        self.assertEqual(durante, [True, False])
        self.assertFalse(self.ponte._sem_autoabrir)
        self.assertEqual(self.digitado, [])


class JanelaAoFechar:
    _pergunta_ao_fechar = mod_app.MainWindow._pergunta_ao_fechar

    def __init__(self, vivas: int, ocupadas: int) -> None:
        self.bridge = unittest.mock.MagicMock()
        self.bridge.sessoes_vivas.return_value = vivas
        self.bridge.sessoes_ocupadas.return_value = ocupadas


class PerguntaAoFecharAJanela(ComPastaPropria):
    def pergunta(self, vivas: int, ocupadas: int = 0) -> str:
        return JanelaAoFechar(vivas, ocupadas)._pergunta_ao_fechar()

    def test_abas_paradas_no_prompt_fecham_sem_perguntar(self):
        """Perguntava sempre, e a pessoa aprendia a clicar em Fechar tudo sem ler."""
        self.assertEqual(self.pergunta(3, 0), "")
        self.assertEqual(self.pergunta(0, 0), "")

    def test_programa_rodando_pergunta_com_a_conta_certa(self):
        self.assertEqual(
            self.pergunta(3, 1), "1 aba com programa rodando. Fechar a janela encerra tudo que estiver rodando dentro."
        )
        self.assertTrue(self.pergunta(3, 2).startswith("2 abas com programa rodando."))

    def test_sem_restaurar_abas_pergunta_sempre_que_ha_aba(self):
        config.salvar({"restaurarAbas": False})
        self.assertEqual(
            self.pergunta(1), "1 aba aberta. Fechar a janela encerra todas, e elas não voltam na próxima vez."
        )
        self.assertTrue(self.pergunta(4).startswith("4 abas abertas."))
        self.assertEqual(self.pergunta(0), "")

    def test_confirmacao_desligada_nunca_pergunta(self):
        config.salvar({"confirmarFechar": False, "restaurarAbas": False})
        self.assertEqual(self.pergunta(5, 5), "")


# ---------------------------------------------------------------- pagina


class ComPaginaInteira(unittest.TestCase):
    """A pagina no QJSEngine, com acesso ao estado que o test_pagina nao expoe."""

    ACESSO = (
        "\nvar deDentro = {"
        "get ordem() { return ordem; }, "
        "set sessoesVistas(v) { sessoes = v; }, "
        "set limitesPorAba(v) { limitesPorAba = v; }, "
        "set leituraPrincipal(v) { leituraPrincipal = v; }, "
        "set perfis(v) { perfis = v; }, "
        "get abaAtiva() { return abaAtiva; }, "
        "set abaAtiva(v) { abaAtiva = v; }, "
        "set bridge(v) { bridge = v; }, "
        "set cfg(v) { cfg = v; }, "
        "set abasComPrograma(v) { abasComPrograma = v; }, "
        "get fechamentoPendente() { return fechamentoPendente; }, "
        "set fechamentoPendente(v) { fechamentoPendente = v; }"
        "};\n"
    )

    @classmethod
    def setUpClass(cls) -> None:
        from test_pagina import NAVEGADOR_FALSO, garantir_app, texto_da_pagina

        garantir_app()
        from PySide6.QtQml import QJSEngine

        cls.motor = QJSEngine()
        cls.motor.evaluate(NAVEGADOR_FALSO)
        carga = cls.motor.evaluate(texto_da_pagina() + cls.ACESSO, "pagina.js")
        assert not carga.isError(), carga.toString()

    def setUp(self) -> None:
        self.definir(
            "deDentro.ordem.length = 0; deDentro.sessoesVistas = []; deDentro.limitesPorAba = {};"
            "deDentro.leituraPrincipal = null; deDentro.perfis = []; deDentro.abaAtiva = null;"
            "deDentro.bridge = null; deDentro.cfg = {}; deDentro.abasComPrograma = null;"
            "deDentro.fechamentoPendente = null;"
        )

    def rodar(self, expressao: str):
        resultado = self.motor.evaluate("JSON.stringify(" + expressao + ")")
        self.assertFalse(resultado.isError(), resultado.toString())
        texto = resultado.toString()
        return None if texto == "undefined" else json.loads(texto)

    def definir(self, codigo: str) -> None:
        resultado = self.motor.evaluate(codigo)
        self.assertFalse(resultado.isError(), resultado.toString())

    def com_trocas(self, trocas: dict[str, str], corpo: str):
        """Roda `corpo` com funcoes da pagina trocadas por falsas, e poe as de verdade de volta.

        As falsas anotam o que receberam em `anotado`, que o corpo pode devolver.
        """
        guarda = "; ".join(f"antes.{nome} = {nome}; {nome} = {falsa}" for nome, falsa in trocas.items())
        volta = "; ".join(f"{nome} = antes.{nome}" for nome in trocas)
        return self.rodar(
            f"(() => {{ const antes = {{}}; const anotado = []; {guarda};"
            f" try {{ {corpo} }} finally {{ {volta}; }} }})()"
        )


class NomeDoModelo(ComPaginaInteira):
    def test_versao_com_ponto_e_sem_data(self):
        self.assertEqual(self.rodar('nomeDeModelo("claude-sonnet-4-5-20250929")'), "Sonnet 4.5")
        self.assertEqual(self.rodar('nomeDeModelo("claude-opus-4-1")'), "Opus 4.1")
        self.assertEqual(self.rodar('nomeDeModelo("claude-3-5-haiku-20241022")'), "3.5 Haiku")
        self.assertEqual(self.rodar("nomeDeModelo(null)"), "")

    def test_janela_grande_vira_sufixo_curto(self):
        self.assertEqual(self.rodar('nomeCurtoDoModelo("Sonnet 4.5 (1M context)")'), "Sonnet 4.5 · 1M")
        self.assertEqual(self.rodar('nomeCurtoDoModelo("Opus 5 (200k Context)")'), "Opus 5 · 200k")
        self.assertEqual(self.rodar('nomeCurtoDoModelo("Opus 5")'), "Opus 5")


class ContasDoGrafico(ComPaginaInteira):
    def test_dia_curto_e_dia_local(self):
        self.assertEqual(self.rodar('diaCurto("2026-09-03")'), "03/09")
        self.assertEqual(self.rodar("diaLocal(new Date(2026, 0, 5, 23, 59))"), "2026-01-05")

    def test_somar_aceita_parcela_ausente_ou_incompleta(self):
        self.assertEqual(
            self.rodar("somar(somar({entrada: 1}, {saida: 2}), null)"),
            {"entrada": 1, "saida": 2, "raciocinio": 0, "cacheEscrito": 0, "cacheLido": 0, "mensagens": 0},
        )

    def test_juntar_modelos_soma_por_nome(self):
        self.assertEqual(
            self.rodar("juntarModelos(juntarModelos({}, {porModelo: {a: 1, b: 2}}), {porModelo: {a: 3}})"),
            {"porModelo": {"a": 4, "b": 2}},
        )
        self.assertEqual(self.rodar("juntarModelos({}, {})"), {"porModelo": {}})


class GradeDasAbas(ComPaginaInteira):
    def test_colunas_pela_quantidade(self):
        self.assertEqual([self.rodar(f"colunasDaGrade({n}, 0)") for n in (1, 2, 4, 5, 9, 10, 16)], [1, 2, 2, 3, 3, 4, 4])

    def test_tela_estreita_corta_as_colunas(self):
        self.assertEqual(self.rodar("colunasDaGrade(9, 1000)"), 2)
        self.assertEqual(self.rodar("colunasDaGrade(9, 300)"), 1)
        self.assertEqual(self.rodar("colunasDaGrade(2, 5000)"), 2)


class AvisosDaSessao(ComPaginaInteira):
    def test_motivo_da_espera_so_quando_esta_esperando(self):
        self.assertEqual(
            self.rodar('motivoDaEspera({estado: "aguardando", espera: "permissao", ferramenta: "Bash"})'),
            "permission Bash",
        )
        self.assertEqual(self.rodar('motivoDaEspera({estado: "aguardando", espera: "pergunta", ferramenta: "X"})'), "question")
        self.assertEqual(self.rodar('motivoDaEspera({estado: "aguardando", espera: ""})'), "")
        self.assertEqual(self.rodar('motivoDaEspera({estado: "rodando", espera: "permissao"})'), "")
        self.assertEqual(self.rodar("motivoDaEspera(null)"), "")

    def test_texto_do_aviso_por_motivo(self):
        self.assertIn("pergunta", self.rodar('textoDoAviso({espera: "pergunta"})'))
        self.assertIn("permissão", self.rodar('textoDoAviso({espera: "permissao"})'))
        self.assertIn("Terminou", self.rodar("textoDoAviso({})"))

    def test_aviso_longo_fica_mais_tempo_ate_o_teto(self):
        self.assertEqual(self.rodar('tempoDoAviso("")'), 2000)
        self.assertEqual(self.rodar('tempoDoAviso("x".repeat(20))'), 3000)
        self.assertEqual(self.rodar('tempoDoAviso("x".repeat(1000))'), 9000)

    def test_colar_conta_toda_quebra_de_linha(self):
        self.assertEqual(self.rodar('contarLinhas("a\\r\\nb\\rc\\nd")'), 4)
        self.assertEqual(self.rodar('contarLinhas("so uma")'), 1)


class AbaESessao(ComPaginaInteira):
    def test_pasta_da_aba_pela_ordem_de_certeza(self):
        self.assertEqual(
            self.rodar('pastaDaAba({sessao: {pasta: "A"}, pastaAtual: "B", pedidoInicial: {pasta: "C"}})'), "A"
        )
        self.assertEqual(self.rodar('pastaDaAba({sessao: {}, pastaAtual: "B", pedidoInicial: {pasta: "C"}})'), "B")
        self.assertEqual(self.rodar('pastaDaAba({pedidoInicial: {pasta: "C"}})'), "C")
        self.assertEqual(self.rodar("pastaDaAba({})"), "")
        self.assertEqual(self.rodar('pastaDaAba({ehPainel: true, pastaAtual: "B"})'), "")

    def test_sessao_so_pertence_a_aba_pareada(self):
        self.definir(
            'deDentro.ordem.push({ehPainel: true, sessao: {sessao: "s1"}, nome: "painel"},'
            ' {sessao: {sessao: "s2"}, nome: "outra"}, {sessao: {sessao: "s1"}, nome: "certa"}, {nome: "vazia"});'
        )
        self.assertEqual(self.rodar('abaDaSessao({sessao: "s1"}).nome'), "certa")
        self.assertIsNone(self.rodar('abaDaSessao({sessao: "s9"})'))
        self.assertFalse(self.rodar('ehDaCasa({sessao: "s9"})'))

    def test_abas_da_conta_so_as_vivas(self):
        self.definir(
            'deDentro.ordem.push({viva: true, perfil: {id: "c1"}, nome: "a"}, {viva: false, perfil: {id: "c1"}, nome: "b"},'
            ' {viva: true, perfil: {id: "c2"}, nome: "c"}, {viva: true, nome: "d"}, {ehPainel: true, viva: true, perfil: {id: "c1"}});'
        )
        self.assertEqual(self.rodar('abasDaConta("c1").map(a => a.nome)'), ["a"])

    def test_conta_removida(self):
        self.definir('deDentro.perfis = [{id: "c1"}]')
        self.assertFalse(self.rodar('contaRemovida({perfil: {id: "c1"}})'))
        self.assertTrue(self.rodar('contaRemovida({perfil: {id: "c2"}})'))
        self.assertFalse(self.rodar("contaRemovida({})"))

    def test_leitura_da_aba_so_com_programa_rodando(self):
        self.definir('deDentro.limitesPorAba = {a1: {titulo: "deploy", contexto: 40}}')
        self.assertEqual(self.rodar('tituloVivoDaAba({sessionId: "a1"})'), "deploy")
        self.assertIsNone(self.rodar('leituraDaAba({sessionId: "a1", programaVivo: false})'))
        self.assertIsNone(self.rodar('leituraDaAba({sessionId: "a1", ehPainel: true})'))
        self.assertIsNone(self.rodar('leituraDaAba({sessionId: "a2"})'))
        self.assertEqual(self.rodar('tituloVivoDaAba({sessionId: "a2"})'), "")

    def test_contexto_vivo_ganha_da_sessao(self):
        self.definir('deDentro.limitesPorAba = {a1: {contexto: 40}, a2: {contexto: "x"}}')
        self.assertEqual(self.rodar('contextoDe({sessionId: "a1", sessao: {contexto: 10}})'), 40)
        self.assertEqual(self.rodar('contextoDe({sessionId: "a2", sessao: {contexto: 10}})'), 10)
        self.assertIsNone(self.rodar('contextoDe({sessionId: "a2", sessao: {}})'))

    def test_conta_da_barra(self):
        self.assertEqual(self.rodar("chaveDaAba()"), "__padrao__")
        self.definir('deDentro.perfis = [{id: "c1"}, {id: "c2", padrao: true}]')
        self.assertEqual(self.rodar("chaveDaAba()"), "c2")
        self.definir('deDentro.abaAtiva = {perfil: {id: "c1"}}')
        self.assertEqual(self.rodar("chaveDaAba()"), "c1")
        self.definir('deDentro.abaAtiva = {ehPainel: true, perfil: {id: "c1"}}')
        self.assertEqual(self.rodar("chaveDaAba()"), "c2")

    def test_principal_sem_leitura_so_quando_a_ponte_disse_que_nao(self):
        self.assertFalse(self.rodar("principalSemLeitura(null)"))
        self.definir("deDentro.leituraPrincipal = false")
        self.assertTrue(self.rodar("principalSemLeitura(null)"))
        self.assertTrue(self.rodar("principalSemLeitura({padrao: true})"))
        self.assertFalse(self.rodar('principalSemLeitura({id: "c1"})'))


class RetomarAConversaDaAba(ComPaginaInteira):
    """O Retomar do botao direito da aba: na propria aba quando o shell esta parado, senao em outra."""

    ABA = (
        '{conversaAnterior: "c1", tituloAnterior: "deploy", perfil: {id: "p1"}, shell: {id: "pwsh"},'
        ' viva: VIVA, sessionId: "a1", pastaAtual: "C:/proj", pintar() { this.pintada = true; }}'
    )
    TROCAS = {
        "novaAba": "(...a) => anotado.push(['nova', ...a])",
        "ativar": "(a) => anotado.push(['ativou', a.sessionId])",
        "anotarAbas": "() => {}",
    }

    def retomar(self, resposta: str | None, viva: bool = True) -> dict:
        """`resposta` e o que a ponte devolve ao retomarNaAba; None e a pagina sem ponte."""
        if resposta is None:
            self.definir("var ponteFalsa = null; deDentro.bridge = null")
        else:
            self.definir(
                "var ponteFalsa = {pedidos: [], retomarNaAba(...a) { this.pedidos.push(a.slice(0, 3)); a[3]("
                + resposta
                + "); }}; deDentro.bridge = ponteFalsa"
            )
        aba = self.ABA.replace("VIVA", "true" if viva else "false")
        return self.com_trocas(
            self.TROCAS,
            f"const aba = {aba}; retomarAntes(aba);"
            " return {anotado, conversa: aba.conversaAnterior, titulo: aba.tituloAnterior, pintada: !!aba.pintada,"
            " pedidos: ponteFalsa ? ponteFalsa.pedidos : []};",
        )

    def test_shell_parado_retoma_na_propria_aba(self):
        resultado = self.retomar("true")
        self.assertEqual(resultado["pedidos"], [["a1", "p1", "c1"]])
        self.assertEqual(resultado["anotado"], [["ativou", "a1"]])
        self.assertEqual((resultado["conversa"], resultado["titulo"], resultado["pintada"]), ("", "", True))

    def test_shell_ocupado_abre_outra_aba_na_mesma_pasta(self):
        resultado = self.retomar("false")
        self.assertEqual(resultado["anotado"], [["nova", "pwsh", "p1", {"pasta": "C:/proj", "conversa": "c1"}]])
        self.assertEqual(resultado["conversa"], "")

    def test_aba_encerrada_ou_sem_ponte_nem_pergunta(self):
        for resultado in (self.retomar("true", viva=False), self.retomar(None)):
            self.assertEqual(resultado["pedidos"], [])
            self.assertEqual(resultado["anotado"], [["nova", "pwsh", "p1", {"pasta": "C:/proj", "conversa": "c1"}]])

    def test_sem_conversa_anterior_nao_faz_nada(self):
        resultado = self.com_trocas(self.TROCAS, "retomarAntes({conversaAnterior: ''}); return anotado;")
        self.assertEqual(resultado, [])


class RetomarSessaoDaLista(ComPaginaInteira):
    TROCAS = {
        "novaAba": "(...a) => anotado.push(['nova', ...a])",
        "piscarAviso": "(t) => anotado.push(['aviso', t])",
        "perguntar": "(t, x, opcoes) => anotado.push(['pergunta', t, opcoes])",
    }

    def retomar(self, sessao: str, clicar_segunda: bool = False) -> list:
        clique = "anotado[0][2][1].acao();" if clicar_segunda else ""
        return self.com_trocas(self.TROCAS, f"retomarSessao({sessao}); {clique} return anotado;")

    def test_conversa_de_conta_cadastrada_abre_nela(self):
        self.definir('deDentro.perfis = [{id: "p1"}]')
        self.assertEqual(
            self.retomar('{perfil: "p1", sessao: "s1", pasta: "C:/p", estado: "aguardando"}'),
            [["nova", "", "p1", {"pasta": "C:/p", "conversa": "s1"}]],
        )

    def test_conversa_da_pasta_de_sempre_abre_sem_conta(self):
        self.assertEqual(
            self.retomar('{perfil: "__padrao__", conta: "p9", sessao: "s1", pasta: "C:/p", estado: "parada"}'),
            [["nova", "", "", {"pasta": "C:/p", "conversa": "s1"}]],
        )

    def test_conta_removida_so_avisa(self):
        anotado = self.retomar('{perfil: "sumiu", sessao: "s1", pasta: "C:/p", estado: "parada"}')
        self.assertEqual([a[0] for a in anotado], ["aviso"])

    def test_conversa_trabalhando_pergunta_antes_e_so_retoma_se_confirmar(self):
        self.definir('deDentro.perfis = [{id: "p1"}]')
        sessao = '{perfil: "p1", sessao: "s1", pasta: "C:/p", estado: "rodando"}'
        anotado = self.retomar(sessao)
        self.assertEqual(len(anotado), 1)
        self.assertEqual(anotado[0][0], "pergunta")
        self.assertEqual([o["rotulo"] for o in anotado[0][2]], ["Cancelar", "Retomar mesmo assim"])
        anotado = self.retomar(sessao, clicar_segunda=True)
        self.assertEqual(anotado[1], ["nova", "", "p1", {"pasta": "C:/p", "conversa": "s1"}])


class FecharAbaComCuidado(ComPaginaInteira):
    TROCAS = {
        "fechar": "(a) => anotado.push(['fechou', a.nome])",
        "pedirConfirmacaoDeFechar": "(a) => anotado.push(['perguntou', a.nome])",
    }

    def fechar(self, aba: str, filhos: str | None = None, na_ordem: bool = True, confirmar: bool = True) -> list:
        self.definir("deDentro.cfg = {confirmarFechar: %s}" % ("true" if confirmar else "false"))
        if filhos is None:
            self.definir("deDentro.bridge = null")
        else:
            self.definir("deDentro.bridge = {temFilhos(sid, responder) { responder(%s); }}" % filhos)
        empurrar = "deDentro.ordem.push(aba);" if na_ordem else ""
        return self.com_trocas(self.TROCAS, f"const aba = {aba}; {empurrar} fecharComCuidado(aba); return anotado;")

    def test_shell_parado_no_prompt_fecha_sem_perguntar(self):
        self.assertEqual(self.fechar('{nome: "a", viva: true, sessionId: "a1"}', "false"), [["fechou", "a"]])

    def test_programa_rodando_no_shell_pergunta(self):
        self.assertEqual(self.fechar('{nome: "a", viva: true, sessionId: "a1"}', "true"), [["perguntou", "a"]])

    def test_conversa_elevada_ou_sem_ponte_pergunta_sem_consultar(self):
        casos = (
            ('{nome: "a", viva: true, sessionId: "a1", sessao: {estado: "aguardando"}}', "false"),
            ('{nome: "a", viva: true, sessionId: "a1", elevado: true}', "false"),
            ('{nome: "a", viva: true, sessionId: null}', "false"),
            ('{nome: "a", viva: true, sessionId: "a1"}', None),
        )
        for aba, filhos in casos:
            self.assertEqual(self.fechar(aba, filhos), [["perguntou", "a"]], aba)

    def test_conversa_ja_parada_nao_segura_o_fechamento(self):
        self.assertEqual(
            self.fechar('{nome: "a", viva: true, sessionId: "a1", sessao: {estado: "parada"}}', "false"), [["fechou", "a"]]
        )

    def test_sem_confirmacao_painel_ou_aba_morta_fecham_direto(self):
        self.assertEqual(self.fechar('{nome: "a", viva: true, sessionId: "a1"}', "true", confirmar=False), [["fechou", "a"]])
        self.assertEqual(self.fechar('{nome: "p", viva: true, ehPainel: true}', "true"), [["fechou", "p"]])
        self.assertEqual(self.fechar('{nome: "m", viva: false, sessionId: "a1"}', "true"), [["fechou", "m"]])

    def test_aba_que_sumiu_enquanto_consultava_nao_fecha_de_novo(self):
        self.assertEqual(self.fechar('{nome: "a", viva: true, sessionId: "a1"}', "false", na_ordem=False), [])


class ConversaDaAbaSemPrograma(ComPaginaInteira):
    """Saindo do Claude o shell fica: a conversa vira o Retomar da aba em vez de continuar dona dela."""

    TROCAS = {
        "avisarProntas": "() => {}",
        "anotarAbas": "() => {}",
        "aplicarAlturaExternas": "() => {}",
        "pintarUsoDaBarra": "() => {}",
        "montarCartao": "(s) => { anotado.push(s.sessao); return {}; }",
    }
    SESSOES = (
        '[{sessao: "velha", aba: "a1", idade: 900, estado: "aguardando", bruto: "respondeu", titulo: "antiga", terminal: "loom"},'
        ' {sessao: "nova", aba: "a1", idade: 5, estado: "aguardando", bruto: "respondeu", titulo: "deploy", terminal: "loom"},'
        ' {sessao: "fora", idade: 1, estado: "rodando", bruto: "rodando", terminal: ""}]'
    )

    def montar(self, com_programa: str, elevada: bool = False) -> dict:
        return self.com_trocas(
            self.TROCAS,
            "const aba = {viva: true, sessionId: 'a1', elevado: %s, refletir(s) { this.dono = s ? s.sessao : null; }};"
            " deDentro.ordem.length = 0; deDentro.ordem.push(aba);"
            " deDentro.sessoesVistas = %s; deDentro.abasComPrograma = %s;"
            " montarSessoes();"
            " return {dono: aba.dono, anterior: aba.conversaAnterior || '', titulo: aba.tituloAnterior || '',"
            " vivo: aba.programaVivo, cartoes: anotado};" % ("true" if elevada else "false", self.SESSOES, com_programa),
        )

    def test_com_o_claude_rodando_a_mais_recente_e_a_dona(self):
        self.assertEqual(
            self.montar("new Set(['a1'])"),
            {"dono": "nova", "anterior": "", "titulo": "", "vivo": True, "cartoes": ["fora"]},
        )

    def test_sem_programa_a_conversa_vira_o_retomar_da_aba(self):
        self.assertEqual(
            self.montar("new Set()"),
            {"dono": None, "anterior": "nova", "titulo": "deploy", "vivo": False, "cartoes": ["fora"]},
        )

    def test_elevada_ou_sem_consulta_fica_como_era(self):
        for com_programa, elevada in (("new Set()", True), ("null", False)):
            resultado = self.montar(com_programa, elevada)
            self.assertEqual((resultado["dono"], resultado["vivo"]), ("nova", True), com_programa)


class ArquivoSoltoNaGrade(ComPaginaInteira):
    def soltar(self) -> list:
        return self.com_trocas(
            {"ativar": "(a) => anotado.push(['ativou', a.sessionId])", "piscarAviso": "() => {}"},
            "const telaDeBaixo = {}, telaDoPainel = {};"
            " const aba = (sid, tela) => ({sessionId: sid, tela, foco() {},"
            "   term: {paste: (t) => anotado.push([sid, t])}});"
            " const deCima = aba('a1', {}), deBaixo = aba('a2', telaDeBaixo);"
            " const painel = {ehPainel: true, sessionId: 'p', tela: telaDoPainel};"
            " deDentro.ordem.push(deCima, deBaixo, painel); deDentro.abaAtiva = deCima;"
            " const documentoAntes = document;"
            " document = {elementFromPoint: (x, y) => y < 100 ? null"
            "   : {closest: (seletor) => seletor !== '.tela' ? null : y < 500 ? telaDeBaixo : telaDoPainel}};"
            " try {"
            "   soltarArquivos(['C:\\\\a.png'], {x: 10, y: 300});"
            "   soltarArquivos(['C:\\\\b.png'], {x: 10, y: 50});"
            "   soltarArquivos(['C:\\\\c.png'], {x: 10, y: 800});"
            "   soltarArquivos(['C:\\\\d.png'], null);"
            " } finally { document = documentoAntes; }"
            " return anotado;",
        )

    def test_vai_pro_painel_debaixo_do_mouse_e_senao_pro_ativo(self):
        """Na grade o print solto no painel de baixo entrava na conversa do de cima."""
        self.assertEqual(
            self.soltar(),
            [
                ["ativou", "a2"],
                ["a2", "C:\\a.png "],
                ["a1", "C:\\b.png "],
                ["a1", "C:\\c.png "],
                ["a1", "C:\\d.png "],
            ],
        )

    def test_ponto_sem_aba_debaixo(self):
        self.assertIsNone(self.rodar("abaNoPonto(null)"))
        self.definir("var documentoAntes = document; document = {}")
        try:
            self.assertIsNone(self.rodar("abaNoPonto({x: 1, y: 1})"))
        finally:
            self.definir("document = documentoAntes")


if __name__ == "__main__":
    unittest.main()
