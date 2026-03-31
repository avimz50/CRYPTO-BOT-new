#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT"

# uv sync runs during postBuild — skip if .venv already exists
if [ ! -f ".venv/pyvenv.cfg" ]; then
  echo "=== .venv not found — running uv sync (first boot) ==="
  uv sync --frozen --no-dev 2>&1 | tail -10 || echo "uv sync failed"
else
  echo "=== .venv found — skipping uv sync ==="
fi

echo "=== Starting Trading Bot (background) ==="
uv run python bot.py > /tmp/bot_stdout.log 2>&1 &
BOT_PID=$!
echo "Bot PID: $BOT_PID"

echo "=== Starting API Server (foreground) ==="
cd "$SCRIPT_DIR/.."
node --enable-source-maps ./dist/index.mjs
