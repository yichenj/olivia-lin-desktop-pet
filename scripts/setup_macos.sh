#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo "This setup script is for macOS. On Linux, install Python 3 and PyQt5." >&2
  exit 1
fi

# Finder's Terminal session may not have Homebrew on PATH.
BASE_PYTHON=""
for candidate in "${OLIVIA_PYTHON:-python3}" /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    BASE_PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [[ -z "$BASE_PYTHON" ]]; then
  echo "Please install Python 3.10+ from python.org or Homebrew (brew install python), then retry." >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  "$BASE_PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check --only-binary=:all: \
  --index-url "${OLIVIA_PIP_INDEX_URL:-https://pypi.org/simple}" -r requirements.txt
.venv/bin/python -c 'from PyQt5 import QtCore, QtWidgets; print("Ready: Python + Qt", QtCore.QT_VERSION_STR)'
echo "Setup complete. Double-click Start.command or run ./run.sh."
