#!/usr/bin/env bash
# Supplementary measurement campaign (2026-09) on the measurement machine.
#
# Every step is re-entrant (--resume): a killed session is restarted with the
# same command and continues where it stopped. Results go to results-2026-09/
# (new CSV schema, never appended to results/). Steps are ordered short to
# long so that a broken pipeline is noticed within minutes, not hours.
#
# Protocol before starting (code reaches this machine through git, not file sync):
#   git pull --ff-only          # the commit hash written into every CSV must describe the code that runs
#   mvn -q package -DskipTests  # or let this script build when build/*.jar is missing
#   ./scripts/run_2026-09.sh    # optionally: STEPS="r2 r3" ./scripts/run_2026-09.sh
#
# Machine: the one declared in the paper (Apple M5, 32 GB, heap 24 GB). Timing
# and memory values are only meaningful from this machine, idle otherwise.
#
# Steps and rough durations (extrapolated from the legacy runs, not measured):
#   r1c Exp 1,2,3,8 counts-only re-run of the HAUSP-UB arms (1 trial; counts are deterministic)   ~2-3 h
#       -> results-2026-09/counts/, read by the count tables; the legacy files cannot give
#          "lists assembled" for these arms (column-sum formula refuted, see EXPERIMENT_CHANGELOG)
#   r2  Exp 1,2,4  arm HAUSP-UB-L1 (true Layer-1-only), 3 trials, 90-min limit      ~2-6 h (OT possible; OT is the result)
#   r3  Exp 1,3 on SYN and LEVIATHAN with adaptive repeats (10/15 trials under 10 s / 1 s)  < 1 h
#       Exp 7 K=10 on SYN and LEVIATHAN, adaptive repeats                             < 1 h
#   r4  Exp 10: Pre-HAUSPM, mu in {0.05,0.10,0.20,0.40}, 7 datasets, 3 trials       ~6-8 h
#   r5  Exp 11: warm-start schedule, K=100, SIGN and SYN, 3 arms, 3 trials           a few hours (OT@0 possible; that is the result)
#   r6  Exp 7 memory probe: FIFA K=100, HAUSP-UB only, 1 trial                       ~90 min
#   mem Live-heap memory runs (--mem-mode live: forced full GC every second, one JVM per arm,
#       results under results-2026-09/mem/, runtimes there are NOT timing data):
#       Exp 4 all arms 3 trials; Exp 3, Exp 7 FIFA K=100 HAUSP-UB, Exp 11 K=100 SIGN/SYN 1 trial   ~16 h
# After the campaign: push results-2026-09/ (git add results-2026-09 && git commit && git push),
# then run the analysis (see README, "Reproducing the paper's analysis").
set -u
cd "$(dirname "$0")/.."
mkdir -p logs

HEAP="${HEAP:-24g}"
TIMEOUT_MIN="${ALGO_TIMEOUT_MIN:-90}"
RESULTS="${RESULTS_DIR:-results-2026-09}"
STEPS="${STEPS:-r1c r2 r3 r4 r5 r6 mem}"
LOG="logs/run-2026-09.log"

if [ "${ALLOW_DIRTY:-0}" != "1" ] && [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    echo "[run-2026-09] tracked files have uncommitted changes; commit or stash them first" >&2
    echo "[run-2026-09] (the commit hash stamped into the CSVs must describe the code that runs; ALLOW_DIRTY=1 overrides)" >&2
    git status --short --untracked-files=no >&2
    exit 1
fi

JAR="$(ls build/incremental-hausp-mining-*.jar 2>/dev/null | grep -v '/original-' | head -n 1 || true)"
if [ -z "$JAR" ]; then
    echo "[run-2026-09] building the jar with Maven"
    mvn -q package -DskipTests
    JAR="$(ls build/incremental-hausp-mining-*.jar 2>/dev/null | grep -v '/original-' | head -n 1 || true)"
    [ -n "$JAR" ] || { echo "[run-2026-09] build failed" >&2; exit 1; }
fi

run() {
    echo "" | tee -a "$LOG"
    echo "[run-2026-09] $(date '+%F %T') java -Xmx$HEAP -jar $JAR $* --timeout $TIMEOUT_MIN --resume" | tee -a "$LOG"
    java -Xmx"$HEAP" -XX:+UseG1GC -jar "$JAR" "$@" --timeout "$TIMEOUT_MIN" --resume 2>&1 | tee -a "$LOG"
    echo "[run-2026-09] $(date '+%F %T') step finished (exit ${PIPESTATUS[0]})" | tee -a "$LOG"
}

echo "[run-2026-09] commit $(git rev-parse --short HEAD), host $(hostname), heap $HEAP, results -> $RESULTS, steps: $STEPS" | tee -a "$LOG"

for step in $STEPS; do
    case "$step" in
        r1c) run --exp 1,3,8 --algo HAUSP-UB --repeats 1 --results-dir "$RESULTS/counts"
             run --exp 2 --algo HAUSP-UB-L1L3,HAUSP-UB*,HAUSP-UB --repeats 1 --results-dir "$RESULTS/counts" ;;
        r2) run --exp 1,2,4 --algo HAUSP-UB-L1 --repeats 3 --results-dir "$RESULTS" ;;
        r3) run --exp 1,3 --dataset syn_c8t1s5i8n5k,leviathan --repeats 3 --repeats-min-seconds 10 --results-dir "$RESULTS"
            run --exp 7 --dataset syn_c8t1s5i8n5k,leviathan --k 10 --repeats 3 --repeats-min-seconds 10 --results-dir "$RESULTS" ;;
        r4) run --exp 10 --repeats 3 --results-dir "$RESULTS" ;;
        r5) run --exp 11 --dataset sign,syn_c8t1s5i8n5k --k 100 --repeats 3 --results-dir "$RESULTS" ;;
        r6) run --exp 7 --dataset fifa --k 100 --algo HAUSP-UB --repeats 1 --results-dir "$RESULTS/exp7_memprobe" ;;
        mem) # one JVM per arm so that no arm inherits the heap history of another
             for arm in EHAUSM-R EHAUSM-I Pre-HAUSPM HAUSP-UB-L1 HAUSP-UB; do
                 run --exp 4 --algo "$arm" --repeats 3 --mem-mode live --results-dir "$RESULTS/mem"
             done
             for arm in EHAUSM-R EHAUSM-I Pre-HAUSPM HAUSP-UB; do
                 run --exp 3 --algo "$arm" --repeats 1 --mem-mode live --results-dir "$RESULTS/mem"
             done
             run --exp 7 --dataset fifa --k 100 --algo HAUSP-UB --repeats 1 --mem-mode live --results-dir "$RESULTS/mem"
             for arm in HAUSP-UB EHAUSM-I Pre-HAUSPM; do
                 run --exp 11 --dataset sign,syn_c8t1s5i8n5k --k 100 --algo "$arm" --repeats 1 --mem-mode live --results-dir "$RESULTS/mem"
             done ;;
        *)  echo "[run-2026-09] unknown step '$step' (r1c r2 r3 r4 r5 r6 mem)" >&2; exit 1 ;;
    esac
done
echo "[run-2026-09] $(date '+%F %T') campaign finished; commit and push $RESULTS/" | tee -a "$LOG"
