# cancel_aggressive_policy decision

Date: 2026-04-24

## Current decision

- Keep `strategy.cancel_aggressive_policy: current`.
- Do not adopt `overlap_quote_fade_only`.
- Do not adopt `overlap_or_strong_tfi`.
- Do not run `DRY_RUN=0`.
- Do not change guard thresholds.
- Do not change spread settings.

## Config check

- `strategy.cancel_aggressive_policy: current`
- `strategy.dry_run: true`
- `strategy.base_half_spread_bps: 8.0`
- `strategy.min_half_spread_bps: 8.0`

## B: overlap_quote_fade_only

Source: `reports/suppressed_cancel_forward_summary.csv`

- Suppressed count: 1075
- `buy ret_5s`: `count=454`, `unsafe_ratio=0.2841`, `mean_directional_ret_bps=0.1017`
- `sell ret_5s`: `count=621`, `unsafe_ratio=0.4106`, `mean_directional_ret_bps=0.0641`

Decision:

- Sell-side `unsafe_ratio` is high.
- Suppression may be too aggressive.
- Do not adopt for production.

## C: overlap_or_strong_tfi

Source: `reports/suppressed_cancel_forward_summary.csv`

- Suppressed count: 274
- `buy ret_5s`: `count=105`, `unsafe_ratio=0.2762`, `mean_directional_ret_bps=-0.0082`
- `sell ret_5s`: `count=169`, `unsafe_ratio=0.3550`, `mean_directional_ret_bps=0.1666`

Decision:

- Sell-side `unsafe_ratio` is lower than B but still high.
- Suppression may still skip useful defensive cancels.
- Do not adopt for production.

## Overall judgment

- Suppressing `cancel_aggressive` produces too many cases where price later moves in the danger direction, especially on sell-side triggers.
- Current behavior is the safest option at this point.
- Stop additional policy-filtering validation for now.
- Next focus: `edge_negative_total`, spread insufficiency, and continued `quote_fade` evaluation.

## Notes

- Existing simulation CSV remains unchanged: `reports/cancel_aggressive_policy_sim_summary.csv`.
- Existing forward-return CSV remains unchanged except for prior analysis regeneration: `reports/suppressed_cancel_forward_summary.csv`.
- No production behavior was changed by this decision record.
