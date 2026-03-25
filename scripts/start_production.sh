#!/bin/bash
# Production startup script — runs both the trading bot and the API server.
# The API server runs in the foreground (keeps the container alive).
# The trading bot runs in the background alongside it.

set -e

echo "=== Starting Trading Bot (background) ==="
python bot.py &
BOT_PID=$!
echo "Trading bot started with PID: $BOT_PID"

echo "=== Starting API Server (foreground) ==="
exec node --enable-source-maps artifacts/api-server/dist/index.mjs
