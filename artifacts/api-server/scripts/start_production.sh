#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "=== Starting Trading Bot (background) ==="
cd "$REPO_ROOT"
python bot.py > /tmp/bot_stdout.log 2> /tmp/bot_stderr.log &
BOT_PID=$!
echo "Bot PID: $BOT_PID"

echo "=== Starting API Server (foreground) ==="
cd "$SCRIPT_DIR/.."
node --enable-source-maps ./dist/index.mjs
