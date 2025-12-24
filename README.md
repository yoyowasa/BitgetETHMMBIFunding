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
