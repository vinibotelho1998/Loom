"""Aviso de limite: uma vez por degrau e por janela, e nunca de leitura velha."""

from __future__ import annotations

import json
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from base import ComPastaPropria

from PySide6.QtCore import QCoreApplication

from loom import aviso_limite as aviso
from loom import bridge as mod_bridge
from loom import config

AGORA = 1_790_650_000.0


def leitura(usado: float, zera: float | None = AGORA + 4200, chave: str = "claude:p1:five_hour") -> dict:
    return {"chave": chave, "ferramenta": "Claude", "conta": "Principal", "janela": "sessão de 5 horas",
            "usado": usado, "zera": zera}


class RegraDoAviso(unittest.TestCase):
    def test_abaixo_de_85_nao_avisa(self):
        avisos, vistos = aviso.avisos_novos([leitura(84.9)], {}, AGORA)
        self.assertEqual((avisos, vistos), ([], {}))

    def test_avisa_uma_vez_por_degrau_na_mesma_janela(self):
        avisos, vistos = aviso.avisos_novos([leitura(87)], {}, AGORA)
        self.assertEqual([a["titulo"] for a in avisos], ["Claude: limite em 87%"])
        self.assertEqual(avisos[0]["texto"], "Principal, sessão de 5 horas em 87%. Renova em 1h 10m.")
        de_novo, vistos = aviso.avisos_novos([leitura(90)], vistos, AGORA + 60)
        self.assertEqual(de_novo, [], "a mesma janela nao avisa a cada minuto")
        no_fim, vistos = aviso.avisos_novos([leitura(96)], vistos, AGORA + 120)
        self.assertEqual([a["titulo"] for a in no_fim], ["Claude: limite em 96%"], "o degrau de 95 avisa de novo")
        self.assertEqual(aviso.avisos_novos([leitura(99)], vistos, AGORA + 180)[0], [])

    def test_janela_nova_arma_o_aviso_de_novo_e_a_velha_sai_do_registro(self):
        _, vistos = aviso.avisos_novos([leitura(90)], {}, AGORA)
        vistos["claude:p9:seven_day"] = {"zera": AGORA + 100, "degrau": 95}
        proxima = AGORA + 4200 + 18000
        avisos, vistos = aviso.avisos_novos([leitura(88, zera=proxima)], vistos, AGORA + 4300)
        self.assertEqual(len(avisos), 1)
        self.assertEqual(vistos, {"claude:p1:five_hour": {"zera": proxima, "degrau": 85}})

    def test_leitura_de_janela_que_ja_renovou_nao_avisa(self):
        avisos, _ = aviso.avisos_novos([leitura(99, zera=AGORA - 5)], {}, AGORA)
        self.assertEqual(avisos, [])

    def test_contas_e_janelas_sao_independentes(self):
        avisos, _ = aviso.avisos_novos(
            [leitura(90), leitura(91, chave="claude:p1:seven_day"), leitura(92, chave="claude:p2:five_hour")], {}, AGORA
        )
        self.assertEqual(len(avisos), 3)

    def test_contagem_do_jeito_do_painel(self):
        casos = {59: "1m", 45 * 60: "45m", 3600: "1h", 4200: "1h 10m", 2 * 86400 + 3 * 3600: "2d 3h", 2 * 86400 + 900: "2d 15m"}
        for segundos, esperado in casos.items():
            self.assertEqual(aviso.contagem(segundos), esperado, segundos)


class LeiturasDasFerramentas(unittest.TestCase):
    def test_claude_vem_da_status_line_com_o_nome_da_conta(self):
        limites = {
            "84092a89": {"faixas": {"five_hour": {"usado": 87, "zera": AGORA + 60}, "seven_day": {"usado": None}}},
            "__padrao__": {"faixas": {"seven_day": {"usado": 91.5, "zera": AGORA + 600}}},
            "sem-faixa": {"lido": 1},
        }
        leituras = aviso.leituras_do_claude(limites, {"84092a89": "Principal"})
        self.assertEqual(
            [(l["chave"], l["conta"], l["janela"], l["usado"]) for l in leituras],
            [("claude:84092a89:five_hour", "Principal", "sessão de 5 horas", 87.0),
             ("claude:__padrao__:seven_day", "Principal", "semana", 91.5)],
        )

    def test_codex_vem_do_painel_so_com_limite_real(self):
        zera = datetime.fromtimestamp(AGORA + 3600, timezone(timedelta(hours=-3))).isoformat()
        painel = {"contas": [
            {"id": "codex:1", "ferramenta": "codex", "nome": "Codex", "janelas": [
                {"tipo": "7d", "usado": 86.0, "zeraEm": zera, "origem": "real"},
                {"tipo": "5h", "usado": 99.0, "zeraEm": zera, "origem": "estimada"}]},
            {"id": "claude:x", "ferramenta": "claude", "janelas": [{"tipo": "5h", "usado": 99.0, "origem": "real"}]},
        ]}
        leituras = aviso.leituras_do_painel(painel)
        self.assertEqual([(l["chave"], l["ferramenta"], l["janela"]) for l in leituras], [("codex:1:7d", "Codex", "semana")])
        self.assertAlmostEqual(leituras[0]["zera"], AGORA + 3600, places=3)


class AvisoNaPonte(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        QCoreApplication.instance() or QCoreApplication([])
        self._dados = unittest.mock.patch.object(mod_bridge, "RAIZ_DADOS", self.raiz / "dados")
        self._dados.start()
        self.ponte = mod_bridge.Bridge()
        self.avisos: list[tuple] = []
        self.ponte.notificacaoPedida.connect(lambda t, x, a: self.avisos.append((t, x)))
        agora = time.time()
        vista = {"perfil": "", "lido": agora, "faixas": {"five_hour": {"usado": 91, "zera": agora + 3600}}}
        self._coletor = unittest.mock.patch.object(mod_bridge.mod_coletor, "ler_todas", return_value={"s1": vista})
        self._coletor.start()

    def tearDown(self) -> None:
        self._coletor.stop()
        self.ponte.shutdown()
        self._dados.stop()
        super().tearDown()

    def test_avisa_pela_bandeja_uma_vez_e_lembra_depois_de_reabrir(self):
        self.ponte._vigiar_limites()
        self.assertEqual(len(self.avisos), 1)
        self.assertIn("Principal, sessão de 5 horas em 91%", self.avisos[0][1])
        self.ponte._vigiar_limites()
        self.assertEqual(len(self.avisos), 1)
        # a tela que nasce depois de uma queda le o que ja foi avisado
        outra = mod_bridge.Bridge()
        outra.notificacaoPedida.connect(lambda t, x, a: self.avisos.append((t, x)))
        outra._vigiar_limites()
        outra.shutdown()
        self.assertEqual(len(self.avisos), 1)

    def test_desligado_nas_configuracoes_nao_avisa(self):
        config.salvar({"avisoDeLimite": False})
        self.ponte._vigiar_limites()
        self.assertEqual(self.avisos, [])


class GravacaoDoQueJaAvisou(ComPastaPropria):
    def test_ida_e_volta_e_arquivo_estragado(self):
        aviso.gravar_vistos({"x": {"zera": 1.0, "degrau": 85}})
        self.assertEqual(aviso.ler_vistos(), {"x": {"zera": 1.0, "degrau": 85}})
        from loom import caminhos

        (caminhos.RAIZ / aviso.ARQUIVO).write_text("{quebrado", encoding="utf-8")
        self.assertEqual(aviso.ler_vistos(), {})
        (caminhos.RAIZ / aviso.ARQUIVO).write_text(json.dumps([1, 2]), encoding="utf-8")
        self.assertEqual(aviso.ler_vistos(), {})


if __name__ == "__main__":
    unittest.main()
