"""Tela que cai e volta sozinha, sem perder os shells.

Nada aqui abre janela nem shell: as sessoes sao falsas e a vigia roda com relogio falso.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from base import ComPastaPropria  # noqa: E402

from PySide6.QtCore import QCoreApplication  # noqa: E402

from loom import bridge as mod_bridge  # noqa: E402
from loom import config, instancia, sessao_salva, vigia  # noqa: E402


def garantir_app() -> None:
    """Na hora do teste e nao no import: a bateria completa importa todos os modulos antes
    de rodar, e uma aplicacao sem janela criada aqui derrubava os testes que abrem a janela."""
    QCoreApplication.instance() or QCoreApplication([])


class SessaoFalsa:
    def __init__(self, viva: bool = True) -> None:
        self.alive = viva
        self.parada = False
        self.tamanho = (132, 40)

    def stop(self) -> None:
        self.parada = True
        self.alive = False

    def write(self, data: str) -> None:
        pass

    def resize(self, cols: int, rows: int) -> None:
        pass


class ShellsVivos(ComPastaPropria):
    """A pagina nova acha os shells que continuaram rodando, com o que eles ja mostraram."""

    def setUp(self) -> None:
        super().setUp()
        garantir_app()
        self.ponte = mod_bridge.Bridge()
        self.ponte._relistar.stop()
        self.ponte._flush_timer.stop()

    def tearDown(self) -> None:
        self.ponte.shutdown()
        super().tearDown()

    def abrir(self, sid: str, pasta: str = "", viva: bool = True) -> SessaoFalsa:
        falsa = SessaoFalsa(viva)
        self.ponte._sessions[sid] = falsa
        self.ponte._origem[sid] = {"shell": "pwsh", "perfil": "p1", "pasta": pasta, "elevado": False}
        return falsa

    def vivas(self) -> list[dict]:
        return json.loads(self.ponte.liveSessions())

    def test_start_normal_nao_tem_nada_pra_reconectar(self):
        self.assertEqual(self.vivas(), [])

    def test_a_aba_volta_com_o_que_o_shell_ja_tinha_mostrado(self):
        self.abrir("a1", pasta=r"C:\proj")
        self.ponte._on_output("a1", "PS C:\\proj> ")
        self.ponte._flush()
        self.ponte._on_output("a1", "claude\r\n")
        self.ponte._flush()

        (unica,) = self.vivas()
        self.assertEqual(unica["id"], "a1")
        self.assertEqual(unica["pasta"], r"C:\proj")
        self.assertEqual(unica["perfil"], "p1")
        self.assertEqual(unica["historico"], "PS C:\\proj> claude\r\n")
        # a tela redesenha no tamanho em que o shell escreveu, antes de medir a propria
        self.assertEqual((unica["cols"], unica["rows"]), (132, 40))

    def test_o_que_ainda_estava_no_buffer_entra_no_historico(self):
        """Sem isso o pedaco que chegou nos ultimos milissegundos sumia da tela refeita."""
        self.abrir("a1")
        self.ponte._on_output("a1", "ainda na fila")
        self.assertEqual(self.vivas()[0]["historico"], "ainda na fila")

    def test_historico_longo_guarda_so_o_fim_e_comeca_numa_linha_inteira(self):
        antes = mod_bridge.HISTORICO_POR_SESSAO
        mod_bridge.HISTORICO_POR_SESSAO = 22
        try:
            self.abrir("a1")
            # o shell entrega em pedacos que nao respeitam linha: o corte cai no meio de uma
            for pedaco in ("\x1b[31mum\r\nlinha dois\r\nlin", "ha tres\r\nlinha quatro\r\n"):
                self.ponte._on_output("a1", pedaco)
                self.ponte._flush()
            historico = self.vivas()[0]["historico"]
        finally:
            mod_bridge.HISTORICO_POR_SESSAO = antes
        self.assertEqual(historico, "linha quatro\r\n")

    def test_corte_no_fim_de_uma_linha_nao_perde_a_seguinte(self):
        antes = mod_bridge.HISTORICO_POR_SESSAO
        mod_bridge.HISTORICO_POR_SESSAO = 22
        try:
            self.abrir("a1")
            for pedaco in ("linha um\r\n", "linha dois\r\n", "linha tres\r\n"):
                self.ponte._on_output("a1", pedaco)
                self.ponte._flush()
            historico = self.vivas()[0]["historico"]
        finally:
            mod_bridge.HISTORICO_POR_SESSAO = antes
        self.assertEqual(historico, "linha tres\r\n")

    def test_ordem_apelido_e_ativa_vem_das_abas_anotadas(self):
        self.abrir("a1")
        self.abrir("a2")
        self.ponte.saveTabs(
            json.dumps(
                [
                    {"shell": "pwsh", "sessao": "a2", "apelido": "build", "ativa": True},
                    {"shell": "pwsh", "sessao": "a1", "apelido": "", "ativa": False},
                ]
            )
        )
        vivas = self.vivas()
        self.assertEqual([v["id"] for v in vivas], ["a2", "a1"])
        self.assertEqual(vivas[0]["apelido"], "build")
        self.assertTrue(vivas[0]["ativa"])

    def test_shell_morto_nao_volta_como_aba_viva(self):
        self.abrir("a1", viva=False)
        self.assertEqual(self.vivas(), [])

    def test_pulso_da_pagina_fica_anotado(self):
        self.assertEqual(self.ponte.ultimo_pulso, 0.0)
        self.ponte.pulso()
        self.assertGreater(self.ponte.ultimo_pulso, 0.0)

    def test_aba_fechada_esquece_o_historico(self):
        self.abrir("a1")
        self.ponte._on_output("a1", "x")
        self.ponte._flush()
        self.ponte.closeSession("a1")
        self.assertNotIn("a1", self.ponte._historico)
        self.assertNotIn("a1", self.ponte._origem)

    def test_sem_restaurar_abas_o_disco_nao_e_tocado_mas_a_memoria_sim(self):
        config.salvar({"restaurarAbas": False})
        self.abrir("a1")
        self.ponte.saveTabs(json.dumps([{"shell": "pwsh", "sessao": "a1", "apelido": "x"}]))
        self.assertFalse(sessao_salva.ARQUIVO.exists())
        self.assertEqual(self.vivas()[0]["apelido"], "x")

    def test_com_restaurar_abas_o_disco_continua_recebendo(self):
        config.salvar({"restaurarAbas": True})
        self.ponte.saveTabs(json.dumps([{"shell": "pwsh", "apelido": "x"}]))
        self.assertEqual(sessao_salva.ler()[0]["apelido"], "x")


class Vigia(unittest.TestCase):
    """Quando refazer a tela, com relogio falso."""

    def rodar(self, vigia_, passos, visivel=True, pulso=None, sumiu=False, inicio=0.0):
        """Anda o relogio de 3 em 3 segundos e devolve o primeiro motivo, com o instante."""
        agora = inicio
        for _ in range(passos):
            agora += vigia.INTERVALO_MS / 1000
            ultimo = agora if pulso is None else pulso
            motivo = vigia_.tique(agora, visivel, ultimo, lambda: sumiu)
            if motivo:
                return motivo, agora
        return "", agora

    def test_pagina_saudavel_nunca_e_refeita(self):
        self.assertEqual(self.rodar(vigia.Vigia(0.0), 200)[0], "")

    def test_sem_pulso_a_tela_e_refeita(self):
        motivo, quando = self.rodar(vigia.Vigia(0.0), 50, pulso=0.0)
        self.assertEqual(motivo, "a pagina parou de desenhar")
        self.assertGreater(quando, vigia.SEM_PULSO)

    def test_so_o_fundo_na_tela_duas_vezes_seguidas_refaz(self):
        motivo, quando = self.rodar(vigia.Vigia(0.0), 50, sumiu=True)
        self.assertEqual(motivo, "a janela mostra so o fundo, sem a pagina")
        self.assertLess(quando, vigia.CARENCIA + 2 * vigia.INTERVALO_MS / 1000 + 0.01)

    def test_uma_leitura_so_do_fundo_nao_basta(self):
        """Um quadro pego no meio de um redesenho nao pode refazer a tela."""
        v = vigia.Vigia(0.0)
        agora = 0.0
        while agora < vigia.CARENCIA + 3:
            agora += 3
            self.assertEqual(v.tique(agora, True, agora, lambda: False), "")
        for sumiu in (True, False, True, False):
            agora += 3
            self.assertEqual(v.tique(agora, True, agora, lambda s=sumiu: s), "")

    def test_tela_que_nao_da_pra_ler_nao_conta(self):
        v = vigia.Vigia(0.0)
        agora = 0.0
        for _ in range(60):
            agora += 3
            self.assertEqual(v.tique(agora, True, agora, lambda: None), "")

    def test_minimizada_nao_e_refeita(self):
        self.assertEqual(self.rodar(vigia.Vigia(0.0), 100, visivel=False, pulso=0.0)[0], "")

    def test_ao_voltar_do_minimizado_ha_carencia(self):
        v = vigia.Vigia(0.0)
        agora = 0.0
        for _ in range(20):
            agora += 3
            v.tique(agora, False, 0.0, lambda: True)
        # voltou a aparecer sem pulso nenhum: a pagina ganha a carencia antes de ser julgada
        agora += 3
        self.assertEqual(v.tique(agora, True, 0.0, lambda: True), "")

    def test_relogio_que_pula_recomeca_a_contagem(self):
        """Maquina que dormiu ou thread da tela presa nao e prova de tela quebrada."""
        v = vigia.Vigia(0.0)
        self.assertEqual(v.tique(500.0, True, 0.0, lambda: True), "")
        self.assertEqual(v.tique(503.0, True, 0.0, lambda: True), "")

    def test_nao_le_a_tela_quando_o_pulso_ja_decidiu(self):
        """Ler a tela custa milissegundos; na volta em que o pulso ja decidiu, nao se le."""
        v = vigia.Vigia(0.0)
        agora = 0.0
        motivo = ""
        while not motivo:
            agora += 3
            lidas = []
            motivo = v.tique(agora, True, 0.0, lambda: lidas.append(1) or False)
        self.assertEqual(motivo, "a pagina parou de desenhar")
        self.assertEqual(lidas, [])

    def test_recomecar_da_nova_carencia(self):
        v = vigia.Vigia(0.0)
        motivo, agora = self.rodar(v, 50, sumiu=True)
        self.assertTrue(motivo)
        v.recomecar(agora)
        self.assertEqual(self.rodar(v, 3, sumiu=True, inicio=agora)[0], "")


class TelaLisa(unittest.TestCase):
    """Quando as linhas lidas da janela contam como so o fundo."""

    # o que a janela preta de verdade mostrava, lido da tela: um degrade quase imperceptivel
    JANELA_PRETA_DE_VERDADE = [0x1E1E1E] * 12 + [0x1D1D1D] * 20 + [0x1C1C1C] * 8

    # a janela de pe no pior caso: barra de cima vazia, lateral e terminal sem texto
    PAGINA_DE_PE_SEM_TEXTO = [0x1C1F25] * 40 + [0x14161B] * 10 + [0x16181C] * 30

    def test_a_janela_preta_de_verdade_conta(self):
        self.assertTrue(vigia.so_fundo(self.JANELA_PRETA_DE_VERDADE))

    def test_a_cor_sentinela_lisa_conta(self):
        self.assertTrue(vigia.so_fundo([vigia.rgb_da_sentinela() | 0xFF000000] * 40))

    def test_preto_liso_conta(self):
        self.assertTrue(vigia.so_fundo([0x000000] * 40))

    def test_pagina_de_pe_mesmo_sem_texto_nao_conta(self):
        """A barra de cima e mais clara que o terminal: isso basta pra nao ser fundo liso."""
        self.assertFalse(vigia.so_fundo(self.PAGINA_DE_PE_SEM_TEXTO))

    def test_um_pixel_da_pagina_basta_pra_nao_ser(self):
        self.assertFalse(vigia.so_fundo(self.JANELA_PRETA_DE_VERDADE + [0xC8CFDA]))

    def test_poucos_pixels_nao_decidem(self):
        self.assertFalse(vigia.so_fundo([0x1E1E1E] * 3))

    def test_a_pagina_nunca_pinta_a_cor_do_fundo(self):
        from pathlib import Path

        ui = Path(__file__).resolve().parent.parent / "ui"
        for nome in ("style.css", "app.js", "stats.js", "index.html"):
            texto = (ui / nome).read_text(encoding="utf-8").lower()
            self.assertNotIn(vigia.COR_SENTINELA, texto, nome)


@unittest.skipUnless(sys.platform == "win32", "pipe nomeado e do Windows")
class OutraInstancia(unittest.TestCase):
    """Abrir o Loom de novo pede ao que esta aberto pra voltar, pelo pipe da instancia."""

    def setUp(self) -> None:
        garantir_app()
        self.nome_antigo = instancia.nome
        self.nome = f"loom-teste-{os.getpid()}"
        instancia.nome = lambda: self.nome

    def tearDown(self) -> None:
        instancia.nome = self.nome_antigo

    def servidor(self, resposta: bytes | None) -> threading.Thread:
        """Um pipe cru, sem Qt, no lugar do Loom aberto."""
        kernel = ctypes.windll.kernel32
        kernel.CreateNamedPipeW.restype = ctypes.c_void_p
        PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE, PIPE_WAIT = 3, 0, 0
        cano = kernel.CreateNamedPipeW(
            rf"\\.\pipe\{self.nome}", PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_WAIT, 1, 4096, 4096, 0, None
        )
        self.assertNotEqual(cano, ctypes.c_void_p(-1).value)
        self.recebido = b""

        def atender():
            kernel.ConnectNamedPipe(ctypes.c_void_p(cano), None)
            buf = ctypes.create_string_buffer(64)
            lidos = ctypes.c_ulong()
            kernel.ReadFile(ctypes.c_void_p(cano), buf, 64, ctypes.byref(lidos), None)
            self.recebido = buf.raw[: lidos.value]
            if resposta is not None:
                escritos = ctypes.c_ulong()
                kernel.WriteFile(ctypes.c_void_p(cano), resposta, len(resposta), ctypes.byref(escritos), None)
                kernel.FlushFileBuffers(ctypes.c_void_p(cano))
                # como o app de verdade: a conexao fica aberta ate quem chamou sair
                kernel.ReadFile(ctypes.c_void_p(cano), buf, 64, ctypes.byref(lidos), None)
            else:
                threading.Event().wait(1.5)
            kernel.DisconnectNamedPipe(ctypes.c_void_p(cano))
            kernel.CloseHandle(ctypes.c_void_p(cano))

        fio = threading.Thread(target=atender, daemon=True)
        fio.start()
        return fio

    def test_sem_ninguem_aberto_sobe_normal(self):
        self.assertFalse(instancia.chamar_quem_ja_esta_aberto(espera_ms=300))

    def test_quem_esta_aberto_atende_e_esta_nao_sobe(self):
        fio = self.servidor(b"ok\n")
        self.assertTrue(instancia.chamar_quem_ja_esta_aberto(espera_ms=2000))
        fio.join(3)
        self.assertEqual(self.recebido.strip(), b"mostrar")

    def test_resposta_estranha_nao_conta_como_atendido(self):
        fio = self.servidor(b"nao\n")
        self.assertFalse(instancia.chamar_quem_ja_esta_aberto(espera_ms=2000))
        fio.join(3)

    def test_quem_esta_aberto_mas_travado_nao_prende_esta(self):
        fio = self.servidor(None)
        self.assertFalse(instancia.chamar_quem_ja_esta_aberto(espera_ms=400))
        fio.join(3)

    def test_nome_muda_com_a_instalacao(self):
        self.assertIn("loom-", self.nome_antigo())
        self.assertEqual(self.nome_antigo(), self.nome_antigo())


if __name__ == "__main__":
    unittest.main()
