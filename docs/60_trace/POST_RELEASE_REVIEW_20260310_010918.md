# Post Release 24h Review

- generated_at: 2026-03-09T17:09:18+00:00
- release_id: codex-20260309
- released_at: 2026-03-08T16:00:00+00:00
- age_hours: 25.16
- review_status: COMPLETE

## Summary

- gate_decision: ROLLBACK_REQUIRED
- rollback_required: True
- alert_count: 2

## Failed Conditions

- `summary.alert_count`: observed=2.0, threshold=0.0 (<=)
- `retrieval.degraded_rate_pct`: observed=21.91, threshold=20.0 (<=)
- `governor.failure_rate_pct`: observed=24.22, threshold=15.0 (<=)

## Findings

- Rollback gate requires rollback based on current thresholds.
- Weekly stability report still has 2 active alerts.

## Actions

- Keep HOST_CHAT_FORCE_CLOUD=true and HOST_CHAT_GRAY_ENABLED=false until gate turns PASS.
- Prioritize reducing timeout/degraded/failure rates before expanding gray percentage.
