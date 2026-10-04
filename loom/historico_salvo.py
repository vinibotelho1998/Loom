"""O que cada aba mostrava, guardado em disco pra voltar no proximo start.

A ponte guarda em memoria o fim da saida de cada shell, mas isso morria com o app: reabrir trazia
as abas de volta com o terminal em branco. Cada aba vira um arquivo com o nome do id do shell,
que e o mesmo que o abas.json anota em `sessao`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import caminhos

log = logging.getLogger(__name__)

# o mesmo teto do historico em memoria da ponte: guardar mais do que ela tem nao traria nada
TETO_DE_TEXTO = 512 * 1024  # caracteres
# Arquivo que ninguem mais cita so sai depois disso. O Loom do codigo e o instalado dividem a
# mesma pasta, e um apagaria o historico do outro, ainda aberto, ao fechar.
IDADE_PRA_LIMPAR = 24 * 3600
# o id vira nome de arquivo e chega da pagina: so letra, numero, hifen e sublinhado
ID_VALIDO = re.compile(r"[0-9A-Za-z_-]{1,64}")


def pasta() -> Path:
    # lida na hora e nao no import: os testes trocam a RAIZ por uma pasta descartavel
    return caminhos.RAIZ / "historico"


def _arquivo(session_id: str) -> Path | None:
    if not isinstance(session_id, str) or not ID_VALIDO.fullmatch(session_id):
        return None
    return pasta() / f"{session_id}.json"


def _cortar(texto: str) -> str:
    if len(texto) <= TETO_DE_TEXTO:
        return texto
    texto = texto[-TETO_DE_TEXTO:]
    # o corte caiu no meio de uma linha, as vezes de uma sequencia de cor: comeca na seguinte
    quebra = texto.find("\n")
    return texto[quebra + 1 :] if quebra >= 0 else texto


def gravar(session_id: str, texto: str, cols: int, rows: int, quando: float | None = None) -> bool:
    """Grava o historico daquela aba inteiro ou nao mexe no que ja estava."""
    alvo = _arquivo(session_id)
    if alvo is None or not texto:
        return False
    dados = {
        "texto": _cortar(texto),
        "cols": max(int(cols), 1),
        "rows": max(int(rows), 1),
        "em": float(quando if quando is not None else time.time()),
    }
    try:
        caminhos.gravar_texto(alvo, json.dumps(dados, ensure_ascii=False))
    except OSError:
        log.warning("nao consegui gravar o historico da aba %s", session_id, exc_info=True)
        return False
    return True


def ler(session_id: str) -> dict | None:
    """O historico guardado, com texto, cols, rows e em. None quando nao ha ou nao da pra ler."""
    alvo = _arquivo(session_id)
    if alvo is None or not alvo.is_file():
        return None
    try:
        dados = json.loads(alvo.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(dados, dict) or not isinstance(dados.get("texto"), str) or not dados["texto"]:
        return None

    def numero(chave: str, padrao: int) -> int:
        valor = dados.get(chave)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor != valor:
            return padrao
        return max(int(valor), 1)

    em = dados.get("em")
    return {
        "texto": _cortar(dados["texto"]),
        "cols": min(numero("cols", 120), 1000),
        "rows": min(numero("rows", 30), 500),
        "em": float(em) if isinstance(em, (int, float)) and not isinstance(em, bool) else 0.0,
    }


def renomear(antigo: str, novo: str) -> bool:
    """O historico da aba reaberta passa a ter o nome do shell novo dela.

    Sem trocar, uma queda antes da proxima gravacao deixava o arquivo com o id velho, que o
    abas.json ja nao cita mais, e a terceira abertura voltava em branco.
    """
    origem, destino = _arquivo(antigo), _arquivo(novo)
    if origem is None or destino is None or origem == destino or not origem.is_file():
        return False
    try:
        origem.replace(destino)
    except OSError:
        return False
    return True


def apagar(session_id: str) -> None:
    alvo = _arquivo(session_id)
    if alvo is not None:
        alvo.unlink(missing_ok=True)


def limpar(manter: set[str], idade_minima: float = IDADE_PRA_LIMPAR, agora: float | None = None) -> int:
    """Apaga o historico que nenhuma aba cita mais e ja passou da idade. Devolve quantos sairam."""
    raiz = pasta()
    if not raiz.is_dir():
        return 0
    agora = time.time() if agora is None else agora
    apagados = 0
    for arquivo in list(raiz.glob("*.json")) + list(raiz.glob("*.tmp")):
        if arquivo.suffix == ".json" and arquivo.stem in manter:
            continue
        try:
            if agora - arquivo.stat().st_mtime < idade_minima:
                continue
            arquivo.unlink()
        except OSError:
            continue
        apagados += 1
    return apagados
