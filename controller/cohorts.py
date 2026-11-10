"""One shared cohort object per workflow — used by detection, experiments, and supervisor view."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


FEATURE_KEYS = ("age_band", "lang", "city_tier", "value_band")


def match_cohort(features: dict, cohorts: list[dict]) -> str:
    """First matching cohort wins; else 'default'. Same semantics as the simulator."""
    for c in cohorts:
        where = c.get("where") or {}
        ok = True
        for k, allowed in where.items():
            if features.get(k) not in allowed:
                ok = False
                break
        if ok:
            return c["id"]
    return "default"


def _count_features(leads: list[dict]) -> dict[str, Counter]:
    out = {k: Counter() for k in FEATURE_KEYS}
    for lead in leads:
        feats = lead.get("features") or lead
        for k in FEATURE_KEYS:
            v = feats.get(k)
            if v is not None:
                out[k][v] += 1
    return out


def build_cohorts_from_history(
    leads: list[dict],
    languages: list[str] | None = None,
    max_cohorts: int = 8,
    min_share: float = 0.08,
) -> list[dict]:
    """
    Form coarse, stable cohorts from observed lead volume.

    Principle: prefer age_band × lang intersections that are large enough to experiment on.
    Cap at max_cohorts so we don't fragment traffic. Order: more specific first.
    """
    if not leads:
        # sensible defaults when history is empty
        cohorts = []
        if languages and "ta" in languages:
            cohorts.append({"id": "senior_ta", "where": {"age_band": ["60+"], "lang": ["ta"]}})
        cohorts.append({"id": "senior", "where": {"age_band": ["60+", "46-60"]}})
        cohorts.append({"id": "young", "where": {"age_band": ["18-30", "31-45"]}})
        return cohorts[:max_cohorts]

    n = len(leads)
    counts = _count_features(leads)
    age_vals = [a for a, c in counts["age_band"].most_common() if c / n >= min_share]
    lang_vals = [a for a, c in counts["lang"].most_common() if c / n >= min_share]

    # Cross tabs for age × lang
    cross: Counter = Counter()
    for lead in leads:
        feats = lead.get("features") or lead
        cross[(feats.get("age_band"), feats.get("lang"))] += 1

    candidates: list[tuple[float, dict]] = []
    for (age, lang), c in cross.items():
        if age is None or lang is None:
            continue
        share = c / n
        if share < min_share:
            continue
        cid = f"age_{age}_lang_{lang}".replace("+", "p").replace("-", "_")
        candidates.append((share, {"id": cid, "where": {"age_band": [age], "lang": [lang]}}))

    # Add large single-feature cohorts as fallbacks (evaluated later = lower priority)
    for age in age_vals:
        share = counts["age_band"][age] / n
        cid = f"age_{age}".replace("+", "p").replace("-", "_")
        candidates.append((share * 0.5, {"id": cid, "where": {"age_band": [age]}}))

    for lang in lang_vals:
        if len(lang_vals) <= 1:
            continue
        share = counts["lang"][lang] / n
        cid = f"lang_{lang}"
        candidates.append((share * 0.4, {"id": cid, "where": {"lang": [lang]}}))

    # Prefer larger specific cohorts; dedupe by id
    candidates.sort(key=lambda t: -t[0])
    seen = set()
    out: list[dict] = []
    for _, cohort in candidates:
        if cohort["id"] in seen:
            continue
        seen.add(cohort["id"])
        out.append(cohort)
        if len(out) >= max_cohorts:
            break
    return out


def empty_allocations(options: dict) -> dict[str, dict[str, float]]:
    return {
        "welcome_message": {options["welcome_message"][0]: 1.0},
        "voice": {options["voice"][0]: 1.0},
        "caller_id": {options["caller_id"][0]: 1.0},
    }


def explore_allocations(
    options: dict,
    dims: list[str],
    control: dict[str, dict[str, float]] | None = None,
    explore_weight: float = 0.5,
) -> dict[str, dict[str, float]]:
    """
    Split traffic between current control and uniform exploration on selected dims.
    Independent sampling across dims matches the simulator.
    """
    out: dict[str, dict[str, float]] = {}
    for dim in ("welcome_message", "voice", "caller_id"):
        opts = options[dim]
        if dim not in dims:
            if control and dim in control:
                out[dim] = dict(control[dim])
            else:
                out[dim] = {opts[0]: 1.0}
            continue
        ctrl = (control or {}).get(dim) or {opts[0]: 1.0}
        # Keep some mass on current winners, rest uniform explore
        explore = {o: explore_weight / len(opts) for o in opts}
        exploit = {o: (1 - explore_weight) * ctrl.get(o, 0.0) / (sum(ctrl.values()) or 1) for o in opts}
        # Ensure every option gets some explore mass
        merged = {o: explore.get(o, 0) + exploit.get(o, 0) for o in opts}
        # If control had options not in opts, ignore
        s = sum(merged.values()) or 1
        out[dim] = {o: merged[o] / s for o in opts}
    return out


def promote_allocation(
    current: dict[str, float],
    winner: str,
    options: list[str],
    holdout: float = 0.1,
) -> dict[str, float]:
    """Put most traffic on winner, keep a small holdout for decay detection."""
    others = [o for o in options if o != winner]
    if not others or holdout <= 0:
        return {winner: 1.0}
    each = holdout / len(others)
    return {winner: 1.0 - holdout, **{o: each for o in others}}


def config_with_cohorts(
    base: dict,
    cohorts: list[dict],
    allocations: dict[str, dict],
    schedule: dict | None = None,
    prompt_version: str | None = None,
) -> dict:
    """Build a full config object. allocations maps cohort_id -> dim -> weights."""
    cfg = {
        "cohorts": cohorts,
        "allocations": allocations,
        "prompt_version": prompt_version if prompt_version is not None else base.get("prompt_version", "P1"),
        "schedule": schedule if schedule is not None else base.get("schedule", {"hour_weights": {}, "lead_preferred_hour": {}}),
    }
    return cfg


def summarize_supervisor_view(
    workflow_id: str,
    cohorts: list[dict],
    experiment: dict | None,
    pending: dict | None,
    recent_changes: list[dict],
    kpi_snapshot: dict[str, Any],
) -> dict:
    """Single consolidated view object — one place, not fifty alerts."""
    return {
        "workflow_id": workflow_id,
        "cohorts": [{"id": c["id"], "where": c.get("where", {})} for c in cohorts],
        "experiment": experiment,
        "pending_approval": pending,
        "recent_changes": recent_changes[:10],
        "kpis": kpi_snapshot,
        "attention": _attention(experiment, pending, kpi_snapshot),
    }


def _attention(experiment, pending, kpis) -> list[str]:
    notes = []
    if pending:
        notes.append(f"Awaiting approval: {pending.get('purpose')}")
    if experiment and experiment.get("state") == "exploring":
        notes.append("Experiment running — collecting evidence")
    if experiment and experiment.get("state") == "monitoring":
        notes.append("Monitoring promoted winner for decay")
    if kpis.get("halluc_rate", 0) > 0.02:
        notes.append("Elevated hallucination rate")
    if kpis.get("worst_did"):
        notes.append(f"Caller ID concern: {kpis['worst_did']}")
    return notes or ["Healthy — no action needed"]
