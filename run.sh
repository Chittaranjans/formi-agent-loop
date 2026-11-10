#!/usr/bin/env bash
# Contract: ./run.sh <SIM_URL> starts the controller and runs until the scenario finishes.
# No manual steps. Tested for 4-vCPU / 8 GB Linux evaluation boxes.
# Dependencies: Python 3.11+, pip install -r requirements.txt (stdlib sqlite3 — no new stateful infra).
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Isolate durable state per run so restarts within one scenario resume correctly,
# while separate evaluation runs do not bleed into each other.
DATA_DIR="${CONTROLLER_DATA:-$ROOT/controller_data/run_$(echo -n "$SIM_URL" | cksum | awk '{print $1}')}"
mkdir -p "$DATA_DIR"
export CONTROLLER_DATA="$DATA_DIR"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "controller starting against $SIM_URL (data=$DATA_DIR)" >&2
exec python -m controller run --url "$SIM_URL" --data-dir "$DATA_DIR"
