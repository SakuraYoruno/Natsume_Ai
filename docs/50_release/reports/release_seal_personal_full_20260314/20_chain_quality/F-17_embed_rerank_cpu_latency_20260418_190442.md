受众：PROJECT
标题：F-17 嵌入/重排 CPU 实测耗时

# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_190442 UTC
- **样本数**: 2 / 2 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（客户端墙钟 + 服务端 audit_refs）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| first_event_ms | 10 | 1425.8 | 1426 | 2700 | 2842 | 2 |
| first_render_ms | 804 | 2272.3 | 2272 | 3593 | 3740 | 2 |
| server_latency_ms | 890 | 898.0 | 898 | 905 | 906 | 2 |
| first_token_latency_ms | 754 | 814.0 | 814 | 868 | 874 | 2 |
| commit_ms (wall) | 901 | 2325.0 | 2325 | 3607 | 3749 | 2 |
| wall_minus_server_commit_ms | 11 | 1427.0 | 1427 | 2702 | 2843 | 2 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## ChatAgent 分段（audit_refs.timings_ms）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| prompt_build_ms | 0 | 0.5 | 0 | 1 | 1 | 2 |
| llm_runtime_resolve_ms | 2 | 3.0 | 3 | 4 | 4 | 2 |
| stream_after_first_token_ms | 10 | 51.0 | 51 | 88 | 92 | 2 |
| response_intelligence_ms | 0 | 0.0 | 0 | 0 | 0 | 2 |
| sanitize_egress_ms | 0 | 0.0 | 0 | 0 | 0 | 2 |
| precommit_log_ms | 0 | 0.0 | 0 | 0 | 0 | 2 |
