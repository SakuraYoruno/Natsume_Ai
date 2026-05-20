# QGBT260520M Emotional Proactive Aftereffect Acceptance

## 结论

本批次补齐“主动之后会怎样影响她”的回流层：

- 主动消息送达并收到用户回复后，生成 `proactive_aftereffects.jsonl`。
- aftereffect 会记录 outcome、reply_mode、reply_warmth、emotional_resolution、loneliness_delta、anticipation_delta、bond_signal、memory_candidate。
- 技术失败不会被解读为关系拒绝。
- 用户不回复时按 `do_not_escalate` 处理，沉入内心流，不升级催促。

## 回流语义

主动 episode 的生命周期现在是：

1. `proactive_episodes.jsonl`: 想说什么、为什么想说、是否会打扰。
2. `proactive_outbox.jsonl`: 允许发送时写入安全 outbox。
3. `delivery_receipts.jsonl`: dry-run 或真实 gateway 回执。
4. `proactive_interactions.jsonl`: 用户回复是否被 Thalamus 识别为主动续接。
5. `proactive_aftereffects.jsonl`: 回复结果如何改变下一次主动冲动。

## 验收重点

- 温暖回应：降低孤独感，生成可记忆的主动情感交换。
- 边界回应：提高下一次主动的克制和 cooldown。
- 未回应：不追问，不升级，不惩罚用户。
- 技术失败：标记为链路问题，不进入关系负反馈。

## 后续

下一批可以继续做真实“长期回流”：把 aftereffect 的 `state_update_hint` 接入长期记忆、关系叙事和主动 cooldown 学习，而不是只作为验收证据落盘。
