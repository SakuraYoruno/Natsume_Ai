# QGBT240413D Query 与技能执行映射留痕

- 日期：2026-04-13
- 目的：补齐“Query -> Skill 执行结果”证据，回答“之前怎么测的”。
- 入口：`POST /v1/host/input`（`source=GATEWAY`, `platform=qq`）
- 证据源：
  - `voice_event_log`（输入生命周期与终态）
  - `skill_exec_log`（技能执行明细）

## 本批测试 Query 与技能结果

| 用例 | Query | request_id | skill_name | skill_status | 输入终态 |
|---|---|---|---|---|---|
| translate_skill | 把这句话翻译成日语：我们先停掉 edge，只验证 host 链路。 | `req_10790bf20f5f44519d6e520a83323101` | `narrative_only` | `SUCCESS` | `INPUT_COMPLETED / OK` |
| memory_skill | 请记住我的偏好：后续验证阶段只测 host，不测 edge。 | `req_2f9aacbad746496184498280ec7c7f67` | `narrative_only` | `SUCCESS` | `INPUT_COMPLETED / OK` |
| repair_skill | 帮我修复这个 Python 报错：TypeError: list indices must be integers or slices, not str，并给最小 diff。 | `req_05eb91a1711148409670795835b386fe` | `narrative_only` | `SUCCESS` | `INPUT_COMPLETED / OK` |

## 结论

1. 之前批测是“网关输入链路与完成态验证”为主，确实没有把 Query 与技能映射展开到报告正文。
2. 本次补证后可以确认：这批 Query 在当前运行配置下都落到了 `narrative_only`，没有触发专用技能族（如 `translate_agent` / `memory_store` / `repair_agent`）。
3. 这也是你之前看不到“技能执行结果”的核心原因：测到了链路完成，但没有打到目标技能。

## 原始结果

- `D:/Natsume_Ai/runtime/test_reports/QGBT240413D_result.json`
