from __future__ import annotations

import gzip
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "ts",
    "event",
    "intent",
    "source",
    "mode",
    "reason",
    "leg",
    "cycle_id",
    "data",
    "res",
    "simulated",
)


def _coerce_dict(value: object) -> dict:
    # data/res を必ず dict にする（型ブレで strict が落ちるのを防ぐ）
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return {"value": value}


def _ensure_required_fields(rec: dict) -> dict:
    # ログ1行の必須フィールドを「欠落ゼロ」に揃える（設計前提を満たす）
    rec = dict(rec)  # 呼び出し側のdictを壊さないようにコピーする

    rec.setdefault("ts", int(time.time() * 1000))  # ts が無ければ「今」を埋める
    rec.setdefault("event", "unknown")  # event が無ければ unknown にする
    rec.setdefault("intent", "unknown")  # intent が無ければ unknown にする
    rec.setdefault("source", "unknown")  # source が無ければ unknown にする
    rec.setdefault("mode", "UNKNOWN")  # mode が無ければ UNKNOWN にする
    rec.setdefault("reason", "unknown")  # reason が無ければ unknown にする
    rec.setdefault("leg", "unknown")  # leg が無ければ unknown にする
    rec.setdefault("cycle_id", "-")  # cycle_id が無ければダミーを入れる

    rec["data"] = _coerce_dict(rec.get("data"))  # data は必ず dict にする
    rec["res"] = _coerce_dict(rec.get("res"))  # res は必ず dict にする
    rec.setdefault("simulated", False)  # simulated は必ず bool にする

    # もし将来フィールドが増えても、ここで必須を強制できるようにする
    for k in REQUIRED_FIELDS:
        rec.setdefault(k, None)  # 最後の保険（基本は上の setdefault で埋まる想定）

    return rec


class JsonlLogger:
    def __init__(
        self,
        path: str,
        *,
        max_bytes: int | None = None,
        rotate_daily: bool | None = None,
        gzip_rotated: bool | None = None,
    ):
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        default_max_bytes = 512 * 1024 * 1024 if max_bytes is None else max_bytes
        self._max_bytes = _env_int("LOG_ROTATE_MAX_BYTES", default_max_bytes)
        self._rotate_daily = _env_bool("LOG_ROTATE_DAILY", True if rotate_daily is None else rotate_daily)
        self._gzip_rotated = _env_bool("LOG_ROTATE_GZIP", True if gzip_rotated is None else gzip_rotated)
        self._current_day = _day_stamp(time.time())
        self._lock = threading.Lock()

    def log(self, record: dict[str, Any]) -> None:
        record = _ensure_required_fields(record)  # 書き込み前に必須フィールドを欠落ゼロに整形する
        line = json.dumps(record, ensure_ascii=True)
        with self._lock:
            self._rotate_if_needed(len(line) + 1)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        path = Path(self._path)
        now = time.time()
        day = _day_stamp(now)
        day_changed = self._rotate_daily and path.exists() and day != self._current_day
        size_exceeded = (
            self._max_bytes > 0
            and path.exists()
            and path.stat().st_size + incoming_bytes > self._max_bytes
        )
        if not day_changed and not size_exceeded:
            self._current_day = day
            return

        rotated = _rotated_path(path, now)
        path.replace(rotated)
        self._current_day = day
        if self._gzip_rotated:
            _gzip_in_background(rotated)


def _day_stamp(ts: float) -> str:
    return time.strftime("%Y%m%d", time.localtime(ts))


def _rotated_path(path: Path, ts: float) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
    for index in range(1000):
        suffix = f".{stamp}.{index:03d}.jsonl"
        candidate = path.with_name(f"{path.stem}{suffix}")
        if not candidate.exists() and not candidate.with_suffix(candidate.suffix + ".gz").exists():
            return candidate
    return path.with_name(f"{path.stem}.{stamp}.{os.getpid()}.jsonl")


def _gzip_in_background(path: Path) -> None:
    def worker() -> None:
        gz_path = path.with_suffix(path.suffix + ".gz")
        try:
            with path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            path.unlink(missing_ok=True)
        except Exception:
            gz_path.unlink(missing_ok=True)

    thread = threading.Thread(target=worker, name=f"gzip-jsonl-{path.name}", daemon=True)
    thread.start()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
