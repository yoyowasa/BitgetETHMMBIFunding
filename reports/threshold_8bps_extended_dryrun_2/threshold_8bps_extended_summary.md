# threshold_8bps extended DRY_RUN summary 2

## 観測事実
- 条件: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `cancel_aggressive_quality_filter=fresh_active_quote_proximity`, `tfi_fade_policy=disabled`, `quote_fade_policy=threshold_8bps`, `one_sided_quote_policy=current`, `dry_run=true`
- 実行時間: `1800 sec`
- 起動確認: `env_DRY_RUN=1`, `dry_run=True`
- 保存先: `reports\threshold_8bps_extended_dryrun_2\`
- 対象期間ログ抜粋: `reports\threshold_8bps_extended_dryrun_2\logs\`

## 代表値
- `pre_quote_decision_rows=13437`
- `order_new quote=1648`
- `quote_lifetime median=1.007175 sec`
- `quote_lifetime mean=1.525694 sec`
- `quote_lifetime p75=2.254137 sec`
- `quote_lifetime p90=3.516080 sec`
- `quote_fade end=800`
- `tfi_fade end=0`
- `cancel_aggressive end=0`
- `order_cancel:quote=846`
- `quote_fade_suppressed=1245`
- `tfi_fade_suppressed=4711`
- `cancel_aggressive_quality_suppressed=5929`
- `final_block_reason`: `none=12877`, `quote_fade=560`

## quote_fade exit quality
- `3s`: `success_ratio=0.256250`, `fail_ratio=0.137500`, `neutral_ratio=0.606250`, `mean_directional_ret_bps=0.245467`
- `5s`: `success_ratio=0.347500`, `fail_ratio=0.208750`, `neutral_ratio=0.443750`, `mean_directional_ret_bps=0.252714`

## active cancel quality
- `active_cancel_rows=0`
- `valid_candidate_ratio=null`

## 1回目との比較
- 1回目: `order_new quote=1391`, `median_lifetime=1.262800 sec`, `mean=1.999645 sec`, `p75=2.736997 sec`, `p90=5.036344 sec`
- 2回目: `order_new quote=1648`, `median_lifetime=1.007175 sec`, `mean=1.525694 sec`, `p75=2.254137 sec`, `p90=3.516080 sec`
- 1回目: `quote_fade end=865`, `order_cancel:quote=526`, `quote_fade_suppressed=1518`, `tfi_fade_suppressed=5142`, `cancel_aggressive_quality_suppressed=6017`
- 2回目: `quote_fade end=800`, `order_cancel:quote=846`, `quote_fade_suppressed=1245`, `tfi_fade_suppressed=4711`, `cancel_aggressive_quality_suppressed=5929`
- 1回目 final_block_reason: `none=13028`, `quote_fade=567`
- 2回目 final_block_reason: `none=12877`, `quote_fade=560`
- 1回目 quote_fade 3s: `success=0.131792`, `fail=0.079769`, `mean_directional_ret=0.180568`
- 2回目 quote_fade 3s: `success=0.256250`, `fail=0.137500`, `mean_directional_ret=0.245467`
- 1回目 quote_fade 5s: `success=0.203468`, `fail=0.131792`, `mean_directional_ret=0.188410`
- 2回目 quote_fade 5s: `success=0.347500`, `fail=0.208750`, `mean_directional_ret=0.252714`

## 推論
- `median_lifetime=1.007175 sec` で判断基準の `0.75 sec` を再現。`threshold_8bps` は継続 DRY_RUN 候補。
- `order_new quote=1648` で、quote 機会の極端な減少は見えない。
- `order_cancel:quote=846` は1回目の `526` より増加。quote を残した副作用として要注意。
- quote_fade exit quality は 3s / 5s とも success_ratio が fail_ratio を上回り、mean directional return もプラス。
- `active_cancel_rows=0` のため、cancel_aggressive は依然として採用材料不足。

## 未確定点
- 2回とも `threshold_8bps` の寿命改善は再現。ただし `order_cancel:quote` 増加の意味は未確定。
- この検証だけで本番採用しない。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、実判定変更は未実施。
