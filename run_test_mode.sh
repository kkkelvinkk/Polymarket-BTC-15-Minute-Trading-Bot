#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="venv"
STAMP_FILE="$VENV_DIR/.requirements-installed"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Creating virtual environment in ./$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [ ! -f "$STAMP_FILE" ] || [ requirements.txt -nt "$STAMP_FILE" ]; then
  echo "Installing dependencies from requirements.txt"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r requirements.txt
  touch "$STAMP_FILE"
fi

exec "$VENV_DIR/bin/python" 15m_bot_runner.py --test-mode "$@"
