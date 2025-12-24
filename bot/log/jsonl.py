from __future__ import annotations

import json
import os
import time
from typing import Any


class JsonlLogger:
    def __init__(self, path: str):
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(self, record: dict[str, Any]) -> None:
        if "ts" not in record:
            record["ts"] = time.time()
        line = json.dumps(record, ensure_ascii=True)
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
