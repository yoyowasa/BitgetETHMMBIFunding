from __future__ import annotations

import json

from bot.log.jsonl import JsonlLogger


def test_jsonl_logger_rotates_by_size_without_gzip(tmp_path) -> None:
    path = tmp_path / "decision.jsonl"
    logger = JsonlLogger(str(path), max_bytes=220, rotate_daily=False, gzip_rotated=False)

    logger.log({"event": "first", "payload": "x" * 180})
    logger.log({"event": "second", "payload": "y" * 180})

    rotated = list(tmp_path.glob("decision.*.jsonl"))
    assert rotated
    current = json.loads(path.read_text(encoding="utf-8").strip())
    assert current["event"] == "second"


def test_jsonl_logger_rotates_daily_without_gzip(tmp_path) -> None:
    path = tmp_path / "system.jsonl"
    logger = JsonlLogger(str(path), max_bytes=0, rotate_daily=True, gzip_rotated=False)

    logger.log({"event": "before"})
    logger._current_day = "19000101"  # type: ignore[attr-defined]
    logger.log({"event": "after"})

    rotated = list(tmp_path.glob("system.*.jsonl"))
    assert rotated
    current = json.loads(path.read_text(encoding="utf-8").strip())
    assert current["event"] == "after"
