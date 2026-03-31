#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT"

echo "=== Starting Trading Bot (background) ==="

# .venv is created by postbuild (uv sync). If missing, run uv sync now.
if [ -f ".venv/pyvenv.cfg" ]; then
  echo "=== .venv ready — starting bot instantly ==="
else
  echo "=== .venv missing — running uv sync now (postbuild may have failed) ==="
  uv sync --frozen --no-dev 2>&1 | tail -10 || echo "uv sync failed"
fi

uv run python bot.py > /tmp/bot_stdout.log 2>&1 &
BOT_PID=$!
echo "Trading bot started with PID: $BOT_PID"

echo "=== Starting API Server (foreground) ==="
cd "$SCRIPT_DIR/.."
node --enable-source-maps ./dist/index.mjs
