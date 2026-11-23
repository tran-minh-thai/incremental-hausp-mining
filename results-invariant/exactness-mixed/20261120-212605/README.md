# Exactness of HAUSP-UB on real data under the mixed update model (run 20261120-212605)

INVARIANT: pattern sets compared batch by batch with a fresh EHAUSM_Remining on the accumulated database; no timing.
Commit 3eb6a4b, clean tree, JAR sha256 in run_meta.json. Datasets: Ta-Feng, REES46 multi-category, REES46 cosmetics;
schedules L5 and W8; three registered thresholds per dataset; arms HAUSP-UB[prefix] and HAUSP-UB.

The verdict in report.json is FAIL only because four cases were stopped by hand, not because a result differed:

| outcome | cases | batches | patterns compared | batches differing |
|---|---|---|---|---|
| compared | 32 of 36 | 240 | 14,696,996 | 0 |
| stopped, not checked | 4 of 36 | 0 | 0 | - |

The four stopped cases are REES46 cosmetics at xi = 7.8125e-05, schedules L5 and W8, both arms (rc 143, SIGTERM).
On L5 the HAUSP-UB[prefix] arm ran 26.5 hours on the development machine without finishing batch 0; the other three
were stopped at start. The generated databases (6 cases), the eight faults (each as expected) and the basket-split
check (119,578 baskets, 0 split, injected split detected) all passed.

Validity on later commits: between 3eb6a4b and abc1605, HAUSP_UB.java gained only phase timers (search time, GC time
at every batch) and EHAUSM_Remining.java gained phase timers and a cache reset between calls. The probe creates a new
EHAUSM_Remining for every batch, so the stale cache fixed on 2026-11-22 could not act here.
