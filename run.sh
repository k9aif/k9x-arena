#!/usr/bin/env bash
# K9X Arena launcher. Uses ./.venv if present, else the k9-aif-framework/.venv beside it (flat or nested layout).
set -e
cd "$(dirname "$0")"
if [ -d .venv ]; then VENV=.venv
elif [ -d ../k9-aif-framework/.venv ]; then VENV=../k9-aif-framework/.venv        # flat: ~/ai/k9x-arena
elif [ -d ../../k9-aif-framework/.venv ]; then VENV=../../k9-aif-framework/.venv  # nested: ~/ai/k9x-ecosystem/k9x_arena
else echo "No venv. Run: python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt"; exit 1; fi
. "$VENV/bin/activate"
pip install -q -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it for your Ollama host and models, then run ./run.sh again."
  exit 1
fi
set -a; . ./.env; set +a
python -m arena.preflight || exit 1
PORT="${ARENA_PORT:-8111}"
echo "K9X Arena → http://localhost:${PORT}"
exec uvicorn arena.api:app --host 0.0.0.0 --port "$PORT"
