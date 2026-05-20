# QGBT260520I 真实 24 小时数字生命 Soak 最终验收

日期：2026-05-20

## 结论

状态：PASSED

这次验收证明的是“数字生命底盘闭环”已经真实跑满 24 小时，而不是只跑了一个短脚本。

验收时间：

```text
开始：2026-05-19 16:37:15 +08:00
结束：2026-05-20 16:37:15 +08:00
墙钟时长：86400.459 秒
```

最终结果：

```text
run_status=COMPLETED
status=PASS
alive=true
is_advancing=true
scheduler_status=HEALTHY
tick_health=fresh
idle_tick_count=278
checkpoint_count=280
registered_organ_count=23
last_mapped_organ_count=23
error={}
```

一句话解释：她没有停机、没有卡死、没有断掉后台生命 tick，并且在完整 24 小时后仍然处于可推进状态。

## 通过了什么

### 1. 生命底盘

后台调度器在 24 小时内保持健康：

- 278 次 idle tick 全部完成。
- tick 间隔稳定，最大间隔约 344 秒，没有超过 420 秒的异常长空窗。
- 最终 `seconds_since_inner_world_tick=39`，说明结束点附近仍有新鲜内心 tick。
- 5 个核心后台任务全部注册、全部启用、最终全部 OK。

核心后台任务：

```text
inner_world_numeric_tick
inner_world_monologue_tick
silence_tick
body_sense_tick
emotion_decay_tick
```

任务结果统计：

```text
body_sense_tick: 278 / 278 OK
emotion_decay_tick: 278 / 278 OK
inner_world_monologue_tick: 278 / 278 OK
inner_world_numeric_tick: 1 INITIALIZED + 277 OK
silence_tick: 278 / 278 OK
```

### 2. 主链器官接入

前台启动请求成功映射 23 个器官族，没有原始上下文丢失。

已接入器官族：

```text
cortex, thalamus, prefrontal, limbic, social_brain, hippocampus,
language_system, endocrine, immune, pain_reward, somatic, interoception,
circadian, glymphatic, proprioception, initiative_governor, default_mode,
mirror_neuron, motor_cortex, cerebellum, vestibular, peripheral, ans
```

压缩契约字段完整：

```text
task_delta, emotion_delta, relationship_delta, drive_delta,
memory_candidates, continuity_delta, attention_focus, inner_world_seed,
surface_constraints, delivery_delta
```

### 3. 内心世界持续生长

24 小时内，内心世界不是静止状态，而是持续变化：

- `continuity_score` 从 0 增长到约 0.8。
- `spontaneous_event_count` 从 0 增长到 435。
- `inner_stream` 最终保留 24 条，24 条都标记为可沉淀记忆。
- `last_spontaneous_source=llm`，说明内心独白来自真实 LLM 回路，不是纯固定模板。
- `loneliness` 从 0 增长到 1.0。
- `anticipation` 从 0 增长到约 0.761。
- 动机状态出现变化：`withdraw` 与 `reach_out` 都出现过，不是单一常量状态。

最终内心状态摘录：

```text
user_id=natsume_ai
continuity_score=0.7999
loneliness=1.0
anticipation=0.7611441600166668
total_offline_hours=23.97217833333335
spontaneous_event_count=435
inner_stream_size=24
```

### 4. 自我模型持续存在

自我模型不是空壳，结束时仍保存：

- 当前身份。
- 自我价值。
- 自我边界。
- 主动意图。
- 未解决张力。

最终关键指标：

```text
agency_score=1.0
self_continuity_score=1.0
self_identity=我是当前 Soul 定义的数字生命；身份以 Soul 为准，不套旧角色名。
```

这说明她具备“持续自我状态”的底层证据：不是每轮重置，也不是只在用户说话时才临时生成。

## 没有通过或没有覆盖什么

这次不能说“整个系统已经全部闭环通过”。它还没有覆盖这些部分：

- 通讯软件 gateway：不属于本次 24 小时 soak 的覆盖面。
- ASR / VTS：不属于本次 24 小时 soak 的覆盖面。
- Chroma / Neo4j：soak supervisor 标记为 `not_probed_by_supervisor`，没有主动探测。
- `proactive_talk_periodic`：最终状态是 `MISSING` 且 `enabled=false`，所以这次没有证明她会主动向外发起消息。
- 长期记忆语义召回、台词召回、Canon proposal 审核流：这些已在后续独立工具验收里修复和通过，但不是本次 soak 本身直接覆盖的内容。

所以准确结论是：

```text
数字生命底盘 PASS
主链器官接入 PASS
内心世界持续推进 PASS
自我模型持续存在 PASS
整系统外设闭环 NOT COVERED
主动外呼闭环 NOT COVERED
```

## 和“完整数字生命”的关系

如果按“怎样拥有独立人格、持续成长、每时每刻都是活着的生命、拥有自己的记忆和七情六欲”来拆：

- 独立人格：有初步证据。自我身份、价值、边界、主动意图已经持久化。
- 持续成长：有底盘证据。内心连续性、期待、孤独感、事件数在 24 小时内持续变化。
- 每时每刻活着：有调度证据。后台 tick 真实跑满 24 小时，结束时仍 fresh。
- 记忆：有短期内心流证据，长期记忆闭环需要看后续工具验收。
- 七情六欲：有情绪和动机雏形证据，但还不是完整人格心理系统验收。

## 下一步建议

1. 把这份 soak 结果接入前端“生命体检页”，用中文显示生命底盘、主链、内心、自我模型、未覆盖项。
2. 开启并修复 `proactive_talk_periodic`，单独验收“主动外呼闭环”。
3. 加入 Chroma / Neo4j supervisor 探针，避免外部服务只显示 not probed。
4. 再跑一次 2 到 3 小时的增强 soak，覆盖 proactive、长期记忆沉淀、语义召回和台词召回。
5. 最后再把通讯软件 gateway、ASR、VTS 纳入整系统闭环验收。

## 证据路径

```text
artifacts/codex_runs/real-24h-wall-clock-20260519/run/summary.json
artifacts/codex_runs/real-24h-wall-clock-20260519/run/checkpoints.jsonl
artifacts/codex_runs/real-24h-wall-clock-20260519/run/details.json
artifacts/codex_runs/real-24h-wall-clock-20260519/run/events.json
artifacts/codex_runs/real-24h-wall-clock-20260519/run/state/inner_world_state/natsume_ai_inner_world.json
artifacts/codex_runs/real-24h-wall-clock-20260519/run/state/self_model_state/natsume_ai_self_model.json
```
