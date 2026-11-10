"""Main control loop: ingest → detect → experiment → propose → apply → learn."""
from __future__ import annotations

import copy
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import cohorts as C
from . import governance as G
from . import stats as S
from .client import SimClient
from .store import Store

log = logging.getLogger("controller")

# Minimum samples before declaring a winner (population, not anecdote).
MIN_N_ENGAGE = 60          # connected calls for engagement experiments
MIN_N_PICKUP = 120         # dials for pickup experiments
MIN_N_DID = 100            # dials per caller ID before health judgment
HALLUC_SPIKE = 0.02        # connected-call hallucination rate threshold
HALLUC_WINDOW_MIN = 30     # need this many connected before judging halluc
DECAY_DROP = 0.035
DECAY_MIN_N = 50
ADVANCE_MINUTES = 60       # react hourly; good latency vs. round-trips


class Controller:
    def __init__(self, url: str, data_dir: str | Path):
        self.client = SimClient(url)
        self.store = Store(Path(data_dir) / "controller.sqlite")
        self.workflows: dict[str, dict] = {}
        self._supervisor_versions: dict[str, int] = {}
        self._last_decision_day: dict[str, int] = {}
        self._last_schedule_day: dict[str, int] = {}
        self._last_did_day: dict[str, int] = {}
        self._did_count: dict[str, int] = {}
        self._demoted_dids: dict[str, set[str]] = {}
        self._halluc_handled: set[str] = set()
        self._credits_reserved = 0
        self._obs = {
            "events_ingested": 0,
            "decisions": 0,
            "proposals": 0,
            "applies": 0,
            "auto_applies": 0,
            "llm_calls": 0,
            "validations": 0,
        }

    # ------------------------------------------------------------------ bootstrap
    def bootstrap(self) -> None:
        self.client.health()
        self.workflows = {w["workflow_id"]: w for w in self.client.workflows()}
        log.info("workflows: %s", list(self.workflows))

        # Reconcile pending approvals after crash
        self._reconcile_pending()

        # Ingest history chunks we haven't processed
        last = self.store.last_chunk()
        hist = self.client.list_chunks("history", after=last)
        for ch in hist:
            self._ingest_chunk(ch["chunk"], kind="history")
        # Also catch any event chunks already present (restart mid-run)
        ev = self.client.list_chunks("events", after=self.store.last_chunk())
        for ch in ev:
            self._ingest_chunk(ch["chunk"], kind="events")

        # Build / restore shared cohorts per workflow
        for wid, wf in self.workflows.items():
            existing = self.store.get_cohorts(wid)
            if existing is None:
                leads = self._sample_leads(wid, limit=5000)
                cohorts = C.build_cohorts_from_history(leads, languages=wf.get("languages"))
                self.store.set_cohorts(wid, cohorts)
                self.store.audit("cohorts_bootstrapped", wid, {"cohorts": cohorts})
                log.info("%s cohorts: %s", wid, [c["id"] for c in cohorts])
            exp = self.store.get_experiment(wid)
            if exp is None:
                self.store.set_experiment(wid, "idle", {"phase": "idle", "started_day": None})

        self.store.audit("bootstrap_complete", None, {"last_chunk": self.store.last_chunk(), "obs": self._obs})

    def _sample_leads(self, wid: str, limit: int = 5000) -> list[dict]:
        """Pull lead features already stored from history ingestion."""
        # Use SQL via a lightweight scan of call_stats isn't enough — use lead_features
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT features FROM lead_features WHERE workflow_id=? LIMIT ?",
                (wid, limit),
            ).fetchall()
        import json
        return [{"features": json.loads(r["features"])} for r in rows]

    def _reconcile_pending(self) -> None:
        pending = self.store.all_pending()
        for wid, p in list(pending.items()):
            try:
                ap = self.client.get_approval(p["approval_id"])
            except Exception as e:
                log.warning("reconcile %s failed: %s", wid, e)
                continue
            status = ap.get("status")
            self.store.update_proposal_status(p["proposal_id"], status)
            if status == "approved":
                code, data = self.client.apply(wid, p["approval_id"])
                self.store.audit(
                    "apply_on_reconcile",
                    wid,
                    {"approval_id": p["approval_id"], "code": code, "data": data},
                )
                if code in (200, 409, 410):
                    self.store.clear_pending(wid)
                    if code == 200:
                        self._obs["applies"] += 1
            elif status in ("rejected", "expired", "lapsed", "applied"):
                self.store.clear_pending(wid)
                # Expired/rejected → re-queue later with fresh evidence (product rule)
                if status in ("rejected", "expired", "lapsed"):
                    exp = self.store.get_experiment(wid) or {}
                    exp["last_terminal"] = status
                    exp["requeue"] = True
                    self.store.set_experiment(wid, exp.get("state", "idle"), exp)

    # ------------------------------------------------------------------ ingest
    def _ingest_chunk(self, chunk: int, kind: str) -> None:
        if self.store.is_chunk_done(chunk):
            return
        for ev in self.client.iter_chunk_events(chunk):
            self._handle_event(ev)
            self._obs["events_ingested"] += 1
        self.store.flush()
        self.store.mark_chunk(chunk, kind)

    def _handle_event(self, ev: dict) -> None:
        et = ev.get("type")
        if et == "lead":
            # Sample leads for cohort formation — full lead store is unnecessary at load scale.
            if hash(ev["lead_id"]) % 10 == 0:
                self.store.upsert_lead(
                    ev["lead_id"], ev["workflow_id"], ev.get("person_id"), ev.get("features") or {}
                )
        elif et == "call":
            self._handle_call(ev)
        elif et == "config_changed":
            self._handle_config_changed(ev)
        elif et == "approval_decided":
            self.store.audit(
                "approval_decided_event",
                ev.get("workflow_id"),
                {"approval_id": ev.get("approval_id"), "status": ev.get("status"), "reasons": ev.get("reasons")},
                sim_ts=ev.get("ts"),
            )

    def _handle_call(self, ev: dict) -> None:
        wid = ev["workflow_id"]
        cohort = ev.get("cohort_id") or "default"
        variant = ev.get("variant") or {}
        dials = 1
        connected = 1 if ev.get("connected") else 0
        engaged = 1 if ev.get("engaged") else 0
        halluc = 0
        if connected and (ev.get("qa") or {}).get("hallucination_flag"):
            halluc = 1

        for dim in ("welcome_message", "voice", "caller_id", "prompt_version"):
            val = variant.get(dim)
            if val is None:
                continue
            self.store.add_call_stat(wid, cohort, dim, val, dials, connected, engaged, halluc)
            # Also roll up to __all__ for workflow-level views
            self.store.add_call_stat(wid, "__all__", dim, val, dials, connected, engaged, halluc)

        # Hour stats
        try:
            hour = int(ev["ts"][11:13])
        except Exception:
            hour = -1
        if hour >= 0:
            self.store.add_hour_stat(wid, cohort, hour, dials, connected, engaged)
            self.store.add_hour_stat(wid, "__all__", hour, dials, connected, engaged)
            pid = ev.get("person_id")
            # Cap per-person tracking: sample ~20% to stay within schedule.lead_preferred_hour budget at scale.
            if pid and hash(pid) % 5 == 0:
                self.store.add_person_hour(wid, pid, hour, dials, connected)

    def _handle_config_changed(self, ev: dict) -> None:
        wid = ev["workflow_id"]
        actor = ev.get("actor")
        version = ev.get("version")
        self.store.audit(
            "config_changed",
            wid,
            {"version": version, "actor": actor, "changed_dims": ev.get("changed_dims"),
             "proposal_id": ev.get("proposal_id"), "approval_id": ev.get("approval_id")},
            sim_ts=ev.get("ts"),
        )
        if actor == "supervisor":
            # Never silently overwrite. Mark experiment dirty / rebaseline.
            self._supervisor_versions[wid] = version
            exp = self.store.get_experiment(wid) or {"phase": "idle"}
            exp["supervisor_edit_version"] = version
            exp["supervisor_edit_ts"] = ev.get("ts")
            exp["dirty"] = True
            # If they changed prompt, clear halluc handled so we can respond again
            if "prompt_version" in (ev.get("changed_dims") or []):
                self._halluc_handled.discard(wid)
                exp["phase"] = "incident_watch"
            self.store.set_experiment(wid, "interrupted", exp)
            log.info("supervisor edit on %s v%s dims=%s", wid, version, ev.get("changed_dims"))

    # ------------------------------------------------------------------ main run
    def run(self) -> None:
        self.bootstrap()
        while True:
            st = self.client.advance(ADVANCE_MINUTES)
            sim_ts = st.get("ts") or ""
            for ch in st.get("new_chunks") or []:
                self._ingest_chunk(ch["chunk"], kind=ch.get("kind", "events"))

            self._poll_and_apply(sim_ts)
            self._decide_all(sim_ts)

            if st.get("finished"):
                break

        state = self.client.state()
        self.store.audit("run_finished", None, {"state": state, "obs": self._obs})
        log.info("finished: %s obs=%s", state, self._obs)
        self.client.close()
        self.store.close()

    # ------------------------------------------------------------------ apply path
    def _poll_and_apply(self, sim_ts: str) -> None:
        for wid, p in list(self.store.all_pending().items()):
            try:
                ap = self.client.get_approval(p["approval_id"])
            except Exception as e:
                log.warning("poll approval %s: %s", p["approval_id"], e)
                continue
            status = ap.get("status")
            if status == "approved":
                code, data = self.client.apply(wid, p["approval_id"])
                self.store.audit(
                    "apply",
                    wid,
                    {"approval_id": p["approval_id"], "proposal_id": p["proposal_id"],
                     "purpose": p.get("purpose"), "code": code, "data": data},
                    sim_ts=sim_ts,
                )
                self.store.update_proposal_status(p["proposal_id"], "applied" if code == 200 else status)
                if code == 200:
                    self._obs["applies"] += 1
                    exp = self.store.get_experiment(wid) or {}
                    exp["last_applied_proposal"] = p["proposal_id"]
                    exp["dirty"] = False
                    if exp.get("phase") == "awaiting_prompt_fix":
                        exp["phase"] = "verify_prompt"
                        exp["verify_from_ts"] = sim_ts
                    elif exp.get("phase") in ("awaiting_explore", "awaiting_promote"):
                        exp["phase"] = "exploring" if "explore" in (p.get("purpose") or "") else "monitoring"
                        exp["monitor_baseline"] = None
                    self.store.set_experiment(wid, exp.get("phase", "idle"), exp)
                    self.store.clear_pending(wid)
                elif code in (409, 410):
                    # stale or lapsed — clear and re-propose later
                    self.store.clear_pending(wid)
                    exp = self.store.get_experiment(wid) or {}
                    exp["requeue"] = True
                    exp["last_terminal"] = "stale" if code == 409 else "lapsed"
                    self.store.set_experiment(wid, exp.get("state", "idle"), exp)
                # else leave pending and retry
            elif status in ("rejected", "expired", "lapsed"):
                self.store.update_proposal_status(p["proposal_id"], status)
                self.store.audit(
                    "proposal_terminal",
                    wid,
                    {"status": status, "reasons": ap.get("reasons"), "proposal_id": p["proposal_id"]},
                    sim_ts=sim_ts,
                )
                self.store.clear_pending(wid)
                exp = self.store.get_experiment(wid) or {}
                exp["requeue"] = True
                exp["last_terminal"] = status
                # Don't hammer after reject — wait until next day decision
                self.store.set_experiment(wid, exp.get("state", "idle"), exp)

    # ------------------------------------------------------------------ decisions
    def _decide_all(self, sim_ts: str) -> None:
        if not sim_ts:
            return
        try:
            hour = int(sim_ts[11:13])
            day = self._day_index(sim_ts)
        except Exception:
            return

        # Decision cadence: mid-morning (after dialling starts) and late afternoon
        if hour not in (11, 12, 16, 17, 18):
            # Still handle urgent incidents every hour
            for wid in self.workflows:
                self._handle_incidents(wid, sim_ts)
                self._handle_did_health(wid, sim_ts, urgent_only=True)
            return

        for wid, wf in self.workflows.items():
            if wid in self.store.all_pending():
                continue
            self._obs["decisions"] += 1
            self._handle_incidents(wid, sim_ts)
            if wid in self.store.all_pending():
                continue
            self._handle_did_health(wid, sim_ts, urgent_only=False)
            if wid in self.store.all_pending():
                continue
            self._handle_schedule(wid, sim_ts)
            if wid in self.store.all_pending():
                continue
            self._handle_experiments(wid, sim_ts, day)
            self.store.audit("decision_tick", wid, {"hour": hour, "day": day, "obs": dict(self._obs)}, sim_ts=sim_ts)

    def _day_index(self, sim_ts: str) -> int:
        # D0 = 2027-01-04
        dt = datetime.fromisoformat(sim_ts)
        base = datetime.fromisoformat("2027-01-04T00:00:00+05:30")
        return (dt - base).days

    # ------------------------------------------------------------------ incidents (hallucination)
    def _handle_incidents(self, wid: str, sim_ts: str) -> None:
        if wid in self._halluc_handled:
            return
        rows = self.store.get_call_stats(wid, dim="prompt_version")
        # Prefer recent-ish by using __all__ cohort aggregate for current prompt
        try:
            cur = self.client.config(wid)
        except Exception:
            return
        prompt = cur["config"].get("prompt_version")
        connected = engaged = halluc = 0
        for r in rows:
            if r["cohort_id"] != "__all__":
                continue
            if r["value"] != prompt:
                continue
            connected += r["connected"]
            engaged += r["engaged"]
            halluc += r["halluc"]
        if connected < HALLUC_WINDOW_MIN:
            return
        rate = halluc / connected
        if rate < HALLUC_SPIKE:
            return

        # Incident: hallucination spike
        log.warning("%s halluc spike rate=%.3f on %s", wid, rate, prompt)
        self.store.audit(
            "halluc_detected",
            wid,
            {"prompt": prompt, "rate": rate, "connected": connected, "halluc": halluc},
            sim_ts=sim_ts,
        )

        state = self.client.state()
        credits = state.get("credits_remaining", 0)
        if credits < 8:
            log.error("insufficient credits for incident response on %s", wid)
            self.store.audit("halluc_skipped_credits", wid, {"credits": credits}, sim_ts=sim_ts)
            return

        code, sug = self.client.suggest_fixes(wid, f"hallucination_rate={rate:.3f} on prompt {prompt}")
        self._obs["llm_calls"] += 1
        if code != 200:
            self.store.audit("llm_failed", wid, {"code": code, "data": sug}, sim_ts=sim_ts)
            return

        suggestions = sug.get("suggestions") or []
        gov = self.workflows[wid]["governance"]
        min_pass = gov.get("min_validation_pass_rate", 0.97)
        catalog = self.workflows[wid].get("prompt_catalog") or {}

        def _score_candidate(pv: str, summary: str, pass_rate: float) -> float:
            """Prefer safe prompts that are not engagement cliffs (strict/verbatim)."""
            s = (summary or catalog.get(pv) or "").lower()
            score = pass_rate
            if "persuasive" in s or "urgency" in s:
                score -= 0.5
            if "strict" in s or "verbatim" in s or "near-verbatim" in s:
                score -= 0.2  # safe but kills engagement — last resort
            if "grounded" in s or "tighter" in s:
                score += 0.05
            if pv == "P1" or "current production" in s:
                score += 0.02
            return score

        best = None
        best_score = -1.0
        candidates = list(suggestions)
        # Always consider catalog alternatives too (LLM order is not trustable).
        seen = {s["prompt_version"] for s in candidates}
        for pv, summary in catalog.items():
            if pv == prompt or pv in seen:
                continue
            candidates.append({"prompt_version": pv, "summary": summary})

        for s in candidates:
            pv = s["prompt_version"]
            summary = s.get("summary") or catalog.get(pv) or ""
            sl = summary.lower()
            if "persuasive" in sl or "urgency" in sl:
                continue  # fail closed on known unsafe families before spending credits
            n = 200 if credits >= 15 else 100
            if credits < (n / 100):
                break
            vcode, vdata = self.client.validate(wid, pv, n=n)
            self._obs["validations"] += 1
            if vcode != 200:
                continue
            self.store.audit("validation", wid, vdata, sim_ts=sim_ts)
            credits = vdata.get("credits_remaining", credits)
            pr = float(vdata.get("pass_rate") or 0)
            if pr < min_pass:
                continue
            sc = _score_candidate(pv, summary, pr)
            if sc > best_score:
                best_score = sc
                best = {**vdata, "summary": summary}

        if not best:
            self.store.audit("halluc_no_safe_fix", wid, {"suggestions": suggestions}, sim_ts=sim_ts)
            self._halluc_handled.add(wid)  # avoid credit burn loop
            return

        new_cfg = copy.deepcopy(cur["config"])
        new_cfg["prompt_version"] = best["prompt_version"]
        # Keep cohorts if we have them
        cohorts = self.store.get_cohorts(wid) or new_cfg.get("cohorts") or []
        new_cfg["cohorts"] = cohorts

        dry = {
            "method": "validation_gate",
            "current_halluc_rate": round(rate, 4),
            "candidate": best["prompt_version"],
            "validation_pass_rate": best["pass_rate"],
            "validation_n": best.get("n"),
            "note": "Validation measures safety, not engagement. We only switch when pass_rate clears policy.",
        }
        body = G.build_proposal(
            workflow_id=wid,
            base_version=cur["version"],
            current_cfg=cur["config"],
            proposed_cfg=new_cfg,
            purpose=f"Replace {prompt} after hallucination spike ({rate:.1%}) with validated {best['prompt_version']}",
            rationale=(
                f"Detected hallucination flag rate {rate:.3f} on {connected} connected calls for {prompt}. "
                f"LLM suggested alternatives; {best['prompt_version']} passed validation at "
                f"{best['pass_rate']:.3f} (threshold {min_pass}). Winner chosen by validation, not by the LLM."
            ),
            dry_run=dry,
            affected_leads=G.estimate_affected(self.workflows[wid]["daily_batch"]["size"]),
            impact_score=min(0.9, 0.4 + rate * 10),
            evidence={"validation_run_id": best["validation_run_id"]},
        )
        if self._submit(wid, body, cur, sim_ts, purpose="prompt_incident_fix"):
            self._halluc_handled.add(wid)
            exp = self.store.get_experiment(wid) or {}
            exp["phase"] = "awaiting_prompt_fix"
            exp["incident_prompt_from"] = prompt
            exp["incident_prompt_to"] = best["prompt_version"]
            self.store.set_experiment(wid, "awaiting_prompt_fix", exp)

    # ------------------------------------------------------------------ caller ID health
    def _handle_did_health(self, wid: str, sim_ts: str, urgent_only: bool) -> None:
        rows = [r for r in self.store.get_call_stats(wid, dim="caller_id") if r["cohort_id"] == "__all__"]
        if not rows:
            return
        # pickup rate per DID
        # Prefer recent weakness: allow a lower n when urgent (incident path).
        need = max(40, MIN_N_DID // 2) if urgent_only else MIN_N_DID
        rates = []
        for r in rows:
            if r["dials"] < need:
                continue
            rates.append((r["value"], r["connected"] / r["dials"], r["dials"], r["connected"]))
        if len(rates) < 2:
            return
        rates.sort(key=lambda t: t[1])
        worst_id, worst_rate, worst_n, _ = rates[0]
        best_id, best_rate, best_n, _ = rates[-1]
        gap = best_rate - worst_rate
        if gap < (0.03 if urgent_only else 0.05):
            return
        if worst_rate > 0.25 and urgent_only:
            return

        day = self._day_index(sim_ts)
        # Allow up to 2 DID rotations/day when urgent; else once/day.
        did_count_key = f"{wid}:{day}"
        did_count = self._did_count.get(did_count_key, 0)
        limit = 2 if urgent_only else 1
        if did_count >= limit:
            return

        try:
            cur = self.client.config(wid)
        except Exception:
            return
        cfg = copy.deepcopy(cur["config"])
        opts = self.workflows[wid]["options"]["caller_id"]
        allocs = cfg.setdefault("allocations", {})
        default = allocs.setdefault("default", {})
        current = dict(default.get("caller_id") or {opts[0]: 1.0})
        demoted = self._demoted_dids.setdefault(wid, set())
        # Sticky demotion: once a DID looks bad, keep it capped (avoids oscillation on cumulative stats).
        demoted.add(worst_id)
        target = {}
        for o in opts:
            if o in demoted:
                target[o] = 0.02
            else:
                target[o] = 1.0
        # Always keep at least one non-demoted option with mass.
        if all(o in demoted for o in opts):
            # If everything was demoted, keep the currently best and clear others.
            demoted.clear()
            demoted.add(worst_id)
            target = {o: (0.02 if o == worst_id else 1.0) for o in opts}
        target = S.normalize(target)

        gov = self.workflows[wid]["governance"]
        max_shift = float((gov.get("auto_apply") or {}).get("max_shift") or 0.5)
        new_did = S.blend_toward(current, target, max_shift if (gov.get("auto_apply") or {}).get("enabled") else 1.0)
        if S.total_variation(current, new_did) < 0.05:
            return
        default["caller_id"] = new_did
        cfg["cohorts"] = self.store.get_cohorts(wid) or cfg.get("cohorts") or []

        dry = G.dry_run_from_stats(
            "pickup_rate", best_rate, worst_rate, best_n, worst_n,
            f"rotating away from {worst_id} pickup={worst_rate:.3f}",
        )
        purpose = f"Rotate away from weak caller ID {worst_id} (pickup {worst_rate:.1%})"
        rationale = (
            f"Across ≥{MIN_N_DID} dials, {worst_id} pickup={worst_rate:.3f} vs best {best_id}={best_rate:.3f}. "
            f"Population-level threshold, not a single call. Shift is capped for safety."
        )
        affected = G.estimate_affected(self.workflows[wid]["daily_batch"]["size"], days=2)
        impact = S.impact_from_lift(best_rate - worst_rate, affected)

        ok_auto, _ = G.can_auto_apply(gov, cur["config"], cfg)
        if ok_auto and "caller_id" in ((gov.get("auto_apply") or {}).get("allowed_dims") or []):
            body = {
                "proposal_id": G.new_proposal_id("did"),
                "base_version": cur["version"],
                "proposed_config": cfg,
                "rationale": rationale,
            }
            code, data = self.client.auto_apply(wid, body)
            self.store.audit("auto_apply_did", wid, {"code": code, "data": data, "worst": worst_id}, sim_ts=sim_ts)
            if code == 200:
                self._obs["auto_applies"] += 1
                self._last_did_day[wid] = day
                self._did_count[did_count_key] = did_count + 1
            return

        body = G.build_proposal(
            wid, cur["version"], cur["config"], cfg, purpose, rationale, dry, affected, impact
        )
        if self._submit(wid, body, cur, sim_ts, purpose="did_rotate"):
            self._last_did_day[wid] = day
            self._did_count[did_count_key] = did_count + 1

    # ------------------------------------------------------------------ schedule / call time
    def _handle_schedule(self, wid: str, sim_ts: str) -> None:
        wf = self.workflows[wid]
        day = self._day_index(sim_ts)
        # At most one schedule auto-apply per workflow per simulated day (governance hygiene).
        if self._last_schedule_day.get(wid) == day:
            return

        window = wf["dialling_window"]
        hours = list(range(window[0], window[1]))
        hstats = self.store.get_hour_stats(wid)
        if not hstats:
            return

        cohorts = self.store.get_cohorts(wid) or []
        hour_weights: dict[str, dict[str, float]] = {}

        by_cohort: dict[str, list] = defaultdict(list)
        for r in hstats:
            if r["hour"] not in hours:
                continue
            by_cohort[r["cohort_id"]].append(r)

        def _weights_for(rows: list) -> dict[str, float]:
            # Every dialling-window hour must appear — omitted hours get weight 0 in the sim.
            weights = {str(h): 0.15 for h in hours}
            for r in rows:
                if r["hour"] not in hours:
                    continue
                pickup = r["connected"] / max(r["dials"], 1)
                weights[str(r["hour"])] = max(0.05, pickup) ** 2
            return S.normalize(weights)

        for cid, rows in by_cohort.items():
            if cid == "__all__":
                continue
            total_dials = sum(r["dials"] for r in rows)
            if total_dials < 80:
                continue
            hour_weights[cid] = _weights_for(rows)

        all_rows = by_cohort.get("__all__") or []
        if all_rows and sum(r["dials"] for r in all_rows) >= 100:
            hour_weights["default"] = _weights_for(all_rows)

        if not hour_weights:
            return

        lead_pref = self.store.best_person_hours(wid, min_dials=5, limit=2000)

        try:
            cur = self.client.config(wid)
        except Exception:
            return
        cfg = copy.deepcopy(cur["config"])
        cfg["cohorts"] = cohorts or cfg.get("cohorts") or []
        old_sched = cfg.get("schedule") or {}
        old_hw = old_sched.get("hour_weights") or {}
        # Skip tiny churn: require meaningful movement in default hour weights or new person prefs.
        if old_hw.get("default") and hour_weights.get("default"):
            if S.total_variation(old_hw["default"], hour_weights["default"]) < 0.08:
                old_pref = old_sched.get("lead_preferred_hour") or {}
                if len(lead_pref) <= len(old_pref) + 100:
                    return

        new_sched = {
            "hour_weights": hour_weights,
            "lead_preferred_hour": lead_pref,
        }
        cfg["schedule"] = new_sched

        gov = wf["governance"]
        ok_auto, problems = G.can_auto_apply(gov, cur["config"], cfg)
        rationale = (
            f"Hour weights learned from historical pickup by shared cohort; "
            f"{len(lead_pref)} returning persons got preferred hours (×6 in-hour / ×0.25 else)."
        )
        if ok_auto:
            body = {
                "proposal_id": G.new_proposal_id("sched"),
                "base_version": cur["version"],
                "proposed_config": cfg,
                "rationale": rationale,
            }
            code, data = self.client.auto_apply(wid, body)
            self.store.audit("auto_apply_schedule", wid, {"code": code, "data": data, "problems": problems}, sim_ts=sim_ts)
            if code == 200:
                self._obs["auto_applies"] += 1
                self._last_schedule_day[wid] = day
            return
        self.store.audit("schedule_skipped_no_auto", wid, {"problems": problems}, sim_ts=sim_ts)

    # ------------------------------------------------------------------ experiments
    def _handle_experiments(self, wid: str, sim_ts: str, day: int) -> None:
        wf = self.workflows[wid]
        primary = wf["objective"]["primary"]
        guard = wf["objective"].get("guardrail") or {}
        exp = self.store.get_experiment(wid) or {"phase": "idle"}
        phase = exp.get("phase", "idle")

        # After supervisor interrupt, rebaseline and cool down before new experiments.
        if exp.get("dirty") or phase == "interrupted":
            cool_until = day + 1
            exp["dirty"] = False
            exp["phase"] = "cooldown"
            exp["cooldown_until_day"] = cool_until
            exp["control_snapshot"] = None
            self.store.set_experiment(wid, "cooldown", exp)
            phase = "cooldown"

        if phase == "cooldown":
            until = exp.get("cooldown_until_day", day)
            if day < until:
                return
            exp["phase"] = "idle"
            self.store.set_experiment(wid, "idle", exp)
            phase = "idle"

        try:
            cur = self.client.config(wid)
        except Exception:
            return
        cfg_now = cur["config"]
        cohorts = self.store.get_cohorts(wid) or cfg_now.get("cohorts") or []

        # Verify prompt fix
        if phase == "verify_prompt":
            rows = [r for r in self.store.get_call_stats(wid, "prompt_version") if r["cohort_id"] == "__all__"]
            pv = cfg_now.get("prompt_version")
            for r in rows:
                if r["value"] == pv and r["connected"] >= HALLUC_WINDOW_MIN:
                    rate = r["halluc"] / r["connected"]
                    self.store.audit("prompt_verify", wid, {"prompt": pv, "rate": rate}, sim_ts=sim_ts)
                    exp["phase"] = "idle" if rate < HALLUC_SPIKE else "incident_watch"
                    self.store.set_experiment(wid, exp["phase"], exp)
                    break
            return

        if phase in ("awaiting_explore", "awaiting_promote", "awaiting_prompt_fix"):
            return

        min_n = MIN_N_ENGAGE if primary == "engagement_rate" else MIN_N_PICKUP

        if phase in ("idle", "incident_watch"):
            # Start exploration once we have some history (day >= 0 after bootstrap)
            if day < 0:
                return
            # Respect approver fatigue / hours
            if not self._may_propose(wid, sim_ts):
                return
            new_cfg = copy.deepcopy(cfg_now)
            new_cfg["cohorts"] = cohorts
            dims = ["welcome_message", "voice"]
            control_default = (cfg_now.get("allocations") or {}).get("default") or {}
            explore = C.explore_allocations(wf["options"], dims, control_default, explore_weight=0.5)
            allocs = {"default": explore}
            # Explore only the largest few cohorts to keep sample sizes usable.
            for c in cohorts[:4]:
                allocs[c["id"]] = C.explore_allocations(
                    wf["options"],
                    dims,
                    (cfg_now.get("allocations") or {}).get(c["id"]) or control_default,
                    0.5,
                )
            new_cfg["allocations"] = allocs
            if G.changed_dims(cfg_now, new_cfg) == []:
                return
            dry = {"method": "explore_start", "dims": dims, "note": "equal-ish explore with control mass retained"}
            body = G.build_proposal(
                wid,
                cur["version"],
                cfg_now,
                new_cfg,
                purpose="Start variant exploration on welcome × voice (shared cohorts)",
                rationale=(
                    "We need population-level evidence before promoting. Traffic splits across welcome and voice "
                    "independently; cohorts are the same objects used for detection and the supervisor view. "
                    "Winner selection will be statistical, not LLM-based."
                ),
                dry_run=dry,
                affected_leads=G.estimate_affected(wf["daily_batch"]["size"], days=2),
                impact_score=0.35,
            )
            if self._submit(wid, body, cur, sim_ts, purpose="explore"):
                exp.update({"phase": "awaiting_explore", "explore_started_day": day, "min_n": min_n})
                self.store.set_experiment(wid, "awaiting_explore", exp)
            return

        if phase == "exploring":
            # Evaluate for winners per cohort × dim
            promotions = self._find_promotions(wid, primary, guard, min_n)
            started = exp.get("explore_started_day")
            if started is not None and day - started < 1 and not promotions:
                return  # give at least ~1 day
            if not promotions:
                # Keep exploring; maybe nudge after several days
                if started is not None and day - started >= 4:
                    exp["phase"] = "idle"  # restart exploration later with fresh eyes
                    self.store.set_experiment(wid, "idle", exp)
                return
            last_promo_day = exp.get("last_promo_day")
            if last_promo_day is not None and day - last_promo_day < 2:
                return
            if not self._may_propose(wid, sim_ts):
                return
            new_cfg = copy.deepcopy(cfg_now)
            new_cfg["cohorts"] = cohorts
            allocs = copy.deepcopy(new_cfg.get("allocations") or {"default": {}})
            notes = []
            for item in promotions:
                cid, dim, winner, lift, evidence = item
                bucket = allocs.setdefault(cid, copy.deepcopy(allocs.get("default") or {}))
                opts = wf["options"][dim]
                bucket[dim] = C.promote_allocation(bucket.get(dim) or {opts[0]: 1.0}, winner, opts, holdout=0.08)
                notes.append(f"{cid}.{dim}→{winner} (lift≈{lift:.3f})")
            new_cfg["allocations"] = allocs
            # Guard: don't propose empty
            if not G.changed_dims(cfg_now, new_cfg):
                return
            # Batch into one proposal (ops supervisor ask)
            dry = {"promotions": [p[4] for p in promotions], "method": "wilson_vs_control"}
            lift = max(p[3] for p in promotions)
            affected = G.estimate_affected(wf["daily_batch"]["size"], days=3)
            body = G.build_proposal(
                wid,
                cur["version"],
                cfg_now,
                new_cfg,
                purpose="Promote statistically winning variants: " + "; ".join(notes)[:160],
                rationale=(
                    f"Wilson lower-bound tests vs control with min_n={min_n}; guardrail checked. "
                    f"Holdout 12% retained for decay detection. Batched into one request."
                ),
                dry_run=dry,
                affected_leads=affected,
                impact_score=S.impact_from_lift(lift, affected),
            )
            # Prefer auto-apply for small shifts on Kestrel-like policies
            ok_auto, _ = G.can_auto_apply(wf["governance"], cfg_now, new_cfg)
            if ok_auto:
                # May need to step toward winners within max_shift
                stepped = self._step_allocations(cfg_now, new_cfg, wf["governance"])
                code, data = self.client.auto_apply(
                    wid,
                    {
                        "proposal_id": G.new_proposal_id("promo"),
                        "base_version": cur["version"],
                        "proposed_config": stepped,
                        "rationale": body["rationale"],
                    },
                )
                self.store.audit("auto_apply_promote", wid, {"code": code, "data": data, "notes": notes}, sim_ts=sim_ts)
                if code == 200:
                    self._obs["auto_applies"] += 1
                    exp["phase"] = "monitoring"
                    exp["promoted"] = notes
                    exp["last_promo_day"] = day
                    exp["monitor_baseline"] = self._snapshot_primary(wid, primary)
                    self.store.set_experiment(wid, "monitoring", exp)
                    return
            if self._submit(wid, body, cur, sim_ts, purpose="promote"):
                exp["phase"] = "awaiting_promote"
                exp["promoted"] = notes
                exp["last_promo_day"] = day
                self.store.set_experiment(wid, "awaiting_promote", exp)
            return

        if phase == "monitoring":
            # Decay detection on promoted arms
            if self._detect_decay(wid, primary, min_n):
                if not self._may_propose(wid, sim_ts):
                    return
                # Rollback toward broader explore / previous control
                new_cfg = copy.deepcopy(cfg_now)
                new_cfg["cohorts"] = cohorts
                control_default = (cfg_now.get("allocations") or {}).get("default") or {}
                explore = C.explore_allocations(wf["options"], ["welcome_message", "voice"], control_default, 0.5)
                allocs = {"default": explore}
                for c in cohorts:
                    allocs[c["id"]] = explore
                new_cfg["allocations"] = allocs
                body = G.build_proposal(
                    wid,
                    cur["version"],
                    cfg_now,
                    new_cfg,
                    purpose="Rollback / re-explore after winner decay",
                    rationale=(
                        f"Promoted variant's recent primary KPI dropped by ≥{DECAY_DROP} vs its post-promotion "
                        f"baseline with n≥{DECAY_MIN_N}. Re-opening exploration rather than clinging to a stale winner."
                    ),
                    dry_run={"method": "decay_rollback", "drop_threshold": DECAY_DROP},
                    affected_leads=G.estimate_affected(wf["daily_batch"]["size"], days=2),
                    impact_score=0.55,
                )
                ok_auto, _ = G.can_auto_apply(wf["governance"], cfg_now, new_cfg)
                if ok_auto:
                    stepped = self._step_allocations(cfg_now, new_cfg, wf["governance"])
                    code, data = self.client.auto_apply(
                        wid,
                        {
                            "proposal_id": G.new_proposal_id("decay"),
                            "base_version": cur["version"],
                            "proposed_config": stepped,
                            "rationale": body["rationale"],
                        },
                    )
                    self.store.audit("auto_apply_decay", wid, {"code": code, "data": data}, sim_ts=sim_ts)
                    if code == 200:
                        self._obs["auto_applies"] += 1
                        exp["phase"] = "exploring"
                        self.store.set_experiment(wid, "exploring", exp)
                        return
                if self._submit(wid, body, cur, sim_ts, purpose="decay_rollback"):
                    exp["phase"] = "awaiting_explore"
                    self.store.set_experiment(wid, "awaiting_explore", exp)

    def _find_promotions(self, wid: str, primary: str, guard: dict, min_n: int) -> list[tuple]:
        """Return list of (cohort_id, dim, winner, lift, evidence_dict)."""
        out = []
        cohorts = ["default"] + [c["id"] for c in (self.store.get_cohorts(wid) or [])]
        # Bonferroni-ish: tighten z slightly when many tests
        n_tests = max(1, len(cohorts) * 2)
        z = 1.645 + 0.1 * min(3, n_tests // 4)

        try:
            cur_cfg = self.client.config(wid)["config"]
        except Exception:
            cur_cfg = {}

        for cid in cohorts:
            for dim in ("welcome_message", "voice"):
                target_cohort = "__all__" if cid == "default" else cid
                rows = [r for r in self.store.get_call_stats(wid, dim) if r["cohort_id"] == target_cohort]
                if not rows:
                    continue
                variants = {}
                for r in rows:
                    if primary == "engagement_rate":
                        # engagement is only defined on connected calls
                        if r["connected"] <= 0:
                            continue
                        variants[r["value"]] = (r["engaged"], r["connected"])
                    else:
                        variants[r["value"]] = (r["connected"], r["dials"])
                if not variants:
                    continue
                alloc = ((cur_cfg.get("allocations") or {}).get(cid)
                         or (cur_cfg.get("allocations") or {}).get("default") or {})
                dist = alloc.get(dim) or {}
                control = max(dist, key=dist.get) if dist else None
                # Default/global promotions need stronger evidence — cohort-specific effects
                # often cancel in the aggregate and create harmful global swings.
                use_min_n = int(min_n * 1.5) if cid == "default" else min_n
                use_lift = 0.03 if cid == "default" else 0.02
                winner = S.choose_winner(variants, control, use_min_n, z=z, min_lift=use_lift)
                if not winner or winner == control:
                    continue
                guard_ok = True
                max_drop = None
                wrow = next(r for r in rows if r["value"] == winner)
                crow = next((r for r in rows if r["value"] == control), None) if control else None
                if "pickup_rate_max_drop" in guard and crow:
                    max_drop = guard["pickup_rate_max_drop"]
                    guard_ok = S.guardrail_ok(
                        wrow["connected"], wrow["dials"], crow["connected"], crow["dials"], max_drop, max(40, min_n // 2)
                    )
                if "engagement_rate_max_drop" in guard and crow:
                    max_drop = guard["engagement_rate_max_drop"]
                    guard_ok = S.guardrail_ok(
                        wrow["engaged"], wrow["connected"], crow["engaged"], crow["connected"], max_drop, max(40, min_n // 2)
                    )
                if not guard_ok:
                    self.store.audit(
                        "guardrail_blocked",
                        wid,
                        {"cohort": cid, "dim": dim, "winner": winner, "max_drop": max_drop},
                    )
                    continue
                ws, wn = variants[winner]
                cs, cn = variants.get(control, (0, 0)) if control else (0, 0)
                lift = S.rate(ws, wn) - S.rate(cs, cn) if cn else S.rate(ws, wn)
                evidence = {
                    "cohort": cid,
                    "dim": dim,
                    "winner": winner,
                    "control": control,
                    "winner_rate": round(S.rate(ws, wn), 4),
                    "control_rate": round(S.rate(cs, cn), 4) if cn else None,
                    "n_winner": wn,
                    "n_control": cn,
                    "z": z,
                }
                out.append((cid, dim, winner, lift, evidence))
        out.sort(key=lambda t: -t[3])
        return out[:6]

    def _detect_decay(self, wid: str, primary: str, min_n: int) -> bool:
        exp = self.store.get_experiment(wid) or {}
        baseline = exp.get("monitor_baseline")
        snap = self._snapshot_primary(wid, primary)
        if not snap:
            return False
        if not baseline:
            exp["monitor_baseline"] = snap
            self.store.set_experiment(wid, "monitoring", exp)
            return False

        if primary == "engagement_rate":
            recent = (snap["engaged"], snap["connected"])
            base = (baseline["engaged"], baseline["connected"])
        else:
            recent = (snap["connected"], snap["dials"])
            base = (baseline["connected"], baseline["dials"])
        new_n = recent[1] - base[1]
        if new_n >= DECAY_MIN_N:
            new_s = recent[0] - base[0]
            if S.decaying((new_s, new_n), (base[0], base[1]), DECAY_MIN_N, DECAY_DROP):
                self.store.audit("decay_detected", wid, {"baseline": baseline, "recent": {"s": new_s, "n": new_n}})
                return True

        # Also: if a heavily allocated welcome/voice arm underperforms the second-best by DECAY_DROP
        try:
            cur = self.client.config(wid)["config"]
        except Exception:
            return False
        for dim in ("welcome_message", "voice"):
            dist = ((cur.get("allocations") or {}).get("default") or {}).get(dim) or {}
            if not dist:
                continue
            top = max(dist, key=dist.get)
            if dist.get(top, 0) < 0.5:
                continue
            rows = [r for r in self.store.get_call_stats(wid, dim) if r["cohort_id"] == "__all__"]
            rates = []
            for r in rows:
                n = r["connected"] if primary == "engagement_rate" else r["dials"]
                s = r["engaged"] if primary == "engagement_rate" else r["connected"]
                if n >= DECAY_MIN_N:
                    rates.append((r["value"], S.rate(s, n), n))
            if len(rates) < 2:
                continue
            by_val = {v: (rate, n) for v, rate, n in rates}
            if top not in by_val:
                continue
            top_rate, _ = by_val[top]
            best_other = max((r for v, r, n in rates if v != top), default=None)
            if best_other is not None and best_other - top_rate >= DECAY_DROP:
                self.store.audit(
                    "decay_detected",
                    wid,
                    {"dim": dim, "top": top, "top_rate": top_rate, "best_other": best_other},
                )
                return True
        return False

    def _snapshot_primary(self, wid: str, primary: str) -> dict | None:
        rows = [r for r in self.store.get_call_stats(wid) if r["cohort_id"] == "__all__"]
        if not rows:
            return None
        dials = sum(r["dials"] for r in rows if r["dim"] == "voice")  # one dim to avoid multi-count
        connected = sum(r["connected"] for r in rows if r["dim"] == "voice")
        engaged = sum(r["engaged"] for r in rows if r["dim"] == "voice")
        return {"dials": dials, "connected": connected, "engaged": engaged, "primary": primary}

    def _step_allocations(self, old: dict, new: dict, gov: dict) -> dict:
        """Shrink allocation shifts to fit auto-apply max_shift."""
        pol = gov.get("auto_apply") or {}
        max_shift = float(pol.get("max_shift") or 0)
        out = copy.deepcopy(new)
        oa = old.get("allocations") or {}
        na = out.get("allocations") or {}
        for cohort, dims in list(na.items()):
            for dim in ("welcome_message", "voice", "caller_id"):
                if dim not in dims:
                    continue
                cur = ((oa.get(cohort) or {}).get(dim)) or ((oa.get("default") or {}).get(dim)) or dims[dim]
                dims[dim] = S.blend_toward(cur, dims[dim], max_shift)
        # Preserve schedule/prompt from new only if allowed
        allowed = set(pol.get("allowed_dims") or [])
        if "schedule" not in allowed:
            out["schedule"] = old.get("schedule")
        if "prompt_version" not in allowed:
            out["prompt_version"] = old.get("prompt_version")
        if "cohorts" not in allowed and "cohorts" in G.changed_dims(old, out):
            # cohorts often need approval — if not allowed, keep old cohorts but that may block voice changes
            # Keep new cohorts only if voice/welcome are allowed (cohorts usually need approval path)
            if "cohorts" not in allowed:
                out["cohorts"] = old.get("cohorts") or []
        return out

    def _may_propose(self, wid: str, sim_ts: str) -> bool:
        wf = self.workflows[wid]
        gov = wf["governance"]
        hours = gov.get("approver_hours") or [10, 19]
        if not G.hour_in_approver_window(sim_ts, hours):
            return False
        # Avoid proposing before 11:00 for fatigue (ops ask) — still allow 11+
        try:
            hh = int(sim_ts[11:13])
        except Exception:
            return False
        if hh < 11:
            return False
        # Check pending count via API
        try:
            recent = self.client.list_approvals(workflow_id=wid)
        except Exception:
            recent = []
        # Rough: count pending + recent approved in last day — trust max_requests by letting API reject
        pending_n = sum(1 for a in recent if a.get("status") == "pending")
        if pending_n:
            return False
        return True

    def _submit(self, wid: str, body: dict, cur: dict, sim_ts: str, purpose: str) -> bool:
        code, data = self.client.request_approval(body)
        self.store.audit(
            "proposal_submitted",
            wid,
            {"code": code, "data": data, "purpose": purpose, "proposal_id": body.get("proposal_id")},
            sim_ts=sim_ts,
        )
        if code == 200:
            self._obs["proposals"] += 1
            self.store.set_pending(wid, data["approval_id"], body["proposal_id"], sim_ts, purpose)
            return True
        if code == 409 and isinstance(data, dict) and data.get("error") == "stale_base_version":
            self.store.audit("stale_base", wid, data, sim_ts=sim_ts)
        return False


def run_controller(url: str, data_dir: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    Controller(url, data_dir).run()
