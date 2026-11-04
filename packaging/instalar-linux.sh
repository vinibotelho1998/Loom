#!/usr/bin/env bash
# Coloca o Loom no menu do sistema, so para este usuario e sem pedir senha.
#
#   ./instalar-linux.sh [pasta do app]
#
# No pacote baixado da release, roda de dentro da pasta extraida. No repositorio, usa dist/Loom. O
# atalho aponta para a pasta do app, que nao e copiada: mover ela depois quebra o atalho. O nome
# loom.desktop tem que bater com o setDesktopFileName do app: e por ele que o compositor acha o
# icone da janela.
set -euo pipefail

aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${1:-}" ]; then
  app="$1"
elif [ -x "$aqui/Loom" ]; then
  app="$aqui"
else
  app="$aqui/../dist/Loom"
fi
[ -x "$app/Loom" ] || { echo "nao achei $app/Loom" >&2; exit 1; }
app="$(realpath "$app")"

dados="${XDG_DATA_HOME:-$HOME/.local/share}"
install -Dm644 "$aqui/loom.png" "$dados/icons/hicolor/256x256/apps/loom.png"
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
