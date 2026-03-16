# Release Rollback Gate

- generated_at: 2026-03-10T01:06:48
- stability_report: D:\Natsume_Ai_\docs\60_trace\WEEKLY_STABILITY_REPORT_20260310_005912.json
- decision: ROLLBACK_REQUIRED
- rollback_required: True

## Failed Conditions

- `summary.alert_count`: observed=2.0, threshold=0.0 (<=)
- `retrieval.degraded_rate_pct`: observed=21.91, threshold=20.0 (<=)
- `governor.failure_rate_pct`: observed=24.22, threshold=15.0 (<=)

## Rollback Commands

- Force cloud route: `$env:HOST_CHAT_FORCE_CLOUD='true'; $env:HOST_CHAT_HOST_8B_ENABLED='false'`
- Disable gaming edge chat: `$env:HOST_GAMING_EDGE_CHAT_ENABLED='false'`
- Disable gray rollout: `$env:HOST_CHAT_GRAY_ENABLED='false'; $env:HOST_CHAT_GRAY_PERCENT='100'`
