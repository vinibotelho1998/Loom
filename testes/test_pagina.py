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
    "pastaPessoal", "perfis", "limitesPorPerfil", "abaAtiva", "usoBruto", "usoPerfis", "usoLimites",
    "cfg", "zoomAtual", "Aba",
)
DE_DENTRO = (
    "\nvar estado = {"
    + ", ".join(f"get {n}() {{ return {n}; }}, set {n}(v) {{ {n} = v; }}" for n in MUTAVEIS)
    + "};\n"
)


def garantir_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def texto_da_pagina() -> str:
    """stats.js e app.js juntos, na ordem do index.html.

    O QJSEngine nao conhece funcao async, e o app.js tem uma so, a de colar pelo navegador:
    ela vira funcao comum aqui, o que nao muda a sintaxe do resto.
    """
    app = (UI / "app.js").read_text(encoding="utf-8")
    app = app.replace("async function", "function").replace("await ", "")
    return (UI / "stats.js").read_text(encoding="utf-8") + "\n" + app + DE_DENTRO


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
        for nome in ("app.js", "stats.js"):
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
        colado = self.soltar('{sessionId: "a1", foco() {}}', ["C:\\a.txt", "C:\\Meus Docs\\b.txt"])
        self.assertEqual(colado, ['C:\\a.txt "C:\\Meus Docs\\b.txt" '])

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


class UsoPorConta(ComPagina):
    def tearDown(self) -> None:
        self.definir("estado.usoBruto = {}; estado.usoPerfis = []; estado.usoLimites = {}")

    def dados(self, bruto: dict, perfis: list) -> None:
        self.definir(f"estado.usoBruto = {json.dumps(bruto)}; estado.usoPerfis = {json.dumps(perfis)}")

    @staticmethod
    def gasto(semana: int, hoje: int = 0) -> dict:
        return {"hoje": {"saida": hoje}, "semana": {"saida": semana}, "mes": {}, "total": {}, "serie": [], "modelos": []}

    def test_pasta_de_sempre_cadastrada_soma_o_gasto_antigo_na_conta(self):
        """Sem juntar, Principal e a conta apareciam lado a lado e o gasto contava duas vezes."""
        self.dados({"__padrao__": self.gasto(5), "p1": self.gasto(7)}, [{"id": "p1", "nome": "Minha", "padrao": True}])
        contas = self.rodar("contasDoUso()")
        self.assertEqual([(c["id"], c["chaves"]) for c in contas], [("p1", ["p1", "__padrao__"])])
        self.assertEqual(self.rodar('totalDe(["p1", "__padrao__"]).semana.saida'), 12)

    def test_conta_apagada_vai_pro_fim_como_removidas(self):
        self.dados(
            {"velha": self.gasto(100), "p1": self.gasto(1), "p2": self.gasto(9), "__padrao__": self.gasto(3)},
            [{"id": "p1", "nome": "Um"}, {"id": "p2", "nome": "Dois"}],
        )
        contas = self.rodar("contasDoUso()")
        self.assertEqual([c["nome"] for c in contas], ["Dois", "Principal", "Um", "Removidas"])
        self.assertEqual(contas[-1]["chaves"], ["velha"])

    def test_leitura_do_plano_pelo_id_e_senao_a_mais_nova_das_juntas(self):
        self.definir(
            "estado.usoLimites = {p1: {faixas: {}}, __padrao__: {lido: 5, faixas: {five_hour: {usado: 40}}},"
            " x: {lido: 9, faixas: {five_hour: {usado: 70}}}}"
        )
        self.assertEqual(self.rodar('faixasDaConta({id: "p1", chaves: ["p1", "__padrao__", "x"]})'), {"five_hour": {"usado": 70}})
        self.definir("estado.usoLimites.p1 = {faixas: {seven_day: {usado: 2}}}")
        self.assertEqual(self.rodar('faixasDaConta({id: "p1", chaves: ["p1", "x"]})'), {"seven_day": {"usado": 2}})
        self.assertEqual(self.rodar('faixasDaConta({id: "z", chaves: ["z"]})'), {})

    def test_conta_so_com_leitura_do_plano_entra_na_lista(self):
        """Conta recem logada, sem transcript, tinha o limite e nao aparecia em lugar nenhum."""
        self.dados({"p1": self.gasto(3)}, [{"id": "p1", "nome": "Um"}, {"id": "p2", "nome": "Nova"}])
        self.definir("estado.usoLimites = {p2: {faixas: {five_hour: {usado: 12}}}, p9: {faixas: {five_hour: {usado: 1}}}}")
        contas = self.rodar("contasDoUso()")
        self.assertEqual([(c["id"], c["chaves"]) for c in contas], [("p1", ["p1"]), ("p2", ["p2"])])

    def test_leitura_da_pasta_de_sempre_vai_pra_conta_que_a_cadastrou(self):
        self.dados({}, [{"id": "p1", "nome": "Minha", "padrao": True}])
        self.definir("estado.usoLimites = {__padrao__: {faixas: {seven_day: {usado: 30}}}}")
        contas = self.rodar("contasDoUso()")
        self.assertEqual([(c["id"], c["chaves"]) for c in contas], [("p1", ["p1", "__padrao__"])])

    def test_todas_mostra_o_limite_de_cada_conta(self):
        """O botao Uso abre em Todas, e ali o bloco do limite nem aparecia."""
        self.dados({"p1": self.gasto(3), "p2": self.gasto(1)}, [{"id": "p1", "nome": "Um"}, {"id": "p2", "nome": "Dois"}])
        self.definir("estado.usoLimites = {p1: {faixas: {five_hour: {usado: 91}}}}")
        html = self.rodar("limitesDeTodas(contasDoUso())")
        self.assertIn("Limite do plano", html)
        self.assertEqual(html.count('class="limite-conta"'), 1)
        self.assertIn("Um", html)
        self.assertIn('class="regua critico"', html)
        self.definir("estado.usoLimites = {}")
        self.assertEqual(self.rodar("limitesDeTodas(contasDoUso())"), "")

    def test_numero_do_uso_diz_a_unidade(self):
        self.assertEqual(self.rodar("apoioDoNumero({mensagens: 40})"), "tokens gerados em 40 respostas")
        self.assertEqual(self.rodar("apoioDoNumero({mensagens: 1})"), "tokens gerados em 1 resposta")
        self.assertEqual(self.rodar("apoioDoNumero({})"), "tokens gerados em 0 respostas")

    def test_semana_comeca_na_segunda_e_o_domingo_fecha_a_anterior(self):
        semanas = self.rodar(
            'porSemana([{dia: "2026-09-13", saida: 1, porModelo: {a: 1}},'
            ' {dia: "2026-09-14", saida: 2, porModelo: {a: 2}},'
            ' {dia: "2026-09-20", saida: 4, porModelo: {b: 4}}])'
        )
        self.assertEqual(
            [(s["dia"], s["saida"], s["porModelo"]) for s in semanas],
            [("2026-09-07", 1, {"a": 1}), ("2026-09-14", 6, {"a": 2, "b": 4})],
        )

    def test_apelido_da_conta_nao_vira_html(self):
        self.assertEqual(
            self.rodar("escapar('<img src=x onerror=\"a()\">&\\'')"),
            "&#60;img src=x onerror=&#34;a()&#34;&#62;&#38;&#39;",
        )

    def test_regua_do_plano(self):
        self.assertIn("Sem leitura ainda", self.rodar('regua("5h", null)'))
        self.assertIn('class="regua critico"', self.rodar('regua("5h", {usado: 85})'))
        self.assertIn('class="regua atencao"', self.rodar('regua("5h", {usado: 60})'))
        cheia = self.rodar('regua("5h", {usado: 130.4})')
        self.assertIn("width:100%", cheia)
        self.assertIn("<b>130%</b>", cheia)

    def test_numeros_e_nomes_de_modelo_curtos(self):
        casos = {
            "curto(0)": "0",
            "curto(999)": "999",
            "curto(1500)": "1.5k",
            "curto(2500000)": "2.5M",
            "curto(3e9)": "3.00B",
            # o mesmo nome da barra de baixo, com ponto na versao
            'nomeModelo("claude-opus-5-20260101")': "Opus 5",
            'nomeModelo("claude-sonnet-4-5")': "Sonnet 4.5",
            'nomeModelo("<b>x</b>")': "bx/b",
            'familiaDoModelo("claude-opus-5")': "#b088f0",
            'familiaDoModelo("gpt")': "#5b6371",
        }
        for expressao, esperado in casos.items():
            self.assertEqual(self.rodar(expressao), esperado, expressao)


if __name__ == "__main__":
    unittest.main()
