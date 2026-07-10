# Experiment 7 — largest completed K per algorithm and recorded failure modes

| Dataset     | Algorithm   |   max K completed | failures                    |
|:------------|:------------|------------------:|:----------------------------|
| BIBLE       | EHAUSM-I    |               100 | —                           |
| BIBLE       | Pre-HAUSPM  |               100 | —                           |
| BIBLE       | HAUSP-UB    |               100 | —                           |
| BMS1_SPMF   | EHAUSM-I    |                50 | OOM@K=100                   |
| BMS1_SPMF   | Pre-HAUSPM  |                50 | OT@K=100                    |
| BMS1_SPMF   | HAUSP-UB    |               100 | —                           |
| FIFA        | EHAUSM-I    |               100 | —                           |
| FIFA        | Pre-HAUSPM  |               100 | —                           |
| FIFA        | HAUSP-UB    |               100 | —                           |
| KOSARAK     | EHAUSM-I    |               100 | —                           |
| KOSARAK     | Pre-HAUSPM  |               100 | —                           |
| KOSARAK     | HAUSP-UB    |               100 | —                           |
| LEVIATHAN   | EHAUSM-I    |                50 | OOM@K=100                   |
| LEVIATHAN   | Pre-HAUSPM  |                50 | OOM@K=100                   |
| LEVIATHAN   | HAUSP-UB    |               100 | —                           |
| SIGN        | EHAUSM-I    |                10 | OOM@K=100/OOM@K=20/OOM@K=50 |
| SIGN        | Pre-HAUSPM  |                10 | OOM@K=100/OOM@K=50/OT@K=20  |
| SIGN        | HAUSP-UB    |                10 | OT@K=100/OT@K=20/OT@K=50    |
| C8T1S5I8N5K | EHAUSM-I    |                10 | OOM@K=100/OOM@K=20/OOM@K=50 |
| C8T1S5I8N5K | Pre-HAUSPM  |                10 | OOM@K=100/OOM@K=20/OOM@K=50 |
| C8T1S5I8N5K | HAUSP-UB    |                20 | OT@K=100/OT@K=50            |
