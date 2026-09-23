"""A ponte entre a tela e os shells, sem shell, sem janela e sem abrir programa nenhum.

O PTY, o editor, o navegador e o programa padrao do Windows entram falsificados, e a pasta
de dados da ponte aponta pra pasta temporaria da base.
"""

from __future__ import annotations

import base64
import json
import os
import time
import unittest.mock
from pathlib import Path

from test_usabilidade import ComPonte

from loom import bridge as mod_bridge
from loom import coletor, config, perfis, sessao_salva, shells, uso

# a da classe, antes de a base trocar por uma que nao faz nada
REVISAR_DE_VERDADE = mod_bridge.Bridge._revisar_vigia


class SessaoFalsa:
    def __init__(self, *args, **kwargs) -> None:
        self.args, self.kwargs = args, kwargs
        self.alive = True
        self.iniciada = False
        self.parada = False
        self.tamanho = (kwargs.get("cols", 80), kwargs.get("rows", 24))
        self.output = unittest.mock.MagicMock()
        self.exited = unittest.mock.MagicMock()

    def start(self) -> None:
        self.iniciada = True

    def stop(self) -> None:
        self.parada = True
        self.alive = False


class ComPonteNaPastaTemporaria(ComPonte):
    def setUp(self) -> None:
        super().setUp()
        self._dados = unittest.mock.patch.object(mod_bridge, "RAIZ_DADOS", self.raiz / "dados")
        self._dados.start()

    def tearDown(self) -> None:
        self._dados.stop()
        super().tearDown()


class ImagemColada(ComPonteNaPastaTemporaria):
    def colagens(self) -> Path:
        return self.raiz / "dados" / "colagens"

    def test_extensao_com_caminho_nao_grava_fora_da_pasta(self):
        caminho = self.ponte.salvarColagem(base64.b64encode(b"PNG").decode(), "/../../fora")
        self.assertEqual(Path(caminho).parent, self.colagens())
        self.assertTrue(caminho.endswith(".fora"))
        self.assertEqual(Path(caminho).read_bytes(), b"PNG")

    def test_sem_extensao_vira_png(self):
        self.assertTrue(self.ponte.salvarColagem(base64.b64encode(b"x").decode(), "").endswith(".png"))

    def test_conteudo_invalido_ou_vazio_nao_grava_nada(self):
        self.assertEqual(self.ponte.salvarColagem("isso nao e base64!", "png"), "")
        self.assertEqual(self.ponte.salvarColagem("", "png"), "")
        self.assertFalse(self.colagens().exists())

    def test_colagem_de_mais_de_uma_semana_e_limpa(self):
        self.colagens().mkdir(parents=True)
        velha, recente = self.colagens() / "velha.png", self.colagens() / "recente.png"
        for arquivo in (velha, recente):
            arquivo.write_bytes(b"x")
        oito_dias = time.time() - 8 * 24 * 3600
        os.utime(velha, (oito_dias, oito_dias))

        self.ponte.salvarColagem(base64.b64encode(b"nova").decode(), "png")

        self.assertFalse(velha.exists())
        self.assertTrue(recente.exists())


class LinkClicado(ComPonteNaPastaTemporaria):
    def setUp(self) -> None:
        super().setUp()
        self.projeto = self.raiz / "projeto"
        (self.projeto / "src").mkdir(parents=True)
        (self.projeto / "src" / "a.py").write_text("x", encoding="utf-8")

    def abrir(self, alvo: str, pasta: str = "", editores: dict | None = None, erro: Exception | None = None):
        abertos = []
        with (
            unittest.mock.patch.object(mod_bridge.subprocess, "Popen", side_effect=erro) as popen,
            unittest.mock.patch.object(mod_bridge, "_abrir_no_sistema", side_effect=abertos.append),
            unittest.mock.patch("shutil.which", side_effect=lambda nome: (editores or {}).get(nome)),
        ):
            aviso = self.ponte.abrirExterno(alvo, pasta)
        return aviso, popen, abertos

    def test_caminho_relativo_e_resolvido_pela_pasta_da_aba_com_linha_e_coluna(self):
        aviso, popen, _ = self.abrir("src/a.py:12:3", str(self.projeto), {"code": r"C:\VS Code\code.cmd"})
        self.assertEqual(aviso, "")
        comando = popen.call_args.args[0]
        self.assertEqual(comando, f'"C:\\VS Code\\code.cmd" -g "{self.projeto / "src/a.py"}:12"')
        self.assertEqual(popen.call_args.kwargs["creationflags"], mod_bridge.subprocess.CREATE_NO_WINDOW)

    def test_aspas_em_volta_do_caminho_sao_tiradas(self):
        aviso, popen, _ = self.abrir(f'"{self.projeto / "src" / "a.py"}"', "", {"code": "code.cmd"})
        self.assertEqual(aviso, "")
        popen.assert_called_once()

    def test_arquivo_que_nao_existe_avisa_pelo_nome_impresso(self):
        # relativo, o aviso diz em que pasta procurou
        aviso, popen, abertos = self.abrir("src/sumiu.py:3", str(self.projeto), {"code": "code.cmd"})
        self.assertEqual(aviso, f"Não achei src/sumiu.py em {self.projeto}")
        popen.assert_not_called()
        self.assertEqual(abertos, [])

    def test_editor_que_nao_sobe_avisa(self):
        aviso, _, _ = self.abrir("src/a.py:1", str(self.projeto), {"code": "code.cmd"}, erro=OSError("negado"))
        self.assertEqual(aviso, "Não consegui abrir o code")

    def test_sem_editor_configurado_abre_no_programa_padrao_calado(self):
        config.salvar({"editor": ""})
        (self.projeto / "notas.md").write_text("x", encoding="utf-8")
        aviso, popen, abertos = self.abrir(str(self.projeto / "notas.md"))
        self.assertEqual(aviso, "")
        self.assertEqual(abertos, [str(self.projeto / "notas.md")])
        popen.assert_not_called()

    def test_sem_editor_configurado_script_tambem_nao_roda(self):
        config.salvar({"editor": ""})
        aviso, _, abertos = self.abrir(str(self.projeto / "src" / "a.py"))
        self.assertTrue(aviso.startswith("Não abri a.py"), aviso)
        self.assertEqual(abertos, [])

    def test_endereco_da_web_vai_pro_navegador(self):
        with unittest.mock.patch("PySide6.QtGui.QDesktopServices") as navegador:
            aviso, popen, abertos = self.abrir("https://exemplo.com/a.py:12")
        self.assertEqual(aviso, "")
        self.assertEqual(navegador.openUrl.call_args.args[0].toString(), "https://exemplo.com/a.py:12")
        popen.assert_not_called()
        self.assertEqual(abertos, [])

    def test_alvo_vazio_nao_faz_nada(self):
        aviso, popen, abertos = self.abrir("  \"\"  ")
        self.assertEqual((aviso, abertos), ("", []))
        popen.assert_not_called()


class LinhaDoEditor(ComPonteNaPastaTemporaria):
    def achar(self, mapa: dict):
        return unittest.mock.patch("shutil.which", side_effect=lambda nome: mapa.get(nome))

    def test_zed_e_sublime_levam_a_linha_depois_dos_dois_pontos(self):
        with self.achar({"zed": r"C:\Zed\zed.exe"}):
            self.assertEqual(mod_bridge.linha_do_editor("zed", r"C:\p\a.py", "7"), r'"C:\Zed\zed.exe" "C:\p\a.py:7"')

    def test_sem_linha_vai_so_o_arquivo(self):
        with self.achar({"code": r"C:\VS Code\code.cmd"}):
            self.assertEqual(mod_bridge.linha_do_editor("code", r"C:\p\a.py", ""), r'"C:\VS Code\code.cmd" "C:\p\a.py"')

    def test_editor_desconhecido_abre_so_o_arquivo_mesmo_com_linha(self):
        with self.achar({"vim": r"C:\vim\vim.exe"}):
            self.assertEqual(mod_bridge.linha_do_editor("vim", r"C:\p\a.py", "7"), r'"C:\vim\vim.exe" "C:\p\a.py"')

    def test_editor_ausente_ou_em_branco_nao_monta_nada(self):
        with self.achar({}):
            self.assertIsNone(mod_bridge.linha_do_editor("code", r"C:\p\a.py", "7"))
            self.assertIsNone(mod_bridge.linha_do_editor('""', r"C:\p\a.py", "7"))

    def test_aspas_sem_fechar_ainda_separam_o_executavel(self):
        self.assertEqual(mod_bridge._separar_editor('"C:\\Meu Editor\\e.exe'), ("C:\\Meu Editor\\e.exe", ""))
        self.assertEqual(mod_bridge._separar_editor("code --reuse-window"), ("code", "--reuse-window"))

    def test_o_que_o_windows_executaria_ao_abrir(self):
        with unittest.mock.patch.dict(os.environ, {"PATHEXT": ".COM;.EXE;.PS1"}):
            self.assertTrue(mod_bridge._roda_ao_abrir(r"C:\p\deploy.PS1"))
            self.assertTrue(mod_bridge._roda_ao_abrir(r"C:\p\setup.PY"))
            self.assertFalse(mod_bridge._roda_ao_abrir(r"C:\p\notas.md"))
            self.assertFalse(mod_bridge._roda_ao_abrir(r"C:\p\sem-extensao"))

    def test_pasta_pessoal_com_barra_no_fim_e_outra_caixa_ainda_e_ela(self):
        with unittest.mock.patch("os.path.expanduser", return_value=r"C:\Users\eu"):
            self.assertTrue(mod_bridge._e_a_pasta_pessoal("c:\\users\\eu\\"))
            self.assertFalse(mod_bridge._e_a_pasta_pessoal(r"C:\Users\eu\proj"))


class AbasAnotadas(ComPonteNaPastaTemporaria):
    def test_lixo_da_tela_nao_apaga_a_ordem_que_ja_estava(self):
        self.ponte.saveTabs(json.dumps([{"shell": "pwsh", "sessao": "a1"}]))
        self.ponte.saveTabs("{quebrado")
        self.ponte.saveTabs(json.dumps({"shell": "pwsh"}))
        self.assertEqual(self.ponte._abas, [{"shell": "pwsh", "sessao": "a1"}])

    def test_entrada_que_nao_e_aba_fica_de_fora(self):
        self.ponte.saveTabs(json.dumps([{"shell": "pwsh"}, "solta", 3]))
        self.assertEqual(self.ponte._abas, [{"shell": "pwsh"}])

    def test_sem_restaurar_abas_a_tela_nao_recebe_as_do_disco(self):
        sessao_salva.gravar([{"shell": "pwsh", "apelido": "x"}])
        self.assertEqual(json.loads(self.ponte.savedTabs())[0]["apelido"], "x")
        config.salvar({"restaurarAbas": False})
        self.assertEqual(json.loads(self.ponte.savedTabs()), [])

    def test_ferramenta_e_ajuste_ilegiveis_nao_derrubam(self):
        self.assertEqual(self.ponte.saveTool("{quebrado"), "{}")
        antes = json.loads(self.ponte.getConfig())
        self.assertEqual(json.loads(self.ponte.saveConfig("{quebrado")), antes)
        salva = json.loads(self.ponte.saveTool(json.dumps({"nome": "Meu CLI", "env": "X", "login": "l"})))
        self.assertEqual(salva["id"], "meu-cli")


class AbrirAba(ComPonteNaPastaTemporaria):
    def setUp(self) -> None:
        super().setUp()
        self.ponte._shells = [
            shells.Shell("cmd", "Prompt", r"C:\Windows\System32\cmd.exe"),
            shells.Shell("pwsh", "PowerShell 7", r"C:\pwsh\pwsh.exe", ("-NoLogo",)),
        ]
        self.criadas: list[SessaoFalsa] = []

        def criar(*args, **kwargs):
            falsa = SessaoFalsa(*args, **kwargs)
            self.criadas.append(falsa)
            return falsa

        self._falsos = [
            unittest.mock.patch.object(mod_bridge, "PtySession", side_effect=criar),
            # a conta principal de verdade nao entra: herdar le o cadastro da pasta pessoal
            unittest.mock.patch.object(mod_bridge.mod_perfis, "herdar_confianca", return_value=0),
            unittest.mock.patch("os.path.expanduser", side_effect=lambda c: c.replace("~", str(self.casa), 1)),
        ]
        for falso in self._falsos:
            falso.start()

    def tearDown(self) -> None:
        for falso in self._falsos:
            falso.stop()
        super().tearDown()

    def test_shell_desconhecido_cai_no_padrao(self):
        sid = self.ponte._abrir("nao-existe", "", 80, 24, False)
        self.assertEqual(self.criadas[0].kwargs["argv"], [r"C:\pwsh\pwsh.exe", "-NoLogo"])
        self.assertEqual(self.ponte._origem[sid]["shell"], "pwsh")

    def test_aba_sem_conta_leva_so_a_marca_da_aba(self):
        sid = self.ponte._abrir("cmd", "", 100, 30, False)
        falsa = self.criadas[0]
        self.assertEqual(falsa.kwargs["env"], {"LOOM_ABA": sid})
        self.assertEqual((falsa.kwargs["cols"], falsa.kwargs["rows"]), (100, 30))
        self.assertTrue(falsa.iniciada)
        self.assertIs(self.ponte._sessions[sid], falsa)
        self.assertEqual(self.digitado, [])

    def test_aba_da_conta_leva_a_pasta_dela_e_sobe_o_cli(self):
        conta = perfis.criar("Uma")
        sid = self.ponte._abrir("cmd", conta.id, 80, 24, False)
        env = self.criadas[0].kwargs["env"]
        self.assertEqual(
            (env["LOOM_ABA"], env["LOOM_PERFIL"], env["CLAUDE_CONFIG_DIR"]), (sid, conta.id, str(conta.config_dir))
        )
        self.assertIn("coletar.py", (conta.config_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(self.digitado, [(sid, "claude", 700)])

    def test_abertura_automatica_desligada_nao_digita(self):
        config.salvar({"abrirCliDaConta": False})
        conta = perfis.criar("Uma")
        self.ponte._abrir("cmd", conta.id, 80, 24, False)
        self.assertEqual(self.digitado, [])

    def test_elevar_com_sudo_que_serve_poe_o_sudo_na_frente(self):
        self.ponte._elevacao = {"disponivel": True, "motivo": "", "comando": r"C:\gsudo\gsudo.exe"}
        sid = self.ponte._abrir("pwsh", "", 80, 24, True)
        self.assertEqual(self.criadas[0].kwargs["argv"], [r"C:\gsudo\gsudo.exe", r"C:\pwsh\pwsh.exe", "-NoLogo"])
        self.assertTrue(self.ponte._origem[sid]["elevado"])

    def test_elevar_sem_sudo_abre_o_shell_normal(self):
        self.ponte._elevacao = {"disponivel": False, "motivo": "desligado", "comando": ""}
        self.ponte._abrir("pwsh", "", 80, 24, True)
        self.assertEqual(self.criadas[0].kwargs["argv"], [r"C:\pwsh\pwsh.exe", "-NoLogo"])

    def test_pasta_que_sumiu_abre_na_de_sempre(self):
        with unittest.mock.patch.object(self.ponte, "_abrir", return_value="s1") as abrir:
            self.ponte.openSessionWith("cmd", "", 80, 24, json.dumps({"pasta": str(self.raiz / "sumiu")}))
            self.ponte.openSessionWith("cmd", "", 80, 24, json.dumps({"pasta": str(self.raiz), "elevar": True}))
        self.assertEqual(abrir.call_args_list[0].args, ("cmd", "", 80, 24, False, None))
        self.assertEqual(abrir.call_args_list[1].args, ("cmd", "", 80, 24, True, str(self.raiz)))

    def test_pedido_ilegivel_abre_uma_aba_comum(self):
        with unittest.mock.patch.object(self.ponte, "_abrir", return_value="s1") as abrir:
            self.assertEqual(self.ponte.openSessionWith("cmd", "", 80, 24, "{quebrado"), "s1")
        self.assertEqual(abrir.call_args.args, ("cmd", "", 80, 24, False, None))
        self.assertEqual(self.digitado, [])

    def test_retomar_desliga_a_abertura_automatica_so_durante_a_abertura(self):
        durante = []

        def abrir(*args):
            durante.append(self.ponte._sem_autoabrir)
            raise OSError("pty nao subiu")

        with unittest.mock.patch.object(self.ponte, "_abrir", side_effect=abrir):
            with self.assertRaises(OSError):
                self.ponte.openSessionWith("cmd", "", 80, 24, json.dumps({"conversa": "abc-123"}))
        self.assertEqual(durante, [True])
        self.assertFalse(self.ponte._sem_autoabrir)

    def test_conta_principal_nao_e_cadastrada_duas_vezes(self):
        principal = self.casa / ".claude"
        with unittest.mock.patch.object(mod_bridge.mod_perfis, "pasta_principal", return_value=principal):
            primeira = json.loads(self.ponte.adoptMainAccount(""))
            segunda = json.loads(self.ponte.adoptMainAccount("De novo"))
        self.assertEqual((primeira["nome"], primeira["emprestada"]), ("Principal", True))
        self.assertEqual(segunda, {"erro": "ja existe", "id": primeira["id"], "nome": "Principal"})
        self.assertEqual(len(perfis.listar()), 1)


class FecharAbas(ComPonteNaPastaTemporaria):
    def test_fechar_para_o_shell_e_esquece_a_aba(self):
        falsa = SessaoFalsa()
        self.ponte._sessions["a1"] = falsa
        self.ponte._origem["a1"] = {"shell": "cmd"}
        self.ponte.closeSession("a1")
        self.ponte.closeSession("nunca-existiu")
        self.assertTrue(falsa.parada)
        self.assertNotIn("a1", self.ponte._sessions)
        self.assertEqual(self.ponte.sessoes_vivas(), 0)

    def test_fechar_o_app_para_todos_os_shells(self):
        falsas = [SessaoFalsa(), SessaoFalsa()]
        for i, falsa in enumerate(falsas):
            self.ponte._sessions[f"a{i}"] = falsa
        self.assertEqual(self.ponte.sessoes_vivas(), 2)
        self.ponte.shutdown()
        self.assertTrue(all(f.parada for f in falsas))
        self.assertEqual(self.ponte._sessions, {})


class RitmoDaEntrega(ComPonteNaPastaTemporaria):
    def entregar(self, instante: float, volume: int) -> None:
        self.ponte._sessions.setdefault("a1", SessaoFalsa())
        if volume:
            self.ponte._on_output("a1", "x" * volume)
        with unittest.mock.patch.object(mod_bridge.time, "monotonic", return_value=instante):
            self.ponte._flush()

    def test_despejo_grande_espaca_e_calmaria_volta_ao_rapido(self):
        self.ponte._marco_volume = 100.0
        self.entregar(100.2, 20000)
        self.assertEqual(self.ponte._flush_timer.interval(), mod_bridge.FLUSH_MINIMO_MS, "meio segundo ainda nao passou")
        self.entregar(100.6, 0)
        self.assertEqual(self.ponte._flush_timer.interval(), mod_bridge.FLUSH_MAXIMO_MS)
        self.entregar(101.2, 100)
        self.assertEqual(self.ponte._flush_timer.interval(), mod_bridge.FLUSH_MINIMO_MS)

    def test_cada_aba_recebe_o_seu_bloco_de_uma_vez(self):
        recebido = []
        self.ponte.output.connect(lambda sid, texto: recebido.append((sid, texto)))
        for sid in ("a1", "a2"):
            self.ponte._sessions[sid] = SessaoFalsa()
        for sid, pedaco in (("a1", "PS"), ("a2", "$ "), ("a1", "> ")):
            self.ponte._on_output(sid, pedaco)
        self.ponte._flush()
        self.assertEqual(sorted(recebido), [("a1", "PS> "), ("a2", "$ ")])


class RespostaDoLogin(ComPonteNaPastaTemporaria):
    def test_so_resposta_legivel_vira_cadastro_e_aviso(self):
        avisos = []
        self.ponte.perfisMudaram.connect(lambda: avisos.append(1))
        for bruto in ("", "{quebrado"):
            self.ponte._perguntando.add("C:\\conta")
            self.ponte._guardar_identidade("C:\\conta", 1.0, bruto)
            self.assertNotIn("C:\\conta", self.ponte._perguntando, "a proxima pergunta ficaria travada")
        self.assertEqual(avisos, [])

        self.ponte._guardar_identidade("C:\\conta", 5.0, json.dumps({"email": "a@b.c"}))
        self.assertEqual(avisos, [1])
        self.assertEqual(perfis._IDENTIDADE["C:\\conta"], (5.0, {"email": "a@b.c"}))

    def test_menu_com_ferramenta_sem_cli_nao_mostra_login_velho(self):
        conta = perfis.criar("Uma", "gemini")
        (conta.config_dir / "oauth_creds.json").write_text("{}", encoding="utf-8")
        perfis._IDENTIDADE[str(conta.config_dir)] = (1.0, {"email": "antigo@b.c"})
        with unittest.mock.patch.object(mod_bridge.mod_ferramentas, "por_id") as por_id:
            por_id.return_value = mod_bridge.mod_ferramentas.Ferramenta("gemini", "Gemini", "", "oauth_creds.json")
            with unittest.mock.patch.object(self.ponte, "_perguntar_identidade") as perguntar:
                lista = json.loads(self.ponte.listProfiles())
        self.assertEqual(lista[0]["email"], "")
        perguntar.assert_not_called()

    def test_ferramenta_marcada_como_achada_so_se_o_comando_existe(self):
        with unittest.mock.patch.object(mod_bridge.shutil, "which", side_effect=lambda n: n if n == "claude" else None):
            achadas = {f["id"]: f["achado"] for f in json.loads(self.ponte.listTools())}
        self.assertEqual(achadas, {"claude": True, "codex": False, "gemini": False})

    def test_tamanho_de_conta_que_nao_existe_e_zero(self):
        self.assertEqual(self.ponte.profileSize("sumiu"), 0.0)


class VigiaDosTranscripts(ComPonteNaPastaTemporaria):
    def setUp(self) -> None:
        super().setUp()
        self.conta = self.raiz / "conta"
        self.projeto = self.conta / "projects" / "proj"
        self.recente = self.escrever_transcript(self.projeto / "s1.jsonl", [self.pedido("2026-01-01T00:00:00Z", "oi")])
        self.velho = self.escrever_transcript(self.projeto / "s0.jsonl", [self.pedido("2026-01-01T00:00:00Z", "oi")])
        dois_dias = time.time() - 2 * 24 * 3600
        os.utime(self.velho, (dois_dias, dois_dias))
        self.subagente = self.escrever_transcript(
            self.projeto / "s1" / "subagents" / "agent-a.jsonl", [self.pedido("2026-01-01T00:00:00Z", "x")]
        )

    def revisar(self) -> set[str]:
        with unittest.mock.patch.object(mod_bridge.mod_uso, "pastas_de", return_value={"c1": self.conta}):
            REVISAR_DE_VERDADE(self.ponte)
        return {os.path.normcase(os.path.normpath(p)) for p in self.ponte._vigia.files() + self.ponte._vigia.directories()}

    def normal(self, caminho: Path) -> str:
        return os.path.normcase(os.path.normpath(str(caminho)))

    def test_olha_so_as_conversas_vivas_e_a_pasta_do_coletor(self):
        vigiados = self.revisar()
        self.assertIn(self.normal(self.recente), vigiados)
        self.assertIn(self.normal(self.projeto), vigiados)
        self.assertIn(self.normal(self.conta / "projects"), vigiados)
        self.assertIn(self.normal(coletor.PASTA), vigiados)
        self.assertNotIn(self.normal(self.velho), vigiados)
        self.assertNotIn(self.normal(self.subagente), vigiados)

    def test_conversa_que_envelheceu_sai_da_vigia(self):
        self.assertIn(self.normal(self.recente), self.revisar())
        dois_dias = time.time() - 2 * 24 * 3600
        os.utime(self.recente, (dois_dias, dois_dias))
        self.assertNotIn(self.normal(self.recente), self.revisar())

    def test_teto_de_arquivos_vigiados(self):
        for i in range(12):
            self.escrever_transcript(self.projeto / f"extra{i}.jsonl", [self.pedido("2026-01-01T00:00:00Z", "oi")])
        with unittest.mock.patch.object(mod_bridge, "TETO_DE_VIGIA", 6):
            self.assertLessEqual(len(self.revisar()), 6)


class LimitesDasAbas(ComPonteNaPastaTemporaria):
    def test_as_duas_visoes_vem_da_pasta_do_coletor(self):
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        (coletor.PASTA / "s1.json").write_text(
            json.dumps({"sessao": "s1", "perfil": "c1", "aba": "a1", "lido": time.time(), "faixas": {"five_hour": {"usado": 3}}}),
            encoding="utf-8",
        )
        limites = json.loads(self.ponte.getLimits())
        self.assertEqual(limites["porConta"]["c1"]["faixas"]["five_hour"]["usado"], 3)
        self.assertEqual(limites["porAba"]["a1"]["sessao"], "s1")

    def test_uso_diz_que_nao_esta_pela_metade_quando_leu_tudo(self):
        self.escrever_transcript(
            self.casa / "projects" / "p" / "s.jsonl",
            [self.resposta(time.strftime("%Y-%m-%dT%H:%M:%S"), "ok")],
        )
        with unittest.mock.patch.object(uso, "pastas_de", return_value={"c1": self.casa}):
            resposta = json.loads(self.ponte.getUsage())
        self.assertIs(resposta["parcial"], False)
        self.assertEqual(resposta["porConta"]["c1"]["hoje"]["mensagens"], 1)


if __name__ == "__main__":
    unittest.main()
