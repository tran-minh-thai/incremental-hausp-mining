# Memory attribution of HAUSP-UB on FIFA, K=100

Source: `results-2026-09/exp7_memprobe/exp7/experiment7_long_batch.csv` run_id=20260905-1402 (trial 0).
Values sampled at the moment each batch's heap peak was recorded; bytes of array payload.

| Batch | MemPeak (MB) | Pool (MB) | Flat DB (MB) | EUCS (MB) | Root AU-DUL (MB) | Sum (MB) | Explained |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 284 | 0 | 0 | 148 | 0 | 149 | 52% |
| 5 | 299 | 2 | 1 | 148 | 3 | 154 | 52% |
| 10 | 312 | 5 | 3 | 148 | 6 | 162 | 52% |
| 15 | 303 | 6 | 4 | 148 | 10 | 167 | 55% |
| 20 | 315 | 10 | 5 | 148 | 13 | 176 | 56% |
| 25 | 317 | 11 | 6 | 148 | 15 | 180 | 57% |
| 30 | 331 | 12 | 7 | 148 | 20 | 187 | 56% |
| 35 | 345 | 18 | 8 | 148 | 24 | 198 | 57% |
| 40 | 360 | 21 | 9 | 148 | 29 | 207 | 57% |
| 45 | 370 | 21 | 11 | 148 | 33 | 212 | 57% |
| 50 | 366 | 21 | 12 | 148 | 36 | 217 | 59% |
| 55 | 373 | 21 | 13 | 148 | 41 | 222 | 60% |
| 60 | 384 | 24 | 14 | 148 | 44 | 230 | 60% |
| 65 | 392 | 29 | 15 | 148 | 47 | 239 | 61% |
| 70 | 417 | 39 | 16 | 148 | 49 | 252 | 60% |
| 75 | 830 | 41 | 17 | 380 | 54 | 492 | 59% |
| 76 | 831 | 41 | 18 | 380 | 54 | 492 | 59% |
| 77 | 832 | 41 | 18 | 380 | 54 | 493 | 59% |
| 80 | 650 | 41 | 19 | 380 | 57 | 497 | 76% |
| 85 | 657 | 42 | 20 | 380 | 59 | 500 | 76% |
| 90 | 656 | 41 | 21 | 380 | 61 | 503 | 77% |
| 95 | 665 | 41 | 22 | 380 | 65 | 507 | 76% |
| 98 | 670 | 41 | 22 | 380 | 67 | 510 | 76% |
| 99 | 672 | 41 | 23 | 380 | 68 | 511 | 76% |

Batches with the five largest peaks: mean explained share = 66% (threshold 50%); largest component there: EucsBytes (380 MB of 767 MB).

CONCLUSION: located -- the tracked structures account for the peak; the dominant term is EucsBytes.
