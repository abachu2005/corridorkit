#!/bin/zsh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${SKULLBASE_PYTHON:-/opt/anaconda3/bin/python3}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
cd "$ROOT"
exec "$PYTHON" "$ROOT/packaging/launcher.py" --segmented-demo
