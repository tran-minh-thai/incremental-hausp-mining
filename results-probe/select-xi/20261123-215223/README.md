# PROBE: amended threshold registration of REES46 cosmetics (supersedes 20261120-201448)

Rule (author, 2026-11-23): a level of the sqrt2 grid is accepted when static mining finishes within 1800 s (6 GB heap,
development machine) with at least 10 patterns on the whole data and on batch 0 of the L5 and W8 schedules; the
dataset uses the three smallest accepted levels. The old rule tested the whole data only; batch 0 is a smaller
database whose threshold is a ratio of its own total utility, and at 7.8125e-05 it timed out on both schedules
(results-probe/first-batch-static/20261123-201951) although the whole data took 435 s.

Registered: 0.00011048543456039807 (1,764 patterns on the whole data), 0.00015625 (1,082), 0.00022097086912079613 (646).
levels.json lists every grid level; levels above 0.00022097086912079613 show accepted=false only because their
batch 0 was not probed (not needed: the dataset takes the three smallest accepted levels).

The other end-to-end datasets keep their registrations: batch 0 of both schedules finished within the limit at every
registered level (results-probe/first-batch-static/*-TAFENG, *-REES46_MULTI, *-HM).
