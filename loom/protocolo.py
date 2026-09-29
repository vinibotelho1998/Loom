"""O canal entre a tela e o nucleo: um JSON por linha.

O pipe local entrega o que foi escrito em pedacos do tamanho que quiser. Uma mensagem pode chegar
cortada ao meio, ou varias numa leitura so; por isso quem le junta tudo e so solta a linha quando
ela fecha.
"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import QCoreApplication

# O estado que a tela recebe ao nascer leva o historico de todas as abas, e e a maior mensagem.
# Uma linha que passa disso sem fechar nao e do Loom, e guardar pra sempre esgotaria a memoria.
TETO_DA_LINHA = 64 * 1024 * 1024


def empacotar(mensagem: dict) -> bytes:
    # so ASCII: quebra de linha e caractere de controle da saida do shell vao escapados, entao a
    # unica quebra de linha crua no canal e a que fecha a mensagem
    return json.dumps(mensagem, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"


class Leitor:
    """Junta o que chega aos pedacos e devolve as mensagens inteiras, na ordem."""

    def __init__(self, teto: int = TETO_DA_LINHA) -> None:
        self._resto = bytearray()
        self._teto = teto
        # a linha passou do teto: quem le derruba a conexao, que ja nao tem como se acertar
        self.estourou = False

    def alimentar(self, dados: bytes) -> list[dict]:
        self._resto += dados
        mensagens = []
        inicio = 0
        while True:
            fim = self._resto.find(b"\n", inicio)
            if fim < 0:
                break
            linha = bytes(self._resto[inicio:fim])
            inicio = fim + 1
            try:
                mensagem = json.loads(linha)
            except ValueError:
                continue
            if isinstance(mensagem, dict) and isinstance(mensagem.get("tipo"), str):
                mensagens.append(mensagem)
        del self._resto[:inicio]
        if len(self._resto) > self._teto:
            self._resto.clear()
            self.estourou = True
        return mensagens


def esvaziar(conexao, prazo_ms: int) -> bool:
    """Espera o que foi escrito sair de verdade pelo canal.

    Sem processar eventos, o waitForBytesWritten sozinho voltava com tudo ainda na fila, e o pedido
    morria junto com a conexao: a segunda instancia chamava e o nucleo nunca ficava sabendo.
    """
    fim = time.monotonic() + prazo_ms / 1000
    while conexao.bytesToWrite() and time.monotonic() < fim:
        conexao.waitForBytesWritten(20)
        QCoreApplication.processEvents()
    return not conexao.bytesToWrite()
