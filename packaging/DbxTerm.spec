# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_dynamic_libs, get_module_file_attribute

ROOT = os.path.dirname(SPECPATH)
ICONE = os.path.join(ROOT, "ui", "dbx-term.ico")

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
WINPTY = os.path.dirname(get_module_file_attribute("winpty"))
BINARIOS = collect_dynamic_libs("winpty") + [
    (os.path.join(WINPTY, nome), "winpty")
    for nome in ("winpty.dll", "conpty.dll", "winpty-agent.exe", "OpenConsole.exe")
    if os.path.exists(os.path.join(WINPTY, nome))
]

# A janela: entrada com as flags do Chromium, sem console.
app = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=BINARIOS,
    datas=[(os.path.join(ROOT, "ui"), "ui")],
    hiddenimports=["winpty"],
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

pyz_app = PYZ(app.pure)
pyz_coletor = PYZ(coletor.pure)

exe_app = EXE(
    pyz_app,
    app.scripts,
    [],
    exclude_binaries=True,
    name="DbxTerm",
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
    name="dbx-coletor",
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
    name="DbxTerm",
)
