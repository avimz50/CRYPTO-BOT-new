#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT"

# ── Python packages strategy (fastest first) ─────────────────────────────────
# 1. .pythonlibs/ — Replit-managed packages bundled with workspace (INSTANT)
# 2. .venv/       — uv virtual env if it exists
# 3. uv sync      — fallback: installs from scratch (~5-20 min)

PYTHONLIBS="$REPO_ROOT/.pythonlibs/lib/python3.12/site-packages"

if [ -d "$PYTHONLIBS" ] && python3 -c "import sys; sys.path.insert(0,'$PYTHONLIBS'); import ccxt, telebot, flask" 2>/dev/null; then
  echo "=== .pythonlibs found — running with system Python (instant) ==="
  export PYTHONPATH="$PYTHONLIBS:$PYTHONPATH"
  python3 bot.py > /tmp/bot_stdout.log 2>&1 &

elif [ -f ".venv/pyvenv.cfg" ]; then
  echo "=== .venv found — running with uv ==="
  uv run python bot.py > /tmp/bot_stdout.log 2>&1 &

else
  echo "=== No packages found — running uv sync (first-time setup) ==="
  uv sync --frozen --no-dev 2>&1 | tail -10 || echo "uv sync failed"
  uv run python bot.py > /tmp/bot_stdout.log 2>&1 &
fi

BOT_PID=$!
echo "Trading bot started with PID: $BOT_PID"

echo "=== Starting API Server (foreground) ==="
cd "$SCRIPT_DIR/.."
node --enable-source-maps ./dist/index.mjs
