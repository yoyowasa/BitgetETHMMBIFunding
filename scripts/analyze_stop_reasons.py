from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


LOG_DIR = Path("logs")
REPORT_DIR = Path("reports")


def main() -> int:
    reason_counter: Counter[str] = Counter()
    edge_stats: list[dict[str, object]] = []

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    for log_file in LOG_DIR.glob("*.jsonl"):
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("event") != "risk":
                    continue

                reason = data.get("reason", "unknown")
                reason_counter[str(reason)] += 1

                if reason == "edge_negative_total":
                    edge_stats.append(
                        {
                            "expected_edge_bps": data.get("expected_edge_bps"),
                            "expected_spread_bps": data.get("expected_spread_bps"),
                            "funding_bps": data.get("funding_bps"),
                            "cost_bps": data.get("cost_bps"),
                            "adverse_buffer_bps": data.get("adverse_buffer_bps"),
                        }
                    )

    with (REPORT_DIR / "stop_reason_counts.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["reason", "count"])
        for reason, count in reason_counter.most_common():
            writer.writerow([reason, count])

    with (REPORT_DIR / "edge_negative_details.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "expected_edge_bps",
                "expected_spread_bps",
                "funding_bps",
                "cost_bps",
                "adverse_buffer_bps",
            ],
        )
        writer.writeheader()
        writer.writerows(edge_stats)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
