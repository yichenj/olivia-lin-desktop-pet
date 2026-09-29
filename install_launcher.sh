#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/applications/olivia-lin-fan-pet.desktop"
mkdir -p "$(dirname "$DEST")"
sed "s|^Exec=.*|Exec=$ROOT/run.sh|; s|^Path=.*|Path=$ROOT|" "$ROOT/Olivia-Lin-Fan-Pet.desktop" > "$DEST"
chmod 644 "$DEST"
printf 'Installed launcher: %s\n' "$DEST"
