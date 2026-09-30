#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
on_error() {
  echo "Olivia could not start. See the error above."
  if [[ -t 0 ]]; then read -r -p "Press Return to close..." _reply; fi
}
trap on_error ERR
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'from PyQt5 import QtWidgets' >/dev/null 2>&1; then
  ./scripts/setup_macos.sh
fi
./run.sh "$@"
