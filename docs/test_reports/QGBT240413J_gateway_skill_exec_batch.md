# QGBT240413J OneBot(qq) 网关批量回放：首判与技能执行明细

- 日期：2026-04-13 21:04:02
- 入口：`POST http://127.0.0.1:18080/v1/host/input` (`source=GATEWAY`, `platform=qq`)
- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240413J_gateway_skill_exec_batch.json`

## 汇总

- 总数：8
- route_decided 命中：4/8
- skill_exec 命中：5/8
- 终态 OK：4，DEGRADED：4，TIMEOUT：0

## 明细

| # | case | route | target | source | conf | skills | terminal | reply_preview |
|---|---|---|---|---|---:|---|---|---|
| 1 | chat_greeting | chat |  | rule | 0.75 |  | INPUT_COMPLETED/DEGRADED | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 2 | translate_zh_to_en |  |  |  |  | sub_agent:translate_agent | INPUT_COMPLETED/OK | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 3 | memory_store | skill | memory_store | override | 0.98 |  | INPUT_COMPLETED/DEGRADED | 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。 |
| 4 | code_fix |  |  |  |  | sub_agent:code_agent | INPUT_COMPLETED/OK | 我先把这件事整理成更清楚一点的说法给你……刚才那一步没有顺好，但我不会把内部草稿直接丢给你。 |
| 5 | recent_turn | recent_turn |  | rule | 0.95 | sub_agent:intent_agent | INPUT_COMPLETED/DEGRADED | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 6 | task_progress | task_progress |  | rule | 0.98 |  | INPUT_COMPLETED/DEGRADED | 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。 |
| 7 | action_continue |  |  |  |  | sub_agent:intent_agent | INPUT_COMPLETED/OK | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 8 | rewrite_intent |  |  |  |  | sub_agent:intent_agent | INPUT_COMPLETED/OK | そうですね……。どんなお話か、聞かせてもらえますか？ |

### Case 1: chat_greeting

- Query: `晚上好呀，蓝蓝。`
- request_id: `req_f2505f138fcc4b759b7c81ff6c1891af`
- 首判: route=chat target= source=rule conf=0.75 reason=RULE_CHAT_DEFAULT override=None llm=None
- skill: （无 skill_exec 记录）
- 终态: INPUT_COMPLETED/DEGRADED reason=GATEWAY_REPLY_FAILED gateway_status=failed latency_ms=149129
- 回执预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。

### Case 2: translate_zh_to_en

- Query: `请翻译成英文：我们今天完成了技能路由改造。`
- request_id: `req_36bffbb5ad064461a0dfc0318e733c89`
- 首判: route=None target=None source=None conf=None reason=None override=None llm=None
- skill: `sub_agent:translate_agent` status=OK digest=None
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None latency_ms=None
- 回执预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。

### Case 3: memory_store

- Query: `保存这条记忆：我更喜欢你先汇报进度再给最终结果。`
- request_id: `req_6e0d06e590f04d9288d28cfc290588a8`
- 首判: route=skill target=memory_store source=override conf=0.98 reason=FORCE_OVERRIDE_MEMORY_STORE override=FORCE_OVERRIDE_MEMORY_STORE llm=None
- skill: （无 skill_exec 记录）
- 终态: INPUT_COMPLETED/DEGRADED reason=GATEWAY_REPLY_FAILED gateway_status=failed latency_ms=39059
- 回执预览: 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。

### Case 4: code_fix

- Query: `帮我修复这个报错：TypeError: string indices must be integers，并给出最小diff思路。`
- request_id: `req_3fd16347b81b4e108bea20ce5557a29c`
- 首判: route=None target=None source=None conf=None reason=None override=None llm=None
- skill: `sub_agent:code_agent` status=OK digest=None
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None latency_ms=None
- 回执预览: 我先把这件事整理成更清楚一点的说法给你……刚才那一步没有顺好，但我不会把内部草稿直接丢给你。

### Case 5: recent_turn

- Query: `你刚刚说了什么`
- request_id: `req_5678862e64524287a60ce1fa80538fe4`
- 首判: route=recent_turn target= source=rule conf=0.95 reason=RULE_RECENT_TURN override=None llm=None
- skill: `sub_agent:intent_agent` status=OK digest=None
- 终态: INPUT_COMPLETED/DEGRADED reason=GATEWAY_REPLY_FAILED gateway_status=failed latency_ms=147372
- 回执预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。

### Case 6: task_progress

- Query: `现在进度怎么样了`
- request_id: `req_3915dafd6cb54404853859c3a7b41548`
- 首判: route=task_progress target= source=rule conf=0.98 reason=RULE_TASK_PROGRESS override=None llm=None
- skill: （无 skill_exec 记录）
- 终态: INPUT_COMPLETED/DEGRADED reason=GATEWAY_REPLY_FAILED gateway_status=failed latency_ms=36343
- 回执预览: 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。

### Case 7: action_continue

- Query: `继续执行上一项并汇报关键节点`
- request_id: `req_0c636bc474d14e5e9cd235ccdcafccb0`
- 首判: route=None target=None source=None conf=None reason=None override=None llm=None
- skill: `sub_agent:intent_agent` status=OK digest=None
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None latency_ms=None
- 回执预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。

### Case 8: rewrite_intent

- Query: `请把这段话润色成更自然的日语口语。`
- request_id: `req_12d34a237a4645cbbe0945b717671912`
- 首判: route=None target=None source=None conf=None reason=None override=None llm=None
- skill: `sub_agent:intent_agent` status=OK digest=None
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None latency_ms=None
- 回执预览: そうですね……。どんなお話か、聞かせてもらえますか？
