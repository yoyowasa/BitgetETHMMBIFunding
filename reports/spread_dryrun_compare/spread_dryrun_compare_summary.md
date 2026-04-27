# spread DRY_RUN compare

Date: 2026-04-24

## Scope

- Mode: `DRY_RUN=1`
- Compared settings:
  - `15bps`: `base_half_spread_bps=15.0`, `min_half_spread_bps=15.0`
  - `18bps`: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`
- Shared settings:
  - `cancel_aggressive_policy=current`
  - `dry_run=true`
- Production setting was restored after validation:
  - `base_half_spread_bps=8.0`
  - `min_half_spread_bps=8.0`
  - `cancel_aggressive_policy=current`
  - `dry_run=true`

## Artifacts

- `reports/spread_dryrun_compare/15bps/logs/*.jsonl`
- `reports/spread_dryrun_compare/15bps/period_summary.json`
- `reports/spread_dryrun_compare/18bps/logs/*.jsonl`
- `reports/spread_dryrun_compare/18bps/period_summary.json`
- Existing analysis CSV snapshots were copied into each condition directory.

## 15bps result

- Runtime: about 10 min
- `order_new` quote count: 28
- `edge_negative_total`: 0
- `cancel_aggressive`: 1977
- `quote_fade`: 375
- `tfi_fade`: 12
- Guard total: 2364
- `order_skip`: 0
- Mean expected edge bps: 0.9990
- Quote seen: yes
- Quote action span: about 511 sec

## 18bps result

- Runtime: about 10 min
- `order_new` quote count: 44
- `edge_negative_total`: 0
- `cancel_aggressive`: 2160
- `quote_fade`: 184
- `tfi_fade`: 9
- Guard total: 2353
- `order_skip`: 0
- Mean expected edge bps: 7.0000
- Quote seen: yes
- Quote action span: about 505 sec

## Comparison

- 18bps produced more quote orders: `44` vs `28`.
- Both removed `edge_negative_total`: `0` vs `0`.
- Guard total was similar: `18bps=2353`, `15bps=2364`.
- 18bps had fewer `quote_fade` events: `184` vs `375`.
- 18bps had more `cancel_aggressive` events: `2160` vs `1977`.
- `order_skip` did not appear in either run.
- 18bps had stronger theoretical EV: `7.0000bps` vs `0.9990bps`.

## Judgment

- Both 15bps and 18bps can produce quotes under DRY_RUN.
- 15bps is too close to break-even for production judgment.
- 18bps is the stronger next validation candidate because quote count and EV were higher.
- Guard pressure remains high in both cases, especially `cancel_aggressive`.
- No production adoption is made from this run.

## Non-changes

- `DRY_RUN=0` was not run.
- Guard thresholds were not changed.
- Production spread settings were not changed.
- Strategy logic was not changed.
