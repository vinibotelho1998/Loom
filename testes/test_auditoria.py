"""Regressoes da auditoria, somente eventos e arquivos sinteticos."""
import json
import unittest
from unittest.mock import patch

from base import ComPastaPropria
from loom import cliente_nucleo, perfis, registro, uso
from test_pagina import ComPagina


class Hidratacao(unittest.TestCase):
    def test_saida_e_fim_antes_de_adotar_chegam_na_ordem(self):
        from test_nucleo import garantir_app
        garantir_app()
        cliente = cliente_nucleo.ClienteDoNucleo("fake", "fake")
        cliente._atender({"tipo": "estado", "sessoes": [{"id": "a1"}]})
        cliente._atender({"tipo": "saida", "id": "a1", "texto": "ultima linha"})
        cliente._atender({"tipo": "saiu", "id": "a1", "codigo": 4})
        sessao = cliente_nucleo.SessaoRemota(cliente, "a1", viva=True)
        eventos = []
        sessao.output.connect(lambda sid, texto: eventos.append(texto))
        sessao.exited.connect(lambda sid, codigo: eventos.append(codigo))
        cliente.registrar(sessao)
        cliente.terminar_adocao()
        self.assertEqual(eventos, ["ultima linha", 4])
        self.assertFalse(sessao.alive)
        self.assertFalse(cliente._pendentes)

    def test_failed_to_start_tem_recuperacao_limitada_e_preserva_shell(self):
        from loom import nucleo
        from test_nucleo import garantir_app, esperar, ShellFalso
        import uuid
        garantir_app()
        with patch.object(nucleo, "ESPERAS_DE_RELANCE", (0,0)), patch.object(nucleo, "TETO_DE_RELANCES", 2):
            h = nucleo.Hospedeiro(["C:/loom-teste-nao-existe-" + uuid.uuid4().hex + ".exe"],
                                 "loom-failed-" + uuid.uuid4().hex, "fake", ShellFalso)
            try:
                h.nucleo.abrir({"tipo":"abrir", "id":"a1", "argv":["fake"]})
                h.subir_tela()
                self.assertTrue(esperar(lambda: len(h.supervisor._relances) == 2 and h._processo is None and not h._relance.isActive()))
                self.assertEqual(h.nucleo.vivas(), 1)
            finally:
                h._relance.stop()
                h.nucleo.encerrar_tudo()
                h.servidor._servidor.close()


class SettingsPreservados(ComPastaPropria):
    def test_settings_invalido_nao_e_reescrito(self):
        perfil = perfis.Perfil("p1", "teste", "blue")
        perfil.config_dir.mkdir(parents=True)
        arquivo = perfil.config_dir / "settings.json"
        for texto in ('{"env": ', '[]', '{"env": []}', '{"hooks": []}'):
            arquivo.write_text(texto, encoding="utf-8")
            self.assertFalse(perfis.instalar_coletor(perfil, "fake", "fake"))
            self.assertEqual(arquivo.read_text(encoding="utf-8"), texto)

    def test_grupo_misto_preserva_gancho_e_opcoes_da_pessoa(self):
        externo = {"type": "command", "command": "meu-gancho", "timeout": 20}
        atual = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": "loom.coletor --hook p1"}, externo]}]}}
        perfis._pendurar_ganchos(atual, "novo loom.coletor", "p1")
        self.assertEqual(atual["hooks"]["SessionStart"][0], {"matcher": "startup", "hooks": [externo]})
        perfis._tirar_ganchos(atual)
        self.assertEqual(atual, {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [externo]}]}})

    def test_log_separado_por_processo(self):
        with patch.object(registro.os, "getpid", return_value=123):
            primeiro = registro.arquivo()
        with patch.object(registro.os, "getpid", return_value=456):
            segundo = registro.arquivo()
        self.assertNotEqual(primeiro, segundo)


class LinhasGrandes(ComPastaPropria):
    def test_linha_maior_que_orcamento_avanca_em_passadas_sem_perder_consumo(self):
        arquivo = self.raiz / "grande.jsonl"
        linha = json.dumps(self.resposta("2026-10-02T12:00:00Z", "x" * 600))
        arquivo.write_text(linha + "\n", encoding="utf-8")
        fim, registros, leituras = 0, [], []
        for _ in range(20):
            medicao = {}
            dados, fim = uso._ler_novidades(arquivo, fim, teto=100, medicao=medicao)
            registros.extend(dados)
            leituras.append(medicao.get("bytes", 0))
            if fim == arquivo.stat().st_size:
                break
        self.assertEqual(len(registros), 1)
        self.assertEqual(fim, arquivo.stat().st_size)
        self.assertTrue(all(n <= 100 for n in leituras))
        self.assertEqual(sum(leituras), fim)


class PaginaAuditada(ComPagina):
    def test_caminhos_no_powershell_sao_literais(self):
        alvo = "C:\\a$env:HOME & coisa's.txt"
        self.assertEqual(self.rodar("caminhoLiteral(" + json.dumps(alvo) + ", {shell:{id:'pwsh'},term:{}})"), "'C:\\a$env:HOME & coisa''s.txt'")

    def test_expansao_cmd_recusada_e_bash_escapa_apostrofo(self):
        self.assertIsNone(self.rodar("caminhoLiteral('C:/%TEMP%/a.txt', {shell:{id:'cmd'},term:{}})"))
        self.assertEqual(self.rodar("caminhoLiteral(\"a'b.txt\", {shell:{id:'bash'},term:{}})"), "'a'\"'\"'b.txt'")

    def test_links_absolutos_com_espacos_e_unicode(self):
        for caminho in ('C:\\Meus projetos\\área\\ação.py:42:3', '/home/eu/área nova/ação.py:2'):
            dados = self.rodar("(() => { let r; const a={term:{buffer:{active:{getLine:()=>({translateToString:()=>" + json.dumps(caminho) + "})}}}}; provedorDeArquivos(a).provideLinks(1,x=>r=x); return r.map(x=>x.text); })()")
            self.assertEqual(dados, [caminho])
