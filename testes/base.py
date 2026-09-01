"""Isolamento dos testes.

Nada aqui pode encostar nos dados de verdade. Cada teste ganha uma pasta temporaria e
os modulos passam a apontar pra ela, incluindo os caminhos que foram calculados na hora
do import. No fim tudo volta ao lugar.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dbx_term import caminhos, coletor, config, ferramentas, perfis, sessao_salva, uso  # noqa: E402


class ComPastaPropria(unittest.TestCase):
    """Base que troca a pasta de dados do app por uma descartavel."""

    def setUp(self) -> None:
        self.raiz = Path(tempfile.mkdtemp(prefix="dbx-teste-"))
        self.casa = self.raiz / "casa"
        (self.casa / ".claude").mkdir(parents=True)

        self._antes = {
            (caminhos, "RAIZ"): caminhos.RAIZ,
            (caminhos, "PERFIS"): caminhos.PERFIS,
            (perfis, "ARQUIVO"): perfis.ARQUIVO,
            (perfis, "PERFIS"): perfis.PERFIS,
            (perfis, "RAIZ"): perfis.RAIZ,
            (ferramentas, "ARQUIVO"): ferramentas.ARQUIVO,
            (ferramentas, "RAIZ"): ferramentas.RAIZ,
            (config, "ARQUIVO"): config.ARQUIVO,
            (config, "RAIZ"): config.RAIZ,
            (coletor, "PASTA"): coletor.PASTA,
            (coletor, "RAIZ"): coletor.RAIZ,
            (uso, "CACHE"): uso.CACHE,
            (uso, "RAIZ"): uso.RAIZ,
            (sessao_salva, "ARQUIVO"): sessao_salva.ARQUIVO,
            (sessao_salva, "RAIZ"): sessao_salva.RAIZ,
        }

        dados = self.raiz / "dados"
        caminhos.RAIZ = dados
        caminhos.PERFIS = dados / "perfis"
        perfis.RAIZ = dados
        perfis.PERFIS = dados / "perfis"
        perfis.ARQUIVO = dados / "perfis.json"
        ferramentas.RAIZ = dados
        ferramentas.ARQUIVO = dados / "ferramentas.json"
        config.RAIZ = dados
        config.ARQUIVO = dados / "config.json"
        coletor.RAIZ = dados
        coletor.PASTA = dados / "sessoes"
        uso.RAIZ = dados
        uso.CACHE = dados / "uso-cache.json"
        sessao_salva.RAIZ = dados
        sessao_salva.ARQUIVO = dados / "abas.json"

        perfis._IDENTIDADE.clear()

    def tearDown(self) -> None:
        for (modulo, atributo), valor in self._antes.items():
            setattr(modulo, atributo, valor)
        shutil.rmtree(self.raiz, ignore_errors=True)

    # -- ajudantes ------------------------------------------------------

    def pasta_de_projeto(self, base: Path, nome: str) -> Path:
        alvo = base / "projects" / nome
        alvo.mkdir(parents=True, exist_ok=True)
        return alvo

    def escrever_transcript(self, alvo: Path, registros: list[dict]) -> Path:
        import json

        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in registros) + "\n",
            encoding="utf-8",
        )
        return alvo

    def resposta(self, quando: str, texto: str = "ok", modelo: str = "claude-opus-5", **consumo) -> dict:
        uso_padrao = {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens_details": {"thinking_tokens": 5},
        }
        uso_padrao.update(consumo)
        return {
            "type": "assistant",
            "timestamp": quando,
            "cwd": r"C:\proj",
            "message": {
                "id": f"msg-{quando}-{texto}",
                "role": "assistant",
                "model": modelo,
                "content": [{"type": "text", "text": texto}],
                "usage": uso_padrao,
            },
        }

    def pedido(self, quando: str, texto: str) -> dict:
        return {
            "type": "user",
            "timestamp": quando,
            "cwd": r"C:\proj",
            "message": {"role": "user", "content": [{"type": "text", "text": texto}]},
        }
