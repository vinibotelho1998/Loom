"""O que estava mal feito pra quem usa: cada teste reproduz o defeito que existia.

Todos foram conferidos ao contrario: com o codigo antigo no lugar, o teste falha. Nenhum abre
processo nem janela: shell, CLI, editor, sudo e registro entram falsificados, e tudo roda na
pasta temporaria da base.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from base import ComPastaPropria  # noqa: E402

from PySide6.QtCore import QCoreApplication  # noqa: E402

import loom  # noqa: E402
from loom import app as mod_app  # noqa: E402
from loom import bridge as mod_bridge  # noqa: E402
from loom import caminhos, config, ferramentas, perfis, pty_session, sessoes, shells, uso  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "ui"


def garantir_app() -> None:
    # na hora do teste e nao no import, igual ao test_tela
    QCoreApplication.instance() or QCoreApplication([])


def agora_menos(segundos: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos)).isoformat().replace("+00:00", "Z")


class ComPonte(ComPastaPropria):
    """Uma ponte sem relogio nenhum andando e sem olhar pasta de login de verdade."""

    def setUp(self) -> None:
        super().setUp()
        garantir_app()
        # a revisao da vigia le as pastas de login, inclusive a de sempre da maquina
        self._vigia = unittest.mock.patch.object(mod_bridge.Bridge, "_revisar_vigia", lambda self: None)
        self._vigia.start()
        self.ponte = mod_bridge.Bridge()
        self.ponte._relistar.stop()
        self.ponte._flush_timer.stop()
        self.digitado: list[tuple[str, str, int]] = []
        self.ponte._digitar = lambda sid, texto, espera: self.digitado.append((sid, texto, espera))

    def tearDown(self) -> None:
        self.ponte.shutdown()
        self._vigia.stop()
        super().tearDown()


class HistoricoNaFaixa(ComPastaPropria):
    def test_historico_zero_nao_apaga_a_rolagem(self):
        """Campo apagado virava 0, e o scrollback 0 cortava na hora todas as abas."""
        self.assertEqual(config.salvar({"historico": 0})["historico"], 1000)
        self.assertEqual(config.salvar({"historico": 10**9})["historico"], 200000)

    def test_arquivo_editado_a_mao_tambem_e_contido(self):
        config.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        config.ARQUIVO.write_text(
            json.dumps({"tamanhoFonte": 99, "entrelinha": 0.2, "larguraLateral": 5000}), encoding="utf-8"
        )
        lido = config.carregar()
        self.assertEqual((lido["tamanhoFonte"], lido["entrelinha"], lido["larguraLateral"]), (22, 1.0, 520))

    def test_dentro_da_faixa_segue_igual(self):
        self.assertEqual(config.salvar({"historico": 5000})["historico"], 5000)


class SubagenteContaComoTrabalho(ComPastaPropria):
    def montar(self, com_subagente: bool) -> dict:
        conta = self.raiz / "conta"
        projeto = conta / "projects" / "proj"
        self.escrever_transcript(
            projeto / "s1.jsonl",
            [
                self.pedido(agora_menos(300), "procura os testes"),
                {
                    "type": "assistant",
                    "timestamp": agora_menos(200),
                    "cwd": r"C:\proj",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "Agent", "input": {"description": "varre"}}],
                    },
                },
            ],
        )
        if com_subagente:
            # o subagente acabou de escrever; a conversa de cima esta calada ha 200 s
            self.escrever_transcript(projeto / "s1" / "subagents" / "agent-a1.jsonl", [self.pedido(agora_menos(1), "x")])
        sessoes._LEMBRADO.clear()
        return {"conta": str(conta)}

    def test_subagente_trabalhando_mantem_a_sessao_rodando(self):
        """Sem isso, com o subagente rodando mais de 90 s, virava ready e saia o aviso."""
        achada = sessoes.listar(self.montar(com_subagente=True))[0]
        self.assertEqual(achada["estado"], "rodando")
        self.assertEqual(achada["bruto"], "rodando")

    def test_sem_subagente_a_regra_da_idade_continua(self):
        self.assertEqual(sessoes.listar(self.montar(com_subagente=False))[0]["estado"], "aguardando")


class UsoHonesto(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        self.arquivo = self.casa / "projects" / "p" / "conversa.jsonl"
        self.pastas = {"c1": self.casa}

    def linha(self, ident: str) -> str:
        registro = self.resposta(datetime.now().astimezone().isoformat(), "ok")
        registro["message"]["id"] = ident
        return json.dumps(registro)

    def test_legenda_de_modelos_soma_so_o_gerado(self):
        """As barras e os numeros sao saida; a legenda somava a entrada junto e nao batia."""
        self.escrever_transcript(self.arquivo, [])
        self.arquivo.write_text(self.linha("m1") + "\n", encoding="utf-8")
        relatorio = uso.relatorio(self.pastas)["c1"]
        self.assertEqual(relatorio["modelos"][0]["tokens"], relatorio["hoje"]["saida"])

    def test_historico_pela_metade_e_avisado_ate_terminar(self):
        anterior = uso.TETO_POR_PASSADA
        uso.TETO_POR_PASSADA = 1000
        try:
            self.escrever_transcript(self.arquivo, [])
            self.arquivo.write_text("".join(self.linha(f"m{i}") + "\n" for i in range(20)), encoding="utf-8")
            estado: dict = {}
            uso.relatorio(self.pastas, estado)
            self.assertTrue(estado["parcial"], "a primeira passada leu so o comeco")
            for _ in range(60):
                relatorio = uso.relatorio(self.pastas, estado)
                if not estado["parcial"]:
                    break
            self.assertFalse(estado["parcial"])
            self.assertEqual(relatorio["c1"]["hoje"]["mensagens"], 20)
        finally:
            uso.TETO_POR_PASSADA = anterior

    def test_ponte_entrega_o_parcial_junto(self):
        garantir_app()
        with unittest.mock.patch.object(mod_bridge.Bridge, "_revisar_vigia", lambda self: None):
            ponte = mod_bridge.Bridge()
        try:
            with unittest.mock.patch.object(uso, "pastas_de", return_value=self.pastas):
                resposta = json.loads(ponte.getUsage())
        finally:
            ponte.shutdown()
        self.assertEqual(set(resposta), {"porConta", "parcial"})


class SudoNoModoCerto(ComPastaPropria):
    def elevacao(self, modo, gsudo: str | None = None) -> tuple[dict, list]:
        provas = []

        def falso_run(argumentos, **opcoes):
            provas.append(argumentos)
            return subprocess.CompletedProcess(argumentos, 0, stdout="uso: sudo", stderr="")

        achados = {"sudo": r"C:\Windows\System32\sudo.exe", "gsudo": gsudo}
        with (
            unittest.mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}),
            unittest.mock.patch.object(shells.shutil, "which", side_effect=lambda nome: achados.get(nome)),
            unittest.mock.patch.object(shells.subprocess, "run", falso_run),
            unittest.mock.patch.object(shells, "_modo_do_sudo", return_value=modo),
        ):
            return shells.elevacao(), provas

    def test_modo_nova_janela_nao_oferece_o_adm(self):
        """Nesse modo o sudo abria um console separado e a aba fechava com codigo 0."""
        resposta, _ = self.elevacao(shells.SUDO_NOVA_JANELA)
        self.assertFalse(resposta["disponivel"])
        self.assertEqual(resposta["motivo"], "nova janela")

    def test_modo_na_mesma_janela_vale_sem_prova(self):
        resposta, provas = self.elevacao(shells.SUDO_NA_MESMA_JANELA)
        self.assertTrue(resposta["disponivel"])
        self.assertEqual(provas, [])

    def test_gsudo_ao_lado_serve_quando_o_do_windows_nao_serve(self):
        resposta, _ = self.elevacao(shells.SUDO_NOVA_JANELA, gsudo=r"C:\gsudo\gsudo.exe")
        self.assertTrue(resposta["disponivel"])
        self.assertEqual(resposta["comando"], r"C:\gsudo\gsudo.exe")


class EditorDosLinks(ComPonte):
    def achar(self, mapa: dict):
        return unittest.mock.patch("shutil.which", side_effect=lambda nome: mapa.get(nome))

    def test_vscode_pula_pra_linha_com_g(self):
        with self.achar({"code": r"C:\VS Code\bin\code.cmd"}):
            comando = mod_bridge.linha_do_editor("code", r"C:\p\a.py", "42")
        self.assertEqual(comando, r'"C:\VS Code\bin\code.cmd" -g "C:\p\a.py:42"')

    def test_notepad_mais_mais_nao_recebe_o_g(self):
        """O -g e do VS Code: o Notepad++ tentava abrir um arquivo chamado -g."""
        with self.achar({"notepad++": r"C:\NPP\notepad++.exe"}):
            comando = mod_bridge.linha_do_editor("notepad++", r"C:\p\a.py", "42")
        self.assertEqual(comando, r'"C:\NPP\notepad++.exe" -n42 "C:\p\a.py"')

    def test_caminho_com_espaco_e_argumento_proprio_seguem_juntos(self):
        subl = r"C:\Program Files\Sublime Text\subl.exe"
        with self.achar({subl: subl}):
            comando = mod_bridge.linha_do_editor(f'"{subl}" --new-window', r"C:\p\a.py", "7")
        self.assertEqual(comando, f'"{subl}" --new-window "C:\\p\\a.py:7"')

    def abrir_sem_editor(self, nome: str) -> tuple[str, list, unittest.mock.MagicMock]:
        arquivo = self.raiz / nome
        arquivo.write_text("x", encoding="utf-8")
        abertos = []
        with (
            unittest.mock.patch.object(mod_bridge.subprocess, "Popen") as popen,
            unittest.mock.patch.object(mod_bridge, "_abrir_no_sistema", side_effect=abertos.append),
            self.achar({}),
        ):
            aviso = self.ponte.abrirExterno(f"{arquivo}:3", "")
        return aviso, abertos, popen

    def test_editor_que_nao_existe_abre_no_programa_padrao_e_avisa(self):
        """Antes o cmd escondido falhava calado e nada abria."""
        aviso, abertos, popen = self.abrir_sem_editor("notas.md")
        self.assertTrue(aviso.startswith("Não achei o editor code"), aviso)
        self.assertEqual(abertos, [str(self.raiz / "notas.md")])
        popen.assert_not_called()

    def test_script_nao_vai_pro_programa_padrao(self):
        """No programa padrao o Windows roda o .py e o .bat em vez de mostrar."""
        for nome in ("a.py", "b.bat", "c.js"):
            aviso, abertos, popen = self.abrir_sem_editor(nome)
            self.assertEqual(abertos, [], nome)
            self.assertIn("Não abri " + nome, aviso)
            popen.assert_not_called()


class RetomarEEntrar(ComPonte):
    def abrir(self, perfil_id: str, pedido: dict) -> str:
        with unittest.mock.patch.object(self.ponte, "_abrir", return_value="s1"):
            return self.ponte.openSessionWith("pwsh", perfil_id, 80, 24, json.dumps(pedido))

    def test_conversa_da_conta_de_sempre_retoma_sem_conta_cadastrada(self):
        """A aba sem conta abria na pasta e nao retomava nada."""
        self.abrir("", {"conversa": "3f2a9c1e-7b4d", "pasta": ""})
        self.assertEqual(self.digitado, [("s1", "claude --resume 3f2a9c1e-7b4d", 700)])

    def test_conversa_com_cara_de_comando_nao_e_digitada(self):
        self.abrir("", {"conversa": "x; del *"})
        self.assertEqual(self.digitado, [])

    def test_conversa_com_quebra_de_linha_no_fim_nao_e_digitada(self):
        """O $ da regra aceitava a quebra no fim, e ela ia pro shell como mais um Enter."""
        self.abrir("", {"conversa": "3f2a9c1e\n"})
        self.assertEqual(self.digitado, [])

    def test_entrar_sobe_o_cli_mesmo_com_a_abertura_automatica_desligada(self):
        """O /login caia no PowerShell como comando inexistente."""
        config.salvar({"abrirCliDaConta": False})
        conta = perfis.criar("Nova")
        self.abrir(conta.id, {"entrar": True})
        self.assertEqual([t for _, t, _ in self.digitado], ["claude", "/login"])

    def test_entrar_no_codex_usa_o_login_dele(self):
        conta = perfis.criar("Codex", "codex")
        self.abrir(conta.id, {"entrar": True})
        self.assertEqual([t for _, t, _ in self.digitado], ["codex login"])

    def test_passos_de_cada_ferramenta(self):
        por_id = ferramentas.por_id
        self.assertEqual(ferramentas.passos_para_entrar(por_id("claude")), [(700, "claude"), (4000, "/login")])
        self.assertEqual(ferramentas.passos_para_entrar(por_id("gemini")), [(700, "gemini")])
        sem = ferramentas.Ferramenta("x", "X", "", "", "")
        self.assertEqual(ferramentas.passos_para_entrar(sem), [])


class ConfiarSoNaPastaDaAba(ComPonte):
    def abrir(self, pasta: str | None) -> list:
        conta = perfis.criar("Uma")
        with (
            unittest.mock.patch.object(mod_bridge, "PtySession"),
            unittest.mock.patch.object(mod_bridge.mod_perfis, "instalar_coletor"),
            unittest.mock.patch.object(mod_bridge.mod_perfis, "herdar_confianca"),
            unittest.mock.patch.object(mod_bridge.mod_perfis, "confiar") as confiar,
        ):
            self.ponte._abrir("pwsh", conta.id, 80, 24, False, pasta)
        return [chamada.args[1] for chamada in confiar.call_args_list]

    def test_aba_sem_pasta_nao_confia_na_pasta_pessoal(self):
        """Confiar na pessoal a cada aba liberava tudo que fosse clonado dentro dela."""
        self.assertEqual(self.abrir(None), [])

    def test_aba_aberta_numa_pasta_confia_so_nela(self):
        pasta = str(self.raiz / "repo")
        self.assertEqual(self.abrir(pasta), [pasta])

    def test_aba_aberta_na_propria_pasta_pessoal_tambem_nao(self):
        self.assertEqual(self.abrir(os.path.expanduser("~")), [])


class IdentidadeAoFundo(ComPonte):
    def test_menu_nao_espera_o_cli_dizer_de_quem_e_o_login(self):
        """O claude auth status rodava na thread da tela e congelava a janela inteira."""
        conta = perfis.criar("Uma")
        (conta.config_dir / ".credentials.json").write_text("{}", encoding="utf-8")
        onde = []

        def falso_run(argumentos, **opcoes):
            onde.append(threading.current_thread() is threading.main_thread())
            return subprocess.CompletedProcess(argumentos, 0, stdout=json.dumps({"email": "a@b.c"}), stderr="")

        mudou = []
        self.ponte.perfisMudaram.connect(lambda: mudou.append(1))
        with unittest.mock.patch.object(perfis.subprocess, "run", falso_run):
            primeira = json.loads(self.ponte.listProfiles())
            limite = time.monotonic() + 5
            while not mudou and time.monotonic() < limite:
                # so o que foi postado pra esta ponte: rodar o laco inteiro dispararia relogios
                # de pontes de outros testes, que olham a pasta de login de verdade
                QCoreApplication.sendPostedEvents(self.ponte, 0)
                time.sleep(0.01)

        self.assertEqual(onde, [False], "a pergunta ao CLI tem que ser fora da thread da tela")
        self.assertEqual(primeira[0]["email"], "")
        self.assertEqual(mudou, [1])
        self.assertEqual(json.loads(self.ponte.listProfiles())[0]["email"], "a@b.c")


class AbaOcupadaAoFechar(ComPonte):
    def test_so_programa_de_verdade_conta_como_filho(self):
        processos = [(10, 1, "pwsh.exe"), (11, 10, "conhost.exe"), (12, 10, "claude.exe"), (13, 99, "node.exe")]
        self.assertEqual(pty_session._filhos_de(10, processos), ["claude.exe"])
        self.assertEqual(pty_session._filhos_de(12, processos), [])

    def test_na_duvida_a_aba_conta_como_ocupada(self):
        sessao = unittest.mock.MagicMock(alive=True, pid=10)
        self.ponte._sessions["a1"] = sessao
        with unittest.mock.patch.object(mod_bridge.mod_pty, "tem_filhos", return_value=None):
            self.assertTrue(self.ponte.temFilhos("a1"))
        with unittest.mock.patch.object(mod_bridge.mod_pty, "tem_filhos", return_value=False):
            self.assertFalse(self.ponte.temFilhos("a1"))
        self.ponte._sessions.clear()
        self.assertFalse(self.ponte.temFilhos("a1"))


class SobreENotificacao(ComPonte):
    def test_sobre_mostra_a_versao_e_a_pasta_de_verdade(self):
        """O HTML dizia 0.2.0 fixo, e a pasta era deduzida da primeira conta."""
        self.assertEqual(
            json.loads(self.ponte.sobre()), {"versao": loom.__version__, "raiz": str(caminhos.RAIZ)}
        )

    def test_notificacao_leva_a_aba_que_avisou(self):
        recebido = []
        self.ponte.notificacaoPedida.connect(lambda *args: recebido.append(args))
        self.ponte.notificar("Build", "Terminou", "a1")
        self.assertEqual(recebido, [("Build", "Terminou", "a1")])


class JanelaQueFecha:
    """So o que o closeEvent usa da MainWindow, com uma aba ainda rodando."""

    closeEvent = mod_app.MainWindow.closeEvent

    def _pergunta_ao_fechar(self) -> str:
        return "1 aba com programa rodando."


class VersaoNoTopo(ComPonte):
    """A versao so aparecia escondida em Configuracoes, Sobre. A pagina roda no QJSEngine."""

    @classmethod
    def setUpClass(cls) -> None:
        from test_pagina import NAVEGADOR_FALSO, texto_da_pagina
        from PySide6.QtQml import QJSEngine

        garantir_app()
        cls.motor = QJSEngine()
        cls.motor.evaluate(NAVEGADOR_FALSO)
        carga = cls.motor.evaluate(texto_da_pagina(), "pagina.js")
        assert not carga.isError(), carga.toString()

    def mostrado(self) -> dict:
        """O texto de cada elemento depois que a resposta da ponte chega na pagina."""
        resultado = self.motor.evaluate(
            "(() => { const vistos = {}; const antes = document;"
            " document = {getElementById: (id) => vistos[id] || (vistos[id] = {textContent: ''})};"
            f" try {{ mostrarSobre({json.dumps(self.ponte.sobre())}); }} finally {{ document = antes; }}"
            " const texto = {}; for (const id in vistos) texto[id] = vistos[id].textContent;"
            " return JSON.stringify(texto); })()"
        )
        self.assertFalse(resultado.isError(), resultado.toString())
        return json.loads(resultado.toString())

    def test_topo_mostra_a_versao_do_pacote(self):
        mostrado = self.mostrado()
        self.assertEqual(mostrado["versao-topo"], "v" + loom.__version__)
        self.assertEqual(mostrado["sobre-versao"], loom.__version__)

    def test_rotulo_fica_ao_lado_do_nome_sem_numero_escrito(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        cabecalho = re.search(r'<header class="cromo">(.*?)</header>', html, re.S).group(1)
        self.assertRegex(
            cabecalho, r'<span class="marca">Loom</span>\s*<span class="versao" id="versao-topo"[^>]*></span>'
        )
        for nome in ("index.html", "app.js", "style.css"):
            self.assertNotIn(loom.__version__, (UI / nome).read_text(encoding="utf-8"), nome)

    def test_rotulo_some_inteiro_antes_de_empurrar_a_engrenagem(self):
        """Com o fps ligado, o cromo com a versao precisa de 303px, medido na tela renderizada.

        Mais estreito que isso a engrenagem saia pela direita e o fps quebrava em duas linhas.
        """
        css = (UI / "style.css").read_text(encoding="utf-8")
        regra = re.search(r"@media \(max-width: (\d+)px\) \{ \.versao \{ display: none; \} \}", css)
        self.assertIsNotNone(regra, "sem regra que esconde a versao na janela estreita")
        self.assertGreaterEqual(int(regra.group(1)), 303)

    def test_titulo_da_janela_leva_a_versao(self):
        self.assertEqual(mod_app.TITULO, "Loom " + loom.__version__)

    def test_nenhum_titulo_ganha_o_nome_da_aplicacao_no_fim(self):
        """No Windows o Qt pendura " - <nome de exibicao>" em todo titulo que nao termina nele.

        Com o nome de exibicao igual ao titulo, a caixa de fechar saia "Fechar o Loom - Loom" e a versao.
        """
        with unittest.mock.patch.object(mod_app, "QApplication") as aplicacao, unittest.mock.patch.object(
            mod_app.mod_instancia, "chamar_quem_ja_esta_aberto", return_value=True
        ):
            self.assertEqual(mod_app.main(), 0)
        with unittest.mock.patch.object(mod_app, "QMessageBox") as caixa:
            JanelaQueFecha().closeEvent(unittest.mock.MagicMock())
        titulos = (mod_app.TITULO, caixa.call_args.args[1])
        for chamada in aplicacao.return_value.setApplicationDisplayName.call_args_list:
            for titulo in titulos:
                self.assertTrue(titulo.endswith(chamada.args[0]), f"{titulo!r} ganharia {chamada.args[0]!r} no fim")


class GraficoDeTrintaDias(ComPastaPropria):
    """A tela de Uso roda no QJSEngine, sem pagina: stats.js nao toca no documento ao carregar."""

    def setUp(self) -> None:
        super().setUp()
        garantir_app()
        from PySide6.QtQml import QJSEngine

        self.motor = QJSEngine()
        resultado = self.motor.evaluate((UI / "stats.js").read_text(encoding="utf-8"), "stats.js")
        self.assertFalse(resultado.isError(), resultado.toString())

    def rodar(self, expressao: str):
        resultado = self.motor.evaluate(expressao)
        self.assertFalse(resultado.isError(), resultado.toString())
        return json.loads(resultado.toString())

    def test_dia_sem_uso_ganha_a_propria_barra(self):
        """Dez dias espalhados viravam dez barras coladas, lidas como dez dias seguidos."""
        serie = self.rodar(
            'JSON.stringify(comDiasVazios([{dia: "2026-09-20", saida: 5, porModelo: {}}], new Date(2026, 8, 22)))'
        )
        self.assertEqual(len(serie), 30)
        self.assertEqual((serie[0]["dia"], serie[-1]["dia"]), ("2026-08-24", "2026-09-22"))
        self.assertEqual([p["saida"] for p in serie if p["saida"]], [5])
        self.assertEqual(serie[-3]["dia"], "2026-09-20")

    def test_grafico_desenha_os_trinta_dias(self):
        barras = self.rodar(
            'JSON.stringify(grafico(comDiasVazios([{dia: "2026-09-01", saida: 9, porModelo: {}}], '
            "new Date(2026, 8, 22))).split('class=\"barra\"').length - 1)"
        )
        self.assertEqual(barras, 30)


class TestForaDasAbasOcultoPorPadrao(ComPastaPropria):
    """A lista de conversas fora das abas ocupava a lateral de quem nunca pediu por ela."""

    def test_instalacao_nova_comeca_com_a_lista_escondida(self):
        self.assertIs(config.carregar()["mostrarSessoesDeFora"], False)

    def test_tela_so_mostra_quando_a_escolha_foi_mostrar(self):
        codigo = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn("const mostrar = cfg.mostrarSessoesDeFora === true;", codigo)

    def test_quem_escolheu_mostrar_continua_vendo(self):
        config.salvar({"mostrarSessoesDeFora": True})
        self.assertIs(config.carregar()["mostrarSessoesDeFora"], True)


if __name__ == "__main__":
    unittest.main()
