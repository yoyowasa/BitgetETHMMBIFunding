from __future__ import annotations

import json
import os
import time
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
    def __init__(self, path: str):
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(self, record: dict[str, Any]) -> None:
        record = _ensure_required_fields(record)  # 書き込み前に必須フィールドを欠落ゼロに整形する
        line = json.dumps(record, ensure_ascii=True)
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
