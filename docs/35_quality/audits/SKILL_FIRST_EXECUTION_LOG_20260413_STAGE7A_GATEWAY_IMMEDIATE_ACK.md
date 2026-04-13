# Skill-First Execution Log 2026-04-13 Stage 7A

## Scope

- Objective: 落地“Gateway 即时人格化 ACK”能力，解决执行前回复机械、缺乏人格温度的问题。
- Non-goal: 不改动 Skill-First 主执行路径与最终回复生成策略。

## Implemented Changes

- `host_brain/app/chat_agent.py`
  - 使用既有 `render_quick_gateway_ack_text(...)` 作为低延迟 ACK 渲染入口（本阶段接线使用）。
- `host_brain/app/input_routes.py`
  - 新增 ACK 判定与调度：
    - `_looks_like_progress_query_for_ack_skip`
    - `_looks_like_execution_intent_for_ack`
    - `_should_send_gateway_immediate_ack`
    - `_maybe_schedule_gateway_immediate_ack`
    - `_deliver_gateway_immediate_ack`
  - 在 `_dispatch_to_graph` 的 `ACCEPTED` 后接入 ACK 调度（仅 Gateway）。
  - 扩展 input ledger gateway 字段：
    - `immediate_ack_status`
    - `immediate_ack_latency_ms`
    - `immediate_ack_preview`
    - `immediate_ack_reason`
- `host_brain/app/prompt_template_defs.py`
  - 在 `chat_agent.runtime_texts` 两处定义中补齐 `quick_ack_*` 模板键。
- `.env.example`
  - 新增 ACK 开关与超时配置说明。

## Test Evidence

- Command:
  - `pytest -q tests/unit/test_chat_agent_quick_ack.py tests/unit/test_input_routes_worker.py`
- Result:
  - `11 passed in 0.72s`
- Command:
  - `pytest -q tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py tests/unit/test_host_14b_runtime_route.py`
- Result:
  - `17 passed in 0.86s`

## Regression Notes

- ACK 发送采用异步任务，不阻塞主执行 worker。
- ACK 判定默认跳过“进度追问”类文本，减少重复回执噪声。
- ACK 失败仅记审计，不会导致请求主链路失败。

## Follow-up

- 下一阶段建议补集成烟测：
  - 真实 Gateway 入站消息 -> `INPUT_ACK_PENDING/ACKED` 审计事件可见
  - ACK 与最终回复顺序稳定（先 ACK 后结果）
  - 高频场景下 ACK 发送成功率与平均时延统计
