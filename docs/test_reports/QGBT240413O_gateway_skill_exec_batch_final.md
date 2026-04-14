# QGBT240413O OneBot(qq) 批量回放最终态（llm_route_used补齐后）

- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240413O_gateway_skill_exec_batch_final.json`

## 汇总

- 总量：10，提交失败：0，超时：0
- 首判留痕：10/10
- llm_route_used=true：7/10
- 技能执行留痕：7/10
- 终态：{'OK': 10}
- gateway状态：{'failed': 9, 'DEGRADED': 1}
- route来源：{'llm': 6, 'override': 1, 'rule': 3}
- 技能分布：{'sub_agent:translate_agent': 3, 'sub_agent:code_agent': 2, 'sub_agent:intent_agent': 2}
- latency: avg=160831ms max=247105ms

## 明细

| # | case | route | target | source | llm_used | skills | terminal | gateway | latency_ms |
|---|---|---|---|---|---|---|---|---|---:|
| 1 | chat_greeting | chat | - | llm | True | - | OK / - | failed | 169280 |
| 2 | translate_zh_en | delegate | translate_agent | llm | True | sub_agent:translate_agent | OK / - | failed | 200569 |
| 3 | translate_ja_zh | delegate | translate_agent | llm | True | sub_agent:translate_agent | OK / - | failed | 231911 |
| 4 | memory_store | skill | memory_store | llm | True | - | OK / - | failed | 31539 |
| 5 | code_fix | delegate | code_agent | llm | True | sub_agent:code_agent | OK / - | failed | 227312 |
| 6 | recent_turn | recent_turn | - | override | True | sub_agent:intent_agent | OK / - | failed | 95848 |
| 7 | task_progress | task_progress | - | rule | False | - | OK / - | failed | 31721 |
| 8 | action_continue | delegate | intent_agent | rule | False | sub_agent:intent_agent | OK / - | failed | 187430 |
| 9 | rewrite_jp | delegate | intent_agent | rule | False | sub_agent:translate_agent | OK / - | failed | 247105 |
| 10 | deploy_action | delegate | code_agent | llm | True | sub_agent:code_agent | OK / - | DEGRADED | 185600 |