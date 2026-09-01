"""Ponto de entrada do DBX Term.

As flags do Chromium precisam ser definidas antes de qualquer coisa do Qt subir,
por isso elas moram aqui e nao dentro do pacote.
"""
import os

BASE = [
    "--enable-gpu-rasterization",
    "--enable-zero-copy",
    "--ignore-gpu-blocklist",
]
SEM_LIMITE = ["--disable-frame-rate-limit", "--disable-gpu-vsync"]


# O motor sempre sobe sem trava. O teto que a pessoa escolhe e aplicado na tela,
# segurando o laco de desenho, porque o Chromium do Qt so sabe livre ou preso em 60.
flags = list(BASE) + SEM_LIMITE
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", " ".join(flags))

from dbx_term.app import main  # noqa: E402

raise SystemExit(main())
