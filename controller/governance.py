"""Proposal packaging, auto-apply routing, and approval lifecycle helpers."""
from __future__ import annotations

import copy
import uuid
from typing import Any

from . import stats as S


def changed_dims(old: dict, new: dict) -> list[str]:
    dims = []
    if old.get("cohorts") != new.get("cohorts"):
        dims.append("cohorts")
    if old.get("prompt_version") != new.get("prompt_version"):
        dims.append("prompt_version")
    oa = old.get("allocations") or {}
    na = new.get("allocations") or {}
    for cohort in set(oa) | set(na):
        for dim in ("welcome_message", "voice", "caller_id"):
            if (oa.get(cohort) or {}).get(dim) != (na.get(cohort) or {}).get(dim):
                if dim not in dims:
                    dims.append(dim)
    os_ = old.get("schedule") or {}
    ns_ = new.get("schedule") or {}
    if os_ != ns_:
        dims.append("schedule")
    return dims


def max_alloc_shift(old: dict, new: dict) -> float:
    oa = old.get("allocations") or {}
    na = new.get("allocations") or {}
    worst = 0.0
    for cohort in set(oa) | set(na):
        for dim in ("welcome_message", "voice", "caller_id"):
            a = (oa.get(cohort) or {}).get(dim) or {}
            b = (na.get(cohort) or {}).get(dim) or {}
            if a or b:
                worst = max(worst, S.total_variation(a, b))
    return worst


def can_auto_apply(gov: dict, old: dict, new: dict) -> tuple[bool, list[str]]:
    pol = (gov or {}).get("auto_apply") or {}
    problems = []
    if not pol.get("enabled"):
        problems.append("auto-apply disabled")
        return False, problems
    dims = changed_dims(old, new)
    allowed = set(pol.get("allowed_dims") or [])
    bad = [d for d in dims if d not in allowed]
    if bad:
        problems.append(f"dims not permitted: {bad}")
    sh = max_alloc_shift(old, new)
    if sh > float(pol.get("max_shift", 0)) + 1e-9:
        problems.append(f"shift {sh:.3f} > max_shift {pol.get('max_shift')}")
    if not dims:
        problems.append("no change")
    return (len(problems) == 0), problems


def hour_in_approver_window(sim_ts: str, hours: list[int]) -> bool:
    """sim_ts like 2027-01-04T14:00:00+05:30"""
    try:
        hh = int(sim_ts[11:13])
    except Exception:
        return False
    lo, hi = hours[0], hours[1]
    return lo <= hh < hi


def new_proposal_id(prefix: str = "p") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def build_proposal(
    workflow_id: str,
    base_version: int,
    current_cfg: dict,
    proposed_cfg: dict,
    purpose: str,
    rationale: str,
    dry_run: dict,
    affected_leads: int,
    impact_score: float,
    evidence: dict | None = None,
    expires_in_hours: float | None = None,
) -> dict:
    dims = changed_dims(current_cfg, proposed_cfg)
    diff = {d: _diff_dim(current_cfg, proposed_cfg, d) for d in dims}
    body = {
        "proposal_id": new_proposal_id(purpose[:12].replace(" ", "_")),
        "workflow_id": workflow_id,
        "base_version": base_version,
        "proposed_config": proposed_cfg,
        "change_summary": purpose[:200],
        "diff": diff if diff else {"note": purpose},
        "affected_leads": int(max(0, affected_leads)),
        "dry_run": dry_run if dry_run else {"note": "historical replay unavailable"},
        "impact_score": float(min(1.0, max(0.0, impact_score))),
        "rationale": rationale,
    }
    if evidence:
        body["evidence"] = evidence
    if expires_in_hours is not None:
        body["expires_in_hours"] = expires_in_hours
    return body


def _diff_dim(old: dict, new: dict, dim: str) -> Any:
    if dim == "prompt_version":
        return {"from": old.get("prompt_version"), "to": new.get("prompt_version")}
    if dim == "cohorts":
        return {"from_ids": [c["id"] for c in old.get("cohorts") or []], "to_ids": [c["id"] for c in new.get("cohorts") or []]}
    if dim == "schedule":
        return {"schedule_changed": True}
    # allocation dims
    out = {}
    oa = old.get("allocations") or {}
    na = new.get("allocations") or {}
    for cohort in sorted(set(oa) | set(na)):
        a = (oa.get(cohort) or {}).get(dim)
        b = (na.get(cohort) or {}).get(dim)
        if a != b:
            out[cohort] = {"from": a, "to": b}
    return out


def estimate_affected(lead_count_per_day: int, days: int = 3) -> int:
    return int(lead_count_per_day * days)


def dry_run_from_stats(
    primary: str,
    control_rate: float,
    treat_rate: float,
    control_n: int,
    treat_n: int,
    guardrail_note: str,
) -> dict:
    return {
        "method": "historical_cohort_replay",
        "primary_kpi": primary,
        "control_rate": round(control_rate, 4),
        "treatment_rate": round(treat_rate, 4),
        "lift": round(treat_rate - control_rate, 4),
        "control_n": control_n,
        "treatment_n": treat_n,
        "guardrail": guardrail_note,
    }


def merge_config(base: dict, patch: dict) -> dict:
    cfg = copy.deepcopy(base)
    for k, v in patch.items():
        cfg[k] = copy.deepcopy(v)
    # Ensure required keys
    cfg.setdefault("cohorts", base.get("cohorts") or [])
    cfg.setdefault("allocations", base.get("allocations") or {"default": {}})
    cfg.setdefault("prompt_version", base.get("prompt_version", "P1"))
    cfg.setdefault("schedule", base.get("schedule") or {"hour_weights": {}, "lead_preferred_hour": {}})
    return cfg
