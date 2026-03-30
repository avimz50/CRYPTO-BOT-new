#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "=== Installing Python dependencies (uv) ==="
cd "$REPO_ROOT"
uv sync --frozen --no-dev 2>&1 | tail -5 || echo "uv sync failed, trying pip fallback..."

echo "=== Starting Trading Bot (background) ==="
uv run python bot.py > /tmp/bot_stdout.log 2> /tmp/bot_stderr.log &
BOT_PID=$!
echo "Bot PID: $BOT_PID"

echo "=== Starting API Server (foreground) ==="
cd "$SCRIPT_DIR/.."
node --enable-source-maps ./dist/index.mjs
