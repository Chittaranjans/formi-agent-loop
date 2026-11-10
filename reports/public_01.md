# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.350 | +5.2% | 0.242 | +1.6% | 0.506 | +3.2% | -0% | 52 (47) |
| nw_kyc | 0.376 | +3.3% | 0.236 | -2.4% | 0.624 | +5.7% | 23% | 19 (21) |
| nw_renewal | 0.425 | +1.2% | 0.264 | +0.1% | 0.554 | +1.5% | 16% | 48 (42) |
| ALL | 0.381 | +3.3% | 0.248 | +0.3% | 0.545 | +3.0% | 11% | 119 (110) |

## Changes applied by the controller

14 applied — beneficial: 4, neutral: 8, harmful: 2

- nw_renewal v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.034 didΔ=+0.000 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=-0.001 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.037 didΔ=+0.000 → **beneficial**
- nw_renewal v3 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v3 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v3 (controller_approved) dims=['voice'] engageΔ=+0.044 didΔ=+0.000 → **beneficial**
- nw_renewal v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.042 didΔ=+0.000 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.024 didΔ=+0.000 → **harmful**
- nw_renewal v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.015 didΔ=+0.000 → **harmful**
- nw_renewal v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.003 didΔ=+0.000 → **neutral**
- ks_cancel v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.001 didΔ=+0.000 → **neutral**
- ks_cancel v5 (controller_approved) dims=['voice'] engageΔ=+0.009 didΔ=+0.000 → **neutral**
- ks_cancel v6 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v7 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 10,
  "decisions": {
    "approved": 10,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 10,
  "auto_applied": 4,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 2,
    "nw_kyc": 2,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "3": 0.213,
    "4": 0.251,
    "5": 0.266,
    "6": 0.257,
    "7": 0.269
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0