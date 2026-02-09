from __future__ import annotations

import argparse
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
        "--terminate-grace-sec",
        type=int,
        default=10,
        help="Grace period after terminate before force kill",
    )
    return parser.parse_args()


def _pump_output(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)


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
    )
    out_thread = threading.Thread(target=_pump_output, args=(proc,), daemon=True)
    out_thread.start()
    started_at = time.time()

    try:
        return proc.wait(timeout=args.duration_sec)
    except subprocess.TimeoutExpired:
        elapsed = int(time.time() - started_at)
        print(
            f"[run_bot_for_duration] timeout elapsed_sec={elapsed} -> terminate",
            flush=True,
        )
        proc.terminate()
        try:
            proc.wait(timeout=args.terminate_grace_sec)
            return 0
        except subprocess.TimeoutExpired:
            print("[run_bot_for_duration] terminate timeout -> kill", flush=True)
            proc.kill()
            proc.wait()
            return 0
    finally:
        out_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
