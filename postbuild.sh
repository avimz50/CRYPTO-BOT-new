#!/usr/bin/env bash
set -e

echo "=== [postBuild] pnpm store prune ==="
pnpm store prune

echo "=== [postBuild] Pre-installing Python dependencies ==="
uv sync --frozen --no-dev
echo "=== [postBuild] Python deps ready ==="
