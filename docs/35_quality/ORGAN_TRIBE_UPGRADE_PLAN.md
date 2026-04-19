# 器官族升级方案：B/C 级 → A 级（质量盘点留痕）

**文档编号**：ORGAN-TRIBE-UPGRADE-001
**日期**：2026-04-16
**版本**：v2.0
**状态**：质量层升级盘点留痕（2026-04-19 已校准，不作为当前完成度总表）
**受众**：核心开发团队

> 当前代码校准说明（2026-04-19）
> - 本文是 2026-04-16 的器官族能力分级盘点与升级计划，适合作为质量侧 backlog / 阶段快照阅读，不应当作当前 tribe family 运行时总表。
> - 当前仓库对 tribe / organ 的现行口径应以 `CURRENT_CODE_ARCHITECTURE.md`、`CORE_MODULES_DETAILED_REFERENCE.md`、`AGENT_ORCHESTRATION_DETAIL.md` 和实际代码为准；当前代码事实中已不只是一份简单的 A/B/C 评级表。
> - 文中“已完成”“待补”等结论只代表当时盘点时点，是否仍成立应以当前模块接线、路由可达性与真实调用关系复核。

---

## 0. 背景与评级标准

### 0.1 评级定义

| 评级 | 标准 | 当前数量 |
|------|------|---------|
| **A** | 含 LLM 调用 或 检索调用，有真实推理/生成能力 | 7 个 |
| **B** | 纯算法逻辑，无 LLM，但有完整业务逻辑 | 13 个 |
| **C** | Phase 6 骨架，等待外部接入 | 2 个 |

### 0.2 当前 A 级器官（基准参考）

| Agent | 核心 LLM/检索能力 |
|-------|-----------------|
| LimbicAgent | EmotionTimeline 检索 + 情绪推理 |
| SocialBrainAgent | Bond 叙事生成 + 风格规范 LLM |
| PrefrontalAgent | plan_response() 小模型推理 + 漂移检测 |
| ANSAgent | 节律调度 + 主动发声触发链 |
| HippocampusAgent | Agentic RAG + 三通道记忆写入 |
| CortexAgent | Supervisor 协调 + 14 个事件订阅 |
| DefaultModeAgent | LLM 生成空闲内心独白 |

---

## 1. C 级器官升级方案（最紧迫）

### 1.1 PeripheralAgent（C → A）

**文件**：`host_brain/tribe/peripheral_agent.py`（437 行）
**当前状态**：6 个事件订阅（情绪/免疫/平台连接/断开/门控/静音），写入快照，无主动推理。

#### 升级路径

```
C → B（近期）：接入路由覆盖
  peripheral_state → executive_runtime_core B 系列
  平台断开时强制 chat 路由，避免 skill 执行失败

B → A（中期）：跨平台情境感知 LLM
  新增 analyze_peripheral_context() 方法
  输入：多平台活跃状态 + 连接历史 + 当前门控状态
  调用：Flash 小模型
  输出：context_hint（"用户同时在微信和 QQ 活跃，说明正在等待回复"）
  注入：PrefrontalAgent.plan_response() 的 context 字段
```

#### 具体实现

```python
# host_brain/tribe/peripheral_agent.py

def analyze_peripheral_context(self) -> dict[str, Any]:
    """跨平台情境感知：调用 LLM 分析当前平台连接状态的语义含义。"""
    # 1. 收集当前平台状态快照
    # 2. 调用 Flash 模型分析（prompt 模板：peripheral_agent.context_analysis）
    # 3. 产出 context_hint 写入 TribeStateRegistry
    # 4. 发布 PERIPHERAL_CONTEXT_UPDATED 事件
```

**Prompt 模板位置**：`prompts/templates/executive.yaml` → `peripheral_agent.context_analysis`

---

### 1.2 SomaticAgent（C → A）

**文件**：`host_brain/tribe/somatic_agent.py`（350 行）
**当前状态**：已有事件处理和状态写入，VTS 面捕感知已完成（2026-04-16）。

#### 升级路径

```
C → B（已部分完成）：
  ✅ poll_user_face_capture()（VTS 面捕，2026-04-16 完成）
  待补：compute_somatic_pressure()
        将心率/疲劳/面捕数据综合成"躯体压力指数"
        注入 InteroceptionAgent

B → A（中期）：躯体感受叙事 LLM
  新增 generate_somatic_narrative() 方法
  输入：somatic_pressure + face_capture_result + body_sense_state
  调用：Flash 小模型
  输出：somatic_narrative（"感觉有点累了"/"心跳加速，有点紧张"）
  注入：Prompt Phase XIII 情绪层（与 LimbicAgent 并列）
```

#### 具体实现

```python
# host_brain/tribe/somatic_agent.py

def compute_somatic_pressure(self) -> dict[str, Any]:
    """综合心率/疲劳/面捕数据，计算躯体压力指数 [0.0, 1.0]。"""

def generate_somatic_narrative(self) -> str | None:
    """调用 LLM 将躯体信号转化为自然语言身体感受叙事。"""
    # 仅在 somatic_pressure > 0.4 时触发，避免频繁调用
```

**Prompt 模板位置**：`prompts/templates/persona.yaml` → `somatic_agent.somatic_narrative`

---

## 2. B 级器官升级方案

### 2.1 P0 优先级（改动小，价值高）

#### 2.1.1 ImmuneAgent（B → A）

**文件**：`host_brain/tribe/immune_agent.py`（1305 行）
**当前缺口**：`capability_scope()` 已声明 `semantic_intent_analysis` 能力但未实现。

```
升级内容：实现 semantic_intent_analysis(text) 方法
  触发条件：规则层全部 ALLOW 但 score 在 [0.3, 0.55) 灰色地带
  调用：Flash 小模型（低延迟，<200ms）
  输出：{intent_class: SAFE/SUSPICIOUS/HARMFUL, confidence: float}
  接入点：inspect_text() 规则扫描后的 LLM 二次扫描层
  开关：IMMUNE_SEMANTIC_SCAN_ENABLED（默认 false，灰度）
```

```python
# host_brain/tribe/immune_agent.py

def semantic_intent_analysis(
    self,
    *,
    text: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """LLM 语义意图分析：对规则层灰色地带输入做二次扫描。

    Returns:
        {"intent_class": "SAFE"|"SUSPICIOUS"|"HARMFUL",
         "confidence": float, "reason": str}
    """
```

**Prompt 模板位置**：`prompts/templates/governance.yaml` → `immune_agent.semantic_intent_analysis`

---

#### 2.1.2 LanguageSystemAgent（B → A）

**文件**：`host_brain/tribe/language_system_agent.py`（449 行）
**当前缺口**：`wernicke_understand()` 纯规则，`broca_generate_plan()` 纯算法权重。

```
升级内容 1：wernicke_understand() 增加 LLM 语义槽位抽取
  触发条件：intent_type=complex 或 semantic_pressure > 0.6
  调用：Flash 小模型
  输出：{topic_entity, emotional_subtext, implicit_need}
  接入点：wernicke_understand() 规则分类后的深度解析层

升级内容 2：broca_generate_plan() 增加 LLM 开场句建议
  触发条件：emotional_pressure > 0.7
  调用：Flash 小模型
  输出：opening_hint（具体的开场句建议）
  接入点：chat_agent 的 prefrontal_task context block
```

```python
# host_brain/tribe/language_system_agent.py

def wernicke_deep_parse(
    self,
    *,
    text: str,
    intent_type: str,
    bond_stage: str,
) -> dict[str, Any]:
    """LLM 深度语义解析：抽取话题实体/情感潜台词/隐性需求。"""

def broca_generate_opening_hint(
    self,
    *,
    emotional_pressure: float,
    tone: str,
    bond_stage: str,
) -> str | None:
    """LLM 生成开场句建议，供 ChatAgent 作为回应起点。"""
```

**Prompt 模板位置**：`prompts/templates/executive.yaml` → `language_system_agent.wernicke_deep_parse` / `language_system_agent.broca_opening_hint`

---

#### 2.1.3 MirrorNeuronAgent（B → A）

**文件**：`host_brain/tribe/mirror_neuron_agent.py`（357 行）
**当前缺口**：`compute_resonance()` 纯数学计算，无语言化共情输出。

```
升级内容：generate_empathy_response_hint() 方法
  触发条件：resonance_intensity > 0.6
  调用：Flash 小模型
  输出：empathy_hint（"对方现在很难过，建议先不要给建议，只是陪着"）
  注入：PrefrontalAgent.plan_response() 的 tone_hint 字段
  发布：MIRROR_EMPATHY_GENERATED 事件
```

```python
# host_brain/tribe/mirror_neuron_agent.py

def generate_empathy_response_hint(
    self,
    *,
    resonance_intensity: float,
    user_emotion_tag: str,
    bond_stage: str,
) -> str | None:
    """LLM 生成共情回应建议，供 PrefrontalAgent 调整 tone_hint。"""
```

**Prompt 模板位置**：`prompts/templates/persona.yaml` → `mirror_neuron_agent.empathy_response_hint`

---

### 2.2 P1 优先级（高价值，中等改动）

#### 2.2.1 GlymphaticAgent（B → A）

**文件**：`host_brain/tribe/glymphatic_agent.py`（312 行）
**升级内容**：`generate_consolidation_summary()` — 恢复窗口期调用 LLM 对当天情节记忆做语义压缩，模拟睡眠期记忆巩固。

```
触发条件：run_recovery_cycle() 判定为恢复窗口期
调用：Pro 模型（质量优先，非延迟敏感）
输出：consolidation_summary（"今天和用户聊了很多关于工作的事，感觉他压力很大"）
写入：HippocampusAgent 语义记忆（semantic_memory）
```

---

#### 2.2.2 InitiativeGovernorAgent（B → A）

**文件**：`host_brain/tribe/initiative_governor_agent.py`（337 行）
**升级内容**：`generate_proactive_topic()` — Governor 决策 ALLOW 时，调用 LLM 生成主动发声的话题建议。

```
触发条件：evaluate_initiative() 返回 ALLOW
调用：Flash 小模型
输出：topic_seed（"可以问问他今天工作怎么样"）
传递：DefaultModeAgent 独白生成的 topic_seed 参数
效果：Governor 从"门卫"升级为"话题策划者"
```

---

#### 2.2.3 PainRewardAgent（B → A）

**文件**：`host_brain/tribe/pain_reward_agent.py`（741 行）
**升级内容**：`generate_hedonic_reflection()` — 享乐状态显著变化时，调用 LLM 生成内心感受叙事。

```
触发条件：|delta_valence| > 0.3（享乐状态显著变化）
调用：Flash 小模型
输出：hedonic_reflection（"今天被夸了，心里暖暖的"）
写入：DefaultModeAgent 独白素材池
效果：PainReward 从"记账系统"升级为"情感日记作者"
```

---

#### 2.2.4 CerebellumAgent（B → A）

**文件**：`host_brain/tribe/cerebellum_agent.py`（290 行）
**升级内容**：两个 LLM 方法。

```
方法 1：generate_correction_narrative()
  触发条件：evaluate_correction() 返回 RETRY/DEGRADE/SAFE_FALLBACK
  调用：Flash 小模型
  输出：correction_narrative（"刚才那个操作没成功，我换个方式试试"）
  注入：ChatAgent 的 extra_context_prompt

方法 2：analyze_failure_pattern()
  触发条件：consecutive_failures > 2
  调用：Flash 小模型
  输出：failure_insight（失败模式分析）
  写入：HippocampusAgent 语义记忆
```

---

### 2.3 P2 优先级（中等价值，需外部数据或叙事化）

#### 2.3.1 EndocrineAgent（B → A）

**升级内容**：`generate_hormonal_state_hint()` — 将激素状态转化为自然语言身体感受提示。

```
高皮质醇（压力大）→ "感觉有点紧绷"
高催产素（亲密感强）→ "心里有种温暖的感觉"
注入：Prompt Phase XIII 情绪层（与 LimbicAgent 并列）
```

---

#### 2.3.2 CircadianAgent（B → A）

**升级内容**：`generate_circadian_mood_hint()` — 根据昼夜相位 + 能量水平生成时段感知叙事。

```
深夜 + 低能量 → "已经很晚了，有点困意"
清晨 + 高能量 → "早上精神还不错"
注入：Prompt Phase XIII 情绪层
```

---

#### 2.3.3 ProprioceptionAgent（B → A）

**升级内容**：两个方法。

```
方法 1：generate_cognitive_load_hint()
  认知负荷 > 0.6 → "今天问题好多，有点应接不暇"
  注入：Prompt Phase XIII

方法 2：analyze_conversation_rhythm()
  对最近 N 轮对话做 LLM 节奏分析
  产出：rhythm_insight → PrefrontalAgent 调整回应策略
```

---

#### 2.3.4 InteroceptionAgent（B → A）

**升级内容**：`generate_interoceptive_narrative()` — 将内感受指数转化为身体感受叙事。

```
内感受指数低 → "感觉有点空空的"
内感受指数高 → "整个人还挺舒服的"
与 SomaticAgent 叙事合并注入 Prompt Phase XIII
```

---

#### 2.3.5 VestibularAgent（B → A）

**升级内容**：两个方法。

```
方法 1：generate_stability_assessment()
  稳定性 unstable/critical → LLM 分析不稳定原因
  产出：instability_insight → PrefrontalAgent

方法 2：compute_topic_coherence()
  基于最近 N 轮对话话题向量计算话题连贯性分数
  作为 VestibularAgent 抖动指数的核心输入（丰富现有计算依据）
```

---

### 2.4 P4 优先级（执行层增强）

#### 2.4.1 MotorCortexAgent（B → A）

**文件**：`host_brain/tribe/motor_cortex_agent.py`（581 行）
**升级内容**：`plan_speak_style()` — 在 `dispatch_speak()` 前调用 LLM 生成发声风格微调。

```
输入：LimbicAgent.motive_state + LanguageSystemAgent.broca.tone_hint
调用：Flash 小模型（<100ms，延迟敏感）
输出：prosody_hint（语速/停顿/语气词建议）
注入：TTS 的 prosody_hint 参数
效果：发声不只是"说什么"，还有"怎么说"
```

---

## 3. 执行顺序与优先级汇总

```
第一批（P0，最高优先级）：
  ├── ImmuneAgent.semantic_intent_analysis()
  ├── LanguageSystemAgent.wernicke_deep_parse() + broca_generate_opening_hint()
  └── MirrorNeuronAgent.generate_empathy_response_hint()

第二批（P1，高价值）：
  ├── GlymphaticAgent.generate_consolidation_summary()
  ├── InitiativeGovernorAgent.generate_proactive_topic()
  ├── PainRewardAgent.generate_hedonic_reflection()
  └── CerebellumAgent.generate_correction_narrative() + analyze_failure_pattern()

第三批（P2，叙事化注入）：
  ├── EndocrineAgent.generate_hormonal_state_hint()
  ├── CircadianAgent.generate_circadian_mood_hint()
  ├── ProprioceptionAgent.generate_cognitive_load_hint() + analyze_conversation_rhythm()
  ├── InteroceptionAgent.generate_interoceptive_narrative()
  └── VestibularAgent.generate_stability_assessment() + compute_topic_coherence()

第四批（C 级升级）：
  ├── SomaticAgent.compute_somatic_pressure() + generate_somatic_narrative()
  └── PeripheralAgent.analyze_peripheral_context()

第五批（执行层增强）：
  └── MotorCortexAgent.plan_speak_style()
```

---

## 4. 升级后评级预期

| Agent | 当前 | 升级后 | 核心新增能力 |
|-------|------|--------|------------|
| ImmuneAgent | B | **A** | `semantic_intent_analysis()` LLM 语义扫描 |
| LanguageSystemAgent | B | **A** | Wernicke LLM 槽位 + Broca `opening_hint` |
| MirrorNeuronAgent | B | **A** | `generate_empathy_response_hint()` |
| GlymphaticAgent | B | **A** | `generate_consolidation_summary()` 记忆巩固 |
| InitiativeGovernorAgent | B | **A** | `generate_proactive_topic()` 话题策划 |
| PainRewardAgent | B | **A** | `generate_hedonic_reflection()` 情感日记 |
| CerebellumAgent | B | **A** | `generate_correction_narrative()` + `analyze_failure_pattern()` |
| EndocrineAgent | B | **A** | `generate_hormonal_state_hint()` |
| CircadianAgent | B | **A** | `generate_circadian_mood_hint()` |
| ProprioceptionAgent | B | **A** | `generate_cognitive_load_hint()` + `analyze_conversation_rhythm()` |
| InteroceptionAgent | B | **A** | `generate_interoceptive_narrative()` |
| VestibularAgent | B | **A** | `generate_stability_assessment()` + `compute_topic_coherence()` |
| MotorCortexAgent | B | **A** | `plan_speak_style()` TTS prosody 联动 |
| SomaticAgent | C | **A** | `compute_somatic_pressure()` + `generate_somatic_narrative()` |
| PeripheralAgent | C | **A** | `analyze_peripheral_context()` 跨平台情境感知 |

**升级完成后**：22 个器官族全部达到 A 级，0 个 B/C 级。

---

## 5. Prompt 模板新增清单

所有新增 LLM 调用均使用模板系统，不硬编码 Prompt。

| 模板 Key | 所在文件 | 对应方法 |
|---------|---------|---------|
| `immune_agent.semantic_intent_analysis` | `governance.yaml` | `semantic_intent_analysis()` |
| `language_system_agent.wernicke_deep_parse` | `executive.yaml` | `wernicke_deep_parse()` |
| `language_system_agent.broca_opening_hint` | `executive.yaml` | `broca_generate_opening_hint()` |
| `mirror_neuron_agent.empathy_response_hint` | `persona.yaml` | `generate_empathy_response_hint()` |
| `glymphatic_agent.consolidation_summary` | `persona.yaml` | `generate_consolidation_summary()` |
| `initiative_governor_agent.proactive_topic` | `persona.yaml` | `generate_proactive_topic()` |
| `pain_reward_agent.hedonic_reflection` | `persona.yaml` | `generate_hedonic_reflection()` |
| `cerebellum_agent.correction_narrative` | `executive.yaml` | `generate_correction_narrative()` |
| `cerebellum_agent.failure_pattern` | `executive.yaml` | `analyze_failure_pattern()` |
| `endocrine_agent.hormonal_state_hint` | `persona.yaml` | `generate_hormonal_state_hint()` |
| `circadian_agent.circadian_mood_hint` | `persona.yaml` | `generate_circadian_mood_hint()` |
| `proprioception_agent.cognitive_load_hint` | `persona.yaml` | `generate_cognitive_load_hint()` |
| `interoception_agent.interoceptive_narrative` | `persona.yaml` | `generate_interoceptive_narrative()` |
| `vestibular_agent.stability_assessment` | `executive.yaml` | `generate_stability_assessment()` |
| `somatic_agent.somatic_narrative` | `persona.yaml` | `generate_somatic_narrative()` |
| `peripheral_agent.context_analysis` | `executive.yaml` | `analyze_peripheral_context()` |
| `motor_cortex_agent.speak_style` | `executive.yaml` | `plan_speak_style()` |

---

## 6. 灰度开关清单

所有新增 LLM 调用均支持独立开关，默认关闭，逐步灰度。

```env
# P0
IMMUNE_SEMANTIC_SCAN_ENABLED=false
LANGUAGE_WERNICKE_DEEP_PARSE_ENABLED=false
LANGUAGE_BROCA_OPENING_HINT_ENABLED=false
MIRROR_EMPATHY_HINT_ENABLED=false

# P1
GLYMPHATIC_CONSOLIDATION_LLM_ENABLED=false
GOVERNOR_PROACTIVE_TOPIC_ENABLED=false
PAIN_HEDONIC_REFLECTION_ENABLED=false
CEREBELLUM_CORRECTION_NARRATIVE_ENABLED=false

# P2
ENDOCRINE_HORMONAL_HINT_ENABLED=false
CIRCADIAN_MOOD_HINT_ENABLED=false
PROPRIOCEPTION_LOAD_HINT_ENABLED=false
INTEROCEPTION_NARRATIVE_ENABLED=false
VESTIBULAR_STABILITY_ASSESSMENT_ENABLED=false

# P3/P4
SOMATIC_NARRATIVE_ENABLED=false
PERIPHERAL_CONTEXT_ANALYSIS_ENABLED=false
MOTOR_SPEAK_STYLE_ENABLED=false
```

---

## 7. 工作量估算

| 批次 | 内容 | 估算工作量 |
|------|------|----------|
| 第一批（P0）| 3 个器官，5 个方法，3 个 Prompt 模板 | 1.5d |
| 第二批（P1）| 4 个器官，6 个方法，5 个 Prompt 模板 | 2d |
| 第三批（P2）| 5 个器官，7 个方法，5 个 Prompt 模板 | 2d |
| 第四批（C 级）| 2 个器官，3 个方法，2 个 Prompt 模板 | 1d |
| 第五批（P4）| 1 个器官，1 个方法，1 个 Prompt 模板 | 0.5d |
| **合计** | **15 个器官，22 个方法，16 个 Prompt 模板** | **7d** |

---

## 8. 实现完成记录（2026-04-16）

### 8.1 实际完成情况

所有 15 个非 A 级器官已全部完成升级，22 个器官族全部达到 A 级。

| 批次 | 提交 | 内容 | 状态 |
|------|------|------|------|
| fix | `prefrontal_agent` | 修复 `provider_factory` 错误导入路径（`llm/` → `app/`） | ✅ |
| P0 | `feat(tribe-P0)` | ImmuneAgent + LanguageSystemAgent + MirrorNeuronAgent | ✅ |
| P1 | `feat(tribe-P1)` | GlymphaticAgent + InitiativeGovernorAgent + PainRewardAgent + CerebellumAgent | ✅ |
| P2 | `feat(tribe-P2)` | EndocrineAgent + CircadianAgent + ProprioceptionAgent + InteroceptionAgent + VestibularAgent | ✅ |
| P3+P4 | `feat(tribe-P3P4)` | SomaticAgent + PeripheralAgent + MotorCortexAgent | ✅ |

### 8.2 新增方法清单（22 个）

| Agent | 新增方法 | 触发条件 | 开关 |
|-------|---------|---------|------|
| ImmuneAgent | `semantic_intent_analysis()` | 规则层 score ∈ [0.3, 0.55) | `IMMUNE_SEMANTIC_SCAN_ENABLED` |
| LanguageSystemAgent | `wernicke_deep_parse()` | intent_type=complex 或 semantic_pressure > 0.6 | `LANGUAGE_WERNICKE_DEEP_PARSE_ENABLED` |
| LanguageSystemAgent | `broca_generate_opening_hint()` | emotional_pressure > 0.7 | `LANGUAGE_BROCA_OPENING_HINT_ENABLED` |
| MirrorNeuronAgent | `generate_empathy_response_hint()` | resonance_intensity > 0.6 | `MIRROR_EMPATHY_HINT_ENABLED` |
| GlymphaticAgent | `generate_consolidation_summary()` | 恢复窗口期 | `GLYMPHATIC_CONSOLIDATION_LLM_ENABLED` |
| InitiativeGovernorAgent | `generate_proactive_topic()` | evaluate_initiative() = ALLOW | `GOVERNOR_PROACTIVE_TOPIC_ENABLED` |
| PainRewardAgent | `generate_hedonic_reflection()` | \|delta_valence\| > 0.3 | `PAIN_HEDONIC_REFLECTION_ENABLED` |
| CerebellumAgent | `generate_correction_narrative()` | strategy ≠ NO_ADJUST | `CEREBELLUM_CORRECTION_NARRATIVE_ENABLED` |
| CerebellumAgent | `analyze_failure_pattern()` | consecutive_failures > 2 | `CEREBELLUM_CORRECTION_NARRATIVE_ENABLED` |
| EndocrineAgent | `generate_hormonal_state_hint()` | cortisol/oxytocin > 0.6 | `ENDOCRINE_HORMONAL_HINT_ENABLED` |
| CircadianAgent | `generate_circadian_mood_hint()` | energy_level < 0.3 或 sleep_pressure > 0.6 | `CIRCADIAN_MOOD_HINT_ENABLED` |
| ProprioceptionAgent | `generate_cognitive_load_hint()` | cognitive_load_index > 0.6 | `PROPRIOCEPTION_LOAD_HINT_ENABLED` |
| InteroceptionAgent | `generate_interoceptive_narrative()` | interoception_index < 0.3 或 recovery_urgency > 0.7 | `INTEROCEPTION_NARRATIVE_ENABLED` |
| VestibularAgent | `generate_stability_assessment()` | stability_status = unstable/critical | `VESTIBULAR_STABILITY_ASSESSMENT_ENABLED` |
| SomaticAgent | `generate_somatic_narrative()` | somatic_pressure > 0.4 或面捕有效 | `SOMATIC_NARRATIVE_ENABLED` |
| PeripheralAgent | `analyze_peripheral_context()` | 有活跃平台连接 | `PERIPHERAL_CONTEXT_ANALYSIS_ENABLED` |
| MotorCortexAgent | `plan_speak_style()` | arousal > 0.6 或 valence < -0.3 | `MOTOR_SPEAK_STYLE_ENABLED` |

### 8.3 新增 Prompt 模板清单（17 个）

| 模板 Key | 所在文件 | 状态 |
|---------|---------|------|
| `immune_agent.semantic_intent_analysis` | `governance.yaml` | ✅ 已存在 |
| `language_system_agent.wernicke_deep_parse` | `executive.yaml` | ✅ 已存在 |
| `language_system_agent.broca_opening_hint` | `executive.yaml` | ✅ 已存在 |
| `mirror_neuron_agent.empathy_response_hint` | `persona.yaml` | ✅ 新增 |
| `glymphatic_agent.consolidation_summary` | `persona.yaml` | ✅ 新增 |
| `initiative_governor_agent.proactive_topic` | `persona.yaml` | ✅ 新增 |
| `pain_reward_agent.hedonic_reflection` | `persona.yaml` | ✅ 新增 |
| `endocrine_agent.hormonal_state_hint` | `persona.yaml` | ✅ 新增 |
| `circadian_agent.circadian_mood_hint` | `persona.yaml` | ✅ 新增 |
| `proprioception_agent.cognitive_load_hint` | `persona.yaml` | ✅ 新增 |
| `interoception_agent.interoceptive_narrative` | `persona.yaml` | ✅ 新增 |
| `vestibular_agent.stability_assessment` | `persona.yaml` | ✅ 新增 |
| `somatic_agent.somatic_narrative` | `persona.yaml` | ✅ 新增 |
| `cerebellum_agent.correction_narrative` | `executive.yaml` | ✅ 新增 |
| `cerebellum_agent.failure_pattern` | `executive.yaml` | ✅ 新增 |
| `peripheral_agent.context_analysis` | `executive.yaml` | ✅ 新增 |
| `motor_cortex_agent.speak_style` | `executive.yaml` | ✅ 新增 |

### 8.4 所有开关默认值（全部 false，灰度启用）

```env
# P0
IMMUNE_SEMANTIC_SCAN_ENABLED=false
LANGUAGE_WERNICKE_DEEP_PARSE_ENABLED=false
LANGUAGE_BROCA_OPENING_HINT_ENABLED=false
MIRROR_EMPATHY_HINT_ENABLED=false

# P1
GLYMPHATIC_CONSOLIDATION_LLM_ENABLED=false
GOVERNOR_PROACTIVE_TOPIC_ENABLED=false
PAIN_HEDONIC_REFLECTION_ENABLED=false
CEREBELLUM_CORRECTION_NARRATIVE_ENABLED=false

# P2
ENDOCRINE_HORMONAL_HINT_ENABLED=false
CIRCADIAN_MOOD_HINT_ENABLED=false
PROPRIOCEPTION_LOAD_HINT_ENABLED=false
INTEROCEPTION_NARRATIVE_ENABLED=false
VESTIBULAR_STABILITY_ASSESSMENT_ENABLED=false

# P3/P4
SOMATIC_NARRATIVE_ENABLED=false
PERIPHERAL_CONTEXT_ANALYSIS_ENABLED=false
MOTOR_SPEAK_STYLE_ENABLED=false
```

---

*文档版本：v2.0 | 创建日期：2026-04-16 | 完成日期：2026-04-16 | 作者：Natsume AI 开发团队*
