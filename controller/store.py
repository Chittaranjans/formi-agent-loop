"""Durable SQLite store: crash-safe offsets, audit, experiments, pending approvals."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._pending_writes = 0
        self._autocommit_every = 500
        self._init()

    def close(self) -> None:
        with self._lock:
            if self._pending_writes:
                self._conn.commit()
                self._pending_writes = 0
            self._conn.close()

    def _touch(self, force: bool = False) -> None:
        """Caller must hold self._lock."""
        self._pending_writes += 1
        if force or self._pending_writes >= self._autocommit_every:
            self._conn.commit()
            self._pending_writes = 0

    def flush(self) -> None:
        with self._lock:
            if self._pending_writes:
                self._conn.commit()
                self._pending_writes = 0

    def _init(self) -> None:
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT NOT NULL,
              sim_ts TEXT,
              workflow_id TEXT,
              kind TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_wf ON audit(workflow_id, id);
            CREATE TABLE IF NOT EXISTS pending (
              workflow_id TEXT PRIMARY KEY,
              approval_id TEXT NOT NULL,
              proposal_id TEXT NOT NULL,
              created_sim_ts TEXT,
              purpose TEXT
            );
            CREATE TABLE IF NOT EXISTS experiments (
              workflow_id TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cohort_defs (
              workflow_id TEXT PRIMARY KEY,
              cohorts_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS call_stats (
              workflow_id TEXT NOT NULL,
              cohort_id TEXT NOT NULL,
              dim TEXT NOT NULL,
              value TEXT NOT NULL,
              dials INTEGER NOT NULL DEFAULT 0,
              connected INTEGER NOT NULL DEFAULT 0,
              engaged INTEGER NOT NULL DEFAULT 0,
              halluc INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (workflow_id, cohort_id, dim, value)
            );
            CREATE TABLE IF NOT EXISTS hour_stats (
              workflow_id TEXT NOT NULL,
              cohort_id TEXT NOT NULL,
              hour INTEGER NOT NULL,
              dials INTEGER NOT NULL DEFAULT 0,
              connected INTEGER NOT NULL DEFAULT 0,
              engaged INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (workflow_id, cohort_id, hour)
            );
            CREATE TABLE IF NOT EXISTS person_hours (
              workflow_id TEXT NOT NULL,
              person_id TEXT NOT NULL,
              hour INTEGER NOT NULL,
              dials INTEGER NOT NULL DEFAULT 0,
              connected INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (workflow_id, person_id, hour)
            );
            CREATE TABLE IF NOT EXISTS lead_features (
              lead_id TEXT PRIMARY KEY,
              workflow_id TEXT NOT NULL,
              person_id TEXT,
              features TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_chunks (
              chunk INTEGER PRIMARY KEY,
              kind TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proposal_log (
              proposal_id TEXT PRIMARY KEY,
              workflow_id TEXT NOT NULL,
              approval_id TEXT,
              status TEXT,
              purpose TEXT,
              created_at TEXT
            );
            """
        )
        c.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self._touch(force=True)

    def audit(self, kind: str, workflow_id: str | None, payload: dict, sim_ts: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit(ts,sim_ts,workflow_id,kind,payload) VALUES(?,?,?,?,?)",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), sim_ts, workflow_id, kind, json.dumps(payload)),
            )
            self._touch(force=True)

    def query_audit(self, workflow_id: str | None = None, kind: str | None = None, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM audit WHERE 1=1"
        args: list[Any] = []
        if workflow_id:
            q += " AND workflow_id=?"
            args.append(workflow_id)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]

    def mark_chunk(self, chunk: int, kind: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_chunks(chunk, kind) VALUES(?,?)",
                (chunk, kind),
            )
            self._touch(force=True)

    def is_chunk_done(self, chunk: int) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM processed_chunks WHERE chunk=?", (chunk,)).fetchone()
        return row is not None

    def last_chunk(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT MAX(chunk) AS m FROM processed_chunks").fetchone()
        return int(row["m"]) if row and row["m"] is not None else -1

    def set_pending(self, workflow_id: str, approval_id: str, proposal_id: str, sim_ts: str, purpose: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending(workflow_id,approval_id,proposal_id,created_sim_ts,purpose) "
                "VALUES(?,?,?,?,?) ON CONFLICT(workflow_id) DO UPDATE SET "
                "approval_id=excluded.approval_id, proposal_id=excluded.proposal_id, "
                "created_sim_ts=excluded.created_sim_ts, purpose=excluded.purpose",
                (workflow_id, approval_id, proposal_id, sim_ts, purpose),
            )
            self._conn.execute(
                "INSERT INTO proposal_log(proposal_id,workflow_id,approval_id,status,purpose,created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(proposal_id) DO UPDATE SET "
                "approval_id=excluded.approval_id, status=excluded.status",
                (proposal_id, workflow_id, approval_id, "pending", purpose, sim_ts),
            )
            self._touch(force=True)

    def clear_pending(self, workflow_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pending WHERE workflow_id=?", (workflow_id,))
            self._touch(force=True)

    def all_pending(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM pending").fetchall()
        return {r["workflow_id"]: dict(r) for r in rows}

    def update_proposal_status(self, proposal_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE proposal_log SET status=? WHERE proposal_id=?",
                (status, proposal_id),
            )
            self._touch(force=True)

    def get_experiment(self, workflow_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT state, payload FROM experiments WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
        if not row:
            return None
        return {"state": row["state"], **json.loads(row["payload"])}

    def set_experiment(self, workflow_id: str, state: str, payload: dict) -> None:
        payload = {**payload, "phase": payload.get("phase", state)}
        with self._lock:
            self._conn.execute(
                "INSERT INTO experiments(workflow_id,state,payload) VALUES(?,?,?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET state=excluded.state, payload=excluded.payload",
                (workflow_id, state, json.dumps(payload)),
            )
            self._touch(force=True)

    def set_cohorts(self, workflow_id: str, cohorts: list[dict]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO cohort_defs(workflow_id,cohorts_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET cohorts_json=excluded.cohorts_json, "
                "updated_at=excluded.updated_at",
                (workflow_id, json.dumps(cohorts), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            self._touch(force=True)

    def get_cohorts(self, workflow_id: str) -> list[dict] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT cohorts_json FROM cohort_defs WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
        return json.loads(row["cohorts_json"]) if row else None

    def upsert_lead(self, lead_id: str, workflow_id: str, person_id: str, features: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO lead_features(lead_id,workflow_id,person_id,features) VALUES(?,?,?,?) "
                "ON CONFLICT(lead_id) DO UPDATE SET features=excluded.features",
                (lead_id, workflow_id, person_id, json.dumps(features)),
            )
            self._touch()

    def get_lead(self, lead_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM lead_features WHERE lead_id=?", (lead_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "lead_id": row["lead_id"],
            "workflow_id": row["workflow_id"],
            "person_id": row["person_id"],
            "features": json.loads(row["features"]),
        }

    def add_call_stat(
        self,
        workflow_id: str,
        cohort_id: str,
        dim: str,
        value: str,
        dials: int = 0,
        connected: int = 0,
        engaged: int = 0,
        halluc: int = 0,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO call_stats(workflow_id,cohort_id,dim,value,dials,connected,engaged,halluc) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(workflow_id,cohort_id,dim,value) DO UPDATE SET "
                "dials=dials+excluded.dials, connected=connected+excluded.connected, "
                "engaged=engaged+excluded.engaged, halluc=halluc+excluded.halluc",
                (workflow_id, cohort_id, dim, value, dials, connected, engaged, halluc),
            )
            self._touch()

    def get_call_stats(self, workflow_id: str, dim: str | None = None) -> list[dict]:
        q = "SELECT * FROM call_stats WHERE workflow_id=?"
        args: list[Any] = [workflow_id]
        if dim:
            q += " AND dim=?"
            args.append(dim)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def add_hour_stat(
        self, workflow_id: str, cohort_id: str, hour: int, dials: int, connected: int, engaged: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO hour_stats(workflow_id,cohort_id,hour,dials,connected,engaged) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(workflow_id,cohort_id,hour) DO UPDATE SET "
                "dials=dials+excluded.dials, connected=connected+excluded.connected, "
                "engaged=engaged+excluded.engaged",
                (workflow_id, cohort_id, hour, dials, connected, engaged),
            )
            self._touch()

    def get_hour_stats(self, workflow_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM hour_stats WHERE workflow_id=?", (workflow_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def add_person_hour(self, workflow_id: str, person_id: str, hour: int, dials: int, connected: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO person_hours(workflow_id,person_id,hour,dials,connected) VALUES(?,?,?,?,?) "
                "ON CONFLICT(workflow_id,person_id,hour) DO UPDATE SET "
                "dials=dials+excluded.dials, connected=connected+excluded.connected",
                (workflow_id, person_id, hour, dials, connected),
            )
            self._touch()

    def best_person_hours(self, workflow_id: str, min_dials: int = 4, limit: int = 5000) -> dict[str, int]:
        """Return person_id -> preferred hour for people with enough history."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT person_id, hour, dials, connected FROM person_hours WHERE workflow_id=?",
                (workflow_id,),
            ).fetchall()
        by: dict[str, list] = {}
        for r in rows:
            by.setdefault(r["person_id"], []).append(r)
        out: dict[str, int] = {}
        for pid, items in by.items():
            total = sum(i["dials"] for i in items)
            if total < min_dials:
                continue
            best = max(items, key=lambda i: (i["connected"] / max(i["dials"], 1), i["connected"]))
            if best["dials"] >= 2 and best["connected"] / best["dials"] >= 0.2:
                out[pid] = int(best["hour"])
            if len(out) >= limit:
                break
        return out

    def reset_dim_stats(self, workflow_id: str, dim: str) -> None:
        """Reset counters for a dimension after a major config change (post-supervisor)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM call_stats WHERE workflow_id=? AND dim=?",
                (workflow_id, dim),
            )
            self._touch(force=True)
