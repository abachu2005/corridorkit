#!/bin/zsh
# Launch the local public computational example without relying on an editable install.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${SKULLBASE_PYTHON:-/opt/anaconda3/bin/python3}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
CASE="$ROOT/research/data-cache/desktop-smoke/computational-case.json"
if [[ ! -f "$CASE" ]]; then
  print -u2 "Public demonstration case is missing. See research/prepare_public_smoke.py."
  exit 1
fi
cd "$ROOT"
exec "$PYTHON" "$ROOT/packaging/launcher.py" --planning-demo "$CASE"
