受众：PROJECT
标题：F-17 嵌入/重排 CPU 实测耗时

# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_191956 UTC
- **样本数**: 8 / 8 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（客户端墙钟 + 服务端 audit_refs）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| response_started_ms | 8 | 3816.0 | 3492 | 8501 | 9043 | 8 |
| first_event_ms | 10 | 4239.1 | 3494 | 10231 | 10869 | 8 |
| first_render_ms | 1145 | 8332.7 | 9540 | 12114 | 12662 | 8 |
| server_latency_ms | 1246 | 8236.9 | 8164 | 12000 | 12086 | 8 |
| first_token_latency_ms | 1089 | 2200.2 | 2116 | 3145 | 3195 | 8 |
| commit_ms (wall) | 1255 | 12429.9 | 12177 | 20020 | 21502 | 8 |
| wall_minus_server_commit_ms | 9 | 4193.0 | 4390 | 8153 | 9416 | 8 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## ChatAgent 分段（audit_refs.timings_ms）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| prompt_build_ms | 0 | 0.0 | 0 | 0 | 0 | 8 |
| llm_runtime_resolve_ms | 3 | 3.9 | 4 | 5 | 5 | 8 |
| stream_after_first_token_ms | 6 | 3196.6 | 3150 | 7269 | 7562 | 8 |
| response_intelligence_ms | 0 | 0.0 | 0 | 0 | 0 | 8 |
| sanitize_egress_ms | 0 | 0.1 | 0 | 1 | 1 | 8 |
| precommit_log_ms | 0 | 0.4 | 0 | 2 | 3 | 8 |
