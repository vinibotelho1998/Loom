#!/usr/bin/env bash
# Gera o Loom para Linux a partir do codigo: a pasta dist/Loom, que roda sem Python.
#
#   packaging/build.sh
#
# Usa o Python do sistema, monta um ambiente proprio em .venv e nao instala nada fora dele. Quem
# coloca no menu do sistema e o instalar-linux.sh, ao lado deste.
set -euo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="$raiz/.venv"

if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv"
fi
if ! "$venv/bin/python" -c "import PySide6, PyInstaller" 2>/dev/null; then
  "$venv/bin/python" -m pip install --quiet -r "$raiz/requirements.txt" pyinstaller
fi

cd "$raiz"
"$venv/bin/python" -m PyInstaller packaging/Loom.spec --noconfirm --distpath dist --workpath build

for arquivo in Loom loom-coletor; do
  [ -x "dist/Loom/$arquivo" ] || { echo "$arquivo nao foi gerado" >&2; exit 1; }
done
echo "Pronto: $raiz/dist/Loom"
