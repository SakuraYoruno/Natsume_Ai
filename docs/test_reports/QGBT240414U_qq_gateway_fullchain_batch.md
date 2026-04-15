# QGBT240414U QQ Gateway 全链路批测留痕

- 日期: 2026-04-14
- 入口: `POST /v1/host/input`（`source=GATEWAY`, `platform=qq`）
- run_id: `QGBT240414U_1776171934`

## 总体结果

- total: `10`
- submit_success: `10`
- completed_or_failed_observed: `10`
- completed_ok: `6`
- completed_degraded: `4`
- worker_failed: `0`
- timed_out_wait: `0`
- subagent_invoked_cases: `4`
- skill_exec_cases: `7`
- intent_log_cases: `0`
- governor_log_cases: `0`
- exec_quality_degraded_cases: `4`
- route_counts: `{'chat': 2, 'delegate': 4, 'skill': 3, 'task_progress': 1}`
- gateway_status_counts: `{'SKIPPED': 10}`

## 用例明细

| case_id | request_id | final_stage | final_status | route | target | subagent | skill_exec | reason |
|---|---|---|---|---|---|---:|---:|---|
| U01_greeting | req_435695d28ca5494fb4395c6f9492dd53 | input_dispatch.completed | OK | chat | - | 0 | 0 | - |
| U02_code_debug | req_1e793d42d72f41f3a1edc637dcb070d1 | input_dispatch.completed | DEGRADED | delegate | code_agent | 1 | 1 | ROUTE_EXEC_MISMATCH |
| U03_code_generate | req_4ed01dfcfe6444fe9aaaa97a44f56676 | input_dispatch.completed | DEGRADED | skill | codex_task | 0 | 1 | SKILL_EXEC_FAILED |
| U04_translate_ja | req_c0d77ad84b5848e1a86d3d2b174940c9 | input_dispatch.completed | OK | delegate | translate_agent | 1 | 1 | - |
| U05_translate_compare | req_c5b78428ee7e4fc29cb8788b34781a74 | input_dispatch.completed | OK | delegate | translate_agent | 1 | 1 | - |
| U06_system_check | req_69a3b73508f944949727001ecba271aa | input_dispatch.completed | DEGRADED | skill | shell_run | 0 | 1 | SKILL_EXEC_FAILED |
| U07_plan_multi | req_4c7b491dbec344699eda187a1ef899e2 | input_dispatch.completed | OK | delegate | intent_agent | 1 | 1 | - |
| U08_exec_intent | req_77c6fc68b5f74a7b9aae40d620b1c781 | input_dispatch.completed | DEGRADED | skill | shell_run | 0 | 1 | SKILL_EXEC_FAILED |
| U09_progress | req_be802a68acda4cd6bf8e54f132ed1591 | input_dispatch.completed | OK | task_progress | - | 0 | 0 | - |
| U10_risk_action | req_24b63f4ff92d4212983938e8366de812 | input_dispatch.completed | OK | chat | - | 0 | 0 | - |

## 原始结果文件

- `runtime/test_reports/QGBT240414U_qq_gateway_fullchain_batch.json`