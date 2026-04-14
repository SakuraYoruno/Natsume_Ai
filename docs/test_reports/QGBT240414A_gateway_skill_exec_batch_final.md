# QGBT240414A OneBot(qq) 大样本批量回放最终态

- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240414A_gateway_skill_exec_batch_final.json`

## 汇总

- 总量：20，提交失败：0，超时：0
- 首判留痕：20/20
- llm_route_used=true：20/20
- 技能执行留痕：13/20
- 终态：{'OK': 16, 'DEGRADED': 4}
- gateway状态：{'failed': 13, 'DEGRADED': 7}
- route来源：{'llm': 14, 'override': 6}
- route类型：{'chat': 2, 'delegate': 12, 'skill': 2, 'recent_turn': 2, 'task_progress': 2}
- 技能分布：{'sub_agent:code_agent': 4, 'sub_agent:translate_agent': 4, 'memory_store': 1, 'codex_task': 2, 'sub_agent:intent_agent': 2}
- latency: avg=173042ms max=322532ms

## 明细

| # | case | route | target | source | llm_used | skills | terminal | gateway | latency_ms |
|---|---|---|---|---|---|---|---|---|---:|
| 1 | chat_1 | chat | - | llm | True | - | OK / - | failed | 123857 |
| 2 | chat_2 | delegate | code_agent | override | True | sub_agent:code_agent | OK / - | DEGRADED | 141951 |
| 3 | translate_zh_en_1 | delegate | translate_agent | llm | True | sub_agent:translate_agent | OK / - | failed | 202903 |
| 4 | translate_zh_en_2 | delegate | translate_agent | llm | True | sub_agent:translate_agent | OK / - | failed | 228514 |
| 5 | translate_ja_zh_1 | delegate | translate_agent | llm | True | sub_agent:translate_agent | OK / - | failed | 322532 |
| 6 | translate_ja_zh_2 | delegate | translate_agent | llm | True | sub_agent:translate_agent | OK / - | DEGRADED | 277829 |
| 7 | memory_1 | skill | memory_store | llm | True | memory_store | OK / - | failed | 157689 |
| 8 | memory_2 | skill | memory_store | llm | True | - | DEGRADED / RATE_LIMIT_EXCEEDED | failed | 10683 |
| 9 | code_fix_1 | delegate | code_agent | llm | True | sub_agent:code_agent | OK / - | DEGRADED | 277792 |
| 10 | code_fix_2 | delegate | code_agent | llm | True | sub_agent:code_agent | OK / - | failed | 322468 |
| 11 | code_gen_1 | delegate | code_agent | llm | True | codex_task | OK / - | DEGRADED | 277774 |
| 12 | code_gen_2 | delegate | code_agent | llm | True | codex_task | OK / - | DEGRADED | 277757 |
| 13 | recent_1 | recent_turn | - | override | True | sub_agent:intent_agent | OK / - | failed | 123673 |
| 14 | recent_2 | recent_turn | - | override | True | - | OK / - | failed | 9203 |
| 15 | progress_1 | task_progress | - | override | True | - | DEGRADED / RATE_LIMIT_EXCEEDED | failed | 12065 |
| 16 | progress_2 | task_progress | - | override | True | - | OK / - | DEGRADED | 82691 |
| 17 | continue_1 | delegate | intent_agent | override | True | sub_agent:intent_agent | OK / - | DEGRADED | 274605 |
| 18 | continue_2 | chat | - | llm | True | - | DEGRADED / RATE_LIMIT_EXCEEDED | failed | 8768 |
| 19 | rewrite_jp_1 | delegate | translate_agent | llm | True | - | DEGRADED / RATE_LIMIT_EXCEEDED | failed | 8889 |
| 20 | deploy_1 | delegate | code_agent | llm | True | sub_agent:code_agent | OK / - | failed | 319203 |