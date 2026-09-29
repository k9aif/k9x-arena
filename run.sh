#!/usr/bin/env bash
# K9X Arena launcher. Uses ./.venv if present, else a sibling k9-aif-framework/.venv.
set -e
cd "$(dirname "$0")"
if [ -d .venv ]; then VENV=.venv
elif [ -d ../../k9-aif-framework/.venv ]; then VENV=../../k9-aif-framework/.venv
else echo "No venv. Run: python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt"; exit 1; fi
. "$VENV/bin/activate"
pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example — edit it for your Ollama host and models."; }
set -a; . ./.env; set +a
PORT="${ARENA_PORT:-8110}"
echo "K9X Arena → http://localhost:${PORT}"
exec uvicorn arena.api:app --host 0.0.0.0 --port "$PORT"
