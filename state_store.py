"""
state_store.py — Persistent state via Replit Object Storage with disk write-through.

Read priority : Object Storage → disk cache → default
Write order   : Object Storage (authoritative) → disk cache (best-effort)

Object Storage is durable across Republish (unlike the ephemeral container
filesystem). The disk cache is kept in sync so the api-server's disk
fallback layer keeps working when Flask is briefly down.

Auth: Replit sidecar at http://127.0.0.1:1106 — no env credentials needed.
Bucket: env var DEFAULT_OBJECT_STORAGE_BUCKET_ID.
Object path prefix: env var PRIVATE_OBJECT_DIR (e.g. "/<bucket>/.private").

Public functions:
    load_state(key, disk_path, default) -> Any
    save_state(key, value, disk_path)   -> None
    reset_state(key, value, disk_path)  -> None  # ignores existing values
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

# ── Object Storage (GCS via Replit sidecar) ────────────────────────────────────
_BUCKET_ID = os.environ.get("DEFAULT_OBJECT_STORAGE_BUCKET_ID", "")
_PRIVATE_DIR = os.environ.get("PRIVATE_OBJECT_DIR", "")
_LOCK = threading.Lock()

_client = None  # lazy-initialised google.cloud.storage.Client
_bucket = None
_init_error: str | None = None


def _sidecar_credentials():
    """Build google.auth credentials backed by the Replit sidecar token endpoint."""
    from google.auth import external_account_authorized_user, identity_pool

    info = {
        "type": "external_account",
        "audience": "replit",
        "subject_token_type": "access_token",
        "token_url": "http://127.0.0.1:1106/token",
        "credential_source": {
            "url": "http://127.0.0.1:1106/credential",
            "format": {
                "type": "json",
                "subject_token_field_name": "access_token",
            },
        },
        "universe_domain": "googleapis.com",
    }
    return identity_pool.Credentials.from_info(info)


def _ensure_client():
    """Lazy GCS client init. Returns (client, bucket) or (None, None) on failure."""
    global _client, _bucket, _init_error
    if _client is not None and _bucket is not None:
        return _client, _bucket
    if not _BUCKET_ID:
        _init_error = "DEFAULT_OBJECT_STORAGE_BUCKET_ID not set"
        return None, None
    try:
        from google.cloud import storage  # type: ignore
        creds = _sidecar_credentials()
        _client = storage.Client(credentials=creds, project="")
        _bucket = _client.bucket(_BUCKET_ID)
        return _client, _bucket
    except Exception as e:
        _init_error = str(e)
        print(f"[state_store] Object Storage init failed: {e}", flush=True)
        return None, None


def _object_name(key: str) -> str:
    """Map a logical key (e.g. 'wallet') to a GCS object path inside PRIVATE_OBJECT_DIR."""
    base = _PRIVATE_DIR.strip("/")
    # PRIVATE_OBJECT_DIR is conventionally "/<bucket>/<sub>"; drop the leading bucket name.
    if base.startswith(f"{_BUCKET_ID}/"):
        base = base[len(_BUCKET_ID) + 1:]
    elif base == _BUCKET_ID:
        base = ""
    return f"{base + '/' if base else ''}state/{key}.json"


def os_get(key: str) -> Any | None:
    """Fetch JSON value from Object Storage. Returns None if missing or unavailable."""
    _, bucket = _ensure_client()
    if bucket is None:
        return None
    try:
        blob = bucket.blob(_object_name(key))
        if not blob.exists():
            return None
        raw = blob.download_as_text()
        return json.loads(raw) if raw else None
    except Exception as e:
        print(f"[state_store] os_get({key}) error: {e}", flush=True)
        return None


def os_set(key: str, value: Any) -> bool:
    """Persist JSON value to Object Storage. Returns True on success."""
    _, bucket = _ensure_client()
    if bucket is None:
        return False
    try:
        blob = bucket.blob(_object_name(key))
        blob.upload_from_string(
            json.dumps(value, default=str, ensure_ascii=False),
            content_type="application/json",
        )
        return True
    except Exception as e:
        print(f"[state_store] os_set({key}) error: {e}", flush=True)
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def load_state(key: str, disk_path: str, default: Any) -> Any:
    """
    Read priority: Object Storage → disk cache → default.
    Always returns a usable value. Emits `[STATE] loaded from <store>` on success.
    """
    with _LOCK:
        # 1) Object Storage (authoritative)
        val = os_get(key)
        if val is not None:
            print(f"[STATE] loaded from object_storage ({key})", flush=True)
            _write_disk_cache(disk_path, val)  # mirror to disk for api-server fallback
            return val

        # 2) Disk cache (warm path inside same deploy)
        try:
            with open(disk_path, "r", encoding="utf-8") as f:
                val = json.load(f)
            print(f"[STATE] loaded from disk_cache ({key} ← {disk_path})", flush=True)
            os_set(key, val)  # backfill durable store so next deploy survives
            return val
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[state_store] {key}: disk read error: {e}", flush=True)

        # 3) Default
        print(f"[STATE] loaded from default ({key} — no durable or disk value)", flush=True)
        if default is not None:
            os_set(key, default)
            _write_disk_cache(disk_path, default)
        return default


def save_state(key: str, value: Any, disk_path: str) -> None:
    """Write to Object Storage (authoritative) then to disk cache (best-effort)."""
    with _LOCK:
        os_set(key, value)
        _write_disk_cache(disk_path, value)


def reset_state(key: str, value: Any, disk_path: str) -> None:
    """
    Force-overwrite both durable store and disk cache, ignoring any existing values.
    Used by the one-time bootstrap to lock in the $200 baseline regardless of stale
    JSON sitting in the deploy snapshot.
    """
    with _LOCK:
        os_set(key, value)
        _write_disk_cache(disk_path, value)
        print(f"[STATE] reset ({key}) → durable + disk overwritten", flush=True)


def _write_disk_cache(disk_path: str, value: Any) -> None:
    try:
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        with open(disk_path, "w", encoding="utf-8") as f:
            json.dump(value, f, default=str, ensure_ascii=False)
    except Exception as e:
        print(f"[state_store] disk cache write error ({disk_path}): {e}", flush=True)
