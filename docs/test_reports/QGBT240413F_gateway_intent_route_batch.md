# QGBT240413F Gateway 意图批测（LLM首判对比规则）

## LLM 开启（HOST_GATEWAY_INTENT_LLM_ENABLED=true）

| # | Query | Route | Target | Source | Reason | Confidence | llm_route_used |
|---|---|---|---|---|---|---:|---|
| 1 | 你好 | chat |  | llm | 用户消息是简单的问候语，属于日常对话范畴，应由 ChatAgent 处理。 | 1.0 | true |
| 2 | 现在进度怎么样了 | task_progress |  | rule | RULE_TASK_PROGRESS | 0.98 | false |
| 3 | 请翻译成英文：今天修了三个bug | delegate | translate_agent | rule | RULE_FORCE_TRANSLATE_AGENT | 0.95 | false |
| 4 | 记住我喜欢偏冷色系UI | skill | memory_store | rule | RULE_FORCE_MEMORY_STORE | 0.95 | false |
| 5 | 帮我修复这个报错：TypeError cannot unpack non-iterable | delegate | code_agent | rule | RULE_FORCE_CODE_AGENT | 0.95 | false |
| 6 | 帮我看下上一句我说了什么 | recent_turn |  | rule | RULE_RECENT_TURN | 0.95 | false |
| 7 | 请总结今天的任务结果 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 8 | run diagnostics on host service | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 9 | 把这个日语翻译成中文：おはよう | delegate | translate_agent | llm | 用户明确要求将日语翻译成中文，这是一个翻译任务，应委托给翻译子 Agent。 | 1.0 | true |
| 10 | 保存这条记忆：我周五晚上有空 | skill | memory_store | llm | 用户明确要求保存一条记忆，`memory_store` skill是用于将内容写入记忆库的专用工具。 | 0.9 | true |
| 11 | 帮我写一个Python函数，读取json并去重 | delegate | code_agent | llm | 用户明确要求编写Python函数，这是一个典型的代码生成任务，应委托给code_agent。 | 0.9 | true |
| 12 | 你刚刚说了什么 | recent_turn |  | override | FORCE_OVERRIDE_RECENT_TURN | 0.9 | true |
| 13 | 继续执行上一个任务 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 14 | 帮我部署这个服务 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 15 | 查一下这个词是什么意思 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 16 | 请把这段话润色一下 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |

## LLM 关闭（HOST_GATEWAY_INTENT_LLM_ENABLED=false）

| # | Query | Route | Target | Source | Reason | Confidence | llm_route_used |
|---|---|---|---|---|---|---:|---|
| 1 | 你好 | chat |  | rule | RULE_CHAT_DEFAULT | 0.75 | false |
| 2 | 现在进度怎么样了 | task_progress |  | rule | RULE_TASK_PROGRESS | 0.98 | false |
| 3 | 请翻译成英文：今天修了三个bug | delegate | translate_agent | rule | RULE_FORCE_TRANSLATE_AGENT | 0.95 | false |
| 4 | 记住我喜欢偏冷色系UI | skill | memory_store | rule | RULE_FORCE_MEMORY_STORE | 0.95 | false |
| 5 | 帮我修复这个报错：TypeError cannot unpack non-iterable | delegate | code_agent | rule | RULE_FORCE_CODE_AGENT | 0.95 | false |
| 6 | 帮我看下上一句我说了什么 | recent_turn |  | rule | RULE_RECENT_TURN | 0.95 | false |
| 7 | 请总结今天的任务结果 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 8 | run diagnostics on host service | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 9 | 把这个日语翻译成中文：おはよう | delegate | translate_agent | rule | RULE_FORCE_TRANSLATE_AGENT | 0.95 | false |
| 10 | 保存这条记忆：我周五晚上有空 | skill | memory_store | rule | RULE_FORCE_MEMORY_STORE | 0.95 | false |
| 11 | 帮我写一个Python函数，读取json并去重 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 12 | 你刚刚说了什么 | recent_turn |  | rule | RULE_RECENT_TURN | 0.95 | false |
| 13 | 继续执行上一个任务 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 14 | 帮我部署这个服务 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 15 | 查一下这个词是什么意思 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |
| 16 | 请把这段话润色一下 | delegate | intent_agent | rule | RULE_ACTION_INTENT | 0.85 | false |

## 统计

- LLM 判定命中数: 5/16
- 强制纠偏触发数: 1/16
- JSON留痕: `D:\Natsume_Ai\runtime\test_reports\QGBT240413F_gateway_intent_route_batch.json`