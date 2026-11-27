#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  BASE_PY=$(command -v python3 || command -v python)
  if [ -z "$BASE_PY" ]; then
    echo "Python 3.10+ not found. Please install Python 3 first."
    exit 1
  fi
  if "$BASE_PY" -c "import pygame, numpy, numba" >/dev/null 2>&1; then
    PY="$BASE_PY"
  else
    echo "First-time setup: creating .venv and installing dependencies..."
    "$BASE_PY" -m venv .venv
    PY=".venv/bin/python"
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$PY" -m pip install -r requirements.txt
  fi
fi

exec "$PY" main.py "$@"
