# PROBE: static mining of the first batch of REES46 cosmetics (development machine, seconds indicative only)

The registered thresholds were chosen on the whole data (results-probe/select-xi/20261120-201448: 435 s at
xi = 7.8125e-05). Batch 0 of each schedule, mined alone at the same ratios, is far more costly:

| xi | L5 batch 0 (61,432 lines) | W8 batch 0 (73,837 lines) | whole data |
|---|---|---|---|
| 0.00015625 | 3.1 s, 1,119 patterns | 1.7 s, 1,097 | 4.6 s, 1,082 |
| 0.00011048543456039807 | 521.1 s, 1,855 | 40.7 s, 1,797 | 9.9 s, 1,764 |
| 7.8125e-05 | time-out at 1800 s | time-out at 1800 s | 435.2 s, 2,878 |

Ran beside another JVM (the real-data exactness check). Command and provenance in run_meta.json.
