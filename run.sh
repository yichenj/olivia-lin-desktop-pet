#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f olivia_idle.png ]]; then
  echo "Missing olivia_idle.png. Place the generated portrait beside pet.py first." >&2
  exit 1
fi
if [[ ! -f olivia_idle.gif && "${OLIVIA_SKIP_RENDER:-0}" != "1" ]] && command -v blender >/dev/null 2>&1; then
  echo "Rendering a short transparent idle loop with Blender (first launch only)…"
  ./render_idle.sh
fi
exec /usr/bin/python3 pet.py "$@"
