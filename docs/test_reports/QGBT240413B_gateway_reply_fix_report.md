# QGBT240413B Gateway 回包容错修复留痕

- 日期：2026-04-13
- 目标：
  - 修复 `gateway_reply_failed` 导致整条输入误记为 `INPUT_FAILED/INPUT_GRAPH_ERROR`。
  - 优化降级 fallback 话术，避免机械句式。

## 代码变更

- `host_brain/app/input_routes.py`
  - `_deliver_gateway_reply` 改为结构化返回失败结果，不再在回包失败时抛异常。
  - `_dispatch_to_graph_worker` 增加 gateway 回包异常兜底：即使 `_deliver_gateway_reply` 抛异常，也转为 `gateway_success=false`，最终写入 `INPUT_COMPLETED + DEGRADED`。
  - 在完成态补充字段：`gateway_success`、`gateway_reason_code`、`gateway_detail`，并在 detail 中追加 gateway 失败原因。
- `host_brain/app/prompt_template_defs.py`
  - 更新 `reflection_chain.contextual_fallback.default`。
  - 更新 `chat_agent.minimal_fallback.default`。
  - 更新 `input_routes.fallback_texts.reject_default`、`approve_empty`。

## 自动化验证

1. 单元测试
   - 命令：`pytest -q tests/unit/test_input_routes_worker.py tests/unit/test_prompt_template_store.py tests/unit/test_intelligent_runtime.py`
   - 结果：`32 passed`

2. 真实链路验证（Host `/v1/host/input`, `source=GATEWAY`, `platform=qq`）
   - 请求：`req_2c8d92f79cf84833b059b7324f8d5954`
   - 终态事件：`INPUT_COMPLETED`
   - 状态：`DEGRADED`
   - 原因：`GATEWAY_REPLY_CALL_FAILED`
   - 关键 detail 片段：
     - `gateway_success=false`
     - `gateway_status=DEGRADED`
     - `gateway_reason_code=GATEWAY_REPLY_CALL_FAILED`
   - 结论：回包失败不再升级为 `INPUT_FAILED/INPUT_GRAPH_ERROR`。

3. fallback 文案验证
   - `reject_default`：`刚刚这一步有点卡住了，我马上重试一下，别担心。`
   - `approve_empty`：`好呀，我收到了，马上去处理。`
   - `chat/reflection default`：`嗯，我在呢。刚才那句我想再整理一下，马上接上你。`

## 原始结果文件

- `D:/Natsume_Ai/runtime/test_reports/QGBT240413B_result.json`（历史批量样本，含旧进程阶段数据）
- `D:/Natsume_Ai/runtime/test_reports/QGBT240413C_result.json`（本次批量提交样本）
