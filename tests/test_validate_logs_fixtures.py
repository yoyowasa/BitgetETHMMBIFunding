import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.example.yaml"
REQUIRE_FILES = "system*.jsonl,orders*.jsonl,fills*.jsonl,mm_*.jsonl"


def strict_params() -> tuple[float, int]:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    controlled_grace = float(data["risk"]["controlled_reconnect_grace_sec"]) + 1.0
    hedge_max_tries = int(data["hedge"]["hedge_max_tries"])
    return controlled_grace, hedge_max_tries


def run_validate(log_dir: Path, report_path: Path):
    controlled_grace, hedge_max_tries = strict_params()
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "validate_logs.py"),
        str(log_dir),
        "--json-out",
        str(report_path),
        "--require-files",
        REQUIRE_FILES,
        "--require-ticket-events",
        "--halt-strict",
        "--controlled-grace-sec",
        str(controlled_grace),
        "--hedge-max-tries",
        str(hedge_max_tries),
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return result, report


def test_halt_order_fails(tmp_path: Path) -> None:
    log_dir = ROOT / "tests" / "fixtures" / "logs_fail_halt_order"
    report_path = tmp_path / "report.json"
    result, report = run_validate(log_dir, report_path)
    assert result.returncode != 0
    assert report["status"] == "FAIL"
    assert any(err.startswith("order_after_halt=") for err in report["errors"])


def test_open_ticket_fails(tmp_path: Path) -> None:
    log_dir = ROOT / "tests" / "fixtures" / "logs_fail_open_ticket"
    report_path = tmp_path / "report.json"
    result, report = run_validate(log_dir, report_path)
    assert result.returncode != 0
    assert report["status"] == "FAIL"
    assert "ticket_open_mismatch" in report["errors"]
    assert any(err.startswith("open_tickets_at_end=") for err in report["errors"])
