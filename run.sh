#!/bin/sh
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Install Python 3.10+ first"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt || exit 1
[ -f .env ] || cp .env.example .env
cd backend
(sleep 2; command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000 || open http://127.0.0.1:8000) >/dev/null 2>&1 &
uvicorn main:app --port 8000
