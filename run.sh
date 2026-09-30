#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -n "${OLIVIA_PYTHON:-}" ]]; then
  PET_PYTHON="$OLIVIA_PYTHON"
elif [[ -x .venv/bin/python ]]; then
  PET_PYTHON="$PWD/.venv/bin/python"
else
  PET_PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PET_PYTHON" ]] || ! "$PET_PYTHON" -c 'from PyQt5 import QtWidgets' >/dev/null 2>&1; then
  echo "Python 3 with PyQt5 is required. On macOS run ./scripts/setup_macos.sh first." >&2
  exit 1
fi
if [[ "$(uname -s)" == Darwin ]]; then
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-cocoa}"
fi
if [[ ! -f assets/portraits/idle.png ]]; then
  echo "Missing assets/portraits/idle.png." >&2
  exit 1
fi
if [[ ! -f assets/animations/idle.gif && "${OLIVIA_SKIP_RENDER:-0}" != "1" ]] && command -v blender >/dev/null 2>&1 && command -v ffmpeg >/dev/null 2>&1; then
  echo "Rendering a short transparent idle loop with Blender (first launch only)…"
  ./scripts/render_idle.sh
fi
exec "$PET_PYTHON" pet.py "$@"
