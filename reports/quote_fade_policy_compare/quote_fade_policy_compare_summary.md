# quote_fade_policy compare

| policy | order_new quote | median lifetime sec | mean lifetime sec | quote_fade end | tfi_fade end | cancel_aggressive end | order_cancel:quote | quote_fade_suppressed | quote_fade risk | final_block_reason |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| current | 990 | 0.516929 | 0.776587 | 753 | 0 | 0 | 235 | 0 | 2034 | {'none': 3411, 'quote_fade': 610} |
| disabled | 172 | 0.489123 | 3.562227 | 0 | 0 | 0 | 171 | 907 | 0 | {'none': 4718} |
| threshold_5bps | 961 | 0.512540 | 0.838652 | 829 | 0 | 0 | 130 | 261 | 2124 | {'none': 3421, 'quote_fade': 628} |
| threshold_8bps | 640 | 0.784076 | 1.426112 | 544 | 0 | 0 | 94 | 538 | 1320 | {'none': 3865, 'quote_fade': 374} |
| threshold_10bps | 584 | 0.998460 | 1.609195 | 459 | 0 | 0 | 124 | 623 | 1132 | {'none': 4080, 'quote_fade': 319} |

## Observations

- disabled: median_lifetime_ratio_vs_current=0.946, quote_fade_end=0, order_cancel_quote=171, quote_fade_suppressed=907
- threshold_5bps: median_lifetime_ratio_vs_current=0.992, quote_fade_end=829, order_cancel_quote=130, quote_fade_suppressed=261
- threshold_8bps: median_lifetime_ratio_vs_current=1.517, quote_fade_end=544, order_cancel_quote=94, quote_fade_suppressed=538
- threshold_10bps: median_lifetime_ratio_vs_current=1.932, quote_fade_end=459, order_cancel_quote=124, quote_fade_suppressed=623
