"""Vigia da tela: percebe sozinho quando a pagina parou de ser desenhada e manda refazer.

Quando o driver de video reinicia, o motor web pode parar de desenhar sem morrer. Os shells
continuam vivos por baixo e a janela mostra so o fundo do Qt, liso. Nenhum sinal do Qt avisa
isso, entao a vigia olha por dois lados:

- pulso: a pagina avisa a cada quadro desenhado, no maximo um aviso por segundo. Sem quadro,
  sem pulso, e isso pega tanto a pagina travada quanto o motor que parou de desenhar.
- pixel: lida direto da tela, a pagina de pe sempre tem icone e texto na barra de cima. Se as
  linhas lidas da janela estao lisas, sobrou so o fundo e a pagina sumiu.

A decisao mora em Vigia, sem Qt e sem relogio proprio, pra ser testada com tempo falso.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Callable

# um tom acima do fundo da pagina: pra quem olha e a mesma cor, e quando a pagina some e ela
# que aparece, lisa, em vez do cinza claro padrao do Qt piscando na troca de tela
COR_SENTINELA = "#121315"
INTERVALO_MS = 3000
# tela recem montada ainda esta carregando e pode mostrar o fundo sem estar quebrada
CARENCIA = 12.0
# folgado de proposito: a thread da tela as vezes para uns segundos esperando um comando de
# conta, e ai o pulso atrasa sem nada estar quebrado
SEM_PULSO = 20.0
# um quadro so pode pegar a tela no meio de um redesenho; duas leituras seguidas nao
SUSPEITAS_PRA_AGIR = 2
MINIMO_DE_PIXELS = 8
# quanto cada canal pode variar e a tela ainda contar como lisa. A janela preta de verdade
# mostrava um degrade de #1e1e1e a #1c1c1c, entao cor exata nao servia; e a pagina de pe mais
# vazia possivel ainda varia 9 no azul entre a barra de cima e o terminal
VARIACAO_DE_FUNDO = 4
# a barra de cima tem 42 px; a leitura passa no meio dela, onde sempre ha icone e texto
LINHA_DA_BARRA = 21


def rgb_da_sentinela() -> int:
    return int(COR_SENTINELA[1:], 16)


def so_fundo(pixels: list[int]) -> bool:
    """Verdadeiro quando as linhas lidas estao lisas, sem nada da pagina desenhado.

    A pagina de pe nunca fica assim: a linha da barra de cima passa por icones e texto. Lisa
    ela so fica quando sobra o fundo da janela, seja a cor sentinela, o cinza do Qt ou preto.
    """
    if len(pixels) < MINIMO_DE_PIXELS:
        return False
    for deslocamento in (16, 8, 0):
        canal = [(p >> deslocamento) & 0xFF for p in pixels]
        if max(canal) - min(canal) > VARIACAO_DE_FUNDO:
            return False
    return True


class Vigia:
    """Decide, a cada volta do relogio, se a tela precisa ser refeita e por que."""

    def __init__(self, agora: float) -> None:
        self.recomecar(agora)

    def recomecar(self, agora: float) -> None:
        self._marco = agora
        self._ultimo_tique = agora
        self._suspeitas = 0

    def tique(
        self,
        agora: float,
        visivel: bool,
        ultimo_pulso: float,
        tela_sumiu: Callable[[], bool | None],
    ) -> str:
        """Devolve o motivo pra refazer a tela, ou vazio.

        tela_sumiu e chamada so quando o resto nao decidiu, porque ler a tela custa alguns
        milissegundos; ela devolve None quando nao da pra ler, com a janela coberta ou atras.
        """
        folga = agora - self._ultimo_tique
        self._ultimo_tique = agora
        if folga > 3 * INTERVALO_MS / 1000:
            # a maquina dormiu ou a thread da tela ficou presa: o relogio pulou e nao prova nada
            self._marco = agora
            self._suspeitas = 0
            return ""
        if not visivel:
            # minimizada, a pagina para de desenhar de proposito; o prazo conta de novo na volta
            self._marco = agora
            self._suspeitas = 0
            return ""
        if agora - self._marco < CARENCIA:
            return ""
        if agora - max(ultimo_pulso, self._marco) > SEM_PULSO:
            return "a pagina parou de desenhar"
        sumiu = tela_sumiu()
        if sumiu is None:
            return ""
        self._suspeitas = self._suspeitas + 1 if sumiu else 0
        if self._suspeitas >= SUSPEITAS_PRA_AGIR:
            return "a janela mostra so o fundo, sem a pagina"
        return ""


def _win32():
    import ctypes.wintypes as w

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    ponteiro = ctypes.c_void_p
    user32.GetForegroundWindow.restype = ponteiro
    user32.WindowFromPoint.restype = ponteiro
    user32.WindowFromPoint.argtypes = [w.POINT]
    user32.GetAncestor.restype = ponteiro
    user32.GetAncestor.argtypes = [ponteiro, ctypes.c_uint]
    user32.GetClientRect.argtypes = [ponteiro, ctypes.POINTER(w.RECT)]
    user32.ClientToScreen.argtypes = [ponteiro, ctypes.POINTER(w.POINT)]
    user32.GetDpiForWindow.argtypes = [ponteiro]
    user32.GetDC.restype = ponteiro
    user32.GetDC.argtypes = [ponteiro]
    user32.ReleaseDC.argtypes = [ponteiro, ponteiro]
    gdi32.CreateCompatibleDC.restype = ponteiro
    gdi32.CreateCompatibleDC.argtypes = [ponteiro]
    gdi32.CreateCompatibleBitmap.restype = ponteiro
    gdi32.CreateCompatibleBitmap.argtypes = [ponteiro, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = ponteiro
    gdi32.SelectObject.argtypes = [ponteiro, ponteiro]
    gdi32.BitBlt.argtypes = [ponteiro, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ponteiro, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    gdi32.GetDIBits.argtypes = [ponteiro, ponteiro, ctypes.c_uint, ctypes.c_uint, ponteiro,
                                ponteiro, ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [ponteiro]
    gdi32.DeleteDC.argtypes = [ponteiro]
    return w, user32, gdi32


def janela_da_frente() -> int:
    if sys.platform != "win32":
        return 0
    _, user32, _ = _win32()
    return user32.GetForegroundWindow() or 0


def janela_no_ponto(x: int, y: int) -> int:
    """A janela de topo que o Windows mostra naquele ponto da tela, em pixel fisico."""
    if sys.platform != "win32":
        return 0
    w, user32, _ = _win32()
    GA_ROOT = 2
    alvo = user32.WindowFromPoint(w.POINT(x, y))
    return (user32.GetAncestor(alvo, GA_ROOT) or 0) if alvo else 0


def _copiar_linha(x: int, y: int, largura: int) -> list[int] | None:
    """Uma linha de pixels da tela como o Windows mostra, ja composta, em 0xRRGGBB."""
    w, user32, gdi32 = _win32()

    class Cabecalho(ctypes.Structure):
        _fields_ = [
            ("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
            ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
            ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG),
            ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD),
        ]

    tela = user32.GetDC(None)
    if not tela:
        return None
    memoria = gdi32.CreateCompatibleDC(tela)
    mapa = gdi32.CreateCompatibleBitmap(tela, largura, 1)
    antigo = gdi32.SelectObject(memoria, mapa)
    try:
        SRCCOPY = 0x00CC0020
        if not gdi32.BitBlt(memoria, 0, 0, largura, 1, tela, x, y, SRCCOPY):
            return None
        # o GetDIBits exige o bitmap fora do DC
        gdi32.SelectObject(memoria, antigo)
        antigo = None
        # altura negativa: linha de cima pra baixo; 32 bits sem compressao, BGRA
        cabecalho = Cabecalho(ctypes.sizeof(Cabecalho), largura, -1, 1, 32, 0, 0, 0, 0, 0, 0)
        destino = (ctypes.c_uint32 * largura)()
        if gdi32.GetDIBits(memoria, mapa, 0, 1, destino, ctypes.byref(cabecalho), 0) != 1:
            return None
        return [valor & 0xFFFFFF for valor in destino]
    finally:
        if antigo is not None:
            gdi32.SelectObject(memoria, antigo)
        gdi32.DeleteObject(mapa)
        gdi32.DeleteDC(memoria)
        user32.ReleaseDC(None, tela)


def ler_janela(janela: int) -> list[int] | None:
    """Duas linhas finas da area da janela, lidas da tela como o Windows mostra.

    Tudo em pixel fisico e pelo proprio Windows, sem passar pela conta de escala do Qt, que
    varia entre monitores. Devolve None quando nao da pra ler com seguranca: janela atras de
    outra, coberta por alguma por cima nos pontos lidos, ou pequena demais.
    """
    if sys.platform != "win32" or not janela:
        return None
    w, user32, _ = _win32()
    if janela_da_frente() != janela:
        return None
    area = w.RECT()
    if not user32.GetClientRect(janela, ctypes.byref(area)):
        return None
    largura, altura = area.right, area.bottom
    if largura < 200 or altura < 120:
        return None
    origem = w.POINT(0, 0)
    if not user32.ClientToScreen(janela, ctypes.byref(origem)):
        return None
    escala = (user32.GetDpiForWindow(janela) or 96) / 96
    pixels: list[int] = []
    # cada leitura da tela custa alguns milissegundos fixos: poucas linhas largas valem mais
    # que muitos pontos soltos
    for y in (round(LINHA_DA_BARRA * escala), altura // 2):
        for x in (4, largura // 2, largura - 5):
            # o pixel so vale se o Windows mostra esta janela ali, e nao uma por cima
            if janela_no_ponto(origem.x + x, origem.y + y) != janela:
                return None
        linha = _copiar_linha(origem.x, origem.y + y, largura)
        if linha is None:
            return None
        pixels += linha[::24]
    return pixels
