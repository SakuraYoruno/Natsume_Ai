# Skill-First Humanoid Execution Plan Stage 7A (2026-04-13)

## 目标

- 将 Gateway 入站任务型请求改造为“先人格化 ACK，再进入执行链路，再给最终结果”。
- ACK 不走重型 LLM 推理，采用 ChatAgent 运行时模板快速渲染，避免冷冰冰“收到”。
- 保持现有 Skill-First 执行链路不变，只做前置体验增强与可观测留痕。

## 设计约束

- 不阻塞主执行链路：ACK 采用异步 fire-and-forget。
- 仅对“执行意图”触发 ACK；对“进度追问”跳过 ACK，避免重复噪音。
- ACK 仍通过 Gateway 正常下发，纳入 input ledger 审计事件。

## 链路变更

1. `input_routes.receive_input` 接收 Gateway 消息后进入 `_dispatch_to_graph`。
2. `_dispatch_to_graph` 在 `INPUT_ACCEPTED` 后新增 `_maybe_schedule_gateway_immediate_ack`。
3. 命中执行意图时，异步触发 `_deliver_gateway_immediate_ack`：
   - 使用 `chat_agent.render_quick_gateway_ack_text(...)`
   - 使用 `post_gateway_reply(..., msg_type=text)` 发出即时 ACK
4. 原有 `_dispatch_to_graph_worker -> cortex_orchestrator -> _deliver_gateway_reply` 保持不变。

## 配置项

- `HOST_GATEWAY_IMMEDIATE_ACK_ENABLED`（默认 `true`）
- `HOST_GATEWAY_IMMEDIATE_ACK_WAIT_RESULT`（默认 `false`）
- `HOST_GATEWAY_IMMEDIATE_ACK_TIMEOUT_MS`（默认 `2500`）

## 模板扩展

- 在 `chat_agent.runtime_texts` 增加：
  - `quick_ack_low_energy`
  - `quick_ack_close`
  - `quick_ack_warm`
  - `quick_ack_stranger`
  - `quick_ack_default`

## 风险与缓解

- 风险：规则误判导致 ACK 过多或过少。
- 缓解：意图判定拆分为 `progress_query` 与 `action_intent`，并支持环境变量一键关闭。
- 风险：ACK 发送失败影响主链路。
- 缓解：ACK 异步失败只记录审计，不中断主执行。

## 验收

- 单测覆盖 ACK 文本渲染优先级、默认兜底、长度限制。
- 单测覆盖 Gateway ACK 触发条件与调度接线。
- 保证原 `input_routes_worker` 回归测试通过。
