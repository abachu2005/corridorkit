#!/bin/zsh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${CORRIDORKIT_PYTHON:-/opt/anaconda3/bin/python3}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
CASE="$ROOT/research/interactive-real-case-v1/interactive-real-case.json"
if [[ ! -f "$CASE" ]]; then
  print -u2 "Prepare the local public CT example first; see research/prepare_interactive_real_case.py."
  exit 1
fi
cd "$ROOT"
exec "$PYTHON" "$ROOT/packaging/launcher.py" --planning-demo "$CASE"
