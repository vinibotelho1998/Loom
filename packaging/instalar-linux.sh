#!/usr/bin/env bash
# Coloca um build do Loom no menu do sistema, so para este usuario e sem pedir senha.
#
#   packaging/instalar-linux.sh [pasta do app]
#
# A pasta do app e a que tem o executavel Loom; sem argumento, usa dist/Loom. O atalho aponta para
# ela, que nao e copiada. O nome loom.desktop tem que bater com o setDesktopFileName do app: e por
# ele que o compositor acha o icone da janela.
set -euo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app="$(realpath "${1:-$raiz/dist/Loom}")"
[ -x "$app/Loom" ] || { echo "nao achei $app/Loom" >&2; exit 1; }

dados="${XDG_DATA_HOME:-$HOME/.local/share}"
install -Dm644 "$raiz/packaging/loom.png" "$dados/icons/hicolor/256x256/apps/loom.png"
mkdir -p "$dados/applications"
cat > "$dados/applications/loom.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Loom
Comment=Terminal para rodar vários agentes ao mesmo tempo
Exec="$app/Loom"
Icon=loom
Terminal=false
Categories=System;TerminalEmulator;
Keywords=terminal;shell;claude;codex;
StartupWMClass=loom
DESKTOP
update-desktop-database "$dados/applications" 2>/dev/null || true
echo "Loom no menu, abrindo de $app"
