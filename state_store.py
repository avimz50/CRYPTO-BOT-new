"""
state_store.py — Persistent state via Replit DB with disk write-through.

Why this exists:
  Replit deploys are ephemeral — the filesystem resets on every Republish.
  Tracked JSON files in artifacts/bot-dashboard/public/ also get reset
  whenever git overwrites them. This module makes Replit DB the single
  source of truth and writes a *cache copy* to disk so the api-server
  (TypeScript) can fall back to disk if the bot/Flask is unreachable.

Read order  : Replit DB → disk cache → default
Write order : Replit DB (authoritative) → disk cache (best-effort)

Replit DB is accessed over HTTP via REPLIT_DB_URL — same env var available
in both Repl runtime and Replit Deployments. No extra package needed.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

import requests

_DB_URL = os.environ.get("REPLIT_DB_URL", "").rstrip("/")
_LOCK = threading.Lock()
_TIMEOUT = 5  # seconds


def _db_available() -> bool:
    return bool(_DB_URL)


def db_get(key: str) -> Any | None:
    """Fetch JSON value from Replit DB. Returns None if missing or unavailable."""
    if not _db_available():
        return None
    try:
        r = requests.get(f"{_DB_URL}/{key}", timeout=_TIMEOUT)
        if r.status_code == 404 or not r.text:
            return None
        if r.status_code != 200:
            print(f"[state_store] db_get({key}) HTTP {r.status_code}", flush=True)
            return None
        return json.loads(r.text)
    except Exception as e:
        print(f"[state_store] db_get({key}) error: {e}", flush=True)
        return None


def db_set(key: str, value: Any) -> bool:
    """Persist JSON value to Replit DB. Returns True on success."""
    if not _db_available():
        return False
    try:
        body = json.dumps(value, default=str)
        r = requests.post(
            _DB_URL,
            data={key: body},
            timeout=_TIMEOUT,
        )
        if r.status_code not in (200, 204):
            print(f"[state_store] db_set({key}) HTTP {r.status_code}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[state_store] db_set({key}) error: {e}", flush=True)
        return False


def load_state(key: str, disk_path: str, default: Any) -> Any:
    """
    Read priority: Replit DB → disk cache → default.
    Always returns a usable value. Logs which source was used.
    """
    with _LOCK:
        # 1) Replit DB (authoritative)
        val = db_get(key)
        if val is not None:
            print(f"[state_store] {key}: loaded from Replit DB", flush=True)
            # Mirror to disk so api-server's disk fallback stays consistent
            _write_disk_cache(disk_path, val)
            return val

        # 2) Disk cache (survives intra-deploy restarts at least)
        try:
            with open(disk_path, "r", encoding="utf-8") as f:
                val = json.load(f)
            print(f"[state_store] {key}: loaded from disk cache ({disk_path})", flush=True)
            # Backfill DB so next read is durable
            db_set(key, val)
            return val
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[state_store] {key}: disk read error: {e}", flush=True)

        # 3) Default
        print(f"[state_store] {key}: using default (no DB or disk value)", flush=True)
        if default is not None:
            db_set(key, default)
            _write_disk_cache(disk_path, default)
        return default


def save_state(key: str, value: Any, disk_path: str) -> None:
    """Write to Replit DB (authoritative) then to disk cache (best-effort)."""
    with _LOCK:
        db_set(key, value)
        _write_disk_cache(disk_path, value)


def _write_disk_cache(disk_path: str, value: Any) -> None:
    try:
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        with open(disk_path, "w", encoding="utf-8") as f:
            json.dump(value, f, default=str, ensure_ascii=False)
    except Exception as e:
        print(f"[state_store] disk cache write error ({disk_path}): {e}", flush=True)
