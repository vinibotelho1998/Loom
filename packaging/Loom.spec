# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# O analisador procura DLLs no PATH. Ferramentas como Poppler podem oferecer
# ICU/UCRT com os mesmos nomes do Windows, mas exports incompatíveis com o Qt.
# Dependências do app vêm dos hooks/pastas dos módulos; o PATH fica restrito
# ao Python usado no build e ao próprio Windows.
if os.name == "nt":
    WINDOWS = os.environ.get("SystemRoot", r"C:\Windows")
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys([
        os.path.join(sys.prefix, "Scripts"), sys.base_prefix,
        os.path.join(WINDOWS, "System32"), WINDOWS,
    ]))


def _verificar_runtime_windows(binarios):
    if os.name != "nt":
        return
    raiz = os.path.normcase(os.path.abspath(WINDOWS)) + os.sep
    for destino, origem, _tipo in binarios:
        nome = os.path.basename(destino).lower()
        if nome in ("icuuc.dll", "icuin.dll", "ucrtbase.dll") or nome.startswith("api-ms-win-"):
            if not os.path.normcase(os.path.abspath(origem)).startswith(raiz):
                raise RuntimeError(f"DLL do Windows resolvida fora do sistema: {origem}")

from PyInstaller.utils.hooks import collect_dynamic_libs, get_module_file_attribute

ROOT = os.path.dirname(SPECPATH)
ICONE = os.path.join(ROOT, "ui", "loom.ico")

# A tela e HTML dentro do QtWebEngine, entao quase nada do Qt pode ser podado:
# o motor web puxa Quick, Qml, Positioning, Network e Pdf por dentro. Aqui so
# saem modulos que o app nunca toca nem por tabela.
QT_SOBRANDO = (
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.Qt3DAnimation", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtSerialPort", "PySide6.QtSensors",
    "PySide6.QtCharts", "PySide6.QtSpatialAudio",
)

# O winpty nao e so um .pyd: a dll procura o agente pelo nome, na propria pasta.
# Sem os dois .exe do lado dela, abrir uma aba falha sem mensagem nenhuma.
# Fora do Windows o terminal e o PTY do kernel, e nao ha nada a mais pra levar.
BINARIOS = []
IMPORTS_DO_SISTEMA = []
if os.name == "nt":
    WINPTY = os.path.dirname(get_module_file_attribute("winpty"))
    BINARIOS = collect_dynamic_libs("winpty") + [
        (os.path.join(WINPTY, nome), "winpty")
        for nome in ("winpty.dll", "conpty.dll", "winpty-agent.exe", "OpenConsole.exe")
        if os.path.exists(os.path.join(WINPTY, nome))
    ]
    IMPORTS_DO_SISTEMA = ["winpty"]

# A janela: entrada com as flags do Chromium, sem console.
app = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=BINARIOS,
    # a tabela de preco do painel de uso vai ao lado do modulo que le ela, em loom/dados
    datas=[(os.path.join(ROOT, "ui"), "ui"), (os.path.join(ROOT, "loom", "dados"), os.path.join("loom", "dados"))],
    hiddenimports=IMPORTS_DO_SISTEMA,
    excludes=["tkinter", "unittest", *QT_SOBRANDO],
    noarchive=False,
    optimize=0,
)

# O coletor: exe separado e com console de proposito. Ele e a status line do
# Claude Code, que le stdin e escreve stdout; um exe de janela nao tem nem um
# nem outro, entao o numero do plano nunca chegaria.
coletor = Analysis(
    [os.path.join(ROOT, "coletar.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=["tkinter", "PySide6", "shiboken6", "winpty"],
    noarchive=False,
    optimize=0,
)

_verificar_runtime_windows(app.binaries)
_verificar_runtime_windows(coletor.binaries)

# A libgbm conversa com o driver de video de quem roda, e tem que ser a do sistema. Levada do runner
# do CI, ela nao combina com outro Mesa: o Chromium avisa Failed to create GBM buffer e cai num
# caminho pior de desenho.
if os.name != "nt":
    app.binaries = [b for b in app.binaries if not os.path.basename(b[0]).startswith("libgbm.so")]

pyz_app = PYZ(app.pure)
pyz_coletor = PYZ(coletor.pure)

exe_app = EXE(
    pyz_app,
    app.scripts,
    [],
    exclude_binaries=True,
    name="Loom",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICONE,
)

exe_coletor = EXE(
    pyz_coletor,
    coletor.scripts,
    [],
    exclude_binaries=True,
    name="loom-coletor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICONE,
)

coll = COLLECT(
    exe_app,
    app.binaries,
    app.datas,
    exe_coletor,
    coletor.binaries,
    coletor.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Loom",
)
