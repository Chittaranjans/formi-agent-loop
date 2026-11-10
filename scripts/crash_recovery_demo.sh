#!/usr/bin/env bash
# Demonstrates crash safety: kill the controller mid-run, restart, finish correctly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SCENARIO="${1:-scenarios/public_01_dev.yaml}"
RUN_DIR="${2:-runs/crash_demo}"
PORT="${PORT:-8711}"
URL="http://127.0.0.1:$PORT"
DATA_DIR="$ROOT/controller_data/crash_demo"
rm -rf "$RUN_DIR" "$DATA_DIR"
mkdir -p "$DATA_DIR" reports

python -m sim serve --scenario "$SCENARIO" --run-dir "$RUN_DIR" --port "$PORT" &
SIM_PID=$!
cleanup() { kill $SIM_PID 2>/dev/null || true; }
trap cleanup EXIT
sleep 2

export CONTROLLER_DATA="$DATA_DIR"
export PYTHONPATH="$ROOT"
python -m controller run --url "$URL" --data-dir "$DATA_DIR" &
CTRL_PID=$!
sleep 25
echo "Killing controller pid=$CTRL_PID mid-run..." >&2
kill "$CTRL_PID" || true
wait "$CTRL_PID" 2>/dev/null || true
echo "Restarting controller..." >&2
python -m controller run --url "$URL" --data-dir "$DATA_DIR"
echo "Crash-recovery run finished." >&2
python -m controller why nw_renewal --data-dir "$DATA_DIR" --limit 5 > reports/crash_recovery_audit_sample.json || true
echo "Sample audit written to reports/crash_recovery_audit_sample.json" >&2
