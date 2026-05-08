from __future__ import annotations

import argparse
import os
import signal
import subprocess
import threading
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run bot.app for a bounded duration and relay stdout/stderr."
    )
    parser.add_argument(
        "--python-exe",
        default=r".\.venv\Scripts\python.exe",
        help="Python executable used to launch bot.app",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to bot config file",
    )
    parser.add_argument(
        "--duration-sec",
        type=int,
        default=300,
        help="Run duration in seconds before graceful stop",
    )
    parser.add_argument(
        "--graceful-shutdown-sec",
        type=int,
        default=20,
        help="Grace period after graceful stop signal before force kill",
    )
    return parser.parse_args()


def _pump_output(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)


def _creationflags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def _request_graceful_shutdown(proc: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGINT)


def _stop_after_timeout(
    proc: subprocess.Popen[str],
    *,
    elapsed_sec: int,
    graceful_shutdown_sec: int,
) -> int:
    print(
        f"[run_bot_for_duration] timeout elapsed_sec={elapsed_sec} -> graceful_shutdown",
        flush=True,
    )
    _request_graceful_shutdown(proc)
    try:
        return proc.wait(timeout=graceful_shutdown_sec)
    except subprocess.TimeoutExpired:
        print(
            "[run_bot_for_duration] bounded_graceful_shutdown_timeout -> kill",
            flush=True,
        )
        proc.kill()
        proc.wait()
        return 1


def main() -> int:
    args = _parse_args()
    python_exe = Path(args.python_exe)
    if not python_exe.exists():
        print(f"[run_bot_for_duration] ERROR: python executable not found: {python_exe}")
        return 2
    if args.duration_sec <= 0:
        print("[run_bot_for_duration] ERROR: duration-sec must be > 0")
        return 2

    cmd = [
        str(python_exe),
        "-m",
        "bot.app",
        "--config",
        args.config,
    ]
    print(
        f"[run_bot_for_duration] start duration_sec={args.duration_sec} cmd={' '.join(cmd)}",
        flush=True,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=_creationflags(),
    )
    out_thread = threading.Thread(target=_pump_output, args=(proc,), daemon=True)
    out_thread.start()
    started_at = time.time()

    try:
        return proc.wait(timeout=args.duration_sec)
    except subprocess.TimeoutExpired:
        elapsed = int(time.time() - started_at)
        return _stop_after_timeout(
            proc,
            elapsed_sec=elapsed,
            graceful_shutdown_sec=args.graceful_shutdown_sec,
        )
    finally:
        out_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
