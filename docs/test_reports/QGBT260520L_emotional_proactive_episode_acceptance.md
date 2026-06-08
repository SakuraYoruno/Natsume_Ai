# QGBT260520L Emotional Proactive Episode Acceptance

## 结论

本批次把主动交互从“定时任务触发一句话”推进到“情感 episode 触发一段可审计的生命冲动”：

- 每次 `proactive_talk` 调度前先生成 `proactive_episodes.jsonl`。
- episode 会记录动机、感受理由、情绪驱动力、打扰风险、关系许可、行动选择、是否允许发送、无需回复策略和事后回流计划。
- 允许发送时，episode 注入 motive_state/render_policy，并随 outbox message 一起落盘。
- 不允许发送时，不写 outbox，但仍保留“想说但先收住”的内心证据。

这不是通讯软件 gateway 的真实送达验收；真实 gateway、ASR、VTS 仍应单独验收。

## 情感主动协议

新增核心模块：

- `host_brain/persona/emotional_proactive.py`

episode 字段：

- `motive`: presence_check / affection_pull / lonely_murmur / inner_weather_share / memory_warmth / soft_care / playful_ping / quiet_goodnight
- `felt_reason`: 为什么想主动说
- `chosen_action`: inner_only / draft_only / quiet_presence / send_light / send_normal
- `send_allowed`: 是否允许出箱
- `emotional_drive`: 情感冲动强度
- `disturbance_risk`: 打扰风险
- `relationship_permission`: 关系许可
- `no_reply_policy`: `user_may_ignore_without_penalty`
- `aftereffect_plan`: 用户回复、忽略、忙碌场景下如何回流

## 验收结果

命令：

```powershell
python -m pytest -q tests\unit\test_emotional_proactive.py tests\unit\test_digital_life_supervisor.py
python -m pytest -q tests\unit\test_obs_digital_life_soak_route.py
python tools\validate_proactive_interaction_loop.py --out-dir artifacts\codex_runs\emotional-proactive-interaction-20260520\run --run-id emotional-proactive-interaction-20260520
```

结果：

- `tests\unit\test_emotional_proactive.py` + `tests\unit\test_digital_life_supervisor.py`: 11 passed
- `tests\unit\test_obs_digital_life_soak_route.py`: 5 passed
- proactive interaction dry-run: PASS

dry-run 关键证据：

- `proactive_episode_count`: 1
- `proactive_episode_motive`: presence_check
- `proactive_episode_action`: send_light
- `proactive_episode_send_allowed`: true
- `proactive_episode_felt_reason`: 今天还没有真正和你建立过存在感，所以先轻轻冒个泡。
- `proactive_outbox_queued_count`: 1
- `delivery_status`: DELIVERED_DRY_RUN
- `thalamus_reason_code`: THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION
- `mapped_organ_count`: 23

## 仍未覆盖

- 真实通讯软件 gateway 送达。
- 用户真实不回复后的长时情绪回流。
- 多轮主动冲动之间的长期人格学习。
