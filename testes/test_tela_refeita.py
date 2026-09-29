"""Quando a janela refaz a tela e como ela atende a segunda instancia, sem abrir janela.

Os metodos da janela rodam presos num objeto falso: nada de motor web, de bandeja nem de
pipe de verdade. Relogio e conexao tambem sao falsos.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import base  # noqa: E402,F401

from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: E402

from loom import app as mod_app  # noqa: E402
from loom import instancia, vigia  # noqa: E402

TERMINO = QWebEnginePage.RenderProcessTerminationStatus


class ViewFalsa:
    def __init__(self) -> None:
        self.pagina = object()

    def page(self):
        return self.pagina


class JanelaFalsa:
    """So o que os metodos testados usam da MainWindow."""

    recuperar_tela = mod_app.MainWindow.recuperar_tela
    _atender = mod_app.MainWindow._atender
    _mostrar_a_quem_chamou = mod_app.MainWindow._mostrar_a_quem_chamou
    _refazer_se_parou = mod_app.MainWindow._refazer_se_parou
    _pagina_caiu = mod_app.MainWindow._pagina_caiu
    escutar_outras_instancias = mod_app.MainWindow.escutar_outras_instancias
    _outra_instancia = mod_app.MainWindow._outra_instancia
    _vigiar = mod_app.MainWindow._vigiar
    _so_fundo_na_tela = mod_app.MainWindow._so_fundo_na_tela
    _abrir_aviso = mod_app.MainWindow._abrir_aviso

    def __init__(self) -> None:
        self._recuperacoes: list[float] = []
        self.montagens = 0
        self.pra_frente = 0
        self.visivel = True
        self.minimizada = False
        self.view = ViewFalsa()
        self.quem_emitiu = self.view.pagina
        # montada ha muito tempo: a carencia da tela nova nao atrapalha quem nao a testa
        self._montada_em = -1000.0
        self._aba_avisada = ""
        self._servidor = None
        self.bridge = unittest.mock.MagicMock(ultimo_pulso=0.0)
        self._vigia = unittest.mock.MagicMock()

    def _montar_tela(self) -> None:
        self.montagens += 1

    def _trazer_pra_frente(self) -> None:
        self.pra_frente += 1

    def sender(self):
        return self.quem_emitiu

    def isVisible(self) -> bool:
        return self.visivel

    def isMinimized(self) -> bool:
        return self.minimizada

    def winId(self) -> int:
        return 77


def relogio(instante: float):
    return unittest.mock.patch.object(mod_app.time, "monotonic", return_value=instante)


class TetoDeRecuperacao(unittest.TestCase):
    def test_tres_por_minuto_e_depois_espera_o_mais_velho_sair(self):
        janela = JanelaFalsa()
        for instante in (0.0, 10.0, 20.0):
            with relogio(instante):
                self.assertTrue(janela.recuperar_tela())
        with relogio(30.0):
            self.assertFalse(janela.recuperar_tela(), "insistir so pisca a janela")
        self.assertEqual(janela.montagens, 3)

        with relogio(61.0):
            self.assertTrue(janela.recuperar_tela())
        self.assertEqual(janela.montagens, 4)

    def test_vigia_que_manda_refazer_no_teto_ainda_recomeca_a_carencia(self):
        janela = JanelaFalsa()
        janela._recuperacoes = [1.0, 2.0, 3.0]
        janela._vigia.tique.return_value = "a pagina parou de desenhar"
        with relogio(5.0), self.assertLogs("loom.app", "WARNING") as logs:
            janela._vigiar()
        self.assertEqual(janela.montagens, 0)
        janela._vigia.recomecar.assert_called_once_with(5.0)
        self.assertIn("a pagina parou de desenhar", logs.output[0])

    def test_vigia_sem_motivo_nao_mexe_em_nada(self):
        janela = JanelaFalsa()
        janela._vigia.tique.return_value = ""
        with relogio(5.0):
            janela._vigiar()
        self.assertEqual(janela.montagens, 0)
        janela._vigia.recomecar.assert_not_called()

    def test_vigia_recebe_o_pulso_da_ponte_e_se_a_janela_aparece(self):
        janela = JanelaFalsa()
        janela.visivel = False
        janela.bridge.ultimo_pulso = 42.0
        janela._vigia.tique.return_value = ""
        with relogio(50.0):
            janela._vigiar()
        agora, visivel, pulso, _ = janela._vigia.tique.call_args.args
        self.assertEqual((agora, visivel, pulso), (50.0, False, 42.0))


class LeituraDaTela(unittest.TestCase):
    def test_sem_tela_montada_ou_sem_leitura_nao_decide(self):
        janela = JanelaFalsa()
        with unittest.mock.patch.object(mod_app.mod_vigia, "ler_janela", return_value=None) as ler:
            self.assertIsNone(janela._so_fundo_na_tela())
        ler.assert_called_once_with(77)
        janela.view = None
        self.assertIsNone(janela._so_fundo_na_tela())

    def test_linhas_lisas_contam_como_so_o_fundo(self):
        janela = JanelaFalsa()
        with unittest.mock.patch.object(mod_app.mod_vigia, "ler_janela", return_value=[0x16181D] * 40):
            self.assertTrue(janela._so_fundo_na_tela())
        with unittest.mock.patch.object(mod_app.mod_vigia, "ler_janela", return_value=[0x16181D, 0xC8CFDA] * 20):
            self.assertFalse(janela._so_fundo_na_tela())


class SegundaInstancia(unittest.TestCase):
    """Abrir o Loom de novo traz a janela; a tela so e refeita se a pagina parou de pulsar."""

    def atender(self, janela: JanelaFalsa, *pedacos: bytes) -> unittest.mock.MagicMock:
        conexao = unittest.mock.MagicMock()
        conexao.readAll.side_effect = list(pedacos)
        acumulado = bytearray()
        for _ in pedacos:
            janela._atender(conexao, acumulado)
        return conexao

    def assertRespondeu(self, conexao: unittest.mock.MagicMock) -> None:
        resposta = conexao.write.call_args.args[0]
        # quem chamou espera ate a quebra de linha e confere o comeco
        self.assertTrue(resposta.endswith(b"\n"))
        self.assertTrue(resposta.startswith(instancia.RESPOSTA))
        conexao.flush.assert_called_once_with()

    def test_pagina_pulsando_so_vem_pra_frente(self):
        """Refazia a tela a cada abertura, recriando o WebGL de todas as abas: o caminho do crash."""
        janela = JanelaFalsa()
        janela.bridge.ultimo_pulso = 99.0
        with relogio(100.0):
            conexao = self.atender(janela, instancia.PEDIDO + b"\n")
        self.assertEqual((janela.montagens, janela.pra_frente), (0, 1))
        self.assertRespondeu(conexao)

    def test_pagina_a_vista_sem_pulso_e_refeita_mesmo_no_teto(self):
        janela = JanelaFalsa()
        janela._recuperacoes = [97.0, 98.0, 99.0]
        janela.bridge.ultimo_pulso = 100.0 - vigia.SEM_PULSO - 1
        with relogio(100.0), self.assertLogs("loom.app", "WARNING"):
            conexao = self.atender(janela, instancia.PEDIDO + b"\n")
        self.assertEqual((janela.montagens, janela.pra_frente), (1, 1))
        self.assertRespondeu(conexao)

    def test_tela_recem_montada_nao_e_refeita_de_novo(self):
        """A vigia acabou de refazer: a pagina nova ainda nao pulsou, e isso nao prova nada."""
        janela = JanelaFalsa()
        janela._montada_em = 95.0
        with relogio(100.0):
            self.atender(janela, instancia.PEDIDO + b"\n")
        self.assertEqual((janela.montagens, janela.pra_frente), (0, 1))

    def test_minimizada_so_e_julgada_depois_de_aparecer(self):
        """Minimizada a pagina nao desenha de proposito: o pulso velho ali nao prova nada."""
        janela = JanelaFalsa()
        janela.minimizada = True
        with relogio(100.0), unittest.mock.patch.object(mod_app, "QTimer") as relogio_qt:
            self.atender(janela, instancia.PEDIDO + b"\n")
        self.assertEqual((janela.montagens, janela.pra_frente), (0, 1))
        prazo, depois = relogio_qt.singleShot.call_args.args
        self.assertEqual(prazo, mod_app.ESPERA_DO_PULSO_MS)

        janela.bridge.ultimo_pulso = 100.5
        with relogio(105.0):
            depois()
        self.assertEqual(janela.montagens, 0, "voltou a pulsar depois de aparecer")

        janela.bridge.ultimo_pulso = 99.0
        with relogio(105.0), self.assertLogs("loom.app", "WARNING"):
            depois()
        self.assertEqual(janela.montagens, 1)

    def test_pedido_em_pedacos_e_juntado_ate_a_quebra_de_linha(self):
        janela = JanelaFalsa()
        janela.bridge.ultimo_pulso = 99.0
        with relogio(100.0):
            conexao = self.atender(janela, b"mos", b"trar", b"\n")
        conexao.disconnectFromServer.assert_not_called()
        self.assertEqual(janela.pra_frente, 1)
        self.assertRespondeu(conexao)

    def test_pedido_estranho_e_desligado_sem_resposta(self):
        janela = JanelaFalsa()
        conexao = self.atender(janela, b"GET / HTTP/1.1\r\n")
        conexao.disconnectFromServer.assert_called_once_with()
        conexao.write.assert_not_called()
        self.assertEqual((janela.montagens, janela.pra_frente), (0, 0))

    def test_linha_que_nunca_fecha_e_desligada_no_teto(self):
        janela = JanelaFalsa()
        conexao = self.atender(janela, b"x" * 200, b"x" * 200)
        conexao.disconnectFromServer.assert_called_once_with()
        conexao.write.assert_not_called()

    def test_canal_so_aceita_a_propria_pessoa(self):
        janela = JanelaFalsa()
        with unittest.mock.patch.object(mod_app, "QLocalServer") as classe:
            classe.return_value.listen.return_value = True
            janela.escutar_outras_instancias()
        servidor = classe.return_value
        servidor.setSocketOptions.assert_called_once_with(classe.SocketOption.UserAccessOption)
        chamadas = [nome for nome, _, _ in servidor.method_calls]
        self.assertLess(chamadas.index("setSocketOptions"), chamadas.index("listen"))


class PaginaQueCai(unittest.TestCase):
    def test_pagina_atual_que_caiu_e_refeita(self):
        janela = JanelaFalsa()
        with unittest.mock.patch.object(mod_app, "QTimer") as relogio_qt, self.assertLogs("loom.app", "ERROR"):
            janela._pagina_caiu(TERMINO.CrashedTerminationStatus, 3)
        prazo, refazer = relogio_qt.singleShot.call_args.args
        self.assertEqual(prazo, 300)
        refazer()
        self.assertEqual(janela.montagens, 1)

    def test_pagina_da_tela_ja_trocada_nao_derruba_a_nova(self):
        """A pagina descartada terminando disparava outra reconstrucao, e a nova soltava mais uma."""
        janela = JanelaFalsa()
        janela.quem_emitiu = object()
        with unittest.mock.patch.object(mod_app, "QTimer") as relogio_qt:
            janela._pagina_caiu(TERMINO.KilledTerminationStatus, 9)
        relogio_qt.singleShot.assert_not_called()

    def test_fim_normal_da_pagina_nao_e_queda(self):
        janela = JanelaFalsa()
        with unittest.mock.patch.object(mod_app, "QTimer") as relogio_qt:
            janela._pagina_caiu(TERMINO.NormalTerminationStatus, 0)
        relogio_qt.singleShot.assert_not_called()


class SubidaDoApp(unittest.TestCase):
    """O main: quem sobe marca a tela aberta; quem so chama o outro nao toca em nada."""

    def subir(self, ja_aberto: bool, trava: bool, atendeu: bool) -> dict:
        falsos = {
            "aplicacao": unittest.mock.patch.object(mod_app, "QApplication"),
            "janela": unittest.mock.patch.object(mod_app, "MainWindow"),
            "chamou": unittest.mock.patch.object(
                mod_app.mod_instancia, "chamar_quem_ja_esta_aberto", return_value=ja_aberto
            ),
            "trava": unittest.mock.patch.object(mod_app.mod_instancia, "segurar_trava", return_value=trava),
            "esperou": unittest.mock.patch.object(
                mod_app.mod_instancia, "esperar_quem_esta_subindo", return_value=atendeu
            ),
            "reparou": unittest.mock.patch.object(mod_app.mod_perfis, "reparar_status_line_principal"),
            "icone": unittest.mock.patch.object(mod_app, "QIcon"),
            "ligou_qt": unittest.mock.patch.object(mod_app.mod_registro, "ligar_qt"),
            "marcou": unittest.mock.patch.object(mod_app.mod_queda, "marcar_subida"),
        }
        pegos = {nome: falso.start() for nome, falso in falsos.items()}
        try:
            pegos["aplicacao"].return_value.exec.return_value = 0
            with self.assertNoLogs("loom.app", "WARNING"):
                pegos["codigo"] = mod_app.main()
        finally:
            for falso in falsos.values():
                falso.stop()
        pegos["app"] = pegos["aplicacao"].return_value
        return pegos

    def test_quem_sobe_marca_a_tela_aberta_e_apaga_ao_sair(self):
        subiu = self.subir(ja_aberto=False, trava=True, atendeu=False)
        self.assertEqual(subiu["codigo"], 0)
        subiu["marcou"].assert_called_once()
        subiu["app"].aboutToQuit.connect.assert_called_once_with(mod_app.mod_queda.apagar_marca)
        subiu["ligou_qt"].assert_called_once_with()
        subiu["esperou"].assert_not_called()
        subiu["janela"].return_value.show.assert_called_once_with()

    def test_segundo_start_quase_junto_espera_o_primeiro_e_sai(self):
        """Dois cliques no atalho subiam duas janelas: nenhum achava o outro no canal ainda."""
        subiu = self.subir(ja_aberto=False, trava=False, atendeu=True)
        self.assertEqual(subiu["codigo"], 0)
        subiu["esperou"].assert_called_once_with()
        subiu["janela"].assert_not_called()
        subiu["marcou"].assert_not_called()

    def test_quem_so_chama_o_aberto_nao_marca_nada(self):
        subiu = self.subir(ja_aberto=True, trava=True, atendeu=False)
        self.assertEqual(subiu["codigo"], 0)
        subiu["janela"].assert_not_called()
        subiu["marcou"].assert_not_called()
        subiu["app"].aboutToQuit.connect.assert_not_called()


class CliqueNoAviso(unittest.TestCase):
    def test_leva_a_aba_que_avisou_e_traz_a_janela(self):
        janela = JanelaFalsa()
        janela._aba_avisada = "a7"
        janela._abrir_aviso()
        janela.bridge.irParaAba.emit.assert_called_once_with("a7")
        self.assertEqual(janela.pra_frente, 1)

    def test_sem_aba_so_traz_a_janela(self):
        janela = JanelaFalsa()
        janela._abrir_aviso()
        janela.bridge.irParaAba.emit.assert_not_called()
        self.assertEqual(janela.pra_frente, 1)


if __name__ == "__main__":
    unittest.main()
