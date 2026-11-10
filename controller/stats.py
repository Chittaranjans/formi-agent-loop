"""Statistical helpers for winner selection. Deterministic — no LLM in the loop."""
from __future__ import annotations

import math


def wilson_lower(successes: int, n: int, z: float = 1.645) -> float:
    """Lower bound of Wilson score interval (default ~90% one-sided)."""
    if n <= 0:
        return 0.0
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def wilson_upper(successes: int, n: int, z: float = 1.645) -> float:
    if n <= 0:
        return 1.0
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return min(1.0, (centre + margin) / denom)


def rate(successes: int, n: int) -> float:
    return successes / n if n else 0.0


def beats_control(
    treat_s: int,
    treat_n: int,
    ctrl_s: int,
    ctrl_n: int,
    min_n: int,
    z: float = 1.645,
    min_lift: float = 0.02,
) -> bool:
    """Treatment wins if Wilson lower exceeds control point estimate by min_lift and n is enough."""
    if treat_n < min_n or ctrl_n < min_n:
        return False
    lift = rate(treat_s, treat_n) - rate(ctrl_s, ctrl_n)
    if lift < min_lift:
        return False
    return wilson_lower(treat_s, treat_n, z) > rate(ctrl_s, ctrl_n) + (min_lift * 0.25)


def guardrail_ok(
    treat_guard_s: int,
    treat_guard_n: int,
    ctrl_guard_s: int,
    ctrl_guard_n: int,
    max_drop: float,
    min_n: int,
) -> bool:
    """True if treatment does not drop the guardrail KPI by more than max_drop."""
    if treat_guard_n < min_n or ctrl_guard_n < min_n:
        return True  # insufficient evidence of harm — allow continue collecting
    drop = rate(ctrl_guard_s, ctrl_guard_n) - rate(treat_guard_s, treat_guard_n)
    return drop <= max_drop + 1e-9


def choose_winner(
    variants: dict[str, tuple[int, int]],
    control: str | None,
    min_n: int,
    z: float = 1.645,
    min_lift: float = 0.02,
) -> str | None:
    """
    variants: value -> (successes, trials) for the primary KPI.
    Returns the best variant that statistically beats control, or None.
    """
    if not variants:
        return None
    if control is None or control not in variants:
        eligible = [(v, s, n) for v, (s, n) in variants.items() if n >= min_n]
        if len(eligible) < 2:
            return None
        ranked = sorted(eligible, key=lambda t: wilson_lower(t[1], t[2], z), reverse=True)
        best_v, best_s, best_n = ranked[0]
        second_v, second_s, second_n = ranked[1]
        if rate(best_s, best_n) - rate(second_s, second_n) < min_lift:
            return None
        if wilson_lower(best_s, best_n, z) <= rate(second_s, second_n):
            return None
        return best_v

    cs, cn = variants[control]
    best = None
    best_lift = 0.0
    for v, (s, n) in variants.items():
        if v == control:
            continue
        if not beats_control(s, n, cs, cn, min_n, z, min_lift=min_lift):
            continue
        lift = rate(s, n) - rate(cs, cn)
        if lift > best_lift:
            best_lift = lift
            best = v
    return best


def decaying(
    recent: tuple[int, int],
    baseline: tuple[int, int],
    min_n: int,
    drop: float = 0.03,
) -> bool:
    """True if recent rate dropped vs baseline by more than `drop` with enough samples."""
    rs, rn = recent
    bs, bn = baseline
    if rn < min_n or bn < min_n:
        return False
    return rate(bs, bn) - rate(rs, rn) >= drop


def total_variation(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    sa = sum(a.values()) or 1.0
    sb = sum(b.values()) or 1.0
    return 0.5 * sum(abs(a.get(k, 0) / sa - b.get(k, 0) / sb) for k in keys)


def normalize(weights: dict[str, float]) -> dict[str, float]:
    s = sum(max(0.0, w) for w in weights.values())
    if s <= 0:
        n = len(weights) or 1
        return {k: 1.0 / n for k in weights}
    return {k: max(0.0, w) / s for k, w in weights.items()}


def blend_toward(current: dict[str, float], target: dict[str, float], max_shift: float) -> dict[str, float]:
    """Move current toward target without exceeding TV distance max_shift."""
    cur = normalize(current)
    tgt = normalize(target)
    keys = set(cur) | set(tgt)
    cur = {k: cur.get(k, 0.0) for k in keys}
    tgt = {k: tgt.get(k, 0.0) for k in keys}
    tv = total_variation(cur, tgt)
    if tv <= max_shift + 1e-12:
        return tgt
    # interpolate
    alpha = max_shift / tv
    mixed = {k: cur[k] + alpha * (tgt[k] - cur[k]) for k in keys}
    return normalize(mixed)


def impact_from_lift(lift: float, affected: int, scale: int = 5000) -> float:
    """Honest-ish impact in [0,1]: larger lift and more affected leads → higher score."""
    if affected <= 0 or lift <= 0:
        return 0.05
    raw = min(1.0, abs(lift) * 8.0) * min(1.0, affected / scale)
    return round(max(0.05, min(0.95, raw)), 3)
