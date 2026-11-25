#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Copy .env.example to .env and fill in your credentials."
  exit 1
fi

source .env

# Rebuild the venv if its Python is gone (e.g. the interpreter it was
# created from was removed by an Xcode or Homebrew upgrade).
if ! venv/bin/python3 -c '' 2>/dev/null; then
  echo "venv Python is broken; rebuilding venv..."
  rm -rf venv
  python3 -m venv venv
  venv/bin/pip install --quiet -r requirements.txt
fi

exec venv/bin/python3 app.py
