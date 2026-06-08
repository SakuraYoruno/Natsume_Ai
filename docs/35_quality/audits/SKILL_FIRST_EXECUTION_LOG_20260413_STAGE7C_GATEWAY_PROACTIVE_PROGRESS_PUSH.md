受众：CODEX
标题：Skill-First Execution Log 2026-04-13 Stage 7C

# Skill-First Execution Log 2026-04-13 Stage 7C

## Scope

- Objective: 为 Gateway 执行链路新增“主动进度播报”，在长任务期间自动回报阶段进展。
- Boundary: 不改变主业务执行逻辑，仅增加并发进度回报子链路。

## Implemented Changes

- `host_brain/app/input_routes.py`
  - 新增主动播报判定与执行：
    - `_should_start_gateway_progress_push`
    - `_run_gateway_proactive_progress_push`
    - `_stop_progress_push_task`
    - `_log_progress_push_task_exception`
  - `_dispatch_to_graph_worker` 接入后台播报生命周期管理：
    - `DISPATCHED` 后判断并启动
    - 主链路完成前停止播报
    - 异常路径兜底停止
  - `_build_gateway_progress_reply_text` 扩展：
    - 支持 `exclude_request_id` 参数
    - 返回 `(reply_text, reason, summary)` 供 fastpath 与主动播报共用
  - 扩展 gateway 审计字段：
    - `progress_push_status`
    - `progress_push_reason`
    - `progress_push_preview`
    - `progress_push_updates`
- `.env.example`
  - 新增 Stage 7C 配置项（开关、预热、间隔、最大次数、发送策略）。

## Test Evidence

- Command:
  - `pytest -q tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py`
- Result:
  - `18 passed in 0.80s`
- Command:
  - `pytest -q tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py tests/unit/test_host_14b_runtime_route.py`
- Result:
  - `24 passed in 0.83s`

## Behavior Notes

- 执行意图请求进入 worker 后，会并行启动“主动进度播报”协程。
- 仅在任务状态 `RUNNING/RECOVERING` 且进度签名变化时播报，避免重复文案。
- 若主链路提前结束，播报协程立即停止，不影响最终回复。

## Follow-up

- 建议加一条集成回放：
  - 构造 >30s 长任务，验证“ACK -> 主动进度播报 -> 最终回复”顺序稳定。
- 建议在前端观测面板增加 `progress_push` 指标卡（命中率/成功率/P95）。
