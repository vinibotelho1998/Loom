"""Motor de desenho: o que o run.py escreve no ambiente antes de o Qt subir.

O ambiente e um dicionario comum, nunca o do processo; so a ponte le o de verdade, e ai ele entra
trocado por um dicionario do teste.
"""

from __future__ import annotations

import ast
import json
import os
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from base import ComPastaPropria  # noqa: E402

from loom import config, motor, queda  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DIA = 86400.0
FLAGS_ANTIGAS = (
    "--enable-gpu-rasterization",
    "--enable-zero-copy",
    "--ignore-gpu-blocklist",
    "--disable-frame-rate-limit",
    "--disable-gpu-vsync",
)


class ComMotorLimpo(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        motor.decisao.clear()

    def tearDown(self) -> None:
        motor.decisao.clear()
        super().tearDown()

    def preparar(self, desenho: str | None = "auto", **ambiente) -> tuple[dict, dict]:
        ajustes = {} if desenho is None else {"desenho": desenho}
        with self.assertLogs("loom.motor", "INFO"):
            decidido = motor.preparar_motor(ambiente, ajustes)
        return ambiente, decidido


class EscolhaDoMotor(ComMotorLimpo):
    def test_gpu_sobe_sem_flag_nenhuma(self):
        """As cinco flags de antes so abriam caminho pro ANGLE cair, e ele cai com o Loom inteiro."""
        for desenho in ("gpu", "auto", None):
            ambiente, decidido = self.preparar(desenho)
            self.assertEqual(ambiente, {}, desenho)
            self.assertEqual(decidido["modo"], "gpu")
            self.assertFalse(decidido["porQueda"])

    def test_software_tira_a_placa_do_chromium_e_do_qt_quick(self):
        ambiente, decidido = self.preparar("software")
        self.assertEqual(
            ambiente, {"QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu", "QT_QUICK_BACKEND": "software"}
        )
        self.assertEqual(decidido["modo"], "software")
        self.assertFalse(decidido["porQueda"], "foi escolha da pessoa, nao queda")

    def test_automatico_depois_de_queda_sobe_em_software_por_sete_dias(self):
        queda.registrar("tela", time.time() - 2 * DIA)
        ambiente, decidido = self.preparar("auto")
        self.assertEqual(ambiente["QTWEBENGINE_CHROMIUM_FLAGS"], "--disable-gpu")
        self.assertTrue(decidido["porQueda"])
        self.assertEqual(decidido["queda"]["tipo"], "tela")

    def test_queda_de_mais_de_sete_dias_ja_nao_conta(self):
        queda.registrar("tela", time.time() - 8 * DIA)
        ambiente, decidido = self.preparar("auto")
        self.assertEqual(ambiente, {})
        self.assertFalse(decidido["porQueda"])

    def test_marca_deixada_pelo_loom_que_caiu_ja_vale_nesta_subida(self):
        """A queda e descoberta e usada na mesma subida, antes do Qt existir."""
        marca = {"pid": 4242, "criado": 1, "boot": queda.boot_atual(), "desde": "2026-09-27T20:58:00-03:00"}
        queda.arquivo_da_marca().parent.mkdir(parents=True, exist_ok=True)
        queda.arquivo_da_marca().write_text(json.dumps(marca), encoding="utf-8")
        with unittest.mock.patch.object(queda, "criado_em", return_value=None), self.assertLogs("loom.queda", "WARNING"):
            ambiente, decidido = self.preparar("auto")
        self.assertEqual(decidido["modo"], "software")
        self.assertTrue(decidido["porQueda"])

    def test_placa_escolhida_na_mao_ignora_a_queda(self):
        queda.registrar("tela")
        ambiente, decidido = self.preparar("gpu")
        self.assertEqual(ambiente, {})
        self.assertFalse(decidido["porQueda"])
        self.assertEqual(motor.situacao(ambiente, {"desenho": "gpu"})["proxima"], "gpu")

    def test_variavel_definida_por_fora_continua_mandando(self):
        ambiente, decidido = self.preparar("software", QTWEBENGINE_CHROMIUM_FLAGS="--enable-logging")
        self.assertEqual(ambiente["QTWEBENGINE_CHROMIUM_FLAGS"], "--enable-logging")
        self.assertEqual(ambiente["QT_QUICK_BACKEND"], "software")
        self.assertEqual(decidido["modo"], "gpu")
        self.assertTrue(decidido["porFora"])

        ambiente, decidido = self.preparar("gpu", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --x")
        self.assertEqual(ambiente, {"QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu --x"})
        self.assertEqual(decidido["modo"], "software")
        self.assertTrue(decidido["porFora"])

    def test_ajuste_fora_da_lista_vale_automatico(self):
        queda.registrar("tela")
        ambiente, decidido = self.preparar("turbo")
        self.assertEqual(decidido["escolha"], "auto")
        self.assertEqual(decidido["modo"], "software")


class AjusteDeDesenho(ComPastaPropria):
    def test_padrao_e_automatico(self):
        self.assertEqual(config.carregar()["desenho"], "auto")

    def test_so_aceita_as_tres_opcoes(self):
        self.assertEqual(config.salvar({"desenho": "turbo"})["desenho"], "auto")
        self.assertEqual(config.salvar({"desenho": "software"})["desenho"], "software")
        self.assertEqual(config.salvar({"desenho": 1})["desenho"], "software")
        config.ARQUIVO.write_text(json.dumps({"desenho": "GPU"}), encoding="utf-8")
        self.assertEqual(config.carregar()["desenho"], "auto", "o arquivo editado a mao tambem passa pela lista")

    def test_a_tela_oferece_as_mesmas_opcoes_que_o_ajuste_aceita(self):
        """Opcao na tela que o ajuste recusa voltava sozinha, e a pessoa nao entendia por que."""
        import re

        html = (RAIZ / "ui" / "index.html").read_text(encoding="utf-8")
        campo = re.search(r'<select id="cfg-desenho"[^>]*>(.*?)</select>', html, re.S).group(1)
        self.assertEqual(tuple(re.findall(r'<option value="([^"]+)"', campo)), config.ESCOLHAS["desenho"])
        self.assertIn('["desenho", "select"]', (RAIZ / "ui" / "app.js").read_text(encoding="utf-8"))


class SituacaoNaPonte(ComMotorLimpo):
    """O que a tela de Configuracoes recebe pela ponte."""

    def setUp(self) -> None:
        super().setUp()
        from test_tela import garantir_app

        from loom import bridge as mod_bridge

        garantir_app()
        self.ponte = mod_bridge.Bridge()
        self.ponte._relistar.stop()
        self.ponte._flush_timer.stop()
        # a ponte le o ambiente do processo; ele volta como era no fim de cada teste
        self._ambiente = unittest.mock.patch.dict(os.environ)
        self._ambiente.start()
        for chave in motor.SOFTWARE:
            os.environ.pop(chave, None)

    def tearDown(self) -> None:
        self._ambiente.stop()
        self.ponte.shutdown()
        super().tearDown()

    def situacao(self) -> dict:
        return json.loads(self.ponte.desenho())

    def subir(self) -> None:
        with self.assertLogs("loom.motor", "INFO"):
            motor.preparar_motor(os.environ, config.carregar())

    def test_diz_o_motor_em_uso_e_que_foi_por_queda(self):
        queda.registrar("tela", time.time() - DIA)
        self.subir()
        s = self.situacao()
        self.assertEqual((s["escolha"], s["emUso"], s["proxima"]), ("auto", "software", "software"))
        self.assertTrue(s["porQueda"])
        self.assertRegex(s["quedaEm"], r"^\d\d/\d\d às \d\d:\d\d$")
        self.assertRegex(s["ateEm"], r"^\d\d/\d\d$")

    def test_voltar_pra_placa_vale_na_proxima_subida(self):
        queda.registrar("tela", time.time() - DIA)
        self.subir()
        s = json.loads(self.ponte.voltarParaGpu())
        self.assertEqual((s["emUso"], s["proxima"]), ("software", "gpu"), "o motor desta subida nao troca")
        self.assertTrue(s["porQueda"])
        self.assertTrue(s["quedaEm"], "o aviso continua dizendo por que esta sem placa")

    def test_placa_que_perde_o_contexto_na_pagina_vira_queda_pro_motor(self):
        self.subir()
        with self.assertLogs("loom.bridge", "WARNING"):
            self.ponte.gpuPerdida()
        self.assertEqual(queda.recente()["tipo"], "contexto")
        s = self.situacao()
        self.assertEqual((s["emUso"], s["proxima"], s["porQueda"]), ("gpu", "software", False))

    def test_trocar_o_ajuste_mostra_o_da_proxima_subida(self):
        self.subir()
        config.salvar({"desenho": "software"})
        s = self.situacao()
        self.assertEqual((s["escolha"], s["emUso"], s["proxima"]), ("software", "gpu", "software"))


class EntradaDoApp(unittest.TestCase):
    """O run.py escolhe o motor antes de qualquer coisa do Qt ser importada."""

    LEVES = {"loom", "loom.caminhos", "loom.config", "loom.motor", "loom.queda", "loom.registro"}

    @staticmethod
    def imports_de_topo(caminho: Path, pacote: str) -> list[str]:
        """Os modulos importados no nivel do arquivo; os de dentro de funcao so rodam depois."""
        nomes = []
        for no in ast.parse(caminho.read_text(encoding="utf-8")).body:
            if isinstance(no, ast.Import):
                nomes += [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom):
                base = no.module or ""
                if no.level:
                    base = pacote + ("." + base if base else "")
                    nomes += [f"{base}.{a.name}" if not no.module else base for a in no.names]
                else:
                    nomes.append(base)
        return nomes

    def test_run_prepara_o_motor_antes_de_importar_o_app(self):
        corpo = ast.parse((RAIZ / "run.py").read_text(encoding="utf-8")).body
        linhas = [ast.unparse(no) for no in corpo]
        preparo = next(i for i, texto in enumerate(linhas) if "preparar_motor(os.environ" in texto)
        app = next(i for i, texto in enumerate(linhas) if texto.startswith("from loom.app import"))
        self.assertLess(preparo, app)
        self.assertLess(next(i for i, t in enumerate(linhas) if "configurar()" in t), preparo)

    def test_nada_do_que_roda_antes_do_motor_importa_o_qt(self):
        """Um import do Qt no caminho do motor e as variaveis chegariam tarde demais."""
        fila, vistos = ["loom.config", "loom.motor", "loom.registro"], set()
        while fila:
            modulo = fila.pop()
            if modulo in vistos:
                continue
            vistos.add(modulo)
            arquivo = RAIZ / (modulo.replace(".", "/") + (".py" if modulo != "loom" else "/__init__.py"))
            for nome in self.imports_de_topo(arquivo, "loom"):
                self.assertFalse(nome.startswith(("PySide6", "shiboken6", "winpty")), f"{modulo} importa {nome}")
                if nome.startswith("loom"):
                    fila.append(nome)
        self.assertEqual(vistos - self.LEVES, set())

    def test_nenhuma_das_flags_antigas_sobrou(self):
        for nome in ("run.py", "loom/motor.py"):
            texto = (RAIZ / nome).read_text(encoding="utf-8")
            for flag in FLAGS_ANTIGAS:
                self.assertNotIn(flag, texto, nome)
        self.assertNotIn("teto que a pessoa escolhe", (RAIZ / "run.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
