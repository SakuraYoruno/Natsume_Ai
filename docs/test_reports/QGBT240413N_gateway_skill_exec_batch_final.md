# QGBT240413N OneBot(qq) 批量回放最终态

- 批次时间：2026-04-13 22:52:48
- 入口：`POST http://127.0.0.1:18080/v1/host/input` (`source=GATEWAY`,`platform=qq`) 
- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240413N_gateway_skill_exec_batch_final.json`

## 汇总

- 总量：12，提交失败：0，超时：0
- 首判留痕(INPUT_ROUTE_DECIDED)：12/12
- LLM首判命中(llm_route_used=true)：0/12
- 技能执行留痕(skill_exec)：9/12
- 终态分布：{'OK': 12}
- gateway状态：{'failed': 11, 'DEGRADED': 1}
- route来源：{'llm': 8, 'rule': 2, 'override': 2}
- 技能分布：{'sub_agent:translate_agent': 3, 'sub_agent:code_agent': 3, 'sub_agent:intent_agent': 3}
- latency: avg=125062ms max=255609ms

## 明细

| # | case | route | target | source | llm_used | skills | terminal | gateway | latency_ms | reply_preview |
|---|---|---|---|---|---|---|---|---|---:|---|
| 1 | chat_greeting | chat | - | llm | None | - | OK / - | failed | 89195 | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 2 | translate_zh_en | delegate | translate_agent | llm | None | sub_agent:translate_agent | OK / - | failed | 153848 | 您想翻译成英文的‘我们今天完成了技能路由改造’……现在好像暂时没办法帮您完成呢。真是抱歉…… |
| 3 | translate_ja_zh | delegate | translate_agent | llm | None | sub_agent:translate_agent | OK / - | failed | 153861 | 您是想让我翻译‘おはようございます。’这句日语，对吧？不过……我这边负责翻译的，它现在好像暂时没办法处理呢。真是抱歉…… |
| 4 | memory_store | skill | memory_store | llm | None | - | OK / - | DEGRADED | 52779 | 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。 |
| 5 | code_fix | delegate | code_agent | rule | None | sub_agent:code_agent | OK / - | failed | 135954 | 嗯……您说的这个 Python 报错，‘TypeError: list indices must be integers or slices, … |
| 6 | code_generate | delegate | code_agent | llm | None | sub_agent:code_agent | OK / - | failed | 135902 | 嗯……您是想让我写一个 Python 函数，读取 JSON 数组并按 id 去重，对吧？我这边负责处理代码的……它告诉我，现在好像暂时没办法帮上… |
| 7 | recent_turn | recent_turn | - | override | None | sub_agent:intent_agent | OK / - | failed | 91691 | 啊……我刚才说的是，您之前提到的翻译请求，我这边负责翻译的那个，它暂时没办法处理呢。 |
| 8 | task_progress | chat | - | llm | None | - | OK / - | failed | 57352 | 当前有个任务还在进行，是 code_agent。route=delegate |
| 9 | action_continue | delegate | intent_agent | override | None | sub_agent:intent_agent | OK / - | failed | 241587 | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |
| 10 | rewrite_jp | delegate | translate_agent | llm | None | sub_agent:translate_agent | OK / - | failed | 64004 | 嗯……您说的这段话を、もっと自然な日本語に……ですね。ごめんなさい……。私の方の翻訳担当が、今、ちょっとお手伝いできないみたいなんです。 |
| 11 | deploy_action | delegate | code_agent | llm | None | sub_agent:code_agent | OK / - | failed | 68972 | 嗯……您是想让我部署服务到测试环境，对吧？不过……我这边负责处理这些的……它告诉我，现在好像暂时没办法帮上忙呢。真是抱歉…… |
| 12 | summary_action | delegate | intent_agent | rule | None | sub_agent:intent_agent | OK / - | failed | 255609 | 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。 |

## Query->执行链路

### Case 1: chat_greeting
- Query: `你好呀蓝蓝，今晚辛苦啦。`
- request_id: `req_e215403825b84efda3842c6c153d6209`
- 首判: route=chat target= source=llm llm_used=None conf=0.9 reason=用户消息是问候和情感表达，属于日常对话范畴，应由 ChatAgent 处理。
- skill: （无 skill_exec 记录）
- 终态: OK reason=None gateway=failed latency_ms=89195
- 回复预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。
- 风格评估: 较符合夏目蓝

### Case 2: translate_zh_en
- Query: `请翻译成英文：我们今天完成了技能路由改造。`
- request_id: `req_fcbb3d52a0cc4f9e85f9875a3f212356`
- 首判: route=delegate target=translate_agent source=llm llm_used=None conf=1.0 reason=用户明确要求将中文句子翻译成英文，这直接对应了 translate_agent 的功能。
- skill: `sub_agent:translate_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=153848
- 回复预览: 您想翻译成英文的‘我们今天完成了技能路由改造’……现在好像暂时没办法帮您完成呢。真是抱歉……
- 风格评估: 一般

### Case 3: translate_ja_zh
- Query: `把这句日语翻译成中文：おはようございます。`
- request_id: `req_c944bbd5e0d44af088ddef87f9bc2c1a`
- 首判: route=delegate target=translate_agent source=llm llm_used=None conf=1.0 reason=用户明确要求将日语翻译成中文，'translate_agent'是处理翻译任务的专用代理。
- skill: `sub_agent:translate_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=153861
- 回复预览: 您是想让我翻译‘おはようございます。’这句日语，对吧？不过……我这边负责翻译的，它现在好像暂时没办法处理呢。真是抱歉……
- 风格评估: 一般

### Case 4: memory_store
- Query: `保存这条记忆：我希望你先回报进度，再给最终结论。`
- request_id: `req_b549319b1bab4035a3e7762130262d00`
- 首判: route=skill target=memory_store source=llm llm_used=None conf=0.9 reason=用户明确要求保存一条记忆，'memory_store' skill是用于此目的的。
- skill: （无 skill_exec 记录）
- 终态: OK reason=None gateway=DEGRADED latency_ms=52779
- 回复预览: 当前有个任务还在进行，是 cortex_orchestrator。我正在整理上下文和检索记忆。
- 风格评估: 偏机械

### Case 5: code_fix
- Query: `帮我修复这个 Python 报错：TypeError: list indices must be integers or slices, not str。`
- request_id: `req_2cefb942e52e4a18971e9fe7cf2d7613`
- 首判: route=delegate target=code_agent source=rule llm_used=None conf=0.95 reason=RULE_FORCE_CODE_AGENT
- skill: `sub_agent:code_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=135954
- 回复预览: 嗯……您说的这个 Python 报错，‘TypeError: list indices must be integers or slices, not str’……听起来是代码方面的问题呢。不过，我这边负责处理代码的……现在好像暂时没...
- 风格评估: 较符合夏目蓝

### Case 6: code_generate
- Query: `帮我写一个 Python 函数，读取 json 数组并按 id 去重。`
- request_id: `req_411b92eee37d42049e3c3cb416b57858`
- 首判: route=delegate target=code_agent source=llm llm_used=None conf=1.0 reason=用户明确要求编写 Python 函数，这是一个编程任务，应委托给 code_agent。
- skill: `sub_agent:code_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=135902
- 回复预览: 嗯……您是想让我写一个 Python 函数，读取 JSON 数组并按 id 去重，对吧？我这边负责处理代码的……它告诉我，现在好像暂时没办法帮上忙呢。真是抱歉……
- 风格评估: 较符合夏目蓝

### Case 7: recent_turn
- Query: `你刚刚说了什么`
- request_id: `req_1fc3934b094d4d5683a334484c05aaf3`
- 首判: route=recent_turn target= source=override llm_used=None conf=0.9 reason=FORCE_OVERRIDE_RECENT_TURN
- skill: `sub_agent:intent_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=91691
- 回复预览: 啊……我刚才说的是，您之前提到的翻译请求，我这边负责翻译的那个，它暂时没办法处理呢。
- 风格评估: 一般

### Case 8: task_progress
- Query: `现在进度怎么样了`
- request_id: `req_ac9c93cee1d94dda85dcc25257b1ad37`
- 首判: route=chat target= source=llm llm_used=None conf=0.8 reason=用户询问'现在进度怎么样了'，这是一个非常模糊的查询。它没有明确指向任何特定的任务、技能或子代理。最常见的情况是用户在闲聊中询问一般性进展，或者需要进一步澄清才能确定具体意图。根据'80% 的消息是闲聊，优先返回 chat'的规则，将其路由到 chat agent 进行处理和澄清。
- skill: （无 skill_exec 记录）
- 终态: OK reason=None gateway=failed latency_ms=57352
- 回复预览: 当前有个任务还在进行，是 code_agent。route=delegate
- 风格评估: 一般

### Case 9: action_continue
- Query: `继续执行上一项并汇报关键节点`
- request_id: `req_4c1af4e089a442ac9838aee950060fd9`
- 首判: route=delegate target=intent_agent source=override llm_used=None conf=0.7 reason=FORCE_OVERRIDE_ACTION_INTENT
- skill: `sub_agent:intent_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=241587
- 回复预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。
- 风格评估: 较符合夏目蓝

### Case 10: rewrite_jp
- Query: `请把这段话润色成更自然的日语口语。`
- request_id: `req_4bfcace237214550b94dde1d4965bc0e`
- 首判: route=delegate target=translate_agent source=llm llm_used=None conf=0.9 reason=用户请求将一段话润色成更自然的日语口语，这属于语言处理和翻译范畴，应委托给 translate_agent 处理。
- skill: `sub_agent:translate_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=64004
- 回复预览: 嗯……您说的这段话を、もっと自然な日本語に……ですね。ごめんなさい……。私の方の翻訳担当が、今、ちょっとお手伝いできないみたいなんです。
- 风格评估: 较符合夏目蓝

### Case 11: deploy_action
- Query: `帮我部署这个服务到测试环境`
- request_id: `req_3b553731a4ff40b39319ab28baeb15ce`
- 首判: route=delegate target=code_agent source=llm llm_used=None conf=0.9 reason=部署服务是一个复杂的代码执行或脚本运行任务，适合委托给 code_agent 处理。
- skill: `sub_agent:code_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=68972
- 回复预览: 嗯……您是想让我部署服务到测试环境，对吧？不过……我这边负责处理这些的……它告诉我，现在好像暂时没办法帮上忙呢。真是抱歉……
- 风格评估: 较符合夏目蓝

### Case 12: summary_action
- Query: `请总结今天完成了什么`
- request_id: `req_48f287b970814431af7a5951ca9859a6`
- 首判: route=delegate target=intent_agent source=rule llm_used=None conf=0.85 reason=RULE_ACTION_INTENT
- skill: `sub_agent:intent_agent` status=OK digest=None
- 终态: OK reason=None gateway=failed latency_ms=255609
- 回复预览: 嗯，我在。刚才那句我想换个更清楚的说法，马上接你。
- 风格评估: 较符合夏目蓝
