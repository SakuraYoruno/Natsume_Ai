# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260315_145715 UTC
- **样本数**: 20 / 20 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 2 | 2.5 | 3 | 3 | 3 | 15 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 15 |

## 主链路（trace_metrics / 墙钟）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| first_token_latency_ms | 872 | 2160.6 | 2284 | 3727 | 3745 | 15 |
| commit_ms (wall) | 3 | 17084.2 | 16682 | 30011 | 31463 | 20 |
| retrieval_latency_ms | 2 | 3.1 | 3 | 4 | 5 | 15 |
