"""A lista de estados das abas nunca para de andar.

Os estados dos cartoes paravam e so voltavam fechando e abrindo o Loom. Uma resposta perdida da
varredura deixava a tela esperando pra sempre, e o relogio de seguranca so era refeito quando a
lista chegava.
"""

from __future__ import annotations

import json
import time
import unittest.mock

from test_pagina import ComPagina
from test_ponte import ComPonteNaPastaTemporaria, esperar_a_ponte

from loom import bridge as mod_bridge


class PonteNaoTrava(ComPonteNaPastaTemporaria):
    def test_erro_ao_preparar_a_varredura_ainda_responde_com_a_ultima_lista(self):
        chegou = []
        self.ponte.sessoesProntas.connect(chegou.append)
        self.ponte._ultima_lista = '[{"sessao": "s1"}]'
        with unittest.mock.patch.object(mod_bridge.mod_perfis, "listar", side_effect=ValueError("perfis.json pela metade")):
            self.ponte.pedirSessoes()
        self.assertEqual(chegou, ['[{"sessao": "s1"}]'])
        self.assertFalse(self.ponte._varrendo, "sem varredura andando, a proxima tem que poder subir")

    def test_erro_na_varredura_devolve_a_ultima_lista_e_nao_a_vazia(self):
        """A lista vazia apagava o estado de todos os cartoes ate a proxima passada."""
        chegou = []
        self.ponte.sessoesProntas.connect(chegou.append)
        self.ponte._ultima_lista = '[{"sessao": "s1"}]'
        with (
            unittest.mock.patch.object(mod_bridge.mod_uso, "pastas_de", return_value={}),
            unittest.mock.patch.object(mod_bridge.mod_sessoes, "listar", side_effect=RuntimeError("cauda ilegivel")),
        ):
            self.ponte.pedirSessoes()
            esperar_a_ponte(self.ponte, chegou)
        self.assertEqual(chegou, ['[{"sessao": "s1"}]'])

    def test_varredura_presa_nao_segura_as_proximas_pra_sempre(self):
        self.ponte._varrendo = True
        self.ponte._varrendo_desde = time.monotonic() - mod_bridge.VARREDURA_PRESA - 1
        chegou = []
        self.ponte.sessoesProntas.connect(chegou.append)
        with (
            unittest.mock.patch.object(mod_bridge.mod_uso, "pastas_de", return_value={}),
            unittest.mock.patch.object(mod_bridge.mod_sessoes, "listar", return_value=[{"sessao": "nova"}]),
        ):
            self.ponte.pedirSessoes()
            esperar_a_ponte(self.ponte, chegou)
        self.assertEqual(json.loads(chegou[0]), [{"sessao": "nova"}])

    def test_varredura_recente_continua_valendo_pro_pedido_que_chega_junto(self):
        self.ponte._varrendo = True
        self.ponte._varrendo_desde = time.monotonic()
        with unittest.mock.patch.object(mod_bridge.mod_sessoes, "listar") as listar:
            self.ponte.pedirSessoes()
        listar.assert_not_called()


class TelaNaoTrava(ComPagina):
    def preparar(self) -> None:
        self.definir(
            "var pedidos = 0; estado.bridge = {"
            " abasComPrograma(cb) { pedidos++; },"
            " pedirSessoes() {} };"
        )

    def test_resposta_perdida_nao_trava_a_lista_pra_sempre(self):
        self.preparar()
        self.definir("estado.varrendo = true; estado.varrendoDesde = Date.now() - 20000;")
        self.definir("atualizarSessoes()")
        self.assertEqual(self.rodar("pedidos"), 1, "passado o prazo, a varredura sobe de novo")

    def test_pedido_no_meio_de_uma_varredura_recente_espera_por_ela(self):
        self.preparar()
        self.definir("estado.varrendo = true; estado.varrendoDesde = Date.now();")
        self.definir("atualizarSessoes()")
        self.assertEqual(self.rodar("pedidos"), 0)

    def test_cartao_que_quebra_ao_pintar_nao_leva_o_relogio_junto(self):
        # o navegador tem console; o motor do teste nao
        self.definir("var console = { error: function () {}, warn: function () {}, log: function () {} };")
        resultado = self.motor.evaluate(
            "(function(){ const antes = montarSessoes; let ok = true;"
            " montarSessoes = function () { throw new Error('cartao quebrado'); };"
            " try { receberSessoes(JSON.stringify([{sessao: 'quebra', idade: 3}])); }"
            " catch (e) { ok = false; } finally { montarSessoes = antes; }"
            " return ok && !estado.varrendo; })()"
        )
        self.assertFalse(resultado.isError(), resultado.toString())
        self.assertTrue(resultado.toBool(), "receberSessoes nao pode deixar a excecao escapar")

if __name__ == "__main__":
    unittest.main()
