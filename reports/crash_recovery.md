# Crash recovery evidence

## Method
1. Start python -m sim serve for public_01_dev.
2. Start python -m controller run with a durable --data-dir.
3. After ~25s wall-clock (mid-scenario), kill the controller process (SIGTERM / taskkill).
4. Restart the same command with the **same** --data-dir.
5. Confirm the run reaches inished: true and processed_chunks resumes (no re-ingest of completed chunks).
6. Confirm pending approvals are reconciled via GET /approvals/{id} before new proposals.

## Result
On local Windows runs, restart resumed from the last committed chunk offset in SQLite WAL.
No duplicate proposal_id was submitted for an already-pending approval (pending table is upsert-by-workflow).
Script: scripts/crash_recovery_demo.sh (Git Bash) or re-run manually with the steps above.

## What state can be lost
In-memory only: _halluc_handled, _demoted_dids, per-day schedule counters.
These recompute from aggregates / events within one decision cycle. Durable: chunk offsets, stats, cohorts, experiment phase, pending approvals, audit log.
