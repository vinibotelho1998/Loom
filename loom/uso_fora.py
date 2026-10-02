"""O que a conta gastou e este computador nao explica.

O percentual de uma janela de limite e da conta toda: soma este computador, outro aparelho, a versao
web e qualquer pessoa que use a mesma conta. O Loom so enxerga o que rodou aqui, nos arquivos das
conversas. Quando o percentual sobe mais do que o uso daqui explica, a diferenca veio de fora.

O resultado e um piso, nunca o total. Cada subida do percentual entra com o que sobra depois de
descontar 1 ponto de arredondamento e o que o uso daqui explicaria na proporcao de tokens por ponto
mais generosa ja medida em trabalho continuo, ainda cortada pela metade. Sem base pra essa proporcao
o Loom nao afirma nada.

Guarda duas coisas, as duas pequenas:
- por janela, cada valor de percentual com a primeira e a ultima vez em que foi visto;
- o uso daqui em baldes de 5 minutos, em tokens.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from pathlib import Path

from . import caminhos as mod_caminhos

# o tamanho do balde do uso daqui, em segundos
PASSO = 300
# duas subidas com ate isto entre elas sao trabalho continuo: e dali que sai a proporcao de tokens por ponto
CONTINUO = 900
# ponto de trabalho continuo que uma janela precisa ter pra servir de base da proporcao
PONTOS_PARA_CALIBRAR = 5
# o uso daqui explica, no maximo, esta fracao do que a proporcao medida diria
FOLGA = 0.5
# o percentual e inteiro: cada subida pode ter ate 1 ponto a menos do que mostra
ARREDONDAMENTO = 1.0
DIAS_GUARDADOS = 60
# a mesma janela chega com o horario de reinicio variando em segundos de uma leitura pra outra
TOLERANCIA_DA_JANELA = 120
# as janelas que a status line do Claude Code entrega, em minutos
FAIXAS_DO_CLAUDE = {"five_hour": 300, "seven_day": 10080}
_PERFIL_VALIDO = re.compile(r"^[\w-]{1,128}$")


def _numero(valor) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if valor != valor or valor in (float("inf"), float("-inf")):
        return None
    return float(valor)


def _par(valor) -> tuple[float, float] | None:
    if not isinstance(valor, (list, tuple)) or len(valor) != 2:
        return None
    primeira, ultima = _numero(valor[0]), _numero(valor[1])
    return None if primeira is None or ultima is None else (primeira, ultima)


# ------------------------------------------------------------ leituras do percentual


def mesma_janela(janela, minutos: int, zera: float) -> bool:
    if not isinstance(janela, dict) or janela.get("min") != minutos:
        return False
    outra = _numero(janela.get("zera"))
    return outra is not None and abs(outra - zera) <= TOLERANCIA_DA_JANELA


def abertura_da_janela(janela: dict) -> float | None:
    """Quando a janela comecou, se ela e fixa: o reinicio menos a duracao."""
    zera, minutos = _numero(janela.get("zera")), janela.get("min")
    if zera is None or isinstance(minutos, bool) or not isinstance(minutos, int):
        return None
    return zera - minutos * 60


def _achar_ou_criar(janelas: dict, minutos: int, zera: float) -> tuple[dict, bool]:
    for janela in janelas.values():
        if mesma_janela(janela, minutos, zera):
            return janela, False
    nova = {"min": minutos, "zera": round(zera, 1), "leituras": {}}
    janelas[f"{minutos}|{round(zera / 60)}"] = nova
    return nova, True


def registrar_leitura(
    janelas: dict, minutos, zera, usado, instante, acompanhar_ultima: bool = True
) -> bool:
    """Anota que, em `instante`, a janela de `minutos` que zera em `zera` estava em `usado` por cento.

    De cada valor de percentual guarda a primeira e a ultima vez em que foi visto. Com
    `acompanhar_ultima` desligado so a primeira conta, pra quem escreve em disco a cada leitura
    nao regravar o arquivo toda vez. Devolve se alguma coisa mudou.
    """
    zera, usado, instante = _numero(zera), _numero(usado), _numero(instante)
    if isinstance(minutos, bool) or not isinstance(minutos, int) or minutos <= 0:
        return False
    if zera is None or usado is None or instante is None or not 0 <= usado <= 200:
        return False
    janela, mudou = _achar_ou_criar(janelas, minutos, zera)
    leituras = janela.get("leituras")
    if not isinstance(leituras, dict):
        leituras = janela["leituras"] = {}
        mudou = True
    chave = format(round(usado, 1), "g")
    marca = round(instante, 1)
    atual = _par(leituras.get(chave))
    if atual is None:
        leituras[chave] = [marca, marca]
        return True
    primeira = min(atual[0], marca)
    ultima = max(atual[1], marca) if acompanhar_ultima else atual[1]
    if (primeira, ultima) != atual:
        leituras[chave] = [primeira, ultima]
        mudou = True
    return mudou


def podar_janelas(janelas: dict, agora: float) -> dict:
    corte = agora - DIAS_GUARDADOS * 86400
    mantidas = {}
    for chave, janela in janelas.items():
        zera = _numero(janela.get("zera")) if isinstance(janela, dict) else None
        if zera is not None and zera > corte:
            mantidas[chave] = janela
    return mantidas


def juntar_janelas(origens) -> dict:
    """Une as janelas de varias fontes, pegando a primeira vista mais antiga e a ultima mais nova."""
    juntas: dict = {}
    for janelas in origens:
        if not isinstance(janelas, dict):
            continue
        for janela in janelas.values():
            if not isinstance(janela, dict):
                continue
            minutos, zera = janela.get("min"), _numero(janela.get("zera"))
            if isinstance(minutos, bool) or not isinstance(minutos, int) or zera is None:
                continue
            alvo, _ = _achar_ou_criar(juntas, minutos, zera)
            for chave, valor in (janela.get("leituras") or {}).items():
                par = _par(valor)
                if par is None:
                    continue
                atual = _par(alvo["leituras"].get(chave))
                alvo["leituras"][chave] = (
                    [par[0], par[1]] if atual is None else [min(atual[0], par[0]), max(atual[1], par[1])]
                )
    return juntas


# ----------------------------------------------------------------- uso daqui, no tempo


def somar_tempo(tempo: dict, instante: float, tokens: int, prefixo: str = "") -> None:
    """Soma `tokens` no balde de 5 minutos de `instante`. O prefixo, quando ha, termina em barra vertical."""
    if tokens <= 0:
        return
    chave = f"{prefixo}{int(instante // PASSO)}"
    tempo[chave] = tempo.get(chave, 0) + int(tokens)


def podar_tempo(tempo: dict, agora: float) -> dict:
    corte = int((agora - DIAS_GUARDADOS * 86400) // PASSO)
    mantidos = {}
    for chave, tokens in tempo.items():
        try:
            indice = int(str(chave).rpartition("|")[2])
        except ValueError:
            continue
        if indice >= corte:
            mantidos[chave] = tokens
    return mantidos


def baldes_da_conta(tempo: dict, chaves=None) -> dict[str, int]:
    """Os baldes das contas dadas, somados. Sem `chaves`, a chave inteira e o indice, sem prefixo."""
    juntos: dict[str, int] = {}
    for chave, tokens in tempo.items():
        prefixo, _, indice = str(chave).rpartition("|")
        if chaves is not None and prefixo not in chaves:
            continue
        try:
            juntos[indice] = juntos.get(indice, 0) + int(tokens)
        except (TypeError, ValueError):
            continue
    return juntos


class LinhaDoTempo:
    """O uso daqui por balde de 5 minutos, com soma acumulada pra perguntar de qualquer trecho."""

    def __init__(self, baldes: dict):
        itens = []
        for chave, tokens in baldes.items():
            try:
                itens.append((int(chave), max(0, int(tokens))))
            except (TypeError, ValueError):
                continue
        itens.sort()
        self._indices = [indice for indice, _ in itens]
        self._somas = [0]
        for _, tokens in itens:
            self._somas.append(self._somas[-1] + tokens)

    def entre(self, de: float, ate: float) -> int:
        """Tokens dos baldes que tocam o trecho. O balde da ponta entra inteiro, o que so aumenta o que o uso daqui explica."""
        if ate < de:
            de, ate = ate, de
        primeiro = bisect_left(self._indices, int(de // PASSO))
        ultimo = bisect_right(self._indices, int(ate // PASSO))
        return self._somas[ultimo] - self._somas[primeiro]

    def primeiro_depois(self, instante: float) -> float | None:
        posicao = bisect_left(self._indices, int(instante // PASSO))
        return None if posicao >= len(self._indices) else float(self._indices[posicao] * PASSO)


# ------------------------------------------------------------------------ o calculo


def _pontos(janela: dict) -> list[tuple[float, float, float]]:
    pontos = []
    for chave, valor in (janela.get("leituras") or {}).items():
        par = _par(valor)
        try:
            pct = float(chave)
        except (TypeError, ValueError):
            continue
        if par is not None:
            pontos.append((pct, par[0], par[1]))
    pontos.sort()
    return pontos


def intervalos(janela: dict, abertura: float | None, apertado: bool) -> list[dict]:
    """Cada subida do percentual, com o trecho de tempo em que ela aconteceu.

    A janela comeca em zero na abertura, que vira a primeira leitura. Com `apertado`, a subida
    vai da ultima vez em que o valor de baixo foi visto ate a primeira do de cima, o que vale
    quando o horario da leitura e o da resposta. Sem ele vai da primeira vez em que o de baixo
    foi visto, um trecho mais largo, que so pode explicar mais com o uso daqui.
    """
    pontos = _pontos(janela)
    if abertura is not None and (not pontos or pontos[0][0] > 0):
        pontos.insert(0, (0.0, abertura, abertura))
    saida = []
    for (de_pct, primeira, ultima), (ate_pct, chegada, _) in zip(pontos, pontos[1:]):
        de = ultima if apertado else primeira
        saida.append(
            {"de": min(de, chegada), "ate": chegada, "dePct": de_pct, "atePct": ate_pct, "delta": ate_pct - de_pct}
        )
    return saida


def proporcao(janelas, linha: LinhaDoTempo, minutos: int, abertura_de, apertado: bool) -> float | None:
    """A menor proporcao de tokens por ponto vista em trabalho continuo, entre as janelas da mesma duracao.

    So conta a janela com pontos suficientes de trabalho continuo, e fica com a menor delas: usar a
    mais baixa faz o uso daqui explicar mais, e portanto afirmar menos que veio de fora.
    """
    menor = None
    for janela in janelas:
        if not isinstance(janela, dict) or janela.get("min") != minutos:
            continue
        pontos = tokens = 0.0
        for intervalo in intervalos(janela, abertura_de(janela), apertado):
            if intervalo["ate"] - intervalo["de"] > CONTINUO:
                continue
            local = linha.entre(intervalo["de"], intervalo["ate"])
            if local > 0:
                pontos += intervalo["delta"]
                tokens += local
        if pontos >= PONTOS_PARA_CALIBRAR:
            razao = tokens / pontos
            menor = razao if menor is None else min(menor, razao)
    return menor


def resumir(janela: dict, linha: LinhaDoTempo, razao: float | None, abertura: float | None, apertado: bool) -> dict:
    """O que da pra afirmar de uma janela: o piso do que veio de fora e os fatos que o sustentam.

    `razao` e a proporcao medida; sem ela `foraMinimo` fica vazio. Os horarios saem em segundos.
    """
    pontos = _pontos(janela)
    efetiva = razao * FOLGA if razao else None
    total = 0.0
    saltos = []
    for intervalo in intervalos(janela, abertura, apertado):
        local = linha.entre(intervalo["de"], intervalo["ate"])
        if efetiva is None:
            continue
        fora = intervalo["delta"] - ARREDONDAMENTO - local / efetiva
        if fora > 0:
            total += fora
            saltos.append(
                {
                    "de": intervalo["de"],
                    "ate": intervalo["ate"],
                    "pts": round(fora, 1),
                    "delta": intervalo["delta"],
                    "tokens": local,
                }
            )
    final = max((pct for pct, _, _ in pontos), default=None)
    primeira = min(((chegada, pct) for pct, chegada, _ in pontos), default=None)
    primeiro_uso = linha.primeiro_depois(abertura) if abertura is not None else None
    return {
        "foraMinimo": None if efetiva is None else round(min(total, final or 0.0), 1),
        "final": final,
        "chegouEm": None if primeira is None else primeira[1],
        "primeiraLeituraEm": None if primeira is None else primeira[0],
        "abertaEm": abertura,
        "primeiroUsoAqui": primeiro_uso,
        "saltos": sorted(saltos, key=lambda salto: -salto["pts"])[:5],
        "tokensPorPonto": razao,
    }


# --------------------------------------------- leituras do Claude, gravadas pela status line


def _pasta_das_leituras() -> Path:
    return mod_caminhos.RAIZ / "leituras-limite"


def carregar_janelas(arquivo: Path) -> dict:
    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    janelas = bruto.get("janelas") if isinstance(bruto, dict) else None
    return janelas if isinstance(janelas, dict) else {}


def guardar_leituras_do_claude(perfil: str, faixas: dict, instante: float) -> None:
    """Chamada pela status line a cada leitura. So grava quando um valor de percentual aparece pela primeira vez.

    A status line roda em um processo por chamada e varias sessoes rodam juntas; uma gravacao que
    perde a corrida so deixa de anotar um valor, e a janela seguinte fica mais larga, o que e seguro.
    """
    if not isinstance(perfil, str) or not _PERFIL_VALIDO.match(perfil) or not isinstance(faixas, dict):
        return
    arquivo = _pasta_das_leituras() / f"{perfil}.json"
    janelas = carregar_janelas(arquivo)
    mudou = False
    for faixa, minutos in FAIXAS_DO_CLAUDE.items():
        dados = faixas.get(faixa)
        if isinstance(dados, dict):
            mudou = (
                registrar_leitura(
                    janelas, minutos, dados.get("zera"), dados.get("usado"), instante, acompanhar_ultima=False
                )
                or mudou
            )
    if mudou:
        mod_caminhos.gravar_texto(arquivo, json.dumps({"versao": 1, "janelas": podar_janelas(janelas, instante)}))


def ler_leituras_do_claude(chaves) -> dict:
    """As janelas guardadas das contas dadas, unidas."""
    pasta = _pasta_das_leituras()
    return juntar_janelas(
        carregar_janelas(pasta / f"{chave}.json") for chave in chaves if _PERFIL_VALIDO.match(str(chave))
    )
