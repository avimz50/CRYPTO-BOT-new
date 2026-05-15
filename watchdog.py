#!/usr/bin/env python3
"""
watchdog.py — keeps bot.py alive 24/7.

Launches bot.py as a subprocess, monitors it, and restarts it automatically
if it exits for any reason. Restart delay is capped at 10 seconds.

Log files written:
  /tmp/bot_stdout.log  — combined stdout of the current bot process
  /tmp/bot_stderr.log  — combined stderr of the current bot process
  /tmp/bot_crash.log   — traceback / exit info for the last crash
  /tmp/bot_restart.log — JSON with restart count, last restart timestamp, history
"""

import subprocess
import sys
import os
import shutil
import time
import json
import signal
import threading

RESTART_DELAY = 8           # seconds to wait before restarting after a crash
MAX_HISTORY   = 20          # keep the last N restart events in the log
RESTART_LOG   = "/tmp/bot_restart.log"
CRASH_LOG     = "/tmp/bot_crash.log"
STDOUT_LOG    = "/tmp/bot_stdout.log"
STDERR_LOG    = "/tmp/bot_stderr.log"

_shutdown = threading.Event()
_current_proc: subprocess.Popen | None = None


def _now_utc() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def _load_restart_log() -> dict:
    try:
        with open(RESTART_LOG, "r") as f:
            return json.load(f)
    except Exception:
        return {"restart_count": 0, "last_restart_at": None, "history": []}


def _save_restart_log(data: dict) -> None:
    try:
        with open(RESTART_LOG, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[WATCHDOG] Could not write restart log: {e}", flush=True)


def _record_restart(exit_code: int | None, reason: str) -> None:
    data = _load_restart_log()
    ts = _now_utc()
    data["restart_count"] = data.get("restart_count", 0) + 1
    data["last_restart_at"] = ts
    data["last_exit_code"] = exit_code
    data["last_reason"] = reason
    history: list = data.get("history", [])
    history.append({"ts": ts, "exit_code": exit_code, "reason": reason})
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    data["history"] = history
    _save_restart_log(data)


def _write_crash_log(exit_code: int | None, stderr_tail: str) -> None:
    ts = _now_utc()
    try:
        with open(CRASH_LOG, "w") as f:
            f.write(f"bot.py exited at {ts}\n")
            f.write(f"Exit code: {exit_code}\n\n")
            if stderr_tail:
                f.write("--- Last stderr output ---\n")
                f.write(stderr_tail)
    except Exception as e:
        print(f"[WATCHDOG] Could not write crash log: {e}", flush=True)


def _stream_pipe(pipe, log_path: str, label: str) -> list[str]:
    """Read a pipe line-by-line, tee to stdout and to a log file.
    Returns the last 100 lines seen (for crash log snippet)."""
    lines: list[str] = []
    try:
        with open(log_path, "w") as lf:
            for raw in pipe:
                line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
                lines.append(line)
                if len(lines) > 200:
                    lines = lines[-200:]
                sys.stdout.write(f"[{label}] {line}")
                sys.stdout.flush()
                lf.write(line)
                lf.flush()
    except Exception as e:
        print(f"[WATCHDOG] Stream error ({label}): {e}", flush=True)
    return lines


def _find_python() -> list[str]:
    """Return the command prefix to run bot.py with the right Python."""
    if os.path.exists(".venv/pyvenv.cfg"):
        if shutil.which("uv"):
            return ["uv", "run", "--no-sync", "python"]
    for candidate in [".venv/bin/python", "python3", "python"]:
        resolved = candidate if os.path.isabs(candidate) else shutil.which(candidate) or (
            candidate if os.path.exists(candidate) else None
        )
        if resolved:
            try:
                subprocess.run([resolved, "--version"], capture_output=True, check=True)
                return [resolved]
            except Exception:
                continue
    return ["python3"]


def _handle_signal(signum, _frame):
    """Gracefully shut down watchdog and terminate the bot subprocess."""
    print(f"[WATCHDOG] Signal {signum} received — shutting down.", flush=True)
    _shutdown.set()
    if _current_proc and _current_proc.poll() is None:
        print("[WATCHDOG] Sending SIGTERM to bot.py …", flush=True)
        _current_proc.terminate()
        try:
            _current_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _current_proc.kill()


def run_watchdog():
    global _current_proc

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    python_cmd = _find_python()
    cmd = python_cmd + ["-u", "bot.py"]

    repo_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(repo_root)

    print(f"[WATCHDOG] Starting. Python command: {' '.join(python_cmd)}", flush=True)
    print(f"[WATCHDOG] Working directory: {repo_root}", flush=True)

    first_run = True

    while not _shutdown.is_set():
        if not first_run:
            print(f"[WATCHDOG] Restarting bot.py in {RESTART_DELAY}s …", flush=True)
            _shutdown.wait(timeout=RESTART_DELAY)
            if _shutdown.is_set():
                break

        first_run = False
        ts = _now_utc()
        print(f"[WATCHDOG] Launching bot.py at {ts}", flush=True)

        try:
            child_env = os.environ.copy()
            child_env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=child_env,
            )
        except Exception as e:
            reason = f"Failed to launch: {e}"
            print(f"[WATCHDOG] {reason}", flush=True)
            _write_crash_log(None, str(e))
            _record_restart(None, reason)
            _shutdown.wait(timeout=RESTART_DELAY)
            continue

        _current_proc = proc

        stderr_lines: list[str] = []

        stdout_thread = threading.Thread(
            target=_stream_pipe,
            args=(proc.stdout, STDOUT_LOG, "BOT"),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=lambda: stderr_lines.extend(
                _stream_pipe(proc.stderr, STDERR_LOG, "BOT-ERR")
            ),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        exit_code = proc.wait()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        _current_proc = None

        if _shutdown.is_set():
            print(f"[WATCHDOG] Bot exited with code {exit_code} (shutdown requested).", flush=True)
            break

        reason = f"Exited with code {exit_code}"
        stderr_tail = "".join(stderr_lines[-100:]) if stderr_lines else ""

        print(f"[WATCHDOG] ⚠ bot.py {reason} at {_now_utc()}", flush=True)

        _write_crash_log(exit_code, stderr_tail)
        _record_restart(exit_code, reason)

    print("[WATCHDOG] Exiting.", flush=True)


if __name__ == "__main__":
    run_watchdog()
