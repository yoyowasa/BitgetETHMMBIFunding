# tfi_fade_policy compare

| policy | order_new quote | median lifetime sec | mean lifetime sec | tfi_fade end | quote_fade end | cancel_aggressive end | order_cancel:quote | tfi_fade_suppressed | quote_fade risk | final_block_reason |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| current | 1080 | 0.256299 | 0.286497 | 558 | 492 | 0 | 30 | 0 | 2872 | {'none': 2760, 'quote_fade': 930} |
| disabled | 1040 | 0.498965 | 0.702222 | 0 | 942 | 0 | 98 | 1237 | 2628 | {'none': 2982, 'quote_fade': 827} |
| threshold_0p7 | 1046 | 0.248277 | 0.311442 | 548 | 457 | 0 | 41 | 86 | 2710 | {'quote_fade': 868, 'none': 2988} |
| threshold_0p8 | 1055 | 0.254590 | 0.342078 | 542 | 460 | 0 | 53 | 167 | 2706 | {'none': 2921, 'quote_fade': 868} |

## Observations

- disabled: median_lifetime_ratio_vs_current=1.947, tfi_fade_end=0, quote_fade_end=942, tfi_fade_suppressed=1237
- threshold_0p7: median_lifetime_ratio_vs_current=0.969, tfi_fade_end=548, quote_fade_end=457, tfi_fade_suppressed=86
- threshold_0p8: median_lifetime_ratio_vs_current=0.993, tfi_fade_end=542, quote_fade_end=460, tfi_fade_suppressed=167
