"""A logica da pagina, rodada no QJSEngine do Qt, sem janela, sem motor web e sem shell.

A pagina carrega os dois scripts um depois do outro, como o index.html faz. O que ela pede
ao navegador na hora de subir, documento, janela, canal com o app, entra como um objeto que
aceita tudo e nao faz nada, entao so a logica e testada, nunca o desenho.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import base  # noqa: E402,F401

from PySide6.QtCore import QCoreApplication  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "ui"

# O que o navegador tem e o QJSEngine nao: qualquer coisa pedida devolve ele mesmo, e chamar
# nao faz nada. O requestAnimationFrame e o canal nunca chamam de volta.
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
var document = qualquer, window = qualquer, performance = qualquer, qt = qualquer;
var QWebChannel = qualquer, requestAnimationFrame = qualquer;
var setTimeout = qualquer, clearTimeout = qualquer, setInterval = qualquer;
"""

# O que o script guarda em let nao vira global e o eval do QJSEngine nao alcanca. Um objeto
# criado no fim do proprio script le e troca cada um por dentro.
MUTAVEIS = (
    "pastaPessoal", "perfis", "limitesPorPerfil", "abaAtiva",
    "cfg", "zoomAtual", "Aba", "bridge", "emGrade", "desenho", "placaDesistida", "perdasDeContexto",
    "varrendo", "varrendoDesde",
)
# o que e const so se le: a lista de abas se mexe por dentro dela
SO_LEITURA = ("ordem", "TETO_DE_WEBGL")
DE_DENTRO = (
    "\nvar estado = {"
    + ", ".join(f"get {n}() {{ return {n}; }}, set {n}(v) {{ {n} = v; }}" for n in MUTAVEIS)
    + "".join(f", get {n}() {{ return {n}; }}" for n in SO_LEITURA)
    + "};\n"
)


def garantir_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def texto_da_pagina() -> str:
    """O app.js, que desde a saida da tela de Uso antiga carrega sozinho o que era do stats.js.

    O QJSEngine nao conhece funcao async, e o app.js tem uma so, a de colar pelo navegador:
    ela vira funcao comum aqui, o que nao muda a sintaxe do resto.
    """
    app = (UI / "app.js").read_text(encoding="utf-8")
    app = app.replace("async function", "function").replace("await ", "")
    return app + DE_DENTRO


class ComPagina(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        garantir_app()
        from PySide6.QtQml import QJSEngine

        cls.motor = QJSEngine()
        cls.motor.evaluate(NAVEGADOR_FALSO)
        cls.carga = cls.motor.evaluate(texto_da_pagina(), "pagina.js")

    def rodar(self, expressao: str):
        resultado = self.motor.evaluate("JSON.stringify(" + expressao + ")")
        self.assertFalse(resultado.isError(), resultado.toString())
        texto = resultado.toString()
        return None if texto == "undefined" else json.loads(texto)

    def definir(self, codigo: str) -> None:
        resultado = self.motor.evaluate(codigo)
        self.assertFalse(resultado.isError(), resultado.toString())


class PaginaSobe(ComPagina):
    def test_os_dois_scripts_carregam_sem_erro(self):
        """Um erro de sintaxe ou um nome repetido entre os dois deixava a janela so no fundo."""
        self.assertFalse(self.carga.isError(), self.carga.toString())

    def test_todo_id_que_o_script_procura_existe_na_pagina(self):
        """Sem o elemento, o el() devolve null e a primeira chamada nele derruba a pagina."""
        html = (UI / "index.html").read_text(encoding="utf-8")
        existentes = set(re.findall(r'\bid="([^"]+)"', html))
        faltando = []
        for nome in ("app.js",):
            codigo = (UI / nome).read_text(encoding="utf-8")
            criados = set(re.findall(r'\.id = "([^"]+)"', codigo))
            for alvo in re.findall(r'(?:\bel|getElementById)\("([^"]+)"\)', codigo):
                if alvo not in existentes and alvo not in criados:
                    faltando.append(f"{nome}: {alvo}")
        self.assertEqual(faltando, [])

    def test_todo_script_e_estilo_da_pagina_existe(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        pedidos = re.findall(r'<script src="([^"]+)"', html) + re.findall(r'<link[^>]+href="([^"]+)"', html)
        self.assertIn("app.js", pedidos)
        self.assertEqual([p for p in pedidos if not (UI / p).is_file()], [])


class EstadoDaAbaViva(ComPagina):
    def como(self, estado: str, bruto: str, saida_ha_ms: int | None) -> str:
        aba = "null" if saida_ha_ms is None else "{ultimaSaida: Date.now() - %d}" % saida_ha_ms
        return self.rodar(f'comoNaAba({{estado: "{estado}", bruto: "{bruto}"}}, {aba}).estado')

    def test_ferramenta_longa_com_o_terminal_mexendo_continua_trabalhando(self):
        """Passado um minuto e meio sem transcript, virava ready e saia o aviso de pronta."""
        self.assertEqual(self.como("aguardando", "rodando", 1000), "rodando")
        self.assertEqual(self.como("aguardando", "pensando", 1000), "pensando")

    def test_terminal_calado_deixa_valer_a_regra_da_idade(self):
        self.assertEqual(self.como("aguardando", "rodando", 60000), "aguardando")
        self.assertEqual(self.como("aguardando", "rodando", None), "aguardando")

    def test_quem_respondeu_continua_pronta_mesmo_parada_ha_muito(self):
        self.assertEqual(self.como("parada", "respondeu", None), "aguardando")

    def test_trabalho_abandonado_continua_parado_sem_saida_no_terminal(self):
        self.assertEqual(self.como("parada", "rodando", None), "parada")

    def test_sem_sessao_nao_inventa_nada(self):
        self.assertIsNone(self.rodar("comoNaAba(null, null)"))


class PastaAnunciadaPeloShell(ComPagina):
    def pasta(self, dado) -> str:
        return self.rodar("pastaDoOsc(" + json.dumps(dado) + ")")

    def test_osc_7_com_drive_e_espaco_codificado(self):
        self.assertEqual(self.pasta("file://MAQUINA/C:/Users/eu/meu%20app"), "C:/Users/eu/meu app")

    def test_osc_7_de_caminho_unix(self):
        self.assertEqual(self.pasta("file://host/home/eu"), "/home/eu")

    def test_osc_9_9_traz_o_caminho_entre_aspas(self):
        self.assertEqual(self.pasta('"C:\\Users\\eu\\proj"'), "C:\\Users\\eu\\proj")

    def test_codificacao_quebrada_ou_sem_caminho_nao_vira_pasta(self):
        self.assertEqual(self.pasta("file://MAQUINA/C:/%E0%A4%A"), "")
        self.assertEqual(self.pasta("file://MAQUINA"), "")
        self.assertEqual(self.pasta(None), "")


class LinksDeArquivoNoTerminal(ComPagina):
    def links(self, linha: str | None) -> list:
        linha_js = "null" if linha is None else "({translateToString: () => " + json.dumps(linha) + "})"
        return self.rodar(
            "(() => { let achados = null;"
            " const aba = {term: {buffer: {active: {getLine: () => " + linha_js + "}}}};"
            " provedorDeArquivos(aba).provideLinks(1, (lista) => { achados = lista; });"
            " return achados ? achados.map((a) => [a.text, a.range.start.x, a.range.end.x]) : []; })()"
        )

    def test_caminho_com_linha_e_coluna_vira_link_na_posicao_certa(self):
        self.assertEqual(self.links("  File src/app.py:42:7 error"), [["src/app.py:42:7", 8, 22]])

    def test_windows_e_unix_na_mesma_linha(self):
        textos = [t for t, _, _ in self.links(r"C:\proj\a.ts:10 e lib/b.js:3")]
        self.assertEqual(textos, [r"C:\proj\a.ts:10", "lib/b.js:3"])

    def test_endereco_com_porta_fica_pro_addon_de_links(self):
        self.assertEqual(self.links("abra http://localhost.dev:8080/x agora"), [])

    def test_texto_sem_linha_nao_vira_link(self):
        self.assertEqual(self.links("versao 1.2 do app.py pronta"), [])

    def test_linha_que_nao_existe_nao_quebra(self):
        self.assertEqual(self.links(None), [])


class TempoAteZerar(ComPagina):
    def falta(self, expressao: str) -> str:
        return self.rodar("faltaPara(" + expressao + ")")

    def test_minutos_arredondados_nao_passam_de_59(self):
        """Com 1h59m50s pela frente, a barra mostrava 1h60m."""
        self.assertEqual(self.falta("Date.now() + 3600000 + 59 * 60000 + 50000"), "2h0m")
        self.assertEqual(self.falta("Date.now() + 59 * 60000 + 50000"), "1h0m")

    def test_segundos_milissegundos_e_texto_dao_o_mesmo(self):
        noventa = 90 * 60000 + 20000
        self.assertEqual(self.falta(f"Math.floor((Date.now() + {noventa}) / 1000)"), "1h30m")
        self.assertEqual(self.falta(f"Date.now() + {noventa}"), "1h30m")
        self.assertEqual(self.falta(f"new Date(Date.now() + {noventa}).toISOString()"), "1h30m")

    def test_mais_de_um_dia_mostra_dias_e_horas(self):
        self.assertEqual(self.falta("Date.now() + 26 * 3600000 + 60000"), "1d2h")

    def test_passado_ou_vazio_nao_mostra_nada(self):
        for expressao in ("Date.now() - 1000", "0", "null", '"ontem"'):
            self.assertEqual(self.falta(expressao), "", expressao)


class NomesNaTela(ComPagina):
    def test_pasta_pessoal_vira_til_e_as_outras_o_ultimo_nome(self):
        self.definir('estado.pastaPessoal = "C:\\\\Users\\\\Eu"')
        try:
            self.assertEqual(self.rodar('nomeDaPasta("c:\\\\users\\\\eu\\\\")'), "~")
            self.assertEqual(self.rodar('nomeDaPasta("C:\\\\Users\\\\Eu\\\\proj\\\\")'), "proj")
            self.assertEqual(self.rodar('nomeDaPasta("/home/eu/app/")'), "app")
            self.assertEqual(self.rodar('nomeDaPasta("")'), "")
        finally:
            self.definir('estado.pastaPessoal = ""')

    def test_id_do_modelo_vira_nome_de_gente(self):
        self.assertEqual(self.rodar('nomeBonito("claude-opus-5-20260101")'), "Opus 5")
        self.assertEqual(self.rodar('nomeBonito("claude-sonnet-latest")'), "Sonnet")
        self.assertEqual(self.rodar('nomeBonito("Opus 5")'), "Opus 5")
        self.assertEqual(self.rodar('nomeBonito("")'), "")

    def test_caminho_com_espaco_ou_aspas_vai_entre_aspas(self):
        self.assertEqual(self.rodar('comAspas("C:\\\\proj\\\\a.py")'), "C:\\proj\\a.py")
        self.assertEqual(self.rodar('comAspas("C:\\\\Meus Docs\\\\a.py")'), '"C:\\Meus Docs\\a.py"')
        self.assertEqual(self.rodar("comAspas('a\"b')"), '"a\\"b"')

    def test_numeros_curtos(self):
        casos = {
            "tempoCurto(59)": "59s",
            "tempoCurto(600)": "10min",
            "tempoCurto(7200)": "2h",
            "tempoCurto(3 * 86400)": "3d",
            "curtoBarra(0)": "0",
            "curtoBarra(1500)": "2k",
            "curtoBarra(2500000)": "2.5M",
            "tamanho(0)": "0 B",
            "tamanho(2048)": "2 kB",
            "tamanho(3 * 1048576)": "3.0 MB",
            "textoDeAbas(1)": "1 aba aberta",
            "textoDeAbas(3)": "3 abas abertas",
            "grau(null)": "",
            "grau(59)": "",
            "grau(60)": "atencao",
            "grau(85)": "critico",
        }
        for expressao, esperado in casos.items():
            self.assertEqual(self.rodar(expressao), esperado, expressao)


class ContaDaBarra(ComPagina):
    def tearDown(self) -> None:
        self.definir("estado.perfis = []; estado.limitesPorPerfil = {}")

    def test_aba_sem_conta_le_a_pasta_de_sempre(self):
        self.definir("estado.limitesPorPerfil = {__padrao__: {faixas: {five_hour: {usado: 7}}}}")
        self.assertEqual(self.rodar('faixaDaConta({}, "five_hour")'), {"usado": 7})

    def test_pasta_de_sempre_cadastrada_le_pelo_id_da_conta(self):
        self.definir(
            'estado.perfis = [{id: "p1", padrao: true, logado: true}];'
            "estado.limitesPorPerfil = {__padrao__: {faixas: {five_hour: {usado: 1}}}, p1: {faixas: {five_hour: {usado: 9}}}}"
        )
        self.assertEqual(self.rodar('faixaDaConta({}, "five_hour")'), {"usado": 9})
        self.assertEqual(self.rodar('contaDaSessao({perfil: "__padrao__", conta: "x"})'), "p1")

    def test_conta_sem_login_nao_mostra_numero_de_outra(self):
        self.definir("estado.limitesPorPerfil = {p2: {faixas: {five_hour: {usado: 50}}}}")
        self.assertIsNone(self.rodar('faixaDaConta({perfil: {id: "p2", logado: false}}, "five_hour")'))

    def test_sessao_de_outra_conta_fica_com_a_dela(self):
        self.assertEqual(self.rodar('contaDaSessao({perfil: "p3", conta: "p9"})'), "p3")
        self.assertEqual(self.rodar('contaDaSessao({perfil: "__padrao__", conta: "p9"})'), "p9")


class ArrastarPraAba(ComPagina):
    def tearDown(self) -> None:
        self.definir("estado.abaAtiva = null")

    def soltar(self, aba: str, caminhos: list) -> list:
        return self.rodar(
            "(() => { const colado = [];"
            " estado.abaAtiva = " + aba + ";"
            " if (estado.abaAtiva) estado.abaAtiva.term = {paste: (t) => colado.push(t)};"
            " soltarArquivos(" + json.dumps(caminhos) + "); return colado; })()"
        )

    def test_varios_caminhos_vao_numa_linha_so_com_aspas_quando_precisa(self):
        colado = self.soltar('{sessionId: "a1", shell: {id: "pwsh"}, foco() {}}', ["C:\\a.txt", "C:\\Meus Docs\\b.txt"])
        self.assertEqual(colado, ["'C:\\a.txt' 'C:\\Meus Docs\\b.txt' "])

    def test_painel_ou_aba_sem_shell_nao_recebe(self):
        self.assertEqual(self.soltar('{ehPainel: true, sessionId: "a1", foco() {}}', ["C:\\a.txt"]), [])
        self.assertEqual(self.soltar('{sessionId: null, foco() {}}', ["C:\\a.txt"]), [])
        self.assertEqual(self.soltar("null", ["C:\\a.txt"]), [])


class ZoomDaFonte(ComPagina):
    def setUp(self) -> None:
        self.definir("estado.cfg = {tamanhoFonte: 16}; estado.zoomAtual = 0")

    def tearDown(self) -> None:
        self.definir("estado.cfg = {}; estado.zoomAtual = 0")

    def test_zoom_nao_grava_e_o_zero_volta_ao_tamanho_escolhido(self):
        """Ctrl+0 voltava pra 14 fixo e gravava, e quem tinha escolhido 16 ficava com 14."""
        self.definir("zoom(1); zoom(1)")
        self.assertEqual(self.rodar("fonteDoTerminal()"), 18)
        self.assertEqual(self.rodar("estado.cfg.tamanhoFonte"), 16)
        self.definir("zoom(0)")
        self.assertEqual(self.rodar("fonteDoTerminal()"), 16)
        self.assertEqual(self.rodar("estado.cfg.tamanhoFonte"), 16)

    def test_zoom_para_nas_bordas(self):
        for _ in range(20):
            self.definir("zoom(1)")
        self.assertEqual(self.rodar("fonteDoTerminal()"), 22)
        self.definir("zoom(-1)")
        self.assertEqual(self.rodar("fonteDoTerminal()"), 21)


class AbaReaberta(ComPagina):
    def test_enter_na_aba_encerrada_volta_na_pasta_que_o_shell_anunciou(self):
        """Depois de um cd, o Enter abria o shell novo na pasta de onde a aba nasceu."""
        pedido = self.rodar(
            "(() => { const falsa = {viva: false, sessionId: null, term: {reset() {}},"
            ' pedidoInicial: {pasta: "C:/nasceu", entrar: true, semCli: true}, pastaAtual: "C:/agora",'
            " pintar() {}, abrir(p) { this.pedido = p; }};"
            " estado.Aba.prototype.reabrir.call(falsa); return falsa.pedido; })()"
        )
        self.assertEqual(pedido, {"pasta": "C:/agora"})

    def test_sem_pasta_anunciada_fica_a_de_nascimento(self):
        pedido = self.rodar(
            "(() => { const falsa = {viva: false, sessionId: null, term: {reset() {}},"
            ' pedidoInicial: {pasta: "C:/nasceu"}, pintar() {}, abrir(p) { this.pedido = p; }};'
            " estado.Aba.prototype.reabrir.call(falsa); return falsa.pedido; })()"
        )
        self.assertEqual(pedido, {"pasta": "C:/nasceu"})


class AbaQueNaoSobe(ComPagina):
    def test_id_vazio_encerra_a_aba_em_vez_de_deixar_viva_sem_shell(self):
        """O shell que nao subia deixava a aba viva e sem sessao, engolindo cada tecla."""
        resultado = self.rodar(
            "(() => { const escrito = []; const antes = [setTimeout, ativar];"
            " setTimeout = (f) => f(); ativar = () => {};"
            " const aba = Object.create(estado.Aba.prototype);"
            " Object.assign(aba, {viva: true, sessionId: null, shell: {id: 'pwsh'}, perfil: null, elevado: false,"
            " fit: {fit() {}}, term: {cols: 80, rows: 24, write: (t) => escrito.push(t), focus() {}}, pintar() {}});"
            " estado.bridge = {openSessionWith(...args) { args[args.length - 1](''); }};"
            " try { aba.abrir({}); } finally { setTimeout = antes[0]; ativar = antes[1]; estado.bridge = null; }"
            " return {viva: aba.viva, sessionId: aba.sessionId, escrito: escrito.join('')}; })()"
        )
        self.assertFalse(resultado["viva"])
        self.assertIsNone(resultado["sessionId"])
        self.assertIn("sessão encerrada", resultado["escrito"])


class EstadoEscrito(ComPagina):
    def test_pronta_pedindo_permissao_diz_o_motivo_numa_lingua_so(self):
        """O topo do painel da grade dizia so READY, igual a um que so terminou."""
        s = '{estado: "aguardando", espera: "permissao", ferramenta: "Edit"}'
        self.assertEqual(self.rodar("textoDoEstado(" + s + ")"), "ready permission Edit")
        self.assertEqual(self.rodar("dicaDoEstado(" + s + ")"), "Parou pra pedir sua permissão")
        self.assertEqual(self.rodar('textoDoEstado({estado: "aguardando", espera: "pergunta"})'), "ready question")
        self.assertEqual(self.rodar('textoDoEstado({estado: "aguardando", espera: ""})'), "ready")
        self.assertEqual(self.rodar('textoDoEstado({estado: "rodando", espera: "permissao"})'), "working")

    def test_conversa_sem_titulo_mostra_a_pasta(self):
        """Tres conversas novas viravam tres cartoes chamados Claude Code."""
        self.assertEqual(self.rodar('tituloDaConversa({titulo: "", pasta: "D:/repos/loja"}, "Claude Code")'), "loja")
        self.assertEqual(self.rodar('tituloDaConversa({titulo: "Corrige o login", pasta: "D:/x"}, "Claude Code")'), "Corrige o login")
        self.assertEqual(self.rodar('tituloDaConversa({titulo: "", pasta: ""}, "Claude Code")'), "Claude Code")


class ColarVariasLinhas(ComPagina):
    def colar(self, texto: str, protegida: bool) -> dict:
        return self.rodar(
            "(() => { const colado = []; let perguntou = null; const antes = perguntar;"
            " perguntar = (titulo, texto, opcoes) => { perguntou = [titulo, opcoes.map((o) => o.rotulo)]; };"
            " try { entregarColagem({term: {modes: {bracketedPasteMode: " + ("true" if protegida else "false") + "},"
            " paste: (t) => colado.push(t)}}, " + json.dumps(texto) + "); } finally { perguntar = antes; }"
            " return {colado, perguntou}; })()"
        )

    def test_shell_cru_pergunta_antes_de_rodar_cada_linha(self):
        resultado = self.colar("cd x\nrm -r build\n", False)
        self.assertEqual(resultado["colado"], [])
        self.assertEqual(resultado["perguntou"][0], "Colar 2 linhas")
        self.assertIn("Colar numa linha só", resultado["perguntou"][1])

    def test_programa_que_pede_colagem_protegida_ou_uma_linha_so_cola_direto(self):
        self.assertEqual(self.colar("a\nb", True), {"colado": ["a\nb"], "perguntou": None})
        self.assertEqual(self.colar("so uma\n", False), {"colado": ["so uma\n"], "perguntou": None})


class WebglSoNaAbaAVista(ComPagina):
    """So a aba que aparece tem contexto WebGL; a escondida desenha pelo DOM.

    Cada contexto e mais um caminho pro ANGLE D3D11, e e ali que a tela cai levando o Loom. O
    addon e o terminal sao falsos: o addon so anota o que foi feito com ele, e o terminal poe
    uma tela de desenho nova na aba a cada addon carregado, como o xterm faz.
    """

    FALSOS = r"""
    var criados = [];
    var WebglAddon = {
      WebglAddon: function () {
        const eu = this;
        criados.push(eu);
        eu.onContextLoss = (f) => { eu.perder = f; };
        eu.dispose = () => { eu.largado = true; };
      },
    };
    var gpuPerdida = 0;
    function abaFalsa(nome) {
      const telas = [];
      const nada = { add() {}, remove() {} };
      const aba = Object.create(estado.Aba.prototype);
      Object.assign(aba, {
        nome: nome, ehPainel: false, webgl: null, telasWebgl: [], chapeu: qualquer,
        tela: { classList: nada, style: qualquer, querySelectorAll: () => telas.slice() },
        botao: { classList: nada, parentNode: null },
        term: {
          loadAddon(addon) {
            const tela = { perdido: false };
            tela.getContext = () => ({
              isContextLost: () => tela.perdido,
              getExtension: () => ({ loseContext() { tela.perdido = true; } }),
            });
            telas.push(tela);
            addon.tela = tela;
          },
        },
        ajustar() {}, foco() {}, nomeDoCartao() { return nome; },
      });
      estado.ordem.push(aba);
      return aba;
    }
    function comWebgl() {
      return estado.ordem.filter((a) => a.webgl).map((a) => a.nome);
    }
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        resultado = cls.motor.evaluate(cls.FALSOS)
        assert not resultado.isError(), resultado.toString()

    def setUp(self) -> None:
        self.definir(
            "estado.ordem.length = 0; criados.length = 0; gpuPerdida = 0; estado.abaAtiva = null;"
            " estado.emGrade = false; estado.desenho = {emUso: 'gpu'}; estado.placaDesistida = false;"
            " estado.perdasDeContexto = []; estado.bridge = {gpuPerdida() { gpuPerdida++; }};"
            " estado.cfg = {}"
        )

    def tearDown(self) -> None:
        self.definir(
            "estado.ordem.length = 0; estado.abaAtiva = null; estado.emGrade = false; estado.bridge = null;"
            " estado.desenho = {emUso: 'gpu'}; estado.placaDesistida = false; estado.perdasDeContexto = []"
        )

    def abas(self, quantas: int) -> None:
        self.definir("; ".join(f'var aba{i} = abaFalsa("a{i}")' for i in range(quantas)))

    def test_so_a_aba_ativa_tem_webgl(self):
        self.abas(3)
        self.definir("ativar(aba0)")
        self.assertEqual(self.rodar("comWebgl()"), ["a0"])
        self.definir("ativar(aba1)")
        self.assertEqual(self.rodar("comWebgl()"), ["a1"])
        # a que sumiu larga o addon e perde o contexto na hora, sem esperar o coletor de lixo
        self.assertEqual(self.rodar("[criados[0].largado, criados[0].tela.perdido]"), [True, True])
        self.assertEqual(self.rodar("gpuPerdida"), 0, "largar de proposito nao e falha da placa")

    def test_grade_liga_em_todos_e_sair_dela_deixa_so_a_ativa(self):
        self.abas(3)
        self.definir("ativar(aba1); alternarGrade(true)")
        self.assertEqual(self.rodar("comWebgl()"), ["a0", "a1", "a2"])
        self.definir("alternarGrade(false)")
        self.assertEqual(self.rodar("comWebgl()"), ["a1"])

    def test_grade_grande_fica_no_teto_e_a_ativa_nunca_fica_de_fora(self):
        """Passando de 16 contextos o Chromium derruba o mais velho, e isso contaria como perda."""
        teto = self.rodar("estado.TETO_DE_WEBGL")
        self.abas(teto + 3)
        self.definir(f"ativar(aba{teto + 2}); alternarGrade(true)")
        ligadas = self.rodar("comWebgl()")
        self.assertEqual(len(ligadas), teto)
        self.assertIn(f"a{teto + 2}", ligadas)

    def test_desenho_por_software_nem_tenta_criar(self):
        self.abas(2)
        self.definir("estado.desenho = {emUso: 'software'}; ativar(aba0); alternarGrade(true)")
        self.assertEqual(self.rodar("comWebgl()"), [])
        self.assertEqual(self.rodar("criados.length"), 0)

    def test_perda_solta_so_larga_aquela_aba(self):
        self.abas(2)
        self.definir("ativar(aba0); criados[0].perder()")
        self.assertEqual(self.rodar("comWebgl()"), [])
        self.assertEqual(self.rodar("gpuPerdida"), 0)
        # voltando a aparecer, ela tenta de novo
        self.definir("ativar(aba1); ativar(aba0)")
        self.assertEqual(self.rodar("comWebgl()"), ["a0"])

    def test_duas_perdas_em_dois_segundos_avisam_o_app_e_largam_tudo(self):
        self.abas(3)
        self.definir("ativar(aba0); alternarGrade(true); criados[0].perder(); criados[1].perder()")
        self.assertEqual(self.rodar("gpuPerdida"), 1)
        self.assertEqual(self.rodar("comWebgl()"), [])
        # a placa foi dada como perdida: ate o fim desta pagina, nenhum contexto novo
        self.definir("alternarGrade(false); ativar(aba1)")
        self.assertEqual(self.rodar("comWebgl()"), [])
        self.definir("criados[2].perder()")
        self.assertEqual(self.rodar("gpuPerdida"), 1, "o app ouve uma vez so")

    def test_perdas_espacadas_nao_sao_a_placa_falhando(self):
        self.assertFalse(self.rodar("perdeuContexto(1000)"))
        self.assertFalse(self.rodar("perdeuContexto(3001)"))
        self.assertTrue(self.rodar("perdeuContexto(4500)"))
        self.assertEqual(self.rodar("gpuPerdida"), 1)


class LinhaDoDesenho(ComPagina):
    def texto(self, **situacao) -> dict:
        base = {"escolha": "auto", "emUso": "gpu", "proxima": "gpu", "porQueda": False, "porFora": False,
                "quedaEm": "", "ateEm": ""}
        base.update(situacao)
        return self.rodar("textoDoDesenho(" + json.dumps(base) + ")")

    def test_depois_de_queda_avisa_e_oferece_voltar(self):
        t = self.texto(emUso="software", proxima="software", porQueda=True, quedaEm="27/09 às 20:58", ateEm="04/10")
        self.assertEqual(
            t["aviso"],
            "O Loom passou a desenhar sem a placa de vídeo depois de uma queda da tela em 27/09 às 20:58."
            " Volta sozinho à placa a partir de 04/10.",
        )
        self.assertTrue(t["voltar"])
        self.assertIn("sem a placa de vídeo", t["agora"])

    def test_depois_de_voltar_diz_que_a_placa_vem_na_proxima(self):
        t = self.texto(emUso="software", proxima="gpu", porQueda=True, quedaEm="27/09 às 20:58")
        self.assertIn("Na próxima vez que abrir, ele volta à placa de vídeo.", t["aviso"])
        self.assertFalse(t["voltar"])

    def test_queda_nesta_subida_avisa_o_que_vem(self):
        t = self.texto(proxima="software", quedaEm="28/09 às 10:00")
        self.assertIn("A tela caiu em 28/09 às 10:00", t["aviso"])
        self.assertTrue(t["voltar"])

    def test_sem_queda_nao_ha_aviso_e_a_troca_manual_diz_quando_vale(self):
        self.assertEqual(self.texto(), {"agora": "Agora com a placa de vídeo.", "aviso": "", "voltar": False})
        t = self.texto(escolha="software", proxima="software")
        self.assertEqual(t["agora"], "Agora com a placa de vídeo. Na próxima vez que abrir: software.")
        self.assertFalse(t["voltar"], "escolha feita na mao nao se desfaz pelo aviso")

    def test_variavel_de_fora_e_dita(self):
        t = self.texto(emUso="software", proxima="gpu", escolha="gpu", porFora=True)
        self.assertIn("variável de ambiente", t["agora"])


class NomesQueSairamDoStats(ComPagina):
    """A tela de Uso antiga saiu junto com o stats.js; o que o app usava dele mora no app.js."""

    def test_apelido_da_conta_nao_vira_html(self):
        self.assertEqual(
            self.rodar("escapar('<img src=x onerror=\"a()\">&\\'')"),
            "&#60;img src=x onerror=&#34;a()&#34;&#62;&#38;&#39;",
        )

    def test_nome_do_modelo_curto_com_ponto_na_versao(self):
        casos = {
            'nomeDeModelo("claude-opus-5-20260101")': "Opus 5",
            'nomeDeModelo("claude-sonnet-4-5")': "Sonnet 4.5",
            'nomeDeModelo("Sonnet 4.5 (1M context)")': "Sonnet 4.5 (1M context)",
            'nomeDeModelo("")': "",
        }
        for expressao, esperado in casos.items():
            self.assertEqual(self.rodar(expressao), esperado, expressao)



if __name__ == "__main__":
    unittest.main()
