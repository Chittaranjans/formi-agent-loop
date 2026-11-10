# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.270 | +9.2% | 0.190 | +3.5% | 0.500 | +5.0% | 37% | 49 (330) |
| nw_kyc | 0.379 | +1.2% | 0.241 | -0.4% | 0.606 | +2.4% | -16% | 38 (31) |
| nw_renewal | 0.412 | -3.3% | 0.263 | +0.3% | 0.530 | -3.2% | -26% | 62 (55) |
| ALL | 0.341 | +2.0% | 0.225 | +1.4% | 0.534 | +0.8% | 8% | 149 (416) |

## Changes applied by the controller

23 applied — beneficial: 8, neutral: 15

- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.012 didΔ=+0.000 → **beneficial**
- nw_renewal v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- nw_renewal v3 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v3 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=-0.010 didΔ=+0.000 → **neutral**
- ks_cancel v4 (controller_approved) dims=['voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_renewal v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.030 didΔ=+0.000 → **beneficial**
- ks_cancel v6 (controller_approved) dims=['prompt_version'] engageΔ=+0.102 didΔ=+0.000 → **beneficial**
- ks_cancel v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nw_renewal v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.005 didΔ=+0.000 → **neutral**
- nw_renewal v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.013 didΔ=+0.000 → **beneficial**
- nw_renewal v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.007 didΔ=+0.000 → **neutral**
- ks_cancel v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.067 → **beneficial**
- ks_cancel v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.063 → **beneficial**
- ks_cancel v10 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.002 → **neutral**
- ks_cancel v11 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.001 → **neutral**
- ks_cancel v12 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v13 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.001 → **neutral**
- ks_cancel v14 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- nw_renewal v9 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.003 didΔ=+0.000 → **neutral**
- nw_kyc v5 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 17,
  "decisions": {
    "approved": 14,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 14,
  "auto_applied": 9,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 2,
    "nw_kyc": 2,
    "ks_cancel": 2
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.496,
      "3": 0.493,
      "4": 0.506,
      "5": 0.489,
      "6": 0.37,
      "7": 0.388,
      "8": 0.385,
      "9": 0.365
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 210,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "2": 0.164,
    "3": 0.547,
    "4": 0.643,
    "5": 0.448,
    "6": 0.407,
    "7": 0.418,
    "8": 0.411,
    "9": 0.4
  }
}
```

Credits used: 9 · validation runs: 3 · LLM fix calls: 1