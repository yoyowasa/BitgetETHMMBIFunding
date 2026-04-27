# threshold_8bps extended DRY_RUN summary

## 観測事実
- 条件: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `cancel_aggressive_quality_filter=fresh_active_quote_proximity`, `tfi_fade_policy=disabled`, `quote_fade_policy=threshold_8bps`, `one_sided_quote_policy=current`, `dry_run=true`
- 実行時間: `1800 sec`
- 起動確認: `env_DRY_RUN=1`, `dry_run=True`
- 保存先: `reports\threshold_8bps_extended_dryrun\`
- 対象期間ログ抜粋: `reports\threshold_8bps_extended_dryrun\logs\`

## 代表値
- `pre_quote_decision_rows=13595`
- `order_new quote=1391`
- `quote_lifetime median=1.262800 sec`
- `quote_lifetime mean=1.999645 sec`
- `quote_lifetime p75=2.736997 sec`
- `quote_lifetime p90=5.036344 sec`
- `quote_fade end=865`
- `tfi_fade end=0`
- `cancel_aggressive end=0`
- `order_cancel:quote=526`
- `quote_fade_suppressed=1518`
- `tfi_fade_suppressed=5142`
- `cancel_aggressive_quality_suppressed=6017`
- `final_block_reason`: `none=13028`, `quote_fade=567`

## quote_fade exit quality
- `3s`: `success_ratio=0.131792`, `fail_ratio=0.079769`, `neutral_ratio=0.788439`, `mean_directional_ret_bps=0.180568`
- `5s`: `success_ratio=0.203468`, `fail_ratio=0.131792`, `neutral_ratio=0.664740`, `mean_directional_ret_bps=0.188410`

## active cancel quality
- `active_cancel_rows=0`
- `valid_candidate_ratio=null`

## 前回比較値
- `current`: `quote=990`, `median_lifetime=0.516929 sec`, `quote_fade_end=753`, `order_cancel:quote=235`
- `threshold_8bps`: `quote=640`, `median_lifetime=0.784076 sec`, `quote_fade_end=544`, `order_cancel:quote=94`, `danger_after_suppression_3s=0.160356`

## 推論
- `median_lifetime=1.262800 sec` で判断基準の `0.75 sec` を維持。`threshold_8bps` は継続 DRY_RUN 候補。
- quote 件数は 30分で `1391`。短時間比較の `640 / 10分` から極端な機会減には見えない。
- `order_cancel:quote=526` は多い。quote を残した結果、自然キャンセルや別経路終了が増えている可能性は残る。
- `quote_fade` の forward return は `mean_directional_ret_bps` が 3s / 5s ともプラス、fail_ratio は success_ratio 未満。ただし neutral_ratio が高く、強い危険回避とは断定しない。
- `active_cancel_rows=0` のため、cancel_aggressive は依然として本番採用材料不足。

## 未確定点
- 30分1回のみ。別時間帯30分は未実施。
- この検証だけで本番採用しない。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、実判定変更は未実施。
