# QGBT240413E Gateway 意图首判落地与留痕报告

- 日期：2026-04-13
- 范围：Host 侧 `Gateway -> Intent -> ExecutiveRuntime` 链路
- 目标：将 Gateway 入站意图从“硬编码 narrative_only”改为“LLM 首判 + 规则兜底 + 强制纠偏”，并补齐可观测留痕

## 1. 已落地改造

### 1.1 Gateway 意图首判（LLM first）
- 文件：`host_brain/app/input_routes.py`
- 新增核心能力：
  - `_build_gateway_intent_draft(...)`
  - `_run_gateway_intent_route_llm(...)`
  - `_normalize_gateway_llm_route_decision(...)`
  - `_extract_first_json_object(...)`
  - `_build_gateway_skill_manifest_lines(...)`
- 路由输出标准化字段：
  - `route`, `target_id`, `task`, `route_reason`, `route_source`, `route_confidence`, `llm_route_used`

### 1.2 规则兜底与强制纠偏
- 文件：`host_brain/app/input_routes.py`
- 新增策略：
  - 规则兜底 `_build_gateway_rule_route_decision(...)`
  - 强制纠偏 `_apply_gateway_force_override(...)`
- 当前强制纠偏覆盖：
  - 翻译类：强制 `delegate -> translate_agent`
  - 记忆写入类：优先 `skill -> memory_store`（不存在时降级 `delegate -> intent_agent`）
  - 代码修复类：强制 `delegate -> code_agent`
  - 明确动作但被判 chat：强制 `delegate -> intent_agent`

### 1.3 路由留痕增强
- 文件：`host_brain/app/input_routes.py`
- 新增事件：
  - `INPUT_ROUTE_DECIDED`（phase=`ROUTE_DECIDED`）
- 新增 payload 字段：
  - `route_type`, `route_target`, `route_task`, `route_source`, `route_reason`, `route_confidence`, `route_override_reason`
- `INPUT_COMPLETED` 也同步写入以上路由字段，便于按 request_id 追溯

## 2. 关键行为变化

- 旧行为：
  - `_run_cortex_orchestrator_pipeline` 固定 `intent_draft.action=narrative_only`
  - 导致执行意图大量落到 chat
- 新行为：
  - Gateway 文本先走 LLM 意图分类（可降级）
  - LLM 失败或不可用时自动规则回退
  - 对翻译/记忆/代码等关键动作意图做强制纠偏，避免误落 chat

## 3. 测试留痕

### 3.1 新增/更新单测
- 文件：`tests/unit/test_input_routes_worker.py`
- 新增用例：
  - `test_build_gateway_intent_draft_prefers_llm_route_when_available`
  - `test_build_gateway_intent_draft_falls_back_to_rule_when_llm_unavailable`
  - `test_build_gateway_intent_draft_force_overrides_translate`
  - `test_dispatch_worker_logs_route_decided_event_with_route_payload`

### 3.2 执行结果

1) 命令：
```powershell
pytest -q D:\Natsume_Ai\tests\unit\test_input_routes_worker.py D:\Natsume_Ai\tests\unit\test_input_routes_dispatch.py
```
结果：`27 passed in 4.89s`

2) 命令：
```powershell
pytest -q D:\Natsume_Ai\tests\unit\test_input_routes_smoke.py D:\Natsume_Ai\tests\unit\test_executive_runtime_entry.py
```
结果：`5 passed in 0.66s`

## 4. 复核点（你可直接查）

- 意图构建入口：`_build_gateway_intent_draft`
- LLM 路由入口：`_run_gateway_intent_route_llm`
- 路由纠偏：`_apply_gateway_force_override`
- 路由事件留痕：`INPUT_ROUTE_DECIDED`（`_dispatch_to_graph_worker`）
- 路由字段写入：`_log_input_event` route 字段段落

