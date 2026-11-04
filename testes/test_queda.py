"""Queda da tela: a marca de tela aberta e o historico que decide o motor.

Tudo numa pasta temporaria. Processo morto e boot trocado entram falsificados; o processo vivo e
o proprio teste, e o boot de verdade e lido do Windows.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import unittest.mock

from base import ComPastaPropria

from loom import caminhos, queda

DIA = 86400.0


def morto(pid: int) -> None:
    """criado_em de quem ja saiu: nenhum processo com aquele numero."""
    return None


class MarcaDeTelaAberta(ComPastaPropria):
    def gravar_marca(self, **dados) -> None:
        marca = {"pid": 4242, "criado": 99, "boot": queda.boot_atual(), "desde": "2026-09-27T20:58:00-03:00"}
        marca.update(dados)
        queda.arquivo_da_marca().parent.mkdir(parents=True, exist_ok=True)
        queda.arquivo_da_marca().write_text(json.dumps(marca), encoding="utf-8")

    def conferir(self):
        with unittest.mock.patch.object(queda, "criado_em", side_effect=morto):
            return queda.conferir_ao_subir()

    def test_sem_marca_nao_ha_queda(self):
        self.assertIsNone(queda.conferir_ao_subir())
        self.assertIsNone(queda.recente())

    def test_marca_que_sobrou_no_mesmo_boot_e_queda_da_tela(self):
        """O ANGLE derruba o processo sem aboutToQuit: a marca fica e o proximo anota a queda."""
        self.gravar_marca()
        with self.assertLogs("loom.queda", "WARNING"):
            anotada = self.conferir()
        self.assertEqual(anotada["tipo"], "tela")
        self.assertFalse(queda.arquivo_da_marca().exists(), "a mesma marca nao pode contar duas vezes")
        self.assertEqual(queda.recente()["quando"], anotada["quando"])

    def test_queda_de_energia_nao_conta_como_queda_da_tela(self):
        """Faltou luz com o Loom aberto: a marca sobra, mas a maquina ligou de novo depois.

        Contar isso tirava a placa de video por uma semana, e a placa nao tinha culpa nenhuma.
        """
        agora = queda.boot_atual()
        outro = {int: lambda i: i - 1, str: lambda i: "outro-" + i, type(None): lambda i: None}[type(agora["id"])](agora["id"])
        antes = {"id": outro, "em": agora["em"] - 3 * 3600}
        self.gravar_marca(boot=antes)
        with self.assertLogs("loom.queda", "INFO") as logs:
            self.assertIsNone(self.conferir())
        self.assertIn("queda de energia", "\n".join(logs.output))
        self.assertIsNone(queda.recente())
        self.assertFalse(queda.arquivo_da_marca().exists())

    def test_loom_aberto_nao_e_queda_e_a_marca_dele_fica(self):
        """Quem sobe com outro Loom aberto e a segunda instancia, que so vai chamar a primeira."""
        queda.marcar_subida("gpu")
        self.assertIsNone(queda.conferir_ao_subir())
        self.assertTrue(queda.arquivo_da_marca().exists())
        self.assertIsNone(queda.recente())

    def test_numero_de_processo_reaproveitado_nao_passa_por_vivo(self):
        """O Windows reusa o pid: sem conferir quando o processo nasceu, a queda sumia."""
        self.gravar_marca(pid=os.getpid(), criado=1)
        with self.assertLogs("loom.queda", "WARNING"):
            self.assertIsNotNone(queda.conferir_ao_subir())

    def test_marca_ilegivel_nao_vira_queda_e_sai(self):
        caminho = queda.arquivo_da_marca()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("{", encoding="utf-8")
        with self.assertLogs("loom.queda", "INFO"):
            self.assertIsNone(self.conferir())
        self.assertFalse(caminho.exists())

    def test_fechar_apaga_so_a_propria_marca(self):
        self.gravar_marca(pid=4242)
        queda.apagar_marca()
        self.assertTrue(queda.arquivo_da_marca().exists(), "a marca de outro Loom continua valendo")
        queda.marcar_subida("gpu")
        queda.apagar_marca()
        self.assertFalse(queda.arquivo_da_marca().exists())

    def test_a_marca_diz_quem_e_e_em_que_boot(self):
        queda.marcar_subida("software")
        marca = json.loads(queda.arquivo_da_marca().read_text(encoding="utf-8"))
        self.assertEqual(marca["pid"], os.getpid())
        self.assertEqual(marca["criado"], queda.criado_em(os.getpid()))
        self.assertEqual(marca["desenho"], "software")
        self.assertTrue(queda.mesmo_boot(marca["boot"], queda.boot_atual()))

    def test_cada_instalacao_tem_a_sua_marca(self):
        """O Loom do codigo e o instalado rodam juntos; a marca de um nao e queda do outro."""
        daqui = queda.arquivo_da_marca()
        with unittest.mock.patch.object(caminhos, "PASTA_DA_TELA", caminhos.PASTA_DA_TELA.parent / "outra" / "ui"):
            self.assertNotEqual(queda.arquivo_da_marca(), daqui)
        self.assertEqual(daqui.parent, caminhos.RAIZ)


class MesmoBoot(unittest.TestCase):
    def test_contador_do_windows_manda_quando_os_dois_tem(self):
        self.assertTrue(queda.mesmo_boot({"id": 7, "em": 1000.0}, {"id": 7, "em": 1030.0}))
        self.assertFalse(queda.mesmo_boot({"id": 7, "em": 1000.0}, {"id": 8, "em": 1000.0}))

    def test_sem_contador_compara_a_hora_em_que_a_maquina_ligou(self):
        self.assertTrue(queda.mesmo_boot({"id": None, "em": 1000.0}, {"id": 5, "em": 1050.0}))
        self.assertFalse(queda.mesmo_boot({"id": None, "em": 1000.0}, {"id": 5, "em": 9000.0}))

    def test_na_duvida_nao_e_o_mesmo(self):
        for antes in (None, {}, {"em": "x"}, "boot"):
            self.assertFalse(queda.mesmo_boot(antes, {"id": 1, "em": 1.0}), antes)

    def test_boot_de_verdade_e_estavel_e_processo_vivo_tem_nascimento(self):
        primeiro, segundo = queda.boot_atual(), queda.boot_atual()
        self.assertTrue(queda.mesmo_boot(primeiro, segundo))
        self.assertLess(primeiro["em"], time.time())
        self.assertIsNotNone(queda.criado_em(os.getpid()))
        self.assertIsNone(queda.criado_em(0x7FFFFFF0))


class QuedaRecente(ComPastaPropria):
    def test_sete_dias_desde_a_ultima(self):
        agora = time.time()
        queda.registrar("tela", agora - 6 * DIA)
        achada = queda.recente(agora)
        self.assertEqual(achada["tipo"], "tela")
        self.assertIsNone(queda.recente(agora + 1.5 * DIA))

    def test_a_ultima_queda_decide_ate_quando(self):
        agora = time.time()
        queda.registrar("tela", agora - 5 * DIA)
        queda.registrar("contexto", agora - 1 * DIA)
        achada = queda.recente(agora)
        self.assertEqual(achada["tipo"], "contexto")
        fim = queda._epoca(achada["ate"])
        self.assertAlmostEqual(fim, agora + 6 * DIA, delta=2)

    def test_voltar_pra_placa_esquece_as_de_antes_mas_nao_as_novas(self):
        agora = time.time()
        queda.registrar("tela", agora - DIA)
        queda.voltar_para_gpu(agora - 10)
        self.assertIsNone(queda.recente(agora))
        queda.registrar("tela", agora)
        self.assertIsNotNone(queda.recente(agora + 1))
        historico = json.loads(queda.arquivo().read_text(encoding="utf-8"))
        self.assertEqual(len(historico["quedas"]), 2, "o historico fica, so deixa de contar")

    def test_historico_tem_teto(self):
        for i in range(queda.TETO_DE_QUEDAS + 5):
            queda.registrar("tela", 1_000_000.0 + i)
        historico = json.loads(queda.arquivo().read_text(encoding="utf-8"))
        self.assertEqual(len(historico["quedas"]), queda.TETO_DE_QUEDAS)

    def test_arquivo_estragado_nao_quebra_e_volta_a_gravar(self):
        queda.arquivo().parent.mkdir(parents=True, exist_ok=True)
        for estragado in ("null", "{", '{"quedas": [{"quando": "ontem"}, 3], "voltouEm": 5}'):
            queda.arquivo().write_text(estragado, encoding="utf-8")
            self.assertIsNone(queda.recente(), estragado)
        queda.registrar("tela")
        self.assertIsNotNone(queda.recente())


if __name__ == "__main__":
    unittest.main()
