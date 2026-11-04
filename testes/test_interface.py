"""A interface refeita: um lugar so pro uso, Configuracoes em secoes de produto e o que saiu da tela.

Tres jeitos de olhar. O HTML lido como texto, pra estrutura. O QJSEngine com a pagina falsa, pras
regras do app.js e do painel. E o motor web de verdade, num processo filho, pro que so existe
desenhado: foco, select cortado e as linhas que dependem de outra.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from test_layout import medir_varios
from test_pagina import ComPagina
from test_painel_uso import CONTRATO, ComPainel

UI = Path(__file__).resolve().parent.parent / "ui"


def html() -> str:
    return (UI / "index.html").read_text(encoding="utf-8")


def secao(nome: str) -> str:
    texto = html()
    inicio = texto.index(f'id="sec-{nome}"')
    fim = texto.find('<div class="secao"', inicio + 1)
    return texto[inicio:fim if fim > 0 else texto.index("</section>", inicio)]


class UmLugarSoProUso(unittest.TestCase):
    def test_a_tela_de_uso_antiga_saiu_inteira(self):
        texto = html()
        self.assertFalse((UI / "stats.js").exists())
        self.assertNotIn("stats.js", texto)
        for alvo in ('id="sec-uso"', 'id="corpo-uso"', 'data-alvo="uso"', 'id="abrir-stats"'):
            self.assertNotIn(alvo, texto, alvo)

    def test_a_pilula_e_o_unico_botao_do_painel(self):
        texto = html()
        self.assertEqual(texto.count('id="alternar-uso"'), 1)
        botao = re.search(r'<button[^>]*id="alternar-uso"[^>]*>', texto).group(0)
        self.assertIn('class="pilula"', botao)
        self.assertIn('aria-pressed="false"', botao)
        self.assertIn("Ctrl+Shift+U", botao)
        self.assertIn('class="pilula-ponto"', texto[texto.index('id="alternar-uso"'):][:200])
        # o estado ligado se ve: a folha pinta a pilula pressionada
        self.assertIn('.pilula[aria-pressed="true"]', (UI / "style.css").read_text(encoding="utf-8"))

    def test_ponto_de_nivel_segue_na_pilula(self):
        self.assertIn('querySelector("#alternar-uso .pilula-ponto")', (UI / "app.js").read_text(encoding="utf-8"))


class OQueSaiuDaTela(unittest.TestCase):
    def test_sem_quadros_por_segundo_sem_opcoes_do_painel_e_sem_atualizar_sessoes(self):
        texto = html()
        app = (UI / "app.js").read_text(encoding="utf-8")
        for alvo in ('id="fps"', 'id="sobre-fps"', 'id="cfg-mostrarFps"', 'id="cfg-painelUso"',
                     'id="cfg-larguraPainelUso"', 'id="atualizar-sessoes"'):
            self.assertNotIn(alvo, texto, alvo)
        for alvo in ("medirFps", '"mostrarFps"', '["painelUso"', '["larguraPainelUso"', "atualizar-sessoes"):
            self.assertNotIn(alvo, app, alvo)


class ConfiguracoesEmSecoes(unittest.TestCase):
    def test_secoes_na_ordem_e_cada_uma_com_icone(self):
        texto = html()
        nav = texto[texto.index('<aside class="painel-nav">'):texto.index("</aside>", texto.index('<aside class="painel-nav">'))]
        itens = re.findall(r'<button class="nav-item[^"]*" data-alvo="(\w+)"><svg[^>]*stroke="currentColor"', nav)
        self.assertEqual(itens, ["geral", "aparencia", "claude", "contas", "ferramentas", "atalhos", "sobre"])
        for alvo in itens:
            self.assertIn(f'id="sec-{alvo}"', texto, alvo)

    def test_cada_ajuste_mora_na_sua_secao(self):
        donos = {
            "geral": ("shellPadrao", "perfilPadrao", "abrirCliDaConta", "restaurarAbas", "guardarHistorico",
                      "retomarConversas", "confirmarFechar", "fecharAoSair", "editor", "historico"),
            "aparencia": ("fonte", "tamanhoFonte", "entrelinha", "espacoLetra", "cursor", "cursorPisca",
                          "densidade", "desenho", "mostrarRecursos"),
            "claude": ("mostrarUsoNaBarra", "esconderStatusLine", "confiarPastas", "avisarQuandoPronta",
                       "avisoDeLimite"),
        }
        for nome, chaves in donos.items():
            trecho = secao(nome)
            for chave in chaves:
                self.assertIn(f'id="cfg-{chave}"', trecho, f"{chave} fora de {nome}")
        self.assertIn('id="amostra"', secao("aparencia"))

    def test_as_duas_chaves_da_persistencia_estao_na_tela_e_no_app(self):
        trecho = secao("geral")
        self.assertIn("Guardar o que cada aba mostrou", trecho)
        self.assertIn("Retomar as conversas ao reabrir", trecho)
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('["guardarHistorico", "check"]', app)
        self.assertIn('["retomarConversas", "check"]', app)
        # as duas apagam com o reabrir desligado
        self.assertEqual(len(re.findall(r'data-depende="restaurarAbas"', trecho)), 2)

    def test_atalhos_tem_secao_propria_e_sairam_do_sobre(self):
        self.assertIn("<kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>T</kbd>", secao("atalhos"))
        sobre = secao("sobre")
        self.assertNotIn("<kbd>", sobre)
        self.assertNotIn("<dl", sobre)
        self.assertIn('id="sobre-versao"', sobre)
        self.assertIn('id="sobre-raiz"', sobre)

    def test_rotulos_comecam_com_maiuscula(self):
        for rotulo in re.findall(r'<div class="rot"><span>([^<]+)</span>', html()):
            self.assertTrue(rotulo[0].isupper(), rotulo)
        for titulo in re.findall(r'<h3 class="grupo-titulo">([^<]+)</h3>', html()):
            self.assertTrue(titulo[0].isupper(), titulo)


class MenuDeNovaAba(unittest.TestCase):
    def test_acoes_com_icone_desenhado_e_nao_caractere(self):
        texto = html()
        for alvo in ("usar-logada", "nova-conta"):
            botao = re.search(rf'<button[^>]*id="{alvo}"[^>]*>(.*?)</button>', texto, re.S).group(1)
            self.assertIn('<svg class="menu-icone"', botao, alvo)
            self.assertNotRegex(botao, r">\s*[↳+]", alvo)

    def test_adm_so_aparece_com_o_mouse_na_linha(self):
        css = (UI / "style.css").read_text(encoding="utf-8")
        self.assertIn(".menu-item:hover .menu-escudo", css)
        refino = css[css.index("/* ---------------------------------------------------------------- refino */"):]
        bloco = refino[refino.index(".menu-escudo {"):refino.index("}", refino.index(".menu-escudo {"))]
        self.assertIn("opacity: 0;", bloco)
        self.assertIn(".menu-escudo.indisponivel { opacity: 0; }", refino)


class ContaDoPainelPelaAba(ComPagina):
    def tearDown(self) -> None:
        self.definir("estado.perfis = []; estado.abaAtiva = null")

    def conta(self, perfis: list, perfil_da_aba: str | None) -> str:
        self.definir(f"estado.perfis = {json.dumps(perfis)};")
        if perfil_da_aba is None:
            self.definir("estado.abaAtiva = {ehPainel: false, perfil: null};")
        else:
            self.definir(f"estado.abaAtiva = {{ehPainel: false, perfil: estado.perfis.find((p) => p.id === {json.dumps(perfil_da_aba)})}};")
        return self.rodar("contaDoPainel()")

    def test_id_do_relatorio_por_provedor_e_perfil(self):
        perfis = [
            {"id": "aaaa1111", "nome": "Pessoal", "ferramenta": "claude"},
            {"id": "cccc3333", "nome": "Codex", "ferramenta": "codex"},
        ]
        self.assertEqual(self.conta(perfis, "aaaa1111"), "claude:aaaa1111")
        self.assertEqual(self.conta(perfis, "cccc3333"), "codex:cccc3333")
        self.assertEqual(self.conta(perfis, None), "claude:padrao")

    def test_aba_sem_perfil_com_a_principal_cadastrada_vai_pro_id_dela(self):
        perfis = [{"id": "bbbb2222", "nome": "Minha", "ferramenta": "claude", "padrao": True}]
        self.assertEqual(self.conta(perfis, None), "claude:bbbb2222")


class PainelAbreNaConta(ComPainel):
    def ligar(self) -> None:
        resposta = json.dumps(CONTRATO)
        self.definir(
            f"estado.cfg = {{}}; estado.bridge = ponteFalsa(true, {json.dumps(resposta)});"
            " estado.abaAtiva = abaFalsa(); P.ligar();"
            ' var painel = elementosFalsos["uso-lateral"], botao = elementosFalsos["alternar-uso"];'
        )

    def test_abre_fechado_no_provedor_e_na_conta_pedidos(self):
        self.ligar()
        self.assertTrue(self.rodar("painel.hidden"))
        self.definir('P.mostrarConta("codex:padrao")')
        self.assertEqual(self.rodar('[painel.hidden, botao.getAttribute("aria-pressed"), estado.cfg.painelUso]'), [False, "true", True])
        desenhado = self.rodar("painel.innerHTML")
        self.assertIn("codex:padrao", desenhado)
        # o resumo do topo lista todas as contas; o cartao aberto e que tem de ser o pedido
        self.assertNotIn('data-conta="claude:84092a89"', desenhado)
        self.assertIn('data-conta="codex:padrao"', desenhado)

    def test_ja_aberto_so_troca_a_conta(self):
        self.ligar()
        self.definir("P.alternar(true)")
        self.assertIn("claude:84092a89", self.rodar("painel.innerHTML"))
        gravados = self.rodar("estado.bridge.gravados.length")
        self.definir('P.mostrarConta("codex:padrao")')
        self.assertIn("codex:padrao", self.rodar("painel.innerHTML"))
        # trocar de conta com ele aberto nao grava nem remede nada
        self.assertEqual(self.rodar("estado.bridge.gravados.length"), gravados)


class DesenhadoDeVerdade(unittest.TestCase):
    """Medido no motor web: o que a folha e o app.js fazem juntos."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.medidas = medir_varios(
            [
                # as duas linhas dependentes apagam com o reabrir desligado, e nascem ligadas sem chave
                """(function () {
                  cfg = { restaurarAbas: false, fonte: "Hack", tamanhoFonte: 14, entrelinha: 1.25, espacoLetra: 0 };
                  preencherCampos();
                  const guardar = document.getElementById("cfg-guardarHistorico");
                  const retomar = document.getElementById("cfg-retomarConversas");
                  const linha = guardar.closest(".linha");
                  const antes = [guardar.checked, retomar.checked, guardar.disabled, linha.classList.contains("desligada")];
                  cfg.restaurarAbas = true;
                  preencherCampos();
                  return JSON.stringify({ antes: antes, depois: [guardar.disabled, linha.classList.contains("desligada")] });
                })()""",
                # o painel abre sem o anel de foco no item do menu, e a conta padrao cabe inteira
                """(function () {
                  document.getElementById("veu-config").hidden = false;
                  mostrarSecao("geral");
                  abrirVeu("veu-config");
                  const ativo = document.querySelector(".nav-item.ativa");
                  const sel = document.getElementById("cfg-perfilPadrao");
                  sel.innerHTML = "<option>Principal, do ~/.claude</option>";
                  const saida = {
                    foco: document.activeElement.className,
                    anel: getComputedStyle(ativo).outlineStyle,
                    cabe: sel.scrollWidth <= sel.clientWidth + 1,
                    secao: document.querySelector(".secao.ativa").id,
                  };
                  mostrarSecao("terminal");
                  saida.antiga = document.querySelector(".secao.ativa").id;
                  return JSON.stringify(saida);
                })()""",
            ]
        )

    def test_linhas_que_dependem_do_reabrir(self):
        medida = self.medidas[0]
        self.assertEqual(medida["antes"], [True, True, True, True])
        self.assertEqual(medida["depois"], [False, False])

    def test_painel_abre_sem_anel_e_sem_cortar_a_conta(self):
        medida = self.medidas[1]
        self.assertIn("painel", medida["foco"])
        self.assertEqual(medida["anel"], "none")
        self.assertTrue(medida["cabe"])
        self.assertEqual(medida["secao"], "sec-geral")
        # a secao Terminal de antes cai na Geral
        self.assertEqual(medida["antiga"], "sec-geral")


if __name__ == "__main__":
    unittest.main()
