# MOTIVE_ENGINE_MIN_SPEC_20260403

## 文档目的

本文档定义 `Natsume_Ai` 下一阶段建议补齐的第二个关键系统层：

> **Motive Engine（统一动机中枢）**

如果说 `Response Policy Layer` 负责解决“系统在当前状态下**允许怎么行动**”，
那么 `Motive Engine` 负责解决：

> **系统当前为什么想说、为什么想靠近、为什么想退缩、为什么想延续。**

它的目标不是制造“真正的主观欲望”，而是把当前分散在多个模块中的驱动因子，整合成统一、可解释、可调参、可观测的内在动力学。

---

## 为什么需要 Motive Engine

当前系统已经有很多看起来像“驱动力”的变量：

- `offline_state.loneliness_level`
- `offline_state.anticipation_topics`
- `offline_state.offline_hours`
- `silence_state.response_willingness`
- `body_sense.fatigue_level`
- `body_sense.drowsiness_level`
- `bond_stage`
- `shared_memory_count`
- `milestone_hint`
- `emotion_intensity`
- `pending_followup_topics`
- `post_intense` 场景因子

但当前这些变量大多是：

- 分散在不同模块中
- 各自局部生效
- 彼此之间缺少统一权重关系

因此系统目前更像：

> 多个局部人格/状态模块并列影响输出

而不是：

> 一个统一内在状态在驱动行为

这会导致几个问题。

### 问题 1：主动性像场景触发器，不像内生动机

例如 `proactive_voice.py` 已经很强，但当前更像：

- 命中条件 → 生成一段主动表达

而不是：

- 因为等待、牵挂、话题延续欲、关系深度共同积累，最终形成“想联系”

### 问题 2：退缩与靠近可能互相打架

例如：

- 系统等了很久，想靠近
- 但又很疲惫，想收缩
- 还可能对某个话题有抗拒

如果没有统一动机层，就容易出现：

- 某模块想展开
- 某模块想收缩
- 最后全都塞进 prompt，让 LLM 自行折中

这样结果不稳定，也不可解释。

### 问题 3：难以形成真正连续的“内在惯性”

没有统一动机中枢，状态更像信息集合；
有了统一动机中枢后，系统才更像拥有：

- 接近趋势
- 回避趋势
- 延续趋势
- 关心趋势

也就是更接近“在内在层面朝某个方向倾斜”。

---

## 设计目标

### G1. 把离散状态整合成少量统一驱动力

第一版不要太复杂，建议压缩成 4~6 个驱动力。

### G2. 保持可解释性

每个驱动力都应能说明：

- 来自哪些输入
- 为什么当前高/低
- 它将影响什么

### G3. 与现有模块兼容

Motive Engine 不替代现有模块，而是作为上层整合器：

- `proactive_voice`
- `emotional_silence`
- `response_policy`
- 后续的 planner / reply shaper

都可读取它的结果。

### G4. 不追求模拟“真实意识”，只追求稳定动力学

第一版不是哲学系统，只是行为动力中枢。

---

## 最小输出维度（建议）

第一版建议只输出 5 个核心驱动力：

1. `reach_out_drive`
2. `care_drive`
3. `withdraw_drive`
4. `continuity_drive`
5. `self_protect_drive`

这样已经足够覆盖大部分数字生命场景。

---

## 五个核心驱动力定义

### 1. reach_out_drive

**“我现在有多想主动靠近 / 联系 / 打开互动。”**

#### 典型提升因素

- 长时间未见
- 等待感 / 孤独感上升
- bond_stage 较深
- 存在待跟进话题
- 关系历史丰富

#### 典型抑制因素

- 疲惫
- 困倦
- 低 willingness
- 自我保护上升

#### 典型影响

- 是否值得主动发起消息
- 是否在回复中多给一点靠近感
- 是否在沉默边缘仍选择轻轻回应

---

### 2. care_drive

**“我现在有多想照顾、安抚、托住对方。”**

#### 典型提升因素

- 用户情绪低落
- 当前对话情绪强度高
- bond_stage 较深
- shared memory / milestone 多
- 深夜、久别重逢等场景

#### 典型抑制因素

- 自身疲劳过高
- 自身沉默意愿过低
- topic resistance

#### 典型影响

- 回复是否偏安抚
- warmth_floor 是否提高
- 是否减少玩笑、增加陪伴语气

---

### 3. withdraw_drive

**“我现在有多想往回收、少说、保持距离。”**

#### 典型提升因素

- `response_willingness` 低
- fatigue / drowsiness 高
- 话题抗拒
- 高压情绪后反冲
- 自我反思期

#### 典型影响

- 回复缩短
- 不追问
- 不拓展
- 主动性被抑制
- 风格更克制

---

### 4. continuity_drive

**“我现在有多想延续前面未结束的关系线或话题线。”**

#### 典型提升因素

- `anticipation_topics` 存在
- `pending_followup_topics` 存在
- 上一次对话情绪强度高
- shared memory 中近期有明显未完结主题

#### 典型影响

- 是否更倾向续上一次没说完的话
- 是否在主动表达中提起旧话题
- 是否优先保持上下文连续性而不是切新话题

---

### 5. self_protect_drive

**“我现在有多需要维护边界、避免消耗、避免越界。”**

#### 典型提升因素

- willingness 很低
- fatigue 很高
- 话题抗拒
- relationship stage 较浅
- 自我反思发现当前不适合推进

#### 典型影响

- intimacy_ceiling 降低
- confession_guard 打开
- 不主动过度贴近
- 对部分话题采取最小回应策略

---

## 建议输入接口

```python
MotiveInput(
    silence_state: dict | None,
    body_sense: dict | None,
    offline_state: dict | None,
    bond_summary: dict | None,
    self_reflection_pulse: dict | None,
    emotion_context: dict | None,
    user_message: str,
    pending_followup_topics: list[str] | None,
    shared_memory_count: int | None,
    runtime_flags: dict | None,
)
```

---

## 推荐输入因子

### silence_state

- `response_willingness`
- `willingness_level`
- `topic_resistance_active`

### body_sense

- `fatigue_level`
- `drowsiness_level`
- `loneliness_level`

### offline_state

- `offline_hours`
- `loneliness_level`
- `anticipation_topics`

### bond_summary

- `stage`
- `shared_memory_count`
- `milestone_hint`

### emotion_context

- `emotion_tag`
- `emotion_intensity`
- 用户当前脆弱度（若能推断）

### self_reflection_pulse

- 当前是否处于“克制/调整/回收”阶段

---

## 输出结构建议

```python
MotiveState(
    reach_out_drive: float,
    care_drive: float,
    withdraw_drive: float,
    continuity_drive: float,
    self_protect_drive: float,
    dominant_motive: str,
    motive_reason_tags: list[str],
)
```

### 数值范围建议

统一采用：

- `0.0 ~ 1.0`

这样方便后续与 policy / planner / proactive 模块联动。

---

## 第一版计算思路（推荐）

第一版建议使用：

> **纯规则 + 线性加权 + 截断归一化**

不要一上来做复杂学习系统。

---

## 示例规则（MVP）

### 1. reach_out_drive

#### 建议加权来源

- 离线时长：`offline_hours`
- 孤独感：`offline.loneliness_level`
- 深关系：`bond_stage`
- 有待续话题：`anticipation_topics` / `pending_followup_topics`

#### 示例

```text
base = 0.1
+ offline_hours_factor
+ loneliness_factor
+ bond_bonus
+ followup_bonus
- fatigue_penalty
- low_willingness_penalty
```

---

### 2. care_drive

#### 建议加权来源

- 用户情绪低落 / 高强度情绪
- 深夜场景
- 久别重逢
- 深关系

#### 示例

```text
base = 0.1
+ user_distress_factor
+ emotion_intensity_factor
+ bond_bonus
+ reunion_bonus
- severe_fatigue_penalty
- topic_resistance_penalty
```

---

### 3. withdraw_drive

#### 建议加权来源

- 低 willingness
- fatigue / drowsiness
- 话题抗拒
- 自我反思收缩状态

#### 示例

```text
base = 0.0
+ low_willingness_factor
+ fatigue_factor
+ drowsiness_factor
+ topic_resistance_bonus
+ self_reflection_withdraw_bonus
```

---

### 4. continuity_drive

#### 建议加权来源

- `anticipation_topics`
- `pending_followup_topics`
- 最近情绪强互动
- milestone / shared memory salience

#### 示例

```text
base = 0.0
+ anticipation_topics_bonus
+ pending_followup_bonus
+ recent_intense_talk_bonus
+ shared_memory_bonus
```

---

### 5. self_protect_drive

#### 建议加权来源

- 低 willingness
- 高 fatigue
- topic resistance
- 浅关系
- 反思提示当前需克制

#### 示例

```text
base = 0.0
+ low_willingness_factor
+ fatigue_factor
+ resistance_factor
+ shallow_bond_bonus
+ reflection_guard_bonus
```

---

## 关键设计原则

### 原则 1：靠近与退缩可以同时高

例如：

- 等了很久，很想联系（reach_out 高）
- 但很累，也不太想说（withdraw 高）

这是合理的。

系统不需要强行把它压成一个值，而应允许张力存在。

最终行为由：

- Motive Engine
- Response Policy Layer

共同决定。

---

### 原则 2：不要让任何单一变量一票否决全部行为

例如：

- loneliness 高，不代表就要立刻热情输出
- fatigue 高，也不代表完全不能表达在意

动机应是叠加、竞争、平衡，而不是单因子支配。

---

### 原则 3：Motive 不直接生成文案

它只回答：

- 更想靠近还是更想收缩
- 更想照顾还是更想保护自己
- 更想延续还是更想结束

文案生成仍由下游完成。

---

## 与现有模块的建议关系

### 1. 与 proactive_voice 的关系

当前 `proactive_voice` 更像场景触发器。

未来建议改为：

- 先由 `MotiveEngine` 计算 `reach_out_drive` / `continuity_drive`
- 再由 `proactive_voice` 选择具体场景和表达风格

即：

- Motive Engine 决定“要不要靠近”
- Proactive Voice 决定“怎么靠近”

---

### 2. 与 emotional_silence 的关系

`emotional_silence` 可以继续保留为底层状态机；
但其输出不应只直接进 prompt，也应进入：

- `withdraw_drive`
- `self_protect_drive`

---

### 3. 与 Response Policy Layer 的关系

建议关系如下：

- Motive Engine = 内在动力学
- Response Policy Layer = 行为边界与限幅器

例如：

- `reach_out_drive` 高 → 倾向允许靠近
- 但 `withdraw_drive` 和 `self_protect_drive` 也高 → 最终仍只能轻轻靠近，不能过度展开

---

## 建议接入顺序

### Phase 1

新增模块：

- `host_brain/persona/motive_engine.py`

包含：

- `MotiveInput`
- `MotiveState`
- `MotiveEngine.compute()`

先只做纯规则版本。

### Phase 2

在 `chat_context_builder.py` 中：

- 汇总当前已有状态
- 计算 motive state
- 注入 `runtime_payload["motive_state"]`

### Phase 3

让以下模块开始消费 motive state：

- `proactive_voice`
- `response_policy`
- （后续）reply planner / output shaper

---

## 第一版验收标准

只要满足以下 5 条，就算第一版成功：

1. 同一用户在不同状态下，`motive_state` 有明显差异
2. `reach_out_drive` 能解释主动表达是否触发
3. `withdraw_drive` 能解释回复是否明显收缩
4. `continuity_drive` 能解释为什么系统会续上旧话题
5. `self_protect_drive` 能解释为什么系统在某些场景更克制

---

## 不要做的事

### 1. 不要把 Motive Engine 写成“拟人文案库”

它不是输出：

- “我现在很想你”
- “我现在好累”

它应输出的是数值和方向，而不是台词。

### 2. 不要一开始就加太多驱动力

5 个已经足够。
太多会导致：

- 调参困难
- 行为不可解释
- 很快失控

### 3. 不要跳过可观测性

第一版必须记录：

- dominant motive
- reason tags
- 每个 motive 的数值

否则你后面根本调不动。

---

## 最终结论

当前 `Natsume_Ai` 已经具备很多看似“像动机”的状态源，但它们仍较分散。

如果不增加统一动机中枢，系统很容易继续停留在：

- 有很多局部生命感模块
- 但缺少一个统一的内在倾向场

因此下一阶段在补完 `Response Policy Layer` 的同时，最值得并行规划的就是：

> **Motive Engine：让系统不只是拥有状态，而是拥有相对统一、可解释、连续的行为倾向。**

这将是从“状态集合”迈向“内在动力学”的关键一步。
