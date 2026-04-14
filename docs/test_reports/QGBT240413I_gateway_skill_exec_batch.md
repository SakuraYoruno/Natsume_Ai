# QGBT240413I OneBot(qq) 网关批量回放：首判与技能执行明细

- 日期：2026-04-13 20:42:54
- 入口：`POST http://127.0.0.1:18080/v1/host/input` (`source=GATEWAY`, `platform=qq`)
- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240413I_gateway_skill_exec_batch.json`

## 汇总

- 总数：8
- route_decided 命中：0/8
- skill_exec 命中：5/8
- 终态 OK：4，DEGRADED：3，TIMEOUT：0

## 明细

| # | case | route | target | source | skills | terminal | reply_preview |
|---|---|---|---|---|---|---|---|
| 1 | chat_greeting |  |  |  |  | / |  |
| 2 | translate_zh_to_en |  |  |  | narrative_only | INPUT_COMPLETED/DEGRADED | 嗯……好的。我可以帮你翻译成英文呢。 |
| 3 | memory_store |  |  |  |  | INPUT_COMPLETED/DEGRADED | 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。 |
| 4 | code_fix |  |  |  | narrative_only | INPUT_COMPLETED/OK | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 5 | recent_turn |  |  |  | sub_agent:intent_agent | INPUT_COMPLETED/OK | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 6 | task_progress |  |  |  |  | INPUT_COMPLETED/DEGRADED | 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。 |
| 7 | action_continue |  |  |  | narrative_only | INPUT_COMPLETED/OK | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 8 | rewrite_intent |  |  |  | narrative_only | INPUT_COMPLETED/OK | ええと、どんなお話ですか？自然な話し言葉にするお手伝い、できますよ。 |

### Case 1: chat_greeting

- Query: `晚上好呀，蓝蓝。`
- request_id: `None`
- 首判: route=None target=None source=None conf=None reason=None
- skill: （无 skill_exec 记录）
- 终态: None/None reason=None gateway_status=None

### Case 2: translate_zh_to_en

- Query: `请翻译成英文：我们今天完成了技能路由改造。`
- request_id: `req_dc2296a248234dfabaa0e9d7e4c02ab7`
- 首判: route=None target=None source=None conf=None reason=None
- skill: `narrative_only` status=SUCCESS digest=builtin_noop
- 终态: INPUT_COMPLETED/DEGRADED reason=GATEWAY_REPLY_FAILED gateway_status=failed
- 回执预览: 嗯……好的。我可以帮你翻译成英文呢。

### Case 3: memory_store

- Query: `保存这条记忆：我更喜欢你先汇报进度再给最终结果。`
- request_id: `req_03ab285ef22f4be6addbd53c5cc1a766`
- 首判: route=None target=None source=None conf=None reason=None
- skill: （无 skill_exec 记录）
- 终态: INPUT_COMPLETED/DEGRADED reason=GATEWAY_REPLY_FAILED gateway_status=failed
- 回执预览: 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。

### Case 4: code_fix

- Query: `帮我修复这个报错：TypeError: string indices must be integers，并给出最小diff思路。`
- request_id: `req_172fd5dfc28141d7b4924cc1d64e3299`
- 首判: route=None target=None source=None conf=None reason=None
- skill: `narrative_only` status=SUCCESS digest=builtin_noop
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None
- 回执预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。

### Case 5: recent_turn

- Query: `你刚刚说了什么`
- request_id: `req_7c544a7d519340cbb74c9c00c5da33df`
- 首判: route=None target=None source=None conf=None reason=None
- skill: `sub_agent:intent_agent` status=OK digest=None
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None
- 回执预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。

### Case 6: task_progress

- Query: `现在进度怎么样了`
- request_id: `req_0a65a1a8801845e69231331b91f93b86`
- 首判: route=None target=None source=None conf=None reason=None
- skill: （无 skill_exec 记录）
- 终态: INPUT_COMPLETED/DEGRADED reason=GATEWAY_REPLY_FAILED gateway_status=failed
- 回执预览: 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。

### Case 7: action_continue

- Query: `继续执行上一项并汇报关键节点`
- request_id: `req_cf150880989c415cb77e9cc2614ca793`
- 首判: route=None target=None source=None conf=None reason=None
- skill: `narrative_only` status=SUCCESS digest=builtin_noop
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None
- 回执预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。

### Case 8: rewrite_intent

- Query: `请把这段话润色成更自然的日语口语。`
- request_id: `req_57827647b2dc4786b00e78b33e4ae916`
- 首判: route=None target=None source=None conf=None reason=None
- skill: `narrative_only` status=SUCCESS digest=builtin_noop
- 终态: INPUT_COMPLETED/OK reason=None gateway_status=None
- 回执预览: ええと、どんなお話ですか？自然な話し言葉にするお手伝い、できますよ。
