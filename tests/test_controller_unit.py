"""Unit tests for statistical and governance helpers (no simulator required)."""
from controller import stats as S
from controller import governance as G
from controller import cohorts as C


def test_wilson_bounds():
    assert 0 <= S.wilson_lower(50, 100) <= 0.5
    assert S.wilson_upper(50, 100) >= 0.5


def test_choose_winner_needs_sample():
    variants = {"A": (5, 10), "B": (8, 10)}
    assert S.choose_winner(variants, "A", min_n=80) is None


def test_choose_winner_clear_lift():
    variants = {"A": (50, 100), "B": (80, 100)}
    assert S.choose_winner(variants, "A", min_n=80) == "B"


def test_total_variation_and_blend():
    a = {"D1": 0.5, "D2": 0.5}
    b = {"D1": 0.0, "D2": 1.0}
    assert abs(S.total_variation(a, b) - 0.5) < 1e-9
    blended = S.blend_toward(a, b, max_shift=0.2)
    assert S.total_variation(a, blended) <= 0.2 + 1e-9


def test_match_cohort_order():
    cohorts = [
        {"id": "senior_ta", "where": {"age_band": ["60+"], "lang": ["ta"]}},
        {"id": "senior", "where": {"age_band": ["60+"]}},
    ]
    assert C.match_cohort({"age_band": "60+", "lang": "ta"}, cohorts) == "senior_ta"
    assert C.match_cohort({"age_band": "60+", "lang": "hi"}, cohorts) == "senior"
    assert C.match_cohort({"age_band": "18-30", "lang": "hi"}, cohorts) == "default"


def test_build_cohorts_min_share():
    leads = []
    for i in range(100):
        leads.append({"features": {"age_band": "60+", "lang": "ta", "city_tier": "1", "value_band": "mid"}})
    for i in range(100):
        leads.append({"features": {"age_band": "18-30", "lang": "hi", "city_tier": "2", "value_band": "low"}})
    cohorts = C.build_cohorts_from_history(leads, max_cohorts=6, min_share=0.08)
    assert cohorts
    assert all("id" in c and "where" in c for c in cohorts)


def test_changed_dims_and_auto_apply():
    old = {
        "cohorts": [],
        "allocations": {"default": {"caller_id": {"D1": 0.5, "D2": 0.5}, "voice": {"V1": 1}, "welcome_message": {"W1": 1}}},
        "prompt_version": "P1",
        "schedule": {"hour_weights": {}, "lead_preferred_hour": {}},
    }
    new = {
        "cohorts": [],
        "allocations": {"default": {"caller_id": {"D1": 0.2, "D2": 0.8}, "voice": {"V1": 1}, "welcome_message": {"W1": 1}}},
        "prompt_version": "P1",
        "schedule": {"hour_weights": {}, "lead_preferred_hour": {}},
    }
    assert "caller_id" in G.changed_dims(old, new)
    gov = {"auto_apply": {"enabled": True, "allowed_dims": ["caller_id", "schedule"], "max_shift": 0.5}}
    ok, problems = G.can_auto_apply(gov, old, new)
    assert ok, problems


def test_promote_allocation_holdout():
    alloc = C.promote_allocation({"V1": 1}, "V3", ["V1", "V2", "V3"], holdout=0.12)
    assert abs(alloc["V3"] - 0.88) < 1e-9
    assert abs(sum(alloc.values()) - 1.0) < 1e-9
