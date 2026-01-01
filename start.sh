#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR/backend"
uv sync --locked
uv run --locked uvicorn main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
cleanup() { kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true; }
FRONTEND_PID=""
trap cleanup EXIT INT TERM
cd "$ROOT_DIR/frontend"
[ -d node_modules ] || npm ci
npm run dev -- --host 127.0.0.1 --port 5173 &
FRONTEND_PID=$!
echo "OBSERVE: backend and frontend started. Press Ctrl+C to stop."
wait
