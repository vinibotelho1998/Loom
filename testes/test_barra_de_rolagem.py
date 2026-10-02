"""A barra de rolagem do Loom precisa aparecer: contraste e nada que faca o navegador ignorar as regras.

Antes o polegar era quase da cor do fundo, e as listas ainda tinham scrollbar-color, que no Chromium
novo faz as regras ::-webkit-scrollbar serem ignoradas. Quem usava nao via barra nenhuma.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"
LUGARES = (".xterm-viewport", ".lateral-lista", ".uso-lateral", ".painel-corpo")
CONTRASTE_MINIMO = 3.0


def css(nome: str) -> str:
    texto = (UI / nome).read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", texto, flags=re.S)


def luminancia(cor: str) -> float:
    canais = [int(cor[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lineares = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * lineares[0] + 0.7152 * lineares[1] + 0.0722 * lineares[2]


def contraste(a: str, b: str) -> float:
    claro, escuro = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


def cor_da_regra(texto: str, seletor: str, propriedade: str) -> str:
    """A cor da propriedade na regra cujo seletor termina em `seletor`, ou levanta se nao achar."""
    for bloco in re.finditer(r"([^{}]+)\{([^{}]*)\}", texto):
        seletores = [s.strip() for s in bloco.group(1).split(",")]
        if seletor in seletores:
            achada = re.search(propriedade + r"\s*:\s*(#[0-9a-fA-F]{6})", bloco.group(2))
            if achada:
                return achada.group(1)
    raise AssertionError(f"sem {propriedade} em {seletor}")


class BarraDeRolagem(unittest.TestCase):
    def test_nenhum_lugar_usa_scrollbar_color_nem_scrollbar_width(self):
        for arquivo in ("style.css", "painel-uso.css"):
            self.assertNotRegex(css(arquivo), r"scrollbar-(color|width)\s*:", arquivo)

    def test_o_polegar_tem_contraste_com_o_trilho_em_todos_os_lugares(self):
        texto = css("style.css")
        for lugar in LUGARES:
            trilho = cor_da_regra(texto, f"{lugar}::-webkit-scrollbar-track", "background")
            polegar = cor_da_regra(texto, f"{lugar}::-webkit-scrollbar-thumb", "background")
            self.assertGreaterEqual(contraste(polegar, trilho), CONTRASTE_MINIMO, lugar)

    def test_o_polegar_aceso_pelo_mouse_continua_visivel(self):
        texto = css("style.css")
        for lugar in LUGARES:
            trilho = cor_da_regra(texto, f"{lugar}::-webkit-scrollbar-track", "background")
            em_cima = cor_da_regra(texto, f"{lugar}::-webkit-scrollbar-thumb:hover", "background")
            self.assertGreaterEqual(contraste(em_cima, trilho), CONTRASTE_MINIMO, lugar)

    def test_nao_sobrou_regra_antiga_que_escondia_o_polegar_fora_do_hover(self):
        for arquivo in ("style.css", "painel-uso.css"):
            texto = css(arquivo)
            self.assertNotIn(":hover::-webkit-scrollbar-thumb", texto, arquivo)
            self.assertNotIn("background-clip: content-box", texto.split("scrollbar-thumb")[-1][:200], arquivo)

    def test_a_largura_da_barra_e_a_mesma_nos_quatro_lugares(self):
        texto = css("style.css")
        bloco = re.search(r"((?:[.\w-]+::-webkit-scrollbar,\s*)+[.\w-]+::-webkit-scrollbar)\s*\{\s*width:\s*(\d+)px", texto)
        self.assertIsNotNone(bloco)
        presentes = [s.strip().replace("::-webkit-scrollbar", "") for s in bloco.group(1).split(",")]
        self.assertEqual(sorted(presentes), sorted(LUGARES))
        self.assertGreaterEqual(int(bloco.group(2)), 10)


if __name__ == "__main__":
    unittest.main()
