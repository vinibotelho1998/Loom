"""O gasto de cada aba: o que ela le de um /proc de mentira, e o que a lateral e o chapeu escrevem."""

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

from loom import recursos_abas  # noqa: E402
from loom.recursos_abas import MedidorDeAbas  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "ui"
GB = 1024**3
MB = 1024**2
PLACA = "/dev/dri/renderD128"


class ProcDeMentira:
    """Cada processo com o stat, o ambiente e os descritores que o kernel mostraria."""

    def __init__(self, raiz: Path) -> None:
        self.proc = raiz / "proc"
        self.proc.mkdir(parents=True)

    def processo(self, pid, pai, nome="bash", tiques=0, nasceu=100, paginas=256, estado="S", ambiente=None):
        pasta = self.proc / str(pid)
        if pasta.exists():
            shutil.rmtree(pasta)
        (pasta / "fd").mkdir(parents=True)
        (pasta / "fdinfo").mkdir()
        # depois do nome: estado, pai, 9 campos, utime, stime, 6 campos, quando nasceu, vsize, rss
        campos = [estado, str(pai)] + ["0"] * 9 + [str(tiques), "0"] + ["0"] * 6 + [str(nasceu), "0", str(paginas)]
        (pasta / "stat").write_text(f"{pid} ({nome}) " + " ".join(campos) + "\n", encoding="ascii")
        if ambiente is not None:
            corpo = b"".join(f"{chave}={valor}".encode() + b"\0" for chave, valor in ambiente.items())
            (pasta / "environ").write_bytes(corpo)

    def tiques(self, pid, tiques):
        stat = self.proc / str(pid) / "stat"
        texto = stat.read_text(encoding="ascii")
        inicio = texto.rindex(")") + 2
        campos = texto[inicio:].split()
        campos[11] = str(tiques)
        stat.write_text(texto[:inicio] + " ".join(campos) + "\n", encoding="ascii")

    def descritor(self, pid, numero, alvo=PLACA, cliente=None, trabalho=0, calculo=0, vram=None, campo="drm-resident-vram", unidade="KiB"):
        pasta = self.proc / str(pid)
        link = pasta / "fd" / str(numero)
        if link.is_symlink():
            link.unlink()
        os.symlink(alvo, link)
        if cliente is not None:
            linhas = ["drm-driver:\tamdgpu", f"drm-client-id:\t{cliente}", "drm-pdev:\t0000:03:00.0",
                      f"drm-engine-gfx:\t{trabalho} ns", f"drm-engine-compute:\t{calculo} ns", "drm-engine-dma:\t999999999 ns"]
            if vram is not None:
                linhas.append(f"{campo}:\t{vram} {unidade}".rstrip())
            (pasta / "fdinfo" / str(numero)).write_text("\n".join(linhas) + "\n", encoding="ascii")

    def sumir(self, pid):
        shutil.rmtree(self.proc / str(pid))


class ComProc(unittest.TestCase):
    def setUp(self) -> None:
        self.raiz = Path(tempfile.mkdtemp(prefix="loom-abas-"))
        self.addCleanup(shutil.rmtree, self.raiz, True)
        self.maquina = ProcDeMentira(self.raiz)
        self.agora = [1000.0]
        # 100 tiques por segundo, paginas de 4 KiB e 16 nucleos, como numa maquina de verdade
        self.medidor = MedidorDeAbas(self.raiz, relogio=lambda: self.agora[0], tique=100, pagina=4096, nucleos=16)

    def passar(self, segundos=2.0):
        self.agora[0] += segundos

    def ler(self, abas):
        return self.medidor.ler(abas)


class QuemEDaAba(ComProc):
    def montar(self):
        m = self.maquina
        m.processo(1, 0, "systemd")
        m.processo(2, 0, "kthreadd")
        m.processo(10, 1, "Loom")
        m.processo(100, 10, "bash", tiques=5, ambiente={"LOOM_ABA": "a1"})
        m.processo(101, 100, "claude", tiques=50, ambiente={"LOOM_ABA": "a1"})
        m.processo(102, 101, "node", tiques=10, ambiente={"LOOM_ABA": "a1"})
        m.processo(200, 10, "bash", tiques=7, ambiente={"LOOM_ABA": "a2"})
        # de outro lugar da maquina, sem marca: nao e de aba nenhuma
        m.processo(300, 1, "chrome", tiques=900, ambiente={"HOME": "/home/x"})

    def test_o_shell_e_tudo_que_nasceu_dele(self):
        self.montar()
        r = self.ler({"a1": 100, "a2": 200})
        self.assertEqual(r["a1"]["processos"], 3)
        self.assertEqual(r["a2"]["processos"], 1)
        nomes = {p["nome"] for p in r["a1"]["principais"]}
        self.assertEqual(nomes, {"bash", "claude", "node"})
        self.assertNotIn("chrome", nomes, "processo de fora da aba entrou na conta")

    def test_o_que_saiu_da_arvore_volta_pela_marca(self):
        """O Claude solta tarefa longa com setsid: o pai vira o systemd, e so a marca diz de quem ela e."""
        self.montar()
        self.maquina.processo(400, 1, "llama-server", tiques=100, ambiente={"PATH": "/usr/bin", "LOOM_ABA": "a1", "X": "1"})
        self.maquina.processo(401, 1, "python3", tiques=100, ambiente={"LOOM_ABA": "outra-que-fechou"})
        r = self.ler({"a1": 100, "a2": 200})
        nomes = {p["nome"] for p in r["a1"]["principais"]}
        self.assertIn("llama-server", nomes)
        self.assertNotIn("python3", nomes, "marca de aba que nao existe mais nao pode cair em outra")
        self.assertEqual(r["a1"]["processos"], 4)

    def test_quem_limpou_o_ambiente_continua_pela_arvore(self):
        self.montar()
        self.maquina.processo(103, 101, "sudo", tiques=1, ambiente={})
        r = self.ler({"a1": 100})
        self.assertIn("sudo", {p["nome"] for p in r["a1"]["principais"]})

    def test_aba_sem_shell_vivo_ou_sem_pid_nao_vem(self):
        self.montar()
        r = self.ler({"a1": 100, "morta": 999, "sem": None})
        self.assertEqual(set(r), {"a1"})

    def test_zumbi_e_linha_de_kernel_ficam_de_fora(self):
        self.montar()
        self.maquina.processo(104, 101, "defunto", tiques=40, estado="Z", ambiente={"LOOM_ABA": "a1"})
        self.maquina.processo(50, 2, "kworker", ambiente={"LOOM_ABA": "a1"})
        r = self.ler({"a1": 100})
        nomes = {p["nome"] for p in r["a1"]["principais"]}
        self.assertNotIn("defunto", nomes)
        self.assertNotIn("kworker", nomes)

    def test_ambiente_e_lido_uma_vez_por_processo(self):
        self.montar()
        original = MedidorDeAbas._marca
        with unittest.mock.patch.object(MedidorDeAbas, "_marca", autospec=True, side_effect=original) as marca:
            self.ler({"a1": 100})
            primeira = marca.call_count
            self.passar()
            self.ler({"a1": 100})
            self.assertEqual(marca.call_count, primeira, "o ambiente nao muda e foi lido de novo")
            self.maquina.processo(500, 1, "novo", ambiente={"LOOM_ABA": "a1"})
            self.passar()
            self.ler({"a1": 100})
            self.assertEqual(marca.call_count, primeira + 1)


class Cpu(ComProc):
    def test_primeira_leitura_nao_inventa_uso(self):
        self.maquina.processo(100, 1, "bash", tiques=500, ambiente={"LOOM_ABA": "a"})
        r = self.ler({"a": 100})["a"]
        self.assertIsNone(r["cpu"])
        self.assertIsNone(r["nucleos"])

    def test_uso_e_a_diferenca_dividida_pelo_tempo(self):
        self.maquina.processo(100, 1, "bash", tiques=500, ambiente={"LOOM_ABA": "a"})
        self.maquina.processo(101, 100, "make", tiques=1000)
        self.ler({"a": 100})
        self.passar(2.0)
        # 2 s depois: o make gastou 400 tiques, 4 s de CPU em 2 s, dois nucleos
        self.maquina.tiques(101, 1400)
        r = self.ler({"a": 100})["a"]
        self.assertAlmostEqual(r["nucleos"], 2.0)
        self.assertAlmostEqual(r["cpu"], 12.5, msg="dois de 16 nucleos")
        self.assertEqual(r["principais"][0]["nome"], "make")
        self.assertAlmostEqual(r["principais"][0]["nucleos"], 2.0)

    def test_quem_nasceu_no_intervalo_conta_tudo_que_gastou(self):
        self.maquina.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        self.ler({"a": 100})
        self.passar(2.0)
        self.maquina.processo(101, 100, "gcc", tiques=150)
        r = self.ler({"a": 100})["a"]
        self.assertAlmostEqual(r["nucleos"], 0.75)

    def test_pid_reaproveitado_nao_desconta_o_antigo(self):
        self.maquina.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        self.maquina.processo(101, 100, "velho", tiques=5000, nasceu=100)
        self.ler({"a": 100})
        self.passar(2.0)
        self.maquina.processo(101, 100, "novo", tiques=20, nasceu=900)
        r = self.ler({"a": 100})["a"]
        self.assertAlmostEqual(r["nucleos"], 0.1)

    def test_aba_inteira_nao_passa_de_toda_a_maquina(self):
        self.maquina.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        self.ler({"a": 100})
        self.passar(1.0)
        self.maquina.tiques(100, 100 * 40)
        self.assertEqual(self.ler({"a": 100})["a"]["cpu"], 100.0)


class Memoria(ComProc):
    def test_soma_o_que_cada_processo_tem_na_ram(self):
        self.maquina.processo(100, 1, "bash", paginas=1000, ambiente={"LOOM_ABA": "a"})
        self.maquina.processo(101, 100, "claude", paginas=100_000)
        r = self.ler({"a": 100})["a"]
        self.assertEqual(r["ram"], 101_000 * 4096)
        self.assertEqual(r["principais"][0]["nome"], "claude", "parado, quem tem mais memoria vem primeiro")


class Placa(ComProc):
    def montar(self):
        m = self.maquina
        m.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        m.processo(101, 100, "llama-server", ambiente={"LOOM_ABA": "a"})
        m.descritor(101, 3, alvo="/dev/null")
        m.descritor(101, 9, cliente=962, trabalho=1_000_000_000, calculo=4_000_000_000, vram=13_000_000)
        # o filho herdou o mesmo descritor: e o mesmo cliente, e nao pode contar duas vezes
        m.processo(102, 101, "llama-worker", ambiente={"LOOM_ABA": "a"})
        m.descritor(102, 9, cliente=962, trabalho=1_000_000_000, calculo=4_000_000_000, vram=13_000_000)

    def test_primeira_vez_que_ve_o_cliente_nao_inventa_uso(self):
        self.montar()
        r = self.ler({"a": 100})["a"]
        self.assertIsNone(r["gpu"])
        self.assertEqual(r["vram"], 13_000_000 * 1024)

    def test_uso_da_placa_pelo_trabalho_no_intervalo_e_cliente_contado_uma_vez(self):
        self.montar()
        self.ler({"a": 100})
        self.passar(2.0)
        # 0,5 s de desenho e 1 s de calculo em 2 s: 75%. A copia de memoria nao conta
        for pid in (101, 102):
            self.maquina.descritor(pid, 9, cliente=962, trabalho=1_500_000_000, calculo=5_000_000_000, vram=13_000_000)
        r = self.ler({"a": 100})["a"]
        self.assertAlmostEqual(r["gpu"], 75.0)
        self.assertEqual(r["vram"], 13_000_000 * 1024, "o cliente dividido entre pai e filho contou duas vezes")
        llama = next(p for p in r["principais"] if p["nome"] == "llama-server")
        self.assertAlmostEqual(llama["gpu"], 75.0)
        self.assertEqual(llama["vram"], 13_000_000 * 1024)
        bash = next(p for p in r["principais"] if p["nome"] == "bash")
        self.assertIsNone(bash["gpu"], "quem nao abriu a placa nao tem uso de placa, nem zero")

    def test_cliente_que_aparece_depois_nao_conta_o_que_gastou_antes(self):
        """O contador do cliente vem desde que ele abriu a placa: na primeira vez, ele so vira o ponto de partida."""
        self.maquina.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        self.ler({"a": 100})
        self.passar(2.0)
        self.maquina.processo(101, 100, "render", ambiente={"LOOM_ABA": "a"})
        self.maquina.descritor(101, 4, cliente=77, trabalho=50_000_000_000, vram=1000)
        r = self.ler({"a": 100})["a"]
        self.assertEqual(r["gpu"], 0.0)
        self.passar(2.0)
        self.maquina.descritor(101, 4, cliente=77, trabalho=50_400_000_000, vram=1000)
        self.assertAlmostEqual(self.ler({"a": 100})["a"]["gpu"], 20.0)

    def test_aba_sem_placa_nao_tem_numero_de_placa(self):
        self.maquina.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        self.maquina.descritor(100, 0, alvo="/dev/pts/3")
        self.ler({"a": 100})
        self.passar()
        r = self.ler({"a": 100})["a"]
        self.assertIsNone(r["gpu"])
        self.assertIsNone(r["vram"])

    def test_unidades_e_nomes_antigos_da_memoria_de_video(self):
        self.maquina.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        self.maquina.descritor(100, 5, cliente=1, vram=3, campo="drm-memory-vram", unidade="MiB")
        self.maquina.processo(101, 100, "jogo", ambiente={"LOOM_ABA": "a"})
        self.maquina.descritor(101, 5, cliente=2, vram=1024, campo="drm-total-vram", unidade="")
        self.assertEqual(self.ler({"a": 100})["a"]["vram"], 3 * MB + 1024)

    def test_descritores_sao_olhados_no_comeco_e_depois_de_tempos_em_tempos(self):
        self.maquina.processo(100, 1, "bash", ambiente={"LOOM_ABA": "a"})
        self.maquina.descritor(100, 0, alvo="/dev/pts/3")
        with unittest.mock.patch("loom.recursos_abas.os.readlink", wraps=os.readlink) as readlink:
            for _ in range(recursos_abas.OLHAR_PLACA_NO_COMECO):
                self.ler({"a": 100})
                self.passar()
            olhadas = readlink.call_count
            self.maquina.descritor(100, 7, cliente=5, vram=10)
            self.ler({"a": 100})
            self.assertEqual(readlink.call_count, olhadas, "depois do comeco, cada leitura nao refaz o readlink")
            for _ in range(recursos_abas.OLHAR_PLACA_A_CADA):
                self.passar()
                self.ler({"a": 100})
        self.assertEqual(self.ler({"a": 100})["a"]["vram"], 10 * 1024, "a placa aberta depois tem que aparecer")


class SemNada(unittest.TestCase):
    def test_sem_proc_volta_vazio(self):
        raiz = Path(tempfile.mkdtemp(prefix="loom-abas-"))
        self.addCleanup(shutil.rmtree, raiz, True)
        self.assertEqual(MedidorDeAbas(raiz).ler({"a": 1}), {})

    def test_sem_aba_nao_varre_o_proc(self):
        medidor = MedidorDeAbas(Path("/caminho/que/nao/existe"))
        with unittest.mock.patch("loom.recursos_abas.os.listdir") as listdir:
            self.assertEqual(medidor.ler({}), {})
            self.assertEqual(medidor.ler({"a": None}), {})
        listdir.assert_not_called()

    def test_nao_usa_programa_externo_nem_dependencia(self):
        fonte = Path(recursos_abas.__file__).read_text(encoding="utf-8")
        for proibido in ("subprocess", "psutil", "os.system", "Popen"):
            self.assertNotIn(proibido, fonte)

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "so com /proc de verdade")
    def test_no_proc_de_verdade_acha_o_proprio_processo(self):
        """O formato do kernel de verdade, e nao so o de mentira: este processo e uma aba de um so."""
        medidor = MedidorDeAbas()
        with unittest.mock.patch.dict(os.environ):
            r = medidor.ler({"eu": os.getpid()})
        self.assertGreaterEqual(r["eu"]["processos"], 1)
        self.assertGreater(r["eu"]["ram"], 0)


class PonteComAbas(ComPonte):
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

    def test_resposta_leva_o_gasto_das_abas_vivas(self):
        sessao = unittest.mock.Mock(alive=True, pid=4321)
        morta = unittest.mock.Mock(alive=False, pid=99)
        self.ponte._sessions = {"viva": sessao, "morta": morta}
        falso = unittest.mock.Mock()
        falso.ler.return_value = {"ram": {"usado": 1, "total": 2}}
        self.ponte._medidor = falso
        abas = unittest.mock.Mock()
        abas.ler.return_value = {"viva": {"cpu": 1.0}}
        self.ponte._medidor_abas = abas
        respostas = self.esperar_resposta()
        self.ponte.pedirRecursos()
        self.assertTrue(self.rodar_ate(lambda: respostas))
        abas.ler.assert_called_once_with({"viva": 4321})
        self.assertEqual(json.loads(respostas[0]), {"ram": {"usado": 1, "total": 2}, "abas": {"viva": {"cpu": 1.0}}})

    def test_gasto_das_abas_que_quebra_nao_apaga_a_barra_de_cima(self):
        self.ponte._sessions = {"viva": unittest.mock.Mock(alive=True, pid=4321)}
        falso = unittest.mock.Mock()
        falso.ler.return_value = {"ram": {"usado": 1, "total": 2}}
        self.ponte._medidor = falso
        abas = unittest.mock.Mock()
        abas.ler.side_effect = ValueError("processo estranho")
        self.ponte._medidor_abas = abas
        respostas = self.esperar_resposta()
        with self.assertLogs("loom.bridge", "WARNING"):
            self.ponte.pedirRecursos()
            self.assertTrue(self.rodar_ate(lambda: respostas))
        self.assertEqual(json.loads(respostas[0]), {"ram": {"usado": 1, "total": 2}})

    def test_sem_aba_viva_nem_cria_o_medidor_das_abas(self):
        self.ponte._sessions = {}
        falso = unittest.mock.Mock()
        falso.ler.return_value = {}
        self.ponte._medidor = falso
        respostas = self.esperar_resposta()
        with unittest.mock.patch("loom.bridge.mod_recursos_abas.MedidorDeAbas") as classe:
            self.ponte.pedirRecursos()
            self.assertTrue(self.rodar_ate(lambda: respostas))
        classe.assert_not_called()
        self.assertEqual(json.loads(respostas[0]), {})


class TextoDaAba(ComPagina):
    def itens(self, dados):
        return self.rodar("itensDaAba(" + json.dumps(dados) + ")")

    def test_programa_que_mais_gasta_e_o_total_da_aba(self):
        itens = self.itens({
            "cpu": 36.4, "nucleos": 5.82, "ram": int(4.6 * GB), "gpu": 98.8, "vram": int(13.7 * GB), "processos": 17,
            "principais": [
                {"nome": "llama-server", "nucleos": 5.8, "ram": int(4.4 * GB), "gpu": 98.8, "vram": int(13.7 * GB)},
                {"nome": "claude", "nucleos": 0.02, "ram": 410 * MB, "gpu": None, "vram": None},
            ],
        })
        self.assertEqual(itens["nome"], "llama-server")
        self.assertEqual(itens["cpu"]["valor"], "36%")
        self.assertEqual(itens["ram"]["valor"], "4,6 GB")
        self.assertEqual((itens["gpu"]["valor"], itens["gpu"]["nivel"]), ("99%", "critico"))
        self.assertEqual(itens["vram"]["valor"], "13,7 GB")
        self.assertFalse(itens["quieta"])
        self.assertEqual(
            itens["dica"],
            "Nesta aba agora, 17 processos, 5,8 núcleos ocupados:\n"
            "llama-server: 5,8 núcleos, 4,4 GB, placa 99%, 13,7 GB de vídeo\n"
            "claude: 0,0 núcleos, 410 MB\n...",
        )

    def test_aba_sem_placa_nao_mostra_placa(self):
        itens = self.itens({"cpu": 0.2, "nucleos": 0.03, "ram": 380 * MB, "gpu": None, "vram": None, "processos": 2,
                            "principais": [{"nome": "claude", "nucleos": 0.03, "ram": 374 * MB}]})
        self.assertIsNone(itens["gpu"])
        self.assertIsNone(itens["vram"])
        self.assertEqual(itens["ram"]["valor"], "380 MB")
        self.assertTrue(itens["quieta"], "parada, a linha fica apagada")

    def test_primeira_leitura_mostra_traco(self):
        itens = self.itens({"cpu": None, "nucleos": None, "ram": GB, "gpu": None, "vram": None, "processos": 1,
                            "principais": [{"nome": "bash", "nucleos": None, "ram": GB}]})
        self.assertEqual(itens["cpu"]["valor"], "--")
        self.assertIn("bash: --, 1,0 GB", itens["dica"])

    def test_um_nucleo_no_singular(self):
        self.assertEqual(self.rodar("textoDeNucleos(1.02)"), "1,0 núcleo")
        self.assertEqual(self.rodar("textoDeNucleos(12.4)"), "12 núcleos")

    def test_sem_dado_da_aba_nao_ha_linha(self):
        self.assertIsNone(self.itens(None))


class UsoDaAbaNaPagina(unittest.TestCase):
    def test_a_linha_nasce_no_cartao_e_no_chapeu_de_cada_aba(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        aba = app[app.index("class Aba extends AbaBase"):app.index("this.term.open(this.tela)")]
        self.assertIn('montarUsoDaAba("sessao-uso")', aba)
        self.assertIn('montarUsoDaAba("chapeu-uso")', aba)

    def test_desligar_o_monitor_tira_tambem_a_linha_das_abas(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        funcao = app[app.index("function aplicarRecursos()"):app.index("function pedirRecursos()")]
        self.assertIn("pintarUsoDasAbas({})", funcao)
        pintar = app[app.index("function pintarUsoDaAba("):app.index("function pintarUsoDasAbas(")]
        self.assertIn("cfg.mostrarRecursos !== false", pintar)

    def medir(self, codigo: str, largura: int, altura: int = 700) -> dict:
        from test_layout import medir_varios

        return medir_varios([codigo], largura=largura, altura=altura)[0]

    def test_no_cartao_mais_estreito_nada_sai_cortado_nem_vaza(self):
        modelo = """(function () {
          cfg.mostrarRecursos = true;
          cfg.larguraLateral = LARGURA;
          aplicarLarguraLateral();
          const lista = document.getElementById("abas");
          const cartao = document.createElement("div");
          cartao.className = "sessao aba";
          cartao.innerHTML = '<div class="sessao-topo"><span class="sessao-pasta rotulo">Canal Dark</span></div>' +
            '<div class="sessao-origem"></div><div class="sessao-linha">thinking</div><div class="sessao-rodape"><span class="sessao-conta">Principal</span></div>';
          const linha = montarUsoDaAba("sessao-uso");
          cartao.appendChild(linha);
          lista.appendChild(cartao);
          pintarUsoDaAba(linha, itensDaAba({ cpu: 100, nucleos: 16, ram: 31.9 * 1073741824, gpu: 100, vram: 15.9 * 1073741824,
            processos: 40, principais: [{ nome: "llama-server-com-nome-comprido", nucleos: 16, ram: 1 }] }));
          const caixa = cartao.getBoundingClientRect();
          const itens = [...linha.querySelectorAll(".uso-aba-item")].map((i) => i.getBoundingClientRect());
          return JSON.stringify({
            visivel: getComputedStyle(linha).display !== "none",
            dentro: itens.every((r) => r.left >= caixa.left && r.right <= caixa.right),
            vaza: linha.scrollWidth - linha.clientWidth,
            linhas: new Set(itens.map((r) => Math.round(r.top))).size,
            nome: linha.querySelector(".uso-aba-nome").getBoundingClientRect().width,
          });
        })()"""
        # 246 e a largura de fabrica da lateral; 180, a menor que ela aceita
        for largura, linhas in ((246, 2), (180, 3)):
            with self.subTest(lateral=largura):
                medida = self.medir(modelo.replace("LARGURA", str(largura)), 1300)
                self.assertTrue(medida["visivel"])
                self.assertTrue(medida["dentro"], "um numero saiu do cartao")
                self.assertLessEqual(medida["vaza"], 0)
                self.assertLessEqual(medida["linhas"], linhas, "a placa desce de linha, mas nao vira escada")
                self.assertGreater(medida["nome"], 20, "o nome do programa sumiu")


class MonitorNoMeio(unittest.TestCase):
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
                  const r = caixa.getBoundingClientRect();
                  const versao = document.getElementById("versao-topo").getBoundingClientRect();
                  const uso = document.getElementById("alternar-uso").getBoundingClientRect();
                  return JSON.stringify({ meio: (r.left + r.right) / 2, janela: innerWidth,
                    folgaEsquerda: r.left - versao.right, folgaDireita: uso.left - r.right });
                })()"""
            ],
            largura=largura,
            altura=600,
        )[0]

    def test_com_espaco_fica_no_meio_da_janela(self):
        for largura in (1100, 1300, 1920, 3440):
            with self.subTest(largura=largura):
                medida = self.medir(largura)
                self.assertAlmostEqual(medida["meio"], medida["janela"] / 2, delta=1)
                self.assertGreaterEqual(medida["folgaDireita"], 16, "o meio caiu em cima do botao de uso")
                self.assertGreaterEqual(medida["folgaEsquerda"], 16)

    def test_sem_espaco_volta_pro_lado_dos_botoes_sem_encostar(self):
        """Controle: abaixo do corte o meio da janela cairia em cima do botao de uso."""
        medida = self.medir(1000)
        self.assertNotAlmostEqual(medida["meio"], medida["janela"] / 2, delta=10)
        self.assertGreaterEqual(medida["folgaDireita"], 14)
        self.assertGreaterEqual(medida["folgaEsquerda"], 14)


if __name__ == "__main__":
    unittest.main()
