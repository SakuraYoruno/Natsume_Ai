受众：MASTER
标题：Long-Term Metric Dashboard

# Long-Term Metric Dashboard

- generated_at: 2026-03-09T17:45:26+00:00
- window_start: 2026-02-07T17:45:26.011000+00:00
- window_end: 2026-03-09T17:45:26.011000+00:00
- days: 30
- status: WARN
- alert_count: 3

## Quality

- governor_approve_rate_pct: 75.64
- retrieval_failure_rate_pct: 0.0
- qa_violation_count: 0

## Latency

- retrieval_p95_ms: 10312
- llm_node_p95_ms: None
- graph_total_p95_ms: 68890

## Cost

- cloud_call_ratio_pct: 59.52
- fallback_used_rate_pct: 0.0

## Availability

- required_up_rate_pct: 95.95
- error_count: 265
- llm_degraded_rate_pct: 47.6

## Active Alerts

- `QUALITY_GOVERNOR_APPROVE_RATE_LOW` (observed=75.64, threshold=80.0)
- `LATENCY_GRAPH_P95_HIGH` (observed=68890, threshold=4000)
- `AVAILABILITY_REQUIRED_UP_RATE_LOW` (observed=95.95, threshold=99.0)
