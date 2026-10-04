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


class RegrasDoPainel(ComPainel):
    def test_percentual_arredonda_o_meio_pro_par_e_diz_menos_de_um(self):
        casos = {58: "58%", 42.5: "42%", 43.5: "44%", 0.4: "<1%", 0: "0%", -5: "0%", 130: "100%"}
        for valor, esperado in casos.items():
            self.assertEqual(self.rodar(f"R.textoPercentual({valor})"), esperado, valor)

    def test_barra_some_abaixo_de_meio_e_enche_acima_de_99_e_meio(self):
        self.assertEqual(self.rodar("[0.4, 0.6, 58.2, 99.4, 99.6].map(R.larguraPreenchimento)"), [0, 0.6, 58.2, 99.4, 100])

    def test_nivel_segue_os_degraus_da_barra_do_topo(self):
        self.assertEqual(
            self.rodar("[0, 59.9, 60, 84.9, 85, 100].map(R.nivel)"), ["ok", "ok", "alto", "alto", "critico", "critico"]
        )

    def test_renova_em_conta_minutos_para_cima(self):
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
        self.assertEqual(self.rodar(f"R.textoAtualizado({agora - 5 * 60000 - 9000}, {agora})"), "Atualizado há 5 min")
        self.assertEqual(self.rodar(f"R.textoAtualizado({agora - 2 * 3600000 - 60000}, {agora})"), "Atualizado há 2 h")
        velho = local("2026-09-26T18:02:00-03:00")
        self.assertEqual(
            self.rodar(f'R.textoAtualizado({ms("2026-09-26T18:02:00-03:00")}, {agora})'),
            f"Atualizado em {velho.day} de {MESES[velho.month - 1]}, {velho:%H:%M}",
        )

    def ritmo(self, usado: float, falta_min: float, duracao: int, contexto: str, folga: bool = False):
        r = f"R.ritmo({{usado: {usado}, janelaMin: {duracao}, zeraEmMs: {falta_min * 60000}}}, 0, {duracao})"
        return self.rodar(
            f"(() => {{ const r = {r}; if (!r) return null; return {{visivel: R.ritmoVisivel(r, '{contexto}'),"
            f" texto: R.textoRitmo(r), previsao: R.textoPrevisao(r, 0, {json.dumps(folga)}), marca: R.marcaDoRitmo(r)}}; }})()"
        )

    def test_ritmo_abaixo_nao_ganha_previsao(self):
        """Metade da janela passou com 44% gasto: 6 pontos abaixo, e abaixo ja diz que sobra."""
        r = self.ritmo(44, 150, 300, "sessao")
        self.assertEqual(r["texto"], "6% abaixo do ritmo")
        self.assertEqual(r["previsao"], "")
        self.assertTrue(r["visivel"])
        self.assertEqual(r["marca"], {"p": 50, "lado": "reserva"})

    def test_ritmo_acima_diz_quando_acaba_antes_de_renovar(self):
        """Metade da semana com 70% gasto: 20 pontos acima, e no passo de agora acaba em 36 horas."""
        r = self.ritmo(70, 5040, 10080, "semanal")
        self.assertEqual(r["texto"], "20% acima do ritmo")
        self.assertEqual(r["previsao"], "No passo de agora, acaba em 1d 12h, antes de renovar")
        self.assertEqual(r["marca"], {"p": 50, "lado": "deficit"})
        self.assertEqual(
            self.ritmo(70, 150, 300, "sessao")["previsao"], "No passo de agora, acaba em 1h 5m, antes de renovar"
        )

    def test_no_ritmo(self):
        r = self.ritmo(49, 150, 300, "sessao")
        self.assertEqual(r["texto"], "No ritmo")
        self.assertEqual(r["marca"]["lado"], "no-ritmo")

    def test_ritmo_some_no_comeco_da_janela_e_sem_sobra(self):
        self.assertFalse(self.ritmo(1, 295, 300, "sessao")["visivel"], "esperado abaixo de 3 pontos")
        self.assertFalse(self.ritmo(100, 150, 300, "sessao")["visivel"], "sem nada restante")
        self.assertIsNone(self.ritmo(10, 400, 300, "sessao"), "zera depois do fim da janela")
        self.assertIsNone(self.ritmo(10, -5, 300, "sessao"), "ja zerou")

    def test_folga_so_no_codex_e_so_com_folga_grande(self):
        self.assertEqual(self.ritmo(10, 150, 300, "sessao", folga=True)["previsao"], "Sobra até renovar, com folga de 1,5×")
        self.assertEqual(self.ritmo(10, 150, 300, "sessao")["previsao"], "")

    def test_tokens_em_portugues_sobem_de_unidade_onde_a_de_baixo_viraria_mil(self):
        casos = {999: "999", 1000: "1 mil", 1250: "1,2 mil", 1200: "1,2 mil", 999499: "999 mil", 999500: "1 mi",
                 1100000: "1,1 mi", 27000000: "27 mi", 45500000: "46 mi", 1500000000: "1,5 bi", -3000: "-3 mil", 0: "0"}
        for valor, esperado in casos.items():
            self.assertEqual(self.rodar(f"R.textoTokens({valor})"), esperado, valor)

    def test_moeda_em_dolar_no_formato_daqui(self):
        casos = {
            "R.textoMoeda(54.72)": "US$ 54,72",
            "R.textoMoeda(-3.1)": "-US$ 3,10",
            "R.textoMoeda(1380.5)": "US$ 1.380,50",
            "R.textoMoeda(1234567)": "US$ 1.234.567,00",
            "R.textoMoedaCompacta(12.4)": "US$ 12",
            "R.textoMoedaCompacta(1380.5)": "US$ 1.381",
            "R.textoMoedaCompacta(12345)": "US$ 12 mil",
            "R.textoMoedaCompacta(2500000)": "US$ 2,5 mi",
            "R.textoMoedaCompacta(0.42)": "US$ 0,42",
            "R.textoMoedaCompacta(0)": "US$ 0",
        }
        for expressao, esperado in casos.items():
            self.assertEqual(self.rodar(expressao), esperado, expressao)

    def test_eixo_cai_em_numero_limpo_e_as_barras_seguem_o_topo(self):
        self.assertEqual(self.rodar("R.marcasEixoY(1072)"), [0, 600, 1200])
        self.assertEqual(self.rodar("R.marcasEixoY(40)"), [0, 20, 40])
        self.assertEqual(self.rodar("R.marcasEixoY(50)"), [0, 25, 50])
        self.assertEqual(self.rodar("R.marcasEixoY(0)"), [])
        self.assertEqual(self.rodar("R.alturasDasBarras([1, 4, 2, 4], 8)"), [12.5, 50, 25, 50])
        self.assertEqual(self.rodar("R.alturasDasBarras([3], 0)"), [0])

    def test_esforco_e_ordinal_num_matiz_so_e_o_fora_da_escala_fica_neutro(self):
        tons = self.rodar('["minimal", "low", "medium", "high", "xhigh", "max", "ultra"].map(R.tomDoEsforco)')
        self.assertTrue(all("var(--marca)" in tom for tom in tons), "um matiz so, o do provedor")
        # max e ultra sao o teto de cada ferramenta e dividem o degrau de cima; o resto tem o seu
        self.assertEqual(tons[5], tons[6])
        self.assertEqual(len(set(tons[:6])), 6)
        # os de baixo apagam pro fundo e os de cima clareiam pro branco, na ordem
        fundo = [int(re.search(r"(\d+)%, var\(--uso-fundo\)", tom).group(1)) for tom in tons[:3]]
        branco = [int(re.search(r"#ffffff (\d+)%", tom).group(1)) for tom in tons[3:6]]
        self.assertEqual(fundo, sorted(fundo))
        self.assertEqual(branco, sorted(branco))
        self.assertIsNone(self.rodar('R.tomDoEsforco("sem registro")'))
        self.assertIsNone(self.rodar('R.tomDoEsforco("<b>")'))
        self.assertIsNone(self.rodar('R.tomDoEsforco("toString")'), "nome de propriedade do objeto nao vira degrau")

    def test_custo_do_periodo_nao_vira_zero_quando_falta_preco(self):
        casos = {
            "R.textoCustoDoPeriodo(42.1, 1000, false)": "US$ 42,10",
            "R.textoCustoDoPeriodo(0, 0, false)": "US$ 0,00",
            "R.textoCustoDoPeriodo(0, 0, true)": "US$ 0,00",
            "R.textoCustoDoPeriodo(0, 5000, true)": "Sem preço",
            "R.textoCustoDoPeriodo(12.5, 5000, true)": "≥ US$ 12,50",
            "R.textoCustoDoPeriodo(null, 5000, true)": "--",
        }
        for expressao, esperado in casos.items():
            self.assertEqual(self.rodar(expressao), esperado, expressao)

    def test_pior_nivel_da_conta_vem_da_janela_mais_cheia(self):
        self.assertEqual(self.rodar('R.piorNivel({janelas: [{tipo: "5h", usado: 10}, {tipo: "7d", usado: 70}]})'), "alto")
        self.assertEqual(self.rodar('R.piorNivel({janelas: [{tipo: "5h", usado: 90}, {tipo: "7d", usado: 70}]})'), "critico")
        self.assertEqual(self.rodar('R.piorNivel({janelas: [{tipo: "5h", usado: null}]})'), "ok")
        self.assertEqual(self.rodar("R.piorNivel({})"), "ok")

class DesenhoDoContrato(ComPainel):
    def test_claude_do_contrato(self):
        html = self.desenho(CONTRATO)
        fim_do_bloco = f"{local('2026-09-28T22:00:00-03:00'):%H:%M}"
        zera = local("2026-09-29T00:40:00-03:00")
        cartao = html[html.index('<section class="uso-cartao"'):]
        for trecho in (
            '<span class="uso-avatar" aria-hidden="true"><svg viewBox="0 0 100 100"><path d="M25.7146',
            '<h3 class="uso-nome" title="Principal">Principal</h3>',
            '<span class="uso-email">Claude</span>',
            '<span class="uso-plano">Max</span>',
            '<p class="uso-atualizado">Atualizado há 1 min</p>',
            '<span class="uso-janela-titulo">Sessão de 5 horas</span></div>',
            '<span class="uso-janela-valor"><b>12%</b> usado</span>',
            f'<span class="uso-janela-zera" title="Renova em {zera.day} de {MESES[zera.month - 1]} às {zera:%H:%M}">',
            "</svg>Renova em 3h 10m</span>",
            '<b class="uso-medidor-cheio" style="width:12%"></b>',
            '<i class="uso-medidor-ritmo" style="--p:36.67%"',
            '<span class="uso-ritmo reserva"',
            "</svg>25% abaixo do ritmo</span>",
            '<span class="uso-janela-titulo">Semana</span>',
            "<b>16%</b> usado",
            "26% abaixo do ritmo",
            "Renova em 4d 1h",
            '<span class="uso-kpi-rotulo">Hoje</span><b class="uso-kpi-valor">US$ 42,10</b><span class="uso-kpi-sub">1,1 mi tokens</span>',
            '<span class="uso-kpi-rotulo">Últimos 30 dias</span><b class="uso-kpi-valor">US$ 1.381</b><span class="uso-kpi-sub">27 mi tokens</span>',
            '<span class="uso-kpi-rotulo">Bloco de 5h atual</span><b class="uso-kpi-valor">US$ 38,20</b>',
            f"Projeção de US$ 71,00 até {fim_do_bloco} · 6,1 mil tokens/min",
            '<p class="uso-nota">Preço de API calculado pelos logs locais. Não é a fatura.</p>',
            "<span>US$ 40</span><span>US$ 20</span><span>US$ 0</span>",
            "<small>30 de ago.</small><strong>US$ 40,00</strong><span>900 mil tokens</span>",
            "<li><span>Opus 5.5</span><em>US$ 40,00</em></li>",
            "US$ 40,00 em 30 de ago.",
        ):
            self.assertIn(trecho, cartao)
        self.assertEqual(cartao.count('class="uso-janela nivel-ok"'), 2)
        self.assertNotIn("uso-estado", cartao, "nenhuma janela esta alta: nada de rotulo de estado")
        self.assertNotIn("uso-selo", html, "tudo veio do limite da propria ferramenta")
        self.assertNotIn("uso-aviso", html)
        self.assertNotIn("uso-janela-alerta", html, "abaixo do ritmo nao ganha previsao")
        # a ordem da pergunta: limite, custo, como andou, e so depois o detalhe
        ordem = [cartao.index(s) for s in ("uso-limites", "uso-custo", "uso-grafico")]
        self.assertEqual(ordem, sorted(ordem))

    def test_resumo_poe_as_janelas_de_todas_as_contas_lado_a_lado(self):
        html = self.desenho(CONTRATO)
        resumo = html[:html.index('<section class="uso-cartao"')]
        linhas = re.findall(r'<button class="uso-linha( ativo)?"[^>]*data-ferramenta="(\w+)" data-escolher-conta="([^"]+)"', resumo)
        self.assertEqual(linhas, [(" ativo", "claude", "claude:84092a89"), ("", "codex", "codex:padrao")])
        # duas colunas fixas, 5h e semana, mesmo na conta que nao tem a de 5h
        self.assertEqual(
            re.findall(r'<span class="uso-mini( vazio| nivel-\w+)"><span class="uso-mini-rotulo">([^<]+)</span>', resumo),
            [(" nivel-ok", "5h"), (" nivel-ok", "Semana"), (" vazio", "5h"), (" nivel-critico", "Semana")],
        )
        self.assertEqual(re.findall(r'<span class="uso-mini-valor">([^<]+)</span>', resumo), ["12%", "16%", "--", "85%"])
        self.assertIn('<b style="width:85%"></b>', resumo)
        # o estado da conta vai escrito, pela janela mais cheia
        self.assertIn('<span class="uso-linha-nome">Codex</span><span class="uso-estado critico">No limite</span>', resumo)
        self.assertIn('aria-pressed="true"', resumo)
        self.assertIn('title="Codex: Codex · Semana: 85% usado"', resumo, "a dica diz de que janela e cada numero")

    def test_uma_conta_so_dispensa_o_resumo(self):
        dados = contrato()
        dados["contas"] = dados["contas"][:1]
        html = self.desenho(dados)
        self.assertNotIn("uso-resumo", html)
        self.assertTrue(html.startswith('<section class="uso-cartao"'))

    def test_linha_da_conta_com_erro_avisa_no_resumo(self):
        dados = contrato()
        dados["contas"][1]["erro"] = "Falhou <x>"
        html = self.desenho(dados)
        self.assertIn('<span class="uso-linha-erro" title="Falhou &#60;x&#62;"><svg class="uso-linha-erro-icone"', html)
        self.assertIn('</svg>Erro</span><span class="uso-estado critico">No limite</span>', html)

    def test_codex_do_contrato(self):
        html = self.desenho(CONTRATO, {"provedor": "codex"})
        velho = local("2026-09-26T18:02:00-03:00")
        cartao = html[html.index('<section class="uso-cartao"'):]
        for trecho in (
            '<section class="uso-cartao" style="--marca:#2AA6B8" data-conta="codex:padrao">',
            '<span class="uso-avatar" aria-hidden="true"><svg viewBox="0 0 100 100"><path d="M83.7733',
            '<h3 class="uso-nome" title="Codex">Codex</h3>',
            f"Atualizado em {velho.day} de {MESES[velho.month - 1]}, {velho:%H:%M}</p>",
            '<span class="uso-plano">Pro</span>',
            '<div class="uso-janela nivel-critico" data-tipo="7d">',
            '<span class="uso-janela-titulo">Semana</span><span class="uso-estado critico">No limite</span>',
            "<b>85%</b> usado",
            "</svg>No ritmo</span>",
            '<p class="uso-janela-alerta"><svg class="uso-alerta-icone"',
            "</svg>No passo de agora, acaba em 1d 59m, antes de renovar</p>",
            "Renova em 1d 2h",
            '<b class="uso-kpi-valor">US$ 0,00</b><span class="uso-kpi-sub">0 tokens</span>',
            '<b class="uso-kpi-valor">≥ US$ 210</b><span class="uso-kpi-sub">5,4 mi tokens</span>',
            "Há modelo sem preço na tabela",
            "Nenhum uso nos últimos 30 dias.",
        ):
            self.assertIn(trecho, cartao)
        self.assertNotIn("Bloco", html)
        self.assertNotIn('class="uso-email"', html, "a conta chamada Codex nao repete o nome do provedor")
        self.assertIn('<button class="uso-linha ativo" aria-pressed="true" data-ferramenta="codex"', html)

    def test_codex_com_modelo_sem_preco_nao_mostra_zero_de_graca(self):
        dados = contrato()
        codex = dados["contas"][1]
        codex["tokens"]["hoje"] = 665000000
        codex["bloco"] = {"inicio": "2026-09-28T21:00:00-03:00", "fim": "2026-09-29T02:00:00-03:00", "tokens": 50000000,
                          "custo": 0.0, "porMinuto": 824856.9, "projecaoCusto": 0.0, "estimado": True}
        html = self.desenho(dados, {"provedor": "codex"})
        self.assertIn('<span class="uso-kpi-rotulo">Hoje</span><b class="uso-kpi-valor">Sem preço</b><span class="uso-kpi-sub">665 mi tokens</span>', html)
        self.assertIn('<span class="uso-kpi-rotulo">Bloco de 5h, estimado</span><b class="uso-kpi-valor">Sem preço</b>', html)
        self.assertIn('<span class="uso-kpi-sub">825 mil tokens/min</span>', html)
        self.assertNotIn("Projeção de US$ 0,00", html, "projecao de zero com modelo sem preco enganaria")
        self.assertNotIn("US$ 0,00</b>", html)

    def test_grafico_de_30_dias_destaca_hoje_e_tem_o_detalhe_de_cada_dia(self):
        dados = contrato()
        dados["contas"][0]["serie"] = serie_de_30()
        html = self.desenho(dados)
        colunas = re.findall(r'<div class="uso-coluna([^"]*)" style="--h:([\d.]+)%"', html)
        self.assertEqual(len(colunas), 30)
        self.assertEqual([c for c, _ in colunas].count(" hoje dir"), 1, "so o ultimo dia e hoje")
        self.assertTrue(colunas[0][0].endswith("esq") and colunas[15][0] == "", "a dica abre pra dentro nas pontas")
        # o maior dia vale 90 e o topo do eixo e 100: a barra dele ocupa 90% da altura
        self.assertEqual(colunas[21][1], "90")
        self.assertEqual(html.count('class="uso-dica" role="tooltip"'), 30)
        self.assertIn("<span>30 de ago.</span><span>Hoje</span>", html)
        self.assertIn("<span>US$ 100</span><span>US$ 50</span><span>US$ 0</span>", html)
        # tres modelos do mais caro pro mais barato no detalhe de hoje
        hoje = html[html.index('class="uso-coluna hoje dir"'):]
        self.assertEqual(
            re.findall(r"<li><span>([^<]+)</span><em>([^<]+)</em></li>", hoje)[:3],
            [("Opus 5.5", "US$ 30,00"), ("Haiku 4.5", "US$ 6,00"), ("Sonnet 4.5", "US$ 3,00")],
        )
        self.assertIn("US$ 794,00 em 30 dias · média de US$ 26,47 por dia", html)
        self.assertIn('aria-label="28 de set.: US$ 39,00"', html, "o teclado le o mesmo que o mouse")

        em_tokens = self.desenho(dados, {"metricas": {"claude:84092a89": "tokens"}})
        self.assertIn("<span>3 mi</span><span>1,5 mi</span><span>0</span>", em_tokens)
        self.assertIn('class="ativo" data-metrica="tokens"', em_tokens)
        # 46,5 milhoes arredonda pro par, como o %.0f do CodexBar
        self.assertIn("46 mi tokens em 30 dias", em_tokens)

    def test_detalhe_junta_os_modelos_depois_do_quarto(self):
        dados = contrato()
        dados["contas"][0]["serie"][0]["porModelo"] = {f"claude-m{i}": float(i) for i in range(1, 7)}
        html = self.desenho(dados)
        self.assertEqual(html.count("<li>"), 5)
        self.assertIn("<li><span>Outros 2</span><em>US$ 3,00</em></li>", html)

    def test_tabela_gemea_do_grafico_abre_pela_vista(self):
        dados = contrato()
        dados["contas"][0]["serie"] = serie_de_30()
        fechada = self.desenho(dados)
        self.assertIn('<details class="uso-detalhes"><summary data-abrir="tabela|claude:84092a89">', fechada)
        self.assertIn("</svg>Ver os dias em tabela</summary>", fechada)
        self.assertEqual(fechada.count("<tr><td>"), 30)
        self.assertLess(fechada.index("<td>28 de set.</td>"), fechada.index("<td>30 de ago.</td>"), "o dia mais novo vem primeiro")
        aberta = self.desenho(dados, {"abertos": {"tabela|claude:84092a89": True}})
        self.assertIn('<details class="uso-detalhes" open><summary data-abrir="tabela|claude:84092a89">', aberta)
        self.assertIn("</svg>Esconder a tabela</summary>", aberta)

    def test_selo_quando_o_limite_e_estimado(self):
        dados = contrato()
        dados["contas"][0]["janelas"][0]["origem"] = "estimada"
        html = self.desenho(dados)
        sessao = re.search(r'data-tipo="5h">(.*?)<div class="uso-medidor', html).group(1)
        semana = re.search(r'data-tipo="7d">(.*?)<div class="uso-medidor', html).group(1)
        self.assertIn('class="uso-selo"', sessao)
        self.assertNotIn("uso-selo", semana)

    def test_linha_da_conta_acende_com_a_janela_alta_e_diz_quando_nao_tem_leitura(self):
        dados = contrato()
        outra = json.loads(json.dumps(dados["contas"][0]))
        outra.update(id="claude:trab", nome="Trabalho")
        outra["janelas"][1]["usado"] = 91.0
        sem = json.loads(json.dumps(dados["contas"][0]))
        sem.update(id="claude:velha", nome="Velha", janelas=[])
        dados["contas"][1:1] = [outra, sem]
        html = self.desenho(dados)
        self.assertIn('<span class="uso-linha-nome">Trabalho</span><span class="uso-estado critico">No limite</span>', html)
        self.assertIn('<span class="uso-mini nivel-critico"><span class="uso-mini-rotulo">Semana</span>', html)
        self.assertIn(
            '<span class="uso-linha-nome">Velha</span></span><span class="uso-linha-janelas"><span class="uso-linha-sem">Sem leitura de limite</span>',
            html,
            "conta sem leitura de limite nao inventa numero",
        )

    def test_uso_alto_e_critico_tem_rotulo_escrito(self):
        dados = contrato()
        dados["contas"][0]["janelas"][0]["usado"] = 91.0
        dados["contas"][0]["janelas"][1]["usado"] = 67.0
        html = self.desenho(dados)
        self.assertIn('<div class="uso-janela nivel-critico" data-tipo="5h">', html)
        self.assertIn('Sessão de 5 horas</span><span class="uso-estado critico">No limite</span>', html)
        self.assertIn('<div class="uso-janela nivel-alto" data-tipo="7d">', html)
        self.assertIn('Semana</span><span class="uso-estado alto">Alto</span>', html)
        self.assertIn('<span class="uso-linha-nome">Principal</span><span class="uso-estado critico">No limite</span>', html)

    def test_aviso_de_preco_faltando_e_de_custo_aproximado(self):
        dados = contrato()
        dados["contas"][0]["custo"].update(semPreco=True, aproximado=True)
        html = self.desenho(dados)
        avisos = re.findall(r'<p class="uso-aviso">([^<]+)</p>', html)
        self.assertEqual(len(avisos), 2)
        self.assertIn("sem preço", avisos[0])
        self.assertIn("aproximado", avisos[1])

    def test_varias_contas_do_mesmo_provedor_entram_no_resumo(self):
        dados = contrato()
        email = "alguem.com.um.nome.longo@empresa-grande.com.br"
        outra = json.loads(json.dumps(dados["contas"][0]))
        outra.update(id="claude:trab", nome="", email=email)
        outra["janelas"][0]["usado"] = 70.0
        dados["contas"].insert(1, outra)
        html = self.desenho(dados)
        self.assertEqual(len(re.findall(r'<button class="uso-linha[ "]', html)), 3)
        # e-mail corta no meio: o comeco e o dominio e que dizem de quem e
        self.assertIn('<span class="uso-linha-nome">' + email[:12] + "…" + email[-11:] + "</span>", html)
        self.assertIn('<span class="uso-mini nivel-alto"><span class="uso-mini-rotulo">5h</span>', html)
        self.assertIn("Sessão de 5 horas: 70% usado · Semana: 16% usado", html, "a dica da linha diz de que janela e o numero")

        trab = self.desenho(dados, {"provedor": "claude", "contas": {"claude": "claude:trab"}})
        self.assertIn('data-conta="claude:trab"', trab)
        self.assertIn('<button class="uso-linha ativo" aria-pressed="true" data-ferramenta="claude" data-escolher-conta="claude:trab"', trab)
        self.assertIn("<b>70%</b> usado", trab)
        self.assertIn(">" + email[:16] + "…" + email[-15:] + "</span>", trab)

        # a conta escolhida de cada provedor fica lembrada ao trocar de provedor
        vista = {"provedor": "codex", "contas": {"claude": "claude:trab"}}
        self.assertIn('data-conta="codex:padrao"', self.desenho(dados, vista))

class EstadosDoPainel(ComPainel):
    def test_antes_da_primeira_coleta_diz_que_esta_lendo(self):
        html = self.desenho({"geradoEm": None, "coletando": True, "contas": []})
        self.assertIn(
            '<i class="uso-giro ja" aria-hidden="true"></i><h3 class="uso-vazio-titulo">Lendo o uso das contas</h3>', html
        )
        self.assertNotIn("Ainda sem uso", html)
        self.assertIn("Lendo o uso das contas", self.rodar(f"P.html(null, {{}}, {ms(AGORA)})"))

    def test_sem_conta_nenhuma_diz_que_ainda_nao_ha_uso(self):
        html = self.desenho({"geradoEm": "2026-09-28T21:29:00-03:00", "coletando": False, "contas": []})
        self.assertIn('<h3 class="uso-vazio-titulo">Ainda sem uso</h3>', html)
        self.assertIn("Atualizado há 1 min.", html)
        self.assertNotIn("uso-giro", html)

    def test_coletando_com_dado_mostra_o_dado_e_so_o_giro(self):
        html = self.desenho(contrato(coletando=True))
        self.assertIn(
            '<p class="uso-atualizado">Atualizado há 1 min<i class="uso-giro" aria-label="Atualizando"></i></p>', html
        )
        self.assertIn("<b>12%</b> usado", html, "o dado de antes continua a vista")
        self.assertNotIn("Lendo o uso", html)

    def test_conta_so_com_erro_mostra_o_erro_e_o_botao_de_copiar_sem_corpo(self):
        dados = contrato()
        conta = dados["contas"][0]
        conta.update(janelas=[], bloco=None, serie=[], erro="Falhou ao ler <status line>")
        conta["custo"].update(hoje=0, dias30=0)
        conta["tokens"].update(hoje=0, dias30=0)
        html = self.desenho(dados)
        self.assertIn(
            '<p class="uso-erro-texto" title="Falhou ao ler &#60;status line&#62;">Falhou ao ler &#60;status line&#62;</p>', html
        )
        self.assertIn("data-copiar", html)
        self.assertTrue(html.endswith("</p></section>"), "so o cabecalho e o erro, sem corpo")
        self.assertNotIn("Limites", html)

    def test_erro_com_dado_mostra_os_dois(self):
        dados = contrato()
        dados["contas"][0]["erro"] = "Leitura antiga"
        html = self.desenho(dados)
        self.assertIn('class="uso-erro" role="alert"', html)
        self.assertIn("<b>12%</b> usado", html)

    def test_conta_sem_limite_lido_mas_com_custo(self):
        dados = contrato()
        dados["contas"][0]["janelas"] = []
        html = self.desenho(dados)
        self.assertIn("Sem leitura de limite nesta conta.", html)
        self.assertIn('<b class="uso-kpi-valor">US$ 42,10</b>', html)

    def test_janela_sem_numero_diz_sem_leitura(self):
        dados = contrato()
        dados["contas"][0]["janelas"][0]["usado"] = None
        html = self.desenho(dados)
        self.assertIn('<div class="uso-janela sem-leitura" data-tipo="5h">', html)
        self.assertIn('<span class="uso-janela-valor">Sem leitura</span>', html)
        self.assertIn("Renova em 3h 10m", html)
        self.assertIn('<div class="uso-medidor vazio" aria-hidden="true"></div>', html)
        self.assertIn('<span class="uso-mini vazio"><span class="uso-mini-rotulo">5h</span>', html)

    def test_janela_do_mes_e_outras_vem_depois_da_semana(self):
        dados = contrato()
        janelas = dados["contas"][1]["janelas"]
        janelas.insert(0, {"tipo": "30d", "rotulo": "Janela de 30d", "usado": 40.0, "zeraEm": "2026-10-20T00:00:00-03:00",
                           "duracaoMin": 43200, "origem": "real", "lidoEm": None})
        janelas.insert(0, {"tipo": "1d", "rotulo": "Janela de 1d", "usado": 10.0, "zeraEm": None,
                           "duracaoMin": 1440, "origem": "real", "lidoEm": None})
        html = self.desenho(dados, {"provedor": "codex"})
        titulos = re.findall(r'<span class="uso-janela-titulo">([^<]+)</span>', html)
        self.assertEqual(titulos, ["Semana", "Janela de 1d", "Mês"])

    def test_relatorio_ilegivel_nao_derruba(self):
        self.assertIn("Não deu pra ler o relatório de uso", self.desenho("{quebrado"))
        self.assertIn("Não deu pra ler o relatório de uso", self.desenho("[1, 2]"))

    def test_nome_da_conta_nao_vira_html(self):
        dados = contrato()
        dados["contas"][0]["nome"] = '<img src=x onerror="a()">'
        dados["contas"][0]["plano"] = "<b>max</b>"
        html = self.desenho(dados)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>Max", html)
        self.assertIn("&#60;img src=x", html)


ABERTO = {"painelUso": True}


class LayoutDoPainel(ComPainel):
    def ligar(self, cfg: dict, com_relatorio: bool = True, resposta=None) -> None:
        resposta = json.dumps(resposta or {"geradoEm": None, "coletando": True, "contas": []})
        self.definir(
            f"estado.cfg = {json.dumps(cfg)}; estado.bridge = ponteFalsa({json.dumps(com_relatorio)}, {json.dumps(resposta)});"
            " var aba = abaFalsa(); estado.abaAtiva = aba; P.ligar();"
            ' var painel = elementosFalsos["uso-lateral"], puxador = elementosFalsos["puxador-uso"],'
            ' botao = elementosFalsos["alternar-uso"];'
        )

    def test_nasce_fechado_e_nao_pede_relatorio(self):
        self.ligar({})
        self.assertEqual(self.rodar("[painel.hidden, puxador.hidden]"), [True, True])
        self.assertEqual(self.rodar('[botao.classList.contains("ativo"), botao.getAttribute("aria-pressed")]'), [False, "false"])
        self.assertEqual(self.rodar("estado.bridge.pedidos"), 0, "fechado nao custa leitura nenhuma")

    def test_aberto_pela_pessoa_volta_aberto_na_largura_do_codexbar(self):
        self.ligar(ABERTO)
        self.assertEqual(self.rodar("[painel.hidden, puxador.hidden]"), [False, False])
        self.assertEqual(self.rodar('painel.estilo["--largura-uso"]'), "310px")
        self.assertEqual(self.rodar('[botao.classList.contains("ativo"), botao.getAttribute("aria-pressed")]'), [True, "true"])
        self.assertEqual(self.rodar("estado.bridge.pedidos"), 1)
        self.assertIn("Lendo o uso das contas", self.rodar("painel.innerHTML"))

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
        self.ligar(ABERTO)
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
        self.ligar(ABERTO)
        impedido = self.rodar('(() => { const ev = {preventDefault() { this.feito = true; }}; botao.disparar("mousedown", ev); return !!ev.feito; })()')
        self.assertTrue(impedido)
        self.definir('botao.disparar("click", {})')
        self.assertEqual(self.rodar("[painel.hidden, estado.bridge.gravados]"), [True, [{"painelUso": False}]])

    def test_na_grade_as_colunas_sao_refeitas(self):
        self.ligar(ABERTO)
        self.definir("var grades = 0; reencaixarGrade = function () { grades++; }; estado.emGrade = true; P.alternar(false)")
        self.assertEqual(self.rodar("[grades, aba.ajustes]"), [1, 0])

    def test_arrastar_a_alca_para_nas_bordas_e_grava_ao_soltar(self):
        self.ligar(ABERTO)
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
        self.ligar(ABERTO)
        self.definir("estado.cfg.painelUso = false; P.aplicar()")
        self.assertTrue(self.rodar("painel.hidden"))
        self.definir("estado.cfg.painelUso = true; estado.cfg.larguraPainelUso = 300; P.aplicar()")
        self.assertEqual(self.rodar('[painel.hidden, painel.estilo["--largura-uso"]]'), [False, "300px"])
        self.assertEqual(self.rodar("aba.ajustes"), 2)
        self.definir("P.aplicar()")
        self.assertEqual(self.rodar("aba.ajustes"), 2, "sem mudanca o terminal nao e remedido")
        self.assertEqual(self.rodar("estado.bridge.gravados"), [], "quem grava e a tela de configuracoes")
        # a largura e o aberto sairam da tela de configuracoes: o painel cuida dos dois sozinho
        self.assertEqual([c for c in self.rodar("estado.campos") if "ainel" in c], [])

    def test_relatorio_novo_pelo_sinal_redesenha(self):
        self.ligar(ABERTO)
        self.definir(f"estado.bridge.pronto({json.dumps(json.dumps(CONTRATO))})")
        html = self.rodar("painel.innerHTML")
        self.assertIn('<h3 class="uso-nome" title="Principal">Principal</h3>', html)
        self.assertIn("<b>12%</b> usado", html)

    def test_app_sem_o_relatorio_mostra_o_vazio(self):
        self.ligar(ABERTO, com_relatorio=False)
        self.assertIn("Ainda sem uso", self.rodar("painel.innerHTML"))

    def test_cliques_trocam_conta_metrica_e_abrem_a_tabela(self):
        dados = contrato()
        dados["contas"][0]["serie"] = serie_de_30()
        self.ligar(ABERTO, resposta=dados)

        def alvo(atributos: dict, conta: str = "") -> str:
            cartao = "({getAttribute: () => " + json.dumps(conta) + "})"
            return (
                "{closest: (s) => ({hasAttribute: (k) => k in " + json.dumps(atributos) + ","
                " getAttribute: (k) => (" + json.dumps(atributos) + ")[k], closest: () => " + cartao + "})}"
            )

        # um clique na linha do resumo troca o provedor e a conta juntos
        codex = {"data-escolher-conta": "codex:padrao", "data-ferramenta": "codex"}
        self.definir(f'painel.disparar("click", {{target: {alvo(codex)}}})')
        self.assertIn('<h3 class="uso-nome" title="Codex">Codex</h3>', self.rodar("painel.innerHTML"))
        claude = {"data-escolher-conta": "claude:84092a89", "data-ferramenta": "claude"}
        self.definir(f'painel.disparar("click", {{target: {alvo(claude)}}})')
        self.assertIn('<h3 class="uso-nome" title="Principal">Principal</h3>', self.rodar("painel.innerHTML"))
        # a aba de provedor de antes continua valendo pra quem a chama
        self.definir(f'painel.disparar("click", {{target: {alvo({"data-provedor": "codex"})}}})')
        self.assertIn('data-conta="codex:padrao"', self.rodar("painel.innerHTML"))
        self.definir(f'painel.disparar("click", {{target: {alvo(claude)}}})')
        self.definir(f'painel.disparar("click", {{target: {alvo({"data-metrica": "tokens"}, "claude:84092a89")}}})')
        self.assertIn("mi tokens em 30 dias", self.rodar("painel.innerHTML"))

        # a tabela abre pelo painel, e o clique no resumo nao deixa o navegador abrir e fechar por conta
        abrir = {"data-abrir": "tabela|claude:84092a89"}
        impedido = self.rodar(
            f'(() => {{ const ev = {{target: {alvo(abrir)}, preventDefault() {{ this.feito = true; }}}};'
            ' painel.disparar("click", ev); return !!ev.feito; })()'
        )
        self.assertTrue(impedido)
        self.assertIn('<details class="uso-detalhes" open><summary data-abrir="tabela|claude:84092a89">', self.rodar("painel.innerHTML"))
        self.definir(f'painel.disparar("click", {{target: {alvo(abrir)}, preventDefault() {{}}}})')
        self.assertNotIn('uso-detalhes" open', self.rodar("painel.innerHTML"), "o segundo clique fecha")

    def test_mostrar_conta_abre_no_provedor_e_na_conta_pedidos(self):
        self.ligar({}, resposta=contrato())
        self.definir('P.mostrarConta("codex:padrao")')
        self.assertFalse(self.rodar("painel.hidden"))
        self.definir(f"estado.bridge.pronto({json.dumps(json.dumps(CONTRATO))})")
        self.assertIn('data-conta="codex:padrao"', self.rodar("painel.innerHTML"))

    def test_clicar_no_painel_nao_tira_o_foco_do_terminal(self):
        self.ligar(ABERTO)
        self.assertTrue(self.rodar('(() => { const ev = {target: {}, preventDefault() { this.feito = true; }}; painel.disparar("mousedown", ev); return !!ev.feito; })()'))
        self.assertFalse(self.rodar('(() => { const ev = {target: painel, preventDefault() { this.feito = true; }}; painel.disparar("mousedown", ev); return !!ev.feito; })()'),
                         "a barra de rolagem do painel continua funcionando")


class ConfigDoPainel(ComPastaPropria):
    def test_aberto_e_largura_sao_gravados_com_a_faixa_da_tela(self):
        self.assertEqual((config.PADRAO["painelUso"], config.PADRAO["larguraPainelUso"]), (False, 310))
        self.assertEqual(config.salvar({"larguraPainelUso": 5000})["larguraPainelUso"], 480)
        self.assertEqual(config.salvar({"larguraPainelUso": 10})["larguraPainelUso"], 280)
        self.assertEqual(config.salvar({"larguraPainelUso": 352.6})["larguraPainelUso"], 352)
        config.salvar({"painelUso": True})
        self.assertIs(config.carregar()["painelUso"], True, "aberto pela pessoa, fica aberto")
        # valor de outro tipo, do arquivo editado a mao, nao entra
        config.salvar({"painelUso": "nao", "larguraPainelUso": "largo"})
        lido = config.carregar()
        self.assertEqual((lido["painelUso"], lido["larguraPainelUso"]), (True, 352))


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
        # fechado desde o HTML: sem isso o painel aparecia vazio por um instante e o terminal remedia
        self.assertIn('id="puxador-uso" title="Arraste pra mudar a largura do painel de uso" hidden', html)
        self.assertIn('id="uso-lateral" aria-label="Uso de cada conta" hidden', html)
        self.assertRegex(html, r'<button class="[^"]+" id="alternar-uso"')

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

    def test_tom_apagado_nasce_onde_a_cor_do_provedor_e_definida(self):
        """Calculado no painel inteiro, o tom apagado usava o acento do Loom: barras e trilho azuis."""
        css = (UI / "painel-uso.css").read_text(encoding="utf-8")

        def bloco(seletor: str) -> str:
            inicio = css.index(seletor + " {")
            return css[inicio:css.index("}", inicio)]

        for variavel in ("--marca-apagada", "--marca-trilho"):
            self.assertNotIn(variavel + ":", bloco(".uso-lateral"), variavel)
            self.assertIn(variavel + ":", bloco(".uso-cartao"), variavel)
        self.assertIn('style="--marca:', (UI / "painel-uso.js").read_text(encoding="utf-8"))

    def test_icone_e_svg_e_nunca_um_caractere(self):
        """Seta e triangulo de texto mudam de tamanho com a fonte e somem em fonte sem o glifo."""
        for nome in ("painel-uso.js", "painel-uso.css"):
            texto = (UI / nome).read_text(encoding="utf-8")
            for glifo in "▲▼●▸▾►◄✓✔⚠":
                self.assertNotIn(glifo, texto, f"{glifo} em {nome}")

    def test_classe_nova_do_painel_comeca_com_uso(self):
        """A barra de baixo ja quebrou por uma classe do painel com o nome dela. Fora do prefixo uso-,
        so os modificadores de estado, que andam sempre junto de uma classe do painel."""
        css = re.sub(r"/\*.*?\*/", "", (UI / "painel-uso.css").read_text(encoding="utf-8"), flags=re.S)
        modificadores = {
            "ativo", "nivel-ok", "nivel-alto", "nivel-critico", "alto", "critico", "vazio", "sem-leitura", "nada",
            "sem-base", "largo", "esq", "dir", "hoje", "base", "reserva", "deficit", "no-ritmo", "neutra", "copiado",
            "ja", "erro",
        }
        # o seletor de numero fracionado (12.5px) nao e classe
        classes = {c for c in re.findall(r"\.([a-zA-Z][\w-]*)", css)}
        self.assertEqual({c for c in classes if not c.startswith("uso-")} - modificadores, set())

    def test_painel_nunca_pinta_a_cor_do_fundo(self):
        """A vigia le a cor do fundo da janela como pagina que sumiu."""
        for nome in ("painel-uso.js", "painel-uso.css"):
            self.assertNotIn(vigia.COR_SENTINELA, (UI / nome).read_text(encoding="utf-8").lower(), nome)


if __name__ == "__main__":
    unittest.main()
