# Skill-First Execution Log 2026-04-13 Stage 7D

## Scope

- Objective: 给 Gateway 执行链路增加偏航检测与自动纠偏能力。
- Focus: “成功但偏题”场景的识别与恢复，而不是错误重试。

## Implemented Changes

- `host_brain/app/chat_agent.py`
  - 新增 `render_quick_gateway_realign_text(...)`，用于偏航时人格化纠偏提示。
- `host_brain/app/prompt_template_defs.py`
  - 在 `chat_agent.runtime_texts` 两处定义新增：
    - `quick_realign_notice`
    - `quick_realign_default`
- `host_brain/app/input_routes.py`
  - 新增偏航检测核心：
    - `_extract_alignment_keywords`
    - `_compute_keyword_overlap`
    - `_detect_gateway_execution_deviation`
    - `_build_realign_user_message`
    - `_maybe_send_gateway_realign_notice`
    - `_maybe_realign_gateway_pipeline`
  - `_dispatch_to_graph_worker` 接入偏航检测与一次纠偏重试。
  - 扩展 gateway 审计字段（deviation 系列）。
- `.env.example`
  - 增加 Stage 7D 配置开关与阈值参数。

## Test Evidence

- Command:
  - `pytest -q tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py`
- Result:
  - `22 passed in 0.72s`
- Command:
  - `pytest -q tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py tests/unit/test_host_14b_runtime_route.py`
- Result:
  - `28 passed in 0.86s`
- Command:
  - `pytest -q tests/unit/test_input_routes_dispatch.py tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py`
- Result:
  - `29 passed in 0.87s`
- Command:
  - `pytest -q tests/unit/test_input_routes_dispatch.py tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py tests/unit/test_host_14b_runtime_route.py`
- Result:
  - `35 passed in 1.02s`

## Behavior Verification

- 偏航命中后：
  - 记录 `DEVIATION_DETECTED`
  - 发送纠偏提示（可配置关闭）
  - 自动执行一次纠偏重试
- 纠偏成功后：
  - 记录 `DEVIATION_RECOVERED`
  - 使用纠偏结果作为最终回复

## Follow-up

- 建议补 integration smoke：
  - 首轮偏航 + 二轮纠偏场景回放
  - 验证 `DEVIATION_DETECTED -> DEVIATION_RECOVERED -> COMPLETED` 顺序
