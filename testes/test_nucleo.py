"""O nucleo, dono dos shells, e o canal dele com a tela.

Nada aqui sobe shell de verdade nem janela: o shell e um falso com os sinais da PtySession, e a tela
e o proprio teste, conversando com o nucleo por um canal local de verdade, com nome so deste teste.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

from base import ComPastaPropria

from PySide6.QtCore import QCoreApplication, QObject, Signal

from loom import bridge as mod_bridge
from loom import cliente_nucleo, nucleo, protocolo

RAIZ = Path(__file__).resolve().parent.parent
_numeros = itertools.count()


def garantir_app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def esperar(condicao, prazo: float = 5.0) -> bool:
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        QCoreApplication.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return condicao()


class ShellFalso(QObject):
    """Os sinais e os metodos da PtySession, sem processo nenhum."""

    output = Signal(str, str)
    exited = Signal(str, int)
    criados: list["ShellFalso"] = []
    falhar = False

    def __init__(self, session_id, argv=None, cwd=None, env=None, cols=120, rows=30, parent=None):
        super().__init__(parent)
        self.session_id = session_id
        self.argv, self.cwd, self.env = argv, cwd, env
        self.cols, self.rows = cols, rows
        self.escrito: list[str] = []
        self.parado = False
        self.vivo = False
        ShellFalso.criados.append(self)

    def start(self) -> None:
        if ShellFalso.falhar:
            raise OSError("o shell nao existe")
        self.vivo = True

    def write(self, data: str) -> None:
        self.escrito.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows

    @property
    def tamanho(self) -> tuple[int, int]:
        return self.cols, self.rows

    @property
    def alive(self) -> bool:
        return self.vivo and not self.parado

    @property
    def pid(self) -> int:
        return 4000 + len(self.session_id)

    def stop(self) -> None:
        self.parado = True

    # o que o shell de verdade faria sozinho
    def mostrar(self, texto: str) -> None:
        self.output.emit(self.session_id, texto)

    def sair(self, codigo: int = 0) -> None:
        self.vivo = False
        self.exited.emit(self.session_id, codigo)


class ComShellFalso(unittest.TestCase):
    def setUp(self) -> None:
        garantir_app()
        ShellFalso.criados = []
        ShellFalso.falhar = False


class Protocolo(unittest.TestCase):
    def test_linha_partida_e_varias_por_leitura(self):
        leitor = protocolo.Leitor()
        pacote = protocolo.empacotar({"tipo": "saida", "texto": "a\nb"}) + protocolo.empacotar({"tipo": "saiu"})
        self.assertEqual(leitor.alimentar(pacote[:7]), [])
        self.assertEqual(leitor.alimentar(pacote[7:]), [{"tipo": "saida", "texto": "a\nb"}, {"tipo": "saiu"}])

    def test_quebra_de_linha_da_saida_nao_corta_a_mensagem(self):
        self.assertEqual(protocolo.empacotar({"tipo": "x", "texto": "1\n2\r\n3"}).count(b"\n"), 1)

    def test_lixo_e_mensagem_sem_tipo_sao_ignorados(self):
        leitor = protocolo.Leitor()
        entrada = b'nao e json\n[1,2]\n{"sem": "tipo"}\n' + protocolo.empacotar({"tipo": "ok"})
        self.assertEqual(leitor.alimentar(entrada), [{"tipo": "ok"}])

    def test_linha_sem_fim_passando_do_teto_estoura(self):
        leitor = protocolo.Leitor(teto=10)
        leitor.alimentar(b'{"tipo": "abcdefghijkl')
        self.assertTrue(leitor.estourou)


class HistoricoDoShell(unittest.TestCase):
    def test_guarda_o_fim_e_recomeca_numa_linha_inteira(self):
        historico = nucleo.Historico(teto=10)
        for pedaco in ("abc\n", "defgh", "ij\nkl", "mn\n"):
            historico.guardar(pedaco)
        # saiu "abc\n" e depois "defgh", que nao fechava a linha: o resto comeca na linha seguinte
        self.assertEqual(historico.texto(), "kl" + "mn\n")

    def test_corte_em_fim_de_linha_guarda_tudo_que_sobrou(self):
        historico = nucleo.Historico(teto=6)
        for pedaco in ("12\n", "34\n", "56\n"):
            historico.guardar(pedaco)
        self.assertEqual(historico.texto(), "34\n56\n")


class NucleoComShellFalso(ComShellFalso):
    def novo(self) -> nucleo.Nucleo:
        n = nucleo.Nucleo(ShellFalso)
        self.eventos: list[tuple] = []
        n.saida.connect(lambda sid, t: self.eventos.append(("saida", sid, t)))
        n.saiu.connect(lambda sid, c: self.eventos.append(("saiu", sid, c)))
        n.aberta.connect(lambda sid, pid: self.eventos.append(("aberta", sid, pid)))
        return n

    def pedido(self, sid: str = "a1", **extra) -> dict:
        return {"tipo": "abrir", "id": sid, "argv": ["pwsh.exe", "-NoLogo"], "cwd": "C:\\x", "env": {"LOOM_ABA": sid},
                "cols": 100, "rows": 40, "origem": {"shell": "pwsh", "perfil": "p1"}, **extra}

    def test_abre_escreve_mede_e_guarda_o_que_o_shell_mostrou(self):
        n = self.novo()
        n.abrir(self.pedido())
        shell = ShellFalso.criados[0]
        self.assertEqual((shell.argv, shell.cwd, shell.env, shell.cols, shell.rows),
                         (["pwsh.exe", "-NoLogo"], "C:\\x", {"LOOM_ABA": "a1"}, 100, 40))
        self.assertEqual(self.eventos, [("aberta", "a1", 4002)])
        n.escrever("a1", "dir\r")
        n.medida("a1", 80, 20)
        self.assertEqual((shell.escrito, shell.tamanho), (["dir\r"], (80, 20)))
        shell.mostrar("ola\n")
        self.assertTrue(esperar(lambda: ("saida", "a1", "ola\n") in self.eventos))
        self.assertEqual(n.estado(), [{"id": "a1", "pid": 4002, "cols": 80, "rows": 20,
                                       "origem": {"shell": "pwsh", "perfil": "p1"}, "historico": "ola\n"}])

    def test_shell_que_sai_avisa_e_some_do_estado(self):
        n = self.novo()
        n.abrir(self.pedido())
        shell = ShellFalso.criados[0]
        shell.sair(3)
        self.assertTrue(esperar(lambda: ("saiu", "a1", 3) in self.eventos))
        self.assertTrue(shell.parado, "o pseudo console tambem e solto")
        self.assertEqual(n.estado(), [])

    def test_fechar_pela_tela_nao_avisa_de_volta(self):
        n = self.novo()
        n.abrir(self.pedido())
        n.fechar("a1")
        QCoreApplication.processEvents()
        self.assertTrue(ShellFalso.criados[0].parado)
        self.assertEqual([e for e in self.eventos if e[0] == "saiu"], [])
        self.assertEqual(n.vivas(), 0)

    def test_shell_que_nao_sobe_avisa_o_fim_logo_depois(self):
        n = self.novo()
        ShellFalso.falhar = True
        n.abrir(self.pedido())
        self.assertEqual(self.eventos, [], "o aviso sai fora do pedido, depois dele")
        self.assertTrue(esperar(lambda: ("saiu", "a1", -1) in self.eventos))
        self.assertEqual(n.estado(), [])

    def test_mesmo_id_duas_vezes_nao_abre_dois_shells(self):
        n = self.novo()
        n.abrir(self.pedido())
        n.abrir(self.pedido())
        self.assertEqual(len(ShellFalso.criados), 1)

    def test_encerrar_tudo_derruba_os_shells(self):
        n = self.novo()
        n.abrir(self.pedido("a1"))
        n.abrir(self.pedido("a2"))
        n.encerrar_tudo()
        self.assertTrue(all(s.parado for s in ShellFalso.criados))
        self.assertEqual(n.estado(), [])


class PoliticaDeRelancar(unittest.TestCase):
    def test_perder_canal_pede_recuperacao_sem_encerramento_limpo(self):
        from types import SimpleNamespace
        from loom.app import MainWindow

        app = unittest.mock.Mock()
        with (
            unittest.mock.patch("loom.app.QApplication.instance", return_value=app),
            unittest.mock.patch("loom.app.QProcess.startDetached", return_value=True),
        ):
            MainWindow._nucleo_caiu(SimpleNamespace())
        app.exit.assert_called_once_with(1)
        app.quit.assert_not_called()
        supervisor = nucleo.Supervisor(relogio=lambda: 0.0)
        self.assertEqual(supervisor.depois_que_a_tela_saiu(app.exit.call_args.args[0], False), 0.0)

    def test_tela_que_cai_volta_com_espera_crescente_e_para_no_teto(self):
        agora = [1000.0]
        s = nucleo.Supervisor(relogio=lambda: agora[0])
        decisoes = [s.depois_que_a_tela_saiu(-1073741819, True) for _ in range(5)]
        self.assertEqual(decisoes, [0.0, 2.0, 10.0, 10.0, nucleo.Supervisor.ESPERAR])
        # passada a janela de dez minutos, volta a tentar
        agora[0] += nucleo.JANELA_DE_RELANCES + 1
        self.assertEqual(s.depois_que_a_tela_saiu(1, True), 0.0)

    def test_quem_chama_o_loom_zera_o_teto(self):
        s = nucleo.Supervisor(relogio=lambda: 0.0)
        for _ in range(nucleo.TETO_DE_RELANCES):
            s.depois_que_a_tela_saiu(1, True)
        self.assertEqual(s.depois_que_a_tela_saiu(1, True), nucleo.Supervisor.ESPERAR)
        s.pediu_pra_mostrar()
        self.assertEqual(s.depois_que_a_tela_saiu(1, True), 0.0)

    def test_fechar_de_proposito_ou_sair_limpo_encerra_o_nucleo(self):
        s = nucleo.Supervisor()
        self.assertEqual(s.depois_que_a_tela_saiu(0, False), nucleo.Supervisor.FIM, "logoff ou instalador")
        s.encerrado = True
        self.assertEqual(s.depois_que_a_tela_saiu(-1, True), nucleo.Supervisor.FIM, "caiu depois de pedir pra fechar")


class CanalDeVerdade(ComShellFalso):
    """Nucleo e tela no mesmo processo, falando por um canal local de verdade."""

    def setUp(self) -> None:
        super().setUp()
        self.nome = f"loom-teste-{os.getpid()}-{next(_numeros)}"
        self.token = "segredo-do-teste"
        self.nucleo = nucleo.Nucleo(ShellFalso)
        self.servidor = nucleo.ServidorDoNucleo(self.nucleo, self.nome, self.token)
        self.clientes: list[cliente_nucleo.ClienteDoNucleo] = []

    def tearDown(self) -> None:
        for c in self.clientes:
            c._encerrando = True
            c._conexao.abort()
        self.servidor._servidor.close()
        QCoreApplication.processEvents()

    def cliente(self, token: str | None = None) -> cliente_nucleo.ClienteDoNucleo:
        c = cliente_nucleo.ClienteDoNucleo(self.nome, token or self.token)
        self.clientes.append(c)
        return c

    def test_token_errado_nao_recebe_nada_nem_abre_shell(self):
        intruso = self.cliente("token-errado")
        self.assertFalse(intruso.conectar(espera_ms=600))
        # e quem manda abrir sem o ola certo nao abre nada
        from PySide6.QtNetwork import QLocalSocket

        conexao = QLocalSocket()
        conexao.connectToServer(self.nome)
        self.assertTrue(conexao.waitForConnected(1000))
        conexao.write(protocolo.empacotar({"tipo": "abrir", "id": "x", "argv": ["cmd.exe"]}))
        conexao.flush()
        esperar(lambda: False, prazo=0.3)
        self.assertEqual(ShellFalso.criados, [])
        conexao.abort()

    def test_a_tela_abre_escreve_e_recebe_a_saida_e_o_fim(self):
        c = self.cliente()
        self.assertTrue(c.conectar())
        self.assertEqual((c.estado_inicial, c.abas_iniciais), ([], []))
        sessao = cliente_nucleo.SessaoRemota(c, "a1", argv=["pwsh.exe"], cwd="C:\\x", env={"LOOM_ABA": "a1"},
                                             cols=90, rows=30, origem={"shell": "pwsh"})
        chegou, fim = [], []
        sessao.output.connect(lambda sid, t: chegou.append(t))
        sessao.exited.connect(lambda sid, codigo: fim.append(codigo))
        sessao.start()
        self.assertTrue(esperar(lambda: ShellFalso.criados and sessao.pid == 4002))
        shell = ShellFalso.criados[0]
        self.assertEqual((shell.argv, shell.env, shell.cols), (["pwsh.exe"], {"LOOM_ABA": "a1"}, 90))
        sessao.write("echo oi\r")
        sessao.resize(100, 40)
        self.assertTrue(esperar(lambda: shell.escrito == ["echo oi\r"] and shell.tamanho == (100, 40)))
        shell.mostrar("oi\n")
        self.assertTrue(esperar(lambda: chegou == ["oi\n"]))
        shell.sair(0)
        self.assertTrue(esperar(lambda: fim == [0]))
        self.assertFalse(sessao.alive)

    def test_tela_que_cai_nao_leva_o_shell_e_a_nova_acha_tudo(self):
        c = self.cliente()
        self.assertTrue(c.conectar())
        sessao = cliente_nucleo.SessaoRemota(c, "a1", argv=["pwsh.exe"], origem={"shell": "pwsh", "perfil": "p1"})
        sessao.start()
        self.assertTrue(esperar(lambda: bool(ShellFalso.criados)))
        c.guardar_abas([{"sessao": "a1", "apelido": "api", "ativa": True}])
        ShellFalso.criados[0].mostrar("antes da queda\n")
        self.assertTrue(esperar(lambda: self.nucleo.estado() and self.nucleo.abas))
        # a tela morre sem pedir pra encerrar, como na queda do motor web
        c._conexao.abort()
        esperar(lambda: not self.servidor.tem_tela())
        self.assertFalse(ShellFalso.criados[0].parado, "o shell continua vivo no nucleo")

        nova = self.cliente()
        self.assertTrue(nova.conectar())
        self.assertEqual(nova.estado_inicial, [{"id": "a1", "pid": 4002, "cols": 120, "rows": 30,
                                                "origem": {"shell": "pwsh", "perfil": "p1"},
                                                "historico": "antes da queda\n"}])
        self.assertEqual(nova.abas_iniciais, [{"sessao": "a1", "apelido": "api", "ativa": True}])

    def test_encerrar_pede_ao_nucleo_pra_fechar_tudo(self):
        c = self.cliente()
        self.assertTrue(c.conectar())
        pedidos = []
        self.servidor.encerrarPedido.connect(lambda: pedidos.append(1))
        c.encerrar()
        self.assertTrue(esperar(lambda: pedidos == [1]))

    def test_segunda_instancia_pede_pra_mostrar_sem_token(self):
        pedidos = []
        self.servidor.pedidoDeMostrar.connect(lambda: pedidos.append(1))
        self.assertTrue(nucleo.chamar_o_nucleo(self.nome, prazo=2))
        self.assertTrue(esperar(lambda: pedidos == [1]))

    def test_nucleo_que_morre_avisa_a_tela(self):
        c = self.cliente()
        self.assertTrue(c.conectar())
        caidas = []
        c.caiu.connect(lambda: caidas.append(1))
        self.servidor._tela.abort()
        self.assertTrue(esperar(lambda: caidas == [1]))


class HospedeiroComTelaDeVerdade(ComShellFalso):
    """A tela e um processo python sem janela que anota que subiu e sai com o codigo pedido."""

    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self.pasta = Path(tempfile.mkdtemp(prefix="loom-nucleo-"))
        self.subidas = self.pasta / "subidas.txt"
        # o que nao abre console no Windows; no Linux nao ha console pra abrir
        self.pythonw = str(Path(sys.executable).with_name("pythonw.exe")) if sys.platform == "win32" else sys.executable
        self.hospedeiros: list[nucleo.Hospedeiro] = []

    def tearDown(self) -> None:
        for h in self.hospedeiros:
            h._relance.stop()
            if h._processo is not None:
                h._processo.kill()
                h._processo.waitForFinished(2000)
            h.servidor._servidor.close()
        QCoreApplication.processEvents()

    def hospedeiro(self, codigo_de_saida: int, espera: float = 0.0) -> nucleo.Hospedeiro:
        script = (
            "import sys, time; "
            f"open({str(self.subidas)!r}, 'a').write('x'); time.sleep({espera}); sys.exit({codigo_de_saida})"
        )
        h = nucleo.Hospedeiro([self.pythonw, "-c", script], f"loom-teste-{os.getpid()}-{next(_numeros)}", "t", ShellFalso)
        self.hospedeiros.append(h)
        return h

    def subidas_ate_agora(self) -> int:
        return len(self.subidas.read_text()) if self.subidas.exists() else 0

    def test_tela_que_cai_e_relancada_ate_o_teto_e_quem_chama_sobe_de_novo(self):
        with (
            unittest.mock.patch.object(nucleo, "ESPERAS_DE_RELANCE", (0, 0, 0)),
            unittest.mock.patch.object(nucleo, "TETO_DE_RELANCES", 2),
        ):
            h = self.hospedeiro(3)
            h.subir_tela()
            # a primeira e mais duas tentativas, e a partir dai espera alguem chamar
            self.assertTrue(esperar(lambda: self.subidas_ate_agora() == 3 and not h.tela_de_pe(), prazo=20))
            esperar(lambda: False, prazo=1.0)
            self.assertEqual(self.subidas_ate_agora(), 3)
            h.servidor.pedidoDeMostrar.emit()
            self.assertTrue(esperar(lambda: self.subidas_ate_agora() >= 4, prazo=10))

    def test_fechar_de_proposito_encerra_o_nucleo_quando_a_tela_sai(self):
        # sai com erro de proposito: sem o pedido de encerrar valendo, isso seria uma queda
        h = self.hospedeiro(1, espera=0.5)
        encerrado = []
        h.encerrar = lambda: encerrado.append(1)
        h.nucleo.abrir({"tipo": "abrir", "id": "a1", "argv": ["x"]})
        h.subir_tela()
        self.assertTrue(esperar(lambda: h.tela_de_pe(), prazo=10))
        h.servidor.encerrarPedido.emit()
        self.assertTrue(ShellFalso.criados[0].parado, "os shells vao junto, como sempre foi")
        self.assertTrue(esperar(lambda: encerrado == [1], prazo=10))
        self.assertEqual(self.subidas_ate_agora(), 1, "tela fechada de proposito nao sobe de novo")


class ClienteFalso:
    """O que a ponte usa do cliente do nucleo, anotando cada pedido."""

    def __init__(self, estado=None, abas=None):
        self.estado_inicial = estado or []
        self.abas_iniciais = abas or []
        self.pedidos: list[tuple] = []
        self.registradas: dict = {}

    def registrar(self, sessao) -> None:
        self.registradas[sessao.session_id] = sessao

    def terminar_adocao(self) -> None:
        pass

    def abrir(self, sessao, argv, cwd, env, cols, rows, origem) -> None:
        self.registrar(sessao)
        self.pedidos.append(("abrir", sessao.session_id, list(argv), cwd, dict(env), origem))

    def escrever(self, sid, texto) -> None:
        self.pedidos.append(("escrever", sid, texto))

    def medida(self, sid, cols, rows) -> None:
        self.pedidos.append(("medida", sid, cols, rows))

    def fechar(self, sid) -> None:
        self.pedidos.append(("fechar", sid))

    def guardar_abas(self, abas) -> None:
        self.pedidos.append(("abas", abas))

    def encerrar(self) -> None:
        self.pedidos.append(("encerrar",))


class PonteComNucleo(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        garantir_app()
        self._dados = unittest.mock.patch.object(mod_bridge, "RAIZ_DADOS", self.raiz / "dados")
        self._dados.start()

    def tearDown(self) -> None:
        self._dados.stop()
        super().tearDown()

    def test_tela_nova_adota_os_shells_vivos_com_historico_e_ordem(self):
        estado = [
            {"id": "a2", "pid": 22, "cols": 80, "rows": 24, "origem": {"shell": "cmd", "perfil": "", "pasta": "", "elevado": False},
             "historico": "segunda\n"},
            {"id": "a1", "pid": 11, "cols": 120, "rows": 30, "origem": {"shell": "pwsh", "perfil": "p1", "pasta": "C:\\x", "elevado": False},
             "historico": "primeira\n"},
        ]
        abas = [{"sessao": "a1", "apelido": "api", "ativa": True}, {"sessao": "a2", "apelido": "", "ativa": False}]
        falso = ClienteFalso(estado, abas)
        ponte = mod_bridge.Bridge(nucleo=falso)
        vivas = json.loads(ponte.liveSessions())
        self.assertEqual([v["id"] for v in vivas], ["a1", "a2"], "na ordem das abas, e nao na do nucleo")
        self.assertEqual((vivas[0]["apelido"], vivas[0]["ativa"], vivas[0]["historico"]), ("api", True, "primeira\n"))
        self.assertEqual((vivas[0]["shell"], vivas[0]["perfil"], vivas[0]["pasta"]), ("pwsh", "p1", "C:\\x"))
        self.assertEqual((vivas[1]["cols"], vivas[1]["rows"]), (80, 24))
        self.assertEqual(set(falso.registradas), {"a1", "a2"}, "a saida delas chega a esta tela")
        self.assertEqual(ponte.sessoes_vivas(), 2)
        ponte.shutdown()

    def test_aba_nova_abre_no_nucleo_e_fechar_o_loom_encerra_la(self):
        falso = ClienteFalso()
        ponte = mod_bridge.Bridge(nucleo=falso)
        sid = ponte.openSession("", "", 100, 30)
        abrir = [p for p in falso.pedidos if p[0] == "abrir"]
        self.assertEqual(len(abrir), 1)
        self.assertEqual(abrir[0][1], sid)
        self.assertEqual(abrir[0][4]["LOOM_ABA"], sid)
        # a hora de abertura vai junto pro nucleo: e por ela que a conversa do Codex acha a aba
        self.assertEqual(set(abrir[0][5]), {"shell", "perfil", "pasta", "elevado", "aberta"})
        ponte.write(sid, "ls\r")
        ponte.resize(sid, 90, 20)
        ponte.saveTabs(json.dumps([{"sessao": sid, "apelido": "x"}]))
        self.assertIn(("escrever", sid, "ls\r"), falso.pedidos)
        self.assertIn(("medida", sid, 90, 20), falso.pedidos)
        self.assertIn(("abas", [{"sessao": sid, "apelido": "x"}]), falso.pedidos)
        ponte.shutdown()
        self.assertEqual(falso.pedidos[-1], ("encerrar",))
        self.assertNotIn(("fechar", sid), falso.pedidos, "quem derruba os shells e o nucleo, de uma vez")

    def test_fechar_a_aba_fecha_o_shell_no_nucleo(self):
        falso = ClienteFalso()
        ponte = mod_bridge.Bridge(nucleo=falso)
        sid = ponte.openSession("", "", 100, 30)
        ponte.closeSession(sid)
        self.assertIn(("fechar", sid), falso.pedidos)
        ponte.shutdown()


class EntradaENucleoLeve(unittest.TestCase):
    def test_nucleo_nao_carrega_tela_nem_motor_web(self):
        """Quanto menos coisa no processo dos shells, menos motivo pra ele cair."""
        codigo = (
            "import sys; sys.path.insert(0, sys.argv[1]); import loom.nucleo; "
            "print(sorted(m for m in sys.modules if m.startswith('PySide6')))"
        )
        saida = subprocess.run(
            [sys.executable, "-c", codigo, str(RAIZ)], capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(saida.returncode, 0, saida.stderr)
        carregados = ast.literal_eval(saida.stdout.strip().splitlines()[-1])
        for proibido in ("PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"):
            self.assertNotIn(proibido, carregados)

    def test_sem_argumento_sobe_o_nucleo_antes_do_motor_da_tela(self):
        corpo = ast.parse((RAIZ / "run.py").read_text(encoding="utf-8")).body
        linhas = [ast.unparse(no) for no in corpo]
        desvio = next(i for i, t in enumerate(linhas) if "main_nucleo" in t)
        self.assertIn("'--tela' not in sys.argv", linhas[desvio])
        self.assertIn("LOOM_SEM_NUCLEO", linhas[desvio])
        self.assertLess(next(i for i, t in enumerate(linhas) if "configurar()" in t), desvio)
        self.assertLess(desvio, next(i for i, t in enumerate(linhas) if "preparar_motor(os.environ" in t))

    def test_tela_sobe_com_o_mesmo_programa_e_o_argumento_da_tela(self):
        comando = nucleo.comando_da_tela()
        self.assertEqual((comando[0], comando[-1]), (sys.executable, "--tela"))
        with unittest.mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(nucleo.comando_da_tela(), [sys.executable, "--tela"])
            self.assertEqual(nucleo.comando_do_nucleo(), [sys.executable])


if __name__ == "__main__":
    unittest.main()
