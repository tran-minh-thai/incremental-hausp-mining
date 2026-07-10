#!/usr/bin/env bash
# Resumable orchestrator for the long experiments (exp7, plus exp1/3/4 top-up).
# Enforces the uniform evaluation protocol reported in the paper:
#   - every algorithm attempts the identical K schedule {10,20,50,100} on
#     every dataset (no per-algorithm or per-dataset caps);
#   - every batch runs under the same 90-minute limit (ALGO_TIMEOUT_MIN); a
#     batch exceeding it is recorded as OT at that batch index, an
#     out-of-memory batch as OOM, completed batches are kept, and the next
#     algorithm starts;
#   - all verdicts stand at the uniform 24 GB heap.
#
# The run is fully resumable: completed work is skipped via the results CSVs,
# and partial groups left by a killed shell are trimmed on the next start
# (groups holding an OT/OOM record are never touched). A safety-net timeout
# of 48 h per experiment applies; override TIMEOUT_SECS for shorter sessions
# (e.g. TIMEOUT_SECS=28800 for an 8-hour chunk).
#
# Usage:
#   nohup ./scripts/run_resume.sh > run.out 2>&1 &
#   disown

set -u

cd "$(dirname "$0")/.."
mkdir -p logs

TIMEOUT_SECS="${TIMEOUT_SECS:-172800}"
POLL_SECS=5
export ALGO_TIMEOUT_MIN="${ALGO_TIMEOUT_MIN:-90}"

EXPERIMENTS=(
    "1|--repeats 3 --resume"
    "4|--repeats 3 --resume"
    "3|--repeats 3 --resume"
    "7|--repeats 3 --resume"
)

log() { echo "[resume-run] $*" >> logs/master.log; }

# Remove ONLY groups interrupted by the shell safety-net mid-execution — i.e.
# a (dataset,algorithm,K,trial) group that has fewer than K batch rows AND no
# OT/OOM/ERROR record of its own. Such a group has no recorded stopping reason:
# its trie state (held in memory) was lost to a SIGKILL, and because exp7 groups
# are atomic it must be re-run from batch 0; the leftover SUCCESS rows are kept
# out only to avoid duplicate rows on that re-run — they are regenerated.
#
# A group that ended in OT/OOM/ERROR is NEVER touched: the algorithm itself
# stopped it, its SUCCESS batches + the OT/OOM batch + the SKIPPED remainder are
# a complete, valid record and fill all K slots. The exclusion is now explicit
# (hasFail) rather than implicit via slot count, and every trimmed group is
# named in the log so the operation is fully auditable.
trim_partial_exp7() {
    local f
    f=$(ls results/exp7/*.csv 2>/dev/null | head -n 1)
    [ -z "$f" ] && return 0
    cp "$f" "$f.pre_trim.bak"
    awk -F, -v LOG="logs/master.log" '
        NR==1 {header=$0; for(i=1;i<=NF;i++) col[$i]=i; next}
        {
            dr=$col["DeltaRatio"]+0; if (dr<=0 || dr>1) { rows[NR]=$0; keepAlways[NR]=1; next }
            K=int(1/dr+0.5)
            grp=$col["Dataset"]"|"$col["Algorithm"]"|"K"|"$col["RunIndex"]
            b=grp"|"$col["BatchID"]
            rows[NR]=$0; grpof[NR]=grp
            if (!(b in seen)) {seen[b]=1; cnt[grp]++}
            kof[grp]=K
            st=$col["Status"]
            if (st=="OT" || st=="OOM" || st=="ERROR") hasFail[grp]=1
        }
        END {
            print header
            for (r=2; r<=NR; r++) {
                if (r in keepAlways) { print rows[r]; continue }
                g=grpof[r]
                # keep if group is complete OR ended in a recorded failure
                if (cnt[g]==kof[g] || (g in hasFail)) print rows[r]
                else dropped[g]=1
            }
            for (g in dropped)
                print "[resume-run] pre-flight: trimming incomplete shell-killed group " g " (" cnt[g] "/" kof[g] " batches, no OT/OOM record) — will re-run" >> LOG
        }' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    local removed
    removed=$(( $(wc -l < "$f.pre_trim.bak") - $(wc -l < "$f") ))
    log "pre-flight: trimmed $removed row(s) from $(basename "$f") (shell-killed partials only; OT/OOM groups preserved)"
}

CURRENT_PID=""
cleanup() {
    if [ -n "$CURRENT_PID" ] && kill -0 "$CURRENT_PID" 2>/dev/null; then
        log "orchestrator exiting; killing child pid=$CURRENT_PID"
        kill -TERM "$CURRENT_PID" 2>/dev/null || true
        sleep 3
        kill -KILL "$CURRENT_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

run_one() {
    local exp=$1; shift
    local out="logs/exp${exp}.txt"

    log "exp${exp} start: $(date) (args: $*, per-batch=${ALGO_TIMEOUT_MIN}min)"

    ./scripts/run.sh "$exp" "$@" >"$out" 2>&1 &
    CURRENT_PID=$!

    local elapsed=0
    while kill -0 "$CURRENT_PID" 2>/dev/null; do
        if [ "$elapsed" -ge "$TIMEOUT_SECS" ]; then
            log "exp${exp} SAFETY-NET HIT after ${TIMEOUT_SECS}s; killing pid=$CURRENT_PID"
            kill -TERM "$CURRENT_PID" 2>/dev/null || true
            sleep 5
            kill -KILL "$CURRENT_PID" 2>/dev/null || true
            wait "$CURRENT_PID" 2>/dev/null || true
            CURRENT_PID=""
            return 124
        fi
        sleep "$POLL_SECS"
        elapsed=$((elapsed + POLL_SECS))
    done

    wait "$CURRENT_PID" 2>/dev/null
    local rc=$?
    CURRENT_PID=""
    log "exp${exp} done: $(date) rc=$rc"
    return $rc
}

log "================================================"
log "resume-run start: $(date)"
log "  scope: fair-protocol exp7 completion (identical K schedule, 90-min per-batch limit)"
log "  per-batch limit (Java)   : ${ALGO_TIMEOUT_MIN} min"
log "  shell safety-net         : ${TIMEOUT_SECS} s"
log "================================================"

trim_partial_exp7

for entry in "${EXPERIMENTS[@]}"; do
    exp="${entry%%|*}"
    args="${entry#*|}"
    # shellcheck disable=SC2086
    run_one "$exp" $args || true
done

log "resume-run finished: $(date)"
