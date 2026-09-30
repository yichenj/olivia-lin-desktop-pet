#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f assets/portraits/idle.png ]]; then
  echo "Missing assets/portraits/idle.png." >&2
  exit 1
fi
if [[ ! -f assets/animations/idle.gif && "${OLIVIA_SKIP_RENDER:-0}" != "1" ]] && command -v blender >/dev/null 2>&1; then
  echo "Rendering a short transparent idle loop with Blender (first launch only)…"
  ./scripts/render_idle.sh
fi
exec /usr/bin/python3 pet.py "$@"
