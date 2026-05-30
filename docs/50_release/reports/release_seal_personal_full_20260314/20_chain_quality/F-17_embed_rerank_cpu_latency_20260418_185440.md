受众：PROJECT
标题：F-17 嵌入/重排 CPU 实测耗时

# F-17 嵌入/重排 CPU 实测耗时

- **设备**: CPU（embed/rerank 未使用 GPU）
- **时间**: 20260418_185440 UTC
- **样本数**: 2 / 2 成功

## 检索子阶段（retrieval_event_log）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| embed_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| rerank_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |

## 主链路（trace_metrics / 墙钟）

| 指标 | min | avg | p50 | p95 | max | n |
|------|-----|-----|-----|-----|-----|---|
| first_event_ms | 3172 | 9472.3 | 9472 | 15143 | 15773 | 2 |
| first_render_ms | 11916 | 18056.5 | 18057 | 23583 | 24197 | 2 |
| first_token_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
| commit_ms (wall) | 11923 | 22095.9 | 22096 | 31252 | 32269 | 2 |
| retrieval_latency_ms | 0 | 0.0 | 0 | 0 | 0 | 0 |
