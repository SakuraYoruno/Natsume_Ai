# QGBT240414J - 两个问题修复留痕（风格兜底 + 执行质量判定）

## 背景
针对上一轮反馈的两个核心问题落地修复：

1. `chat_agent` 兜底回复出现“内部草稿/重说一遍”式机械文案。  
2. 技能执行存在“名义成功但无有效结果”，`INPUT_COMPLETED` 仍被记为 `OK`。

## 改动摘要

### 1) 修复兜底文案机械感
- 文件：`host_brain/app/prompt_template_defs.py`
- 更新模板：
  - `chat_agent.minimal_fallback.code_agent`
  - `chat_agent.minimal_fallback.recent_turn_direct`
  - `chat_agent.minimal_fallback.default`
- 目标：移除“内部草稿/整理说法”等不自然句式，改为简短、可用、人格连续的过渡话术。

### 2) 修复“伪成功”执行判定
- 文件：`host_brain/app/input_routes.py`
- 新增执行质量门控（默认开启）：
  - 环境开关：`HOST_INPUT_EXEC_QUALITY_GATE_ENABLED`（默认 true）
  - 在 `INPUT_COMPLETED` 前对技能执行结果进行二次判定：
    - `SKILL_EXEC_MISSING`
    - `SKILL_EXEC_FAILED`
    - `SKILL_EXEC_NO_EFFECT`
    - `SKILL_EXEC_RESULT_EMPTY`
    - `ROUTE_EXEC_MISMATCH`
  - 命中后将 `completion_status` 置为 `DEGRADED`，并写入 `exec_quality_*` 留痕字段。

### 3) 修复子智能体审计缺口
- 文件：`host_brain/app/sub_agent_protocol.py`
- 修复 `skill_exec_log` 字段映射：
  - 补齐 `intent_id/event_id/result_code/result_digest/evidence_ref` 等标准字段。
  - 解决此前子智能体执行记录缺少 digest、结果不可追踪问题。

### 4) 修复 memory_store 无写入器仍判成功
- 文件：`host_brain/skills/impl/memory_executor.py`
- 当无 memory writer/store 时返回 `DEGRADED` + 明确 `reason_code`：
  - `MEMORY_WRITER_UNAVAILABLE`
  - `MEMORY_STORE_UNAVAILABLE`

### 5) 强化技能“有效成功”判定
- 文件：`host_brain/app/executive_runtime_core.py`
- 增加 `_is_effective_skill_success`，对“状态成功但结果无效”做二次拦截（如 `no memory writer available`、`SCHED_NO_CONTENT` 等）。

## 单测留痕

1. `pytest tests/unit/test_input_routes_worker.py -q`  
   - 结果：`29 passed`
2. `pytest tests/unit/test_prompt_template_store.py -q`  
   - 结果：`6 passed`
3. `pytest tests/unit/test_sub_agent_protocol_context.py tests/unit/test_sub_agent_protocol_templates.py -q`  
   - 结果：`8 passed`
4. `pytest tests/unit/test_phase15_dispatch_to_agent.py -q`  
   - 结果：`41 passed`
5. `pytest tests/integration/test_executive_skill_runtime_smoke.py -q`  
   - 结果：`2 passed`

## 结论
- 问题 1（机械兜底文案）已替换为自然化模板。  
- 问题 2（技能伪成功）已改为“执行质量优先”判定并入审计。  
- 后续 OneBot 批量回放时，`INPUT_COMPLETED=OK` 将不再掩盖无效执行。
