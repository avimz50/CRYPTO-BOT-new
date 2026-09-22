#!/bin/bash
# Production startup script — runs the trading bot via watchdog (auto-restart)
# alongside the API server. The API server runs in the foreground (keeps the
# container alive). The bot runs in the background, supervised by watchdog.py.

set -e

echo "=== [Bot] Production startup — REPO=$(pwd) ==="
echo "=== [Bot] uv=$(which uv 2>/dev/null || echo MISSING) | py=$(which python3 2>/dev/null || echo MISSING) ==="

if command -v uv &>/dev/null; then
  echo "=== [Bot] Using build-prepared Python environment — uv run --no-sync ==="
  if ! uv run --no-sync python -c "import ccxt" 2>/dev/null; then
    echo "=== [Bot] FATAL: Python environment missing or incomplete; production build must run uv sync ==="
    exit 1
  fi
  PYTHON_RUN="uv run --no-sync python"
elif command -v python3 &>/dev/null; then
  echo "=== [Bot] uv not found — using python3 ==="
  python3 -c "import ccxt" 2>/dev/null || pip3 install -r requirements.txt --quiet 2>&1 | tail -5 || true
  PYTHON_RUN="python3"
else
  echo "=== [Bot] FATAL: no Python or uv found ==="
  PYTHON_RUN="echo NO_PYTHON_FOUND"
fi

echo "=== [Bot] Starting watchdog.py (auto-restart supervisor) ==="
# Redirect watchdog stdout/stderr explicitly to the parent's fds (1 & 2) so
# they keep flowing even after `exec node` replaces the bash process below.
# Without this, autoscale loses the background process's pipe on exec.
$PYTHON_RUN -u watchdog.py >&1 2>&1 &
BOT_PID=$!
echo "=== [Bot] Watchdog PID=$BOT_PID — background started ==="

sleep 5
if kill -0 "$BOT_PID" 2>/dev/null; then
  echo "=== [Bot] ✅ Watchdog still running after 5s (PID=$BOT_PID) ==="
else
  echo "=== [Bot] ❌ Watchdog CRASHED within 5s — check logs above ==="
fi

echo "=== [API] Starting API Server (foreground) ==="
exec node --enable-source-maps artifacts/api-server/dist/index.mjs
