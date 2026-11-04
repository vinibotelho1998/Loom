"""Monitor de recursos da barra de cima: CPU, memoria e placa de video, lidos direto do kernel.

Leve de proposito. Nada de programa externo nem dependencia: so alguns arquivos pequenos do /proc e
do /sys, e so enquanto a janela esta a vista. Onde fica cada sensor e procurado uma vez so, porque
varrer o /sys a cada leitura custaria mais que ler os numeros.

Fora do Linux esses arquivos nao existem, tudo volta vazio e a barra nao mostra nada.
"""

from __future__ import annotations

from pathlib import Path

# o sensor da CPU de cada fabricante, e a linha que vale dentro dele
SENSORES_DE_CPU = ("k10temp", "zenpower", "coretemp")
ROTULOS_DE_CPU = ("Tctl", "Tdie", "Package id 0")
# com algum sensor faltando, de quanto em quanto procurar de novo: a placa pode voltar de um reset
# com outro numero de hwmon. A 2 s por leitura, uma vez por minuto
LEITURAS_PRA_PROCURAR_DE_NOVO = 30


def _texto(caminho: Path) -> str | None:
    try:
        with open(caminho, encoding="ascii", errors="replace") as arquivo:
            return arquivo.read().strip()
    except OSError:
        # placa dormindo responde erro no sensor, e o que sumiu nao existe mais: nos dois nao ha leitura
        return None


def _inteiro(caminho: Path) -> int | None:
    texto = _texto(caminho)
    try:
        return int(texto) if texto is not None else None
    except ValueError:
        return None


def _graus(caminho: Path | None) -> float | None:
    # o kernel entrega em milesimos de grau
    valor = _inteiro(caminho) if caminho is not None else None
    return round(valor / 1000, 1) if valor is not None else None


def _sensores_de(pasta: Path) -> dict[str, Path]:
    """Cada temperatura de um hwmon pelo rotulo. Sem rotulo, pelo nome do arquivo: temp1."""
    achados = {}
    for arquivo in sorted(pasta.glob("temp*_input")):
        base = arquivo.name[: -len("_input")]
        rotulo = _texto(pasta / f"{base}_label")
        achados[rotulo or base] = arquivo
    return achados


class Medidor:
    """Le os recursos da maquina. Guarda o contador da CPU, porque o uso e a diferenca entre duas leituras."""

    def __init__(self, raiz: Path = Path("/")) -> None:
        self._proc = raiz / "proc"
        self._sys = raiz / "sys"
        self._sensor_da_cpu: Path | None = None
        self._placa: Path | None = None
        self._sensores_da_placa: dict[str, Path] = {}
        self._leituras = 0
        self._faltou = False
        self._cpu_antes = self._contadores_da_cpu()
        self._procurar()

    def _procurar(self) -> None:
        self._faltou = False
        self._sensor_da_cpu = self._achar_sensor_da_cpu()
        self._placa = self._achar_placa()
        self._sensores_da_placa = {}
        if self._placa is not None:
            for pasta in sorted((self._placa / "hwmon").glob("hwmon*")):
                self._sensores_da_placa.update(_sensores_de(pasta))

    def _achar_sensor_da_cpu(self) -> Path | None:
        for pasta in sorted((self._sys / "class" / "hwmon").glob("hwmon*")):
            if _texto(pasta / "name") not in SENSORES_DE_CPU:
                continue
            sensores = _sensores_de(pasta)
            for rotulo in ROTULOS_DE_CPU:
                if rotulo in sensores:
                    return sensores[rotulo]
            return sensores.get("temp1")
        return None

    def _achar_placa(self) -> Path | None:
        """A placa com mais memoria: quem tem duas quer ver a dedicada, e nao o video do processador."""
        melhor, maior = None, 0
        for cartao in sorted((self._sys / "class" / "drm").glob("card*")):
            # card1-DP-1 e uma saida de video da placa, e nao outra placa
            if not cartao.name[len("card") :].isdigit():
                continue
            total = _inteiro(cartao / "device" / "mem_info_vram_total")
            if total and total > maior:
                melhor, maior = cartao / "device", total
        return melhor

    def _contadores_da_cpu(self) -> tuple[int, int] | None:
        """O tempo total e o ocioso da CPU desde o boot, em tiques."""
        try:
            with open(self._proc / "stat", encoding="ascii", errors="replace") as arquivo:
                campos = arquivo.readline().split()
        except OSError:
            return None
        try:
            # user nice system idle iowait irq softirq steal; guest ja vem somado em user
            numeros = [int(c) for c in campos[1:9]]
        except ValueError:
            return None
        if len(campos) < 9 or campos[0] != "cpu":
            return None
        return sum(numeros), numeros[3] + numeros[4]

    def _uso_da_cpu(self) -> float | None:
        agora = self._contadores_da_cpu()
        antes, self._cpu_antes = self._cpu_antes, agora
        if agora is None or antes is None:
            return None
        total = agora[0] - antes[0]
        if total <= 0:
            return None
        return round(max(0.0, min(100.0, 100 * (total - (agora[1] - antes[1])) / total)), 1)

    def _memoria(self) -> dict | None:
        total = disponivel = None
        try:
            with open(self._proc / "meminfo", encoding="ascii", errors="replace") as arquivo:
                for linha in arquivo:
                    partes = linha.split()
                    if len(partes) >= 2 and partes[0] == "MemTotal:":
                        total = int(partes[1]) * 1024
                    elif len(partes) >= 2 and partes[0] == "MemAvailable:":
                        disponivel = int(partes[1]) * 1024
                    if total is not None and disponivel is not None:
                        # o disponivel e o que os programas ainda podem usar: a cache do disco nao conta
                        return {"usado": max(total - disponivel, 0), "total": total}
        except (OSError, ValueError):
            pass
        return None

    def _dados_da_placa(self) -> tuple[dict | None, dict | None]:
        placa = self._placa
        if placa is None:
            return None, None
        vram = None
        usado, total = _inteiro(placa / "mem_info_vram_used"), _inteiro(placa / "mem_info_vram_total")
        if usado is not None and total:
            vram = {"usado": usado, "total": total}
        # Placa de notebook dormindo acorda quando se pergunta o uso ou a temperatura, e so de olhar o
        # monitor ela gastaria bateria. A memoria nao acorda ninguem
        if _texto(placa / "power" / "runtime_status") == "suspended":
            return {"dormindo": True}, vram
        gpu = {
            "uso": _inteiro(placa / "gpu_busy_percent"),
            "temp": _graus(self._sensores_da_placa.get("edge") or self._sensores_da_placa.get("temp1")),
            "juncao": _graus(self._sensores_da_placa.get("junction")),
        }
        if vram is not None:
            vram["temp"] = _graus(self._sensores_da_placa.get("mem"))
        if gpu["uso"] is None and gpu["temp"] is None:
            gpu = None
        return gpu, vram

    def ler(self) -> dict:
        """O que deu pra ler agora. O que nao existe nesta maquina simplesmente nao vem."""
        self._leituras += 1
        if self._faltou and self._leituras % LEITURAS_PRA_PROCURAR_DE_NOVO == 0:
            self._procurar()
        dados: dict = {}
        uso, temp = self._uso_da_cpu(), _graus(self._sensor_da_cpu)
        if uso is not None or temp is not None:
            dados["cpu"] = {"uso": uso, "temp": temp}
        ram = self._memoria()
        if ram is not None:
            dados["ram"] = ram
        gpu, vram = self._dados_da_placa()
        if gpu is not None:
            dados["gpu"] = gpu
        if vram is not None:
            dados["vram"] = vram
        self._faltou = temp is None or gpu is None or (gpu.get("temp") is None and not gpu.get("dormindo"))
        return dados
