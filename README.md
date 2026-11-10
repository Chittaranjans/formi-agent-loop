# Outcome-First Agent Loop — Submission

Formi senior SDE assignment. This repository contains the simulator (unchanged) and a production-minded **controller** that learns which welcome / voice / caller-ID / schedule settings work, proposes changes through governance, and keeps an auditable trail.

## Quick start

```bash
pip install -r requirements.txt

# baseline (no-op controller)
python -m sim baseline --scenario scenarios/public_01_dev.yaml --run-dir runs/base_public_01_dev

# terminal A — simulator
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/mine_public_01_dev --force

# terminal B — controller (Linux/macOS)
./run.sh http://127.0.0.1:8700
# Windows PowerShell
# .\run.ps1 http://127.0.0.1:8700

# grade
python -m sim grade --run runs/mine_public_01_dev --baseline runs/base_public_01_dev --out reports/public_01_dev.md
```

Or one-shot local eval:

```bash
python scripts/run_public_eval.py --scenario scenarios/public_01_dev.yaml --port 8700
python scripts/run_public_eval.py --scenario scenarios/public_02_incidents.yaml --port 8702
```

## Audit query (review demo)

```bash
python -m controller why nw_renewal --data-dir controller_data/mine_public_01_dev --limit 20
python -m controller view ks_cancel --data-dir controller_data/mine_public_02_incidents
```

## Crash recovery demo

```bash
bash scripts/crash_recovery_demo.sh scenarios/public_01_dev.yaml
```

## Layout

| Path | Role |
|---|---|
| `controller/` | The loop (ingest, detect, experiment, govern, apply, audit) |
| `run.sh` / `run.ps1` | Evaluation entrypoint |
| `SUBMISSION.md` | Design, scope, stakeholders, results |
| `sim/` | Simulator — **do not modify** for evaluation |
| `reports/` | Grade outputs and crash-recovery evidence |

## Design in one paragraph

Events land in SQLite with idempotent chunk offsets. One shared cohort object per workflow drives detection, experiments and the supervisor view. Welcome/voice are explored with holdout, winners are chosen by Wilson lower-bound tests (never by the LLM), caller-ID and schedule use policy auto-apply when allowed, prompt incidents go LLM→validate→approve, and every decision is queryable after a kill/restart.
