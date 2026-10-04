"""A paleta de comandos e a tela dividida em dois.

Quatro jeitos de olhar. A listagem de conversas do disco, em pasta temporaria. A ponte, com os dois
slots novos. As regras do app.js no QJSEngine com a pagina falsa, com abas de mentira que so contam
o que aconteceu com elas. E o motor web de verdade, num processo filho, pro que so existe desenhado:
os dois lados lado a lado, a divisoria arrastada e a caixa da paleta por cima de tudo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from base import ComPastaPropria  # noqa: E402
from test_layout import medir_varios  # noqa: E402
from test_pagina import NAVEGADOR_FALSO, garantir_app, texto_da_pagina  # noqa: E402

from loom import bridge as mod_bridge  # noqa: E402
from loom import conversas  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "ui"
AGORA = time.time()


def escrever(alvo: Path, registros: list[dict], idade: float = 0) -> Path:
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in registros) + "\n", encoding="utf-8")
    if idade:
        os.utime(alvo, (AGORA - idade, AGORA - idade))
    return alvo


def pedido(texto: str, cwd: str = r"C:\proj\loja") -> dict:
    return {"type": "user", "cwd": cwd, "message": {"role": "user", "content": texto}}


def resposta(cwd: str = r"C:\proj\loja") -> dict:
    return {"type": "assistant", "cwd": cwd, "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}}


class ConversasDoDisco(unittest.TestCase):
    def setUp(self) -> None:
        self.raiz = Path(tempfile.mkdtemp(prefix="loom-conversas-"))
        conversas._LIDOS.clear()

    def tearDown(self) -> None:
        shutil.rmtree(self.raiz, ignore_errors=True)
        conversas._LIDOS.clear()

    def transcript(self, conta: str, nome: str, registros: list[dict], idade: float = 0) -> Path:
        return escrever(self.raiz / conta / "projects" / "C--proj-loja" / (nome + ".jsonl"), registros, idade)

    def listar(self, codex: dict | None = None) -> list[dict]:
        return conversas.listar({"__padrao__": str(self.raiz / "principal")}, codex, agora=AGORA)

    def test_traz_as_dos_ultimos_30_dias_com_titulo_e_pasta_e_deixa_a_velha(self):
        """A lateral so ve 24 horas; a paleta acha a conversa de semanas atras pelo nome."""
        self.transcript("principal", "nova", [pedido("corrige o checkout"), resposta(), {"type": "ai-title", "aiTitle": "Checkout no CI"}], 3600)
        self.transcript("principal", "semana", [pedido("cupom no carrinho"), resposta()], 6 * 86400)
        self.transcript("principal", "velha", [pedido("coisa antiga"), resposta()], 45 * 86400)
        lista = self.listar()
        self.assertEqual([c["sessao"] for c in lista], ["nova", "semana"], "a mais nova primeiro, sem a de 45 dias")
        self.assertEqual(lista[0]["titulo"], "Checkout no CI")
        self.assertEqual(lista[1]["titulo"], "cupom no carrinho", "sem titulo, vale o primeiro pedido")
        self.assertEqual(lista[0]["pasta"], r"C:\proj\loja")
        self.assertEqual((lista[0]["perfil"], lista[0]["cli"]), ("__padrao__", "claude"))
        self.assertAlmostEqual(lista[1]["idade"], 6 * 86400, delta=5)

    def test_abertura_que_nunca_virou_conversa_fica_de_fora(self):
        self.transcript("principal", "so-pedido", [pedido("oi")], 60)
        self.assertEqual(self.listar(), [])

    def test_titulo_do_fim_de_um_transcript_grande_sem_ler_o_meio(self):
        """O ai-title mora no fim; o meio de um transcript de dezenas de MB nao pode ser lido."""
        meio = [resposta() for _ in range(3000)]
        alvo = self.transcript("principal", "grande", [pedido("refatora o modulo")] + meio + [{"type": "ai-title", "aiTitle": "Refatorar o modulo"}])
        self.assertGreater(alvo.stat().st_size, conversas.CABECA_CLAUDE + conversas.CAUDA_CLAUDE)
        lidos = []
        original = conversas._linhas_json

        def contar(bruto):
            lidos.append(len(bruto))
            return original(bruto)

        with unittest.mock.patch.object(conversas, "_linhas_json", contar):
            lista = self.listar()
        self.assertEqual(lista[0]["titulo"], "Refatorar o modulo")
        self.assertLessEqual(sum(lidos), conversas.CABECA_CLAUDE + 4096, "so a cabeca passa por aqui; a cauda vem do leitor da lista")

    def test_arquivo_que_nao_mudou_nao_e_lido_de_novo(self):
        self.transcript("principal", "a", [pedido("um"), resposta()], 60)
        with unittest.mock.patch.object(conversas, "_ler_claude", wraps=conversas._ler_claude) as leitor:
            self.listar()
            self.listar()
        self.assertEqual(leitor.call_count, 1)

    def test_codex_pega_o_id_do_cabecalho_e_o_primeiro_pedido_de_verdade(self):
        """O contexto do ambiente entra como fala entre tags e nao serve de nome."""
        rollout = self.raiz / "codex" / "sessions" / "2026" / "10" / "02" / "rollout-2026-10-02T10-00-00-abc.jsonl"
        escrever(rollout, [
            {"type": "session_meta", "payload": {"id": "0199-abc", "cwd": r"C:\proj\api", "base_instructions": {"text": "x" * 4000}}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "<environment_context>cwd</environment_context>"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "explica o middleware"}},
        ], 7200)
        lista = self.listar({"cx1": str(self.raiz / "codex")})
        self.assertEqual(len(lista), 1)
        self.assertEqual((lista[0]["sessao"], lista[0]["titulo"], lista[0]["cli"], lista[0]["perfil"]),
                         ("0199-abc", "explica o middleware", "codex", "cx1"))
        self.assertEqual(lista[0]["pasta"], r"C:\proj\api")

    def test_a_mesma_conversa_na_mesma_conta_aparece_uma_vez(self):
        escrever(self.raiz / "principal" / "projects" / "um" / "x.jsonl", [pedido("a"), resposta()], 60)
        escrever(self.raiz / "principal" / "projects" / "dois" / "x.jsonl", [pedido("a"), resposta()], 600)
        self.assertEqual(len(self.listar()), 1)


class PonteDaPaletaEDaDivisao(ComPastaPropria):
    def setUp(self) -> None:
        super().setUp()
        garantir_app()
        conversas._LIDOS.clear()
        self._dados = unittest.mock.patch.object(mod_bridge, "RAIZ_DADOS", self.raiz / "dados")
        self._dados.start()
        self._casa = unittest.mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.casa / ".claude")})
        self._casa.start()
        self.ponte = mod_bridge.Bridge()

    def tearDown(self) -> None:
        self.ponte.shutdown()
        self._casa.stop()
        self._dados.stop()
        super().tearDown()

    def test_os_slots_novos_sao_declarados(self):
        """Metodo da ponte sem @Slot vira slot na hora da ligacao e baguncaria os sinais da pagina."""
        meta = self.ponte.metaObject()
        nomes = {bytes(meta.method(i).name()).decode() for i in range(meta.methodCount())}
        self.assertIn("listarConversas", nomes)
        self.assertIn("ladosDaDivisao", nomes)

    def test_conversas_vem_da_thread_e_a_pagina_pergunta_de_novo(self):
        escrever(self.casa / ".claude" / "projects" / "C--proj" / "s1.jsonl", [pedido("corrige o teste"), resposta()], 120)
        primeira = json.loads(self.ponte.listarConversas())
        self.assertTrue(primeira["coletando"], "a leitura nao roda na thread da tela")
        self.ponte._conversas["linha"].join(10)
        segunda = json.loads(self.ponte.listarConversas())
        self.assertFalse(segunda["coletando"])
        self.assertEqual([c["sessao"] for c in segunda["conversas"]], ["s1"])

    def test_lados_da_divisao_voltam_pela_anotacao_das_abas(self):
        self.ponte.saveTabs(json.dumps([
            {"sessao": "a1", "shell": "pwsh", "lado": "esq"},
            {"sessao": "a2", "shell": "pwsh", "lado": ""},
            {"sessao": "a3", "shell": "cmd", "lado": "dir"},
        ]))
        self.assertEqual(json.loads(self.ponte.ladosDaDivisao()), {"esq": "a1", "dir": "a3"})
        self.ponte.saveTabs(json.dumps([{"sessao": "a1", "shell": "pwsh", "lado": ""}]))
        self.assertEqual(json.loads(self.ponte.ladosDaDivisao()), {})


# o que o script guarda em let e o teste precisa ler e trocar
MUTAVEIS = ("divisao", "abaAtiva", "abaAnterior", "emGrade", "sessoes", "conversasRecentes", "perfis", "shells", "cfg")
DE_DENTRO = (
    "\nvar tela2 = {"
    + ", ".join(f"get {n}() {{ return {n}; }}, set {n}(v) {{ {n} = v; }}" for n in MUTAVEIS)
    + ", get ordem() { return ordem; }, get atalhos() { return Array.from(ATALHOS_DA_JANELA); }"
    + ", get conversasSemBusca() { return CONVERSAS_SEM_BUSCA; }};\n"
)

ABA_FALSA = r"""
function classesFalsas() {
  const s = new Set();
  return {
    add: (...c) => c.forEach((x) => s.add(x)),
    remove: (...c) => c.forEach((x) => s.delete(x)),
    toggle: (c, v) => { const on = v === undefined ? !s.has(c) : !!v; if (on) s.add(c); else s.delete(c); return on; },
    contains: (c) => s.has(c),
  };
}
function abaFalsa(nome) {
  const a = {
    ehPainel: false, viva: true, nome, sessionId: "s-" + nome, perfil: null, sessao: null,
    shell: { id: "pwsh", nome: "PowerShell" },
    tela: { classList: classesFalsas(), style: qualquer },
    botao: { classList: classesFalsas(), parentNode: null },
    chapeu: qualquer, webgl: false, destruida: false,
    ajustar() {}, foco() {}, pintar() {}, refletir() {},
    ligarWebgl() { this.webgl = true; }, largarWebgl() { this.webgl = false; },
    nomeDoCartao() { return nome; },
    destruir() { const i = ordem.indexOf(this); if (i >= 0) ordem.splice(i, 1); this.destruida = true; },
  };
  ordem.push(a);
  return a;
}
function zerarTela() {
  ordem.length = 0;
  tela2.divisao = null; tela2.abaAtiva = null; tela2.abaAnterior = null; tela2.emGrade = false;
}
"""


class ComTela(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        garantir_app()
        from PySide6.QtQml import QJSEngine

        cls.motor = QJSEngine()
        cls.motor.evaluate(NAVEGADOR_FALSO)
        cls.carga = cls.motor.evaluate(texto_da_pagina() + DE_DENTRO + ABA_FALSA, "pagina.js")

    def setUp(self) -> None:
        self.assertFalse(self.carga.isError(), self.carga.toString())
        self.definir("zerarTela(); tela2.sessoes = []; tela2.conversasRecentes = []; tela2.perfis = []; tela2.shells = []; tela2.cfg = {}")

    def rodar(self, expressao: str):
        resultado = self.motor.evaluate("JSON.stringify(" + expressao + ")")
        self.assertFalse(resultado.isError(), resultado.toString())
        texto = resultado.toString()
        return None if texto == "undefined" else json.loads(texto)

    def definir(self, codigo: str) -> None:
        resultado = self.motor.evaluate(codigo)
        self.assertFalse(resultado.isError(), resultado.toString())


class BuscaAproximada(ComTela):
    def casa(self, termo: str, texto: str):
        return self.rodar(f"casarAproximado({json.dumps(termo)}, {json.dumps(texto)})")

    def test_letras_em_ordem_casam_e_fora_de_ordem_nao(self):
        self.assertIsNotNone(self.casa("nvtr", "Nova aba em Trabalho"))
        self.assertIsNone(self.casa("trnv", "Nova aba em Trabalho"))

    def test_comeco_de_palavra_vale_mais_e_e_o_que_ganha_realce(self):
        """"ab" acha o Aba da segunda palavra antes do a e do b soltos no meio de outra."""
        achado = self.casa("nat", "Nova aba em Trabalho")
        self.assertEqual(achado["posicoes"], [0, 5, 12], "n de Nova, a de aba, t de Trabalho")

    def test_sem_acento_dos_dois_lados(self):
        self.assertIsNotNone(self.casa("configuracoes", "Configurações, Aparência"))
        self.assertIsNotNone(self.casa("aparência", "Configuracoes, Aparencia"))

    def test_trecho_seguido_ganha_do_espalhado(self):
        # o trecho seguido no meio da palavra ganha ate das iniciais de palavra espalhadas
        seguido = self.casa("ab", "Tabela")["pontos"]
        espalhado = self.casa("ab", "Ativar busca")["pontos"]
        self.assertGreater(seguido, espalhado)

    def test_cada_palavra_do_termo_precisa_casar_em_qualquer_ordem(self):
        itens = json.dumps([
            {"id": "1", "grupo": "Nova aba", "rotulo": "Nova aba em Trabalho", "detalhe": "Claude Code"},
            {"id": "2", "grupo": "Nova aba", "rotulo": "Nova aba em Pessoal", "detalhe": "Claude Code"},
        ])
        self.assertEqual(self.rodar(f"filtrarPaleta({itens}, 'trab nova').map((a) => a.item.id)"), ["1"])
        self.assertEqual(self.rodar(f"filtrarPaleta({itens}, 'claude').map((a) => a.item.id)"), ["1", "2"], "o detalhe tambem conta")

    def test_sem_termo_esconde_o_que_so_aparece_buscando_e_poda_as_conversas(self):
        itens = [{"id": "a", "grupo": "Ações", "rotulo": "Nova aba"},
                 {"id": "b", "grupo": "Nova aba", "rotulo": "Nova aba em X no Y", "soBuscando": True}]
        itens += [{"id": f"c{i}", "grupo": "Conversas recentes", "rotulo": f"conversa {i}"} for i in range(12)]
        ids = self.rodar(f"filtrarPaleta({json.dumps(itens)}, '').map((a) => a.item.id)")
        self.assertNotIn("b", ids)
        self.assertEqual(len([i for i in ids if i.startswith("c")]), self.rodar("tela2.conversasSemBusca"))
        self.assertIn("b", self.rodar(f"filtrarPaleta({json.dumps(itens)}, 'no y').map((a) => a.item.id)"))

    def test_ha_quanto_tempo(self):
        self.assertEqual(self.rodar("[30, 600, 7200, 86400, 5 * 86400].map(haQuantoTempo)"),
                         ["agora", "há 10 min", "há 2 h", "há 1 dia", "há 5 dias"])


class ItensDaPaleta(ComTela):
    def itens(self) -> list[dict]:
        return self.rodar("itensDaPaletaAgora().map((i) => ({id: i.id, grupo: i.grupo, rotulo: i.rotulo, atalho: i.atalho, marca: i.marca, detalhe: i.detalhe}))")

    def test_cada_aba_aberta_com_o_alt_e_o_numero(self):
        self.definir("var a1 = abaFalsa('api'), a2 = abaFalsa('front'); tela2.abaAtiva = a1;")
        abas = [i for i in self.itens() if i["grupo"] == "Abas abertas"]
        self.assertEqual([(i["rotulo"], i["atalho"], i["marca"]) for i in abas],
                         [("api", ["Alt", "1"], "Atual"), ("front", ["Alt", "2"], "")])

    def test_acoes_com_o_atalho_e_divisao_que_troca_de_nome(self):
        self.definir("var b1 = abaFalsa('api'), b2 = abaFalsa('front'); tela2.abaAtiva = b1;")
        por_id = {i["id"]: i for i in self.itens()}
        self.assertEqual(por_id["dividir"]["atalho"], ["Ctrl", "Shift", "D"])
        self.assertEqual(por_id["nova"]["atalho"], ["Ctrl", "Shift", "T"])
        self.assertEqual(por_id["uso"]["atalho"], ["Ctrl", "Shift", "U"])
        self.assertIn("buscar", por_id)
        self.definir("dividir(b1, b2, b1)")
        por_id = {i["id"]: i for i in self.itens()}
        self.assertNotIn("dividir", por_id)
        self.assertEqual(por_id["desfazer"]["rotulo"], "Desfazer a divisão")

    def test_nova_aba_em_cada_conta_e_uso_de_cada_conta(self):
        self.definir("tela2.perfis = [{id: 'p1', nome: 'Trabalho', cor: '#63c98a', ferramenta: 'claude', ferramentaNome: 'Claude Code', logado: true},"
                     " {id: 'cx', nome: 'Codex', cor: '#d9b25f', ferramenta: 'codex', ferramentaNome: 'Codex CLI', logado: false}]")
        rotulos = [i["rotulo"] for i in self.itens()]
        for esperado in ("Nova aba em Principal", "Nova aba em Trabalho", "Nova aba em Codex", "Uso da conta Trabalho", "Uso da conta Principal"):
            self.assertIn(esperado, rotulos)
        self.assertEqual(self.rodar("itensDaPaletaAgora().filter((i) => i.id === 'uso:codex:cx').length"), 1, "o id do painel e provedor:perfil")

    def test_conversas_juntam_as_da_lateral_com_as_de_30_dias_sem_repetir(self):
        self.definir("tela2.perfis = [{id: 'p1', nome: 'Trabalho', cor: '#63c98a', ferramenta: 'claude', ferramentaNome: 'Claude Code'},"
                     " {id: 'cx', nome: 'Codex', cor: '#d9b25f', ferramenta: 'codex', ferramentaNome: 'Codex CLI'}];"
                     " tela2.conversasRecentes = [{perfil: 'p1', sessao: 's1', titulo: 'velho nome', pasta: 'C:/proj/loja', idade: 9000},"
                     "  {perfil: 'p1', sessao: 's2', titulo: 'Cupom', pasta: 'C:/proj/loja', idade: 4 * 86400},"
                     "  {perfil: 'cx', sessao: 'c1', titulo: 'explica o middleware', pasta: 'C:/proj/api', idade: 7200, cli: 'codex'}];"
                     " tela2.sessoes = [{perfil: 'p1', sessao: 's1', titulo: 'Checkout no CI', pasta: 'C:/proj/loja', idade: 60, estado: 'rodando'}];")
        conversas_ = [i for i in self.itens() if i["grupo"] == "Conversas recentes"]
        self.assertEqual([i["rotulo"] for i in conversas_], ["Checkout no CI", "explica o middleware", "Cupom"])
        self.assertEqual(conversas_[0]["detalhe"], "Trabalho · loja · há 1 min · trabalhando")
        self.assertEqual(conversas_[1]["marca"], "Codex")

    def test_enter_numa_conversa_aberta_vai_pra_aba_e_fechada_retoma_na_conta(self):
        self.definir("tela2.perfis = [{id: 'p1', nome: 'Trabalho', ferramenta: 'claude', ferramentaNome: 'Claude Code'}];"
                     " var c1 = abaFalsa('com conversa'); c1.sessao = {sessao: 's9'}; var c2 = abaFalsa('outra'); tela2.abaAtiva = c2;"
                     " var retomadas = []; retomarSessao = (s) => retomadas.push(s.sessao);")
        self.definir("irParaConversa({perfil: 'p1', sessao: 's9'})")
        self.assertEqual(self.rodar("tela2.abaAtiva.nome"), "com conversa")
        self.definir("irParaConversa({perfil: 'p1', sessao: 's7', estado: 'parada'})")
        self.assertEqual(self.rodar("retomadas"), ["s7"])

    def test_o_atalho_da_paleta_e_da_divisao_nao_vao_pro_shell(self):
        atalhos = self.rodar("tela2.atalhos")
        self.assertIn("KeyP", atalhos)
        self.assertIn("KeyD", atalhos)


class TelaDividida(ComTela):
    def montar(self) -> None:
        self.definir("var e1 = abaFalsa('um'), e2 = abaFalsa('dois'), e3 = abaFalsa('tres'); ativar(e1);")

    def test_dividir_poe_a_ativa_a_esquerda_e_a_outra_a_direita(self):
        self.montar()
        self.definir("dividirCom(e3)")
        self.assertEqual(self.rodar("[tela2.divisao.esq.nome, tela2.divisao.dir.nome, tela2.abaAtiva.nome]"), ["um", "tres", "um"])
        self.assertTrue(self.rodar("e1.tela.classList.contains('lado-esq') && e3.tela.classList.contains('lado-dir')"))
        self.assertTrue(self.rodar("e3.botao.classList.contains('na-divisao')"), "o cartao do outro lado se marca na lateral")

    def test_trocar_pela_lateral_troca_o_lado_em_foco(self):
        self.montar()
        self.definir("dividirCom(e2); ativar(e3)")
        self.assertEqual(self.rodar("[tela2.divisao.esq.nome, tela2.divisao.dir.nome, tela2.abaAtiva.nome]"), ["tres", "dois", "tres"])
        self.assertFalse(self.rodar("e1.tela.classList.contains('lado')"))

    def test_ativar_o_outro_lado_so_muda_o_foco(self):
        self.montar()
        self.definir("dividirCom(e2); ativar(e2)")
        self.assertEqual(self.rodar("[tela2.divisao.esq.nome, tela2.divisao.dir.nome, tela2.abaAtiva.nome]"), ["um", "dois", "dois"])

    def test_os_dois_lados_tem_webgl_e_o_resto_nao(self):
        self.montar()
        self.definir("dividirCom(e2)")
        self.assertEqual(self.rodar("[e1.webgl, e2.webgl, e3.webgl]"), [True, True, False])
        self.definir("desfazerDivisao()")
        self.assertEqual(self.rodar("[e1.webgl, e2.webgl, e3.webgl]"), [True, False, False])

    def test_fechar_um_lado_desfaz_e_o_foco_vai_pro_que_ficou(self):
        self.montar()
        self.definir("dividirCom(e3); fechar(e1)")
        self.assertIsNone(self.rodar("tela2.divisao"))
        self.assertEqual(self.rodar("tela2.abaAtiva.nome"), "tres", "e nao a vizinha da lista")
        self.assertFalse(self.rodar("e3.tela.classList.contains('lado')"))

    def test_fechar_o_lado_sem_foco_tambem_desfaz(self):
        self.montar()
        self.definir("dividirCom(e3); fechar(e3)")
        self.assertIsNone(self.rodar("tela2.divisao"))
        self.assertEqual(self.rodar("tela2.abaAtiva.nome"), "um")

    def test_grade_e_divisao_se_excluem(self):
        self.montar()
        self.definir("dividirCom(e2); alternarGrade(true)")
        self.assertIsNone(self.rodar("tela2.divisao"))
        self.assertTrue(self.rodar("tela2.emGrade"))
        self.definir("dividirCom(e3)")
        self.assertFalse(self.rodar("tela2.emGrade"))
        self.assertEqual(self.rodar("tela2.divisao.dir.nome"), "tres")

    def test_o_atalho_divide_com_a_aba_de_antes_e_de_novo_desfaz(self):
        self.montar()
        # a de antes e a tres, mesmo com a dois logo depois na lista
        self.definir("ativar(e3); ativar(e1); alternarDivisao()")
        self.assertEqual(self.rodar("[tela2.divisao.esq.nome, tela2.divisao.dir.nome]"), ["um", "tres"])
        self.definir("alternarDivisao()")
        self.assertIsNone(self.rodar("tela2.divisao"))

    def test_fracao_fora_da_faixa_volta_pra_borda(self):
        self.definir("tela2.cfg = {fracaoDivisao: 0.95}")
        self.assertEqual(self.rodar("fracaoDaDivisao()"), 0.8)
        self.definir("tela2.cfg = {}")
        self.assertEqual(self.rodar("fracaoDaDivisao()"), 0.5)
        self.assertEqual(self.rodar("colunasDaDivisao(0.6)"), "minmax(0, 0.6fr) 8px minmax(0, 0.4fr)")


def html() -> str:
    return (UI / "index.html").read_text(encoding="utf-8")


class AtalhosNaTela(unittest.TestCase):
    def test_atalhos_nas_configuracoes_e_na_tela_vazia(self):
        texto = html()
        secao = texto[texto.index('id="sec-atalhos"'):texto.index('id="sec-sobre"')]
        self.assertIn("<kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>P</kbd>", secao)
        self.assertIn("<kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>D</kbd>", secao)
        vazia = texto[texto.index('id="palco-vazio"'):texto.index("</dl>", texto.index('id="palco-vazio"'))]
        self.assertIn("<dt>Ctrl+Shift+P</dt>", vazia)
        self.assertIn("<dt>Ctrl+Shift+D</dt>", vazia)

    def test_botao_direito_da_aba_divide(self):
        self.assertIn('data-aba="dividir">Dividir com esta aba<', html())
        self.assertIn('data-aba="desfazer-divisao"', html())

    def test_a_paleta_nasce_depois_de_todas_as_caixas(self):
        """Caixa que nasce antes do painel no HTML abre atras dele com o mesmo z-index."""
        texto = html()
        self.assertGreater(texto.index('id="paleta"'), texto.index('id="veu-config"'))
        css = (UI / "style.css").read_text(encoding="utf-8")
        z = int(re.search(r"\.paleta-veu \{[^}]*z-index: (\d+)", css).group(1))
        self.assertGreater(z, 40, "acima do veu das configuracoes")

    def test_icones_da_paleta_sao_svg_e_nao_caractere(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        bloco = app[app.index("const ICONES_DA_PALETA"):app.index("function iconeDaPaleta")]
        self.assertNotRegex(bloco, r"[\u2190-\u2bff]")


MONTAR_DIVISAO = """
  desenho = { emUso: "software" };
  cfg = { tamanhoFonte: 13, entrelinha: 1.2, espacoLetra: 0, cursor: "bar", cursorPisca: false, historico: 1000, fonte: "Consolas", fracaoDivisao: 0.5 };
  const a = new Aba({ id: "pwsh", nome: "PowerShell" }, null, false);
  const b = new Aba({ id: "cmd", nome: "Prompt" }, null, false);
  const c = new Aba({ id: "bash", nome: "Git Bash" }, null, false);
  ativar(a);
  dividir(a, b, a);
  a.ajustar(); b.ajustar();
  const caixa = (x) => { const r = x.getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }; };
"""


class DesenhoDaDivisaoEDaPaleta(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.medidas = medir_varios([
            """(function () {""" + MONTAR_DIVISAO + """
              const d = document.getElementById("divisoria");
              const antes = { a: caixa(a.tela), b: caixa(b.tela), d: caixa(d), colsA: a.term.cols, colsB: b.term.cols,
                visB: getComputedStyle(b.tela).visibility, visC: getComputedStyle(c.tela).visibility,
                chapeu: getComputedStyle(b.chapeu).display };
              const p = document.getElementById("palco").getBoundingClientRect();
              d.dispatchEvent(new MouseEvent("mousedown", { button: 0, bubbles: true, clientX: p.left + p.width / 2, clientY: 300 }));
              window.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: p.left + p.width * 0.7, clientY: 300 }));
              window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
              a.ajustar(); b.ajustar();
              const depois = { a: caixa(a.tela), b: caixa(b.tela), colsA: a.term.cols, colsB: b.term.cols, fracao: cfg.fracaoDivisao };
              b.tela.dispatchEvent(new MouseEvent("mousedown", { button: 0, bubbles: true }));
              const foco = abaAtiva === b;
              desfazerDivisao();
              b.ajustar();
              const sozinha = { b: caixa(b.tela), visA: getComputedStyle(a.tela).visibility, divisoria: d.hidden, palco: p.width };
              return JSON.stringify({ antes, depois, foco, sozinha });
            })()""",
            """(function () {""" + MONTAR_DIVISAO + """
              abrirPaleta();
              const paleta = document.getElementById("paleta");
              const lista = document.getElementById("paleta-lista");
              const vazia = { aberta: !paleta.hidden, foco: document.activeElement.id, veu: veuAberto(),
                caixa: caixa(document.querySelector(".paleta")), itens: lista.querySelectorAll(".paleta-item").length,
                grupos: Array.from(lista.querySelectorAll(".paleta-grupo")).map((g) => g.textContent),
                escolhido: lista.querySelector(".paleta-item.escolhido .paleta-rotulo").textContent,
                transborda: lista.scrollWidth > lista.clientWidth + 1, janela: window.innerWidth };
              const termo = document.getElementById("paleta-termo");
              termo.value = "config apar";
              termo.dispatchEvent(new Event("input"));
              const primeiro = lista.querySelector(".paleta-item.escolhido .paleta-rotulo");
              const filtrando = { primeiro: primeiro.textContent, realce: primeiro.querySelectorAll("b").length };
              termo.value = "config";
              termo.dispatchEvent(new Event("input"));
              termo.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
              const desceu = lista.querySelector(".paleta-item.escolhido").dataset.indice;
              termo.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
              const fechou = paleta.hidden;
              window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP", key: "P", ctrlKey: true, shiftKey: true, bubbles: true }));
              const peloAtalho = !paleta.hidden;
              termo.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP", key: "P", ctrlKey: true, shiftKey: true, bubbles: true }));
              return JSON.stringify({ vazia, filtrando, desceu, fechou, peloAtalho, fechouDeNovo: paleta.hidden });
            })()""",
        ])

    def test_os_dois_lados_aparecem_lado_a_lado_com_a_divisoria_no_meio(self):
        m = self.medidas[0]["antes"]
        self.assertEqual(m["visB"], "visible")
        self.assertEqual(m["visC"], "hidden", "a aba que nao esta em lado nenhum continua escondida")
        self.assertLess(m["a"]["r"], m["d"]["l"] + 1)
        self.assertLessEqual(m["d"]["r"], m["b"]["l"] + 1)
        self.assertAlmostEqual(m["a"]["w"], m["b"]["w"], delta=2)
        self.assertEqual(m["a"]["t"], m["b"]["t"])
        self.assertEqual(m["chapeu"], "flex", "cada lado diz de quem e, como o painel da grade")
        self.assertAlmostEqual(m["colsA"], m["colsB"], delta=1)

    def test_arrastar_a_divisoria_muda_a_largura_e_os_terminais_remedem(self):
        antes, depois = self.medidas[0]["antes"], self.medidas[0]["depois"]
        self.assertAlmostEqual(depois["fracao"], 0.7, delta=0.03)
        self.assertGreater(depois["a"]["w"], antes["a"]["w"] * 1.25)
        self.assertLess(depois["b"]["w"], antes["b"]["w"] * 0.75)
        self.assertGreater(depois["colsA"], antes["colsA"])
        self.assertLess(depois["colsB"], antes["colsB"])

    def test_clique_num_lado_da_o_foco_e_desfazer_volta_a_largura_inteira(self):
        m = self.medidas[0]
        self.assertTrue(m["foco"])
        self.assertTrue(m["sozinha"]["divisoria"])
        # o lado em foco fica com a largura inteira, e o outro volta a se esconder
        self.assertEqual(m["sozinha"]["visA"], "hidden")
        self.assertGreater(m["sozinha"]["b"]["w"], m["sozinha"]["palco"] - 30)

    def test_paleta_abre_no_topo_com_o_foco_no_campo_e_conta_como_caixa(self):
        m = self.medidas[1]["vazia"]
        self.assertTrue(m["aberta"])
        self.assertEqual(m["foco"], "paleta-termo")
        self.assertEqual(m["veu"], "paleta", "com ela aberta nenhuma tecla vai pro shell")
        self.assertLess(m["caixa"]["t"], 120)
        self.assertAlmostEqual(m["caixa"]["l"] + m["caixa"]["w"] / 2, m["janela"] / 2, delta=4)
        self.assertGreater(m["itens"], 10)
        self.assertEqual(m["grupos"][:2], ["Abas abertas", "Ações"])
        self.assertIn("Configurações", m["grupos"])
        self.assertEqual(m["escolhido"], "PowerShell", "a primeira linha ja vem escolhida")
        self.assertFalse(m["transborda"])

    def test_filtrar_setas_esc_e_o_atalho_que_abre_e_fecha(self):
        m = self.medidas[1]
        self.assertEqual(m["filtrando"]["primeiro"], "Configurações, Aparência")
        self.assertGreater(m["filtrando"]["realce"], 0)
        self.assertEqual(m["desceu"], "1")
        self.assertTrue(m["fechou"])
        self.assertTrue(m["peloAtalho"])
        self.assertTrue(m["fechouDeNovo"])


if __name__ == "__main__":
    unittest.main()
