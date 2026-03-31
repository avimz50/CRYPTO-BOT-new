#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT"

echo "=== Checking Python dependencies ==="

# Check if packages are already installed (postbuild may have installed them)
if python3 -c "import ccxt, telebot, flask, anthropic" 2>/dev/null; then
  echo "=== Python packages already installed — starting bot instantly ==="
else
  echo "=== Installing Python packages via pip ==="
  pip3 install -r requirements.txt --quiet 2>&1 | tail -5 || echo "[WARN] pip install had issues"
  echo "=== pip install done ==="
fi

echo "=== Starting Trading Bot (background) ==="
python3 bot.py > /tmp/bot_stdout.log 2>&1 &
BOT_PID=$!
echo "Trading bot started with PID: $BOT_PID"

echo "=== Starting API Server (foreground) ==="
cd "$SCRIPT_DIR/.."
node --enable-source-maps ./dist/index.mjs
