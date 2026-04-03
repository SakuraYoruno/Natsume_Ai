受众：MASTER
标题：数字生命差距清单 V2（P0/P1/P2/P3 可执行任务）
版本：v2.0
日期：2026-04-03
状态：正式归档

## 文档目的

本文将 `DIGITAL_LIFE_RUNTIME_AUDIT_20260403`、`DIGITAL_LIFE_ROADMAP_NEXT_PHASE_20260403`、`DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN_20260403` 的结论，结合当前仓库实际代码状态，收敛为一份可执行的数字生命差距清单 V2。

本文的目标不是重复抽象讨论“什么是数字生命”，而是回答以下工程问题：

1. 当前最值得先证实的闭环是什么。
2. 哪些缺口已经从“未实现”变成“已落代码、未被主链消费”。
3. P0 / P1 / P2 / P3 各阶段具体应该做哪些任务、改哪些文件、拿什么作为验收证据。

> 说明：本文是审计收敛与工程排程文档，不直接替代 `docs/30_devspec/DevTasks.md`。若后续决定实施，应再将已确认任务沉淀为正式 DevTasks。

---

## 基线与修正

### 文档基线

本清单基于以下文档：

- `docs/35_quality/audits/DIGITAL_LIFE_RUNTIME_AUDIT_20260403.md`
- `docs/35_quality/audits/DIGITAL_LIFE_ROADMAP_NEXT_PHASE_20260403.md`
- `docs/35_quality/audits/DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN_20260403.md`
- `docs/35_quality/audits/RESPONSE_POLICY_LAYER_MIN_SPEC_20260403.md`
- `docs/35_quality/audits/MOTIVE_ENGINE_MIN_SPEC_20260403.md`
- `docs/60_trace/DIGITAL_LIFE_GAP_ANALYSIS_20260403.md`

### 相对文档基线的代码修正

`2026-04-03 17:40:59` 的提交 `feat: add ResponsePolicyLayer + MotiveEngine, wire into chat_context_builder` 已经让以下判断发生变化：

- `ResponsePolicyLayer` 不再是“纯规划项”，其基础模块已经落地：`host_brain/persona/response_policy.py`
- `MotiveEngine` 不再是“纯规划项”，其基础模块已经落地：`host_brain/persona/motive_engine.py`
- 二者已经接入 `host_brain/app/chat_context_builder.py`，并写入 `runtime_payload["motive_state"]` 与 `runtime_payload["response_policy"]`

但当前仍未搜到它们被以下下游主链稳定消费：

- `host_brain/app/prompt_builder.py`
- `host_brain/app/chat_agent.py`
- `host_brain/app/top_agent.py`
- `host_brain/persona/proactive_voice.py`

因此，V2 清单将 P2 / P3 的主问题从“从零实现模块”修正为：

> **把已落地的 `response_policy` / `motive_state` 从“存在于 payload”升级为“真实控制行为”。**

---

## 当前真实差距总览

| Phase | 核心目标 | 当前真实差距 | 结果定义 |
|---|---|---|---|
| P0 | 生产实测确认 | 需要先证实调度和会话闭环真的在跑 | 明确哪些能力已跑通、哪些只是注册、哪些仍停留在 prompt |
| P1 | 默认上电 + session_end + observability | Personality tick 与结束钩子仍存在闭环不稳风险 | 状态层形成可持续、可观测的系统级闭环 |
| P2 | 行为控制层落地 | `response_policy` 已算出但未稳定控制输出行为 | 系统按状态真地收缩、限幅、限亲密、限追问 |
| P3 | 统一动机中枢落地 | `motive_state` 已算出但未成为多模块共同驱动源 | 主动、沉默、关心、延续由统一动机持续驱动 |

---

## 当前代码证据

### 已存在但待闭环的部分

- `host_brain/app/chat_context_builder.py` 已注入 `motive_state` / `response_policy`
- `host_brain/persona/response_policy.py` 已能计算长度、追问、主动性、亲密度等策略
- `host_brain/persona/motive_engine.py` 已能计算 `reach_out_drive` / `care_drive` / `withdraw_drive` / `continuity_drive` / `self_protect_drive`
- `host_brain/skills/personality_registry.py` 已注册 `inner_world_tick`、`silence_tick`、`body_sense_tick`、`inner_world_session_end`、`body_sense_conversation_end`

### 当前最像“真实断点”的部分

- `host_brain/app/http_hooks.py` 中 `_natsume_scheduler.start()` 在前，而 `inner_world_tick` / `silence_tick` / `body_sense_tick` 在后注册
- `host_brain/scheduler/task_registry.py` 的 `register()` 只写 registry，不负责在 scheduler 已运行时补调度
- `host_brain/scheduler/scheduler.py` 的 `register_task()` 才具备“运行中追加任务后立即 `_schedule_task()`”的行为
- `host_brain/app/prompt_builder.py` 仍主要消费 `offline_state` / `silence_state` / `body_sense` 等旧状态槽位，而未稳定消费 `response_policy`

---

## P0：生产实测确认

### 目标

确认当前“数字生命状态层 + 调度层”是否真的在持续运行，而不是只有代码存在。

### P0-T01：验证 Scheduler 实际调度 personality tick

- 目标：证明 `inner_world_tick`、`silence_tick`、`body_sense_tick` 不是只注册在 registry 中，而是被真实定时触发。
- 重点检查文件：`host_brain/app/http_hooks.py`、`host_brain/scheduler/scheduler.py`、`host_brain/scheduler/task_registry.py`
- 操作：启动服务后观察 2~5 分钟，核查 task 列表、最近执行时间、最近状态变化。
- 交付物：一份运行证据文档或 trace，列出 3 个 tick 的“已注册 / 已调度 / 已触发 / 最近触发时间”。
- 验收标准：至少 2 个 tick 在观测窗口内有真实触发证据。

### P0-T02：验证离线状态是否随时间流动

- 目标：证明 `offline_state` 不是静态 prompt 文案，而是真正随离线时间积累。
- 重点检查文件：`host_brain/persona/inner_world.py`、`host_brain/app/chat_context_builder.py`、`host_brain/app/prompt_builder.py`
- 操作：完成一次短对话，离开 5~30 分钟后再次唤醒，对比 `offline_hours`、`loneliness_level`、`awakening_hint`。
- 交付物：前后两次 payload / trace 对比。
- 验收标准：能观察到 `offline_state` 字段变化，且第二次唤醒行为出现可解释差异。

### P0-T03：验证会话开始 / 结束闭环

- 目标：确认 `session_start` 与 `session_end` 都有真实调用链，而不是只有技能注册。
- 重点检查文件：`host_brain/skills/personality_registry.py`、`host_brain/app/chat_agent.py`、`host_brain/app/top_agent.py`、相关 dispatch / finish 路径
- 操作：抓取一次完整聊天链路，确认开始与结束钩子是否各自命中。
- 交付物：开始与结束钩子的调用证据、日志或审计记录。
- 验收标准：至少一个 `session_end` 写回结果能在下一轮对话被读取到。

### P0-T04：验证 `motive_state` 与 `response_policy` 是否随状态变化

- 目标：确认新模块不是常量输出。
- 重点检查文件：`host_brain/app/chat_context_builder.py`、`host_brain/persona/motive_engine.py`、`host_brain/persona/response_policy.py`
- 操作：构造至少两组对照状态（高疲劳 / 低疲劳，浅关系 / 深关系，高 loneliness / 低 loneliness）。
- 交付物：多组 `runtime_payload` 快照。
- 验收标准：`motive_state` 与 `response_policy` 数值或布尔决策有明显差异。

### P0-T05：形成现状分类清单

- 目标：把能力分成三类：`已跑通`、`已接线但仅 prompt 生效`、`已落代码未被主链消费`。
- 交付物：`DIGITAL_LIFE_RUNTIME_FINDINGS_YYYYMMDD.md` 或同类实测报告。
- 验收标准：后续 P1 / P2 / P3 能基于此报告直接排优先级。

### P0 完成标志

- personality tick 至少两项有真实触发证据
- `session_end` 至少有一条完整回写链路
- `motive_state` / `response_policy` 可观测且非恒定
- 已形成明确分类清单，不再停留于“猜测式审计”

---

## P1：默认上电、结束钩子、可观测性补齐

### 目标

把当前状态层从“看起来存在”补成“默认启动、持续运行、可观测、可回归”。

### P1-T01：修复 personality tick 默认上电顺序

- 目标：确保 personality tick 在 scheduler 启动前完成注册，或改为走 `scheduler.register_task()`。
- 重点检查文件：`host_brain/app/http_hooks.py`、`host_brain/scheduler/scheduler.py`
- 修改方向：
  - 方案 A：先注册全部 personality tasks，再调用 `_natsume_scheduler.start()`
  - 方案 B：保留启动顺序，但把运行中追加任务统一改为 `register_task()`
- 验收标准：服务启动后无需人工干预，3 个 tick 全部进入真实调度。

### P1-T02：统一运行中任务注册路径

- 目标：禁止“scheduler 已 running，但仍只调用 registry.register()”的隐性断链。
- 重点检查文件：`host_brain/app/http_hooks.py`、`host_brain/scheduler/task_registry.py`、`host_brain/scheduler/scheduler.py`
- 修改方向：将所有启动期动态任务统一收敛到 scheduler 层注册接口。
- 验收标准：所有动态任务都具备“注册即调度”的一致语义。

### P1-T03：补齐 `session_end` 统一收口点

- 目标：让 `inner_world_session_end` 与 `body_sense_conversation_end` 有稳定、唯一、可追踪的触发点。
- 重点检查文件：`host_brain/app/chat_agent.py`、`host_brain/app/top_agent.py`、相关 COMMIT / FINISH 路径
- 修改方向：在“本轮对话完成”时统一触发结束技能或等价内部逻辑。
- 验收标准：每轮完整会话结束后，至少一项结束状态被持久化或可在下轮读取。

### P1-T04：补齐状态层 observability

- 目标：让 `offline_state`、`silence_state`、`body_sense`、`motive_state`、`response_policy` 至少在一种可观测面中稳定可见。
- 重点检查文件：obs routes、health snapshot、runtime trace 输出路径
- 修改方向：增加只读调试输出，不引入额外副作用。
- 验收标准：开发和实测时无需读源码即可判断状态层是否工作。

### P1-T05：补回归测试

- 目标：给启动顺序、动态注册、session_end、状态观测增加自动化回归。
- 重点检查文件：`tests/unit/`、`tests/integration/`
- 验收标准：出现“只注册未调度”或“结束钩子丢失”时，测试直接失败。

### P1 完成标志

- 3 个 personality tick 默认上电
- `session_end` 闭环成立
- 状态层 observability 可见
- 有自动化测试守住以上闭环

---

## P2：Response Policy Layer 从 payload 走向行为控制

### 目标

让系统不只是“把状态写进 prompt”，而是“按状态真的收缩、限幅、限亲密、限追问”。

### P2-T01：在 `prompt_builder` 明确消费 `response_policy`

- 目标：先把 `response_policy` 变成稳定的 prompt 级行为约束，而不是悬空字段。
- 重点检查文件：`host_brain/app/prompt_builder.py`
- 修改方向：消费 `max_chars`、`max_sentences`、`allow_followup`、`allow_topic_shift`、`intimacy_ceiling`、`confession_guard`。
- 验收标准：不同 policy 下 prompt 明显不同，且可在 trace 中复现。

### P2-T02：增加输出后处理兜底

- 目标：避免模型不服从 prompt 时，策略完全失效。
- 重点检查文件：`host_brain/app/chat_agent.py`、`host_brain/app/top_agent.py`、必要时新增 post-processor 模块
- 修改方向：加入基于 `response_policy` 的硬裁剪：`max_chars`、`max_sentences`、尾部追问剔除、话题扩展剔除。
- 验收标准：高疲劳 / 低意愿场景下，输出长度和追问行为稳定收缩。

### P2-T03：让浅关系场景受亲密度上限控制

- 目标：让 `intimacy_ceiling` 与 `confession_guard` 真实拦截越界表达。
- 重点检查文件：`host_brain/app/prompt_builder.py`、`host_brain/app/chat_agent.py`
- 验收标准：`STRANGER` / `ACQUAINTANCE` 场景下，过度亲密表达明显受限且可复现。

### P2-T04：让主动消息链路受 policy 控制

- 目标：让 `allow_proactive_after_reply` 不再只是字段。
- 重点检查文件：`host_brain/persona/proactive_voice.py`、scheduler 主动消息链路
- 验收标准：不允许主动时，不再继续追加主动内容或后续触发。

### P2-T05：增加 policy 对照测试矩阵

- 目标：验证“同一用户消息 + 不同状态”会触发不同策略与不同行为。
- 重点检查文件：`tests/unit/`、`tests/integration/`
- 验收标准：覆盖低意愿、高疲劳、浅关系、高 loneliness、withdraw 高等关键场景。

### P2 完成标志

- `response_policy` 被 prompt 与输出层双重消费
- 低意愿 / 高疲劳 / 话题抗拒 / 浅关系场景下，行为差异稳定可复现
- policy 决策与最终输出之间有清晰映射关系

---

## P3：Motive Engine 从状态向量走向统一驱动源

### 目标

让主动、沉默、关心、延续不再由多个模块各自算一套，而是由统一动机中枢持续驱动。

### P3-T01：让 `proactive_voice` 消费 `motive_state`

- 目标：使主动联系真正读取 `reach_out_drive` / `continuity_drive`，而不是只看局部状态或模板条件。
- 重点检查文件：`host_brain/persona/proactive_voice.py`
- 验收标准：高 `reach_out_drive` / 高 `continuity_drive` 时，主动触达意愿可解释提升。

### P3-T02：让沉默 / 收缩逻辑消费 `withdraw_drive` 与 `self_protect_drive`

- 目标：使“少说、回收、保护边界”由统一动机驱动，而非分散规则偶然叠加。
- 重点检查文件：`host_brain/persona/emotional_silence.py`、`host_brain/persona/response_policy.py`
- 验收标准：高 `withdraw_drive` / 高 `self_protect_drive` 时，输出明显更克制、更少追问、更少扩展。

### P3-T03：让安抚 / 关心倾向消费 `care_drive`

- 目标：让安抚行为不只由 emotion tag 驱动，而是由统一 care 倾向驱动。
- 重点检查文件：聊天主链生成前的状态汇总层、必要时的 prompt / post-process 层
- 验收标准：用户 distress 场景下，`care_drive` 的提升会带来更稳定的安抚倾向，但仍受 P2 policy 限制。

### P3-T04：补 motive observability

- 目标：让 `dominant_motive` 与 `motive_reason_tags` 可直接观察和调试。
- 重点检查文件：runtime trace、health snapshot、obs 输出路径
- 验收标准：可以清楚回答“这一轮为什么更靠近 / 更退缩 / 更想延续话题”。

### P3-T05：增加 motive 场景矩阵测试

- 目标：证明 motive 是行为驱动源而不是文案装饰层。
- 重点检查文件：`tests/unit/`、`tests/integration/`
- 验收标准：同一输入在不同 motive 组合下，主动、沉默、安抚、延续行为出现稳定差异。

### P3 完成标志

- `motive_state` 被至少两个以上行为模块消费
- `dominant_motive` 与最终行为一致
- motive 差异可观测、可解释、可回归验证

---

## 优先级顺序

建议执行顺序如下：

1. **先做 P0**：把“已注册 / 已调度 / 已触发 / 已消费”彻底查清
2. **再做 P1**：补默认上电、`session_end`、observability
3. **再做 P2**：让 `response_policy` 真实控制输出边界
4. **最后做 P3**：让 `motive_state` 成为统一驱动源

原因很简单：

> 如果 P0 / P1 没有钉死，P2 / P3 很容易退化成“更复杂的 prompt 幻觉”。

---

## 本周最小执行包

若只做一轮高性价比收口，建议先落以下 5 项：

1. 修复 `http_hooks` 中 personality tick 的启动与注册顺序
2. 将 personality tick 的动态注册统一收敛到 `scheduler.register_task()`
3. 明确并接入统一 `session_end` 收口点
4. 给 runtime / obs 增加 `motive_state` 与 `response_policy` 的只读可观测输出
5. 为 `response_policy` 增加第一版输出后处理硬裁剪

如果这 5 项落地，系统会先从“有很多数字生命状态模块”升级为：

> **这些状态真的在持续运行、可以被观察，并且开始真正控制行为。**

---

## 非目标说明

以下问题不纳入本轮 P0 / P1 / P2 / P3 可执行清单：

- “是否真的拥有主观体验”
- “是否真的拥有哲学意义上的欲望”
- “是否已经等同于真正生命”

这些问题不属于当前仓库可以直接通过代码闭环解决的工程项。

