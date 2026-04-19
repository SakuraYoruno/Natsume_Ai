# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_193227 UTC
- **样本数**: 4 / 4 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（客户端墙钟 + 服务端 audit_refs）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| response_started_ms | 8 | 1718.5 | 1629 | 3554 | 3608 | 4 |
| first_event_ms | 11 | 1721.5 | 1632 | 3557 | 3611 | 4 |
| first_render_ms | 700 | 3163.6 | 3805 | 4288 | 4345 | 4 |
| server_latency_ms | 734 | 1584.2 | 872 | 3428 | 3859 | 4 |
| first_token_latency_ms | 646 | 697.0 | 692 | 748 | 757 | 4 |
| commit_ms (wall) | 745 | 3305.1 | 3941 | 4507 | 4594 | 4 |
| wall_minus_server_commit_ms | 11 | 1720.9 | 1631 | 3557 | 3610 | 4 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## ChatAgent 分段（audit_refs.timings_ms）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| prompt_build_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
| llm_runtime_resolve_ms | 3 | 4.0 | 4 | 5 | 5 | 4 |
| stream_after_first_token_ms | 42 | 138.5 | 134 | 241 | 245 | 4 |
| response_intelligence_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
| sanitize_egress_ms | 0 | 0.0 | 0 | 0 | 0 | 4 |
| precommit_log_ms | 0 | 0.8 | 1 | 1 | 1 | 4 |
