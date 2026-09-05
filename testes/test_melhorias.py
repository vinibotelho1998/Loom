"""Testes das mudancas de desempenho e de comportamento feitas na revisao.

Cada um prova um comportamento que antes nao existia ou estava errado, e todos foram
conferidos ao contrario: quebrando o codigo de proposito, o teste correspondente falha.
"""

from __future__ import annotations

import json
import os
import time

from base import ComPastaPropria

from dbx_term import coletor, config, perfis, sessao_salva, uso


class LeituraIncremental(ComPastaPropria):
    """O medidor le em binario e por blocos, guardando o deslocamento em bytes."""

    def escrever(self, linhas: list[str]) -> None:
        self.arquivo.write_text("".join(l + "\n" for l in linhas), encoding="utf-8")

    def setUp(self) -> None:
        super().setUp()
        self.arquivo = self.casa / "projects" / "p" / "conversa.jsonl"
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)

    def linha(self, ident: str, saida: int = 20) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-09-04T10:00:00Z",
                "cwd": "C:\\proj",
                "message": {
                    "id": ident,
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 1, "output_tokens": saida},
                },
            },
            ensure_ascii=False,
        )

    def test_acento_nao_desalinha_o_deslocamento(self):
        """O offset e em bytes. Contando em caracteres, a segunda leitura comeria uma linha."""
        acentuada = self.linha("m1").replace('"ok"', '"integração e acentuação"')
        self.escrever([acentuada, self.linha("m2")])

        registros, fim = uso._ler_novidades(self.arquivo, 0)
        self.assertEqual(len(registros), 2)
        self.assertEqual(fim, self.arquivo.stat().st_size)

        # nada novo desde entao
        self.assertEqual(uso._ler_novidades(self.arquivo, fim), ([], fim))

    def test_linha_pela_metade_fica_para_a_proxima_passada(self):
        """Transcript e escrito ao vivo: a ultima linha pode estar incompleta."""
        with open(self.arquivo, "w", encoding="utf-8") as fh:
            fh.write(self.linha("m1") + "\n")
            fh.write(self.linha("m2")[:40])

        registros, fim = uso._ler_novidades(self.arquivo, 0)
        self.assertEqual(len(registros), 1)

        # o resto chega depois e a linha e lida inteira, uma vez so
        with open(self.arquivo, "a", encoding="utf-8") as fh:
            fh.write(self.linha("m2")[40:] + "\n")
        registros2, _ = uso._ler_novidades(self.arquivo, fim)
        self.assertEqual([r["message"]["id"] for r in registros2], ["m2"])

    def test_arquivo_que_encolheu_e_lido_do_comeco(self):
        self.escrever([self.linha("m1"), self.linha("m2")])
        _, fim = uso._ler_novidades(self.arquivo, 0)
        self.escrever([self.linha("m3")])
        registros, _ = uso._ler_novidades(self.arquivo, fim)
        self.assertEqual([r["message"]["id"] for r in registros], ["m3"])

    def test_teto_por_passada_entrega_o_resto_na_chamada_seguinte(self):
        """Indexar um transcript grande de uma vez segurava a janela por segundos."""
        anterior = uso.TETO_POR_PASSADA
        uso.TETO_POR_PASSADA = 400
        try:
            self.escrever([self.linha(f"m{i}") for i in range(20)])
            vistos = []
            fim = 0
            for _ in range(40):
                parte, novo = uso._ler_novidades(self.arquivo, fim)
                vistos.extend(r["message"]["id"] for r in parte)
                if novo == fim:
                    break
                fim = novo
            self.assertEqual(len(vistos), 20)
            self.assertEqual(fim, self.arquivo.stat().st_size)
        finally:
            uso.TETO_POR_PASSADA = anterior

    def test_cache_so_e_gravado_quando_ha_novidade(self):
        self.escrever([self.linha("m1")])
        pastas = {"c1": self.casa}
        uso.coletar(pastas)
        carimbo = uso.CACHE.stat().st_mtime_ns

        time.sleep(0.02)
        uso.coletar(pastas)
        self.assertEqual(uso.CACHE.stat().st_mtime_ns, carimbo)

        time.sleep(0.02)
        self.escrever([self.linha("m1"), self.linha("m2")])
        uso.coletar(pastas)
        self.assertNotEqual(uso.CACHE.stat().st_mtime_ns, carimbo)

    def test_serie_traz_o_gasto_quebrado_por_modelo(self):
        """O grafico empilha por modelo, entao cada dia precisa saber de quem foi o gasto."""
        hoje = time.strftime("%Y-%m-%dT%H:%M:%S")
        opus = json.loads(self.linha("m1", saida=100))
        opus["timestamp"] = hoje
        sonnet = json.loads(self.linha("m2", saida=40))
        sonnet["timestamp"] = hoje
        sonnet["message"]["model"] = "claude-sonnet-5"
        self.arquivo.write_text(
            json.dumps(opus) + "\n" + json.dumps(sonnet) + "\n", encoding="utf-8"
        )

        relatorio = uso.relatorio({"c1": self.casa})
        ponto = relatorio["c1"]["serie"][-1]
        self.assertEqual(ponto["porModelo"]["claude-opus-5"], 100)
        self.assertEqual(ponto["porModelo"]["claude-sonnet-5"], 40)


class ConfiguracaoNumerica(ComPastaPropria):
    def test_inteiro_vale_num_campo_decimal(self):
        """O range da tela manda 1, nao 1.0, e a entrelinha nunca chegava em 1."""
        salvo = config.salvar({"entrelinha": 1})
        self.assertEqual(salvo["entrelinha"], 1.0)
        self.assertIsInstance(salvo["entrelinha"], float)

    def test_decimal_num_campo_inteiro_vira_inteiro(self):
        salvo = config.salvar({"tamanhoFonte": 16.0})
        self.assertEqual(salvo["tamanhoFonte"], 16)
        self.assertIsInstance(salvo["tamanhoFonte"], int)

    def test_booleano_nao_passa_por_numero(self):
        """True e int em Python; sem barrar, ligar uma chave viraria fonte tamanho 1."""
        salvo = config.salvar({"tamanhoFonte": True})
        self.assertEqual(salvo["tamanhoFonte"], config.PADRAO["tamanhoFonte"])

    def test_texto_em_campo_numerico_e_ignorado(self):
        salvo = config.salvar({"historico": "muito"})
        self.assertEqual(salvo["historico"], config.PADRAO["historico"])


class AbasSalvas(ComPastaPropria):
    def test_conteudo_igual_nao_reescreve_o_arquivo(self):
        """Era uma gravacao atomica por ciclo de sessoes, o dia inteiro."""
        abas = [{"shell": "pwsh", "perfil": "p1", "pasta": "C:\\x", "conversa": "", "ativa": True}]
        sessao_salva.gravar(abas)
        carimbo = sessao_salva.ARQUIVO.stat().st_mtime_ns

        time.sleep(0.02)
        sessao_salva.gravar(abas)
        self.assertEqual(sessao_salva.ARQUIVO.stat().st_mtime_ns, carimbo)

        time.sleep(0.02)
        sessao_salva.gravar(abas + [dict(abas[0], perfil="p2")])
        self.assertNotEqual(sessao_salva.ARQUIVO.stat().st_mtime_ns, carimbo)

    def test_apelido_da_aba_sobrevive(self):
        sessao_salva.gravar([{"shell": "pwsh", "apelido": "build", "ativa": False}])
        self.assertEqual(sessao_salva.ler()[0]["apelido"], "build")


class LeituraDoColetor(ComPastaPropria):
    def gravar_sessao(self, nome: str, dados: dict) -> None:
        coletor.PASTA.mkdir(parents=True, exist_ok=True)
        (coletor.PASTA / f"{nome}.json").write_text(json.dumps(dados), encoding="utf-8")

    def test_arquivo_que_nao_mudou_nao_e_reparseado(self):
        """A tela pede isso varias vezes por segundo; reparsear tudo era custo de graca."""
        self.gravar_sessao("s1", {"sessao": "s1", "perfil": "c1", "lido": time.time()})
        self.assertEqual(len(coletor.ler_todas()), 1)
        marca = coletor._LIDAS[str(coletor.PASTA / "s1.json")]

        coletor.ler_todas()
        self.assertIs(coletor._LIDAS[str(coletor.PASTA / "s1.json")][2], marca[2])

    def test_arquivo_que_mudou_e_lido_de_novo(self):
        self.gravar_sessao("s1", {"sessao": "s1", "perfil": "c1", "lido": time.time()})
        coletor.ler_todas()
        self.gravar_sessao(
            "s1", {"sessao": "s1", "perfil": "c1", "lido": time.time(), "modelo": "Opus 5"}
        )
        # o mtime pode nao mudar em disco rapido; o tamanho muda e isso basta
        self.assertEqual(coletor.ler_todas()["s1"].get("modelo"), "Opus 5")

    def test_sessao_vencida_sai_do_disco_e_do_cache(self):
        self.gravar_sessao("velha", {"sessao": "velha", "lido": time.time() - coletor.VALIDADE - 10})
        self.assertEqual(coletor.ler_todas(), {})
        self.assertFalse((coletor.PASTA / "velha.json").exists())
        self.assertEqual(coletor._LIDAS, {})

    def test_as_duas_visoes_saem_de_uma_leitura_so(self):
        """getLimits passa o mesmo dicionario pras duas, em vez de ler a pasta duas vezes."""
        self.gravar_sessao(
            "s1",
            {
                "sessao": "s1",
                "perfil": "c1",
                "aba": "a1",
                "lido": time.time(),
                "faixas": {"five_hour": {"usado": 10}},
            },
        )
        vistas = coletor.ler_todas()
        self.assertEqual(coletor.limites_por_perfil(vistas)["c1"]["sessao"], "s1")
        self.assertEqual(coletor.por_aba(vistas)["a1"]["sessao"], "s1")

        # passando um dicionario vazio, nada e inventado a partir do disco
        self.assertEqual(coletor.limites_por_perfil({}), {})
        self.assertEqual(coletor.por_aba({}), {})


class TamanhoDaConta(ComPastaPropria):
    def test_soma_subpastas_e_guarda_a_conta(self):
        perfil = perfis.criar("teste")
        (perfil.config_dir / "sub").mkdir(parents=True, exist_ok=True)
        (perfil.config_dir / "a.txt").write_bytes(b"12345")
        (perfil.config_dir / "sub" / "b.txt").write_bytes(b"123")
        self.assertEqual(perfis.tamanho(perfil), 8)

        # dentro da validade o valor vem do cache, mesmo com o disco mudando
        (perfil.config_dir / "c.txt").write_bytes(b"9" * 100)
        self.assertEqual(perfis.tamanho(perfil), 8)

        perfis._TAMANHO.clear()
        self.assertEqual(perfis.tamanho(perfil), 108)


class IdentidadeGuardada(ComPastaPropria):
    def test_o_que_foi_descoberto_sobrevive_ao_fechar_o_app(self):
        """Sem isto, todo start esperava o CLI subir de novo pra dizer de quem e o login."""
        perfil = perfis.criar("teste")
        credencial = perfil.config_dir / ".credentials.json"
        credencial.write_text("{}", encoding="utf-8")
        carimbo = credencial.stat().st_mtime

        perfis._IDENTIDADE[str(perfil.config_dir)] = (carimbo, {"email": "a@b.c", "logado": True})
        perfis._gravar_identidade()

        # como se o app tivesse sido reaberto
        perfis._IDENTIDADE.clear()
        self.assertEqual(perfis.identidade(perfil).get("email"), "a@b.c")

    def test_credencial_trocada_invalida_o_que_estava_guardado(self):
        """O carimbo do disco e de uma credencial diferente: o cache nao pode valer pra esta."""
        perfil = perfis.criar("teste")
        credencial = perfil.config_dir / ".credentials.json"
        credencial.write_text("{}", encoding="utf-8")
        carimbo_real = credencial.stat().st_mtime

        perfis._IDENTIDADE[str(perfil.config_dir)] = (1.0, {"email": "antigo@b.c"})
        perfis._gravar_identidade()
        perfis._IDENTIDADE.clear()

        perfis._identidade_do_disco()
        lembrado = perfis._IDENTIDADE.get(str(perfil.config_dir))
        self.assertIsNotNone(lembrado)
        self.assertNotEqual(lembrado[0], carimbo_real)

        # sem CLI configurado pra essa ferramenta, identidade() nao pode inventar resposta,
        # e o carimbo velho do disco nao pode passar pelo teste de igualdade com o de agora
        sem_comando = perfis.mod_ferramentas.Ferramenta(
            id="claude", nome="x", env="", login=".credentials.json"
        )
        original = perfis.mod_ferramentas.por_id
        perfis.mod_ferramentas.por_id = lambda _id: sem_comando
        try:
            self.assertEqual(perfis.identidade(perfil), {})
        finally:
            perfis.mod_ferramentas.por_id = original


class AmbienteDoTerminal(ComPastaPropria):
    def test_marcas_do_claude_nao_sao_herdadas(self):
        """Rodando de dentro do Claude Code, o filho parava de gravar transcript."""
        from dbx_term import pty_session

        with_env = {
            "CLAUDE_CODE_CHILD_SESSION": "1",
            "ANTHROPIC_API_KEY": "x",
            "QT_SCALE_FACTOR": "2",
            "PATH": os.environ.get("PATH", ""),
        }
        antes = dict(os.environ)
        os.environ.update(with_env)
        try:
            limpo = pty_session.ambiente_limpo()
        finally:
            os.environ.clear()
            os.environ.update(antes)

        self.assertNotIn("CLAUDE_CODE_CHILD_SESSION", limpo)
        self.assertNotIn("ANTHROPIC_API_KEY", limpo)
        self.assertNotIn("QT_SCALE_FACTOR", limpo)
        self.assertIn("PATH", limpo)
