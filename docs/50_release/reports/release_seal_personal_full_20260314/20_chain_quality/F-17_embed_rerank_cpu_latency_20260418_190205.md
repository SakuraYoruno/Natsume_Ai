# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_190205 UTC
- **样本数**: 2 / 2 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（客户端墙钟 + 服务端 audit_refs）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| first_event_ms | 4632 | 6547.0 | 6547 | 8270 | 8462 | 2 |
| first_render_ms | 9882 | 12809.7 | 12810 | 15445 | 15738 | 2 |
| server_latency_ms | 11671 | 15285.0 | 15285 | 18538 | 18899 | 2 |
| first_token_latency_ms | 2484 | 2679.0 | 2679 | 2854 | 2874 | 2 |
| commit_ms (wall) | 13962 | 19096.5 | 19097 | 23718 | 24231 | 2 |
| wall_minus_server_commit_ms | 2291 | 3811.5 | 3812 | 5180 | 5332 | 2 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## ChatAgent 分段（audit_refs.timings_ms）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| prompt_build_ms | 0 | 0.0 | 0 | 0 | 0 | 2 |
| llm_runtime_resolve_ms | 4 | 4.5 | 4 | 5 | 5 | 2 |
| stream_after_first_token_ms | 6812 | 8762.0 | 8762 | 10517 | 10712 | 2 |
| response_intelligence_ms | 0 | 0.0 | 0 | 0 | 0 | 2 |
| sanitize_egress_ms | 0 | 0.0 | 0 | 0 | 0 | 2 |
| precommit_log_ms | 0 | 0.0 | 0 | 0 | 0 | 2 |
