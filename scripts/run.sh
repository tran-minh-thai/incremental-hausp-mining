#!/usr/bin/env bash
# Launcher for macOS and Linux.
#
# Usage:
#   ./scripts/run.sh             # all eight experiments, three trials each
#   ./scripts/run.sh 1           # only Experiment 1
#   ./scripts/run.sh 1,3,5       # selected experiments
#   ./scripts/run.sh all         # explicit form of the default
#
# The JVM heap ceiling can be tuned through the HEAP environment variable
# (default 24g on this 32 GB machine, leaving ~8 GB for the OS):
#   HEAP=16g ./scripts/run.sh 4
# Only -Xmx (the ceiling) is set; -Xms is deliberately left unset so the JVM
# grows the heap lazily and does not reserve the full 24 GB when a run needs
# far less.
#
# Requirements: JDK >= 11. Maven is needed only if no prebuilt JAR
# exists under build/; an existing JAR is reused as-is.

set -e

# Environments that must not produce measurements set HAUSP_NO_MEASURE. The Java
# launcher enforces the same rule; this copy fails earlier and with the reason.
if [ -n "${HAUSP_NO_MEASURE:-}" ]; then
    echo "[$(basename "$0")] REFUSED: HAUSP_NO_MEASURE is set. Run this on the measurement machine." >&2
    exit 3
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

HEAP="${HEAP:-24g}"
ALGO_TIMEOUT_MIN="${ALGO_TIMEOUT_MIN:-90}"
EXP_ARG="${1:-all}"

echo "[run.sh] project root      : $(pwd)"
echo "[run.sh] heap               : $HEAP"
echo "[run.sh] per-batch timeout  : ${ALGO_TIMEOUT_MIN} min"
echo "[run.sh] experiments        : $EXP_ARG"

JAR="$(ls build/incremental-hausp-mining-*.jar 2>/dev/null | grep -v '/original-' | head -n 1 || true)"
if [ -n "$JAR" ]; then
    # A JAR older than the sources is the worst kind of stale: the run still writes a
    # provenance line naming the current commit with a clean tree, so the artifact claims to
    # come from code that never executed. It happened on 2026-09-18: a JAR built the previous
    # day was reused after a dataset was added, the dataset was silently absent from every
    # result file, and the header still read "git=<current> tree=clean".
    STALE="$(find src pom.xml -newer "$JAR" 2>/dev/null | head -n 5)"
    if [ -n "$STALE" ]; then
        echo "[run.sh] REFUSED: $JAR is older than the sources, so it is not the code in this" >&2
        echo "[run.sh] working tree. Newer than the JAR:" >&2
        echo "$STALE" | sed 's/^/[run.sh]   /' >&2
        echo "[run.sh] Rebuild with 'mvn -q package -DskipTests', or delete build/*.jar and" >&2
        echo "[run.sh] run this again. To run the old JAR on purpose: ALLOW_STALE_JAR=1" >&2
        [ "${ALLOW_STALE_JAR:-}" = "1" ] || exit 1
        echo "[run.sh] ALLOW_STALE_JAR=1 given; continuing with the older JAR" >&2
    fi
    echo "[run.sh] step 1/2: reuse existing JAR $JAR (skip mvn)"
else
    if ! command -v mvn >/dev/null 2>&1; then
        echo "[run.sh] No JAR under build/ and 'mvn' not on PATH. Install Maven or place a prebuilt JAR in build/." >&2
        exit 1
    fi
    echo "[run.sh] step 1/2: mvn -q package"
    mvn -q package -DskipTests
    JAR="$(ls build/incremental-hausp-mining-*.jar 2>/dev/null | grep -v '/original-' | head -n 1 || true)"
    if [ -z "$JAR" ]; then
        echo "[run.sh] No fat JAR found under build/. Maven build failed?" >&2
        exit 1
    fi
fi

shift || true
echo "[run.sh] step 2/2: java -Xmx$HEAP -jar $JAR --exp $EXP_ARG --timeout $ALGO_TIMEOUT_MIN $@"
exec java -Xmx"$HEAP" -XX:+UseG1GC -jar "$JAR" --exp "$EXP_ARG" --timeout "$ALGO_TIMEOUT_MIN" "$@"
