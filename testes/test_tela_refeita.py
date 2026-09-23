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

from loom import app as mod_app  # noqa: E402
from loom import instancia  # noqa: E402


class JanelaFalsa:
    """So o que os metodos testados usam da MainWindow."""

    recuperar_tela = mod_app.MainWindow.recuperar_tela
    _atender = mod_app.MainWindow._atender
    _vigiar = mod_app.MainWindow._vigiar
    _so_fundo_na_tela = mod_app.MainWindow._so_fundo_na_tela
    _abrir_aviso = mod_app.MainWindow._abrir_aviso

    def __init__(self) -> None:
        self._recuperacoes: list[float] = []
        self.montagens = 0
        self.pra_frente = 0
        self.visivel = True
        self.view = object()
        self._aba_avisada = ""
        self.bridge = unittest.mock.MagicMock(ultimo_pulso=0.0)
        self._vigia = unittest.mock.MagicMock()

    def _montar_tela(self) -> None:
        self.montagens += 1

    def _trazer_pra_frente(self) -> None:
        self.pra_frente += 1

    def isVisible(self) -> bool:
        return self.visivel

    def isMinimized(self) -> bool:
        return False

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
        with relogio(5.0), unittest.mock.patch("builtins.print"):
            janela._vigiar()
        self.assertEqual(janela.montagens, 0)
        janela._vigia.recomecar.assert_called_once_with(5.0)

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
    def conexao(self, pedido: bytes) -> unittest.mock.MagicMock:
        conexao = unittest.mock.MagicMock()
        conexao.readAll.return_value = pedido
        return conexao

    def test_pedido_de_mostrar_refaz_a_tela_mesmo_no_teto_e_responde(self):
        janela = JanelaFalsa()
        janela._recuperacoes = [1.0, 2.0, 3.0]
        conexao = self.conexao(instancia.PEDIDO + b"\n")
        with relogio(4.0):
            janela._atender(conexao)
        self.assertEqual((janela.montagens, janela.pra_frente), (1, 1))
        resposta = conexao.write.call_args.args[0]
        # quem chamou espera ate a quebra de linha e confere o comeco
        self.assertTrue(resposta.endswith(b"\n"))
        self.assertTrue(resposta.startswith(instancia.RESPOSTA))
        conexao.flush.assert_called_once_with()

    def test_pedido_estranho_e_desligado_sem_resposta(self):
        janela = JanelaFalsa()
        conexao = self.conexao(b"GET / HTTP/1.1")
        janela._atender(conexao)
        conexao.disconnectFromServer.assert_called_once_with()
        conexao.write.assert_not_called()
        self.assertEqual((janela.montagens, janela.pra_frente), (0, 0))


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
