受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7B (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7B (2026-04-13)

## 目标

- 补齐“类人体并行交互”中的进度回报能力：用户中途询问进度时，系统快速回报当前执行状态。
- 对 Gateway 进度询问建立轻量 fastpath，避免每次都走完整 Cortex 执行链路。
- 与 Stage 7A 形成闭环：
  - 收到任务 -> 即时 ACK
  - 任务执行中被询问 -> 立即回进度
  - 执行完成 -> 正常最终回复

## 设计

1. 触发判定
- 复用进度询问语义判定（`_looks_like_progress_query_for_ack_skip`）。
- 仅 `source=GATEWAY` 生效。

2. 数据来源
- 读取 `RuntimeTaskRegistry.summarize_for_chat(...)`。
- 取当前任务状态、目标 skill、进展摘要、耗时、结果摘要。

3. 文案渲染
- 通过 `chat_agent.render_quick_gateway_progress_text(...)` 渲染人格化短回复。
- 模板键写入 `chat_agent.runtime_texts`，支持运行中/完成/失败/无任务四态。

4. 执行策略
- fastpath 发送成功：直接完成本次请求（短路主执行链路）。
- fastpath 发送失败：回落原 `_dispatch_to_graph` 主链路。

## 配置

- `HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED`（默认 `true`）
- `HOST_GATEWAY_PROGRESS_FASTPATH_WAIT_RESULT`（默认 `true`）
- `HOST_GATEWAY_PROGRESS_FASTPATH_TIMEOUT_MS`（默认 `6000`）

## 审计与可观测

- 新增 `INPUT_PROGRESS_FASTPATH` 审计事件。
- 扩展 gateway payload 字段：
  - `progress_fastpath_status`
  - `progress_fastpath_latency_ms`
  - `progress_fastpath_preview`
  - `progress_fastpath_reason`

## 风险与缓解

- 风险：误判普通对话为进度询问。
- 缓解：只在命中进度语义且 Gateway 场景触发，且发送失败自动回落主链路。
- 风险：任务注册表无数据时回复空洞。
- 缓解：提供人格化 `quick_progress_missing` 兜底文案。
