"""A textura de letras do desenho por GPU nao depende de mipmaps.

Em 05/11/2026, com a Radeon RX 9070 no Mesa, o generateMipmap que o addon WebGL do xterm.js chamava a cada envio da
textura de letras falhava no driver, com GL_INVALID_OPERATION em allocateMipmapLevelsForGeneration. Sem filtro proprio,
a textura usava o padrao do WebGL, que exige mipmaps, e ficava incompleta: o WebGL le preto opaco de textura incompleta,
e as letras que entravam depois saiam como blocos pretos. O NOTICE.md registra a modificacao no arquivo de terceiros.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ADDON = RAIZ / "ui" / "vendor" / "addon-webgl.js"


class TexturaDeLetrasSemMipmap(unittest.TestCase):
    def setUp(self) -> None:
        self.codigo = ADDON.read_text(encoding="utf-8")

    def test_o_addon_nao_pede_mipmap(self) -> None:
        self.assertNotIn("generateMipmap", self.codigo)

    def test_filtro_linear_na_criacao_da_textura(self) -> None:
        criacao = r"texParameteri\((\w)\.TEXTURE_2D,\1\.TEXTURE_MIN_FILTER,\1\.LINEAR\),\1\.texImage2D\(\1\.TEXTURE_2D,0,\1\.RGBA,1,1,0,"
        self.assertRegex(self.codigo, criacao)

    def test_filtro_linear_em_cada_envio_da_textura(self) -> None:
        envio = r"texParameteri\((\w)\.TEXTURE_2D,\1\.TEXTURE_MIN_FILTER,\1\.LINEAR\),\1\.texImage2D\(\1\.TEXTURE_2D,0,\1\.RGBA,\1\.RGBA,\1\.UNSIGNED_BYTE,t\.pages\[i\]\.canvas\)"
        self.assertRegex(self.codigo, envio)

    def test_a_modificacao_esta_marcada_e_registrada(self) -> None:
        self.assertTrue(self.codigo.startswith("/* Loom: modificado"))
        self.assertIn("generateMipmap", (RAIZ / "NOTICE.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
