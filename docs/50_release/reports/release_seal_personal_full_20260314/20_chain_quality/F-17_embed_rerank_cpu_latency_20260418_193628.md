受众：PROJECT
标题：F-17 嵌入/重排 CPU 实测耗时

# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_193628 UTC
- **样本数**: 4 / 4 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（客户端墙钟 + 服务端 audit_refs）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| response_started_ms | 14 | 20.0 | 21 | 25 | 25 | 4 |
| first_event_ms | 19 | 23.8 | 24 | 28 | 28 | 4 |
| first_render_ms | 1052 | 2844.6 | 3357 | 3591 | 3614 | 4 |
| server_latency_ms | 1083 | 2841.0 | 3345 | 3570 | 3591 | 4 |
| first_token_latency_ms | 1003 | 2788.2 | 3296 | 3534 | 3557 | 4 |
| commit_ms (wall) | 1110 | 2864.5 | 3365 | 3596 | 3617 | 4 |
| wall_minus_server_commit_ms | 19 | 23.5 | 24 | 27 | 27 | 4 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## ChatAgent 分段（audit_refs.timings_ms）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| prompt_build_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
| llm_runtime_resolve_ms | 4 | 9.2 | 4 | 21 | 24 | 4 |
| stream_after_first_token_ms | 4 | 19.5 | 9 | 49 | 56 | 4 |
| response_intelligence_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
| sanitize_egress_ms | 0 | 0.2 | 0 | 1 | 1 | 4 |
| precommit_log_ms | 0 | 0.2 | 0 | 1 | 1 | 4 |
