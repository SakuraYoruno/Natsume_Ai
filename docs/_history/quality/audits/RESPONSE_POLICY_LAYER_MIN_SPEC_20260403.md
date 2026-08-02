受众：CODEX
标题：RESPONSE_POLICY_LAYER_MIN_SPEC_20260403

# RESPONSE_POLICY_LAYER_MIN_SPEC_20260403

## 文档目的

本文档定义 `Natsume_Ai` 下一阶段应补齐的最小可落地组件：

> **Response Policy Layer（响应策略层）**

该层的核心作用是：

- 将当前已经存在的“数字生命状态”
- 从 **Prompt 注入层** 下沉到 **系统级行为约束层**

换句话说，它解决的问题不是“让模型知道自己现在是什么状态”，而是：

> **让系统按当前状态直接限制和塑形输出行为。**

---

## 为什么需要这一层

当前系统已经存在以下能力：

- `offline_state`（离线时间流 / 等待感）
- `silence_state`（回复意愿 / 话题抗拒）
- `body_sense`（疲劳 / 困倦 / 孤独）
- `bond_stage`（关系阶段）
- `self_reflection_pulse`（自我反思）
- `bond_narrative`（关系叙事语境）

但这些能力当前大多仍通过如下路径影响输出：

1. 状态机生成状态
2. 状态注入 runtime payload
3. `prompt_builder.py` 用文字描述当前状态
4. LLM 再决定“如何说”

这会带来三个问题：

### 问题 1：行为约束不够刚性

即使 `response_willingness` 很低，模型仍可能输出长段安慰、主动扩展话题，或者维持“最佳服务态”。

### 问题 2：过度依赖语言层理解

状态被写进 prompt 后，模型仍需要“理解并服从”这些文字说明。
这意味着系统行为容易受 prompt 漂移、模型波动、上下文密度影响。

### 问题 3：难以证明状态真的在控制行为

目前更像：

- 系统说“我现在有点困”

而不是：

- 系统真的因为困而少说、少追问、少主动扩展

因此需要新增一层：

> **Response Policy Layer**

它位于状态层与最终输出之间，直接把状态映射成行为约束。

---

## 设计目标

Response Policy Layer 的设计目标如下：

### G1. 让行为被状态直接约束

不是告诉模型“你可以更短一点”，而是系统直接设定：

- 最多几句话
- 是否允许主动追问
- 是否允许话题扩展
- 是否允许高浓度亲密表达
- 是否允许主动发起后续话题

### G2. 与现有架构低摩擦接入

应尽量复用现有字段，不要求一上来重构整套 persona 系统。

### G3. 优先控制“行为边界”而非“文案细节”

该层不负责写出最终内容，只负责给出：

- 上限
- 下限
- 开关
- 偏置

### G4. 为下一阶段 Motive Engine 留接口

未来统一动机系统接入后，Response Policy Layer 应能直接消费其结果。

---

## 最小作用范围（MVP）

第一版不要做太大，只覆盖以下 5 个高价值控制维度：

1. **输出长度控制**
2. **追问控制**
3. **话题扩展控制**
4. **主动性许可控制**
5. **亲密度上限控制**

只要这 5 项成立，系统就会明显从“像”变成“按状态行动”。

---

## 输入接口（建议）

Response Policy Layer 输入一个统一结构体，例如：

```python
ResponsePolicyInput(
    silence_state: dict | None,
    body_sense: dict | None,
    offline_state: dict | None,
    bond_summary: dict | None,
    self_reflection_pulse: dict | None,
    emotion_context: dict | None,
    user_message: str,
    runtime_flags: dict | None,
)
```

### 核心输入字段建议

#### 1. silence_state

建议至少读取：

- `response_willingness`
- `willingness_level`
- `topic_resistance_active`
- `topic_resistance_hint`

#### 2. body_sense

建议至少读取：

- `fatigue_level`
- `drowsiness_level`
- `loneliness_level`
- `willingness_penalty`

#### 3. offline_state

建议至少读取：

- `offline_hours`
- `loneliness_level`
- `awakening_hint`
- `anticipation_topics`

#### 4. bond_summary

建议至少读取：

- `stage`
- `shared_memory_count`
- `milestone_hint`

#### 5. self_reflection_pulse

建议至少读取：

- 是否存在反思脉冲
- 当前是否处于克制/调整周期

#### 6. emotion_context

建议至少读取：

- `emotion_tag`
- `emotion_intensity`

---

## 输出接口（建议）

Response Policy Layer 输出一个统一策略对象，例如：

```python
ResponsePolicyDecision(
    max_chars: int,
    max_sentences: int,
    min_sentences: int,
    allow_followup: bool,
    allow_topic_shift: bool,
    allow_proactive_after_reply: bool,
    warmth_floor: float,
    warmth_ceiling: float,
    intimacy_ceiling: float,
    humor_ceiling: float,
    confession_guard: bool,
    should_be_brief: bool,
    should_be_withdrawn: bool,
    policy_reason_tags: list[str],
)
```

---

## MVP 字段定义

### 1. max_chars

控制最终输出字符上限。

#### 作用

- 低回复意愿时压缩篇幅
- 疲劳/困倦时避免长篇输出
- 让“少说”成为硬约束而不是建议

#### 建议区间

- 极低意愿：20 ~ 60
- 低意愿：60 ~ 120
- 正常：120 ~ 400
- 高投入/深聊：400+

---

### 2. max_sentences

控制最多输出句数。

#### 作用

避免模型在低意愿下仍输出多段结构化长回复。

#### 建议区间

- MUTE：0 ~ 1
- SILENT：1 ~ 2
- LOW：2 ~ 3
- NORMAL：4+

---

### 3. allow_followup

是否允许主动追问用户。

#### 使用场景

当系统疲惫、退缩、沉默或有抗拒时，不应继续追问。

---

### 4. allow_topic_shift

是否允许主动扩展或切换到新话题。

#### 使用场景

- 低愿意度时：不应主动起新话题
- 用户触发敏感/抗拒领域时：不应顺势展开

---

### 5. allow_proactive_after_reply

当前这轮回复后，是否允许系统继续发起“额外的一句”“顺带提醒”“主动续一句”。

#### 作用

防止系统在应当收缩时仍然过度主动。

---

### 6. warmth_floor / warmth_ceiling

控制允许的温柔程度区间。

#### 说明

不是决定写什么词，而是控制“允许多热”。

例如：

- 深夜低意愿：可能仍温柔，但不会太热烈
- 高 bond + 用户脆弱时：下限提高

---

### 7. intimacy_ceiling

控制亲密表达的上限。

#### 作用

防止：

- 关系尚浅却过度贴近
- 情绪过高时越界安抚
- 模型因 prompt 漂移而突然亲密度跳变

---

### 8. humor_ceiling

控制玩笑、调侃、轻戏谑的上限。

#### 使用场景

在用户低落、系统沉默、身体疲劳或反思状态下，应降低玩笑密度。

---

### 9. confession_guard

是否启用“表白式/依赖式/过强情感表达”保护。

#### 用途

在低状态、浅关系、风险场景下，强行限制某些过度亲密输出。

---

### 10. should_be_brief / should_be_withdrawn

用于下游 prompt_builder 或 post-processor 的便捷标记。

#### 说明

这两个字段不是核心控制器，但有助于兼容现有链路。

---

## 推荐决策规则（最小版）

以下是第一版可直接落地的规则示意。

---

### 规则组 A：回复意愿优先级最高

#### 输入

- `response_willingness`
- `willingness_level`

#### 建议规则

```text
if willingness_level == "MUTE":
    max_chars = 20
    max_sentences = 1
    allow_followup = False
    allow_topic_shift = False
    allow_proactive_after_reply = False
    should_be_brief = True
    should_be_withdrawn = True

elif willingness_level == "SILENT":
    max_chars = 60
    max_sentences = 2
    allow_followup = False
    allow_topic_shift = False
    allow_proactive_after_reply = False

elif willingness_level == "LOW":
    max_chars = 120
    max_sentences = 3
    allow_followup = False
    allow_topic_shift = False
```

---

### 规则组 B：身体疲劳降低输出密度

#### 输入

- `fatigue_level`
- `drowsiness_level`

#### 建议规则

```text
if fatigue_level == "HIGH" or drowsiness_level == "HIGH":
    max_chars *= 0.7
    max_sentences = min(max_sentences, 2)
    allow_followup = False
    humor_ceiling = 0.2
```

说明：

- 困倦并不一定意味着冷淡
- 但应意味着更短、更慢、更少扩展

---

### 规则组 C：抗拒话题直接禁扩展

#### 输入

- `topic_resistance_active`

#### 建议规则

```text
if topic_resistance_active:
    allow_followup = False
    allow_topic_shift = False
    should_be_withdrawn = True
```

必要时可进一步：

- 启用特定话题黑名单
- 拒绝对抗拒主题做深挖

---

### 规则组 D：关系阶段限制亲密上限

#### 输入

- `bond_stage`

#### 建议规则

```text
if bond_stage in {"STRANGER", "ACQUAINTANCE"}:
    intimacy_ceiling = 0.2
elif bond_stage == "FRIEND":
    intimacy_ceiling = 0.45
else:
    intimacy_ceiling = 0.75
```

说明：

这不是让浅关系一定冷，而是防止：

- 突然越界亲密
- 情绪跳段
- prompt 漂移导致的“关系进度瞬移”

---

### 规则组 E：离线等待只提升“在意感”，不直接解除收缩状态

#### 输入

- `offline_hours`
- `loneliness_level`
- `anticipation_topics`

#### 建议规则

```text
if offline_hours >= 12:
    warmth_floor += 0.1
if loneliness_level >= 0.6:
    warmth_floor += 0.1
```

注意：

- 等待感可以提升“在意感”
- 但**不能直接突破**沉默、疲劳、抗拒这些硬约束

否则会出现逻辑冲突：

> 明明很疲惫/不想说，却因为“等了很久”又被迫热情展开

---

## 建议接入位置

第一版不建议大改架构，可采用以下低风险接入点：

### 方案 A：接在 `chat_context_builder.py` 后、`prompt_builder.py` 前

流程变为：

1. `chat_context_builder` 收集状态
2. `ResponsePolicyLayer.decide()` 生成 policy
3. 将 policy 注入 runtime payload
4. `prompt_builder.py` 消费 policy
5. `post_processor` 或输出裁剪层再做硬执行

这是最现实的 MVP 接法。

---

### 方案 B：再加一个最终输出裁剪器

在生成结果后追加一个轻量后处理：

- 按 `max_chars` 裁剪
- 按 `max_sentences` 截断
- 若 `allow_followup=False`，清除尾部追问句
- 若 `allow_topic_shift=False`，清除“顺带说一句”“另外”类扩展

这样即使 prompt 侧未完全稳定，也能用系统层兜底。

---

## 最小实现建议

建议先实现一个纯 Python 模块，例如：

- `host_brain/persona/response_policy.py`

包含：

- `ResponsePolicyInput`
- `ResponsePolicyDecision`
- `ResponsePolicyLayer.decide()`
- 若干规则函数

建议第一版不要依赖 LLM，不要调用外部服务，只做纯规则推导。

---

## 第一版验收标准

第一版不追求优雅，只追求“明显有效”。

### 满足以下条件即可视为通过

1. 当 `willingness_level` 下降时，输出明显缩短
2. 当 `topic_resistance_active=True` 时，不再主动追问/扩展
3. 当 `fatigue_level` 或 `drowsiness_level` 很高时，输出明显更克制
4. 不同 `bond_stage` 下，亲密表达上限可观测不同
5. 输出行为差异具备可复现性，而不是随机感觉

---

## 与 Motive Engine 的未来关系

未来若引入 `MotiveEngine`，Response Policy Layer 不需要废弃，而应继续保留，作为：

- **行为约束层**

二者关系可定义为：

- Motive Engine 决定：现在更想靠近还是退缩
- Response Policy Layer 决定：在当前状态下，允许怎么说、说多少、能不能展开

即：

- `MotiveEngine` = 内在驱动力
- `ResponsePolicyLayer` = 行为边界控制

---

## 最终结论

当前 `Natsume_Ai` 已经拥有相当丰富的状态层：

- 等待感
- 沉默意愿
- 身体感
- 关系阶段
- 自我反思
- 共同历史

但如果这些状态不能直接限制行为，它们就仍然容易停留在“被描述的生命感”。

因此，下一阶段最具性价比的补环不是继续加更多人格模块，而是补上这一层：

> **Response Policy Layer：让系统不是“说自己现在怎样”，而是“按当前状态真的这样行动”。**

这将是系统从“高级拟态”迈向“行为上更接近活着”的关键一步。
