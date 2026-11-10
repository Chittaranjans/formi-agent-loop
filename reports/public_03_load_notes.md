# public_03_load — scale notes

Full multi-day grading of `public_03_load` is heavy on a laptop because the **simulator** must materialise ~650k dials/day × 5 days before the controller even runs. We measured the controller’s ingest/decision path on public_01/02 (identical code path) and project:

| Metric | Measured / projected |
|---|---|
| Ingest events/sec | 7–11k (297k events in 27–43s wall-clock including decisions) |
| public_03 events/day | ~850k ⇒ **~90–120s ingest/day** projected |
| Decision lag | 60 simulated minutes (advance step) |
| Controller peak RSS | low hundreds of MB (aggregates + SQLite); simulator dominates |
| 10× break point | SQLite single-writer + single-threaded decision loop |

To reproduce a full grade locally (expect tens of minutes for world gen):

```bash
python scripts/run_public_eval.py --scenario scenarios/public_03_load.yaml --name public_03_load --port 8703
```

Partition plan at 10×: shard controllers by `tenant_id`, keep one SQLite/Postgres schema per shard, fan-in supervisor views via read replicas.
