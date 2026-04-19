# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_190649 UTC
- **样本数**: 1 / 1 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（客户端墙钟 + 服务端 audit_refs）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| response_started_ms | 10 | 10.0 | 10 | 10 | 10 | 1 |
| first_event_ms | 13 | 12.8 | 13 | 13 | 13 | 1 |
| first_render_ms | 1446 | 1445.9 | 1446 | 1446 | 1446 | 1 |
| server_latency_ms | 1569 | 1569.0 | 1569 | 1569 | 1569 | 1 |
| first_token_latency_ms | 1409 | 1409.0 | 1409 | 1409 | 1409 | 1 |
| commit_ms (wall) | 1617 | 1617.5 | 1617 | 1617 | 1617 | 1 |
| wall_minus_server_commit_ms | 48 | 48.5 | 48 | 48 | 48 | 1 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## ChatAgent 分段（audit_refs.timings_ms）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| prompt_build_ms | 0 | 0.0 | 0 | 0 | 0 | 1 |
| llm_runtime_resolve_ms | 4 | 4.0 | 4 | 4 | 4 | 1 |
| stream_after_first_token_ms | 132 | 132.0 | 132 | 132 | 132 | 1 |
| response_intelligence_ms | 0 | 0.0 | 0 | 0 | 0 | 1 |
| sanitize_egress_ms | 0 | 0.0 | 0 | 0 | 0 | 1 |
| precommit_log_ms | 38 | 38.0 | 38 | 38 | 38 | 1 |
