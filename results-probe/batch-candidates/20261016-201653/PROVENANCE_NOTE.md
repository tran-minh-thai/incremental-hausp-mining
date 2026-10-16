# Provenance note for this probe run

run_meta.json records commit 066e50b. Every transition ran in its own JVM, which loaded
build/incremental-hausp-mining-1.0.0.jar at its start, and that file was rebuilt several times
from 21:10 on (later commits and short-lived builds that tested other code paths). The transitions
that finished after 21:10 therefore ran from jars other than the one 066e50b builds.

Check: the jar of 066e50b was rebuilt in a separate worktree (sha256 prefix d3067cbd1dff93ae) and
17 of the affected transitions were run again with it: LEVIATHAN K=100 b=50, 75, 99; SIGN K=5
b=1-4; TAFENG K=5 b=1, 3, 4 and K=20 b=1, 5, 10; C8T1S5I8N5K K=5 b=1-4. All 17 reproduce
candidates, cand_lists, cand_recursed, items_allowed and restricted_items exactly. The transitions
that timed out were not re-run.

Verdict of the registered decision rule (analysis/probe_batch_candidates.py --evaluate): NOT WORTH
IT, not worth on 4 datasets (C8T1S5I8N5K, FIFA, LEVIATHAN, SIGN), open on 3, worth on none; TAFENG
has no batch where the full search completed at K = 20 or 100.
