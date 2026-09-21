# Paired Wilcoxon signed-rank tests (Demsar 2006)

| Comparison                             | Baseline   |   n pairs |   p (two-sided) |   pairs where HAUSP-UB is lower |
|:---------------------------------------|:-----------|----------:|----------------:|--------------------------------:|
| Exp1 runtime (5 batches)               | EHAUSM-R   |         8 |     0.0078125   |                               8 |
| Exp1 runtime (5 batches)               | EHAUSM-I   |         8 |     0.0078125   |                               8 |
| Exp1 runtime (5 batches)               | Pre-HAUSPM |         8 |     0.0078125   |                               8 |
| Exp3 update runtime (Batch 1)          | EHAUSM-R   |        28 |     7.45058e-09 |                              28 |
| Exp3 update runtime (Batch 1)          | EHAUSM-I   |        28 |     7.45058e-09 |                              28 |
| Exp3 update runtime (Batch 1)          | Pre-HAUSPM |        28 |     7.45058e-09 |                              28 |
| Exp4 peak live heap                    | EHAUSM-R   |         8 |     0.0078125   |                               0 |
| Exp4 peak live heap                    | EHAUSM-I   |         8 |     0.0546875   |                               7 |
| Exp4 peak live heap                    | Pre-HAUSPM |         8 |     0.195312    |                               2 |
| Exp7 total runtime (completed-by-both) | EHAUSM-I   |        20 |     1.90735e-06 |                              20 |
| Exp7 total runtime (completed-by-both) | Pre-HAUSPM |        20 |     1.90735e-06 |                              20 |
