"""Terminais que sobrevivem a fechar e abrir o app, e o comando que o shell ja nasce rodando.

Nada aqui abre shell nem janela: o PTY entra falsificado, a pagina roda no QJSEngine com o
navegador falso do test_pagina, e todo arquivo vai pra pasta temporaria da base.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from base import ComPastaPropria  # noqa: E402

import test_pagina as pagina  # noqa: E402
import test_usabilidade as usabilidade  # noqa: E402

from loom import bridge as mod_bridge  # noqa: E402
from loom import caminhos, config, ferramentas, historico_salvo, perfis, pty_session, sessao_salva, shells  # noqa: E402


class SessaoFalsa:
    def __init__(self, *args, **kwargs) -> None:
        self.args, self.kwargs = args, kwargs
        self.alive = True
        self.tamanho = (kwargs.get("cols", 80), kwargs.get("rows", 24))
        self.output = unittest.mock.MagicMock()
        self.exited = unittest.mock.MagicMock()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.alive = False

    def deleteLater(self) -> None:
        pass


# ---------------------------------------------------------------- PATH do exe congelado


class PathDoExeCongelado(unittest.TestCase):
    PASTA = r"C:\Users\eu\Desktop\Loom"

    def test_tira_a_pasta_do_exe_e_o_que_mora_dentro_dela(self):
        path = os.pathsep.join([
            r"C:\Users\eu\Desktop\Loom\_internal",
            r"c:\users\EU\desktop\loom\_internal\PySide6",
            r"C:\Users\eu\Desktop\Loom",
            r"C:\Windows\System32",
            r"C:\Users\eu\Desktop\LoomVelho\bin",
            r"C:\Program Files\Git\cmd",
        ])
        limpo = pty_session.path_sem_a_pasta(path, Path(self.PASTA)).split(os.pathsep)
        self.assertEqual(limpo, [r"C:\Windows\System32", r"C:\Users\eu\Desktop\LoomVelho\bin", r"C:\Program Files\Git\cmd"])

    def test_ambiente_do_shell_so_limpa_quando_congelado(self):
        path = os.pathsep.join([self.PASTA + r"\_internal", r"C:\Windows"])
        with unittest.mock.patch.dict(os.environ, {"PATH": path}):
            self.assertEqual(pty_session.ambiente_limpo()["PATH"], path)
            with (
                unittest.mock.patch.object(sys, "frozen", True, create=True),
                unittest.mock.patch.object(sys, "executable", self.PASTA + r"\Loom.exe"),
            ):
                self.assertEqual(pty_session.ambiente_limpo()["PATH"], r"C:\Windows")


# ---------------------------------------------------------------- comando na subida


class ComandoNaSubida(unittest.TestCase):
    def shell(self, sid: str, caminho: str = r"C:\x\s.exe", args: tuple = ()) -> shells.Shell:
        return shells.Shell(sid, sid, caminho, args)

    def test_cada_shell_roda_e_fica_aberto(self):
        self.assertEqual(
            shells.com_comando(self.shell("pwsh", args=("-NoLogo",)), "claude"),
            [r"C:\x\s.exe", "-NoLogo", "-NoExit", "-Command", "claude"],
        )
        self.assertEqual(
            shells.com_comando(self.shell("powershell", args=("-NoLogo",)), "codex login"),
            [r"C:\x\s.exe", "-NoLogo", "-NoExit", "-Command", "codex login"],
        )
        self.assertEqual(shells.com_comando(self.shell("cmd"), "claude --resume a1"), [r"C:\x\s.exe", "/K", "claude --resume a1"])
        self.assertEqual(
            shells.com_comando(self.shell("bash", args=("--login", "-i")), "claude"),
            [r"C:\x\s.exe", "--login", "-i", "-c", "claude; exec bash --login -i"],
        )

    def test_wsl_desconhecido_e_sem_comando_voltam_a_digitar(self):
        self.assertIsNone(shells.com_comando(self.shell("wsl"), "claude"))
        self.assertIsNone(shells.com_comando(self.shell("nushell"), "claude"))
        self.assertIsNone(shells.com_comando(self.shell("pwsh"), "  "))

    def test_retomar_de_cada_ferramenta(self):
        claude, codex = ferramentas.EMBUTIDAS[0], ferramentas.EMBUTIDAS[1]
        self.assertEqual(ferramentas.comando_de_retomar(claude, "3f2a"), "claude --resume 3f2a")
        self.assertEqual(ferramentas.comando_de_retomar(codex, "3f2a"), "codex resume 3f2a")
        sem = ferramentas.Ferramenta("x", "X", "", "")
        self.assertEqual(ferramentas.comando_de_retomar(sem, "3f2a"), "")


class AbrirComComando(usabilidade.ComPonte):
    def setUp(self) -> None:
        super().setUp()
        self.ponte._shells = [
            shells.Shell("pwsh", "PowerShell 7", r"C:\pwsh\pwsh.exe", ("-NoLogo",)),
            shells.Shell("wsl", "WSL", r"C:\Windows\System32\wsl.exe"),
        ]
        self.criadas: list[SessaoFalsa] = []

        def criar(*args, **kwargs):
            falsa = SessaoFalsa(*args, **kwargs)
            self.criadas.append(falsa)
            return falsa

        self._falsos = [
            unittest.mock.patch.object(mod_bridge, "PtySession", side_effect=criar),
            unittest.mock.patch.object(mod_bridge.mod_perfis, "herdar_confianca", return_value=0),
            unittest.mock.patch.object(mod_bridge.mod_perfis, "instalar_coletor", return_value=None),
        ]
        for falso in self._falsos:
            falso.start()

    def tearDown(self) -> None:
        for falso in self._falsos:
            falso.stop()
        super().tearDown()

    def argv(self) -> list[str]:
        return self.criadas[-1].kwargs["argv"]

    def test_cli_da_conta_vai_no_argv_e_nao_e_digitado(self):
        conta = perfis.criar("Uma")
        self.ponte._abrir("pwsh", conta.id, 80, 24, False)
        self.assertEqual(self.argv(), [r"C:\pwsh\pwsh.exe", "-NoLogo", "-NoExit", "-Command", "claude"])
        self.assertEqual(self.digitado, [])

    def test_wsl_continua_digitando(self):
        conta = perfis.criar("Uma")
        sid = self.ponte._abrir("wsl", conta.id, 80, 24, False)
        self.assertEqual(self.argv(), [r"C:\Windows\System32\wsl.exe"])
        self.assertEqual(self.digitado, [(sid, "claude", 700)])

    def test_aba_elevada_continua_digitando(self):
        self.ponte._elevacao = {"disponivel": True, "motivo": "", "comando": r"C:\gsudo\gsudo.exe"}
        conta = perfis.criar("Uma")
        sid = self.ponte._abrir("pwsh", conta.id, 80, 24, True)
        self.assertEqual(self.argv(), [r"C:\gsudo\gsudo.exe", r"C:\pwsh\pwsh.exe", "-NoLogo"])
        self.assertEqual(self.digitado, [(sid, "claude", 700)])

    def test_retomar_sobe_o_shell_ja_dentro_da_conversa(self):
        self.ponte.openSessionWith("pwsh", "", 80, 24, json.dumps({"conversa": "3f2a-9c1e"}))
        self.assertEqual(self.argv()[-1], "claude --resume 3f2a-9c1e")
        conta = perfis.criar("Cdx", "codex")
        self.ponte.openSessionWith("pwsh", conta.id, 80, 24, json.dumps({"conversa": "3f2a-9c1e"}))
        self.assertEqual(self.argv()[-1], "codex resume 3f2a-9c1e")
        self.assertEqual(self.digitado, [])

    def test_entrar_sobe_o_cli_no_argv_e_digita_o_login_depois(self):
        conta = perfis.criar("Nova")
        sid = self.ponte.openSessionWith("pwsh", conta.id, 80, 24, json.dumps({"entrar": True}))
        self.assertEqual(self.argv()[-1], "claude")
        self.assertEqual(self.digitado, [(sid, "/login", 4000)])

    def test_retomar_no_shell_vivo_usa_o_formato_da_ferramenta(self):
        sessao = unittest.mock.MagicMock(alive=True, pid=10)
        self.ponte._sessions["a1"] = sessao
        self.ponte._origem["a1"] = {"elevado": False}
        conta = perfis.criar("Cdx", "codex")
        with unittest.mock.patch.object(mod_bridge.mod_pty, "tem_filhos", return_value=False):
            self.assertTrue(self.ponte.retomarNaAba("a1", conta.id, "3f2a"))
        self.assertEqual(self.digitado, [("a1", "codex resume 3f2a", 0)])


# ---------------------------------------------------------------- historico em disco


class HistoricoEmDisco(ComPastaPropria):
    def test_grava_e_le_de_volta_na_pasta_de_dados(self):
        self.assertTrue(historico_salvo.gravar("a1b2", "ola\r\nmundo", 100, 30, 1234.5))
        self.assertEqual((caminhos.RAIZ / "historico" / "a1b2.json").is_file(), True)
        self.assertEqual(historico_salvo.ler("a1b2"), {"texto": "ola\r\nmundo", "cols": 100, "rows": 30, "em": 1234.5})

    def test_id_que_nao_e_id_nao_vira_caminho(self):
        for ruim in ("../fora", "a/b", "a\\b", "", "x" * 65, "a.b"):
            self.assertFalse(historico_salvo.gravar(ruim, "x", 80, 24), ruim)
            self.assertIsNone(historico_salvo.ler(ruim), ruim)
        self.assertFalse((self.raiz / "fora.json").exists())

    def test_texto_grande_guarda_o_fim_a_partir_de_uma_linha_inteira(self):
        with unittest.mock.patch.object(historico_salvo, "TETO_DE_TEXTO", 20):
            historico_salvo.gravar("a1", "linha-um\nlinha-dois\nlinha-tres\n", 80, 24)
            self.assertEqual(historico_salvo.ler("a1")["texto"], "linha-tres\n")

    def test_falha_ao_gravar_deixa_o_anterior_inteiro(self):
        historico_salvo.gravar("a1", "antes", 80, 24)
        with unittest.mock.patch.object(Path, "replace", side_effect=OSError("disco")):
            with self.assertLogs("loom.historico_salvo", "WARNING"):
                self.assertFalse(historico_salvo.gravar("a1", "depois", 80, 24))
        self.assertEqual(historico_salvo.ler("a1")["texto"], "antes")
        self.assertEqual(list(historico_salvo.pasta().glob("*.tmp")), [])

    def test_arquivo_quebrado_ou_medida_absurda(self):
        historico_salvo.pasta().mkdir(parents=True)
        (historico_salvo.pasta() / "q1.json").write_text("{quebrado", encoding="utf-8")
        self.assertIsNone(historico_salvo.ler("q1"))
        (historico_salvo.pasta() / "q2.json").write_text(
            json.dumps({"texto": "x", "cols": 10**9, "rows": "muito", "em": True}), encoding="utf-8"
        )
        self.assertEqual(historico_salvo.ler("q2"), {"texto": "x", "cols": 1000, "rows": 30, "em": 0.0})

    def test_renomear_e_apagar(self):
        historico_salvo.gravar("velho", "x", 80, 24)
        self.assertTrue(historico_salvo.renomear("velho", "novo"))
        self.assertIsNone(historico_salvo.ler("velho"))
        self.assertEqual(historico_salvo.ler("novo")["texto"], "x")
        self.assertFalse(historico_salvo.renomear("nunca", "outro"))
        historico_salvo.apagar("novo")
        self.assertIsNone(historico_salvo.ler("novo"))

    def test_limpa_so_o_que_ninguem_cita_e_ja_passou_da_idade(self):
        for sid in ("citado", "solto", "novinho"):
            historico_salvo.gravar(sid, "x", 80, 24)
        velho = time.time() - historico_salvo.IDADE_PRA_LIMPAR - 10
        for sid in ("citado", "solto"):
            os.utime(historico_salvo.pasta() / f"{sid}.json", (velho, velho))
        self.assertEqual(historico_salvo.limpar({"citado"}), 1)
        sobraram = sorted(a.stem for a in historico_salvo.pasta().glob("*.json"))
        self.assertEqual(sobraram, ["citado", "novinho"])
        self.assertEqual(historico_salvo.limpar(set(), idade_minima=0), 2)

    def test_abas_json_guarda_o_id_do_shell(self):
        sessao_salva.gravar([{"shell": "pwsh", "sessao": "a1b2"}])
        self.assertEqual(sessao_salva.ler()[0]["sessao"], "a1b2")

    def test_chaves_novas_ligadas_por_padrao(self):
        ajustes = config.carregar()
        self.assertIs(ajustes["guardarHistorico"], True)
        self.assertIs(ajustes["retomarConversas"], True)
        self.assertIs(config.salvar({"guardarHistorico": False})["guardarHistorico"], False)


class HistoricoNaPonte(usabilidade.ComPonte):
    def viva(self, sid: str, texto: str = "", cols: int = 100, rows: int = 30) -> SessaoFalsa:
        falsa = SessaoFalsa(cols=cols, rows=rows)
        self.ponte._sessions[sid] = falsa
        if texto:
            self.ponte._guardar_historico(sid, texto)
        return falsa

    def test_fechar_o_app_grava_cada_aba_viva_antes_de_parar_o_shell(self):
        falsa = self.viva("a1", "PS C:\\> dir\r\n")
        self.viva("a2")
        parado_antes = []
        original = historico_salvo.gravar

        def gravar(*args, **kwargs):
            parado_antes.append(not falsa.alive)
            return original(*args, **kwargs)

        with unittest.mock.patch.object(historico_salvo, "gravar", side_effect=gravar):
            self.ponte.shutdown()
        self.assertEqual(parado_antes, [False])
        self.assertEqual(historico_salvo.ler("a1")["texto"], "PS C:\\> dir\r\n")
        self.assertEqual((historico_salvo.ler("a1")["cols"], historico_salvo.ler("a1")["rows"]), (100, 30))
        self.assertIsNone(historico_salvo.ler("a2"), "aba sem nada na tela nao vira arquivo")

    def test_o_que_estava_no_buffer_entra_no_que_vai_pro_disco(self):
        self.viva("a1", "antes ")
        self.ponte._buffers["a1"] = ["no buffer"]
        self.ponte.shutdown()
        self.assertEqual(historico_salvo.ler("a1")["texto"], "antes no buffer")

    def test_desligado_nao_grava_e_apaga_o_que_ja_estava(self):
        for chave in ("guardarHistorico", "restaurarAbas"):
            with self.subTest(chave):
                historico_salvo.gravar("velho", "x", 80, 24)
                config.salvar({"guardarHistorico": True, "restaurarAbas": True, chave: False})
                ponte = mod_bridge.Bridge()
                ponte._sessions["a1"] = SessaoFalsa()
                ponte._guardar_historico("a1", "segredo")
                ponte.shutdown()
                self.assertIsNone(historico_salvo.ler("a1"))
                self.assertIsNone(historico_salvo.ler("velho"))

    def test_relogio_grava_so_as_que_mudaram(self):
        self.viva("a1", "um")
        self.viva("a2", "dois")
        self.ponte._gravar_historicos_mudados()
        self.assertEqual(historico_salvo.ler("a2")["texto"], "dois")
        historico_salvo.apagar("a2")
        self.ponte._guardar_historico("a1", " mais")
        self.ponte._gravar_historicos_mudados()
        self.assertEqual(historico_salvo.ler("a1")["texto"], "um mais")
        self.assertIsNone(historico_salvo.ler("a2"), "a2 parada nao volta pro disco")

    def test_relogio_nasce_ligado_e_chama_slot_declarado(self):
        self.assertTrue(self.ponte._relogio_do_historico.isActive())
        self.assertEqual(self.ponte._relogio_do_historico.interval(), mod_bridge.GRAVAR_HISTORICO_MS)
        self.assertGreaterEqual(self.ponte.metaObject().indexOfSlot("_gravar_historicos_mudados()"), 0)

    def test_aba_fechada_leva_o_arquivo_junto(self):
        self.viva("a1", "x")
        historico_salvo.gravar("a1", "x", 80, 24)
        self.ponte.closeSession("a1")
        self.assertIsNone(historico_salvo.ler("a1"))

    def test_abas_guardadas_vem_com_o_historico(self):
        historico_salvo.gravar("a1", "tela de antes", 132, 40, 1700000000.0)
        sessao_salva.gravar([{"shell": "pwsh", "sessao": "a1"}, {"shell": "cmd", "sessao": "sem"}])
        com, sem = json.loads(self.ponte.savedTabs())
        self.assertEqual(
            (com["historico"], com["cols"], com["rows"], com["historicoEm"]), ("tela de antes", 132, 40, 1700000000.0)
        )
        self.assertNotIn("historico", sem)
        config.salvar({"guardarHistorico": False})
        self.assertNotIn("historico", json.loads(self.ponte.savedTabs())[0])


class SementeDaAbaReaberta(usabilidade.ComPonte):
    def abrir(self, pedido: dict, sid: str = "novo1") -> str:
        def abrir(*_args, comando=""):
            self.ponte._sessions[sid] = SessaoFalsa()
            return sid

        with unittest.mock.patch.object(self.ponte, "_abrir", side_effect=abrir):
            return self.ponte.openSessionWith("pwsh", "", 80, 24, json.dumps(pedido))

    def test_shell_novo_continua_o_historico_e_o_arquivo_troca_de_nome(self):
        historico_salvo.gravar("velho1", "tela de antes\r\n", 80, 24)
        self.abrir({"anterior": "velho1", "emenda": "\x1b[0m-- Restaurado --\r\n"})
        self.assertEqual(self.ponte._historico_de("novo1"), "tela de antes\r\n\x1b[0m-- Restaurado --\r\n")
        self.assertIsNone(historico_salvo.ler("velho1"))
        self.assertEqual(historico_salvo.ler("novo1")["texto"], "tela de antes\r\n")

    def test_emenda_grande_e_cortada(self):
        historico_salvo.gravar("velho1", "x", 80, 24)
        self.abrir({"anterior": "velho1", "emenda": "y" * 5000})
        self.assertEqual(len(self.ponte._historico_de("novo1")), 1 + mod_bridge.TETO_DA_EMENDA)

    def test_id_antigo_torto_ou_desligado_nao_semeia(self):
        historico_salvo.gravar("velho1", "x", 80, 24)
        self.abrir({"anterior": "../velho1"}, "n1")
        self.assertEqual(self.ponte._historico_de("n1"), "")
        config.salvar({"guardarHistorico": False})
        self.abrir({"anterior": "velho1"}, "n2")
        self.assertEqual(self.ponte._historico_de("n2"), "")
        self.assertEqual(historico_salvo.ler("velho1")["texto"], "x")

    def test_saida_nova_nao_empurra_o_historico_antigo_inteiro(self):
        linhas = "".join(f"linha {i:05d}\n" for i in range(1500))
        historico_salvo.gravar("velho1", linhas, 80, 24)
        with unittest.mock.patch.object(mod_bridge, "HISTORICO_POR_SESSAO", len(linhas) + 10):
            self.abrir({"anterior": "velho1"})
            self.ponte._guardar_historico("novo1", "prompt novo\n")
            texto = self.ponte._historico_de("novo1")
        self.assertTrue(texto.endswith("linha 01499\nprompt novo\n"))
        self.assertGreater(len(texto), len(linhas) // 2)


# ---------------------------------------------------------------- a pagina


def texto_da_pagina() -> str:
    # o stats.js pode nao existir mais; o que interessa aqui mora no app.js
    stats = pagina.UI / "stats.js"
    app = (pagina.UI / "app.js").read_text(encoding="utf-8")
    app = app.replace("async function", "function").replace("await ", "")
    antes = stats.read_text(encoding="utf-8") + "\n" if stats.is_file() else ""
    return antes + app + pagina.DE_DENTRO


class PaginaRestaura(pagina.ComPagina):
    @classmethod
    def setUpClass(cls) -> None:
        pagina.garantir_app()
        from PySide6.QtQml import QJSEngine

        cls.motor = QJSEngine()
        cls.motor.evaluate(pagina.NAVEGADOR_FALSO)
        cls.carga = cls.motor.evaluate(texto_da_pagina(), "pagina.js")

    def setUp(self) -> None:
        self.assertFalse(self.carga.isError(), self.carga.toString())

    def em(self) -> str:
        return "(new Date(2026, 9, 3, 21, 40).getTime() / 1000)"

    def test_linha_de_restaurado_logo_abaixo_do_ultimo_texto(self):
        emenda = self.rodar(f'emendaDoRestauro(["$ ls", "a  b", "   ", ""], {self.em()})')
        self.assertTrue(emenda.startswith("\x1b[3;1H"), repr(emenda))
        self.assertIn("── Restaurado · 03/10 21:40 ──", emenda)
        self.assertTrue(emenda.endswith("\x1b[0m\r\n"))

    def test_texto_ate_o_fim_desce_rolando_e_tela_vazia_comeca_no_topo(self):
        self.assertTrue(self.rodar(f'emendaDoRestauro(["a", "b"], {self.em()})').startswith("\x1b[2;1H\r\n"))
        self.assertTrue(self.rodar(f'emendaDoRestauro(["", " "], {self.em()})').startswith("\x1b[1;1H"))

    def test_pedido_da_aba_salva(self):
        s = '{shell: "pwsh", pasta: "C:/p", conversa: "c1", sessao: "v1", historico: "oi", cols: 90, rows: 20, historicoEm: 5}'
        self.assertEqual(
            self.rodar(f"pedidoDaAbaSalva({s}, true)"),
            {"pasta": "C:/p", "conversa": "c1", "anterior": "v1",
             "restauro": {"texto": "oi", "cols": 90, "rows": 20, "em": 5}},
        )
        desligado = self.rodar(f"pedidoDaAbaSalva({s}, false)")
        self.assertNotIn("conversa", desligado)
        self.assertIs(desligado["semCli"], True)
        self.assertEqual(self.rodar('pedidoDaAbaSalva({pasta: "C:/p"}, true)'), {"pasta": "C:/p", "semCli": False})

    def abrir_restaurando(self, linhas: str) -> dict:
        return self.rodar(
            "(() => { const ops = []; let enviado = null; const antes = [setTimeout, ativar];"
            " setTimeout = (f) => f(); ativar = () => {};"
            " const aba = Object.create(estado.Aba.prototype);"
            f" const linhas = {linhas};"
            " Object.assign(aba, {viva: true, sessionId: null, shell: {id: 'pwsh'}, perfil: null, elevado: false,"
            " fit: {fit() { ops.push('fit ' + aba.restaurando); }}, pintar() {},"
            " term: {cols: 80, rows: 4, focus() {},"
            "  resize(c, r) { ops.push('resize ' + c + 'x' + r); this.cols = c; this.rows = r; },"
            "  write(t, cb) { ops.push(t); if (cb) cb(); },"
            "  buffer: {active: {baseY: 0, getLine(i) { return {translateToString: () => linhas[i] || ''}; }}}}});"
            " estado.bridge = {openSessionWith(...args) { ops.push('abriu ' + args[2] + 'x' + args[3]);"
            "  enviado = JSON.parse(args[4]); args[args.length - 1]('novo1'); }};"
            " try { aba.abrir({pasta: 'C:/p', anterior: 'v1',"
            "  restauro: {texto: 'TELA ANTIGA', cols: 100, rows: 4, em: 0}}); }"
            " finally { setTimeout = antes[0]; ativar = antes[1]; estado.bridge = null; }"
            " return {ops, enviado, inicial: aba.pedidoInicial, sessao: aba.sessionId, restaurando: aba.restaurando}; })()"
        )

    def test_aba_redesenha_o_historico_antes_de_abrir_o_shell(self):
        r = self.abrir_restaurando('["$ ls", "a", "", ""]')
        ops = r["ops"]
        self.assertEqual(ops[0], "resize 100x4")
        self.assertEqual(ops[1], "TELA ANTIGA")
        zerar = ops[2]
        for modo in ("\x1b[?1049l", "\x1b[?1000l", "\x1b[?1002l", "\x1b[?1003l", "\x1b[?1006l",
                     "\x1b[?2004l", "\x1b[?25h", "\x1b[0m"):
            self.assertIn(modo, zerar)
        self.assertTrue(ops[3].startswith("\x1b[3;1H"))
        self.assertIn("Restaurado", ops[3])
        # so depois de tudo escrito: medir e abrir, com o fit valendo de novo
        self.assertEqual(ops[4:6], ["fit false", "abriu 100x4"])
        self.assertFalse(r["restaurando"])
        self.assertEqual(r["sessao"], "novo1")

    def test_texto_do_historico_nao_vai_pro_app_so_o_id_antigo(self):
        r = self.abrir_restaurando('["x", "", "", ""]')
        enviado = r["enviado"]
        self.assertNotIn("restauro", enviado)
        self.assertNotIn("TELA ANTIGA", json.dumps(enviado))
        self.assertEqual(enviado["anterior"], "v1")
        self.assertEqual(enviado["emenda"], r["ops"][2] + r["ops"][3])
        self.assertEqual(r["inicial"], {"pasta": "C:/p"}, "o Enter na aba encerrada nao repete o restauro")


if __name__ == "__main__":
    unittest.main()
