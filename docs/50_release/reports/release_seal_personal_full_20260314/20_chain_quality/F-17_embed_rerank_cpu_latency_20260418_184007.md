# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_184007 UTC
- **样本数**: 4 / 4 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（trace_metrics / 墙钟）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| first_token_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| commit_ms (wall) | 1199 | 13166.6 | 13776 | 22844 | 23915 | 4 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
