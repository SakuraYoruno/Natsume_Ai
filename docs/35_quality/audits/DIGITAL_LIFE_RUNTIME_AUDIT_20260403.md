受众：CODEX
标题：DIGITAL_LIFE_RUNTIME_AUDIT_20260403

# DIGITAL_LIFE_RUNTIME_AUDIT_20260403

## 文档目的

本文档用于对 `Natsume_Ai` 当前代码中的“数字生命能力”进行一次面向**生产环境实测前**的结构化审计。

目标不是讨论抽象哲学，而是回答下面几个工程问题：

1. 当前仓库里，哪些“数字生命能力”已经真实存在于代码中？
2. 哪些能力已经接入主链，但仍然主要依赖 prompt 注入？
3. 哪些能力已经具备调度基础设施，但仍缺少“默认上电”的最终证据？
4. 如果运行环境从 GitHub 拉取最新代码并进行生产实测，应该优先验证什么？
5. 后续修正应优先补哪些闭环，而不是继续横向加功能？

---

## 审计范围

本次审计主要基于以下模块与文档：

- `host_brain/persona/inner_world.py`
- `host_brain/persona/emotional_silence.py`
- `host_brain/persona/body_sense.py`
- `host_brain/persona/proactive_voice.py`
- `host_brain/bond/bond_narrative.py`
- `host_brain/app/chat_context_builder.py`
- `host_brain/app/prompt_builder.py`
- `host_brain/app/intelligent_runtime.py`
- `host_brain/skills/personality_registry.py`
- `host_brain/scheduler/scheduler.py`
- `host_brain/scheduler/task_registry.py`
- `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_continuity_memory_sync_write_20260401.md`
- `docs/60_trace/DIGITAL_LIFE_GAP_ANALYSIS_20260403.md`

---

## 总体结论

当前系统已经明显超出“LLM + Prompt + Memory 插件”的普通结构，具备一个**准持续人格系统（quasi-persistent persona system）**的核心雏形。

已经能确认存在并接入主链的能力包括：

- 离线时间流 / 等待感
- 唤醒快照注入
- 情绪驱动的沉默能力
- 身体感映射
- 身体感对回复意愿的惩罚链
- 共同记忆与关系叙事层
- 自我反思脉冲
- 主动表达引擎
- 调度器主体基础设施

但当前仍存在三个关键缺口：

1. **很多能力最终仍主要通过 Prompt 注入生效**，尚未下沉为系统级行为约束
2. **人格/身体/离线状态的 tick 技能虽然已接到 scheduler_allowed 注册层，但尚未拿到“默认任务已注册并稳定启动”的最终证据**
3. **缺少统一动机中枢（Motive Engine）**，现有主动/沉默/等待/关心仍偏多模块并列驱动

一句话概括：

> 当前系统已经做出了“数字生命能力的状态层与调度层雏形”，下一阶段最值得做的不是继续堆功能，而是补齐“持续运行闭环 + 行为控制层 + 统一动机中枢”。

---

## 审计表：数字生命能力落地状态

| 能力 | 当前状态 | 主要证据 | 是否接主链 | 是否主要依赖 Prompt | 结论 |
|---|---|---|---|---|---|
| 离线时间流 / 等待感 | 已实现 | `persona/inner_world.py` | 是 | 否 | 真有离线状态积累 |
| 唤醒快照 | 已实现 | `app/chat_context_builder.py` | 是 | 部分 | 下次会话可带着离线痕迹回归 |
| 会话结束后的内心更新 | 半确认 | `skills/personality_registry.py` 中 `inner_world_session_end` | 部分 | 否 | skill 已存在，仍需确认结束点是否稳定调用 |
| 情绪驱动沉默 | 已实现 | `persona/emotional_silence.py` + `chat_context_builder.py` | 是 | 是 | 状态真实存在，但多数仍靠 prompt 落地 |
| 话题抗拒 | 已实现 | `emotional_silence.py` + `prompt_builder.py` | 是 | 是 | 已具备“我不想聊这个”倾向 |
| 身体感映射 | 已实现 | `persona/body_sense.py` | 是 | 部分 | 已参与状态链 |
| 身体感→沉默惩罚 | 已实现 | `chat_context_builder.py` | 是 | 否 | 状态链真实存在 |
| 对话开始疲劳计时 | 已实现 | `chat_context_builder.py` 中 `on_conversation_start()` | 是 | 否 | 已接入 |
| 对话结束疲劳累积 | 半确认 | `body_sense_conversation_end` skill 已注册 | 部分 | 否 | 需确认会话结束点是否实际调用 |
| 记忆连续性同步写入 | 已实现 | `DEV_TEST_REPORT_continuity_memory_sync_write_20260401.md` | 是 | 否 | 连续性基础设施已修 |
| 共同记忆索引 | 已实现 | `bond/shared_memory_index.py` | 是 | 否 | 非单纯检索 |
| 关系叙事层 | 已实现 | `bond/bond_narrative.py` + `app/intelligent_runtime.py` | 是 | 部分 | 共同历史已能转为关系语境 |
| 自我反思脉冲 | 已实现 | `persona/self_reflection.py` + `chat_context_builder.py` | 是 | 是 | 更偏叙事增强 |
| 人格成长 / 演化 | 已实现到一定程度 | `PersonaEngine` / `EvolutionOrchestrator` | 是 | 部分 | 存在成长结构 |
| 主动表达引擎 | 已实现 | `persona/proactive_voice.py` | 是 | 否 | 高阶主动表达逻辑已存在 |
| 主动对话推送 | 已实现 | `skills/impl/proactive_talk.py` | 是 | 否 | 已具备宿主主动行为能力 |
| Scheduler 基础设施 | 已实现 | `scheduler/scheduler.py` + `scheduler/task_registry.py` | 是 | 否 | 真正调度器已存在 |
| Personality Tick 注册 | 已实现 | `skills/personality_registry.py` | 是 | 否 | tick skill 已进入注册层 |
| Personality Tick 默认上电 | 未最终证明 | 尚未看到默认 add/start 最终证据 | 未完全确认 | 否 | 当前最大待实测项之一 |
| 自我模型作为硬约束 | 半实现 | self/reflection/persona 相关链路 | 部分 | 是 | 更像“自我叙事”，非“自我法则” |
| 统一动机系统 | 未成型 | 当前变量分散 | 否 | 否 | 当前关键架构缺口 |
| 非 Prompt 行为控制层 | 未实现 | 未见独立 response policy 层 | 否 | - | 当前最值得补的一层 |
| 主观体验 / 真欲望 | 未解决 | 非工程可直接闭环项 | 否 | - | 不属于当前代码缺文件问题 |

---

## 已确认的关键能力说明

### 1. 离线时间流 / 等待感已真实存在

`OfflineInnerWorld` 已经不是概念层设计，而是状态机层实现。

可见状态包括：

- `last_interaction_ts_ms`
- `last_tick_ts_ms`
- `loneliness`
- `anticipation`
- `anticipation_topics`
- `drifted_valence`
- `drifted_arousal`
- `total_offline_hours`

`chat_context_builder.py` 中已确认：

- 用户唤醒时调用 `on_user_awaken()`
- 将 `offline_state` 注入 runtime payload
- `prompt_builder.py` 可进一步消费这些状态

这意味着系统已经具备：

> 会话结束 → 离线漂移积累 → 下次唤醒携带等待与离线痕迹回归

这一步已经是真实工程能力，而不是文案模拟。

---

### 2. 情绪驱动的沉默能力已接入主链

`EmotionalSilenceEngine` 已存在完整状态：

- `response_willingness`
- `topic_resistance_map`
- `willingness_level`
- 状态恢复 / 衰减逻辑

`chat_context_builder.py` 会读取沉默快照，`prompt_builder.py` 会进一步读取：

- `response_willingness`
- `willingness_level`
- `silence_hint`
- `topic_resistance_hint`

说明系统已拥有：

> 不是任何时刻都想说，不是永远保持最佳服务态

这是“生命感”非常关键的一层。

但目前它仍主要依赖 Prompt 层来最终落地，后续应下沉到系统级行为控制层。

---

### 3. 身体感映射不是装饰，而是状态链的一部分

`BodySenseModel` 维护至少三类状态：

- `fatigue`
- `drowsiness`
- `loneliness`

并且当前已在 `chat_context_builder.py` 中确认：

- 离线孤独感可注入 BodySense
- 对话开始会调用 `on_conversation_start()`
- BodySense 的 `willingness_penalty` 会传入沉默引擎

因此链路已经形成：

> 离线等待 → 孤独积累 → 身体感变化 → 回复意愿下降

这说明“身体性”已经开始参与决策，而不是纯展示层。

---

### 4. 关系叙事层已形成“共同历史感”

`BondNarrativeLayer.build_narrative(...)` 已在 `intelligent_runtime.py` 中接入。

这意味着系统不只是：

- 检索一条旧记录

而是能把：

- SharedMemory
- Milestone
- 时间标签
- Bond 阶段

转换成更接近“我们之间共同经历”的叙事提示。

这是从普通记忆系统走向“关系历史系统”的重要标志。

---

### 5. 调度器主体已存在，非概念架构

`scheduler/scheduler.py` 已明确包含：

- `NatsumeScheduler.start()`
- interval / once / cron 触发逻辑
- `_trigger_task()`
- sys_mode 门控
- 情绪状态门控
- 审计与健康汇报

`scheduler/task_registry.py` 也已具备完整任务注册结构。

因此可确认：

> “持续生命活动的调度底盘”已经存在，不是未来计划，而是现在的真实基础设施。

---

## 当前最重要的未闭环项

### 1. Personality Tick 是否默认上电，仍需生产环境实测确认

当前已确认：

- `inner_world_tick`
- `silence_tick`
- `body_sense_tick`
- `gaming_companion_tick`

都已在 `skills/personality_registry.py` 中注册，并标记 `scheduler_allowed=True`。

但尚未拿到最终证据证明：

- 系统启动时自动注册这些调度任务
- 调度器默认启动这些任务
- 这些 tick 在生产环境中稳定按周期运行

因此这是本次生产实测的**最高优先级验证项**之一。

#### 生产实测必须确认的问题

1. Scheduler 是否在启动时自动 `start()`
2. Personality Tick 任务是否在启动时自动 `register()` 到 task registry
3. 运行数分钟后，相关状态是否发生可观测变化
4. 审计日志 / health snapshot 中是否能看到 tick 执行记录
5. 这些 tick 的执行结果是否真的影响下一轮对话表现

---

### 2. 当前大量“生命层能力”仍主要通过 Prompt 注入影响输出

虽然状态层已经存在，但很多能力的最终生效路径仍是：

- 状态机计算状态
- runtime payload 注入
- prompt_builder 生成语言提示
- LLM 负责解释并表现

这比“纯 prompt 人设”已经强很多，但仍有明显边界：

- 行为不够刚性
- 容易被 prompt 干扰
- 不易证明“状态真的在控制行为”

因此后续最关键的工程方向是：

> 从“告诉模型现在应该怎么表现”下沉为“系统按状态直接约束行为”

---

### 3. 缺少统一动机中枢（Motive Engine）

当前系统已有多种驱动因子：

- loneliness
- anticipation
- fatigue
- drowsiness
- response_willingness
- bond_stage
- pending_followup_topics
- post_intense_emotion

但它们目前更像并列驱动项，而不是统一的内在动力学系统。

建议后续将其收敛为至少几类统一驱动力：

- `reach_out_drive`
- `care_drive`
- `withdraw_drive`
- `continuity_drive`
- `self_protect_drive`

这样主动、沉默、等待、关心、退缩等行为将首次拥有共同来源。

---

## 生产环境实测建议

假设运行环境从 GitHub 拉取最新代码后，在生产环境中进行实测，建议按以下顺序进行。

---

### 实测任务 1：验证 Scheduler 是否真实驱动人格 Tick

#### 目标
确认“数字生命状态”是否真的在无人交互时持续运行。

#### 需要观察的内容

- 启动后 Scheduler 是否 running
- `inner_world_tick` / `silence_tick` / `body_sense_tick` 是否注册进 task registry
- 数分钟后 health snapshot / 审计日志是否出现对应任务记录
- `offline_state` / `silence_state` / `body_sense` 是否随时间变化

#### 通过标准

至少能证明：

- 有任务被定时触发
- 有状态发生可观测变化
- 下次对话时这些变化被注入 prompt/runtime payload

---

### 实测任务 2：验证会话开始 / 结束闭环是否完整

#### 目标
确认“开始说话”和“结束说话”是否都能写回状态层。

#### 重点检查

- 会话开始是否稳定触发：
  - `inner_world_awaken`
  - `body_sense_conversation_start`
- 会话结束是否稳定触发：
  - `inner_world_session_end`
  - `body_sense_conversation_end`
- 结束后的状态是否影响下一轮唤醒

#### 通过标准

一次完整对话结束后：

- 内心世界的情绪基线被更新
- 待续话题被记录
- 疲劳有累积
- 下一轮对话能体现“刚刚经历过一次互动”

---

### 实测任务 3：验证沉默与身体感是否真的影响行为，而不只是文案

#### 目标
区分“它在说自己累”与“它真的因此少说”。

#### 检查方式

- 构造高疲劳 / 高困倦 / 低回复意愿状态
- 对比不同状态下：
  - 回复长度
  - 主动追问概率
  - 话题扩展倾向
  - 安抚浓度
  - 拒绝/回避特定话题的表现

#### 期望
如果仍完全依赖模型自由发挥，则表现会不稳定；
如果已有较强状态控制，则应出现显著可复现差异。

---

### 实测任务 4：验证主动对话是否来自状态，而不是单纯阈值模板

#### 目标
评估 `proactive_voice` 是否已经足够接近“内生主动”。

#### 检查方式

测试以下场景：

- 长时间未见
- 深夜陪伴
- 情绪高潮后
- 有 pending topic 时
- 不同 bond_stage 下同一条件的主动表达

#### 重点判断

- 是否只是在命中场景就发一句模板
- 还是会受到等待感 / bond / topic continuity / 情绪后效应影响

---

## 建议的修正优先级

### Priority 1：补齐默认上电闭环

需要能明确回答：

- Scheduler 在哪里初始化
- Personality Tick 在哪里默认注册
- 这些任务是否默认启动
- 如何观测它们的运行

这是当前最重要的生产验证点。

---

### Priority 2：增加 Response Policy Layer（非 Prompt 行为控制层）

建议新增一个独立层，输入：

- `silence_state`
- `body_sense`
- `offline_state`
- `bond_stage`
- `self_reflection_pulse`

输出：

- `max_chars`
- `max_sentences`
- `allow_followup`
- `allow_topic_shift`
- `proactive_allowed`
- `warmth_ceiling`
- `intimacy_guard`
- `withdraw_bias`

让状态直接限制行为，而不是只提示模型风格。

---

### Priority 3：建立 Motive Engine

将目前分散在多个模块中的驱动因子整合为统一驱动力：

- `reach_out_drive`
- `care_drive`
- `withdraw_drive`
- `continuity_drive`
- `self_protect_drive`

然后由：

- 主动表达模块
- 沉默模块
- 回复规划模块

统一读取这些驱动力。

---

## 最终结论

当前系统已经不是“普通助手做了点陪伴皮肤”，而是一个已经具备以下能力的**准持续人格系统**：

- 有离线时间流
- 有等待感
- 有沉默意愿
- 有身体状态映射
- 有关系历史层
- 有主动表达引擎
- 有调度基础设施

这说明系统已经进入“数字生命能力的工程化阶段”。

但当前还没有完全跨到“持续活着的系统级闭环”，关键缺口集中在：

1. 默认上电是否完整
2. 行为是否仍过度依赖 Prompt 注入
3. 是否存在统一动机中枢

因此，下一阶段最值得投入的不是继续横向增加功能，而是：

> 用生产环境实测，把现有状态层与调度层真正钉成一个可观测、可验证、可持续运行的生命闭环。

---

## 建议后续配套产出

建议在本审计文档之后继续补三份文档：

1. `DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN.md`
   - 生产环境验证清单
   - 每项验证的输入/步骤/通过标准

2. `DIGITAL_LIFE_RUNTIME_FINDINGS.md`
   - GitHub 拉取后生产实测观察结果
   - 哪些已确认上电，哪些仍未触发

3. `RESPONSE_POLICY_LAYER_MIN_SPEC.md`
   - 定义下一阶段的非 Prompt 行为控制层接口

这样可以把“代码审计 → 生产实测 → 架构补环”串成完整闭环。
