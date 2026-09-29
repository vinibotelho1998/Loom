"""O painel de uso a direita dos terminais, rodado no QJSEngine do Qt, sem janela nem motor web.

A pagina sobe com os scripts na ordem do index.html. Documento e janela sao falsos: o que o
painel mexe ganha um elemento de mentira que anota o que foi feito com ele, e o resto aceita
tudo e nao faz nada, como no test_pagina. O relogio entra de fora, entao nenhum texto depende
da hora em que o teste roda nem do fuso da maquina.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from base import ComPastaPropria  # noqa: E402

from PySide6.QtCore import QCoreApplication  # noqa: E402

from loom import config, vigia  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "ui"

# o painel mexe nestes tres; qualquer outro id da pagina vira o objeto que aceita tudo
NAVEGADOR_FALSO = r"""
var qualquer = new Proxy(function () {}, {
  get(_, chave) {
    if (chave === Symbol.iterator) return function* () {};
    if (chave === Symbol.toPrimitive) return () => 0;
    if (chave === "then") return undefined;
    return qualquer;
  },
  set() { return true; },
  apply() { return qualquer; },
  construct() { return qualquer; },
});

function elementoFalso(id) {
  const classes = new Set();
  const atributos = {};
  const ouvintes = {};
  const estilo = {};
  return {
    id: id, hidden: false, innerHTML: "", scrollTop: 0, estilo: estilo,
    style: { setProperty(k, v) { estilo[k] = v; } },
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      contains(c) { return classes.has(c); },
      toggle(c, forcar) {
        const ligar = forcar === undefined ? !classes.has(c) : !!forcar;
        if (ligar) classes.add(c); else classes.delete(c);
        return ligar;
      },
    },
    setAttribute(k, v) { atributos[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(atributos, k) ? atributos[k] : null; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(atributos, k); },
    addEventListener(tipo, f) { (ouvintes[tipo] = ouvintes[tipo] || []).push(f); },
    disparar(tipo, ev) { for (const f of ouvintes[tipo] || []) f(ev); },
    getBoundingClientRect() { return { left: 1290, right: 1600, top: 42, bottom: 900, width: 310, height: 858 }; },
    closest() { return null; },
  };
}

var elementosFalsos = {
  "uso-lateral": elementoFalso("uso-lateral"),
  "puxador-uso": elementoFalso("puxador-uso"),
  "alternar-uso": elementoFalso("alternar-uso"),
};
var corpoFalso = elementoFalso("body");
var ouvintesDaJanela = {};

var document = new Proxy(qualquer, {
  get(_, chave) {
    if (chave === "getElementById") return (id) => elementosFalsos[id] || qualquer;
    if (chave === "hidden") return false;
    if (chave === "body") return corpoFalso;
    if (chave === Symbol.toPrimitive) return () => 0;
    if (chave === "then") return undefined;
    return qualquer;
  },
  set() { return true; },
});
var window = new Proxy(qualquer, {
  get(_, chave) {
    if (chave === "addEventListener") return (tipo, f) => { (ouvintesDaJanela[tipo] = ouvintesDaJanela[tipo] || []).push(f); };
    if (chave === Symbol.toPrimitive) return () => 0;
    if (chave === "then") return undefined;
    return qualquer;
  },
  set() { return true; },
});
var performance = qualquer, qt = qualquer, QWebChannel = qualquer, requestAnimationFrame = qualquer;
var setTimeout = qualquer, clearTimeout = qualquer, setInterval = qualquer;

function naJanela(tipo, ev) {
  for (const f of ouvintesDaJanela[tipo] || []) f(ev);
}
function tecla(codigo, extra) {
  const ev = Object.assign({ ctrlKey: true, shiftKey: true, altKey: false, metaKey: false, code: codigo,
    key: codigo.slice(3), impedido: false, preventDefault() { this.impedido = true; } }, extra || {});
  naJanela("keydown", ev);
  return ev.impedido;
}
function ponteFalsa(comRelatorio, resposta) {
  const p = { gravados: [], pedidos: 0, pronto: null, resposta: resposta };
  p.saveConfig = (json, depois) => { p.gravados.push(JSON.parse(json)); };
  p.painelUsoPronto = { connect(f) { p.pronto = f; } };
  if (comRelatorio) p.getPainelUso = (depois) => { p.pedidos++; depois(p.resposta); };
  return p;
}
function abaFalsa() {
  return { ehPainel: false, ajustes: 0, ajustar() { this.ajustes++; }, foco() {} };
}
"""

# o que o script guarda em let e const nao vira global, e o eval do QJSEngine nao alcanca
DE_DENTRO = r"""
var P = PainelUso;
var R = PainelUso.regras;
var estado = {
  get cfg() { return cfg; }, set cfg(v) { cfg = v; },
  get bridge() { return bridge; }, set bridge(v) { bridge = v; },
  get abaAtiva() { return abaAtiva; }, set abaAtiva(v) { abaAtiva = v; },
  get emGrade() { return emGrade; }, set emGrade(v) { emGrade = v; },
  get atalhos() { return Array.from(ATALHOS_DA_JANELA); },
  get campos() { return CAMPOS.map((c) => c[0]); },
};
"""

MESES = ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."]

AGORA = "2026-09-28T21:30:00-03:00"

# o exemplo do contrato do painel, como a Bridge entrega
CONTRATO = {
    "geradoEm": "2026-09-28T21:30:00-03:00",
    "coletando": False,
    "contas": [
        {
            "id": "claude:84092a89",
            "ferramenta": "claude",
            "nome": "Principal",
            "plano": "max",
            "email": "",
            "atualizadoEm": "2026-09-28T21:28:10-03:00",
            "janelas": [
                {"tipo": "5h", "rotulo": "Sessao", "usado": 12.0, "zeraEm": "2026-09-29T00:40:00-03:00",
                 "duracaoMin": 300, "origem": "real", "lidoEm": "2026-09-28T21:28:10-03:00"},
                {"tipo": "7d", "rotulo": "Semana", "usado": 16.0, "zeraEm": "2026-10-02T23:00:00-03:00",
                 "duracaoMin": 10080, "origem": "real", "lidoEm": "2026-09-28T21:28:10-03:00"},
            ],
            "bloco": {"inicio": "2026-09-28T17:00:00-03:00", "fim": "2026-09-28T22:00:00-03:00",
                      "tokens": 1200000, "custo": 38.2, "porMinuto": 6100.0, "projecaoCusto": 71.0,
                      "estimado": False},
            "custo": {"hoje": 42.1, "dias30": 1380.5, "semPreco": False, "aproximado": False},
            "tokens": {"hoje": 1100000, "dias30": 27000000},
            "serie": [
                {"dia": "2026-08-30", "custo": 40.0, "tokens": 900000, "porModelo": {"claude-opus-5-5": 40.0}}
            ],
            "erro": None,
        },
        {
            "id": "codex:padrao",
            "ferramenta": "codex",
            "nome": "Codex",
            "plano": "pro",
            "email": "",
            "atualizadoEm": "2026-09-26T18:02:00-03:00",
            "janelas": [
                {"tipo": "7d", "rotulo": "Semana", "usado": 85.0, "zeraEm": "2026-09-30T00:00:00-03:00",
                 "duracaoMin": 10080, "origem": "real", "lidoEm": "2026-09-26T18:02:00-03:00"}
            ],
            "bloco": None,
            "custo": {"hoje": 0.0, "dias30": 210.4, "semPreco": True, "aproximado": False},
            "tokens": {"hoje": 0, "dias30": 5400000},
            "serie": [],
            "erro": None,
        },
    ],
}


def local(iso: str) -> datetime:
    """A hora do contrato no fuso desta maquina, que e o mesmo que o QJSEngine usa."""
    return datetime.fromisoformat(iso).astimezone()


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def contrato(**troca) -> dict:
    dados = json.loads(json.dumps(CONTRATO))
    dados.update(troca)
    return dados


def serie_de_30() -> list[dict]:
    """Trinta dias ate 28/09 com o pico em 20/09, e o ultimo dia com tres modelos."""
    dias = []
    for n in range(30):
        dia = 30 + n if n < 2 else n - 1
        mes = 8 if n < 2 else 9
        custo = 10.0 + n
        dias.append({"dia": f"2026-{mes:02d}-{dia:02d}", "custo": custo, "tokens": 100000 * (n + 1),
                     "porModelo": {"claude-opus-5-5": custo}})
    dias[21]["custo"] = 90.0
    dias[-1]["porModelo"] = {"claude-sonnet-4-5": 3.0, "claude-opus-5-5": 30.0, "claude-haiku-4-5": 6.0}
    return dias


def garantir_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def texto_da_pagina() -> str:
    """Os scripts nossos, na ordem do index.html. As bibliotecas de fora nao entram."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    nossos = [s for s in re.findall(r'<script src="([^"]+)"', html) if not s.startswith("vendor/")]
    partes = [(UI / nome).read_text(encoding="utf-8") for nome in nossos]
    # o QJSEngine nao conhece funcao async, e o app.js tem uma so, a de colar pelo navegador
    return "\n".join(p.replace("async function", "function").replace("await ", "") for p in partes) + DE_DENTRO


class ComPainel(unittest.TestCase):
    """Uma pagina nova por teste: o painel se liga uma vez so e guarda o que viu."""

    def setUp(self) -> None:
        garantir_app()
        from PySide6.QtQml import QJSEngine

        self.motor = QJSEngine()
        self.motor.evaluate(NAVEGADOR_FALSO)
        carga = self.motor.evaluate(texto_da_pagina(), "pagina.js")
        self.assertFalse(carga.isError(), carga.toString())

    def rodar(self, expressao: str):
        resultado = self.motor.evaluate("JSON.stringify(" + expressao + ")")
        self.assertFalse(resultado.isError(), resultado.toString())
        texto = resultado.toString()
        return None if texto == "undefined" else json.loads(texto)

    def definir(self, codigo: str) -> None:
        resultado = self.motor.evaluate(codigo)
        self.assertFalse(resultado.isError(), resultado.toString())

    def desenho(self, dados, vista: dict | None = None, agora: str = AGORA) -> str:
        bruto = dados if isinstance(dados, str) else json.dumps(dados)
        return self.rodar(f"P.html(P.lerRelatorio({json.dumps(bruto)}), {json.dumps(vista or {})}, {ms(agora)})")


class RegrasDoCodexBar(ComPainel):
    def test_percentual_arredonda_o_meio_pro_par_e_diz_menos_de_um(self):
        casos = {58: "58% restante", 42.5: "42% restante", 43.5: "44% restante", 0.4: "<1% restante",
                 0: "0% restante", -5: "0% restante", 130: "100% restante"}
        for valor, esperado in casos.items():
            self.assertEqual(self.rodar(f'R.textoPercentual({valor}, "restante")'), esperado, valor)

    def test_barra_some_abaixo_de_meio_e_enche_acima_de_99_e_meio(self):
        self.assertEqual(self.rodar("[0.4, 0.6, 58.2, 99.4, 99.6].map(R.larguraPreenchimento)"), [0, 0.6, 58.2, 99.4, 100])

    def test_zera_em_conta_minutos_para_cima(self):
        hora, dia = 3600000, 86400000
        casos = {
            2 * hora + 15 * 60000: "Renova em 2h 15m",
            2 * dia + 3 * hora: "Renova em 2d 3h",
            2 * dia + 15 * 60000: "Renova em 2d 15m",
            2 * dia: "Renova em 2d",
            3 * hora: "Renova em 3h",
            59500: "Renova em 1m",
            45 * 60000 - 1000: "Renova em 45m",
            500: "Renova agora",
            -60000: "Renova agora",
        }
        for falta, esperado in casos.items():
            self.assertEqual(self.rodar(f"R.textoZera({falta}, 0)"), esperado, falta)

    def test_atualizado_ha_e_a_data_quando_passa_de_um_dia(self):
        agora = ms(AGORA)
        self.assertEqual(self.rodar(f"R.textoAtualizado({agora - 30000}, {agora})"), "Atualizado agora mesmo")
        self.assertEqual(self.rodar(f"R.textoAtualizado({agora + 5000}, {agora})"), "Atualizado agora mesmo")
        self.assertEqual(self.rodar(f"R.textoAtualizado({agora - 5 * 60000 - 9000}, {agora})"), "Atualizado há 5 min.")
        self.assertEqual(self.rodar(f"R.textoAtualizado({agora - 2 * 3600000 - 60000}, {agora})"), "Atualizado há 2 h")
        velho = local("2026-09-26T18:02:00-03:00")
        self.assertEqual(
            self.rodar(f'R.textoAtualizado({ms("2026-09-26T18:02:00-03:00")}, {agora})'),
            f"Atualizado {velho.day} de {MESES[velho.month - 1]} {velho:%H:%M}",
        )

    def ritmo(self, usado: float, falta_min: float, duracao: int, contexto: str, folga: bool = False):
        r = f"R.ritmo({{usado: {usado}, janelaMin: {duracao}, zeraEmMs: {falta_min * 60000}}}, 0, {duracao})"
        return self.rodar(
            f"(() => {{ const r = {r}; if (!r) return null; return {{visivel: R.ritmoVisivel(r, '{contexto}'),"
            f" texto: R.textoRitmo(r, '{contexto}', 0, {json.dumps(folga)}), listra: R.listraRitmo(r)}}; }})()"
        )

    def test_ritmo_da_sessao_em_reserva_dura_ate_renovar(self):
        """Metade da janela passou com 44% gasto: 6 pontos abaixo, e no passo de agora sobra."""
        r = self.ritmo(44, 150, 300, "sessao")
        self.assertEqual(r["texto"], "6% em reserva · Dura até a renovação")
        self.assertTrue(r["visivel"])
        self.assertEqual(r["listra"], {"p": 50, "deficit": False})

    def test_ritmo_da_semana_em_deficit_diz_quando_esgota(self):
        """Metade da semana com 70% gasto: 20 pontos acima, e no passo de agora acaba em 36 horas."""
        r = self.ritmo(70, 5040, 10080, "semanal")
        self.assertEqual(r["texto"], "20% em déficit · Esgota em 1d 12h")
        self.assertEqual(r["listra"], {"p": 50, "deficit": True})
        self.assertEqual(self.ritmo(70, 150, 300, "sessao")["texto"], "20% em déficit · Esgotamento previsto em 1h 5m")

    def test_no_ritmo_nao_tem_listra(self):
        r = self.ritmo(49, 150, 300, "sessao")
        self.assertEqual(r["texto"], "No ritmo · Dura até a renovação")
        self.assertIsNone(r["listra"])

    def test_ritmo_some_no_comeco_da_janela_e_sem_sobra(self):
        self.assertFalse(self.ritmo(1, 295, 300, "sessao")["visivel"], "esperado abaixo de 3 pontos")
        self.assertFalse(self.ritmo(100, 150, 300, "sessao")["visivel"], "sem nada restante")
        self.assertIsNone(self.ritmo(10, 400, 300, "sessao"), "zera depois do fim da janela")
        self.assertIsNone(self.ritmo(10, -5, 300, "sessao"), "ja zerou")

    def test_folga_so_no_codex_e_so_com_folga_grande(self):
        self.assertEqual(self.ritmo(10, 150, 300, "sessao", folga=True)["texto"],
                         "40% em reserva · Dura até a renovação · folga de 1,5×")
        self.assertEqual(self.ritmo(10, 150, 300, "sessao")["texto"], "40% em reserva · Dura até a renovação")

    def test_tokens_sobem_de_unidade_onde_a_de_baixo_viraria_mil(self):
        casos = {999: "999", 1000: "1K", 1250: "1.2K", 1200: "1.2K", 999499: "999K", 999500: "1M",
                 1100000: "1.1M", 27000000: "27M", 45500000: "46M", 1500000000: "1.5B", -3000: "-3K", 0: "0"}
        for valor, esperado in casos.items():
            self.assertEqual(self.rodar(f"R.textoTokens({valor})"), esperado, valor)

    def test_moeda_no_formato_americano_e_compacta_no_eixo(self):
        casos = {
            "R.textoMoeda(54.72)": "$54.72",
            "R.textoMoeda(-3.1)": "-$3.10",
            "R.textoMoeda(1380.5)": "$1,380.50",
            "R.textoMoeda(1234567)": "$1,234,567.00",
            "R.textoMoedaCompacta(12.4)": "$12",
            "R.textoMoedaCompacta(1380.5)": "$1,381",
            "R.textoMoedaCompacta(0.42)": "$0.42",
            "R.textoMoedaCompacta(0)": "$0",
            'R.linhaCusto("Hoje", 4.12, 1200000)': "Hoje: $4.12 · 1.2M tokens",
            'R.linhaCusto("Hoje", null, null)': "Hoje: --",
        }
        for expressao, esperado in casos.items():
            self.assertEqual(self.rodar(expressao), esperado, expressao)

    def test_eixo_do_grafico_e_as_barras(self):
        self.assertEqual(self.rodar('R.marcasEixoY(0.8, "custo")'), [0, 0.8])
        self.assertEqual(self.rodar('R.marcasEixoY(50, "custo")'), [0, 25, 50])
        self.assertEqual(self.rodar('R.marcasEixoY(1, "tokens")'), [0, 1])
        self.assertEqual(self.rodar('R.marcasEixoY(0, "custo")'), [])
        barras = self.rodar("R.geometriaBarras([1, 4, 2, 4], 100, 50)")
        self.assertEqual([b["h"] for b in barras], [12.5, 50, 25, 50])
        self.assertEqual([b["tampa"] for b in barras], [0, 2.5, 0, 0], "a tampa fica no primeiro pico so")
        self.assertEqual((barras[0]["x"], barras[0]["larg"]), (3.75, 17.5))
        self.assertEqual(self.rodar("R.geometriaBarras([0, 0], 100, 50).map((b) => b.tampa)"), [0, 0])
        self.assertEqual(self.rodar("[0, 99, 150, 299, 400, -5].map((x) => R.indiceNoGrafico(x, 300, 3))"), [0, 0, 1, 2, 2, 0])
        self.assertEqual(self.rodar("R.indiceNoGrafico(999, 300, 1)"), 0)


class DesenhoDoContrato(ComPainel):
    def test_claude_do_contrato(self):
        html = self.desenho(CONTRATO)
        for trecho in (
            '<h3 class="uso-nome">Claude</h3>',
            ">Principal</span>",
            '<span class="uso-legenda">Atualizado há 1 min.</span>',
            '<span class="uso-plano">Max</span>',
            "Sessão 88% restante</span>",
            '<span class="uso-janela-zera">Renova em 3h 10m</span>',
            "Semanal 84% restante</span>",
            "Renova em 4d 1h",
            '<i class="uso-trilho-marca" style="--p:50"></i><i class="uso-trilho-marca" style="--p:20"></i>',
            '<i class="uso-trilho-ritmo reserva" style="--p:63.33"></i>',
            "25% em reserva · Dura até a renovação",
            "26% em reserva · Dura até a renovação",
            ">Hoje: $42.10 · 1.1M tokens</p>",
            ">Últimos 30 dias: $1,380.50 · 27M tokens</p>",
            ">Bloco atual: $38.20 · 1.2M tokens</p>",
            f">Projeção do bloco: $71.00 até {local('2026-09-28T22:00:00-03:00'):%H:%M}</p>",
            "Estimado pelos logs locais · pode diferir da fatura",
            '<p class="uso-grafico-dia">30 de ago.: $40.00 · 900K tokens</p>',
            "<b>Opus 5.5</b><span>$40.00</span>",
            "Total est. em 30 de ago.: $40.00",
        ):
            self.assertIn(trecho, html)
        self.assertNotIn("uso-selo", html, "tudo veio do limite da propria ferramenta")
        self.assertNotIn("uso-custo-aviso", html)
        self.assertNotIn("uso-seletor-contas", html, "uma conta por provedor dispensa o seletor")

    def test_abas_de_provedor_com_a_cota_da_semana(self):
        html = self.desenho(CONTRATO)
        abas = re.findall(r'<button class="uso-provedor( ativo)?"[^>]*data-provedor="(\w+)".*?</button>', html)
        self.assertEqual(abas, [(" ativo", "claude"), ("", "codex")])
        self.assertEqual(re.findall(r'uso-provedor-cota"><b style="width:([\d.]+)%', html), ["84", "15"])

    def test_codex_do_contrato(self):
        html = self.desenho(CONTRATO, {"provedor": "codex"})
        velho = local("2026-09-26T18:02:00-03:00")
        for trecho in (
            '<h3 class="uso-nome">Codex</h3>',
            f"Atualizado {velho.day} de {MESES[velho.month - 1]} {velho:%H:%M}</span>",
            '<span class="uso-plano">Pro</span>',
            "Semanal 15% restante</span>",
            "Renova em 1d 2h",
            "No ritmo · Esgota em 1d 59m",
            ">Hoje: $0.00 · 0 tokens</p>",
            ">Últimos 30 dias: $210.40 · 5.4M tokens</p>",
            "Há modelo sem preço na tabela",
            "Nenhum dado disponível",
        ):
            self.assertIn(trecho, html)
        self.assertNotIn("uso-trilho-ritmo", html, "no ritmo nao ganha listra")
        self.assertNotIn("Bloco", html)
        self.assertNotIn('class="uso-email"', html, "a conta chamada Codex nao repete o nome do provedor")

    def test_grafico_de_30_dias_comeca_no_ultimo_e_segue_a_escolha(self):
        dados = contrato()
        dados["contas"][0]["serie"] = serie_de_30()
        html = self.desenho(dados)
        self.assertEqual(html.count('<rect class="barra"'), 30)
        self.assertEqual(len(re.findall(r'<rect class="pico" x="([\d.]+)"', html)), 1)
        # a faixa de cada dia tem 1000/30 e a barra comeca 15% dentro dela: o dia 20 e o vigesimo segundo
        self.assertIn('<rect class="pico" x="705"', html, "o pico e o dia 20")
        self.assertIn("<span>30 de ago.</span><span>28 de set.</span>", html)
        self.assertIn("<span>$90</span><span>$45</span><span>$0</span>", html)
        self.assertIn('<p class="uso-grafico-dia">28 de set.: $39.00 · 3M tokens</p>', html)
        # tres modelos do mais caro pro mais barato, com o filete cada vez mais apagado
        modelos = re.findall(r'--a:([\d.]+)"><i class="uso-grafico-acento"></i><div><b>([^<]+)</b><span>([^<]+)', html)
        self.assertEqual(modelos, [("0.75", "Opus 5.5", "$30.00"), ("0.63", "Haiku 4.5", "$6.00"), ("0.51", "Sonnet 4.5", "$3.00")])
        self.assertIn("Total est. dos últimos 30 dias: $794.00", html)
        faixa_final = re.search(r'<rect class="faixa" x="([\d.]+)"', html).group(1)

        escolhido = self.desenho(dados, {"dias": {"claude:84092a89": "2026-08-30"}})
        self.assertIn('<p class="uso-grafico-dia">30 de ago.: $10.00 · 100K tokens</p>', escolhido)
        self.assertNotEqual(re.search(r'<rect class="faixa" x="([\d.]+)"', escolhido).group(1), faixa_final)

        em_tokens = self.desenho(dados, {"metricas": {"claude:84092a89": "tokens"}})
        self.assertIn("<span>3M</span><span>1.5M</span><span>0</span>", em_tokens)
        self.assertIn('class="ativo" data-metrica="tokens"', em_tokens)
        # 46,5 milhoes arredonda pro par, como o %.0f do CodexBar
        self.assertIn("Total dos últimos 30 dias: 46M tokens", em_tokens)

    def test_mais_de_quatro_modelos_rolam(self):
        dados = contrato()
        dados["contas"][0]["serie"][0]["porModelo"] = {f"claude-m{i}": float(i) for i in range(1, 6)}
        self.assertIn("Role para ver mais modelos", self.desenho(dados))

    def test_selo_quando_o_limite_e_estimado(self):
        dados = contrato()
        dados["contas"][0]["janelas"][0]["origem"] = "estimada"
        html = self.desenho(dados)
        sessao = re.search(r'data-tipo="5h">(.*?)</div><div class="uso-trilho', html).group(1)
        semana = re.search(r'data-tipo="7d">(.*?)</div><div class="uso-trilho', html).group(1)
        self.assertIn('class="uso-selo"', sessao)
        self.assertNotIn("uso-selo", semana)

    def test_aviso_de_preco_faltando_e_de_custo_aproximado(self):
        dados = contrato()
        dados["contas"][0]["custo"].update(semPreco=True, aproximado=True)
        html = self.desenho(dados)
        avisos = re.findall(r'<p class="uso-custo-aviso">([^<]+)</p>', html)
        self.assertEqual(len(avisos), 2)
        self.assertIn("sem preço", avisos[0])
        self.assertIn("aproximado", avisos[1])

    def test_varias_contas_do_mesmo_provedor_ganham_o_seletor(self):
        dados = contrato()
        email = "alguem.com.um.nome.longo@empresa-grande.com.br"
        outra = json.loads(json.dumps(dados["contas"][0]))
        outra.update(id="claude:trab", nome="", email=email)
        outra["janelas"][0]["usado"] = 70.0
        dados["contas"].insert(1, outra)
        html = self.desenho(dados)
        self.assertIn('style="--colunas:2"', html)
        self.assertEqual(len(re.findall(r'class="uso-seletor-conta[ "]', html)), 2)
        # e-mail corta no meio: o comeco e o dominio e que dizem de quem e
        self.assertIn(">" + email[:12] + "…" + email[-11:] + "</button>", html)

        trab = self.desenho(dados, {"contas": {"claude": "claude:trab"}})
        self.assertIn('data-conta="claude:trab"', trab)
        self.assertIn("Sessão 30% restante", trab)
        self.assertIn(">" + email[:15] + "…" + email[-14:] + "</span>", trab)

    def test_quatro_contas_viram_duas_fileiras(self):
        dados = contrato()
        base = dados["contas"][0]
        for n in range(3):
            copia = json.loads(json.dumps(base))
            copia.update(id=f"claude:{n}", nome=f"Conta {n}")
            dados["contas"].append(copia)
            colunas = {1: 2, 2: 3, 3: 2}[n + 1]
            self.assertIn(f'style="--colunas:{colunas}"', self.desenho(dados), n + 2)


class EstadosDoPainel(ComPainel):
    def test_antes_da_primeira_coleta_diz_que_esta_atualizando(self):
        html = self.desenho({"geradoEm": None, "coletando": True, "contas": []})
        self.assertIn('<span class="uso-legenda carregando">Atualizando…</span><i class="uso-giro"></i>', html)
        self.assertNotIn("Ainda sem uso", html)
        self.assertIn("Atualizando…", self.rodar(f"P.html(null, {{}}, {ms(AGORA)})"))

    def test_sem_conta_nenhuma_diz_que_ainda_nao_ha_uso(self):
        html = self.desenho({"geradoEm": "2026-09-28T21:29:00-03:00", "coletando": False, "contas": []})
        self.assertIn('<p class="uso-sem-dado">Ainda sem uso</p>', html)
        self.assertIn("Atualizado há 1 min.", html)
        self.assertNotIn("Atualizando", html)

    def test_coletando_com_dado_velho_mostra_o_dado_e_avisa(self):
        html = self.desenho(contrato(coletando=True))
        self.assertIn('<span class="uso-legenda carregando">Atualizando…</span>', html)
        self.assertIn("Sessão 88% restante", html, "o dado de antes continua a vista")
        self.assertIn('<i class="uso-giro"></i>', html)

    def test_conta_so_com_erro_mostra_o_erro_e_o_botao_de_copiar_sem_corpo(self):
        dados = contrato()
        conta = dados["contas"][0]
        conta.update(janelas=[], bloco=None, serie=[], erro="Falhou ao ler <status line>")
        conta["custo"].update(hoje=0, dias30=0)
        conta["tokens"].update(hoje=0, dias30=0)
        html = self.desenho(dados)
        self.assertIn('<span class="uso-legenda erro" title="Falhou ao ler &#60;status line&#62;">Falhou ao ler &#60;status line&#62;</span>', html)
        self.assertIn("data-copiar", html)
        self.assertTrue(html.endswith("</header></section>"), "so o cabecalho, sem corpo nem ainda sem uso")

    def test_erro_com_dado_mostra_os_dois(self):
        dados = contrato()
        dados["contas"][0]["erro"] = "Leitura antiga"
        html = self.desenho(dados)
        self.assertIn('uso-legenda erro', html)
        self.assertIn("Sessão 88% restante", html)

    def test_conta_sem_limite_lido_mas_com_custo(self):
        dados = contrato()
        dados["contas"][0]["janelas"] = []
        html = self.desenho(dados)
        self.assertIn('<p class="uso-sem-dado">Limites indisponíveis</p>', html)
        self.assertIn("Hoje: $42.10", html)

    def test_janela_sem_numero_vira_indisponivel(self):
        dados = contrato()
        dados["contas"][0]["janelas"][0]["usado"] = None
        html = self.desenho(dados)
        self.assertIn('<p class="uso-janela-meta">Indisponível · Renova em 3h 10m</p>', html)

    def test_janela_do_mes_e_outras_vem_depois_da_semana(self):
        dados = contrato()
        janelas = dados["contas"][1]["janelas"]
        janelas.insert(0, {"tipo": "30d", "rotulo": "Janela de 30d", "usado": 40.0, "zeraEm": "2026-10-20T00:00:00-03:00",
                           "duracaoMin": 43200, "origem": "real", "lidoEm": None})
        janelas.insert(0, {"tipo": "1d", "rotulo": "Janela de 1d", "usado": 10.0, "zeraEm": None,
                           "duracaoMin": 1440, "origem": "real", "lidoEm": None})
        html = self.desenho(dados, {"provedor": "codex"})
        titulos = re.findall(r'<span class="uso-janela-titulo">([^<]+)</span>', html)
        self.assertEqual(titulos, ["Semanal 15% restante", "Janela de 1d 90% restante", "Mensal 60% restante"])

    def test_relatorio_ilegivel_nao_derruba(self):
        self.assertIn("Não deu pra ler o relatório de uso.", self.desenho("{quebrado"))
        self.assertIn("Não deu pra ler o relatório de uso.", self.desenho("[1, 2]"))

    def test_nome_da_conta_nao_vira_html(self):
        dados = contrato()
        dados["contas"][0]["nome"] = '<img src=x onerror="a()">'
        dados["contas"][0]["plano"] = "<b>max</b>"
        html = self.desenho(dados)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>Max", html)
        self.assertIn("&#60;img src=x", html)


class LayoutDoPainel(ComPainel):
    def ligar(self, cfg: dict, com_relatorio: bool = True, resposta=None) -> None:
        resposta = json.dumps(resposta or {"geradoEm": None, "coletando": True, "contas": []})
        self.definir(
            f"estado.cfg = {json.dumps(cfg)}; estado.bridge = ponteFalsa({json.dumps(com_relatorio)}, {json.dumps(resposta)});"
            " var aba = abaFalsa(); estado.abaAtiva = aba; P.ligar();"
            ' var painel = elementosFalsos["uso-lateral"], puxador = elementosFalsos["puxador-uso"],'
            ' botao = elementosFalsos["alternar-uso"];'
        )

    def test_nasce_aberto_na_largura_do_codexbar_e_pede_o_relatorio(self):
        self.ligar({})
        self.assertEqual(self.rodar("[painel.hidden, puxador.hidden]"), [False, False])
        self.assertEqual(self.rodar('painel.estilo["--largura-uso"]'), "310px")
        self.assertEqual(self.rodar('[botao.classList.contains("ativo"), botao.getAttribute("aria-pressed")]'), [True, "true"])
        self.assertEqual(self.rodar("estado.bridge.pedidos"), 1)
        self.assertIn("Atualizando…", self.rodar("painel.innerHTML"))

    def test_config_fechada_e_largura_fora_da_faixa(self):
        self.ligar({"painelUso": False, "larguraPainelUso": 999})
        self.assertEqual(self.rodar("[painel.hidden, puxador.hidden]"), [True, True])
        self.assertEqual(self.rodar('painel.estilo["--largura-uso"]'), "480px")
        self.assertEqual(self.rodar("estado.bridge.pedidos"), 0, "fechado nao pede relatorio")

    def test_largura_fica_entre_280_e_480(self):
        casos = {"1000": 480, "100": 280, "333.4": 333, '"320"': 320, '"x"': 310, "null": 310, "true": 310, "NaN": 310}
        for valor, esperado in casos.items():
            self.assertEqual(self.rodar(f"P.limitarLargura({valor})"), esperado, valor)

    def test_ctrl_shift_u_alterna_grava_e_remede_o_terminal(self):
        self.ligar({})
        self.assertIn("KeyU", self.rodar("estado.atalhos"), "sem isso o terminal recebe a tecla junto")
        self.assertTrue(self.rodar('tecla("KeyU")'))
        self.assertEqual(self.rodar("[painel.hidden, puxador.hidden, estado.cfg.painelUso]"), [True, True, False])
        self.assertEqual(self.rodar("estado.bridge.gravados"), [{"painelUso": False}])
        self.assertEqual(self.rodar("aba.ajustes"), 1)
        self.assertEqual(self.rodar('botao.getAttribute("aria-pressed")'), "false")

        self.assertTrue(self.rodar('tecla("KeyU")'))
        self.assertEqual(self.rodar("[painel.hidden, estado.cfg.painelUso]"), [False, True])
        self.assertEqual(self.rodar("estado.bridge.gravados[1]"), {"painelUso": True})
        self.assertEqual(self.rodar("estado.bridge.pedidos"), 2, "abrir pede o relatorio de novo")
        self.assertEqual(self.rodar("aba.ajustes"), 2)

    def test_botao_do_topo_alterna_sem_tirar_o_foco(self):
        self.ligar({})
        impedido = self.rodar('(() => { const ev = {preventDefault() { this.feito = true; }}; botao.disparar("mousedown", ev); return !!ev.feito; })()')
        self.assertTrue(impedido)
        self.definir('botao.disparar("click", {})')
        self.assertEqual(self.rodar("[painel.hidden, estado.bridge.gravados]"), [True, [{"painelUso": False}]])

    def test_na_grade_as_colunas_sao_refeitas(self):
        self.ligar({})
        self.definir("var grades = 0; reencaixarGrade = function () { grades++; }; estado.emGrade = true; P.alternar(false)")
        self.assertEqual(self.rodar("[grades, aba.ajustes]"), [1, 0])

    def test_arrastar_a_alca_para_nas_bordas_e_grava_ao_soltar(self):
        self.ligar({})
        self.definir('puxador.disparar("mousedown", {button: 0, preventDefault() {}}); naJanela("mousemove", {clientX: 600})')
        self.assertEqual(self.rodar('[painel.estilo["--largura-uso"], estado.cfg.larguraPainelUso]'), ["480px", 480])
        self.assertTrue(self.rodar('corpoFalso.classList.contains("puxando-largura")'))
        self.definir('naJanela("mousemove", {clientX: 1500})')
        self.assertEqual(self.rodar('painel.estilo["--largura-uso"]'), "280px")
        self.definir('naJanela("mousemove", {clientX: 1250})')
        self.assertEqual(self.rodar("estado.bridge.gravados"), [], "arrastando nao grava a cada pixel")
        self.definir('naJanela("mouseup", {})')
        self.assertEqual(self.rodar("estado.bridge.gravados"), [{"larguraPainelUso": 350}])
        self.assertEqual(self.rodar("aba.ajustes"), 1)
        self.assertFalse(self.rodar('corpoFalso.classList.contains("puxando-largura")'))
        self.definir('naJanela("mousemove", {clientX: 900}); naJanela("mouseup", {})')
        self.assertEqual(self.rodar("estado.bridge.gravados.length"), 1, "solto, o mouse nao arrasta mais")

    def test_configuracoes_abrem_fecham_e_mudam_a_largura(self):
        self.ligar({})
        self.definir("estado.cfg.painelUso = false; P.aplicar()")
        self.assertTrue(self.rodar("painel.hidden"))
        self.definir("estado.cfg.painelUso = true; estado.cfg.larguraPainelUso = 300; P.aplicar()")
        self.assertEqual(self.rodar('[painel.hidden, painel.estilo["--largura-uso"]]'), [False, "300px"])
        self.assertEqual(self.rodar("aba.ajustes"), 2)
        self.definir("P.aplicar()")
        self.assertEqual(self.rodar("aba.ajustes"), 2, "sem mudanca o terminal nao e remedido")
        self.assertEqual(self.rodar("estado.bridge.gravados"), [], "quem grava e a tela de configuracoes")
        self.assertEqual(
            [c for c in self.rodar("estado.campos") if "ainel" in c], ["painelUso", "larguraPainelUso"]
        )

    def test_relatorio_novo_pelo_sinal_redesenha(self):
        self.ligar({})
        self.definir(f"estado.bridge.pronto({json.dumps(json.dumps(CONTRATO))})")
        html = self.rodar("painel.innerHTML")
        self.assertIn('<h3 class="uso-nome">Claude</h3>', html)
        self.assertIn("Sessão 88% restante", html)

    def test_app_sem_o_relatorio_mostra_o_vazio(self):
        self.ligar({}, com_relatorio=False)
        self.assertIn("Ainda sem uso", self.rodar("painel.innerHTML"))

    def test_cliques_trocam_provedor_conta_e_metrica_e_o_mouse_escolhe_o_dia(self):
        dados = contrato()
        dados["contas"][0]["serie"] = serie_de_30()
        self.ligar({}, resposta=dados)

        def alvo(atributos: dict, conta: str = "") -> str:
            cartao = "({getAttribute: () => " + json.dumps(conta) + "})"
            return (
                "{closest: (s) => ({hasAttribute: (k) => k in " + json.dumps(atributos) + ","
                " getAttribute: (k) => (" + json.dumps(atributos) + ")[k], closest: () => " + cartao + "})}"
            )

        self.definir(f'painel.disparar("click", {{target: {alvo({"data-provedor": "codex"})}}})')
        self.assertIn('<h3 class="uso-nome">Codex</h3>', self.rodar("painel.innerHTML"))
        self.definir(f'painel.disparar("click", {{target: {alvo({"data-provedor": "claude"})}}})')
        self.definir(f'painel.disparar("click", {{target: {alvo({"data-metrica": "tokens"}, "claude:84092a89")}}})')
        self.assertIn("Total dos últimos 30 dias", self.rodar("painel.innerHTML"))

        area = (
            '{closest: (s) => s === ".uso-grafico-area" ? {getBoundingClientRect: () => ({left: 100, width: 300}),'
            ' closest: () => ({getAttribute: () => "claude:84092a89"})} : null}'
        )
        self.definir(f'painel.disparar("mousemove", {{clientX: 102, target: {area}}})')
        self.assertIn("30 de ago.: $10.00", self.rodar("painel.innerHTML"))
        # o ponteiro sai do grafico e o dia escolhido fica
        self.definir('painel.disparar("mousemove", {clientX: 5, target: {closest: () => null}})')
        self.assertIn("30 de ago.: $10.00", self.rodar("painel.innerHTML"))

    def test_clicar_no_painel_nao_tira_o_foco_do_terminal(self):
        self.ligar({})
        self.assertTrue(self.rodar('(() => { const ev = {target: {}, preventDefault() { this.feito = true; }}; painel.disparar("mousedown", ev); return !!ev.feito; })()'))
        self.assertFalse(self.rodar('(() => { const ev = {target: painel, preventDefault() { this.feito = true; }}; painel.disparar("mousedown", ev); return !!ev.feito; })()'),
                         "a barra de rolagem do painel continua funcionando")


class ConfigDoPainel(ComPastaPropria):
    def test_aberto_e_largura_sao_gravados_com_a_faixa_da_tela(self):
        self.assertEqual((config.PADRAO["painelUso"], config.PADRAO["larguraPainelUso"]), (True, 310))
        self.assertEqual(config.salvar({"larguraPainelUso": 5000})["larguraPainelUso"], 480)
        self.assertEqual(config.salvar({"larguraPainelUso": 10})["larguraPainelUso"], 280)
        self.assertEqual(config.salvar({"larguraPainelUso": 352.6})["larguraPainelUso"], 352)
        config.salvar({"painelUso": False})
        self.assertIs(config.carregar()["painelUso"], False)
        # valor de outro tipo, do arquivo editado a mao, nao entra
        config.salvar({"painelUso": "nao", "larguraPainelUso": "largo"})
        lido = config.carregar()
        self.assertEqual((lido["painelUso"], lido["larguraPainelUso"]), (False, 352))


class PaginaComOPainel(unittest.TestCase):
    def test_painel_mora_a_direita_da_coluna_dos_terminais(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        coluna = html.index('<div class="coluna">')
        puxador = html.index('id="puxador-uso"')
        painel = html.index('id="uso-lateral"')
        fim_do_corpo = html.index('<div class="menu" id="menu"')
        self.assertLess(coluna, puxador)
        self.assertLess(puxador, painel)
        self.assertLess(painel, fim_do_corpo)
        self.assertIn('id="alternar-uso"', html)
        self.assertIn('id="cfg-painelUso"', html)
        self.assertIn('id="cfg-larguraPainelUso" min="280" max="480"', html)

    def test_script_antes_do_app_e_estilo_carregado(self):
        """O app.js chama o painel quando o canal responde: ele precisa existir antes."""
        html = (UI / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="([^"]+)"', html)
        self.assertLess(scripts.index("painel-uso.js"), scripts.index("app.js"))
        self.assertIn('href="painel-uso.css"', html)
        faltando = [
            alvo
            for alvo in re.findall(r'getElementById\("([^"]+)"\)', (UI / "painel-uso.js").read_text(encoding="utf-8"))
            if f'id="{alvo}"' not in html
        ]
        self.assertEqual(faltando, [])

    def test_painel_nunca_pinta_a_cor_do_fundo(self):
        """A vigia le a cor do fundo da janela como pagina que sumiu."""
        for nome in ("painel-uso.js", "painel-uso.css"):
            self.assertNotIn(vigia.COR_SENTINELA, (UI / nome).read_text(encoding="utf-8").lower(), nome)


if __name__ == "__main__":
    unittest.main()
