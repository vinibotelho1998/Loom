"""O monitor de recursos: o que ele le do kernel, com um /proc e um /sys de mentira, e o que ele escreve."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_pagina import ComPagina  # noqa: E402
from test_usabilidade import ComPonte  # noqa: E402

from PySide6.QtCore import QCoreApplication  # noqa: E402

from loom import config, recursos  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "ui"
GB = 1024**3


class MaquinaDeMentira:
    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz

    def escrever(self, caminho: str, texto) -> None:
        alvo = self.raiz / caminho
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(str(texto), encoding="ascii")

    def cpu(self, total_ocupado: int, ocioso: int, espera: int = 0) -> None:
        self.escrever("proc/stat", f"cpu  {total_ocupado} 0 0 {ocioso} {espera} 0 0 0 0 0\ncpu0 1 1 1 1 1 1 1 1 0 0\nintr 1 2\n")

    def memoria(self, total_kb: int, disponivel_kb: int) -> None:
        self.escrever("proc/meminfo", f"MemTotal:  {total_kb} kB\nMemFree:  1 kB\nMemAvailable:  {disponivel_kb} kB\n")

    def sensor_cpu(self, numero: int = 3, nome: str = "k10temp", milesimos: int = 48750, rotulo: str = "Tctl") -> None:
        self.escrever(f"sys/class/hwmon/hwmon{numero}/name", nome)
        self.escrever(f"sys/class/hwmon/hwmon{numero}/temp1_input", milesimos)
        self.escrever(f"sys/class/hwmon/hwmon{numero}/temp1_label", rotulo)

    def placa(self, cartao: str = "card1", vram: int = 16 * GB, usada: int = GB, busy: int = 7, hwmon: str = "hwmon2", estado: str = "active") -> None:
        base = f"sys/class/drm/{cartao}/device"
        self.escrever(f"{base}/mem_info_vram_total", vram)
        self.escrever(f"{base}/mem_info_vram_used", usada)
        self.escrever(f"{base}/gpu_busy_percent", busy)
        self.escrever(f"{base}/power/runtime_status", estado)
        for n, rotulo, milesimos in ((1, "edge", 39000), (2, "junction", 41000), (3, "mem", 60000)):
            self.escrever(f"{base}/hwmon/{hwmon}/temp{n}_input", milesimos)
            self.escrever(f"{base}/hwmon/{hwmon}/temp{n}_label", rotulo)


class ComMaquina(unittest.TestCase):
    def setUp(self) -> None:
        self.raiz = Path(tempfile.mkdtemp(prefix="loom-recursos-"))
        self.addCleanup(shutil.rmtree, self.raiz, True)
        self.maquina = MaquinaDeMentira(self.raiz)

    def medidor(self) -> recursos.Medidor:
        return recursos.Medidor(self.raiz)


class LeituraDaCpu(ComMaquina):
    def test_uso_e_a_diferenca_entre_duas_leituras(self):
        self.maquina.cpu(5000, 1000)
        medidor = self.medidor()
        # 100 ocupados e 300 ociosos nesse intervalo: 25%, e nao os 77% da media desde o boot
        self.maquina.cpu(5100, 1300)
        self.assertEqual(medidor.ler()["cpu"]["uso"], 25.0)

    def test_espera_de_disco_conta_como_ocioso(self):
        self.maquina.cpu(1000, 3000, espera=500)
        medidor = self.medidor()
        self.maquina.cpu(1100, 3200, espera=700)
        self.assertEqual(medidor.ler()["cpu"]["uso"], 20.0)

    def test_sem_tempo_passado_nao_inventa_numero(self):
        self.maquina.cpu(1000, 3000)
        medidor = self.medidor()
        self.assertNotIn("cpu", medidor.ler())

    def test_temperatura_em_graus_pelo_sensor_do_processador(self):
        self.maquina.cpu(1000, 3000)
        # o sensor da placa-mae vem antes na lista e nao e o do processador
        self.maquina.sensor_cpu(numero=1, nome="gigabyte_wmi", milesimos=35000, rotulo="")
        self.maquina.sensor_cpu(numero=3)
        self.assertEqual(self.medidor().ler()["cpu"]["temp"], 48.8)

    def test_sensor_com_varias_linhas_usa_a_do_processador_inteiro(self):
        self.maquina.cpu(1000, 3000)
        self.maquina.sensor_cpu(numero=3, milesimos=37000, rotulo="Tccd1")
        self.maquina.escrever("sys/class/hwmon/hwmon3/temp3_input", 52000)
        self.maquina.escrever("sys/class/hwmon/hwmon3/temp3_label", "Tctl")
        self.assertEqual(self.medidor().ler()["cpu"]["temp"], 52.0)


class LeituraDaMemoria(ComMaquina):
    def test_usado_desconta_o_que_ainda_esta_disponivel(self):
        """A cache do disco fica disponivel: contar o livre diria que a maquina esta sempre cheia."""
        self.maquina.memoria(32 * 1024 * 1024, 24 * 1024 * 1024)
        self.assertEqual(self.medidor().ler()["ram"], {"usado": 8 * GB, "total": 32 * GB})

    def test_arquivo_estranho_nao_derruba(self):
        self.maquina.escrever("proc/meminfo", "MemTotal: lixo kB\n")
        self.assertNotIn("ram", self.medidor().ler())


class LeituraDaPlaca(ComMaquina):
    def test_uso_memoria_e_as_tres_temperaturas(self):
        self.maquina.placa()
        dados = self.medidor().ler()
        self.assertEqual(dados["gpu"], {"uso": 7, "temp": 39.0, "juncao": 41.0})
        self.assertEqual(dados["vram"], {"usado": GB, "total": 16 * GB, "temp": 60.0})

    def test_com_duas_placas_vale_a_de_mais_memoria(self):
        for integrada, dedicada in (("card0", "card1"), ("card1", "card0")):
            with self.subTest(integrada=integrada):
                shutil.rmtree(self.raiz / "sys", ignore_errors=True)
                self.maquina.placa(integrada, vram=GB // 2, busy=99)
                self.maquina.placa(dedicada, vram=16 * GB, busy=7)
                dados = self.medidor().ler()
                self.assertEqual(dados["gpu"]["uso"], 7, "o video do processador nao e a placa")
                self.assertEqual(dados["vram"]["total"], 16 * GB)

    def test_saida_de_video_nao_conta_como_placa(self):
        self.maquina.placa("card1")
        self.maquina.escrever("sys/class/drm/card1-DP-1/device/mem_info_vram_total", 99 * GB)
        self.assertEqual(self.medidor().ler()["vram"]["total"], 16 * GB)

    def test_placa_dormindo_nao_e_acordada(self):
        """Em notebook, perguntar o uso ou a temperatura acorda a placa e gasta bateria."""
        self.maquina.placa(estado="suspended")
        dados = self.medidor().ler()
        self.assertEqual(dados["gpu"], {"dormindo": True})
        self.assertNotIn("temp", dados["vram"])

    def test_placa_sem_os_arquivos_do_driver_fica_de_fora(self):
        self.maquina.escrever("sys/class/drm/card0/device/vendor", "0x10de")
        dados = self.medidor().ler()
        self.assertNotIn("gpu", dados)
        self.assertNotIn("vram", dados)

    def test_sensor_que_mudou_de_numero_e_achado_de_novo(self):
        """A placa pode voltar de um reset com outro hwmon, e o monitor nao pode ficar cego ate fechar."""
        self.maquina.placa(hwmon="hwmon2")
        medidor = self.medidor()
        self.assertEqual(medidor.ler()["gpu"]["temp"], 39.0)
        shutil.rmtree(self.raiz / "sys/class/drm/card1/device/hwmon/hwmon2")
        self.maquina.placa(hwmon="hwmon9")
        self.assertIsNone(medidor.ler()["gpu"]["temp"])
        for _ in range(recursos.LEITURAS_PRA_PROCURAR_DE_NOVO):
            dados = medidor.ler()
        self.assertEqual(dados["gpu"]["temp"], 39.0)

    def test_com_tudo_achado_nao_varre_o_sys_a_cada_leitura(self):
        """Varrer custaria mais que ler os numeros, e isto roda a cada 2 s."""
        self.maquina.cpu(1000, 3000)
        self.maquina.sensor_cpu()
        self.maquina.placa()
        medidor = self.medidor()
        with unittest.mock.patch.object(recursos.Medidor, "_procurar") as procurar:
            for _ in range(recursos.LEITURAS_PRA_PROCURAR_DE_NOVO * 2):
                medidor.ler()
        procurar.assert_not_called()


class MaquinaSemNada(ComMaquina):
    def test_sem_proc_nem_sys_volta_vazio(self):
        """E o que acontece fora do Linux: a barra nao mostra nada, e nada quebra."""
        self.assertEqual(self.medidor().ler(), {})

    def test_config_liga_por_padrao_e_aceita_desligar(self):
        self.assertIs(config.PADRAO["mostrarRecursos"], True)


class PonteDosRecursos(ComPonte):
    def esperar_resposta(self) -> list:
        respostas: list[str] = []
        self.ponte.recursosProntos.connect(respostas.append)
        return respostas

    def rodar_ate(self, condicao, prazo: float = 3.0) -> bool:
        fim = time.monotonic() + prazo
        while time.monotonic() < fim and not condicao():
            QCoreApplication.processEvents()
            time.sleep(0.005)
        return condicao()

    def test_le_fora_da_thread_da_tela_e_responde_por_sinal(self):
        respostas = self.esperar_resposta()
        falso = unittest.mock.Mock()
        falso.ler.return_value = {"ram": {"usado": 1, "total": 2}}
        self.ponte._medidor = falso
        self.ponte.pedirRecursos()
        self.assertTrue(self.rodar_ate(lambda: respostas))
        self.assertEqual(json.loads(respostas[0]), {"ram": {"usado": 1, "total": 2}})

    def test_pedido_com_leitura_andando_nao_abre_outra_thread(self):
        """Driver lento com a placa em carga nao pode acumular uma thread a cada 2 s."""
        falso = unittest.mock.Mock()
        falso.ler.side_effect = lambda: time.sleep(0.2) or {}
        self.ponte._medidor = falso
        respostas = self.esperar_resposta()
        for _ in range(5):
            self.ponte.pedirRecursos()
        self.assertTrue(self.rodar_ate(lambda: respostas))
        self.assertEqual(falso.ler.call_count, 1)
        self.ponte.pedirRecursos()
        self.assertTrue(self.rodar_ate(lambda: len(respostas) == 2), "depois da resposta, a proxima leitura vale")

    def test_sensor_que_quebra_responde_vazio_em_vez_de_calar(self):
        falso = unittest.mock.Mock()
        falso.ler.side_effect = ValueError("sensor estranho")
        self.ponte._medidor = falso
        respostas = self.esperar_resposta()
        with self.assertLogs("loom.bridge", "WARNING"):
            self.ponte.pedirRecursos()
            self.assertTrue(self.rodar_ate(lambda: respostas))
        self.assertEqual(respostas, ["{}"])

    def test_leitura_presa_nao_trava_o_monitor_pra_sempre(self):
        preso = unittest.mock.Mock()
        self.ponte._medidor = preso
        self.ponte._lendo_recursos_desde = time.monotonic() - 3600
        respostas = self.esperar_resposta()
        with unittest.mock.patch("loom.bridge.mod_recursos.Medidor") as classe:
            classe.return_value.ler.return_value = {}
            self.ponte.pedirRecursos()
            self.assertTrue(self.rodar_ate(lambda: respostas))
        classe.assert_called_once()
        preso.ler.assert_not_called()


class TextoDosRecursos(ComPagina):
    def itens(self, dados: dict) -> dict:
        return self.rodar("itensDosRecursos(" + json.dumps(dados) + ")")

    def test_o_que_cada_item_escreve(self):
        itens = self.itens({
            "cpu": {"uso": 12.4, "temp": 48.8},
            "ram": {"usado": 9 * GB, "total": 31 * GB},
            "gpu": {"uso": 7, "temp": 39.0, "juncao": 41.0},
            "vram": {"usado": GB, "total": 16 * GB, "temp": 60.0},
        })
        self.assertEqual((itens["cpu"]["valor"], itens["cpu"]["temp"]), ("12%", "49°"))
        self.assertEqual((itens["ram"]["valor"], itens["ram"]["temp"]), ("9,0/31 GB", ""))
        self.assertEqual((itens["gpu"]["valor"], itens["gpu"]["temp"]), ("7%", "39°"))
        self.assertEqual((itens["vram"]["valor"], itens["vram"]["temp"]), ("1,0/16 GB", "60°"))
        self.assertEqual(itens["gpu"]["dica"], "Placa de vídeo: 7% em uso, borda 39 °C, junção 41 °C")
        # a barra de cada item mede o mesmo que o numero ao lado
        self.assertEqual(itens["cpu"]["parte"], 12.4)
        self.assertEqual(itens["gpu"]["parte"], 7)
        self.assertAlmostEqual(itens["ram"]["parte"], 100 * 9 / 31)
        self.assertAlmostEqual(itens["vram"]["parte"], 100 / 16)
        self.assertEqual(itens["vram"]["dica"], "Memória de vídeo: 1,0 de 16,0 GB em uso, 60 °C")

    def test_so_o_que_pede_atencao_ganha_cor(self):
        calmo = self.itens({"cpu": {"uso": 50, "temp": 60}})["cpu"]
        self.assertEqual((calmo["nivel"], calmo["calor"]), ("", ""))
        alto = self.itens({"cpu": {"uso": 90, "temp": 88}})["cpu"]
        self.assertEqual((alto["nivel"], alto["calor"]), ("alto", "alto"))
        limite = self.itens({"cpu": {"uso": 100, "temp": 96}})["cpu"]
        self.assertEqual((limite["nivel"], limite["calor"]), ("critico", "critico"))

    def test_memoria_quase_cheia_chama_atencao_pela_fracao(self):
        self.assertEqual(self.itens({"ram": {"usado": 30 * GB, "total": 31 * GB}})["ram"]["nivel"], "critico")
        self.assertEqual(self.itens({"ram": {"usado": 8 * GB, "total": 31 * GB}})["ram"]["nivel"], "")

    def test_placa_quente_olha_a_juncao_tambem(self):
        """A borda pode estar fria com a juncao ja cortando: e ela que limita a placa."""
        self.assertEqual(self.itens({"gpu": {"uso": 50, "temp": 70, "juncao": 101}})["gpu"]["calor"], "critico")

    def test_primeira_leitura_sem_uso_mostra_traco_e_nao_zero(self):
        self.assertEqual(self.itens({"cpu": {"uso": None, "temp": 50}})["cpu"]["valor"], "--")

    def test_placa_dormindo_diz_que_esta_dormindo(self):
        item = self.itens({"gpu": {"dormindo": True}})["gpu"]
        self.assertEqual((item["valor"], item["temp"]), ("off", ""))
        self.assertEqual(item["dica"], "Placa de vídeo dormindo")

    def test_sem_leitura_nao_vem_item(self):
        self.assertEqual(self.itens({}), {})


class MonitorNaPagina(unittest.TestCase):
    def test_o_monitor_fica_na_barra_de_cima(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        cabecalho = html[html.index('<header class="cromo">'):html.index("</header>")]
        for elemento in ("recursos", "rec-cpu", "rec-ram", "rec-gpu", "rec-vram"):
            self.assertIn(f'id="{elemento}"', cabecalho)
        self.assertIn('id="recursos" hidden', cabecalho, "sem leitura, como no Windows, ele nao aparece")

    def test_so_pede_com_a_janela_a_vista_e_com_o_monitor_ligado(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        funcao = app[app.index("function pedirRecursos()"):app.index("function ligarRecursos()")]
        self.assertIn("document.hidden", funcao)
        self.assertIn("cfg.mostrarRecursos === false", funcao)

    def medir(self, largura: int) -> dict:
        from test_layout import medir_varios

        return medir_varios(
            [
                """(function () {
                  document.body.classList.add("janela-propria");
                  document.getElementById("janela-botoes").hidden = false;
                  const caixa = document.getElementById("recursos");
                  caixa.hidden = false;
                  const textos = { cpu: ["100%", "100°"], ram: ["31,0/32 GB", ""], gpu: ["100%", "110°"], vram: ["16,0/16 GB", "108°"] };
                  for (const nome in textos) {
                    const item = document.getElementById("rec-" + nome);
                    item.hidden = false;
                    item.querySelector("b").textContent = textos[nome][0];
                    const em = item.querySelector("em");
                    if (em) em.textContent = textos[nome][1];
                  }
                  const fim = caixa.getBoundingClientRect();
                  const uso = document.getElementById("alternar-uso").getBoundingClientRect();
                  const fechar = document.querySelector('[data-janela="fechar"]').getBoundingClientRect();
                  const cromo = document.querySelector(".cromo").getBoundingClientRect();
                  return JSON.stringify({ visivel: getComputedStyle(caixa).display !== "none", direita: fim.right, largura: fim.width,
                    uso: uso.left, fechar: fechar.right, janela: innerWidth, altura: cromo.height,
                    barra: getComputedStyle(document.querySelector("#rec-cpu .recurso-trilho")).display !== "none",
                    corta: caixa.scrollWidth - caixa.clientWidth });
                })()"""
            ],
            largura=largura,
            altura=600,
        )[0]

    def test_com_os_maiores_numeros_cabe_inteiro_e_nao_encosta_nos_botoes(self):
        # 1000 sem a barrinha de nivel; 1300, o menor tamanho com ela
        for largura, com_barra in ((1000, False), (1300, True)):
            with self.subTest(largura=largura):
                medida = self.medir(largura)
                self.assertTrue(medida["visivel"])
                self.assertEqual(medida["barra"], com_barra)
                self.assertLessEqual(medida["corta"], 0, "o monitor saiu cortado")
                self.assertLessEqual(medida["direita"], medida["uso"], "o monitor passou por cima do botao de uso")
                self.assertLessEqual(medida["fechar"], medida["janela"], "o fechar saiu da janela")
                self.assertEqual(medida["altura"], 44, "a barra quebrou de linha")

    def test_janela_estreita_tira_o_monitor_antes_de_empurrar_os_botoes(self):
        medida = self.medir(800)
        self.assertFalse(medida["visivel"])
        self.assertLessEqual(medida["fechar"], medida["janela"])

    def test_a_barra_de_nivel_nao_anima_a_cada_leitura(self):
        """Tem que ser leve: uma transicao aqui repintaria a barra de cima por 300 ms a cada 2 s."""
        css = (UI / "style.css").read_text(encoding="utf-8")
        monitor = css[css.index("monitor de recursos */"):]
        self.assertNotIn("transition:", monitor)
        self.assertNotIn("animation:", monitor)

    def test_nao_usa_programa_externo_nem_dependencia(self):
        """O monitor tem que ser leve: nada de subir nvidia-smi ou sensors a cada 2 s."""
        fonte = (Path(recursos.__file__)).read_text(encoding="utf-8")
        for proibido in ("subprocess", "psutil", "os.system", "Popen"):
            self.assertNotIn(proibido, fonte)


if __name__ == "__main__":
    unittest.main()
