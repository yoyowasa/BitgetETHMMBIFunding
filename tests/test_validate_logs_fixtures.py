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


def run_validate(log_dir: Path, report_path: Path, extra_args: list[str] | None = None):
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
    if extra_args:
        cmd.extend(extra_args)
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


def test_require_fills_and_pnl_pass(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    (log_dir / "system.jsonl").write_text(
        '{"ts":1,"event":"startup","intent":"SYSTEM","source":"test","mode":"RUN","reason":"ok","leg":"system","cycle_id":1}\n',
        encoding="utf-8",
    )
    (log_dir / "orders.jsonl").write_text(
        '{"ts":2,"event":"order_new","intent":"QUOTE_BID","source":"test","mode":"RUN","reason":"quote","leg":"perp","cycle_id":1}\n',
        encoding="utf-8",
    )
    (log_dir / "fills.jsonl").write_text(
        "\n".join(
            [
                '{"ts":3,"event":"fill","intent":"QUOTE_BID","source":"test","mode":"RUN","reason":"fill","leg":"perp","cycle_id":1,"inst_type":"USDT-FUTURES","side":"buy","price":100.0,"size":1.0,"fee":0.1,"simulated":true}',
                '{"ts":4,"event":"fill","intent":"QUOTE_ASK","source":"test","mode":"RUN","reason":"fill","leg":"perp","cycle_id":2,"inst_type":"USDT-FUTURES","side":"sell","price":101.0,"size":1.0,"fee":0.1,"simulated":true}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (log_dir / "decision.jsonl").write_text(
        '{"ts":5,"event":"tick","intent":"quote","source":"strategy","mode":"QUOTING","reason":"quote","leg":"perp","cycle_id":3,"mid_spot":100.0,"mid_perp":101.0}\n',
        encoding="utf-8",
    )

    report_path = tmp_path / "report.json"
    result, report = run_validate(
        log_dir,
        report_path,
        extra_args=["--require-fills", "--min-fills", "2", "--require-pnl"],
    )

    assert result.returncode == 0
    assert report["status"] == "PASS"
    assert report["checks"]["min_fills"]["status"] == "PASS"
    assert report["checks"]["pnl"]["status"] == "PASS"
    assert report["pnl"]["fills_processed"] == 2
    assert report["pnl"]["net_usdt"] == 0.8
