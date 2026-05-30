受众：PROJECT
标题：F-17 嵌入/重排 CPU 实测耗时

# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_192508 UTC
- **样本数**: 4 / 4 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（客户端墙钟 + 服务端 audit_refs）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| response_started_ms | 9 | 2657.4 | 2142 | 5723 | 6336 | 4 |
| first_event_ms | 12 | 2660.7 | 2146 | 5726 | 6339 | 4 |
| first_render_ms | 1370 | 5933.2 | 6300 | 9259 | 9762 | 4 |
| server_latency_ms | 1506 | 4766.0 | 5006 | 7260 | 7547 | 4 |
| first_token_latency_ms | 1319 | 1596.8 | 1547 | 1940 | 1974 | 4 |
| commit_ms (wall) | 1520 | 7461.6 | 8100 | 11774 | 12128 | 4 |
| wall_minus_server_commit_ms | 14 | 2695.6 | 3094 | 4514 | 4581 | 4 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## ChatAgent 分段（audit_refs.timings_ms）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| prompt_build_ms | 0 | 0.2 | 0 | 1 | 1 | 4 |
| llm_runtime_resolve_ms | 4 | 4.2 | 4 | 5 | 5 | 4 |
| stream_after_first_token_ms | 6 | 1451.8 | 1100 | 3368 | 3600 | 4 |
| response_intelligence_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
| sanitize_egress_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
| precommit_log_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
