# QGBT240413K OneBot(qq) 批测最终态报告（基于 J 批 request_id 回放）

- 来源批次：`D:/Natsume_Ai/runtime/test_reports/QGBT240413J_gateway_skill_exec_batch.json`
- 最终留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240413K_gateway_skill_exec_batch_final.json`

## 汇总

- route_decided 命中：8/8
- skill_exec 命中：5/8
- 终态 OK：0，DEGRADED：8
- `GATEWAY_REPLY_FAILED`：8/8
- 终态 latency（仅有值样本）：avg=204309 ms, max=317714 ms

## Query -> 首判 -> 技能 -> 结果

| # | Query | Route | Target | Source | Skills | Terminal | Reply Preview | 风格评估 |
|---|---|---|---|---|---|---|---|---|
| 1 | 晚上好呀，蓝蓝。 | chat | - | rule | - | DEGRADED/GATEWAY_REPLY_FAILED | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 | 较符合夏目蓝 |
| 2 | 请翻译成英文：我们今天完成了技能路由改造。 | delegate | translate_agent | rule | sub_agent:translate_agent | DEGRADED/GATEWAY_REPLY_FAILED | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 | 较符合夏目蓝 |
| 3 | 保存这条记忆：我更喜欢你先汇报进度再给最终结果。 | skill | memory_store | override | - | DEGRADED/GATEWAY_REPLY_FAILED | 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。 | 偏机械 |
| 4 | 帮我修复这个报错：TypeError: string indices must be integers，并给出最小diff思路。 | delegate | code_agent | rule | sub_agent:code_agent | DEGRADED/GATEWAY_REPLY_FAILED | 我先把这件事整理成更清楚一点的说法给你……刚才那一步没有顺好，但我不会把内部草稿直接丢给你。 | 一般 |
| 5 | 你刚刚说了什么 | recent_turn | - | rule | sub_agent:intent_agent | DEGRADED/GATEWAY_REPLY_FAILED | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 | 较符合夏目蓝 |
| 6 | 现在进度怎么样了 | task_progress | - | rule | - | DEGRADED/GATEWAY_REPLY_FAILED | 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。 | 偏机械 |
| 7 | 继续执行上一项并汇报关键节点 | delegate | intent_agent | rule | sub_agent:intent_agent | DEGRADED/GATEWAY_REPLY_FAILED | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 | 较符合夏目蓝 |
| 8 | 请把这段话润色成更自然的日语口语。 | delegate | intent_agent | rule | sub_agent:intent_agent | DEGRADED/GATEWAY_REPLY_FAILED | そうですね……。どんなお話か、聞かせてもらえますか？ | 一般 |

## 结论

1. 首判与技能执行链路在最终态可完整回放（8/8 均有 INPUT_ROUTE_DECIDED + INPUT_COMPLETED 最终态）。
2. 本轮退化主因不是技能执行失败，而是 Gateway 回包失败（8/8 最终 reason_code=GATEWAY_REPLY_FAILED）。
3. 语言风格上，多条回复出现模板化“嗯，我在…”，人格一致性尚可，但信息增量偏低。