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
