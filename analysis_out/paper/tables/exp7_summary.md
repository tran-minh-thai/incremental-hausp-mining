# Experiment 7 — total runtime vs batch count K, mean ± std

| Dataset     | Algorithm   |   K | Runtime (ms)           |
|:------------|:------------|----:|:-----------------------|
| BIBLE       | EHAUSM-I    |  10 | 134261.3 ± 1206.5      |
| BIBLE       | EHAUSM-I    |  20 | 242349.0 ± 5706.5      |
| BIBLE       | EHAUSM-I    |  50 | 521127.7 ± 13717.9     |
| BIBLE       | EHAUSM-I    | 100 | 972575.3 ± 24567.6     |
| BIBLE       | Pre-HAUSPM  |  10 | 177085.3 ± 253.0       |
| BIBLE       | Pre-HAUSPM  |  20 | 345829.3 ± 68.0        |
| BIBLE       | Pre-HAUSPM  |  50 | 839955.0 ± 2871.3      |
| BIBLE       | Pre-HAUSPM  | 100 | 3489812.0 ± 5183.8     |
| BIBLE       | HAUSP-UB    |  10 | 48502.7 ± 504.7        |
| BIBLE       | HAUSP-UB    |  20 | 94934.3 ± 694.6        |
| BIBLE       | HAUSP-UB    |  50 | 230107.3 ± 462.1       |
| BIBLE       | HAUSP-UB    | 100 | 516690.3 ± 3867.8      |
| BMS1_SPMF   | EHAUSM-I    |  10 | 119513.7 ± 3178.4      |
| BMS1_SPMF   | EHAUSM-I    |  20 | 254989.3 ± 1755.1      |
| BMS1_SPMF   | EHAUSM-I    |  50 | 463555.3 ± 2036.2      |
| BMS1_SPMF   | Pre-HAUSPM  |  10 | 1220424.7 ± 617.8      |
| BMS1_SPMF   | Pre-HAUSPM  |  20 | 2696988.7 ± 38502.9    |
| BMS1_SPMF   | Pre-HAUSPM  |  50 | 7342957.7 ± 2784.1     |
| BMS1_SPMF   | HAUSP-UB    |  10 | 58404.3 ± 898.8        |
| BMS1_SPMF   | HAUSP-UB    |  20 | 119217.3 ± 1411.2      |
| BMS1_SPMF   | HAUSP-UB    |  50 | 336417.0 ± 4272.0      |
| BMS1_SPMF   | HAUSP-UB    | 100 | 2744878.0 ± 25926.8    |
| FIFA        | EHAUSM-I    |  10 | 1410450.3 ± 6976.5     |
| FIFA        | EHAUSM-I    |  20 | 2507226.7 ± 67723.8    |
| FIFA        | EHAUSM-I    |  50 | 6734562.3 ± 638465.7   |
| FIFA        | EHAUSM-I    | 100 | 12370909.0 ± 652547.3  |
| FIFA        | Pre-HAUSPM  |  10 | 2158327.7 ± 1135.1     |
| FIFA        | Pre-HAUSPM  |  20 | 4173323.7 ± 2698.9     |
| FIFA        | Pre-HAUSPM  |  50 | 12938849.3 ± 1923822.9 |
| FIFA        | Pre-HAUSPM  | 100 | 21685623.0             |
| FIFA        | HAUSP-UB    |  10 | 511479.3 ± 12784.3     |
| FIFA        | HAUSP-UB    |  20 | 971044.7 ± 29041.7     |
| FIFA        | HAUSP-UB    |  50 | 2565362.3 ± 28634.2    |
| FIFA        | HAUSP-UB    | 100 | 5385923.0 ± 9507.1     |
| KOSARAK     | EHAUSM-I    |  10 | 278807.3 ± 915.5       |
| KOSARAK     | EHAUSM-I    |  20 | 532003.7 ± 11132.0     |
| KOSARAK     | EHAUSM-I    |  50 | 1505793.7 ± 4131.6     |
| KOSARAK     | EHAUSM-I    | 100 | 3004169.0 ± 38814.4    |
| KOSARAK     | Pre-HAUSPM  |  10 | 698487.0 ± 5138.2      |
| KOSARAK     | Pre-HAUSPM  |  20 | 1339058.3 ± 5879.8     |
| KOSARAK     | Pre-HAUSPM  |  50 | 3234739.3 ± 5257.2     |
| KOSARAK     | Pre-HAUSPM  | 100 | 6343955.0              |
| KOSARAK     | HAUSP-UB    |  10 | 128459.7 ± 2938.3      |
| KOSARAK     | HAUSP-UB    |  20 | 237992.3 ± 6199.0      |
| KOSARAK     | HAUSP-UB    |  50 | 697309.0 ± 8356.9      |
| KOSARAK     | HAUSP-UB    | 100 | 1379297.0 ± 6642.2     |
| LEVIATHAN   | EHAUSM-I    |  10 | 27993.3 ± 114.9        |
| LEVIATHAN   | EHAUSM-I    |  20 | 50715.7 ± 289.0        |
| LEVIATHAN   | EHAUSM-I    |  50 | 113597.7 ± 480.5       |
| LEVIATHAN   | Pre-HAUSPM  |  10 | 37669.7 ± 174.3        |
| LEVIATHAN   | Pre-HAUSPM  |  20 | 72035.3 ± 37.2         |
| LEVIATHAN   | Pre-HAUSPM  |  50 | 174735.0 ± 810.8       |
| LEVIATHAN   | HAUSP-UB    |  10 | 9002.7 ± 249.5         |
| LEVIATHAN   | HAUSP-UB    |  20 | 16676.7 ± 417.9        |
| LEVIATHAN   | HAUSP-UB    |  50 | 41798.0 ± 809.6        |
| LEVIATHAN   | HAUSP-UB    | 100 | 218432.7 ± 3008.3      |
| SIGN        | EHAUSM-I    |  10 | 197700.3 ± 308.8       |
| SIGN        | Pre-HAUSPM  |  10 | 327716.0 ± 394.8       |
| SIGN        | HAUSP-UB    |  10 | 76326.3 ± 996.6        |
| C8T1S5I8N5K | EHAUSM-I    |  10 | 11534.3 ± 582.1        |
| C8T1S5I8N5K | Pre-HAUSPM  |  10 | 32059.3 ± 882.7        |
| C8T1S5I8N5K | HAUSP-UB    |  10 | 9761.7 ± 947.2         |
| C8T1S5I8N5K | HAUSP-UB    |  20 | 123848.0 ± 10886.0     |
