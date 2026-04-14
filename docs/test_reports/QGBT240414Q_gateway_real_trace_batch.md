# QGBT240414Q Gateway 实测留痕（真实链路）

- 日期: 2026-04-14
- Host: `http://127.0.0.1:18080`
- 目标: 验证重构后 `chat/skill/plan/delegate/capability_gap_runtime_synth` 链路是否真实可执行，不只看 `success` 标记。
- 取证方式:
  - 请求侧: `POST /v1/chat`（SSE，抓取 `EXECUTIVE_ROUTE` 与 `COMMIT`）。
  - 追踪侧: `GET /obs/trace/{request_id}`（拓扑节点状态）。
  - 审计侧: `host_brain/data/audit.sqlite3` 的 `intent_log/governor_log/skill_exec_log`。

## 实测结果总览

- 总用例: 7
- 路由符合预期: 6/7
- 三联审计（intent/governor/skill_exec）均有落库: 7/7
- 关键结论:
  - `Skill-First` 主链路可跑通，显式 skill/plan/delegate 均可落审计。
  - `Capability Gap -> Runtime Synth` 已真实触发（见 C6）。
  - 自动复杂规划对中文长多步骤句可触发（见 C7），但英文复杂句本轮未触发（见 C5）。
  - 子智能体执行存在统一降级问题：`[ReAct 降级] ... 当前没有可用的 LLM gateway`（见 C4/C6/C7）。

## 用例详情（Query -> 链路 -> 结果）

### C1 chat 默认问候

- request_id: `req_f83a1a286a8548098a689882fcdfc9e3`
- route: `skill -> narrative_only`（`intent_draft:skill_target`）
- 审计:
  - intent_log: 1
  - governor_log: 2
  - skill_exec_log: `narrative_only / SUCCESS / builtin_noop`
- 结果: 通过，得到人格化回复。

### C2 显式 skill（session_summary）

- request_id: `req_a300a0434be74a2f81e7ef4c8f2e140e`
- route: `skill -> session_summary`（`intent_draft:skill_target`）
- 审计:
  - intent_log: 1
  - governor_log: 2
  - skill_exec_log: `session_summary / SUCCESS / session_summary`
- 结果: 通过，技能执行链路真实落库。

### C3 显式 plan（planned_steps）

- request_id: `req_099d2fd9d5ed4066b0cd1548647c62f6`
- route: `plan -> skill_dag`（`hard_rule:multi_step`）
- 审计:
  - intent_log: 1
  - governor_log: 2
  - skill_exec_log:
    - `session_summary / SUCCESS`
    - `emotion_query / SUCCESS`
- trace 节点: `sub_agent:executive_planner` 存在
- 结果: 通过，DAG 双技能执行成功。

### C4 显式 delegate（code_agent）

- request_id: `req_e175f3935bf64db8afb5320071786e68`
- route: `delegate -> code_agent`（`intent_draft:delegate`）
- 审计:
  - intent_log: 1
  - governor_log: 2
  - skill_exec_log: `sub_agent:code_agent / OK / SUB_AGENT_OK`
  - result_digest: `[ReAct 降级] agent_id=code_agent，当前没有可用的 LLM gateway`
- 结果: 部分通过（路由与执行记录正确，但执行内容降级）。

### C5 英文复杂请求（无 intent_draft）

- request_id: `req_c6611949726743bd8ac063a1ff1cbb6e`
- route: `skill -> narrative_only`（`intent_draft:skill_target`）
- 审计:
  - intent_log: 1
  - governor_log: 2
  - skill_exec_log: `narrative_only / SUCCESS`
- 结果: 未达到预期（应更倾向 plan，但本次落到 narrative_only）。

### C6 capability gap 运行时合成

- request_id: `req_403428ac592149b987509a99e611fd01`
- route: `delegate -> code_agent`（`capability_gap:runtime_synthesized`）
- runtime_capability_id: `runtime_cap_228d57f022d79f`
- 审计:
  - intent_log: 1
  - governor_log: 2
  - skill_exec_log: `sub_agent:code_agent / OK / SUB_AGENT_OK`
  - result_digest: `[ReAct 降级] agent_id=code_agent，当前没有可用的 LLM gateway`
- 结果: 通过（runtime synth 分支真实触发并有 ID 留痕）。

### C7 中文长多步骤自动 plan

- request_id: `req_5fa52da898ea467aa9a812519cd00f05`
- route: `plan -> skill_dag`（`hard_rule:multi_step`）
- 审计:
  - intent_log: 1
  - governor_log: 2
  - skill_exec_log:
    - `session_summary / SUCCESS`
    - `emotion_query / SUCCESS`
    - `sub_agent:intent_agent / OK / SUB_AGENT_OK`
  - intent_agent 结果摘要: `[ReAct 降级] ... 当前没有可用的 LLM gateway`
- 结果: 部分通过（计划执行真实发生，但最终 commit 文本是 ACK 风格，非完整结果汇总）。

## Capability Forge 观测（本轮）

- `/obs/capability-gaps/stats`:
  - `detected_total=1`
  - `executed_total=1`
  - `success_total=1`
  - `forge_candidates=[]`
- `/obs/capability-gaps/forge-proposals`: `total=0`
- 阈值门槛:
  - `min_detected_count=3`
  - `min_unique_sessions=2`
  - `min_failure_rate=0.35`
  - `min_failed_count=2`
- 结论: 本轮只触发了 1 次 gap 检测，未达到自动提案阈值，因此无 forge proposal。

## 问题清单（实测暴露）

1. 子智能体执行内容降级到“无可用 LLM gateway”，影响真实任务可用性（C4/C6/C7）。
2. 英文复杂 query 未触发 plan，存在语言/模式偏置（C5）。
3. Plan 路由执行后，commit 话术可能只给 ACK，不输出完整执行汇总（C7）。

## 对应原始留痕文件

- JSON 明细: `runtime/test_reports/QGBT240414Q_gateway_real_trace_batch.json`
