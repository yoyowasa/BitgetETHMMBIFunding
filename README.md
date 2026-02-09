# Bitget ETH Spot/Perp MM Funding Bot (MVP)

Minimal skeleton for market making + order book imbalance + funding capture on Bitget.

## Setup (Poetry)

```bash
poetry env use 3.11
poetry install
```

Optional pip install:

```bash
pip install -r requirements.txt
```

## Configuration

```bash
copy .env.example .env
```

Environment variables:

- `BITGET_API_KEY`
- `BITGET_API_SECRET`
- `BITGET_API_PASSPHRASE`
- `SYMBOL`
- `SPOT_SYMBOL`
- `PERP_SYMBOL`
- `PRODUCT_TYPE`
- `MARGIN_MODE`
- `MARGIN_COIN`
- `DRY_RUN` (1 or 0)
- `LOG_PATH`

## Run

```bash
poetry run bitget-ws
poetry run bitget-mm
```

Logs are written to `LOG_PATH` (default: `logs/mm_obi_funding.jsonl`) for `bitget-mm`.

To run the full bot config entrypoint (legacy):

```bash
copy config.example.yaml config.yaml
python -m bot.app --config config.yaml
```

Near-live validation (Windows, safe):

```powershell
.\scripts\run_near_live_validation.ps1 -DurationSec 180 -PrivateMode auto
```

Order-fill + provisional PnL validation (dry-run simulation):

```powershell
.\scripts\run_near_live_validation.ps1 `
  -DurationSec 180 `
  -PrivateMode private `
  -EnableFillSimulation `
  -SimFillIntervalSec 3 `
  -SimFillQty 0.01 `
  -SimFillSide both `
  -MinFills 4 `
  -RequirePnl
```

`PrivateMode` options:
- `auto`: uses `public` when another `bot.app` process exists, otherwise `private`
- `public`: forces private API off (`FORCE_PRIVATE_OFF=1`) to avoid account-side impact
- `private`: enables private WS path (fails fast if another `bot.app` is running)

This always uses `scripts/run_real_logs.ps1`, forces `DRY_RUN=1`, writes logs under
`runtime_logs/<RUN_ID>`, and runs strict validation into `artifacts/near_live_validate_<RUN_ID>.json`.

Legacy env flags:

- `BOT_MODE` (dry or live)
- `LOG_DIR` (default: log)

## Bitget Payload Templates

Reference payload builders live in `bot/exchange/bitget_payloads.py`.

## Codex Checklist

P0:
- Poetry project and dependencies
- .env loading for API keys
- Bitget REST wrappers (spot/perp place/cancel)
- clientOid everywhere
- JSONL logging for orders/fills/decisions
- Dry-run mode logs payloads without sending

P1:
- BitgetV2DataStore public books (spot/perp)
- BBO + OBI + microprice calculations

P2:
- Perp post-only quotes (1 bid/1 ask)
- Fill detect -> Spot limit IOC hedge
- Unhedged guards (time + notional) and flatten

P3:
- Funding polling (current-fund-rate)
- Funding threshold gate
- Optional hysteresis / min-hold
