"""O que cada aba esta gastando agora: CPU, memoria e placa de video do que roda dentro dela.

Uma aba e o shell dela e tudo que nasceu dele. A arvore de processos acha quase tudo, mas o que sai
dela com setsid ou nohup, como o Claude faz com tarefa longa, vira filho do systemd e some da arvore.
Esses continuam com o LOOM_ABA que o Loom pos no ambiente do shell, e e por ele que voltam pra aba
certa. O ambiente de um processo nao muda depois que ele nasce, entao cada um e lido uma vez so.

Mesma regra do monitor da barra de cima: so arquivos do /proc, sem programa externo. Fora do Linux
nao ha /proc, e nenhuma aba ganha numero.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

MARCA = b"LOOM_ABA="
# os motores que medem trabalho de verdade: desenho e calculo. Video e copia ficam de fora
MOTORES = ("drm-engine-gfx", "drm-engine-compute")
# a memoria de video de cada cliente, na ordem de preferencia: o kernel trocou o nome com o tempo
CAMPOS_DE_VRAM = ("drm-resident-vram", "drm-memory-vram", "drm-total-vram")
UNIDADES = {"": 1, "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
# quantos processos a dica de cada aba mostra
PRINCIPAIS = 5
# Ver quais descritores de um processo sao da placa custa um readlink por descritor. Processo novo e
# olhado nas primeiras leituras, que e quando ele abre a placa; depois, de tempos em tempos
OLHAR_PLACA_NO_COMECO = 3
OLHAR_PLACA_A_CADA = 15


def _ler(caminho: Path) -> bytes | None:
    try:
        with open(caminho, "rb") as arquivo:
            return arquivo.read()
    except OSError:
        # processo que acabou no meio da leitura, ou de outro usuario
        return None


def _bytes(texto: str) -> int | None:
    partes = texto.split()
    if not partes:
        return None
    try:
        return int(partes[0]) * UNIDADES.get(partes[1] if len(partes) > 1 else "", 1)
    except ValueError:
        return None


def _cliente_da_placa(texto: bytes) -> tuple[str, int, int | None] | None:
    """(id do cliente, nanossegundos de trabalho, bytes de VRAM) de um fdinfo da placa."""
    campos = {}
    for linha in texto.decode("ascii", "replace").splitlines():
        chave, _, valor = linha.partition(":")
        if chave.startswith("drm-"):
            campos[chave] = valor.strip()
    cliente = campos.get("drm-client-id")
    if not cliente:
        return None
    trabalho = 0
    for motor in MOTORES:
        nanos = _bytes(campos.get(motor, ""))
        trabalho += nanos or 0
    vram = None
    for campo in CAMPOS_DE_VRAM:
        if campo in campos:
            vram = _bytes(campos[campo])
            break
    return campos.get("drm-pdev", "") + "/" + cliente, trabalho, vram


class MedidorDeAbas:
    """Le o gasto de cada aba. Guarda os contadores da leitura anterior: o uso e a diferenca entre duas."""

    def __init__(
        self,
        raiz: Path = Path("/"),
        relogio=time.monotonic,
        tique: int | None = None,
        pagina: int | None = None,
        nucleos: int | None = None,
    ) -> None:
        self._proc = raiz / "proc"
        self._relogio = relogio
        sysconf = getattr(os, "sysconf", None)
        self._tique = tique or (sysconf("SC_CLK_TCK") if sysconf else 100)
        self._pagina = pagina or (sysconf("SC_PAGE_SIZE") if sysconf else 4096)
        self._nucleos = nucleos or os.cpu_count() or 1
        self._quando: float | None = None
        # (pid, quando nasceu): o pid volta a ser usado, e o par nao. Guarda o tempo de CPU, o pai e a aba
        self._antes: dict[tuple[int, int], tuple[int, int, str | None]] = {}
        self._marcas: dict[tuple[int, int], str] = {}
        self._descritores: dict[tuple[int, int], tuple[list[str], int]] = {}
        self._trabalho_antes: dict[str, int] = {}

    def _processos(self) -> list[tuple[int, int, str, int, int, int, bool]] | None:
        """(pid, pai, nome, tiques, quando nasceu, paginas na RAM, zumbi) de cada processo.

        Os tiques somam o que o processo gastou e o que gastaram os filhos que ele ja recolheu. Sem
        a parte dos filhos, o que nasce e morre entre duas leituras, como cada compilador de um build,
        sumia da conta. O zumbi ainda nao foi recolhido: conta na CPU e fica fora da lista.
        """
        try:
            nomes = os.listdir(self._proc)
        except OSError:
            return None
        processos = []
        # em ordem de pid: o cliente da placa que pai e filho dividem fica sempre com o pai
        for nome in sorted((n for n in nomes if n.isdigit()), key=int):
            texto = _ler(self._proc / nome / "stat")
            if not texto:
                continue
            texto = texto.decode("utf-8", "replace")
            inicio, fim = texto.find("("), texto.rfind(")")
            campos = texto[fim + 1 :].split()
            if inicio < 0 or fim < inicio or len(campos) < 22 or campos[0] == "X":
                continue
            try:
                tiques = sum(int(c) for c in campos[11:15])
                processos.append(
                    (int(nome), int(campos[1]), texto[inicio + 1 : fim], tiques, int(campos[19]), int(campos[21]),
                     campos[0] == "Z")
                )
            except ValueError:
                continue
        return processos

    def _marca(self, pid: int) -> str:
        ambiente = _ler(self._proc / str(pid) / "environ") or b""
        if ambiente.startswith(MARCA):
            comeco = len(MARCA)
        else:
            achado = ambiente.find(b"\0" + MARCA)
            if achado < 0:
                return ""
            comeco = achado + 1 + len(MARCA)
        fim = ambiente.find(b"\0", comeco)
        return ambiente[comeco : fim if fim >= 0 else len(ambiente)].decode("utf-8", "replace")

    def _descritores_da_placa(self, pid: int, chave: tuple[int, int]) -> list[str]:
        guardado = self._descritores.get(chave)
        vezes = guardado[1] + 1 if guardado else 0
        if guardado is not None and vezes >= OLHAR_PLACA_NO_COMECO and vezes % OLHAR_PLACA_A_CADA:
            self._descritores[chave] = (guardado[0], vezes)
            return guardado[0]
        pasta = self._proc / str(pid) / "fd"
        achados = []
        try:
            nomes = os.listdir(pasta)
        except OSError:
            nomes = []
        for nome in nomes:
            try:
                if os.readlink(pasta / nome).startswith("/dev/dri/"):
                    achados.append(nome)
            except OSError:
                continue
        self._descritores[chave] = (achados, vezes)
        return achados

    def ler(self, abas: dict[str, int | None]) -> dict[str, dict]:
        """O gasto de cada aba, pelo id dela. A aba sem shell vivo, ou fora do Linux, nao vem."""
        abas = {aba: pid for aba, pid in abas.items() if pid}
        processos = self._processos() if abas else None
        if processos is None:
            return {}
        agora = self._relogio()
        intervalo = agora - self._quando if self._quando is not None else None
        self._quando = agora

        filhos: dict[int, list[int]] = defaultdict(list)
        for pid, pai, *_ in processos:
            filhos[pai].append(pid)
        dona: dict[int, str] = {}
        for aba, raiz in abas.items():
            pilha = [raiz]
            while pilha:
                pid = pilha.pop()
                if pid in dona:
                    continue
                dona[pid] = aba
                pilha.extend(filhos.get(pid, ()))

        vivos = set()
        agora_por_pid: dict[int, tuple[int, int]] = {}
        antes_agora: dict[tuple[int, int], tuple[int, int, str | None]] = {}
        membros: dict[int, list] = {}
        for pid, pai, nome, tiques, nasceu, paginas, zumbi in processos:
            chave = (pid, nasceu)
            vivos.add(chave)
            agora_por_pid[pid] = chave
            aba = dona.get(pid)
            # linha de kernel nao tem ambiente; o resto e lido uma vez na vida do processo
            if aba is None and not zumbi and pid != 2 and pai != 2:
                marca = self._marcas.get(chave)
                if marca is None:
                    marca = self._marcas[chave] = self._marca(pid)
                aba = marca if marca in abas else None
            antes_agora[chave] = (tiques, pai, aba)
            if aba is None:
                continue
            antes = self._antes.get(chave)
            # o que nasceu depois da ultima leitura gastou tudo dentro do intervalo
            gasto = tiques - antes[0] if antes is not None else tiques
            membros[pid] = [aba, nome, gasto, paginas * self._pagina, chave, zumbi]

        # Quem morreu no intervalo passou o tempo todo dele pro pai que o recolheu, e daquele tempo
        # uma parte ja tinha sido contada nas leituras de antes. Essa parte sai do primeiro antepassado
        # vivo, que e onde o kernel somou. Recolhido por quem e de fora da aba, o tempo nao volta.
        antes_por_pid = {chave[0]: (chave, info) for chave, info in self._antes.items()}
        for chave, (tiques, pai, aba) in self._antes.items():
            if chave in vivos or aba is None:
                continue
            passo = pai
            for _ in range(64):
                if passo <= 1:
                    break
                viva = agora_por_pid.get(passo)
                anterior = antes_por_pid.get(passo)
                if viva is not None and (anterior is None or anterior[0] == viva):
                    if passo in membros:
                        membros[passo][2] -= tiques
                    break
                if anterior is None:
                    break
                passo = anterior[1][1]

        por_aba: dict[str, list] = defaultdict(list)
        cpu_por_aba: dict[str, int] = defaultdict(int)
        for pid, (aba, nome, gasto, ram, chave, zumbi) in membros.items():
            gasto = max(gasto, 0)
            cpu_por_aba[aba] += gasto
            if not zumbi:
                por_aba[aba].append((pid, nome, gasto, ram, chave))

        segundos = intervalo if intervalo and intervalo > 0 else None
        resultado = {}
        trabalho_agora = {}
        for aba, membros in por_aba.items():
            vistos = set()
            uso_da_placa = 0.0
            vram_da_aba = 0
            com_placa = False
            linhas = []
            for pid, nome, gasto, ram, chave in membros:
                gpu_do_processo = 0.0
                vram_do_processo = 0
                processo_com_placa = False
                for descritor in self._descritores_da_placa(pid, chave):
                    texto = _ler(self._proc / str(pid) / "fdinfo" / descritor)
                    cliente = _cliente_da_placa(texto) if texto else None
                    # o mesmo cliente aparece em cada processo que herdou o descritor
                    if cliente is None or cliente[0] in vistos:
                        continue
                    vistos.add(cliente[0])
                    com_placa = processo_com_placa = True
                    identidade, trabalho, vram = cliente
                    trabalho_agora[identidade] = trabalho
                    antes = self._trabalho_antes.get(identidade)
                    # cliente visto pela primeira vez: o contador dele vem de antes, e nao do intervalo
                    if segundos and antes is not None and trabalho >= antes:
                        gpu_do_processo += 100 * (trabalho - antes) / (segundos * 1e9)
                    vram_do_processo += vram or 0
                uso_da_placa += gpu_do_processo
                vram_da_aba += vram_do_processo
                nucleos = gasto / self._tique / segundos if segundos else None
                linhas.append({
                    "nome": nome,
                    "nucleos": round(nucleos, 2) if nucleos is not None else None,
                    "ram": ram,
                    "gpu": round(min(gpu_do_processo, 100.0), 1) if processo_com_placa and segundos else None,
                    "vram": vram_do_processo or None,
                })
            nucleos_da_aba = cpu_por_aba[aba] / self._tique / segundos if segundos else None
            linhas.sort(key=lambda p: (p["nucleos"] or 0, p["gpu"] or 0, p["ram"]), reverse=True)
            resultado[aba] = {
                "cpu": round(min(100.0, 100 * nucleos_da_aba / self._nucleos), 1) if nucleos_da_aba is not None else None,
                "nucleos": round(nucleos_da_aba, 2) if nucleos_da_aba is not None else None,
                "ram": sum(p[3] for p in membros),
                "gpu": round(min(uso_da_placa, 100.0), 1) if com_placa and segundos else None,
                "vram": vram_da_aba if com_placa else None,
                "processos": len(membros),
                "principais": linhas[:PRINCIPAIS],
            }

        self._antes = antes_agora
        self._trabalho_antes = trabalho_agora
        self._marcas = {chave: marca for chave, marca in self._marcas.items() if chave in vivos}
        self._descritores = {chave: d for chave, d in self._descritores.items() if chave in vivos}
        return resultado
