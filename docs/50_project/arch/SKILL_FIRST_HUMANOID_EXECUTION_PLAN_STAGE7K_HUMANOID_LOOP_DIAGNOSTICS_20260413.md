受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7K (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7K (2026-04-13)

## 目标

- 在 Stage7I/7J 的“修复建议 + 请求直达”基础上，继续补齐“异常回放摘要 / 根因归纳”层。
- 让数字生命体总览不仅知道“哪里坏了”，还知道“最近哪几条请求为什么坏、断在什么位置、应优先打开哪条链路”。

## 范围

- 后端新增：
  - `GET /obs/input/humanoid-loop-diagnostics`
- 前端增强：
  - `DigitalLifeOverview` 新增“异常回放摘要 / 根因归纳”面板
  - 与 remediation / representative request 联动展示

## 后端设计

- 诊断输入：
  - 复用 `GET /obs/input/humanoid-loop-remediation` 提取的 `representative_requests[]`
  - 每个 request 再补采：
    - `voice_event_log`
    - `intent_log`
    - `governor_log`
    - `retrieval_event_log`
    - `llm_node_call_log`
    - `airlock_log`
    - `bond_update_log`
    - `memory_ops_log`
    - `error_log`
- 诊断逻辑：
  - 复用 `_build_trace_summary(...)` 生成请求级输入/输出摘要
  - 基于 Gateway 闭环事件做确定性根因映射
- 典型根因码：
  - `EXECUTION_FAILED`
  - `DEVIATION_NOT_RECOVERED`
  - `COMPLETED_WITHOUT_HUMANOID_TOUCH`
  - `ACK_PATH_MISSING`
  - `PROGRESS_PATH_MISSING`
  - `GOVERNOR_BLOCKED`

## 前端设计

- 在“闭环自愈建议”面板内部新增诊断卡片区，统一展示：
  - `request_id`
  - `root_cause_code`
  - `suspected_breakpoint`
  - `input_text`
  - `output_text`
  - `reason_hint`
  - `evidence_codes`
  - `recommended_view_path`
- 目标是把“告警 -> 建议 -> 根因 -> Trace 回放”变成单屏闭环。

## 验收标准

1. diagnostics 端点能返回稳定、可测试的确定性根因归纳结果。
2. 失败、无触达、偏航未恢复等典型异常能映射到不同 `root_cause_code`。
3. 前端总览页能直接看到根因摘要，并跳转到 `Trace` 做精查。
