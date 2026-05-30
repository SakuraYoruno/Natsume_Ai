受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7C (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7C (2026-04-13)

## 目标

- 增强“类人体并行交互”体验：长任务执行期间，系统可主动播报阶段性进展，不等用户追问。
- 与 Stage 7A/7B 串联形成完整闭环：
  - Stage 7A：即时受理 ACK
  - Stage 7B：用户追问时快速进度回报
  - Stage 7C：系统主动进度播报

## 设计

1. 触发条件
- `source=GATEWAY`
- 消息判定为“执行意图”（非进度追问）
- 目标会话完整（`platform + conversation_id`）

2. 执行形态
- 在 `_dispatch_to_graph_worker` 里启动后台进度播报协程。
- 预热后按间隔轮询 RuntimeTaskRegistry 状态，命中 `RUNNING/RECOVERING` 时发送一次人格化进度文本。
- 主链路结束时主动停止后台播报，避免与最终回复冲突。

3. 去重与控频
- 使用 `status + target_id + progress_message` 作为签名去重。
- 限制最多播报次数，避免刷屏。

## 配置

- `HOST_GATEWAY_PROGRESS_PUSH_ENABLED`（默认 `true`）
- `HOST_GATEWAY_PROGRESS_PUSH_WARMUP_MS`（默认 `15000`）
- `HOST_GATEWAY_PROGRESS_PUSH_INTERVAL_MS`（默认 `15000`）
- `HOST_GATEWAY_PROGRESS_PUSH_MAX_UPDATES`（默认 `2`）
- `HOST_GATEWAY_PROGRESS_PUSH_WAIT_RESULT`（默认 `false`）
- `HOST_GATEWAY_PROGRESS_PUSH_TIMEOUT_MS`（默认 `4000`）

## 审计可观测

- 新增阶段事件：
  - `INPUT_PROGRESS_PUSH_PENDING`
  - `INPUT_PROGRESS_PUSH_SKIPPED`
  - `INPUT_PROGRESS_PUSHED`
- 扩展字段：
  - `progress_push_status`
  - `progress_push_reason`
  - `progress_push_preview`
  - `progress_push_updates`

## 风险与缓解

- 风险：短任务也触发播报，造成噪音。
- 缓解：预热窗口 + 最大次数限制；主链路结束立即停播。
- 风险：播报与最终回复乱序。
- 缓解：主链路完成后先停播，再发送最终回复。
