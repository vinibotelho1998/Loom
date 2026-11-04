"""Revisao independente: somente arquivos sinteticos e JavaScript no Qt."""

import json
import os
from collections import OrderedDict
from unittest.mock import patch

from base import ComPastaPropria
from loom import uso
from test_pagina import ComPagina


class CaminhosLiterais(ComPagina):
    def test_bracketed_paste_do_powershell_nao_autoriza_expansao(self):
        caminho = "C:/a$env:HOME & coisa's.txt"
        aba = "{shell:{id:'pwsh'},term:{modes:{bracketedPasteMode:true}}}"
        self.assertEqual(self.rodar("caminhoLiteral(" + json.dumps(caminho) + "," + aba + ")"),
                         "'C:/a$env:HOME & coisa''s.txt'")

    def test_bracketed_paste_do_bash_nao_autoriza_expansao(self):
        caminho = "/tmp/a$(comando) & coisa's.txt"
        aba = "{shell:{id:'bash'},term:{modes:{bracketedPasteMode:true}}}"
        self.assertEqual(self.rodar("caminhoLiteral(" + json.dumps(caminho) + "," + aba + ")"),
                         "'/tmp/a$(comando) & coisa'\"'\"'s.txt'")

    def test_links_recusam_urls_e_preservam_dois_caminhos_absolutos(self):
        texto = "https://host:8080/a.py:99 C:/ação/a.py:42 D:/área nova/b.js:2:3"
        expressao = "(() => { let r; const a={term:{buffer:{active:{getLine:()=>({translateToString:()=>" + json.dumps(texto) + "})}}}}; provedorDeArquivos(a).provideLinks(1,x=>r=x); return r.map(x=>x.text); })()"
        self.assertEqual(self.rodar(expressao), ["C:/ação/a.py:42", "D:/área nova/b.js:2:3"])

    def test_apostrofo_em_caminho_absoluto_nao_vira_cauda_relativa(self):
        caminho = "C:/O'Brien/projeto/ação.py:42"
        expressao = "(() => { let r; const a={term:{buffer:{active:{getLine:()=>({translateToString:()=>" + json.dumps(caminho) + "})}}}}; provedorDeArquivos(a).provideLinks(1,x=>r=x); return r.map(x=>x.text); })()"
        self.assertEqual(self.rodar(expressao), [caminho])

    def test_faixa_do_link_usa_celulas_com_emoji_cjk_e_acento_combinado(self):
        caminho = "C:/代码/ação.py:42"
        prefixo = "🚀 中 e\u0301 "
        # Cell width zero e a segunda coluna de um caractere largo. Uma
        # celula vazia de largura 1 vira espaco em translateToString.
        celulas = [{"chars": "🚀", "width": 2}, {"chars": "", "width": 0},
                   {"chars": "", "width": 1}, {"chars": "中", "width": 2},
                   {"chars": "", "width": 0}, {"chars": " ", "width": 1},
                   {"chars": "e\u0301", "width": 1}, {"chars": " ", "width": 1}]
        for caractere in caminho:
            largura = 2 if caractere in "代码" else 1
            celulas.append({"chars": caractere, "width": largura})
            if largura == 2:
                celulas.append({"chars": "", "width": 0})
        expressao = "(() => { let r; const cells=" + json.dumps(celulas) + "; const line={length:cells.length,getCell:i=>({getChars:()=>cells[i].chars,getWidth:()=>cells[i].width}),translateToString:()=>" + json.dumps(prefixo + caminho) + "}; const a={term:{buffer:{active:{getLine:()=>line}}}}; provedorDeArquivos(a).provideLinks(1,x=>r=x); return r.map(x=>({text:x.text,range:x.range})); })()"
        self.assertEqual(self.rodar(expressao), [{"text": caminho,
            "range": {"start": {"x": 9, "y": 1}, "end": {"x": len(celulas), "y": 1}}}])


class PonteHidratada(ComPastaPropria):
    def test_historico_saida_e_fim_na_hidratacao_chegam_a_ponte(self):
        from loom import bridge as mod_bridge, cliente_nucleo
        from test_nucleo import garantir_app, esperar
        garantir_app()
        cliente = cliente_nucleo.ClienteDoNucleo("fake", "fake")
        cliente._atender({"tipo": "estado", "sessoes": [
            {"id": "viva", "origem": {"shell": "pwsh"}, "historico": "antes\n"},
            {"id": "terminada", "origem": {"shell": "pwsh"}, "historico": "velho\n"}],
            "abas": [{"sessao": "viva", "ativa": True, "conversa": "uuid-chat", "titulo": "Conversa"}]})
        cliente._atender({"tipo": "saida", "id": "viva", "texto": "depois\n"})
        cliente._atender({"tipo": "saida", "id": "terminada", "texto": "ultima\n"})
        cliente._atender({"tipo": "saiu", "id": "terminada", "codigo": 7})
        with patch.object(mod_bridge, "RAIZ_DADOS", self.raiz / "dados"), \
             patch.object(mod_bridge.Bridge, "_revisar_vigia", lambda _: None), \
             patch.object(mod_bridge.mod_shells, "descobrir", lambda: []):
            ponte = mod_bridge.Bridge(nucleo=cliente)
        eventos = []
        ponte.output.connect(lambda sid, texto: eventos.append((sid, texto)))
        ponte.exited.connect(lambda sid, codigo: eventos.append((sid, codigo)))
        try:
            self.assertTrue(esperar(lambda: ("terminada", 7) in eventos))
            vivas = json.loads(ponte.liveSessions())
            self.assertEqual([v["id"] for v in vivas], ["viva"])
            self.assertEqual(vivas[0]["historico"], "antes\ndepois\n")
            self.assertEqual((vivas[0]["conversa"], vivas[0]["titulo"], vivas[0]["ativa"]),
                             ("uuid-chat", "Conversa", True))
            self.assertLess(eventos.index(("terminada", "ultima\n")),
                            eventos.index(("terminada", 7)))
            self.assertFalse(cliente._pendentes)
            self.assertFalse(cliente._hidratando)
        finally:
            ponte.shutdown()
            ponte.deleteLater()


class LeituraIncremental(ComPastaPropria):
    def tearDown(self):
        # O cache global conserva linhas abertas entre passadas. Nossos arquivos
        # temporarios ja vao desaparecer, entao tambem fechamos suas sobras.
        for chave in list(uso._LINHAS_PENDENTES):
            if chave[0].startswith(str(self.raiz.resolve())):
                uso._LINHAS_PENDENTES.pop(chave)[0].close()
        super().tearDown()

    def test_multiplas_linhas_com_cortes_e_ultima_linha_incompleta(self):
        arquivo = self.raiz / "varias.jsonl"
        registros = [self.resposta("2026-10-02T12:00:00Z", "á" * 180),
                     self.resposta("2026-10-02T12:00:01Z", "fim")]
        bruto = (json.dumps(registros[0], ensure_ascii=False) + "\n" +
                 json.dumps(registros[1], ensure_ascii=False)).encode("utf-8")
        arquivo.write_bytes(bruto)
        fim, recebidos, lidos = 0, [], 0
        for _ in range((len(bruto) // 47) + 3):
            medicao = {}
            dados, fim = uso._ler_novidades(arquivo, fim, teto=47, medicao=medicao)
            recebidos.extend(dados)
            lidos += medicao.get("bytes", 0)
            self.assertLessEqual(medicao.get("bytes", 0), 47)
        self.assertEqual(recebidos, registros[:1])
        self.assertEqual(lidos, len(bruto))
        with arquivo.open("ab") as fh:
            fh.write(b"\n")
        medicao = {}
        dados, fim = uso._ler_novidades(arquivo, fim, teto=47, medicao=medicao)
        self.assertEqual(dados, registros[1:])
        self.assertEqual(medicao["bytes"], 1)
        self.assertEqual(fim, arquivo.stat().st_size)

    def test_sobra_e_descartada_quando_arquivo_e_reescrito(self):
        arquivo = self.raiz / "reescrito.jsonl"
        antigo = json.dumps(self.resposta("2026-10-02T12:00:00Z", "x" * 600)).encode()
        novo = json.dumps(self.resposta("2026-10-02T12:00:01Z", "novo")).encode() + b"\n"
        arquivo.write_bytes(antigo)
        _, fim = uso._ler_novidades(arquivo, 0, teto=100)
        self.assertEqual(fim, 0)
        arquivo.write_bytes(novo)
        dados, fim = uso._ler_novidades(arquivo, fim)
        self.assertEqual(dados, [json.loads(novo)])
        self.assertEqual(fim, len(novo))

    def test_arquivo_reescrito_no_mesmo_tique_do_relogio_nao_herda_a_sobra(self):
        """No Windows, duas gravacoes seguidas podem sair com o mesmo carimbo, e o teste acima oscilava."""
        arquivo = self.raiz / "mesmo-tique.jsonl"
        antigo = json.dumps(self.resposta("2026-10-02T12:00:00Z", "x" * 600)).encode()
        novo = json.dumps(self.resposta("2026-10-02T12:00:01Z", "novo")).encode() + b"\n"
        arquivo.write_bytes(antigo)
        carimbo = arquivo.stat().st_mtime_ns
        _, fim = uso._ler_novidades(arquivo, 0, teto=100)
        arquivo.write_bytes(novo)
        os.utime(arquivo, ns=(carimbo, carimbo))
        dados, fim = uso._ler_novidades(arquivo, fim)
        self.assertEqual(dados, [json.loads(novo)])

    def test_linha_maior_que_teto_real_nao_fica_presa(self):
        arquivo = self.raiz / "imagem-grande.jsonl"
        # Limites menores mantem o teste barato, mas usam o mesmo caminho de
        # rollover do spool e passagem completa da linha acima do teto.
        registro = self.resposta("2026-10-02T12:00:00Z", "x" * 1024)
        arquivo.write_text(json.dumps(registro) + "\n", encoding="utf-8")
        with patch.object(uso, "BLOCO", 31), patch.object(uso, "TETO_POR_PASSADA", 95):
            fim, recebidos = 0, []
            for _ in range(30):
                dados, fim = uso._ler_novidades(arquivo, fim)
                recebidos.extend(dados)
                if fim == arquivo.stat().st_size:
                    break
        self.assertEqual(recebidos, [registro])
        self.assertEqual(fim, arquivo.stat().st_size)

    def test_sobras_limitadas_a_128_arquivos_fecham_temporarios_antigos(self):
        pendentes = OrderedDict()
        primeira = None
        with patch.object(uso, "_LINHAS_PENDENTES", pendentes):
            try:
                for i in range(130):
                    arquivo = self.raiz / f"aberto-{i}.jsonl"
                    arquivo.write_bytes(b'{"usage":12345')
                    uso._ler_novidades(arquivo, 0, teto=2)
                    if i == 0:
                        primeira = next(iter(pendentes.values()))[0]
                    self.assertLessEqual(len(pendentes), 128)
                self.assertEqual(len(pendentes), 128)
                self.assertTrue(primeira.closed)
            finally:
                for sobra, *_ in pendentes.values():
                    sobra.close()
