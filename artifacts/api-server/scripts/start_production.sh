#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT"

# ── הפעל bot.py רק ב-production (REPLIT_DEPLOYMENT=1) ──────────────────────
if [ -n "$REPLIT_DEPLOYMENT" ]; then
  echo "=== [Bot] Production mode detected — starting bot.py in background ==="
  echo "=== [Bot] REPO=$REPO_ROOT | uv=$(which uv 2>/dev/null || echo MISSING) | py=$(which python3 2>/dev/null || echo MISSING) ==="

  if command -v uv &>/dev/null; then
    if [ -f ".venv/pyvenv.cfg" ]; then
      echo "=== [Bot] .venv ready — uv run --no-sync ==="
      PYTHON_RUN="uv run --no-sync python"
    else
      echo "=== [Bot] .venv missing — running uv sync ==="
      uv sync --frozen --no-dev 2>&1 | tail -5 || echo "[WARN] uv sync failed"
      PYTHON_RUN="uv run python"
    fi
  elif command -v python3 &>/dev/null; then
    echo "=== [Bot] uv not found — using python3 ==="
    python3 -c "import ccxt" 2>/dev/null || pip3 install -r requirements.txt --quiet 2>&1 | tail -5 || true
    PYTHON_RUN="python3"
  else
    echo "=== [Bot] FATAL: no Python or uv found ==="
    PYTHON_RUN="echo NO_PYTHON_FOUND"
  fi

  echo "=== [Bot] Starting: $PYTHON_RUN bot.py ==="
  $PYTHON_RUN bot.py >> /tmp/bot_stdout.log 2>>/tmp/bot_stderr.log &
  BOT_PID=$!
  echo "=== [Bot] PID=$BOT_PID — background started ==="

  # המתן רגע לוודא שה-Flask עלה לפני שמתחיל Node
  sleep 3
  echo "=== [Bot] Flask startup wait done ==="
else
  echo "=== [API] Dev mode — bot.py managed by separate Trading Bot workflow ==="
fi

echo "=== [API] Starting Node.js API server ==="
cd "$SCRIPT_DIR/.."
node --enable-source-maps ./dist/index.mjs
