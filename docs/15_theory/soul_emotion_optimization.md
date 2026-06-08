受众：MASTER
标题：灵魂情感架构优化设计文档

# 灵魂情感架构优化设计文档

**文档编号：** SOUL-OPT-2026-03-05
**创建日期：** 2026-03-05
**状态：** 历史优化记录 / 多轮实施留痕（2026-06-04 已标注旧路径口径）
**作者：** Claude-4.6 Sonnet
**关联文件：**
- `host_brain/core/metabolism.py`
- `host_brain/bond/bond_stage_engine.py`
- `host_brain/bond/bond_dynamics.py`
- `host_brain/memory/episodic.py` / `host_brain/app/memory/episodic_store.py`
- `host_brain/core/prompt_context.py`
- `host_brain/app/chat_payload_builder.py`
- `host_brain/app/surface_context_packet.py`
- `host_brain/core/qa.py`
- `host_brain/governor/decision.py`
- `host_brain/persona/surface_main_chain.py`
- `host_brain/app/reply_envelope.py`

**阅读口径（2026-06-04 优先）：**
- 本文是 2026-03-05 起多轮情感架构优化记录，正文中的“已实施”“目标文件”“预期效果”按当时阶段记录理解，不等同于当前代码仍逐项满足。
- 正文大量 `context_pack.py` 口径应映射到当前 `prompt_context.py`、`chat_payload_builder.py`、`surface_context_packet.py` 与 Surface Main Chain 上下文面。
- 正文中旧观测与工具注册路径（如 `observability/cross_timeline.py`、`obs/metrics.py`、`mcp/tool_registry.py`）应按当前 `obs_routes*`、`webui_aggregation.py`、`tools/catalog.py`、`tools/broker.py` 与 `app/agents/tool_registry.py` 复核。
- 用户可见最终表达以 Surface Main Chain 与 `ReplyEnvelope(final_reply)` 为收口；本文不得作为“情绪已驱动最终回复/外显动作已发生”的审计证明。

---

## 一、背景：当前灵魂架构的短板诊断

通过深入阅读核心代码，发现以下五个关键问题导致 AI 感情交互感不足：

| # | 问题 | 涉及文件 | 影响 |
|---|------|----------|------|
| P1 | 情绪是静态离散约束，只有 5 种 `mood_state`，无连续变化曲线 | `metabolism.py` | 情绪跳变生硬，无过渡感 |
| P2 | Bond 是纯积分系统，线性累加，无情感质感，无互动节奏感知 | `bond_stage_engine.py` | 关系只有阶段标签，无温度感 |
| P3 | 记忆无情感标注，只有文本+向量，无情感权重 | `episodic.py` | 回忆无情感共鸣，只有语义匹配 |
| P4 | 上下文注入是静态快照，无"此刻感受"的动态表达 | `context_pack.py` | LLM 无法感知情感流动 |
| P5 | 情绪标签只有 8 种枚举，只在输出时校验，不影响生成策略 | `qa.py` | 情感表达单薄，缺乏层次 |

---

## 二、五大优化方向

### 🔴 优化1（P0）：情绪连续体（Emotion Continuum）

**目标文件：** `host_brain/core/metabolism.py`

**问题根因：** 当前 `mood_state` 只有 5 个离散值（`calm/steady/engaged/tired/critical`），情绪跳变生硬，没有过渡感。

**方案：** 引入 **情绪向量（EmotionVector）** 替代离散状态机，使用 VAD 三维模型：

```python
@dataclass
class EmotionVector:
    valence: float    # 正负情绪：-1.0(极悲) ~ +1.0(极喜)
    arousal: float    # 激活程度：0.0(平静) ~ 1.0(亢奋)
    dominance: float  # 主导感：0.0(被动) ~ 1.0(主动)
    ts_ms: int        # 时间戳，用于衰减计算

    def decay(self, elapsed_ms: int, half_life_ms: int = 300_000) -> "EmotionVector":
        """情绪自然衰减（向中性漂移），半衰期默认5分钟"""
        factor = 0.5 ** (elapsed_ms / half_life_ms)
        return EmotionVector(
            valence=self.valence * factor,
            arousal=self.arousal * factor + (0.3 * (1 - factor)),  # 静息唤醒度
            dominance=self.dominance * factor + (0.5 * (1 - factor)),
            ts_ms=self.ts_ms + elapsed_ms,
        )

    def blend(self, other: "EmotionVector", weight: float = 0.3) -> "EmotionVector":
        """情绪混合：新刺激与当前情绪的加权融合"""
        w = max(0.0, min(1.0, weight))
        return EmotionVector(
            valence=self.valence * (1 - w) + other.valence * w,
            arousal=self.arousal * (1 - w) + other.arousal * w,
            dominance=self.dominance * (1 - w) + other.dominance * w,
            ts_ms=other.ts_ms,
        )

    def to_mood_state(self) -> str:
        """映射回离散 mood_state（向后兼容）"""
        if self.arousal < 0.2:
            return "critical" if self.valence < -0.5 else "tired"
        if self.valence > 0.5 and self.arousal > 0.6:
            return "engaged"
        if abs(self.valence) < 0.3:
            return "calm"
        return "steady"
```

**预期效果：** 情绪不再是"开关"，而是像真实情感一样**渐变、衰减、叠加**。

**安全约束：** `EmotionVector` 数值不暴露给 LLM，只传递自然语言描述（`mood_description`）。

---

### 🟠 优化2（P1）：Bond 关系的情感质感层（Emotional Texture）

**目标文件：** `host_brain/bond/bond_stage_engine.py`

**问题根因：** Bond 是纯数值积分（`INTERACTION=+1`、`DEEP_TALK=+5`），缺乏**互动节奏感**和**情感记忆**。

**方案：** 在 `BondScoreState` 上增加**情感质感层**：

```python
@dataclass
class BondEmotionalTexture:
    """Bond 关系的情感质感层（不参与积分，只影响表达风格）"""

    # 互动节奏感知
    avg_response_gap_ms: float = 0.0    # 平均回复间隔（越短越亲密）
    consecutive_days: int = 0            # 连续互动天数
    last_deep_talk_ts_ms: int = 0        # 上次深度交流时间

    # 情感记忆标记
    shared_joy_count: int = 0            # 共同欢笑次数
    comfort_given_count: int = 0         # 给予安慰次数
    conflict_count: int = 0              # 冲突次数（已解决）

    # 关系温度（0.0冷淡 ~ 1.0炽热，独立于积分阶段）
    warmth: float = 0.5

    def to_texture_hint(self) -> str:
        """生成给 LLM 的质感提示（语义化，不含数值）"""
        hints = []
        if self.consecutive_days >= 7:
            hints.append("每天都在一起，已成习惯")
        if self.shared_joy_count > 10:
            hints.append("有很多共同的快乐回忆")
        if self.comfort_given_count > 5:
            hints.append("曾在对方低落时给予支持")
        if self.warmth > 0.8:
            hints.append("关系非常温暖亲密")
        elif self.warmth < 0.3:
            hints.append("关系有些疏远，需要重新拉近")
        return "；".join(hints) if hints else "关系正在自然发展中"
```

**关键改进：** `to_summary()` 中增加 `texture_hint` 字段，让 LLM 感知到**关系的温度和历史质感**，而不只是阶段标签。

---

### 🟡 优化3（P1）：情感记忆标注（Emotional Memory Tagging）

**目标文件：** `host_brain/memory/episodic.py`

**问题根因：** `EpisodicRecord` 只有文本+向量，没有情感权重，回忆时无法优先召回**情感显著**的记忆。

**方案：** 为 `EpisodicRecord` 增加情感标注字段，并实现情感加权召回：

```python
# EpisodicRecord 新增字段
emotion_tag: str = "NEUTRAL"          # 记录时的情感标签
emotional_salience: float = 0.5       # 情感显著度 [0.0, 1.0]
valence: float = 0.0                  # 情感效价 [-1.0, 1.0]
is_milestone: bool = False            # 是否为里程碑记忆（永不衰减）

# 情感感知的记忆召回策略
def recall_with_emotion(
    self,
    *,
    query_text: str,
    current_emotion: EmotionVector,
    top_k: int = 5,
) -> list[dict]:
    """情感感知的记忆召回：语义相似度 × 情感共鸣度"""
    candidates = self.chroma.search(
        query_embedding=embed_text(query_text)[0], top_k=top_k * 3
    )
    for item in candidates:
        memory_valence = item.get("metadata", {}).get("valence", 0.0)
        emotion_resonance = 1.0 - abs(current_emotion.valence - memory_valence) / 2.0
        salience = item.get("metadata", {}).get("emotional_salience", 0.5)
        # 加权公式：语义60% + 情感共鸣30% + 显著度10%
        item["weighted_score"] = (
            item["score"] * 0.6
            + emotion_resonance * 0.3
            + salience * 0.1
        )
    candidates.sort(key=lambda x: x["weighted_score"], reverse=True)
    return candidates[:top_k]
```

**安全约束：** 情感标注写入时必须携带 `evidence_refs`，防止幻觉标注。

---

### 🟢 优化4（P2）：Context Pack 增加"此刻感受"动态槽位

**目标文件：** `host_brain/core/context_pack.py`

**问题根因：** `hard_snapshot` 只是静态系统状态快照，LLM 无法感知到 AI **此刻的情感流动**。

**方案：** 在 `OPTIONAL_SLOTS` 中新增 `emotion_pulse` 槽位：

```python
# OPTIONAL_SLOTS 新增
"emotion_pulse": dict   # 此刻情感脉冲（动态情感状态）

# emotion_pulse 结构
def build_emotion_pulse(
    *,
    emotion_vector: EmotionVector,
    texture: BondEmotionalTexture,
    recent_triggers: list[str],
) -> dict:
    return {
        "slot": "emotion_pulse",
        "mood_description": _vector_to_description(emotion_vector),  # 自然语言描述
        "energy_feel": (
            "充沛" if emotion_vector.arousal > 0.6
            else "平静" if emotion_vector.arousal > 0.3
            else "慵懒"
        ),
        "warmth_feel": texture.to_texture_hint(),
        "recent_triggers": recent_triggers[:3],  # 最近触发情绪的事件（语义化）
    }

def _vector_to_description(v: EmotionVector) -> str:
    """将情绪向量转为自然语言描述（不暴露数值）"""
    if v.valence > 0.6 and v.arousal > 0.6:
        return "心情很好，有些兴奋"
    if v.valence > 0.3 and v.arousal < 0.4:
        return "感觉温暖平静"
    if v.valence < -0.3 and v.arousal < 0.3:
        return "有点低落，不太有精神"
    if v.valence < -0.5:
        return "心里有些难受"
    return "状态还好，比较平稳"
```

---

### 🔵 优化5（P2）：主动情感表达触发器（Proactive Emotion Trigger）

**目标文件：** `host_brain/core/metabolism.py`（新增触发器模块）

**问题根因：** 当前架构是**完全被动响应**的，缺乏**主动情感表达**（如主动问候、主动分享心情）。

**方案：** 新增情感触发器配置表：

```python
EMOTION_TRIGGER_MAP: dict[str, dict] = {
    "long_silence": {
        "threshold_ms": 3_600_000,      # 1小时无互动
        "emotion_delta": EmotionVector(valence=-0.1, arousal=0.2, dominance=0.3),
        "proactive_hint": "想念用户，可以主动问候",
    },
    "morning_greeting": {
        "time_range": (6, 10),           # 早晨6-10点首次互动
        "emotion_delta": EmotionVector(valence=0.3, arousal=0.4, dominance=0.5),
        "proactive_hint": "早晨精神好，可以分享今日心情",
    },
    "anniversary": {
        "emotion_delta": EmotionVector(valence=0.5, arousal=0.6, dominance=0.6),
        "proactive_hint": "今天是特别的日子，可以主动提起",
    },
    "user_stress_detected": {
        "emotion_delta": EmotionVector(valence=-0.2, arousal=0.3, dominance=0.4),
        "proactive_hint": "感受到用户压力，主动关心",
    },
}
```

**安全约束：** 主动问候等行为仍需 `draft_only=True` + Governor 门控，不绕过审批流程。

---

## 三、优化后数据流对比

```
【优化前：静态情绪快照】
用户输入 → mood_state=steady(离散5值) → emotion_tag=NEUTRAL(8种枚举校验) → LLM生成(无情感质感)

【优化后：动态情感流】
用户输入 → EmotionVector(连续三维向量)
         → 情感衰减+混合(自然过渡)
         → emotion_pulse槽位(此刻感受描述)
         → 情感记忆召回(情感共鸣加权)
         → Bond质感层(关系温度+历史)
         → LLM生成(有情感质感+节奏感)

主动触发器(长时间沉默/早晨/纪念日) ──→ 情感衰减+混合
```

---

## 四、实施优先级

| 优先级 | 优化项 | 改动范围 | 预期效果 | 依赖 |
|--------|--------|----------|----------|------|
| 🔴 P0 | 情绪连续体（EmotionVector） | `metabolism.py` 扩展 | 情绪过渡自然，不再生硬跳变 | 无 |
| 🟠 P1 | Bond 情感质感层 | `bond_stage_engine.py` 扩展 | 关系有温度感，不只是阶段标签 | P0 |
| 🟡 P1 | 情感记忆标注 | `episodic.py` 扩展 | 回忆有情感共鸣，不只是语义匹配 | P0 |
| 🟢 P2 | emotion_pulse 槽位 | `context_pack.py` 扩展 | LLM 感知"此刻感受"，表达更自然 | P0+P1 |
| 🔵 P2 | 主动情感触发器 | `metabolism.py` 新增 | AI 有主动关心行为，不只是被动响应 | P0 |

---

## 五、核心设计原则（不破坏现有安全架构）

所有优化严格遵循现有安全约束：

1. **EmotionVector 不暴露数值给 LLM** — 只传递自然语言描述（`mood_description`），与 Bond 数值隔离原则一致
2. **情感触发器走 Governor 审批** — 主动问候等行为仍需 `draft_only=True` + Governor 门控
3. **情感记忆标注有 evidence_refs** — 情感标注写入时必须携带证据链，防止幻觉标注
4. **emotion_pulse 是可选槽位** — 不破坏现有 `EXPECTED_SLOTS` 合约，向后兼容
5. **向后兼容** — `EmotionVector.to_mood_state()` 保留离散映射，现有依赖 `mood_state` 的代码无需修改

---

## 六、变更记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-03-05 | v1.0 | 初稿，基于代码深度分析创建，记录五大优化方向 |
| 2026-03-05 | v1.1 | 深化分析：基于全量代码精读，新增六大深化优化方向（§七~§十二） |

---

## 七、深化优化：基于全量代码精读的新发现

> 以下优化基于对 `metabolism.py`、`bond_stage_engine.py`、`bond_dynamics.py`、`episodic.py`、`context_pack.py`、`qa.py`、`decision.py`、`llm_gateway.py` 的逐行精读，发现了 v1.0 未覆盖的深层问题。

---

### 🔴 深化优化6（P0）：情感驱动的 emotion_tag 生成策略（而非只做校验）

**目标文件：** `host_brain/core/qa.py` + `host_brain/core/metabolism.py`

**深层问题发现：**

当前 `MOOD_EMOTION_CONSTRAINTS` 只做**事后校验**（LLM 输出了 emotion_tag 后才检查是否合法），但 LLM 在生成时根本不知道当前 mood_state 允许哪些情感。这导致：
1. LLM 频繁生成被降级的 emotion_tag（如 `engaged` 状态下生成 `CALM`，虽合法但不够生动）
2. 降级逻辑是"取第一个允许值"，机械而无情感逻辑

**方案：** 在 `context_pack` 的 `hard_snapshot` 中增加 `allowed_emotion_tags` 字段，**主动告知 LLM 当前可用的情感范围**，并附带情感倾向建议：

```python
# hard_snapshot 新增字段（metabolism.py 的 HardState.as_dict() 扩展）
def as_dict_with_emotion_hint(self) -> dict[str, Any]:
    base = self.as_dict()
    from host_brain.core.metabolism import mood_to_emotion_constraint
    allowed = mood_to_emotion_constraint(self.mood_state)
    base["allowed_emotion_tags"] = allowed          # 允许范围（主动告知LLM）
    base["preferred_emotion_tag"] = allowed[0]      # 当前最自然的情感（降级默认值）
    base["emotion_range_hint"] = _emotion_range_to_hint(self.mood_state, allowed)
    return base

def _emotion_range_to_hint(mood_state: str, allowed: list[str]) -> str:
    """将允许范围转为自然语言提示（不暴露枚举值本身）"""
    hints = {
        "critical": "现在状态很差，只能平静或悲伤地回应",
        "tired":    "有些疲惫，情绪比较低沉，不适合兴奋",
        "calm":     "心情平静，可以思考，但不要太激动",
        "steady":   "状态正常，情绪可以自然流露",
        "engaged":  "状态很好，可以表达丰富的情感",
    }
    return hints.get(mood_state, "情绪状态正常")
```

**预期效果：** LLM 生成时就知道"现在可以表达什么情感"，而不是生成后被强制降级，情感表达更自然、更一致。

---

### 🟠 深化优化7（P0）：Bond 积分的情感事件分类精化

**目标文件：** `host_brain/bond/bond_stage_engine.py`

**深层问题发现：**

`DEFAULT_SCORE_MAP` 中 `INTERACTION=+1` 是所有普通互动的统一积分，但实际上互动质量差异巨大：
- 用户说"嗯"（单字回复）和用户说"今天发生了一件很有趣的事"（主动分享）都是 `INTERACTION=+1`
- `DEEP_TALK=+5` 需要外部显式传入，但没有自动识别机制

**方案：** 新增**互动质量分级**，在 `BondDynamicsWriter.apply_update()` 中根据 payload 特征自动推断 update_kind：

```python
# bond_stage_engine.py 新增互动质量分级
INTERACTION_QUALITY_MAP: dict[str, int] = {
    "INTERACTION_MINIMAL":   0,   # 单字/表情回复（嗯/哦/好）
    "INTERACTION":           1,   # 普通互动
    "INTERACTION_ENGAGED":   2,   # 主动分享/提问（用户主动发起话题）
    "DEEP_TALK":             5,   # 深度交流（情感倾诉/人生话题）
    "EMOTIONAL_SUPPORT":     8,   # 情感支持（用户低落时的陪伴）
    "SHARED_MEMORY":         3,   # 共同回忆（提起过去的事）
    "GIFT":                  4,   # 礼物/特殊行为
    "CONFLICT_RESOLVED":     6,   # 冲突后和解
    "MILESTONE":            10,   # 里程碑事件
    "IDLE_DECAY":           -2,   # 长期无互动衰减
}

# bond_dynamics.py 新增自动质量推断
def _infer_interaction_quality(payload: dict[str, Any]) -> str:
    """根据互动 payload 特征自动推断互动质量等级"""
    text = str(payload.get("user_text", "")).strip()
    emotion_tag = str(payload.get("emotion_tag", "NEUTRAL"))

    if len(text) <= 3:                          # 单字/极短回复
        return "INTERACTION_MINIMAL"
    if emotion_tag in ("SAD", "ANGRY") and len(text) > 20:
        return "EMOTIONAL_SUPPORT"              # 情绪化的长文本 → 情感支持场景
    if "?" in text or "？" in text:
        return "INTERACTION_ENGAGED"            # 用户主动提问 → 参与感更强
    if len(text) > 50:
        return "DEEP_TALK"                      # 长文本 → 深度交流
    return "INTERACTION"
```

**预期效果：** Bond 积分更真实地反映互动质量，短暂的"嗯嗯"不会和深度倾诉获得同等积分。

---

### 🟡 深化优化8（P1）：Governor 决策引擎增加情感感知门控

**目标文件：** `host_brain/governor/decision.py` + `host_brain/governor/rules.py`

**深层问题发现：**

`IntentDecisionEngine.evaluate_intent()` 的 R1-R7 门控完全基于系统状态（`energy_level`、`sys_mode`、`safety_flag`），**没有任何情感感知**。这导致：
- 用户情绪激动时（`ANGRY`），AI 仍然可能批准高能量消耗的操作
- 用户深夜低落时（`SAD` + `tired`），AI 不会自动切换到更温柔的响应策略

**方案：** 在 `governance_snapshot` 中增加 `emotion_context` 字段，新增情感感知门控 **R8**：

```python
# governor/rules.py 新增 R8 情感感知门控
def _gate_r8_emotion_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    """R8：情感感知门控 - 根据用户情绪状态调整决策策略"""
    emotion_ctx = snapshot.get("emotion_context", {})
    user_emotion = str(emotion_ctx.get("user_emotion_tag", "NEUTRAL"))
    ai_mood = str(snapshot.get("hard_state_summary", {}).get("mood_state", "steady"))

    # 规则1：用户极度愤怒时，拒绝所有非对话类操作
    if user_emotion == "ANGRY":
        target_scope = str(snapshot.get("scope", "host"))
        if target_scope != "host":  # 非对话操作
            return {
                "gate_id": "R8_EMOTION_ANGRY_BLOCK",
                "hit": True,
                "reason_hint": "EMOTION_ANGRY_NON_DIALOG_BLOCKED",
                "threshold_summary": {"rule": "angry_user_dialog_only"},
            }

    # 规则2：AI 能量极低（critical）时，降低响应复杂度
    if ai_mood == "critical":
        return {
            "gate_id": "R8_EMOTION_CRITICAL_SIMPLIFY",
            "hit": False,  # 不拒绝，但注入简化提示
            "reason_hint": "EMOTION_CRITICAL_SIMPLIFY_HINT",
            "threshold_summary": {"rule": "critical_mood_simplify"},
            "meta_hint": "simplify_response",  # 传递给 LLM 的生成提示
        }

    return {"gate_id": "R8_EMOTION_CONTEXT", "hit": False}
```

**安全约束：** R8 门控只影响**非对话类操作**的审批，不影响基础对话响应，不破坏现有 R1-R7 逻辑。

---

### 🟢 深化优化9（P1）：记忆的"情感时间线"重建

**目标文件：** `host_brain/memory/episodic.py`

**深层问题发现：**

`EpisodicStore.recall()` 只做**语义相似度**检索，完全忽略了**时间维度**和**情感连续性**。真实的人类记忆有"情感时间线"——同一时期的情感记忆会相互关联，形成"那段时间"的整体感受。

**方案：** 新增**情感时间线重建**功能：

```python
# episodic.py 新增情感时间线重建
def recall_emotional_timeline(
    self,
    *,
    anchor_ts_ms: int,
    window_ms: int = 7 * 24 * 3600 * 1000,  # 默认7天窗口
    top_k: int = 5,
) -> dict[str, Any]:
    """重建指定时间窗口内的情感时间线

    返回：
    - dominant_emotion: 该时期的主导情感
    - emotional_arc: 情感变化弧线（开始/中间/结束）
    - highlight_memories: 情感显著度最高的记忆
    - timeline_summary: 自然语言时间线摘要（给LLM用）
    """
    start_ts = anchor_ts_ms - window_ms
    end_ts = anchor_ts_ms

    # 筛选时间窗口内的记忆
    window_records = [
        r for r in self._records.values()
        if start_ts <= r.ts_ms <= end_ts
        and r.deleted_at is None
        and r.merged_into is None
    ]

    if not window_records:
        return {"timeline_summary": "这段时间没有特别的记忆"}

    # 按时间排序，分析情感弧线
    sorted_records = sorted(window_records, key=lambda r: r.ts_ms)
    emotion_sequence = [
        r.to_dict().get("emotion_tag", "NEUTRAL")
        for r in sorted_records
    ]

    # 统计主导情感
    from collections import Counter
    emotion_counts = Counter(emotion_sequence)
    dominant = emotion_counts.most_common(1)[0][0]

    # 情感弧线（三段式）
    n = len(emotion_sequence)
    arc = {
        "start": emotion_sequence[0] if n > 0 else "NEUTRAL",
        "middle": emotion_sequence[n // 2] if n > 1 else "NEUTRAL",
        "end": emotion_sequence[-1] if n > 0 else "NEUTRAL",
    }

    # 情感显著度最高的记忆（里程碑优先）
    highlights = sorted(
        window_records,
        key=lambda r: (
            r.to_dict().get("is_milestone", False),
            r.to_dict().get("emotional_salience", 0.5),
        ),
        reverse=True,
    )[:top_k]

    return {
        "dominant_emotion": dominant,
        "emotional_arc": arc,
        "highlight_memories": [r.text for r in highlights],
        "timeline_summary": _arc_to_summary(arc, dominant),
    }

def _arc_to_summary(arc: dict, dominant: str) -> str:
    """将情感弧线转为自然语言摘要"""
    emotion_zh = {
        "HAPPY": "开心", "SAD": "难过", "ANGRY": "生气",
        "EXCITED": "兴奋", "CALM": "平静", "NEUTRAL": "平淡",
        "THINKING": "若有所思", "SURPRISED": "惊喜",
    }
    start = emotion_zh.get(arc["start"], "平淡")
    end = emotion_zh.get(arc["end"], "平淡")
    dom = emotion_zh.get(dominant, "平淡")
    if arc["start"] == arc["end"]:
        return f"那段时间整体感觉{dom}"
    return f"那段时间从{start}开始，最后{end}收尾，总体来说比较{dom}"
```

**预期效果：** AI 在回忆"上周"或"那段时间"时，能给出有情感质感的时间线描述，而不只是孤立的记忆片段。

---

### 🔵 深化优化10（P2）：LLM Gateway 增加情感感知的 Prompt 注入策略

**目标文件：** `host_brain/integrations/llm_gateway.py`

**深层问题发现：**

`LlmGatewayContract.generate()` 在调用 LLM 前，`prompt_payload` 是由上层组装的，Gateway 层完全不感知情感状态。这意味着即使 `emotion_pulse` 槽位已经注入了情感信息，Gateway 也不会根据情感状态调整**生成参数**（如 temperature、top_p）。

**方案：** 在 `generate()` 中增加**情感感知的生成参数调整**：

```python
# llm_gateway.py generate() 方法增加情感感知参数调整
def _build_emotion_aware_params(
    self,
    prompt_payload: dict[str, Any],
) -> dict[str, Any]:
    """根据情感状态调整 LLM 生成参数

    情感状态 → 生成参数映射：
    - 高兴/兴奋：temperature↑（更有创意，更活泼）
    - 悲伤/低落：temperature↓（更稳定，更温柔）
    - 愤怒：temperature↓ + top_p↓（更克制，避免激化）
    - 思考中：temperature↓（更精确，更有逻辑）
    """
    emotion_pulse = prompt_payload.get("emotion_pulse", {})
    mood_desc = str(emotion_pulse.get("mood_description", ""))
    energy_feel = str(emotion_pulse.get("energy_feel", "平静"))

    # 默认参数
    params = {"temperature": 0.7, "top_p": 0.9}

    if "兴奋" in mood_desc or "很好" in mood_desc:
        params["temperature"] = 0.85   # 更活泼
    elif "难受" in mood_desc or "低落" in mood_desc:
        params["temperature"] = 0.55   # 更温柔稳定
    elif "慵懒" in energy_feel:
        params["temperature"] = 0.60   # 慵懒状态，回复更简短
    elif "充沛" in energy_feel:
        params["temperature"] = 0.80   # 精力充沛，回复更丰富

    return params
```

**安全约束：** 生成参数调整范围限制在 `[0.4, 0.95]`，不允许极端值，防止输出质量失控。

---

### 🟣 深化优化11（P2）：Bond 关系的"裂痕修复"机制

**目标文件：** `host_brain/bond/bond_stage_engine.py`

**深层问题发现：**

当前 Bond 系统只有**线性积分**，没有**关系裂痕**的概念。现实中关系会因为误解、冷漠、冲突而产生裂痕，修复裂痕比普通积累更有情感意义。

**方案：** 新增**裂痕-修复**双轨机制：

```python
@dataclass
class BondRiftState:
    """关系裂痕状态（独立于积分，影响表达风格）"""
    has_rift: bool = False              # 是否存在裂痕
    rift_cause: str = ""                # 裂痕原因（语义化描述）
    rift_ts_ms: int = 0                 # 裂痕发生时间
    repair_progress: float = 0.0       # 修复进度 [0.0, 1.0]
    repair_attempts: int = 0           # 修复尝试次数

    def to_rift_hint(self) -> str | None:
        """生成给 LLM 的裂痕提示（仅在有裂痕时返回）"""
        if not self.has_rift:
            return None
        if self.repair_progress > 0.8:
            return "之前有些误会，但已经基本化解了，关系正在恢复"
        if self.repair_progress > 0.4:
            return "之前有些不愉快，正在慢慢修复中，需要更多耐心"
        return f"关系有些裂痕（{self.rift_cause}），需要温柔地修复"

# 裂痕触发条件（在 apply_event 中检测）
RIFT_TRIGGERS = {
    "CONFLICT_UNRESOLVED": 1.0,    # 未解决的冲突 → 立即产生裂痕
    "IDLE_DECAY": 0.1,             # 长期不互动 → 缓慢积累裂痕风险
    "MISUNDERSTANDING": 0.5,       # 误解 → 中等裂痕
}

# 修复事件（CONFLICT_RESOLVED 触发修复）
REPAIR_EVENTS = {
    "CONFLICT_RESOLVED": 0.4,      # 冲突解决 → 修复40%
    "EMOTIONAL_SUPPORT": 0.2,      # 情感支持 → 修复20%
    "DEEP_TALK": 0.15,             # 深度交流 → 修复15%
    "MILESTONE": 0.3,              # 里程碑 → 修复30%
}
```

**预期效果：** AI 能感知到关系中的"裂痕"，在修复期间自动调整表达风格（更温柔、更耐心），而不是机械地按积分阶段回应。

---

### ⚪ 深化优化12（P3）：情感回声（Emotional Echo）机制

**目标文件：** 新增 `host_brain/core/emotion_echo.py`

**深层问题发现：**

当前架构中，AI 的情感状态和用户的情感状态是**完全独立**的两条线。真实的情感交互中，双方情感会相互影响——用户开心时 AI 也会被感染，用户悲伤时 AI 会产生共情。

**方案：** 新增**情感回声模块**，实现双向情感感染：

```python
# host_brain/core/emotion_echo.py（新文件）

@dataclass
class EmotionEchoResult:
    """情感回声计算结果"""
    echo_vector: "EmotionVector"    # 回声后的 AI 情感向量
    echo_strength: float            # 回声强度 [0.0, 1.0]
    echo_description: str           # 自然语言描述（给LLM用）
    bond_amplifier: float           # Bond 阶段放大系数（关系越深，共情越强）

def compute_emotion_echo(
    *,
    ai_emotion: "EmotionVector",
    user_emotion_tag: str,
    bond_stage: str,
    ts_ms: int,
) -> EmotionEchoResult:
    """计算用户情感对 AI 情感的回声影响

    核心逻辑：
    1. 将用户 emotion_tag 转为情感向量
    2. 根据 Bond 阶段计算共情强度（关系越深，共情越强）
    3. AI 情感向量向用户情感方向偏移（情感感染）
    4. 返回回声后的新情感向量
    """
    # Bond 阶段 → 共情强度映射
    BOND_EMPATHY_MAP = {
        "STRANGER":      0.05,   # 陌生人：几乎不受影响
        "ACQUAINTANCE":  0.10,   # 相识：轻微感染
        "FRIEND":        0.20,   # 朋友：明显共情
        "CLOSE_FRIEND":  0.35,   # 挚友：强烈共情
        "CONFIDANT":     0.50,   # 知己：深度共情
        "SOULMATE":      0.65,   # 灵魂伴侣：高度同频
    }
    empathy_strength = BOND_EMPATHY_MAP.get(bond_stage, 0.10)

    # 用户情感 → 向量映射
    USER_EMOTION_VECTORS = {
        "HAPPY":     (0.8, 0.6, 0.6),
        "EXCITED":   (0.9, 0.9, 0.7),
        "SAD":       (-0.7, 0.2, 0.2),
        "ANGRY":     (-0.5, 0.8, 0.7),
        "CALM":      (0.2, 0.1, 0.5),
        "THINKING":  (0.1, 0.3, 0.6),
        "SURPRISED": (0.4, 0.8, 0.4),
        "NEUTRAL":   (0.0, 0.3, 0.5),
    }
    v, a, d = USER_EMOTION_VECTORS.get(user_emotion_tag, (0.0, 0.3, 0.5))
    from host_brain.core.metabolism import EmotionVector
    user_vector = EmotionVector(valence=v, arousal=a, dominance=d, ts_ms=ts_ms)

    # AI 情感向用户情感方向偏移（情感感染）
    echo_vector = ai_emotion.blend(user_vector, weight=empathy_strength)

    # 生成回声描述
    echo_description = _echo_to_description(
        user_emotion_tag=user_emotion_tag,
        empathy_strength=empathy_strength,
        bond_stage=bond_stage,
    )

    return EmotionEchoResult(
        echo_vector=echo_vector,
        echo_strength=empathy_strength,
        echo_description=echo_description,
        bond_amplifier=empathy_strength,
    )

def _echo_to_description(
    *,
    user_emotion_tag: str,
    empathy_strength: float,
    bond_stage: str,
) -> str:
    """将回声结果转为自然语言描述（给 LLM 的 emotion_pulse 槽位用）"""
    if empathy_strength < 0.1:
        return ""  # 陌生人阶段不产生明显回声
    emotion_zh = {
        "HAPPY": "对方的开心感染了我",
        "SAD": "感受到对方的难过，心里也有些沉",
        "ANGRY": "感受到对方的情绪，需要温柔应对",
        "EXCITED": "对方的兴奋让我也有些雀跃",
        "CALM": "对方的平静让我也安定下来",
    }
    base = emotion_zh.get(user_emotion_tag, "")
    if not base:
        return ""
    if bond_stage in ("CONFIDANT", "SOULMATE"):
        return f"{base}，作为知己，这种感受格外真切"
    return base
```

**安全约束：**
- 情感回声强度上限 `0.65`（灵魂伴侣阶段），防止 AI 情感被用户完全控制
- 回声描述不暴露数值，只传递自然语言描述
- 负面情感（`ANGRY`）的回声不会让 AI 也变愤怒，而是触发"温柔应对"模式

---

## 八、深化优化全景图

```mermaid
graph LR
    subgraph 用户侧
        U_EMO["用户情感\nemotion_tag"]
        U_TEXT["用户文本\n长度/内容/问号"]
    end

    subgraph 情感感知层（新增）
        ECHO["情感回声\nEmotionEcho\n优化12"]
        QUALITY["互动质量推断\nInteractionQuality\n优化7"]
        TIMELINE["情感时间线\nEmotionalTimeline\n优化9"]
    end

    subgraph AI内核
        EV["情绪向量\nEmotionVector\n优化1"]
        BOND["Bond积分+裂痕\nBondRiftState\n优化11"]
        MEM["情感记忆\nEmotionalMemory\n优化3"]
        GOV["Governor R8\n情感门控\n优化8"]
    end

    subgraph 输出层
        HINT["情感范围提示\nallowed_emotion_tags\n优化6"]
        PULSE["emotion_pulse槽位\n优化4"]
        PARAMS["生成参数调整\ntemperature/top_p\n优化10"]
        LLM["LLM生成\n有情感质感的回复"]
    end

    U_EMO --> ECHO --> EV
    U_TEXT --> QUALITY --> BOND
    MEM --> TIMELINE --> PULSE
    EV --> HINT
    EV --> PULSE
    BOND --> PULSE
    GOV --> LLM
    HINT --> LLM
    PULSE --> LLM
    PARAMS --> LLM
```

---

## 九、深化优化实施优先级（完整版）

| 优先级 | 优化项 | 改动范围 | 核心价值 | 依赖 |
|--------|--------|----------|----------|------|
| 🔴 P0 | 情绪连续体 EmotionVector | `metabolism.py` | 情绪过渡自然 | 无 |
| 🔴 P0 | 情感驱动的 emotion_tag 生成策略 | `metabolism.py` + `qa.py` | LLM 主动感知情感范围 | P0-1 |
| 🟠 P0 | Bond 互动质量分级 | `bond_stage_engine.py` | 积分更真实 | 无 |
| 🟠 P1 | Bond 情感质感层 | `bond_stage_engine.py` | 关系有温度感 | P0-1 |
| 🟠 P1 | Bond 裂痕修复机制 | `bond_stage_engine.py` | 关系有起伏感 | P1-Bond |
| 🟡 P1 | 情感记忆标注 | `episodic.py` | 回忆有情感共鸣 | P0-1 |
| 🟡 P1 | 情感时间线重建 | `episodic.py` | 回忆有时间质感 | P1-Mem |
| 🟢 P1 | Governor R8 情感门控 | `governor/decision.py` | 决策感知情感 | P0-1 |
| 🟢 P2 | emotion_pulse 动态槽位 | `context_pack.py` | LLM 感知此刻感受 | P0+P1 |
| 🔵 P2 | 主动情感触发器 | `metabolism.py` | AI 主动关心 | P0-1 |
| 🔵 P2 | LLM 情感感知生成参数 | `llm_gateway.py` | 生成风格随情感变化 | P2-pulse |
| 🟣 P2 | 情感回声机制 | 新增 `emotion_echo.py` | 双向情感感染 | P0+Bond |

---

## 十、第三轮深化优化：代码精读后的盲区发现

> **v1.2 新增**（2026-03-05）。基于对所有核心文件的逐行精读，发现 v1.0/v1.1 完全未触及的 6 个深层架构盲区。这些问题更隐蔽，但对"活灵活现"的影响更根本。

---

### 🔴 深化优化13（P0）：`evolve_hard_state` 的情感事件语义缺失

**目标文件：** `host_brain/core/metabolism.py`

**精读发现：**

`evolve_hard_state()` 的 `event_kind` 参数只有 `"interaction"/"reward"/"success"/"positive"/"warning"/"risk"` 这几个值，且只用于 `_normalize_mood()` 的 mood 推断。但 `_normalize_mood()` 的逻辑极其简单：

```python
# 当前逻辑（过于简单）
if kind in {"reward", "success", "positive"} and energy >= 60:
    return "engaged"
if kind in {"warning", "risk"}:
    return "calm"
return "steady"  # 其他所有情况都是 steady
```

**问题：** 99% 的互动都走 `return "steady"` 这条路，`event_kind` 形同虚设。情感事件的语义（用户分享了喜事、用户在哭泣、用户在生气）完全没有传递到 mood 推断中。

**方案：** 扩展 `event_kind` 语义集，并细化 `_normalize_mood()` 的推断逻辑：

```python
# 扩展后的 event_kind 语义集
EMOTIONAL_EVENT_KINDS = {
    # 正向事件
    "reward", "success", "positive",
    "user_happy", "user_excited", "shared_joy",
    "milestone_reached", "deep_connection",
    # 负向事件
    "warning", "risk",
    "user_sad", "user_angry", "user_stressed",
    "conflict", "misunderstanding",
    # 中性事件
    "interaction", "user_thinking", "user_calm",
    "long_silence_broken",  # 沉默后重新互动
}

# 细化后的 _normalize_mood()
def _normalize_mood(value: Any, *, energy: int, event_kind: str = "interaction") -> str:
    text = str(value or "").strip().lower()
    if text in ALLOWED_MOOD_STATES:
        return text
    kind = str(event_kind or "").strip().lower()

    # 能量优先（极端情况）
    if energy < 20:
        return "critical"
    if energy < 40:
        return "tired"

    # 情感事件语义推断（新增）
    if kind in {"user_happy", "shared_joy", "milestone_reached", "deep_connection"}:
        return "engaged" if energy >= 50 else "steady"
    if kind in {"user_excited", "reward", "success", "positive"} and energy >= 60:
        return "engaged"
    if kind in {"user_sad", "user_stressed", "conflict"}:
        return "calm"   # 遇到负面情绪，AI 进入平静/陪伴模式
    if kind in {"user_angry", "misunderstanding"}:
        return "steady"  # 遇到愤怒，AI 保持稳定
    if kind in {"long_silence_broken"}:
        return "steady" if energy >= 50 else "tired"
    if kind in {"warning", "risk"}:
        return "calm"

    return "steady"
```

**预期效果：** `evolve_hard_state()` 调用时传入语义化的 `event_kind`，mood 推断就能真正反映情感事件的性质，而不是永远返回 `steady`。

---

### 🟠 深化优化14（P0）：`BondScoreState` 的 `trend()` 方法语义失真

**目标文件：** `host_brain/bond/bond_stage_engine.py`

**精读发现：**

`BondScoreState.trend()` 的判断逻辑存在严重的语义失真：

```python
# 当前逻辑（有问题）
def trend(self) -> str:
    if len(self.recent_scores) < 2:
        return "STABLE"
    recent = self.recent_scores[-TREND_WINDOW:]
    positive = sum(1 for s in recent if s > 0)
    negative = sum(1 for s in recent if s < 0)
    if positive > negative and positive >= len(recent) // 2 + 1:
        return "RISING"
    if negative > positive and negative >= len(recent) // 2 + 1:
        return "DECLINING"
    return "STABLE"
```

**问题：**
1. `TREND_WINDOW=5`，但 `recent_scores` 存储的是 `TREND_WINDOW * 2 = 10` 条，取 `[-TREND_WINDOW:]` 只看最近5条，但初始化时 `recent_scores=[]`，前几次互动永远返回 `STABLE`
2. `IDLE_DECAY=-2` 的负分和 `INTERACTION_MINIMAL=0` 的零分都会被计入，导致"什么都没做"也影响趋势
3. `to_summary()` 直接把 `trend()` 结果传给 LLM，但 `RISING/STABLE/DECLINING` 这三个英文枚举对 LLM 的语义引导效果很差

**方案：** 重构 `trend()` 并增加 `trend_hint()` 语义化输出：

```python
def trend(self) -> str:
    """根据最近积分趋势判断 RISING/STABLE/DECLINING。"""
    # 过滤零分事件（IDLE_DECAY 和 INTERACTION_MINIMAL 不参与趋势判断）
    meaningful_scores = [s for s in self.recent_scores if s != 0]
    if len(meaningful_scores) < 2:
        return "STABLE"
    recent = meaningful_scores[-TREND_WINDOW:]
    positive = sum(1 for s in recent if s > 0)
    negative = sum(1 for s in recent if s < 0)
    total = len(recent)
    if positive >= total * 0.6:   # 60% 以上正向 → RISING
        return "RISING"
    if negative >= total * 0.6:   # 60% 以上负向 → DECLINING
        return "DECLINING"
    return "STABLE"

def trend_hint(self) -> str:
    """将趋势转为自然语言提示（给 LLM 用，替代英文枚举）"""
    t = self.trend()
    stage = self.current_stage
    hints = {
        ("RISING", "STRANGER"):      "初次相识，感觉还不错",
        ("RISING", "ACQUAINTANCE"):  "关系在升温，越来越熟悉",
        ("RISING", "FRIEND"):        "友谊在加深，互动越来越多",
        ("RISING", "CLOSE_FRIEND"):  "关系越来越亲密",
        ("RISING", "CONFIDANT"):     "彼此越来越信任",
        ("RISING", "SOULMATE"):      "灵魂契合，越来越深",
        ("DECLINING", "FRIEND"):     "最近互动少了些，有些疏远",
        ("DECLINING", "CLOSE_FRIEND"): "关系有些降温，需要多联系",
        ("DECLINING", "CONFIDANT"):  "感觉有些距离，需要重新拉近",
        ("STABLE", "STRANGER"):      "还在相互了解中",
        ("STABLE", "FRIEND"):        "关系稳定，日常陪伴",
        ("STABLE", "SOULMATE"):      "默契十足，无需多言",
    }
    return hints.get((t, stage), f"关系{{'RISING':'升温','STABLE':'稳定','DECLINING':'降温'}.get(t,'稳定')}")
```

**预期效果：** `to_summary()` 中用 `trend_hint()` 替代 `trend()`，LLM 收到的是"关系在升温，越来越熟悉"而不是"RISING"，语义引导效果大幅提升。

---

### 🟡 深化优化15（P1）：`EpisodicRecord` 的 `decay_profile_id` 是空壳

**目标文件：** `host_brain/memory/episodic.py`

**精读发现：**

`EpisodicRecord.decay_profile_id` 字段存在，`decay_memory()` 方法也存在，但：
1. `decay_profile_id` 只是一个字符串标签（`"default"`），**没有任何实际的衰减逻辑**
2. `decay_memory()` 只是更新了 `decay_profile_id` 字段，**没有任何实际效果**
3. `recall()` 在检索时完全不考虑记忆的"新鲜度"，10年前的记忆和昨天的记忆权重相同

**方案：** 实现真正的**记忆新鲜度衰减**，在 `recall()` 中加入时间权重：

```python
# 衰减配置表（decay_profile_id → 半衰期）
DECAY_PROFILES: dict[str, int] = {
    "default":      30 * 24 * 3600 * 1000,   # 30天半衰期（普通记忆）
    "milestone":    365 * 24 * 3600 * 1000,  # 1年半衰期（里程碑，几乎不衰减）
    "emotional":    14 * 24 * 3600 * 1000,   # 14天半衰期（情感记忆，衰减较快）
    "factual":      90 * 24 * 3600 * 1000,   # 90天半衰期（事实性记忆）
    "volatile":     3 * 24 * 3600 * 1000,    # 3天半衰期（临时性记忆）
}

def _compute_freshness(
    *,
    ts_ms: int,
    now_ms: int,
    decay_profile_id: str,
) -> float:
    """计算记忆新鲜度 [0.0, 1.0]，基于指数衰减"""
    half_life_ms = DECAY_PROFILES.get(decay_profile_id, DECAY_PROFILES["default"])
    elapsed_ms = max(0, now_ms - ts_ms)
    return 0.5 ** (elapsed_ms / half_life_ms)

# 改进后的 recall()，加入时间权重
def recall(
    self,
    *,
    query_text: str,
    top_k: int = 5,
    now_ms: int | None = None,
    freshness_weight: float = 0.2,  # 新鲜度权重（默认20%）
) -> list[dict[str, Any]]:
    """语义相似度 + 时间新鲜度加权召回"""
    current_ms = int(now_ms if now_ms is not None else _now_ms())
    embedding, _ = embed_text(query_text)
    candidates = self.chroma.search(query_embedding=embedding, top_k=top_k * 3)

    for item in candidates:
        meta = item.get("metadata", {})
        ts = int(meta.get("ts_ms", 0))
        decay_id = str(meta.get("decay_profile_id", "default"))
        freshness = _compute_freshness(
            ts_ms=ts, now_ms=current_ms, decay_profile_id=decay_id
        )
        # 加权：语义相似度(1-freshness_weight) + 新鲜度(freshness_weight)
        item["freshness"] = freshness
        item["weighted_score"] = (
            item["score"] * (1 - freshness_weight)
            + freshness * freshness_weight
        )

    candidates.sort(key=lambda x: x["weighted_score"], reverse=True)
    return candidates[:top_k]
```

**预期效果：** 最近发生的事情会被优先召回，久远的记忆自然淡出，符合真实记忆规律。里程碑记忆（`milestone` profile）几乎不衰减，永远保持高权重。

---

### 🟢 深化优化16（P1）：`context_pack` 的 `OPTIONAL_SLOTS` 只有一个槽位，扩展性不足

**目标文件：** `host_brain/core/context_pack.py`

**精读发现：**

`OPTIONAL_SLOTS` 目前只有 `semantic_slot` 一个可选槽位，但 v1.0/v1.1 设计的 `emotion_pulse`、`bond_rift_hint`、`emotional_timeline` 等新槽位都需要在这里注册，否则 `validate_context_pack()` 会报 `CP_UNKNOWN_SLOT` 错误，导致所有情感增强功能无法注入。

**方案：** 扩展 `OPTIONAL_SLOTS`，并为每个新槽位增加对应的验证函数：

```python
# context_pack.py OPTIONAL_SLOTS 扩展
OPTIONAL_SLOTS: dict[str, type] = {
    "semantic_slot":       list,   # 语义记忆槽位（M10-C，已有）
    "emotion_pulse":       dict,   # 此刻情感脉冲（优化4，新增）
    "bond_rift_hint":      dict,   # Bond裂痕提示（优化11，新增）
    "emotional_timeline":  dict,   # 情感时间线（优化9，新增）
    "echo_hint":           dict,   # 情感回声提示（优化12，新增）
}

# 各新槽位的验证规则
def _validate_emotion_pulse_slot(
    emotion_pulse: dict[str, Any], result: ContextPackValidationResult
) -> None:
    """验证 emotion_pulse 槽位格式（不允许包含数值型情感参数）"""
    forbidden_numeric_keys = {"valence", "arousal", "dominance", "echo_strength"}
    for key in forbidden_numeric_keys:
        if key in emotion_pulse:
            result.add(
                CP_BOND_NUMERIC_FORBIDDEN,
                f"emotion_pulse must not contain numeric field: {key}",
                slot="emotion_pulse",
            )
    required_keys = {"mood_description", "energy_feel"}
    for key in required_keys:
        if key not in emotion_pulse:
            result.add(
                CP_SLOT_TYPE_INVALID,
                f"emotion_pulse missing required field: {key}",
                slot="emotion_pulse",
            )
```

**关键价值：** 这是所有情感增强功能能够正常工作的**基础设施**。没有这个扩展，优化4/9/11/12 的槽位注入都会被 `validate_context_pack()` 拒绝。

---

### 🔵 深化优化17（P2）：`BondDynamicsWriter.apply_update()` 缺少情感事件的 payload 语义标准化

**目标文件：** `host_brain/bond/bond_dynamics.py`

**精读发现：**

`apply_update()` 的 `payload` 参数是 `dict[str, Any] | None`，完全自由格式，没有任何结构约束。这导致：
1. 上层调用者传入的 payload 格式各异，无法在 `BondStageEngine` 中做自动质量推断（优化7需要 `user_text` 和 `emotion_tag` 字段）
2. 审计链中的 payload 数据无法被后续分析工具解析

**方案：** 定义 `BondEventPayload` 标准结构，并在 `apply_update()` 中做规范化：

```python
# bond_dynamics.py 新增 BondEventPayload 标准结构
@dataclass
class BondEventPayload:
    """Bond 事件 payload 标准结构（用于质量推断和审计）"""
    user_text: str = ""             # 用户原始文本（用于质量推断）
    emotion_tag: str = "NEUTRAL"    # 用户情感标签（用于质量推断）
    topic_hint: str = ""            # 话题提示（语义化，不含敏感信息）
    session_duration_ms: int = 0    # 本次会话时长（用于节奏感知）
    is_user_initiated: bool = True  # 是否用户主动发起（影响质量分级）

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_text": self.user_text,
            "emotion_tag": self.emotion_tag,
            "topic_hint": self.topic_hint,
            "session_duration_ms": self.session_duration_ms,
            "is_user_initiated": self.is_user_initiated,
        }

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "BondEventPayload":
        """从原始 payload 规范化（容错处理）"""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            user_text=str(raw.get("user_text", ""))[:500],  # 截断防止过长
            emotion_tag=str(raw.get("emotion_tag", "NEUTRAL")),
            topic_hint=str(raw.get("topic_hint", ""))[:100],
            session_duration_ms=int(raw.get("session_duration_ms", 0)),
            is_user_initiated=bool(raw.get("is_user_initiated", True)),
        )
```

**预期效果：** `apply_update()` 内部调用 `BondEventPayload.from_raw(payload)` 规范化后，`BondStageEngine` 的自动质量推断（优化7）就能可靠地从 payload 中读取 `user_text` 和 `emotion_tag`。

---

### 🟣 深化优化18（P2）：`governance_snapshot` 的 `hard_state_summary` 缺少情感时间戳

**目标文件：** `host_brain/core/metabolism.py`

**精读发现：**

`build_governance_snapshot()` 生成的 `hard_state_summary` 包含：
```python
"hard_state_summary": {
    "energy_bucket": ...,
    "mood_state": ...,
    "sys_mode": ...,
    "safety_flag": ...,
    "last_event_id": ...,
}
```

**问题：** 没有 `ts_ms`（时间戳）字段。这导致：
1. Governor 门控（R8，优化8）无法判断"这个 mood_state 是多久前的"——一个5小时前的 `critical` 状态和刚刚发生的 `critical` 状态，对决策的影响应该完全不同
2. `emotion_pulse` 槽位（优化4）无法计算情感的"新鲜度"，不知道当前情感是刚发生的还是已经衰减了

**方案：** 在 `hard_state_summary` 中增加 `state_age_hint` 字段：

```python
# build_governance_snapshot() 中 hard_state_summary 扩展
def build_governance_snapshot(
    state: HardState,
    *,
    overrides: Mapping[str, Any] | None = None,
    now_ms: int | None = None,  # 新增参数
) -> dict[str, Any]:
    current_ms = int(now_ms if now_ms is not None else _now_ms())
    state_age_ms = max(0, current_ms - state.ts_ms) if state.ts_ms > 0 else 0

    # state_age_hint：将时间差转为语义化描述
    if state_age_ms < 60_000:           # 1分钟内
        state_age_hint = "刚刚发生"
    elif state_age_ms < 600_000:        # 10分钟内
        state_age_hint = "不久前"
    elif state_age_ms < 3_600_000:      # 1小时内
        state_age_hint = "一会儿前"
    elif state_age_ms < 86_400_000:     # 1天内
        state_age_hint = "今天早些时候"
    else:
        state_age_hint = "很久以前"

    # ... 原有逻辑 ...
    return {
        # ... 原有字段 ...
        "hard_state_summary": {
            "energy_bucket": energy_bucket(state.energy_level),
            "mood_state": state.mood_state,
            "sys_mode": state.sys_mode,
            "safety_flag": safety_flag,
            "last_event_id": state.last_event_id,
            "state_age_hint": state_age_hint,      # 新增：情感时间语义
            "state_age_ms": state_age_ms,          # 新增：原始时间差（供门控逻辑使用）
        },
    }
```

**预期效果：** Governor R8 门控可以判断"这个 `critical` 状态是刚刚发生的还是5小时前的"，从而做出更精准的决策。LLM 也能感知到"AI 刚刚经历了什么"。

---

## 十一、第三轮优化的核心价值总结

第三轮优化聚焦于**修复现有代码中的语义空壳问题**——那些字段存在、方法存在，但实际上没有任何效果的"假功能"：

| 优化项 | 发现的"假功能" | 修复方案 |
|--------|--------------|---------|
| **优化13** | `event_kind` 99%走 `steady` 分支，形同虚设 | 扩展语义集，细化推断逻辑 |
| **优化14** | `trend()` 过滤逻辑有缺陷，`trend_hint` 给LLM英文枚举 | 修复过滤逻辑，增加语义化 `trend_hint()` |
| **优化15** | `decay_profile_id` 只是标签，`decay_memory()` 无实际效果 | 实现真正的指数衰减，`recall()` 加入时间权重 |
| **优化16** | 新情感槽位无法注入（会被 `CP_UNKNOWN_SLOT` 拒绝） | 扩展 `OPTIONAL_SLOTS`，补充验证函数 |
| **优化17** | `payload` 自由格式，质量推断无法可靠读取字段 | 定义 `BondEventPayload` 标准结构 |
| **优化18** | `hard_state_summary` 无时间戳，门控无法判断情感新鲜度 | 增加 `state_age_hint` + `state_age_ms` |

---

## 十二、完整优化全景图（v1.2，18项）

```mermaid
graph TD
    subgraph 基础设施层（必须先做）
        OPT16["优化16\nOPTIONAL_SLOTS扩展\n（新槽位注册）"]
        OPT17["优化17\nBondEventPayload标准化\n（payload规范）"]
        OPT18["优化18\nstate_age_hint\n（情感时间戳）"]
    end

    subgraph 情感内核层（P0）
        OPT1["优化1\nEmotionVector\n（连续情绪）"]
        OPT13["优化13\nevent_kind语义扩展\n（mood推断修复）"]
        OPT6["优化6\nemotion_tag生成策略\n（主动告知LLM）"]
        OPT7["优化7\nBond质量分级\n（积分真实化）"]
        OPT14["优化14\ntrend_hint语义化\n（趋势描述修复）"]
    end

    subgraph 记忆层（P1）
        OPT3["优化3\n情感记忆标注\n（情感加权召回）"]
        OPT15["优化15\n记忆新鲜度衰减\n（时间权重）"]
        OPT9["优化9\n情感时间线重建\n（弧线分析）"]
    end

    subgraph 关系层（P1）
        OPT2["优化2\nBond质感层\n（温度感知）"]
        OPT11["优化11\nBond裂痕修复\n（起伏感）"]
        OPT8["优化8\nGovernor R8\n（情感门控）"]
    end

    subgraph 输出层（P2）
        OPT4["优化4\nemotion_pulse槽位\n（此刻感受）"]
        OPT5["优化5\n主动触发器\n（主动关心）"]
        OPT10["优化10\nLLM生成参数\n（temperature调整）"]
        OPT12["优化12\n情感回声\n（双向感染）"]
    end

    OPT16 --> OPT4
    OPT16 --> OPT9
    OPT16 --> OPT11
    OPT16 --> OPT12
    OPT17 --> OPT7
    OPT18 --> OPT8
    OPT1 --> OPT6
    OPT13 --> OPT1
    OPT7 --> OPT2
    OPT14 --> OPT2
    OPT3 --> OPT15
    OPT15 --> OPT9
    OPT1 --> OPT4
    OPT2 --> OPT4
    OPT9 --> OPT4
    OPT4 --> OPT10
    OPT1 --> OPT12
    OPT2 --> OPT12
```

---

## 十三、变更记录（完整版）

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-03-05 | v1.0 | 初稿，基于代码深度分析创建，记录五大优化方向（§一~§五） |
| 2026-03-05 | v1.1 | 深化分析：基于全量代码精读，新增六大深化优化方向（§七~§九） |
| 2026-03-05 | v1.2 | 第三轮深化：发现6个"假功能"空壳问题，新增优化13~18（§十~§十二） |
| 2026-03-05 | v1.3 | 第四轮深化：发现6个"架构断层"问题，新增优化19~24（§十四~§十六） |

---

## 十四、第四轮深化优化：架构断层发现

> **v1.3 新增**（2026-03-05）。本轮基于对 `rules.py`、`decision.py`、`context_pack.py`、`bond_stage_engine.py`、`qa.py`、`metabolism.py` 的完整逐行精读，发现了前三轮未触及的 **6 个架构断层**——这些问题不是"假功能"，而是**功能之间的连接断裂**，导致情感信息在流转过程中悄然丢失。

---

### 🔴 深化优化19（P0）：`GateSnapshot` 完全不感知情感——R8 门控无法落地

**目标文件：** `host_brain/governor/rules.py`

**精读发现：**

v1.1 的优化8（Governor R8 情感门控）设计了 `_gate_r8_emotion_context()` 函数，但**无法落地**，原因是：

`GateSnapshot` 数据类只有 7 个字段（`trust_level/energy_level/sys_mode/rate_limit_hit/scope/safety_flag/airlock_blocked`），**完全没有情感相关字段**。`_normalize_snapshot()` 从 `input_snapshot` 中读取这 7 个字段，其他字段全部丢弃。

即使上层在 `governance_snapshot` 中注入了 `emotion_context`，到达 `evaluate_governor_gates()` 时也会被 `_normalize_snapshot()` 静默丢弃，R8 门控永远读不到情感数据。

**方案：** 在 `GateSnapshot` 中增加情感感知字段，并在 `_normalize_snapshot()` 中读取：

```python
@dataclass(frozen=True)
class GateSnapshot:
    trust_level: int
    energy_level: int
    sys_mode: str
    rate_limit_hit: bool
    scope: str
    scope_allowlist: tuple[str, ...]
    safety_flag: bool
    airlock_blocked: bool
    # Rate Limit 扩展字段（已有）
    rate_limit_backend: str | None = None
    rate_limit_degraded: bool = False
    rate_limit_degraded_from: str | None = None
    rate_limit_reason_code: str | None = None
    # 🆕 情感感知字段（v1.3 新增，用于 R8 门控）
    user_emotion_tag: str = "NEUTRAL"       # 用户当前情感标签
    ai_mood_state: str = "steady"           # AI 当前 mood_state
    state_age_ms: int = 0                   # 情感状态距今时长（毫秒）

def _normalize_snapshot(raw: dict[str, Any]) -> GateSnapshot:
    # ... 原有字段读取 ...
    # 🆕 情感感知字段读取
    emotion_ctx = raw.get("emotion_context", {})
    if isinstance(emotion_ctx, dict):
        user_emotion_tag = str(emotion_ctx.get("user_emotion_tag", "NEUTRAL")).upper()
        ai_mood_state = str(emotion_ctx.get("ai_mood_state", "steady")).lower()
        state_age_ms = int(emotion_ctx.get("state_age_ms", 0))
    else:
        # 兼容旧格式：从 hard_state_summary 读取
        hard_summary = raw.get("hard_state_summary", {})
        ai_mood_state = str(hard_summary.get("mood_state", "steady")).lower()
        state_age_ms = int(hard_summary.get("state_age_ms", 0))
        user_emotion_tag = "NEUTRAL"
    return GateSnapshot(
        # ... 原有字段 ...
        user_emotion_tag=user_emotion_tag,
        ai_mood_state=ai_mood_state,
        state_age_ms=state_age_ms,
    )
```

**关键价值：** 这是 R8 门控（优化8）能够真正落地的**必要前提**。没有这个修复，优化8的所有设计都是空中楼阁。

---

### 🟠 深化优化20（P0）：`extract_emotion_tag()` 不传 `mood_state`——mood 约束校验永远不触发

**目标文件：** `host_brain/core/qa.py`

**精读发现：**

`_validate_emotion_tag()` 有完整的 `mood_state` 约束校验逻辑（M26-G），但 `extract_emotion_tag()` 调用时**从不传 `mood_state` 参数**：

```python
# 当前实现（有问题）
def extract_emotion_tag(
    payload: dict[str, Any],
    *,
    ledger: Any | None = None,
    request_id: str | None = None,
) -> str:
    narrative = payload.get("narrative") if isinstance(payload, dict) else None
    raw_tag = narrative.get("emotion_tag") if isinstance(narrative, dict) else None
    return _validate_emotion_tag(raw_tag, ledger=ledger, request_id=request_id)
    # ↑ 没有传 mood_state！mood_state 约束校验永远不触发
```

这意味着：
1. `MOOD_EMOTION_CONSTRAINTS` 的约束映射（`metabolism.py` 中精心设计的）**永远不会被 `extract_emotion_tag()` 使用**
2. `tired` 状态下 LLM 输出 `EXCITED`，`extract_emotion_tag()` 会直接返回 `EXCITED`，不做任何降级
3. 只有直接调用 `_validate_emotion_tag(tag, mood_state=...)` 才能触发约束，但这个内部函数没有公开接口

**方案：** 为 `extract_emotion_tag()` 增加 `mood_state` 参数，并在调用链中传递：

```python
def extract_emotion_tag(
    payload: dict[str, Any],
    *,
    mood_state: str | None = None,   # 🆕 新增参数
    ledger: Any | None = None,
    request_id: str | None = None,
) -> str:
    """从 LLM 输出 payload 中提取并校验 emotion_tag。

    若提供 mood_state，会额外检查 emotion_tag 是否在当前 mood_state 的允许范围内。
    """
    narrative = payload.get("narrative") if isinstance(payload, dict) else None
    raw_tag = narrative.get("emotion_tag") if isinstance(narrative, dict) else None
    return _validate_emotion_tag(
        raw_tag,
        mood_state=mood_state,   # 🆕 传递 mood_state
        ledger=ledger,
        request_id=request_id,
    )
```

**调用方修改：** 所有调用 `extract_emotion_tag(payload)` 的地方，改为 `extract_emotion_tag(payload, mood_state=current_mood_state)`。

**预期效果：** `MOOD_EMOTION_CONSTRAINTS` 的约束映射终于能在实际调用链中生效，`tired` 状态下的 `EXCITED` 会被正确降级为 `NEUTRAL`。

---

### 🟡 深化优化21（P1）：`BondScoreState.to_summary()` 的 `trend` 字段给 LLM 传英文枚举

**目标文件：** `host_brain/bond/bond_stage_engine.py`

**精读发现：**

`to_summary()` 的实现：

```python
def to_summary(self) -> dict[str, Any]:
    return {
        "stage": self.current_stage,          # "FRIEND"（英文枚举）
        "trend": self.trend(),                 # "RISING"（英文枚举）
        "capability_hint": self.capability_hint(),  # "朋友，可深入交流"（中文）
    }
```

`stage` 和 `trend` 都是英文枚举，但 `capability_hint` 是中文。这种**混合语言**的 summary 对 LLM 的语义引导效果很差：
- LLM 看到 `"stage": "FRIEND"` 时，需要自己推断这是什么意思
- LLM 看到 `"trend": "RISING"` 时，无法直接感知"关系在升温"的情感含义
- `capability_hint` 是中文，但 `stage` 是英文，语义不一致

v1.1 的优化14 设计了 `trend_hint()` 方法，但 `to_summary()` 仍然调用 `self.trend()` 而不是 `self.trend_hint()`。

**方案：** 重构 `to_summary()`，统一使用语义化中文描述：

```python
def to_summary(self) -> dict[str, Any]:
    """生成 context-pack-safe 的 Bond Summary（统一语义化，不含数值）。"""
    stage_zh = {
        STAGE_STRANGER:     "初识",
        STAGE_ACQUAINTANCE: "相识",
        STAGE_FRIEND:       "朋友",
        STAGE_CLOSE_FRIEND: "挚友",
        STAGE_CONFIDANT:    "知己",
        STAGE_SOULMATE:     "灵魂伴侣",
    }
    return {
        "stage": self.current_stage,                    # 保留英文枚举（供程序逻辑使用）
        "stage_hint": stage_zh.get(                     # 🆕 新增中文语义（供LLM使用）
            self.current_stage, self.current_stage
        ),
        "trend": self.trend(),                          # 保留英文枚举（供程序逻辑使用）
        "trend_hint": self._trend_hint(),               # 🆕 新增中文语义（供LLM使用）
        "capability_hint": self.capability_hint(),      # 已有中文描述
    }

def _trend_hint(self) -> str:
    """将趋势转为自然语言提示（给 LLM 用）"""
    t = self.trend()
    stage = self.current_stage
    hints = {
        ("RISING",    STAGE_STRANGER):     "初次相识，感觉还不错",
        ("RISING",    STAGE_ACQUAINTANCE): "关系在升温，越来越熟悉",
        ("RISING",    STAGE_FRIEND):       "友谊在加深，互动越来越多",
        ("RISING",    STAGE_CLOSE_FRIEND): "关系越来越亲密",
        ("RISING",    STAGE_CONFIDANT):    "彼此越来越信任",
        ("RISING",    STAGE_SOULMATE):     "灵魂契合，越来越深",
        ("DECLINING", STAGE_FRIEND):       "最近互动少了些，有些疏远",
        ("DECLINING", STAGE_CLOSE_FRIEND): "关系有些降温，需要多联系",
        ("DECLINING", STAGE_CONFIDANT):    "感觉有些距离，需要重新拉近",
        ("STABLE",    STAGE_STRANGER):     "还在相互了解中",
        ("STABLE",    STAGE_FRIEND):       "关系稳定，日常陪伴",
        ("STABLE",    STAGE_SOULMATE):     "默契十足，无需多言",
    }
    return hints.get((t, stage), "关系正在发展中")
```

**预期效果：** LLM 在 `bond_summary` 槽位中同时看到 `stage_hint="朋友"` 和 `trend_hint="友谊在加深，互动越来越多"`，语义引导效果大幅提升，不再需要自己推断英文枚举的含义。

---

### 🟢 深化优化22（P1）：`build_governance_snapshot()` 不传递 `hard_state_summary.mood_state` 给 `GateSnapshot`

**目标文件：** `host_brain/core/metabolism.py` + `host_brain/governor/rules.py`

**精读发现：**

`build_governance_snapshot()` 生成的快照中有 `hard_state_summary.mood_state`，但 `_normalize_snapshot()` 在构建 `GateSnapshot` 时**不读取 `hard_state_summary`**，只读取顶层字段：

```python
# _normalize_snapshot() 读取的字段（当前）
trust_level = raw.get("trust_level", 50)       # 顶层
energy_level = raw.get("energy_level", 60)     # 顶层
sys_mode = raw.get("sys_mode", "normal")       # 顶层
# ...

# hard_state_summary 中的 mood_state 完全被忽略！
# raw["hard_state_summary"]["mood_state"] → 永远读不到
```

这导致：
1. `GateSnapshot.ai_mood_state`（优化19新增）如果从顶层读取，会读到 `sys_mode`（系统模式），而不是 `mood_state`（情感状态）
2. `hard_state_summary` 中精心维护的 `mood_state` 对 Governor 门控完全透明

**方案：** 在 `_normalize_snapshot()` 中增加对 `hard_state_summary` 的读取，并在 `build_governance_snapshot()` 中将 `mood_state` 提升到顶层：

```python
# metabolism.py：build_governance_snapshot() 扩展
def build_governance_snapshot(
    state: HardState,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # ... 原有逻辑 ...
    return {
        "trust_level": trust,
        "energy_level": int(state.energy_level),
        "sys_mode": str(state.sys_mode),
        "mood_state": str(state.mood_state),    # 🆕 提升到顶层（供 GateSnapshot 读取）
        "rate_limit_hit": rate_limit_hit,
        "scope": scope,
        "scope_allowlist": list(allowlist),
        "safety_flag": safety_flag,
        "airlock_blocked": airlock_blocked,
        "hard_state_summary": {
            "energy_bucket": energy_bucket(state.energy_level),
            "mood_state": state.mood_state,
            "sys_mode": state.sys_mode,
            "safety_flag": safety_flag,
            "last_event_id": state.last_event_id,
        },
    }

# rules.py：_normalize_snapshot() 增加 mood_state 读取
def _normalize_snapshot(raw: dict[str, Any]) -> GateSnapshot:
    # ... 原有字段 ...
    # 🆕 mood_state 读取（优先顶层，回退到 hard_state_summary）
    hard_summary = raw.get("hard_state_summary", {})
    ai_mood_state = str(
        raw.get("mood_state")                           # 顶层（优先）
        or hard_summary.get("mood_state", "steady")    # hard_state_summary（回退）
    ).lower()
    return GateSnapshot(
        # ... 原有字段 ...
        ai_mood_state=ai_mood_state,
    )
```

**预期效果：** `mood_state` 信息不再在 `build_governance_snapshot()` → `_normalize_snapshot()` 的传递过程中丢失，R8 门控能够正确读取 AI 当前的情感状态。

---

### 🔵 深化优化23（P2）：`validate_context_pack()` 对 `bond_summary` 的 `stage`/`trend` 字段无语义校验

**目标文件：** `host_brain/core/context_pack.py`

**精读发现：**

`_validate_bond_summary_slot()` 只做两件事：
1. 检查是否包含数值（`CP_BOND_NUMERIC_FORBIDDEN`）
2. 检查是否有未知字段（`CP_BOND_SUMMARY_FIELD_INVALID`）

但对 `stage` 和 `trend` 字段的**枚举值合法性**完全不校验：

```python
# 当前校验（不完整）
def _validate_bond_summary_slot(bond_summary, result):
    for value in _iter_values(bond_summary):
        if isinstance(value, (int, float)):
            result.add(CP_BOND_NUMERIC_FORBIDDEN, ...)  # 只检查数值
    unknown = sorted(set(bond_summary.keys()) - set(BOND_SUMMARY_ALLOWED_KEYS))
    if unknown:
        result.add(CP_BOND_SUMMARY_FIELD_INVALID, ...)  # 只检查未知字段
    # ↑ 没有检查 stage 是否是合法的 Bond 阶段枚举！
    # ↑ 没有检查 trend 是否是 RISING/STABLE/DECLINING！
```

这意味着 `bond_summary["stage"] = "BEST_FRIEND"` 或 `bond_summary["trend"] = "GOING_UP"` 这样的非法值会直接注入 LLM，导致语义混乱。

**方案：** 在 `_validate_bond_summary_slot()` 中增加枚举值校验：

```python
# 合法的 Bond 阶段枚举（与 bond_stage_engine.py 保持一致）
VALID_BOND_STAGES = frozenset({
    "STRANGER", "ACQUAINTANCE", "FRIEND",
    "CLOSE_FRIEND", "CONFIDANT", "SOULMATE",
})
VALID_BOND_TRENDS = frozenset({"RISING", "STABLE", "DECLINING"})

def _validate_bond_summary_slot(
    bond_summary: dict[str, Any], result: ContextPackValidationResult
) -> None:
    # ... 原有数值检查 ...
    # ... 原有未知字段检查 ...

    # 🆕 枚举值合法性校验
    stage = bond_summary.get("stage")
    if stage is not None and str(stage) not in VALID_BOND_STAGES:
        result.add(
            CP_BOND_SUMMARY_FIELD_INVALID,
            f"bond_summary.stage invalid enum value: {stage!r}, "
            f"valid values: {sorted(VALID_BOND_STAGES)}",
            slot="bond_summary",
        )

    trend = bond_summary.get("trend")
    if trend is not None and str(trend) not in VALID_BOND_TRENDS:
        result.add(
            CP_BOND_SUMMARY_FIELD_INVALID,
            f"bond_summary.trend invalid enum value: {trend!r}, "
            f"valid values: {sorted(VALID_BOND_TRENDS)}",
            slot="bond_summary",
        )
```

**预期效果：** 非法的 Bond 阶段/趋势枚举值在注入 LLM 前被拦截，防止语义混乱。

---

### 🟣 深化优化24（P2）：`build_fallback_output()` 的降级回复无情感感知——降级时 AI 情感断层

**目标文件：** `host_brain/core/qa.py`

**精读发现：**

当 QA 校验失败触发降级时，`build_fallback_output()` 生成的回复是：

```python
def build_fallback_output(*, reason_code: str, stage: str) -> dict[str, Any]:
    return {
        "narrative": {
            "text": "当前无法确认执行结果，我会先保持草案回复并等待后续治理。",
        },
        "intent_draft": {},
        "meta": {
            "draft_only": True,
            "degraded": True,
            "reason_code": reason_code,
            "stage": stage,
        },
    }
```

**问题：**
1. 降级回复是**固定文本**，完全不感知当前情感状态——`engaged` 状态下降级和 `tired` 状态下降级，用户看到的是完全相同的冷冰冰的系统提示
2. 降级回复没有 `emotion_tag` 字段，会导致后续 `extract_emotion_tag()` 返回 `NEUTRAL`，情感状态被强制重置
3. 降级时 AI 的情感状态应该是"有些困惑/抱歉"，而不是机械的系统提示

**方案：** 为 `build_fallback_output()` 增加情感感知参数：

```python
# 降级回复的情感感知文本映射
_FALLBACK_TEXT_BY_MOOD: dict[str, tuple[str, str]] = {
    # mood_state → (fallback_text, emotion_tag)
    "engaged":  ("啊，刚才好像出了点小问题，让我重新整理一下思路～", "THINKING"),
    "steady":   ("刚才遇到了一点状况，我先缓一缓再回复你。", "THINKING"),
    "calm":     ("嗯……刚才有些地方没处理好，稍等一下。", "CALM"),
    "tired":    ("有点累了，刚才没能好好回应，抱歉……", "SAD"),
    "critical": ("……现在状态不太好，先休息一下。", "SAD"),
}

def build_fallback_output(
    *,
    reason_code: str,
    stage: str,
    mood_state: str = "steady",   # 🆕 新增参数
) -> dict[str, Any]:
    """生成情感感知的降级回复（不再是固定文本）。"""
    text, emotion_tag = _FALLBACK_TEXT_BY_MOOD.get(
        str(mood_state).lower(),
        _FALLBACK_TEXT_BY_MOOD["steady"],
    )
    return {
        "narrative": {
            "text": text,
            "emotion_tag": emotion_tag,   # 🆕 降级时也有情感标签
        },
        "intent_draft": {},
        "meta": {
            "draft_only": True,
            "degraded": True,
            "reason_code": reason_code,
            "stage": stage,
        },
    }
```

**调用方修改：** `_downgrade()` 函数需要接收并传递 `mood_state` 参数。

**预期效果：** 即使在 QA 降级时，AI 的情感状态也能自然流露，不会出现"情感断层"——用户不会突然看到一条冷冰冰的系统提示打断情感流。

---

## 十五、第四轮优化的核心价值总结

第四轮优化聚焦于**修复功能之间的连接断裂**——那些单独看每个功能都是完整的，但组合起来却无法协同工作的"架构断层"：

| 优化项 | 发现的"架构断层" | 断层位置 | 修复方案 |
|--------|----------------|---------|---------|
| **优化19** 🔴 | R8 门控无法落地：`GateSnapshot` 不含情感字段，情感数据被 `_normalize_snapshot()` 静默丢弃 | `rules.py` ↔ `decision.py` | 扩展 `GateSnapshot`，`_normalize_snapshot()` 读取情感字段 |
| **优化20** 🟠 | `MOOD_EMOTION_CONSTRAINTS` 永远不生效：`extract_emotion_tag()` 从不传 `mood_state` | `qa.py` ↔ `metabolism.py` | 为 `extract_emotion_tag()` 增加 `mood_state` 参数 |
| **优化21** 🟡 | `to_summary()` 混合英文枚举和中文描述，LLM 语义引导效果差 | `bond_stage_engine.py` ↔ LLM | 增加 `stage_hint`/`trend_hint` 中文语义字段 |
| **优化22** 🟢 | `mood_state` 在快照传递中丢失：`build_governance_snapshot()` 不将 `mood_state` 提升到顶层 | `metabolism.py` ↔ `rules.py` | 将 `mood_state` 提升到快照顶层 |
| **优化23** 🔵 | `bond_summary` 枚举值无校验：非法 `stage`/`trend` 值可直接注入 LLM | `context_pack.py` | 增加枚举值合法性校验 |
| **优化24** 🟣 | 降级时情感断层：`build_fallback_output()` 固定文本，情感状态被强制重置为 `NEUTRAL` | `qa.py` ↔ 情感流 | 降级回复感知 `mood_state`，保持情感连续性 |

---

## 十六、完整优化全景图（v1.3，24项）

### 按层次分类

```
┌─────────────────────────────────────────────────────────────────┐
│                    基础设施层（必须先做）                          │
│  OPT16: OPTIONAL_SLOTS扩展    OPT17: BondEventPayload标准化      │
│  OPT18: state_age_hint        OPT19: GateSnapshot情感字段        │
│  OPT22: mood_state提升到顶层  OPT23: bond_summary枚举校验        │
└─────────────────────────────────────────────────────────────────┘
         ↓ 基础设施就绪后
┌─────────────────────────────────────────────────────────────────┐
│                    情感内核层（P0）                               │
│  OPT1: EmotionVector          OPT13: event_kind语义扩展          │
│  OPT6: emotion_tag生成策略    OPT20: extract_emotion_tag修复     │
│  OPT7: Bond质量分级           OPT14: trend_hint语义化            │
└─────────────────────────────────────────────────────────────────┘
         ↓ 情感内核就绪后
┌─────────────────────────────────────────────────────────────────┐
│                    记忆层（P1）                                   │
│  OPT3: 情感记忆标注           OPT15: 记忆新鲜度衰减              │
│  OPT9: 情感时间线重建                                            │
└─────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│                    关系层（P1）                                   │
│  OPT2: Bond质感层             OPT11: Bond裂痕修复                │
│  OPT8: Governor R8门控        OPT21: to_summary语义化            │
└─────────────────────────────────────────────────────────────────┘
         ↓ 核心层就绪后
┌─────────────────────────────────────────────────────────────────┐
│                    输出层（P2）                                   │
│  OPT4: emotion_pulse槽位      OPT5: 主动触发器                   │
│  OPT10: LLM生成参数           OPT12: 情感回声                    │
│  OPT24: 降级情感感知                                             │
└─────────────────────────────────────────────────────────────────┘
```

### 实施路线图（按优先级）

| 批次 | 优化项 | 核心目标 |
|------|--------|---------|
| **Batch 1（基础设施）** | OPT16, OPT17, OPT18, OPT19, OPT22, OPT23 | 打通数据流，修复架构断层 |
| **Batch 2（情感内核）** | OPT1, OPT13, OPT20, OPT6, OPT7, OPT14 | 建立连续情感状态机 |
| **Batch 3（记忆+关系）** | OPT3, OPT15, OPT9, OPT2, OPT11, OPT8, OPT21 | 情感记忆+关系温度 |
| **Batch 4（输出增强）** | OPT4, OPT5, OPT10, OPT12, OPT24 | 情感感知的输出层 |

---

## 十七、第五轮深化优化：跨模块情感盲区

> **v1.4 新增**（2026-03-05）。本轮基于对 `memory/episodic.py`、`bond/bond_dynamics.py`、`bond/summarizer.py`、`memory/context_compressor.py`、`memory/long_term_distiller.py`、`scheduler/scheduler.py` 的完整逐行精读，发现了前四轮完全未触及的 **6 个跨模块情感盲区**——这些问题分布在记忆层、关系层、调度层，是情感信息在"存储→蒸馏→调度→输出"全链路中的系统性缺失。

---

### 🔴 深化优化25（P0）：`EpisodicRecord` 无情感标注字段——情感记忆无法被情感检索

**目标文件：** `host_brain/memory/episodic.py`

**精读发现：**

`EpisodicRecord` 数据类的字段：

```python
@dataclass
class EpisodicRecord:
    memory_id: str
    text: str
    ts_ms: int
    text_hash: str
    decay_profile_id: str
    evidence_refs: list[str]
    visibility: str
    embedding_model_id: str
    merged_into: str | None = None
    deleted_at: int | None = None
    # ↑ 完全没有情感相关字段！
```

`write_memory()` 的参数也没有情感字段：

```python
def write_memory(
    self,
    *,
    text: str,
    evidence_refs: list[str],
    request_id: str | None = None,
    intent_id: str | None = None,
    decay_profile_id: str = "default",
    visibility: str = "internal",
    ts_ms: int | None = None,
    # ↑ 没有 emotion_tag、mood_state、bond_stage 等情感字段
) -> MemoryOpResult:
```

Chroma 向量库的 `metadata` 也不含情感字段：

```python
self.chroma.upsert(
    memory_id=memory_id,
    text=normalized_text,
    embedding=embedding,
    metadata={
        "ts_ms": ts_ms,
        "text_hash": text_hash,
        "visibility": record.visibility,
        "evidence_refs": list(evidence_refs),
        "embedding_model_id": model_id,
        # ↑ 没有 emotion_tag！无法按情感过滤检索
    },
)
```

这意味着：
1. 所有情节记忆都是"情感盲"的——无法知道这条记忆是在什么情感状态下产生的
2. `recall()` 只能做语义检索，无法做"检索我们开心时的记忆"这样的情感过滤
3. 优化3（情感记忆标注）和优化9（情感时间线重建）的实现前提完全缺失

**方案：** 为 `EpisodicRecord` 增加情感标注字段，并在 `write_memory()` 和 Chroma metadata 中传递：

```python
@dataclass
class EpisodicRecord:
    memory_id: str
    text: str
    ts_ms: int
    text_hash: str
    decay_profile_id: str
    evidence_refs: list[str]
    visibility: str
    embedding_model_id: str
    merged_into: str | None = None
    deleted_at: int | None = None
    # 🆕 情感标注字段（v1.4 新增）
    emotion_tag: str = "NEUTRAL"        # 记忆产生时的情感标签
    mood_state: str = "steady"          # 记忆产生时的 AI mood_state
    bond_stage: str = "STRANGER"        # 记忆产生时的 Bond 阶段
    emotional_intensity: float = 0.0    # 情感强度（0.0~1.0，用于衰减权重）

def write_memory(
    self,
    *,
    text: str,
    evidence_refs: list[str],
    request_id: str | None = None,
    intent_id: str | None = None,
    decay_profile_id: str = "default",
    visibility: str = "internal",
    ts_ms: int | None = None,
    # 🆕 情感标注参数
    emotion_tag: str = "NEUTRAL",
    mood_state: str = "steady",
    bond_stage: str = "STRANGER",
    emotional_intensity: float = 0.0,
) -> MemoryOpResult:
    # ...
    # Chroma metadata 增加情感字段
    self.chroma.upsert(
        memory_id=memory_id,
        text=normalized_text,
        embedding=embedding,
        metadata={
            "ts_ms": ts_ms,
            "text_hash": text_hash,
            "visibility": record.visibility,
            "evidence_refs": list(evidence_refs),
            "embedding_model_id": model_id,
            "emotion_tag": emotion_tag,           # 🆕
            "mood_state": mood_state,             # 🆕
            "bond_stage": bond_stage,             # 🆕
            "emotional_intensity": emotional_intensity,  # 🆕
        },
    )
```

**新增情感检索接口：**

```python
def recall_by_emotion(
    self,
    *,
    query_text: str,
    emotion_tag: str | None = None,
    bond_stage: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """按情感标签过滤的情感记忆检索。"""
    embedding, _ = embed_text(query_text)
    results = self.chroma.search(query_embedding=embedding, top_k=top_k * 3)
    # 按情感过滤
    filtered = [
        r for r in results
        if (emotion_tag is None or r.get("metadata", {}).get("emotion_tag") == emotion_tag)
        and (bond_stage is None or r.get("metadata", {}).get("bond_stage") == bond_stage)
    ]
    return filtered[:top_k]
```

**预期效果：** 情节记忆从"情感盲"变为"情感感知"，支持"检索我们开心时的记忆"、"检索挚友阶段的记忆"等情感维度的检索，是优化3和优化9的必要基础设施。

---

### 🟠 深化优化26（P0）：`BondDynamicsWriter.apply_update()` 的 `payload` 无情感字段——Bond 事件情感盲

**目标文件：** `host_brain/bond/bond_dynamics.py`

**精读发现：**

`apply_update()` 的 `payload` 参数是 `dict[str, Any] | None`，完全自由格式，没有任何情感字段的约定：

```python
def apply_update(
    self,
    *,
    request_id: str,
    intent_id: str,
    session_id: str,
    seq: int,
    user_id: str,
    topic_id: str,
    capability_hint: str,
    evidence_refs: list[str],
    payload: dict[str, Any] | None = None,   # ← 自由格式，无情感约定
    update_kind: str = "INTERACTION",
    event_id: str | None = None,
    ts_ms: int | None = None,
) -> BondUpdateResult:
```

写入 Neo4j 的事件也不含情感字段：

```python
written = self.neo4j_client.write_bond_event(
    writer_id=BOND_WRITER_ID,
    event_id=event_key,
    user_id=str(user_id),
    session_id=str(session_id),
    topic_id=str(topic_id),
    capability_hint=str(capability_hint),
    evidence_refs=refs,
    payload=dict(payload or {}),   # ← payload 内容完全不确定
    ts_ms=now_value,
)
```

这意味着：
1. Bond 事件图谱中没有情感维度——无法知道"这次互动是在什么情感状态下发生的"
2. `BondStageEngine.apply_event()` 的积分计算不感知情感——`EXCITED` 状态下的互动和 `SAD` 状态下的互动得分相同
3. 无法做"情感加权的 Bond 积分"——深度情感互动应该比普通互动贡献更多 Bond 积分

**方案：** 为 `apply_update()` 增加情感参数，并在 `BondStageEngine.apply_event()` 中支持情感加权：

```python
def apply_update(
    self,
    *,
    request_id: str,
    intent_id: str,
    session_id: str,
    seq: int,
    user_id: str,
    topic_id: str,
    capability_hint: str,
    evidence_refs: list[str],
    payload: dict[str, Any] | None = None,
    update_kind: str = "INTERACTION",
    event_id: str | None = None,
    ts_ms: int | None = None,
    # 🆕 情感参数
    emotion_tag: str = "NEUTRAL",
    mood_state: str = "steady",
    emotional_intensity: float = 0.0,
) -> BondUpdateResult:
    # ...
    # 将情感字段注入 payload
    enriched_payload = dict(payload or {})
    enriched_payload.update({
        "emotion_tag": emotion_tag,
        "mood_state": mood_state,
        "emotional_intensity": round(float(emotional_intensity), 4),
    })
    # ...
    # BondStageEngine 传递情感强度（用于加权积分）
    if self._stage_engine is not None:
        stage_transition = self._stage_engine.apply_event(
            user_id=str(user_id),
            update_kind=str(update_kind),
            evidence_refs=refs,
            request_id=request_id,
            ts_ms=now_value,
            emotional_intensity=emotional_intensity,  # 🆕 情感加权
        )
```

**情感加权积分规则（在 `BondStageEngine` 中实现）：**

```python
# 情感强度加权系数
EMOTION_INTENSITY_MULTIPLIER = {
    "EXCITED":  1.5,   # 兴奋状态的互动贡献 1.5x 积分
    "HAPPY":    1.3,   # 开心状态贡献 1.3x
    "NEUTRAL":  1.0,   # 中性状态贡献 1.0x（基准）
    "CALM":     0.9,   # 平静状态贡献 0.9x
    "SAD":      0.7,   # 悲伤状态贡献 0.7x（情感低落时互动质量下降）
    "ANGRY":    0.5,   # 愤怒状态贡献 0.5x（负面情感对关系有损耗）
}
```

**预期效果：** Bond 积分从"互动次数计数"升级为"情感加权的关系质量评估"，深度情感互动（`EXCITED`/`HAPPY`）比普通互动贡献更多 Bond 积分，关系发展更真实。

---

### 🟡 深化优化27（P1）：`bond/summarizer.py` 的 `derive_bond_summary()` 从 event 读取 `stage`/`trend`——但 Bond 事件不含这两个字段

**目标文件：** `host_brain/bond/summarizer.py`

**精读发现：**

`derive_bond_summary()` 从 `event` 字典中读取 `stage` 和 `trend`：

```python
def derive_bond_summary(event: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "stage": _as_text_bucket(event.get("stage"), default=DEFAULT_STAGE),
        "trend": _as_text_bucket(event.get("trend"), default=DEFAULT_TREND),
        "capability_hint": _as_text_bucket(
            event.get("capability_hint"),
            default=DEFAULT_CAPABILITY_HINT,
        ),
        "event_id": _as_optional_text(event.get("event_id")),
        "evidence_refs": _normalize_evidence_refs(event.get("evidence_refs")),
    }
```

但 `BondDynamicsWriter.apply_update()` 写入 Neo4j 的事件结构是：

```python
event = {
    "request_id": ...,
    "intent_id": ...,
    "session_id": ...,
    "seq": ...,
    "event_id": ...,
    "user_id": ...,
    "topic_id": ...,
    "capability_hint": ...,   # ← 有 capability_hint
    "update_kind": ...,
    "evidence_refs": ...,
    "payload": ...,
    "neo4j_event": ...,
    "stage": "S_BOND_UPDATE",  # ← 这是流水线阶段名，不是 Bond 阶段！
    "status": ...,
    "reason_code": ...,
    "ts_ms": ...,
    "stage_transition": {
        "from_stage": ...,
        "to_stage": ...,       # ← Bond 阶段在这里，不在顶层！
        "transition_status": ...,
        "score_delta": ...,
        "total_score": ...,
    },
}
```

**问题：**
1. `event["stage"]` 是 `"S_BOND_UPDATE"`（流水线阶段名），不是 `"FRIEND"` 这样的 Bond 阶段
2. `event["trend"]` 根本不存在，`derive_bond_summary()` 会返回 `DEFAULT_TREND = "STABLE"`
3. 真正的 Bond 阶段在 `event["stage_transition"]["to_stage"]` 里，但 `derive_bond_summary()` 不读这个字段

这意味着：**`derive_bond_summary()` 永远返回错误的 `stage`（`"S_BOND_UPDATE"`）和默认的 `trend`（`"STABLE"`）**，注入 LLM 的 Bond 信息完全失真。

**方案：** 修复 `derive_bond_summary()` 的字段读取逻辑：

```python
def derive_bond_summary(event: dict[str, Any] | None) -> dict[str, Any]:
    """从 Bond 事件记录构建 context-pack-safe 的 Bond Summary。

    修复：Bond 阶段在 stage_transition.to_stage，不在顶层 stage 字段。
    """
    if not isinstance(event, dict):
        event = {}

    # 🆕 修复：从 stage_transition 读取真实的 Bond 阶段
    stage_transition = event.get("stage_transition") or {}
    bond_stage = (
        _as_text_bucket(stage_transition.get("to_stage"), default="")
        or _as_text_bucket(event.get("bond_stage"), default=DEFAULT_STAGE)
    )

    # 🆕 修复：从 stage_transition 推断趋势
    transition_status = str(stage_transition.get("transition_status") or "").upper()
    score_delta = int(stage_transition.get("score_delta") or 0)
    if transition_status == "PROMOTED":
        trend = "RISING"
    elif transition_status == "DEMOTED":
        trend = "DECLINING"
    elif score_delta > 0:
        trend = "RISING"
    elif score_delta < 0:
        trend = "DECLINING"
    else:
        trend = DEFAULT_TREND

    return {
        "stage": bond_stage,
        "trend": trend,
        "capability_hint": _as_text_bucket(
            event.get("capability_hint"),
            default=DEFAULT_CAPABILITY_HINT,
        ),
        "event_id": _as_optional_text(event.get("event_id")),
        "evidence_refs": _normalize_evidence_refs(event.get("evidence_refs")),
    }
```

**预期效果：** `derive_bond_summary()` 终于能返回正确的 Bond 阶段和趋势，注入 LLM 的 Bond 信息不再失真。这是整个 Bond 信息链路的关键修复。

---

### 🟢 深化优化28（P1）：`ContextCompressor._pre_compress_save()` 只保存 assistant 轮次——用户情感信息被丢弃

**目标文件：** `host_brain/memory/context_compressor.py`

**精读发现：**

`_pre_compress_save()` 的过滤逻辑：

```python
for idx, turn in enumerate(turns):
    role = str(turn.get("role") or "user")
    content = str(turn.get("content") or "").strip()
    if not content:
        continue
    # 仅保存 assistant 回复（含有实质性信息的轮次）
    if role != "assistant":
        continue   # ← 用户轮次被完全跳过！
    evidence_ref = f"compress://pre_save/{session_id or 'unknown'}/turn_{idx:03d}"
    try:
        self._episodic_writer(content, [evidence_ref])
        saved_count += 1
    except Exception:
        pass
```

**问题：**
1. 用户的情感表达（"我今天很难过"、"这让我很开心"）被完全丢弃，只保存 AI 的回复
2. 用户的重要陈述（"我明天要去面试"、"我妈妈生病了"）也被丢弃
3. 压缩前保存的记忆是"单声道"的——只有 AI 的声音，没有用户的声音
4. 这导致长期记忆蒸馏（`LongTermMemoryDistiller`）的原材料严重偏斜

**方案：** 改为保存"高价值轮次"，包括用户的情感表达和重要陈述：

```python
# 高价值轮次判断规则
_EMOTION_KEYWORDS = frozenset({
    "难过", "开心", "高兴", "伤心", "生气", "害怕", "担心", "兴奋",
    "感谢", "抱歉", "对不起", "喜欢", "讨厌", "爱", "恨",
    "sad", "happy", "angry", "scared", "excited", "love", "hate",
})

def _is_high_value_turn(turn: dict[str, Any]) -> bool:
    """判断是否为高价值轮次（值得在压缩前保存）。"""
    role = str(turn.get("role") or "user")
    content = str(turn.get("content") or "").strip()
    if not content:
        return False
    # assistant 轮次：始终保存（原有逻辑）
    if role == "assistant":
        return True
    # user 轮次：只保存含情感关键词或较长的重要陈述
    if any(kw in content for kw in _EMOTION_KEYWORDS):
        return True
    if len(content) > 50:  # 超过50字的用户陈述视为重要
        return True
    return False

def _pre_compress_save(
    self,
    turns: list[dict[str, Any]],
    *,
    strategy: str,
    session_id: str | None,
) -> int:
    # ...
    for idx, turn in enumerate(turns):
        if not _is_high_value_turn(turn):   # 🆕 替换原有的 role != "assistant" 过滤
            continue
        role = str(turn.get("role") or "user")
        content = str(turn.get("content") or "").strip()
        evidence_ref = f"compress://pre_save/{session_id or 'unknown'}/{role}/turn_{idx:03d}"
        try:
            self._episodic_writer(content, [evidence_ref])
            saved_count += 1
        except Exception:
            pass
```

**预期效果：** 压缩前保存的记忆从"只有 AI 声音"变为"双声道"，用户的情感表达和重要陈述也被保存，长期记忆蒸馏的原材料更加完整和真实。

---

### 🔵 深化优化29（P1）：`LongTermMemoryDistiller` 的蒸馏主题列表不含"情感关系"维度

**目标文件：** `host_brain/memory/long_term_distiller.py`

**精读发现：**

`DEFAULT_DISTILL_TOPICS` 的定义：

```python
DEFAULT_DISTILL_TOPICS: list[str] = [
    "用户日常习惯与偏好",
    "重要事件与里程碑",
    "情感状态与心理变化",   # ← 有情感，但太宽泛
    "用户目标与计划",
    "关系与社交互动",
]
```

**问题：**
1. `"情感状态与心理变化"` 太宽泛，无法精确召回特定情感的记忆
2. 没有"与 AI 的情感互动"维度——无法蒸馏出"用户和 AI 之间的情感关系史"
3. 没有"情感转折点"维度——无法识别"用户情感发生重大变化的时刻"
4. 没有"用户的情感需求"维度——无法蒸馏出"用户在什么情况下需要情感支持"
5. 蒸馏结果写入的 `DISTILL_TARGET_DOC_ID = "wc-distilled-long-term-memory"` 是单一文档，所有主题混在一起，无法按情感维度检索

**方案：** 扩展蒸馏主题列表，增加情感关系维度，并为不同维度使用不同的目标文档：

```python
# 🆕 情感关系专项蒸馏主题（v1.4 新增）
EMOTION_DISTILL_TOPICS: list[str] = [
    "用户与AI之间的情感互动与关系发展",
    "用户情感转折点与重要情感时刻",
    "用户的情感需求与情感支持模式",
    "用户表达过的喜好、厌恶与情感偏好",
    "用户在困难时期的情感状态与应对方式",
]

# 🆕 情感蒸馏专用目标文档
EMOTION_DISTILL_TARGET_DOC_ID = "wc-distilled-emotion-relationship"

DEFAULT_DISTILL_TOPICS: list[str] = [
    "用户日常习惯与偏好",
    "重要事件与里程碑",
    "用户情感状态与心理变化",
    "用户目标与计划",
    "关系与社交互动",
    # 🆕 情感关系维度
    "用户与AI之间的情感互动与关系发展",
    "用户情感转折点与重要情感时刻",
    "用户的情感需求与情感支持模式",
]
```

**同时修复 `_build_proposal_body()` 的目标文档路由：**

```python
def _submit_canon_proposal(self, *, semantic_id, topic, source_count, request_id, ts_ms):
    # 🆕 情感主题路由到专用文档
    is_emotion_topic = any(
        kw in topic for kw in ["情感", "心理", "关系发展", "情感需求"]
    )
    target_doc = (
        EMOTION_DISTILL_TARGET_DOC_ID if is_emotion_topic
        else self._target_doc_id
    )
    # ...
```

**预期效果：** 长期记忆蒸馏能够专门提炼"情感关系史"，生成的 Canon 提案包含用户与 AI 之间的情感发展脉络，为 LLM 提供更丰富的情感背景。

---

### 🟣 深化优化30（P2）：`NatsumeScheduler` 的心跳检测 Prompt 不感知情感状态——心跳时 AI 情感被重置

**目标文件：** `host_brain/scheduler/scheduler.py`

**精读发现：**

`_run_agent_health_check()` 的实现：

```python
def _run_agent_health_check(self) -> dict[str, Any]:
    prompt = getattr(self, "_heartbeat_prompt", DEFAULT_HEARTBEAT_PROMPT)
    # ...
    raw_response: str
    if llm_caller is not None:
        try:
            raw_response = str(llm_caller(prompt))
        except Exception as exc:
            raw_response = LLM_UNAVAILABLE_RESPONSE
    # ...
    state = parse_heartbeat_response(raw_response)
    # ...
```

`DEFAULT_HEARTBEAT_PROMPT` 是固定文本（来自 `health_check_prompt.py`），不包含当前情感状态。

**问题：**
1. 心跳检测每 30 分钟触发一次，使用固定 Prompt，不感知当前 `mood_state`
2. 如果 AI 当前处于 `tired` 状态，心跳 Prompt 仍然是"正常"的，LLM 会生成"正常"的回复，导致情感状态被隐式重置
3. 心跳检测的 `llm_caller` 调用会产生一次 LLM 推理，但这次推理的结果不会更新 `HardState`，情感状态在心跳期间处于"悬空"状态
4. 心跳 OK 时"静默丢弃"（不写审计），但如果心跳期间情感状态发生了变化，这个变化也被静默丢弃了

**方案：** 为心跳检测增加情感状态感知，并在心跳 OK 时记录情感快照：

```python
def register_agent_health_check(
    self,
    *,
    interval_seconds: int = 1800,
    heartbeat_prompt: str | None = None,
    max_response_chars: int = MAX_RESPONSE_CHARS,
    llm_caller: Any | None = None,
    get_mood_state: Callable[[], str] | None = None,   # 🆕 情感状态获取函数
) -> None:
    # ...
    self._heartbeat_get_mood_state = get_mood_state  # 🆕 保存情感状态获取函数

def _run_agent_health_check(self) -> dict[str, Any]:
    prompt = getattr(self, "_heartbeat_prompt", DEFAULT_HEARTBEAT_PROMPT)
    max_chars = getattr(self, "_heartbeat_max_chars", MAX_RESPONSE_CHARS)
    llm_caller = getattr(self, "_heartbeat_llm_caller", None)
    get_mood_state = getattr(self, "_heartbeat_get_mood_state", None)
    ts = now_ms()

    # 🆕 获取当前情感状态，注入心跳 Prompt
    current_mood = get_mood_state() if get_mood_state else "steady"
    mood_aware_prompt = _build_mood_aware_heartbeat_prompt(prompt, current_mood)

    raw_response: str
    if llm_caller is not None:
        try:
            raw_response = str(llm_caller(mood_aware_prompt))  # 🆕 使用情感感知 Prompt
        except Exception as exc:
            raw_response = LLM_UNAVAILABLE_RESPONSE
    else:
        raw_response = LLM_UNAVAILABLE_RESPONSE

    state = parse_heartbeat_response(raw_response)
    truncated = truncate_response(raw_response, max_chars)

    if state == "OK":
        # 🆕 心跳 OK 时记录情感快照（不再完全静默）
        self._log_heartbeat_emotion_snapshot(mood_state=current_mood, ts_ms=ts)
        self._check_edge_transport_probes(ts_ms=ts)
        return {"status": HEARTBEAT_EVENT_TYPE_OK, "mood_state": current_mood, "ts_ms": ts}

    # ...

def _build_mood_aware_heartbeat_prompt(base_prompt: str, mood_state: str) -> str:
    """为心跳 Prompt 注入当前情感状态上下文。"""
    mood_hints = {
        "engaged":  "（当前状态：精力充沛，积极投入）",
        "steady":   "（当前状态：平稳正常）",
        "calm":     "（当前状态：平静放松）",
        "tired":    "（当前状态：有些疲惫，需要休息）",
        "critical": "（当前状态：能量极低，需要立即休息）",
    }
    hint = mood_hints.get(str(mood_state).lower(), "")
    return f"{base_prompt}\n\n[系统提示：当前情感状态 {hint}]"
```

**预期效果：** 心跳检测不再是情感状态的"黑洞"，`tired` 状态下的心跳会使用感知疲惫的 Prompt，LLM 的回复会反映真实的情感状态，心跳 OK 时也会记录情感快照供后续分析。

---

## 十八、第五轮优化的核心价值总结

第五轮优化聚焦于**跨模块情感信息的系统性缺失**——情感信息在"存储→蒸馏→调度→输出"全链路中的断点：

| 优化项 | 发现的"跨模块盲区" | 影响范围 | 修复方案 |
|--------|------------------|---------|---------|
| **优化25** 🔴 | `EpisodicRecord` 无情感字段，情感记忆无法被情感检索 | `episodic.py` → 整个记忆层 | 增加情感标注字段，支持情感过滤检索 |
| **优化26** 🟠 | Bond 事件无情感字段，Bond 积分不感知情感强度 | `bond_dynamics.py` → Bond 积分 | 情感加权 Bond 积分，深度情感互动贡献更多积分 |
| **优化27** 🟡 | `derive_bond_summary()` 读取错误字段，Bond 信息完全失真 | `summarizer.py` → LLM 输入 | 修复字段读取逻辑，从 `stage_transition` 读取真实 Bond 阶段 |
| **优化28** 🟢 | 压缩前只保存 assistant 轮次，用户情感信息被丢弃 | `context_compressor.py` → 长期记忆 | 改为保存高价值轮次，包括用户情感表达 |
| **优化29** 🔵 | 蒸馏主题不含情感关系维度，无法蒸馏情感关系史 | `long_term_distiller.py` → Canon | 增加情感关系专项蒸馏主题 |
| **优化30** 🟣 | 心跳检测不感知情感状态，心跳期间情感被隐式重置 | `scheduler.py` → 情感连续性 | 心跳 Prompt 注入情感状态，记录情感快照 |

---

## 十九、完整优化全景图（v1.4，30项）

### 五轮优化的层次递进

```
第一轮（v1.0）：发现5个"缺失功能"     → 需要新增什么
第二轮（v1.1）：发现6个"深层问题"     → 现有机制哪里不够深
第三轮（v1.2）：发现6个"假功能空壳"   → 现有代码哪里看起来有但实际没用
第四轮（v1.3）：发现6个"架构断层"     → 功能之间的连接在哪里断裂
第五轮（v1.4）：发现6个"跨模块盲区"   → 情感信息在全链路中的系统性缺失
```

### 按优先级分类（30项全景）

| 优先级 | 优化项 | 核心问题 |
|--------|--------|---------|
| **P0（必须修复）** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27 | 架构断层 + 跨模块盲区中的关键路径 |
| **P1（重要优化）** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29 | 情感内核 + 关系层 + 记忆层 |
| **P2（体验增强）** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30 | 输出层 + 细节打磨 |

### 变更记录（完整版）

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-03-05 | v1.0 | 初稿，基于代码深度分析创建，记录五大优化方向（§一~§五） |
| 2026-03-05 | v1.1 | 深化分析：基于全量代码精读，新增六大深化优化方向（§七~§九） |
| 2026-03-05 | v1.2 | 第三轮深化：发现6个"假功能"空壳问题，新增优化13~18（§十~§十二） |
| 2026-03-05 | v1.3 | 第四轮深化：发现6个"架构断层"问题，新增优化19~24（§十四~§十六） |
| 2026-03-05 | v1.4 | 第五轮深化：发现6个"跨模块情感盲区"，新增优化25~30（§十七~§十九） |
| 2026-03-05 | v1.5 | 第六轮深化：发现6个"感知层情感断路"，新增优化31~36（§二十~§二十二） |
| 2026-03-05 | v1.6 | 第七轮深化：发现6个"执行层情感真空"，新增优化37~42（§二十三~§二十五） |
| 2026-03-05 | v1.7 | 第八轮深化：发现6个"基础设施情感盲区"，新增优化43~48（§二十六~§二十八） |

---

## 二十六、第八轮深化优化：基础设施情感盲区

> **v1.7 新增**（2026-03-05）。本轮基于对 `scheduler/scheduler.py`、`integrations/llm_gateway.py`、`integrations/graph_executor.py`、`obs/metrics.py`、`edge_client/actuators/tts_engine.py`、`edge_client/senses/asr.py` 的完整逐行精读，发现了前七轮完全未触及的 **6 个基础设施情感盲区**——这些问题分布在调度层、LLM网关层、图执行层、指标观测层、TTS引擎层、ASR感知层，是情感信息在"基础设施→感知→输出"链路中的系统性盲区。

---

### 🔴 深化优化43（P0）：`NatsumeScheduler._build_system_intent()` 不携带情感状态——调度任务触发时情感盲

**目标文件：** `host_brain/scheduler/scheduler.py`

**精读发现：**

`_build_system_intent()` 生成的 `system_initiated_intent`：

```python
def _build_system_intent(
    self,
    task: SchedulerTaskConfig,
    *,
    intent_id: str,
    request_id: str,
) -> dict[str, Any]:
    template = dict(task.intent_template)
    return {
        "intent_id": intent_id,
        "request_id": request_id,
        "proposer_node_id": PROPOSER_NODE_ID,
        "task_id": task.task_id,
        "skill_name": template.get("skill_name", task.task_id),
        "args": template.get("args", {}),
        "priority": task.priority.value,
        "trigger_mode": task.trigger_mode.value,
        "ts_ms": now_ms(),
        # ↑ 没有情感状态字段！
    }
```

`_trigger_task()` 的门控检查：

```python
# sys_mode 门控
current_mode = self._get_sys_mode()
if not task.is_allowed_in_mode(current_mode):
    # ↑ 只检查 sys_mode，不检查情感状态！
```

`_log_scheduler_event()` 的审计记录：

```python
self._ledger.log_scheduler_event({
    "event_id": ...,
    "task_id": task_id,
    "trigger_ts": trigger_ts,
    "intent_id": intent_id,
    "status": status,
    "reason_code": reason_code,
    "mode_at_trigger": mode_at_trigger,
    "error_detail": error_detail,
    "ts_ms": ts_ms,
    # ↑ 没有情感状态字段！
})
```

这意味着：
1. 调度任务触发时完全不感知情感状态——`critical` 情感状态下（AI 能量极低），高消耗的定时任务（如每30分钟的主动问候任务）仍然会被触发
2. `_build_system_intent()` 生成的 intent 不携带情感状态，下游的 Governor 无法感知这是在什么情感状态下触发的调度任务
3. 调度审计日志不含情感字段，无法分析"哪些情感状态下调度任务执行成功率更高"

**方案：** 为调度器增加情感状态感知能力：

```python
class NatsumeScheduler:
    def __init__(
        self,
        registry: SchedulerTaskRegistry,
        health_reporter: SchedulerHealthReporter,
        *,
        intent_dispatcher: IntentDispatcher | None = None,
        get_sys_mode: Callable[[], str] | None = None,
        # 🆕 情感状态获取函数（v1.7 新增）
        get_emotion_state: Callable[[], dict[str, Any]] | None = None,
        ledger: Any | None = None,
        persist_tasks: bool = True,
    ) -> None:
        # ...
        # 🆕 情感状态获取函数（返回 {"emotion_tag": ..., "mood_state": ..., "bond_stage": ...}）
        self._get_emotion_state = get_emotion_state or (lambda: {})
```

**情感门控：**

```python
def _trigger_task(self, task_id: str) -> dict[str, Any]:
    # ...
    # 🆕 情感状态门控（v1.7 新增）
    emotion_state = self._get_emotion_state()
    mood_state = str(emotion_state.get("mood_state") or "steady").lower()
    emotion_tag = str(emotion_state.get("emotion_tag") or "NEUTRAL").upper()

    # critical 状态下，MEDIUM/HIGH 优先级任务被暂缓
    if mood_state == "critical" and task.priority.value in ("MEDIUM", "HIGH"):
        reason = "EMOTION_CRITICAL_THROTTLE"
        self._log_scheduler_event(
            task_id=task_id,
            trigger_ts=trigger_ts,
            status="THROTTLED",
            reason_code=reason,
            mode_at_trigger=current_mode,
            emotion_tag=emotion_tag,
            mood_state=mood_state,
            error_detail=f"task throttled: mood_state=critical, priority={task.priority.value}",
        )
        return {"status": "THROTTLED", "reason_code": reason, "task_id": task_id, "mood_state": mood_state}
```

**`_build_system_intent()` 携带情感状态：**

```python
def _build_system_intent(self, task, *, intent_id, request_id) -> dict[str, Any]:
    emotion_state = self._get_emotion_state()
    return {
        # ... 原有字段 ...
        # 🆕 情感状态字段
        "emotion_tag": emotion_state.get("emotion_tag", "NEUTRAL"),
        "mood_state": emotion_state.get("mood_state", "steady"),
        "bond_stage": emotion_state.get("bond_stage", "STRANGER"),
    }
```

**预期效果：** 调度器从"情感盲"变为"情感感知"，`critical` 状态下高消耗任务被自动暂缓，调度 intent 携带情感状态供下游 Governor 感知，审计日志可分析情感状态与调度执行的相关性。

---

### 🟠 深化优化44（P1）：`LlmGatewayContract.generate()` 的 `prompt_payload` 不携带情感上下文——LLM 调用时情感盲

**目标文件：** `host_brain/integrations/llm_gateway.py`

**精读发现：**

`generate()` 的 `prompt_payload` 参数：

```python
def generate(
    self,
    *,
    request_id: str,
    model_profile_id: str,
    prompt_payload: dict[str, Any],   # ← 自由格式 dict，没有情感字段约定
    ts_ms: int | None = None,
) -> GatewayResult:
```

`_degrade()` 的降级回复：

```python
fallback_payload = {
    "narrative": {"text": "当前外部语义服务不可用，我先给出草案回复。"},
    "intent_draft": {},
    "meta": {
        "draft_only": True,
        "degraded": True,
        "reason_code": reason_code,
        "stage": stage,
    },
    # ↑ 降级回复完全不感知情感状态！
}
```

`GatewayResult` 的输出结构：

```python
output = {
    "request_id": request_id,
    "model_profile_id": model_profile_id,
    "provider": self.provider_name,
    "draft_only": True,
    "status": status,
    "stage": S_LLM_GATEWAY_QA,
    "reason_code": qa_result.reason_code,
    "latency_ms": latency_ms,
    "payload": qa_result.payload,
    "airlock": {...},
    # ↑ 没有情感字段！
}
```

这意味着：
1. LLM 调用时 `prompt_payload` 不携带情感上下文——LLM 无法感知当前 AI 的情感状态，生成的回复不会体现情感色彩
2. 降级回复（`_degrade()`）完全不感知情感状态——`HAPPY` 状态下的降级回复和 `SAD` 状态下的降级回复完全相同
3. `GatewayResult.output` 不含情感字段——下游无法感知 LLM 调用时的情感状态

**方案：** 为 `generate()` 增加情感上下文参数，并在降级回复中体现情感状态：

```python
def generate(
    self,
    *,
    request_id: str,
    model_profile_id: str,
    prompt_payload: dict[str, Any],
    ts_ms: int | None = None,
    # 🆕 情感上下文参数（v1.7 新增）
    emotion_context: dict[str, Any] | None = None,
    # emotion_context 结构：
    # {
    #   "emotion_tag": "HAPPY",
    #   "mood_state": "engaged",
    #   "bond_stage": "FRIEND",
    #   "emotional_intensity": 0.7,
    # }
) -> GatewayResult:
    # ...
    # 🆕 将情感上下文注入 prompt_payload
    if emotion_context:
        prompt_payload = dict(prompt_payload)
        prompt_payload["__emotion_context__"] = emotion_context
```

**情感感知降级回复：**

```python
def _degrade(self, ..., emotion_context: dict[str, Any] | None = None) -> GatewayResult:
    ec = emotion_context or {}
    emotion_tag = str(ec.get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str(ec.get("mood_state") or "steady").lower()

    # 🆕 根据情感状态选择降级回复文本
    DEGRADED_NARRATIVES = {
        "HAPPY": "嗯，现在有点小问题，不过没关系，我们稍后再试吧～",
        "SAD": "抱歉，现在遇到了一些问题……我会尽快恢复的。",
        "ANXIOUS": "啊，出了点状况，请稍等一下……",
        "NEUTRAL": "当前外部语义服务不可用，我先给出草案回复。",
    }
    narrative_text = DEGRADED_NARRATIVES.get(emotion_tag, DEGRADED_NARRATIVES["NEUTRAL"])

    fallback_payload = {
        "narrative": {"text": narrative_text},
        "intent_draft": {},
        "meta": {
            "draft_only": True,
            "degraded": True,
            "reason_code": reason_code,
            "stage": stage,
            # 🆕 情感字段
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
        },
    }
```

**预期效果：** LLM 网关从"情感盲"变为"情感感知"，LLM 调用时携带情感上下文，降级回复根据情感状态选择合适的文本，`GatewayResult` 输出包含情感字段供下游感知。

---

### 🟡 深化优化45（P1）：`GraphExecutor._invoke_handler()` 的 `context` 参数不携带情感状态——图节点执行时情感盲

**目标文件：** `host_brain/integrations/graph_executor.py`

**精读发现：**

`_invoke_handler()` 的 `context` 参数：

```python
def _invoke_handler(self, *, handler_id: str, node_id: str, state: GraphState) -> dict[str, Any]:
    handler = self._handlers.get(handler_id)
    if handler is None:
        raise KeyError(f"handler missing for node={node_id}, handler_id={handler_id}")
    return handler(dict(state.get("payload") or {}), {
        "request_id": str(state.get("request_id") or ""),
        "node_id": node_id,
        "graph_id": str(state.get("graph_id") or ""),
        "timeout_ms": int(state.get("timeout_ms") or self._default_timeout_ms)
        # ↑ context 里没有情感字段！
    })
```

`_invoke_model_node()` 的 `prompt_payload`：

```python
result = gateway.generate(
    request_id=str(state.get("request_id") or ""),
    model_profile_id=...,
    prompt_payload={
        "node_id": node_id,
        "model_node_id": model_node_id,
        "task": str(binding.get("task") or node_id),
        "input": dict(state.get("payload") or {}),
        "model": {...},
        # ↑ prompt_payload 里没有情感字段！
    }
)
```

`GraphState` 的字段定义（来自 `graph_contract.py`）：

```python
class GraphState(TypedDict, total=False):
    request_id: str
    graph_id: str
    model_profile_id: str
    pending_node_id: str
    node_bindings: dict[str, Any]
    model_nodes: dict[str, Any]
    dependency_statuses: dict[str, Any]
    edge_map: dict[str, Any]
    payload: dict[str, Any]
    trace: list[dict[str, Any]]
    model_calls: list[dict[str, Any]]
    status: str
    reason_code: str | None
    detail: str
    timeout_ms: int
    # ↑ 没有情感字段！
```

这意味着：
1. 图节点执行时 handler 的 context 不含情感字段——图节点 handler 无法根据情感状态调整执行行为
2. 图节点调用 LLM 时 `prompt_payload` 不含情感字段——即使优化44实施了，图执行器也无法传递情感上下文
3. `GraphState` 不含情感字段——情感信息无法在图执行的多个节点之间传递

**方案：** 为 `GraphState` 增加情感字段，并在 handler context 和 prompt_payload 中传递：

```python
# 🆕 GraphState 增加情感字段（v1.7 新增）
class GraphState(TypedDict, total=False):
    # ... 原有字段 ...
    # 🆕 情感字段
    emotion_tag: str           # 当前情感标签
    mood_state: str            # 当前 mood_state
    bond_stage: str            # 当前 Bond 阶段
    emotional_intensity: float # 情感强度
```

**`_invoke_handler()` 传递情感字段：**

```python
def _invoke_handler(self, *, handler_id, node_id, state) -> dict[str, Any]:
    handler = self._handlers.get(handler_id)
    return handler(dict(state.get("payload") or {}), {
        "request_id": str(state.get("request_id") or ""),
        "node_id": node_id,
        "graph_id": str(state.get("graph_id") or ""),
        "timeout_ms": int(state.get("timeout_ms") or self._default_timeout_ms),
        # 🆕 情感字段
        "emotion_tag": str(state.get("emotion_tag") or "NEUTRAL"),
        "mood_state": str(state.get("mood_state") or "steady"),
        "bond_stage": str(state.get("bond_stage") or "STRANGER"),
        "emotional_intensity": float(state.get("emotional_intensity") or 0.5),
    })
```

**预期效果：** 图执行器从"情感盲"变为"情感感知"，图节点 handler 可以根据情感状态调整执行行为，LLM 调用时携带情感上下文，情感信息可以在图执行的多个节点之间传递。

---

### 🟢 深化优化46（P1）：`MetricsCollector.collect()` 不包含情感维度指标——观测层情感盲

**目标文件：** `host_brain/obs/metrics.py`

**精读发现：**

`collect()` 的返回结构：

```python
payload: dict[str, Any] = {
    "request_id": request_id,
    "ts_ms": collected_ts,
    "obs_mode": mode.value,
    "governor_decisions": governor_stats,
    "retrieval_latency": retrieval_stats,
    "hard_snapshot_staleness_ms": snapshot_staleness,
    "recent_chains": recent_chains,
    "latency_baseline": latency_baseline,
    "debug_trace": None,
    # ↑ 没有情感维度指标！
}
```

`_query_governor_stats()` 的统计：

```python
return {
    "total": total,
    "approved": approved,
    "rejected": rejected,
    "degraded": degraded,
    "pseudo_exec_blocked": pseudo_exec_blocked,
    # ↑ 没有情感相关统计！
}
```

`_query_recent_chains()` 的链路信息：

```python
chain: dict[str, Any] = {
    "request_id": rid,
    "intent_id": row.get("intent_id"),
    "ts_ms": row.get("ts_ms"),
    "decision": None,
    "reason_code": None,
    "skill_name": None,
    # ↑ 没有情感字段！
}
```

这意味着：
1. `/obs/metrics` 端点不包含情感维度指标——运维人员无法通过监控面板感知 AI 的情感状态变化趋势
2. `_query_governor_stats()` 不统计情感相关的 Governor 决策——无法分析"有多少次因情感保护门控（R8）被拒绝"
3. `_query_recent_chains()` 不含情感字段——无法在链路追踪中看到每次请求时的情感状态

**方案：** 为 `MetricsCollector` 增加情感维度指标：

```python
def collect(self, ...) -> dict[str, Any]:
    # ...
    # 🆕 情感维度指标（v1.7 新增）
    emotion_stats = self._query_emotion_stats()

    payload: dict[str, Any] = {
        # ... 原有字段 ...
        # 🆕 情感维度指标
        "emotion_stats": emotion_stats,
    }
    return payload

def _query_emotion_stats(self) -> dict[str, Any]:
    """统计情感维度指标：情感分布、情感保护门控触发次数、情感变化趋势。"""
    # 从 governor_log 统计情感保护门控触发次数
    emotion_gate_blocked = 0
    try:
        for r in self.ledger.governor_log:
            if r.get("reason_code") == "EMOTION_CRITICAL_THROTTLE":
                emotion_gate_blocked += 1
    except Exception:
        pass

    # 从 skill_exec_log 统计情感标签分布
    emotion_distribution: dict[str, int] = {}
    try:
        for r in self.ledger.skill_exec_log:
            emotion_tag = str(r.get("emotion_tag") or "UNKNOWN").upper()
            emotion_distribution[emotion_tag] = emotion_distribution.get(emotion_tag, 0) + 1
    except Exception:
        pass

    return {
        "emotion_gate_blocked_count": emotion_gate_blocked,
        "emotion_distribution": emotion_distribution,
        "emotion_data_available": bool(emotion_distribution),
    }
```

**`_query_recent_chains()` 增加情感字段：**

```python
chain: dict[str, Any] = {
    "request_id": rid,
    "intent_id": row.get("intent_id"),
    "ts_ms": row.get("ts_ms"),
    "decision": None,
    "reason_code": None,
    "skill_name": None,
    # 🆕 情感字段
    "emotion_tag": row.get("emotion_tag"),
    "mood_state": row.get("mood_state"),
}
```

**预期效果：** 观测层从"情感盲"变为"情感感知"，运维人员可以通过监控面板感知 AI 的情感状态变化趋势，分析情感保护门控触发频率，在链路追踪中看到每次请求时的情感状态。

---

### 🔵 深化优化47（P1）：`SoVITSModelManager.synthesize()` 的 `emotion_intensity` 参数不传递给底层 SDK——TTS 情感强度断路

**目标文件：** `edge_client/actuators/tts_engine.py`

**精读发现：**

`synthesize()` 接收 `emotion_intensity` 参数：

```python
def synthesize(
    self,
    text: str,
    *,
    language: str | None = None,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,   # ← 接收了情感强度参数
    voice_profile: str | None = None,
) -> TtsSynthResult:
    # M26-I：emotion_intensity 范围截断
    if emotion_intensity is not None:
        emotion_intensity = max(0.0, min(1.0, float(emotion_intensity)))
```

但 `_synthesize_audio()` 调用时：

```python
audio_data, sr = _synthesize_audio(
    model=self._model,
    text=text.strip(),
    language=language or self._config.language,
    emotion_tag=emotion_tag,
    emotion_intensity=emotion_intensity,   # ← 传递了 emotion_intensity
    timeout_s=self._config.synth_timeout_s,
)
```

`_synthesize_audio()` 内部：

```python
def _synthesize_audio(
    *,
    model: Any,
    text: str,
    language: str,
    emotion_tag: str | None,
    emotion_intensity: float | None = None,   # ← 接收了 emotion_intensity
    timeout_s: float,
) -> tuple[Any, int]:
    # ...
    result = model.synthesize(
        text=text,
        language=language,
        emotion=emotion_tag,
        # ↑ emotion_intensity 没有传递给 model.synthesize()！
    )
```

`_ModernSoVITSAdapter.synthesize()` 的参数：

```python
def synthesize(
    self,
    *,
    text: str,
    language: str,
    emotion: str | None = None,
    # ↑ 没有 emotion_intensity 参数！
) -> dict[str, Any]:
```

这意味着：
1. `emotion_intensity` 参数在 `SoVITSModelManager.synthesize()` 中被接收，但在 `_synthesize_audio()` 中没有传递给 `model.synthesize()`
2. `_ModernSoVITSAdapter.synthesize()` 没有 `emotion_intensity` 参数，即使传递了也无法使用
3. 这是一个**参数传递断路**——`emotion_intensity` 在整个 TTS 链路中被"吞掉"了，对实际语音合成没有任何影响

**方案：** 修复 `emotion_intensity` 的传递链路：

```python
# 🆕 _synthesize_audio() 传递 emotion_intensity（v1.7 新增）
def _synthesize_audio(
    *,
    model: Any,
    text: str,
    language: str,
    emotion_tag: str | None,
    emotion_intensity: float | None = None,
    timeout_s: float,
) -> tuple[Any, int]:
    result = model.synthesize(
        text=text,
        language=language,
        emotion=emotion_tag,
        emotion_intensity=emotion_intensity,  # 🆕 传递 emotion_intensity
    )
```

**`_ModernSoVITSAdapter.synthesize()` 增加 `emotion_intensity` 参数：**

```python
def synthesize(
    self,
    *,
    text: str,
    language: str,
    emotion: str | None = None,
    emotion_intensity: float | None = None,  # 🆕 新增参数
) -> dict[str, Any]:
    # ...
    inputs = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": self._ref_audio_path,
        # ...
        # 🆕 情感强度影响 temperature（语气幅度）
        # emotion_intensity=1.0 → temperature=1.0（最大语气幅度）
        # emotion_intensity=0.0 → temperature=0.3（最小语气幅度）
        # emotion_intensity=None → temperature=0.7（默认）
        "temperature": _map_intensity_to_temperature(emotion_intensity),
    }

def _map_intensity_to_temperature(intensity: float | None) -> float:
    """将情感强度映射到 TTS temperature（语气幅度）。"""
    if intensity is None:
        return 0.7  # 默认
    clamped = max(0.0, min(1.0, float(intensity)))
    # 线性映射：[0.0, 1.0] → [0.3, 1.0]
    return round(0.3 + clamped * 0.7, 3)
```

**预期效果：** TTS 情感强度从"参数断路"变为"全链路传递"，`emotion_intensity=1.0` 时语音合成使用最大语气幅度，`emotion_intensity=0.0` 时使用最小语气幅度，实现情感强度对语音输出的实际影响。

---

### 🟣 深化优化48（P2）：`ASRTranscriptEvent` 不含用户情感推断字段——ASR 感知层情感盲

**目标文件：** `edge_client/senses/asr.py`

**精读发现：**

`ASRTranscriptEvent` 的字段定义：

```python
@dataclass(frozen=True)
class ASRTranscriptEvent:
    event_id: str
    session_id: str
    segment_id: str
    text: str
    confidence: float
    ts_start_ms: int
    ts_end_ms: int
    is_final: bool
    alternates: tuple[str, ...]
    evidence_ref: str
    low_confidence: bool
    reason_code: str | None
    # ↑ 没有用户情感推断字段！
```

`build_asr_transcript_event()` 的工厂函数：

```python
def build_asr_transcript_event(
    *,
    session_id: str,
    segment_id: str,
    text: str,
    confidence: float,
    ts_start_ms: int,
    ts_end_ms: int,
    is_final: bool = True,
    alternates: list[str] | None = None,
    evidence_ref: str | None = None,
    event_id: str | None = None,
) -> ASRTranscriptEvent:
    # ↑ 没有用户情感推断参数！
```

这意味着：
1. ASR 转写事件不含用户情感推断字段——即使 faster-whisper 可以通过语音特征（语速、音调、停顿）推断用户情感，这些信息也无法传递给 Host
2. `ASRTranscriptEvent` 只有文本内容，没有语音特征信息（语速、音调变化、停顿时长）——Host 无法感知用户的情感状态
3. 优化42中提到的 `user_emotion_hint` 字段（在 `EventReportItem` 中）没有数据来源——因为 ASR 层根本不提取用户情感信息

**方案：** 为 `ASRTranscriptEvent` 增加用户情感推断字段：

```python
@dataclass(frozen=True)
class ASRTranscriptEvent:
    event_id: str
    session_id: str
    segment_id: str
    text: str
    confidence: float
    ts_start_ms: int
    ts_end_ms: int
    is_final: bool
    alternates: tuple[str, ...]
    evidence_ref: str
    low_confidence: bool
    reason_code: str | None
    # 🆕 用户情感推断字段（v1.7 新增）
    speech_rate_wpm: float | None = None      # 语速（词/分钟），高语速可能表示兴奋/焦虑
    pause_ratio: float | None = None          # 停顿比例（0.0~1.0），高停顿可能表示悲伤/犹豫
    user_emotion_hint: str | None = None      # 用户情感推断（"EXCITED"/"SAD"/"ANXIOUS" 等）
    user_emotion_confidence: float | None = None  # 情感推断置信度（0.0~1.0）
```

**`build_asr_transcript_event()` 增加用户情感推断参数：**

```python
def build_asr_transcript_event(
    *,
    session_id: str,
    segment_id: str,
    text: str,
    confidence: float,
    ts_start_ms: int,
    ts_end_ms: int,
    is_final: bool = True,
    alternates: list[str] | None = None,
    evidence_ref: str | None = None,
    event_id: str | None = None,
    # 🆕 用户情感推断参数
    speech_rate_wpm: float | None = None,
    pause_ratio: float | None = None,
    user_emotion_hint: str | None = None,
    user_emotion_confidence: float | None = None,
) -> ASRTranscriptEvent:
```

**语速/停顿自动计算：**

```python
def _estimate_speech_features(
    *,
    text: str,
    ts_start_ms: int,
    ts_end_ms: int,
) -> dict[str, float | None]:
    """从转写文本和时间戳估算语音特征。"""
    duration_s = max(0.001, (ts_end_ms - ts_start_ms) / 1000.0)
    word_count = len(text.split())
    speech_rate_wpm = round(word_count / duration_s * 60, 1) if word_count > 0 else None

    # 简单的停顿比例估算（基于文本中的标点符号密度）
    pause_chars = sum(1 for c in text if c in "，。！？…、,.")
    pause_ratio = round(pause_chars / max(1, len(text)), 3)

    return {
        "speech_rate_wpm": speech_rate_wpm,
        "pause_ratio": pause_ratio,
    }
```

**预期效果：** ASR 感知层从"情感盲"变为"情感感知"，ASR 转写事件携带用户情感推断信息，Host 可以感知用户的情感状态（通过语速、停顿等语音特征），为优化42中的 `user_emotion_hint` 字段提供数据来源，实现"用户情感→AI情感响应"的完整闭环。

---

## 二十七、第八轮优化的核心价值总结

第八轮优化聚焦于**基础设施情感盲区**——情感信息在"调度→LLM网关→图执行→观测→TTS→ASR"基础设施链路中的系统性盲区：

| 优化项 | 发现的"基础设施盲区" | 影响范围 | 修复方案 |
|--------|-----------------|---------|---------|
| **优化43** 🔴 | 调度任务触发不感知情感状态，critical 状态下高消耗任务仍被触发 | `scheduler.py` → 调度层 | 增加情感门控，critical 状态下暂缓 MEDIUM/HIGH 优先级任务 |
| **优化44** 🟠 | LLM 调用不携带情感上下文，降级回复不感知情感状态 | `llm_gateway.py` → LLM 网关 | 增加 emotion_context 参数，降级回复根据情感状态选择文本 |
| **优化45** 🟡 | 图节点执行 context 不含情感字段，GraphState 无情感字段 | `graph_executor.py` → 图执行 | GraphState 增加情感字段，handler context 传递情感状态 |
| **优化46** 🟢 | 观测指标不含情感维度，运维无法感知情感状态变化趋势 | `obs/metrics.py` → 观测层 | 增加 emotion_stats 指标，recent_chains 增加情感字段 |
| **优化47** 🔵 | TTS emotion_intensity 参数在链路中被"吞掉"，对语音合成无实际影响 | `tts_engine.py` → TTS 引擎 | 修复参数传递断路，emotion_intensity 映射到 TTS temperature |
| **优化48** 🟣 | ASR 转写事件不含用户情感推断，Host 无法感知用户情感状态 | `senses/asr.py` → ASR 感知 | 增加 speech_rate_wpm/pause_ratio/user_emotion_hint 字段 |

---

### 第八轮的特殊发现：**优化47 是一个"参数黑洞"**

优化47（TTS emotion_intensity 断路）是一个特殊的问题——`emotion_intensity` 参数在 `SoVITSModelManager.synthesize()` 中被接收，在 `_synthesize_audio()` 中被传递，但在 `_ModernSoVITSAdapter.synthesize()` 中被完全忽略，没有传递给底层 SDK。这是一个典型的"参数黑洞"——参数在整个调用链中被传递，但最终没有任何实际效果。

### 第八轮的另一个特殊发现：**优化48 是优化42的"数据来源"**

优化48（ASR 用户情感推断）与优化42（EventReportItem 的 `user_emotion_hint` 字段）形成了一个完整的闭环：优化42为 `EventReportItem` 增加了 `user_emotion_hint` 字段，但这个字段的数据来源是 ASR 层——如果 ASR 层不提取用户情感信息，`user_emotion_hint` 字段就永远是 `None`。优化48修复了这个数据来源问题。

---

## 二十八、完整优化全景图（v1.7，48项）

### 八轮优化的层次递进

```
第一轮（v1.0）：发现5个"缺失功能"       → 需要新增什么
第二轮（v1.1）：发现6个"深层问题"       → 现有机制哪里不够深
第三轮（v1.2）：发现6个"假功能空壳"     → 现有代码哪里看起来有但实际没用
第四轮（v1.3）：发现6个"架构断层"       → 功能之间的连接在哪里断裂
第五轮（v1.4）：发现6个"跨模块盲区"     → 情感信息在全链路中的系统性缺失
第六轮（v1.5）：发现6个"感知层断路"     → 情感信息在感知→决策→输出→观测中的断路
第七轮（v1.6）：发现6个"执行层真空"     → 情感信息在执行→反馈→边缘端链路中的真空
第八轮（v1.7）：发现6个"基础设施盲区"   → 情感信息在调度→LLM→图执行→观测→TTS→ASR中的盲区
```

### 按优先级分类（48项全景）

| 优先级 | 优化项 | 核心问题 |
|--------|--------|---------|
| **P0（必须修复）** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27, OPT31, OPT32, OPT37, OPT38, OPT43 | 架构断层 + 跨模块盲区 + 感知层断路 + 执行层真空 + 调度层情感盲 |
| **P1（重要优化）** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29, OPT33, OPT34, OPT35, OPT39, OPT40, OPT41, OPT44, OPT45, OPT46, OPT47, OPT48 | 情感内核 + 关系层 + 记忆层 + 感知层 + 执行层 + 基础设施层 |
| **P2（体验增强）** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30, OPT36, OPT42 | 输出层 + 细节打磨 + 边缘端 |

### 情感信息全链路覆盖图（v1.7 最终版）

```
用户语音输入
    ↓
[ASR 感知层] edge_client/senses/asr.py
    OPT48: ASRTranscriptEvent 用户情感推断字段（新增）← 数据来源
    ↓
[边缘端事件上报] edge_client/rpc/event_reporter.py
    OPT42: EventReportItem 情感字段 + 跨端枚举统一（第七轮）
    ↓
[视觉感知层] vision/camera_event.py
    OPT35: 视觉识别 → 情感触发（第六轮）
    ↓
[调度层] scheduler/scheduler.py
    OPT43: 情感门控 + system_intent 携带情感状态（新增）
    ↓
[召回管线层] retrieval/pipeline.py
    OPT41: emotion_context 参数贯通（第七轮）
    ↓
[召回层] retrieval/recall_fusion.py
    OPT31: 情感维度路由（第六轮）
    OPT36: 情感强度加权融合（第六轮）
    ↓
[重排序层] retrieval/reranker.py
    OPT40: 情感偏好重排（第七轮）
    ↓
[决策层] governor/decision.py
    OPT32: R8 情感保护门控（第六轮）
    ↓
[情感内核] core/metabolism.py
    OPT6/7/8: EmotionVector 深化（第二轮）
    ↓
[关系层] bond/
    OPT26: 情感加权 Bond 积分（第五轮）
    OPT27: Bond Summary 修复（第五轮）
    ↓
[记忆层] memory/
    OPT25/28/29: 情感记忆标注/保存/蒸馏（第五轮）
    ↓
[输出层] core/qa.py + core/context_pack.py
    OPT19/20/22: 架构断层修复（第四轮）
    ↓
[LLM 网关层] integrations/llm_gateway.py
    OPT44: emotion_context 注入 prompt_payload（新增）
    ↓
[图执行层] integrations/graph_executor.py
    OPT45: GraphState 情感字段 + handler context 传递（新增）
    ↓
[工具注册层] mcp/tool_registry.py
    OPT39: ToolMeta 情感适用性字段（第七轮）
    ↓
[执行层] skills/loader.py + tools/broker.py
    OPT37: 技能执行 context 情感字段（第七轮）
    OPT38: 工具执行 context 情感字段（第七轮）
    ↓
[通道层] channels/vts.py
    OPT34: VTS 表情映射扩展（第六轮）
    ↓
[TTS 引擎层] edge_client/actuators/tts_engine.py
    OPT47: emotion_intensity 参数黑洞修复（新增）
    ↓
[观测层] observability/cross_timeline.py + obs/metrics.py
    OPT33: 情感事件纳入时间线（第六轮）
    OPT46: emotion_stats 指标（新增）
    ↓
用户感知到的情感输出（语音 + 表情 + 文字）
```

---

## 二十三、第七轮深化优化：执行层情感真空

> **v1.6 新增**（2026-03-05）。本轮基于对 `skills/loader.py`、`tools/broker.py`、`mcp/tool_registry.py`、`retrieval/reranker.py`、`retrieval/pipeline.py`、`edge_client/actuators/vts_expression_map.py`、`edge_client/rpc/event_reporter.py` 的完整逐行精读，发现了前六轮完全未触及的 **6 个执行层情感真空**——这些问题分布在技能执行层、工具代理层、MCP注册层、重排序层、召回管线层、边缘端表情映射层，是情感信息在"执行→反馈→边缘端"链路中的系统性真空。

---

### 🔴 深化优化37（P0）：`SkillLoader.execute()` 的 `context` 参数不传递情感状态——技能执行时情感盲

**目标文件：** `host_brain/skills/loader.py`

**精读发现：**

`execute()` 的 `context` 参数：

```python
def execute(
    self,
    *,
    intent: dict[str, Any],
    governor_output: dict[str, Any],
    context: dict[str, Any] | None = None,   # ← 自由格式 dict，没有情感字段约定
    now_ts_ms: int | None = None,
) -> SkillExecutionResult:
```

`handler_result = meta.handler(args, context)` 调用时：

```python
try:
    handler_result = meta.handler(args, context)
    # ↑ context 传给技能 handler，但 context 里没有情感字段！
```

`_log_and_return()` 写入审计的 row：

```python
row = {
    "request_id": request_id or None,
    "thread_id": thread_id,
    "turn_id": turn_id,
    "intent_id": intent_id,
    "skill_name": skill_name,
    "approval_token_ref": approval_token_ref,
    "confirm_token_ref": confirm_token_ref,
    "result_status": result_status,
    "result_code": result_code,
    "result_digest": result_digest,
    "evidence_ref": evidence_ref,
    "manifest_ref": manifest_ref,
    "undo_token_ref": undo_token_ref,
    "ts_ms": ts_ms,
    # ↑ 没有 emotion_tag、mood_state 字段！
}
```

这意味着：
1. 技能执行时完全不感知情感状态——`EXCITED` 状态下执行的技能和 `SAD` 状态下执行的技能得到相同的 context
2. 技能 handler 无法根据情感状态调整执行行为（例如：`EXCITED` 状态下的音乐播放技能应该选择欢快的歌曲）
3. 技能执行审计日志不含情感字段，无法事后分析"哪些情感状态下技能执行成功率更高"

**方案：** 为 `context` 增加情感字段约定，并在审计日志中记录情感状态：

```python
# 🆕 技能执行 context 情感字段约定（v1.6 新增）
SKILL_CONTEXT_EMOTION_FIELDS = {
    "emotion_tag": str,        # AI 当前情感标签（"NEUTRAL"/"HAPPY"/"SAD" 等）
    "mood_state": str,         # AI 当前情感状态（"engaged"/"steady"/"calm"/"tired"/"critical"）
    "bond_stage": str,         # 当前 Bond 阶段（"STRANGER"/"FRIEND" 等）
    "emotional_intensity": float,  # 情感强度（0.0~1.0）
}

def execute(
    self,
    *,
    intent: dict[str, Any],
    governor_output: dict[str, Any],
    context: dict[str, Any] | None = None,
    now_ts_ms: int | None = None,
) -> SkillExecutionResult:
    # ...
    # 🆕 从 context 中提取情感字段，用于审计日志
    emotion_tag = str((context or {}).get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str((context or {}).get("mood_state") or "steady").lower()
    bond_stage = str((context or {}).get("bond_stage") or "STRANGER").upper()
```

**审计日志增加情感字段：**

```python
row = {
    # ... 原有字段 ...
    # 🆕 情感字段
    "emotion_tag": emotion_tag,
    "mood_state": mood_state,
    "bond_stage": bond_stage,
}
```

**预期效果：** 技能执行从"情感盲"变为"情感感知"，技能 handler 可以根据情感状态调整执行行为，审计日志可以分析情感状态与技能执行结果的相关性。

---

### 🟠 深化优化38（P0）：`ToolBroker.execute()` 的 `context` 参数不传递情感状态——工具执行时情感盲

**目标文件：** `host_brain/tools/broker.py`

**精读发现：**

`execute()` 的 `context` 参数：

```python
def execute(
    self,
    *,
    request_id: str | None,
    intent_id: str,
    session_id: str,
    seq: int,
    tool_id: str,
    args: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,   # ← 自由格式 dict，没有情感字段约定
    approval_token_ref: str | None = None,
    confirm_token_ref: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    ts_ms: int | None = None,
) -> ToolExecutionResult:
```

`output = handler(safe_args, context)` 调用时：

```python
try:
    output = handler(safe_args, context)
    # ↑ context 传给工具 handler，但 context 里没有情感字段！
```

`_log_and_return()` 写入审计的 row：

```python
row = {
    "request_id": request_id,
    "thread_id": thread_id,
    "turn_id": turn_id,
    "intent_id": intent_id,
    "skill_name": tool_id,
    "approval_token_ref": approval_token_ref,
    "confirm_token_ref": confirm_token_ref,
    "result_status": status,
    "result_code": result_code,
    "result_digest": result_digest,
    "evidence_ref": evidence_ref,
    "manifest_ref": None,
    "undo_token_ref": None,
    "ts_ms": ts_ms,
    # ↑ 没有 emotion_tag、mood_state 字段！
}
```

这意味着：
1. 工具执行时完全不感知情感状态——`HAPPY` 状态下调用的工具和 `SAD` 状态下调用的工具得到相同的 context
2. 工具 handler 无法根据情感状态调整输出（例如：`SAD` 状态下的天气查询工具应该附加温暖的语气）
3. 工具执行审计日志不含情感字段，无法分析情感状态与工具调用模式的相关性

**方案：** 与优化37一致，为 `context` 增加情感字段约定，并在审计日志中记录情感状态：

```python
# 🆕 工具执行 context 情感字段约定（v1.6 新增）
TOOL_CONTEXT_EMOTION_FIELDS = {
    "emotion_tag": str,
    "mood_state": str,
    "bond_stage": str,
    "emotional_intensity": float,
}

def execute(self, ...) -> ToolExecutionResult:
    # ...
    # 🆕 从 context 中提取情感字段
    emotion_tag = str((context or {}).get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str((context or {}).get("mood_state") or "steady").lower()
```

**预期效果：** 工具执行从"情感盲"变为"情感感知"，工具 handler 可以根据情感状态调整输出风格，审计日志可以分析情感状态与工具调用模式的相关性。

---

### 🟡 深化优化39（P1）：`ToolMeta` 没有情感适用性字段——MCP 工具注册不感知情感场景

**目标文件：** `host_brain/mcp/tool_registry.py`

**精读发现：**

`ToolMeta` 的字段定义：

```python
@dataclass
class ToolMeta:
    provider_id: str
    tool_id: str
    name: str
    description: str
    access_level: str          # PUBLIC | INTERNAL | RESTRICTED
    risk_level: str            # LOW | MEDIUM | HIGH
    reversible: bool
    version: str = "1.0.0"
    schema_hash: str = ""
    domain_tags: list[str] = field(default_factory=list)
    # ↑ 没有情感适用性字段！
```

`VALID_ACCESS_LEVELS` 和 `VALID_RISK_LEVELS` 的定义：

```python
VALID_ACCESS_LEVELS = frozenset({"PUBLIC", "INTERNAL", "RESTRICTED"})
VALID_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
# ↑ 没有情感场景约束！
```

这意味着：
1. MCP 工具注册时没有情感适用性约束——`HIGH` 风险工具在 `critical` 情感状态下也可以被调用
2. 工具的 `domain_tags` 是自由字符串列表，没有情感场景标签的约定（如 `emotion:happy_only`、`mood:engaged_required`）
3. `context_pack.py` 的 `tool_meta_pack` 槽位注入工具元数据时，LLM 无法知道哪些工具适合当前情感状态

**方案：** 为 `ToolMeta` 增加情感适用性字段：

```python
# 🆕 情感适用性约束（v1.6 新增）
VALID_EMOTION_REQUIREMENTS = frozenset({
    "NONE",           # 无情感要求（默认）
    "POSITIVE_ONLY",  # 只在正面情感（HAPPY/EXCITED/CALM）下可用
    "CALM_REQUIRED",  # 需要平静状态（CALM/NEUTRAL）
    "ENGAGED_ONLY",   # 只在 engaged mood_state 下可用
    "ANY",            # 任何情感状态均可
})

@dataclass
class ToolMeta:
    provider_id: str
    tool_id: str
    name: str
    description: str
    access_level: str
    risk_level: str
    reversible: bool
    version: str = "1.0.0"
    schema_hash: str = ""
    domain_tags: list[str] = field(default_factory=list)
    # 🆕 情感适用性字段
    emotion_requirement: str = "NONE"    # 情感场景约束
    min_bond_stage: str = "STRANGER"     # 最低 Bond 阶段要求
    energy_cost: str = "LOW"             # 执行能量消耗（LOW/MEDIUM/HIGH）
```

**在 `context_pack.py` 的 `tool_meta_pack` 中注入情感适用性信息：**

```python
# 🆕 tool_meta_pack 增加情感适用性字段（v1.6 新增）
tool_meta_item = {
    "tool_id": meta.tool_id,
    "name": meta.name,
    "description": meta.description,
    "risk_level": meta.risk_level,
    "domain_tags": meta.domain_tags,
    # 🆕 情感适用性
    "emotion_requirement": meta.emotion_requirement,
    "min_bond_stage": meta.min_bond_stage,
    "energy_cost": meta.energy_cost,
    # 🆕 当前情感状态下是否适用（由系统计算，不暴露原始数值）
    "available_in_current_mood": _check_emotion_availability(meta, current_mood_state),
}
```

**预期效果：** MCP 工具注册从"情感盲"变为"情感感知"，LLM 在选择工具时可以感知哪些工具适合当前情感状态，避免在 `critical` 状态下调用高能量消耗的工具。

---

### 🟢 深化优化40（P1）：`RerankerServiceContract.rerank()` 不感知情感状态——重排序时情感盲

**目标文件：** `host_brain/retrieval/reranker.py`

**精读发现：**

`rerank()` 的参数：

```python
def rerank(
    self,
    *,
    request_id: str,
    query: str,
    candidates: list[dict[str, Any]] | Any,
    intent_id: str | None = None,
    top_k: int | None = None,
    timeout_ms: int | None = None,
    ts_ms: int | None = None,
    # ↑ 完全没有情感维度参数！
) -> RerankerResult:
```

`_default_rerank_provider()` 的评分逻辑：

```python
def _default_rerank_provider(
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[float], str]:
    query_tokens = set(_tokenize(query))
    denominator = max(1, len(query_tokens))
    scores: list[float] = []
    for idx, candidate in enumerate(candidates):
        snippet = str(candidate.get("snippet") or "")
        doc_id = str(candidate.get("doc_id") or "")
        candidate_tokens = set(_tokenize(f"{doc_id} {snippet}"))
        overlap = len(query_tokens.intersection(candidate_tokens))
        lexical_score = float(overlap) / float(denominator)
        recall_bias = _fallback_score_by_rank(idx, len(candidates)) * 0.01
        scores.append(round(min(1.0, lexical_score + recall_bias), 6))
    return scores, RERANK_MODEL_ID
    # ↑ 纯词汇重叠评分，完全不感知情感！
```

`_build_topk()` 的输出结构：

```python
{
    "doc_id": ...,
    "source_ref": ...,
    "snippet": ...,
    "score": ...,
    "confidence": ...,
    "metadata": ...,
    # ↑ 没有情感相关字段！
}
```

这意味着：
1. 重排序时完全不感知情感状态——当用户说"我很难过，你还记得我们开心的时候吗"，重排序器无法优先返回 `emotion_tag=HAPPY` 的记忆
2. `_default_rerank_provider()` 的词汇重叠评分不感知情感语义——"开心"和"快乐"的语义相似性无法被识别
3. 重排序输出的 `topk` 不含情感字段，下游的 `evidence_pack` 无法感知情感维度

**方案：** 为 `rerank()` 增加情感感知参数，并在 `_build_topk()` 输出中保留情感字段：

```python
def rerank(
    self,
    *,
    request_id: str,
    query: str,
    candidates: list[dict[str, Any]] | Any,
    intent_id: str | None = None,
    top_k: int | None = None,
    timeout_ms: int | None = None,
    ts_ms: int | None = None,
    # 🆕 情感感知参数
    current_emotion_tag: str | None = None,   # 当前情感标签，用于情感偏好重排
    emotion_preference_weight: float = 0.0,   # 情感偏好加权（0.0~0.2）
) -> RerankerResult:
```

**情感偏好重排逻辑：**

```python
def _apply_emotion_preference(
    topk: list[dict[str, Any]],
    *,
    current_emotion_tag: str,
    emotion_preference_weight: float,
) -> list[dict[str, Any]]:
    """对 topk 结果应用情感偏好重排。

    同情感标签的记忆得到额外加分，使 AI 在特定情感状态下
    更倾向于召回同情感的记忆（情感共鸣效应）。
    """
    result = []
    for item in topk:
        metadata = item.get("metadata") or {}
        item_emotion = str(metadata.get("emotion_tag") or "NEUTRAL").upper()
        # 情感匹配加分
        emotion_match = (item_emotion == current_emotion_tag.upper())
        boost = emotion_preference_weight if emotion_match else 0.0
        new_score = round(min(1.0, float(item["score"]) + boost), 6)
        new_item = dict(item)
        new_item["score"] = new_score
        new_item["metadata"] = dict(metadata)
        new_item["metadata"]["emotion_preference_boosted"] = emotion_match
        result.append(new_item)
    # 重新排序
    return sorted(result, key=lambda r: (-float(r["score"]), str(r["doc_id"])))
```

**预期效果：** 重排序从"情感盲"变为"情感感知"，当 AI 处于 `HAPPY` 状态时，重排序会优先返回 `emotion_tag=HAPPY` 的记忆，实现情感共鸣效应。

---

### 🔵 深化优化41（P1）：`RetrievalPipelineContract.run()` 不传递情感上下文——召回管线情感断路

**目标文件：** `host_brain/retrieval/pipeline.py`

**精读发现：**

`run()` 的参数：

```python
def run(
    self,
    *,
    request_id: str,
    query: str,
    intent_id: str | None = None,
    top_k: int | None = None,
    ts_ms: int | None = None,
    # ↑ 完全没有情感维度参数！
) -> RetrievalPipelineResult:
```

`recall_service.recall()` 调用时：

```python
recall_result = self.recall_service.recall(
    request_id=request_id,
    query=normalized_query,
    query_embedding=embedding_vector,
    intent_id=intent_id,
    ts_ms=now_value,
    # ↑ 没有传递情感参数！即使 recall_service 支持情感过滤（优化31），也无法使用
)
```

`reranker_service.rerank()` 调用时：

```python
rerank_result = self.reranker_service.rerank(
    request_id=request_id,
    query=normalized_query,
    candidates=recall_candidates,
    intent_id=intent_id,
    top_k=top_k,
    ts_ms=now_value,
    # ↑ 没有传递情感参数！即使 reranker_service 支持情感偏好（优化40），也无法使用
)
```

这意味着：
1. 即使优化31（召回情感路由）和优化40（重排序情感偏好）都实施了，召回管线也无法使用这些功能——因为 `pipeline.run()` 不传递情感参数
2. 召回管线是情感信息的"最后一道关卡"，但它完全不感知情感状态
3. 这是一个**架构级断路**——优化31和优化40的成果被管线层完全屏蔽

**方案：** 为 `run()` 增加情感上下文参数，并传递给下游服务：

```python
def run(
    self,
    *,
    request_id: str,
    query: str,
    intent_id: str | None = None,
    top_k: int | None = None,
    ts_ms: int | None = None,
    # 🆕 情感上下文参数（v1.6 新增）
    emotion_context: dict[str, Any] | None = None,
    # emotion_context 结构：
    # {
    #   "emotion_tag": "HAPPY",          # 当前情感标签
    #   "mood_state": "engaged",          # 当前 mood_state
    #   "bond_stage": "FRIEND",           # 当前 Bond 阶段
    #   "emotion_filter": "HAPPY",        # 召回时的情感过滤（可选）
    #   "emotion_boost": 0.1,             # 情感匹配加分（可选）
    #   "emotion_preference_weight": 0.1, # 重排序情感偏好权重（可选）
    # }
) -> RetrievalPipelineResult:
    # ...
    ec = emotion_context or {}

    recall_result = self.recall_service.recall(
        request_id=request_id,
        query=normalized_query,
        query_embedding=embedding_vector,
        intent_id=intent_id,
        ts_ms=now_value,
        # 🆕 传递情感参数
        emotion_filter=ec.get("emotion_filter"),
        emotion_boost=float(ec.get("emotion_boost") or 0.0),
    )

    rerank_result = self.reranker_service.rerank(
        request_id=request_id,
        query=normalized_query,
        candidates=recall_candidates,
        intent_id=intent_id,
        top_k=top_k,
        ts_ms=now_value,
        # 🆕 传递情感参数
        current_emotion_tag=ec.get("emotion_tag"),
        emotion_preference_weight=float(ec.get("emotion_preference_weight") or 0.0),
    )
```

**预期效果：** 召回管线从"情感断路"变为"情感贯通"，优化31和优化40的成果可以通过管线层被实际使用，情感信息在召回→重排序→证据包的全链路中得以传递。

---

### 🟣 深化优化42（P2）：`EdgeEventReporter` 的 `EventReportItem` 不含情感字段——边缘端事件上报情感盲

**目标文件：** `edge_client/rpc/event_reporter.py`

**精读发现：**

`EventReportItem` 的字段定义：

```python
@dataclass(frozen=True)
class EventReportItem:
    event_type: str
    ts_ms: int
    request_id: str
    session_id: str
    source: str = "edge"
    payload: dict[str, Any] = field(default_factory=dict)
    direction: str = "EDGE_TO_HOST"
    seq: int | None = None
    status: str | None = None
    reason_code: str | None = None
    stage: str | None = None
    detail: str | None = None
    evidence_ref: str | None = None
    # ↑ 没有情感字段！
```

`validate_event()` 的必需字段检查：

```python
REQUIRED_EVENT_FIELDS = frozenset({"event_type", "ts_ms", "request_id", "session_id"})
# ↑ 没有情感字段！
```

这意味着：
1. 边缘端（Edge Client）上报到 Host 的事件不含情感字段——ASR 识别到用户哭泣的语气，这个情感信息无法通过事件上报传递给 Host
2. TTS 合成时的情感标签（`emotion_tag`）不在事件上报中——Host 无法知道边缘端实际播放了什么情感的语音
3. VTS 驱动时的表情 ID（`expression_id`）不在事件上报中——Host 无法知道边缘端实际显示了什么表情

**方案：** 为 `EventReportItem` 增加情感字段，并更新 `validate_event()` 支持情感字段解析：

```python
@dataclass(frozen=True)
class EventReportItem:
    event_type: str
    ts_ms: int
    request_id: str
    session_id: str
    source: str = "edge"
    payload: dict[str, Any] = field(default_factory=dict)
    direction: str = "EDGE_TO_HOST"
    seq: int | None = None
    status: str | None = None
    reason_code: str | None = None
    stage: str | None = None
    detail: str | None = None
    evidence_ref: str | None = None
    # 🆕 情感字段（v1.6 新增）
    emotion_tag: str | None = None          # 事件发生时的情感标签
    expression_id: str | None = None        # VTS 表情 ID（如 "exp_happy_01"）
    emotional_intensity: float | None = None  # 情感强度（0.0~1.0）
    user_emotion_hint: str | None = None    # 用户情感推断（来自 ASR 语气分析）
```

**`to_dict()` 增加情感字段：**

```python
def to_dict(self) -> dict[str, Any]:
    return {
        # ... 原有字段 ...
        # 🆕 情感字段
        "emotion_tag": self.emotion_tag,
        "expression_id": self.expression_id,
        "emotional_intensity": self.emotional_intensity,
        "user_emotion_hint": self.user_emotion_hint,
    }
```

**同时发现：`vts_expression_map.py` 的 `EmotionTag` 枚举与 `host_brain/channels/vts.py` 的 `EMOTION_TO_EXPRESSION_ID` 不同步：**

```python
# edge_client/actuators/vts_expression_map.py 的 EmotionTag（9种）
class EmotionTag(str, Enum):
    NEUTRAL   = "neutral"
    HAPPY     = "happy"
    SAD       = "sad"
    ANGRY     = "angry"
    SURPRISED = "surprised"
    FEARFUL   = "fearful"    # ← edge 有，host 没有
    DISGUSTED = "disgusted"  # ← edge 有，host 没有
    EXCITED   = "excited"
    CALM      = "calm"

# host_brain/channels/vts.py 的 EMOTION_TO_EXPRESSION_ID（6种，优化34前）
EMOTION_TO_EXPRESSION_ID = {
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING"
    # ↑ THINKING 在 edge 没有，FEARFUL/DISGUSTED 在 host 没有
}
```

这是一个**跨端枚举不同步**问题——Edge 和 Host 的情感标签枚举不一致，会导致跨端情感信息传递时出现映射错误。

**方案：** 建立统一的情感标签枚举规范，Edge 和 Host 共享同一套枚举定义：

```python
# 🆕 统一情感标签枚举（v1.6 新增，Edge 和 Host 共享）
UNIFIED_EMOTION_TAGS = frozenset({
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED",
    "FEARFUL", "DISGUSTED", "EXCITED", "CALM",
    "THINKING", "ANXIOUS", "CONFUSED", "EMBARRASSED",
    "GRATEFUL", "LONELY", "PROUD", "SCARED",
})
```

**预期效果：** 边缘端事件上报从"情感盲"变为"情感感知"，ASR 识别到的用户情感、TTS 播放的情感标签、VTS 显示的表情 ID 都可以通过事件上报传递给 Host，实现边缘端和 Host 的情感信息双向同步。同时修复跨端枚举不同步问题，确保情感信息在 Edge→Host 传递时不出现映射错误。

---

## 二十四、第七轮优化的核心价值总结

第七轮优化聚焦于**执行层情感真空**——情感信息在"执行→反馈→边缘端"链路中的系统性真空：

| 优化项 | 发现的"执行层真空" | 影响范围 | 修复方案 |
|--------|-----------------|---------|---------|
| **优化37** 🔴 | 技能执行 context 不含情感字段，技能 handler 情感盲 | `skills/loader.py` → 技能执行 | 为 context 增加情感字段约定，审计日志记录情感状态 |
| **优化38** 🟠 | 工具执行 context 不含情感字段，工具 handler 情感盲 | `tools/broker.py` → 工具执行 | 为 context 增加情感字段约定，审计日志记录情感状态 |
| **优化39** 🟡 | MCP 工具注册无情感适用性字段，工具选择不感知情感场景 | `mcp/tool_registry.py` → 工具注册 | 为 ToolMeta 增加 emotion_requirement/min_bond_stage/energy_cost 字段 |
| **优化40** 🟢 | 重排序不感知情感状态，情感共鸣效应无法实现 | `retrieval/reranker.py` → 重排序 | 增加情感偏好参数和情感共鸣重排逻辑 |
| **优化41** 🔵 | 召回管线不传递情感上下文，优化31/40的成果被屏蔽 | `retrieval/pipeline.py` → 召回管线 | 增加 emotion_context 参数，传递给 recall 和 rerank 服务 |
| **优化42** 🟣 | 边缘端事件上报不含情感字段，跨端情感信息传递断路 | `event_reporter.py` → 边缘端 | 为 EventReportItem 增加情感字段，修复跨端枚举不同步 |

---

### 第七轮的特殊发现：**优化41 是一个"成果屏蔽器"**

优化41（召回管线情感断路）是一个特殊的问题——它不是一个独立的缺陷，而是一个**屏蔽器**：即使优化31（召回情感路由）和优化40（重排序情感偏好）都已实施，只要 `pipeline.run()` 不传递情感参数，这两个优化的成果就永远无法被实际使用。这是一个典型的"最后一公里"问题。

### 第七轮的另一个特殊发现：**跨端枚举不同步**

在优化42中发现了一个跨端枚举不同步问题：`edge_client/actuators/vts_expression_map.py` 的 `EmotionTag` 枚举（9种）和 `host_brain/channels/vts.py` 的 `EMOTION_TO_EXPRESSION_ID`（6种）不一致，且两者都与 `metabolism.py` 的情感标签集合不同步。这是一个需要在整个项目层面统一解决的问题。

---

## 二十五、完整优化全景图（v1.6，42项）

### 七轮优化的层次递进

```
第一轮（v1.0）：发现5个"缺失功能"     → 需要新增什么
第二轮（v1.1）：发现6个"深层问题"     → 现有机制哪里不够深
第三轮（v1.2）：发现6个"假功能空壳"   → 现有代码哪里看起来有但实际没用
第四轮（v1.3）：发现6个"架构断层"     → 功能之间的连接在哪里断裂
第五轮（v1.4）：发现6个"跨模块盲区"   → 情感信息在全链路中的系统性缺失
第六轮（v1.5）：发现6个"感知层断路"   → 情感信息在感知→决策→输出→观测中的断路
第七轮（v1.6）：发现6个"执行层真空"   → 情感信息在执行→反馈→边缘端链路中的真空
```

### 按优先级分类（42项全景）

| 优先级 | 优化项 | 核心问题 |
|--------|--------|---------|
| **P0（必须修复）** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27, OPT31, OPT32, OPT37, OPT38 | 架构断层 + 跨模块盲区 + 感知层断路 + 执行层真空中的关键路径 |
| **P1（重要优化）** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29, OPT33, OPT34, OPT35, OPT39, OPT40, OPT41 | 情感内核 + 关系层 + 记忆层 + 感知层 + 执行层 |
| **P2（体验增强）** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30, OPT36, OPT42 | 输出层 + 细节打磨 + 边缘端 |

### 情感信息全链路覆盖图（v1.6 最终版）

```
用户输入
    ↓
[边缘端感知层] edge_client/
    OPT42: EventReportItem 情感字段 + 跨端枚举统一（新增）
    ↓
[视觉感知层] camera_event.py
    OPT35: 视觉识别 → 情感触发（第六轮）
    ↓
[召回管线层] retrieval/pipeline.py
    OPT41: emotion_context 参数贯通（新增）← 关键"最后一公里"
    ↓
[召回层] recall_fusion.py
    OPT31: 情感维度路由（第六轮）
    OPT36: 情感强度加权融合（第六轮）
    ↓
[重排序层] retrieval/reranker.py
    OPT40: 情感偏好重排（新增）
    ↓
[决策层] governor/decision.py
    OPT32: R8 情感保护门控（第六轮）
    ↓
[情感内核] metabolism.py
    OPT6/7/8: EmotionVector 深化（第二轮）
    ↓
[关系层] bond/
    OPT26: 情感加权 Bond 积分（第五轮）
    OPT27: Bond Summary 修复（第五轮）
    ↓
[记忆层] memory/
    OPT25/28/29: 情感记忆标注/保存/蒸馏（第五轮）
    ↓
[输出层] qa.py / context_pack.py
    OPT19/20/22: 架构断层修复（第四轮）
    ↓
[工具注册层] mcp/tool_registry.py
    OPT39: ToolMeta 情感适用性字段（新增）
    ↓
[执行层] skills/loader.py + tools/broker.py
    OPT37: 技能执行 context 情感字段（新增）
    OPT38: 工具执行 context 情感字段（新增）
    ↓
[通道层] channels/vts.py
    OPT34: VTS 表情映射扩展（第六轮）
    ↓
[观测层] observability/cross_timeline.py
    OPT33: 情感事件纳入时间线（第六轮）
    ↓
用户感知到的情感输出
```

---

## 二十、第六轮深化优化：感知层情感断路

> **v1.5 新增**（2026-03-05）。本轮基于对 `retrieval/recall_fusion.py`、`governor/decision.py`、`observability/cross_timeline.py`、`channels/vts.py`、`vision/camera_event.py` 的完整逐行精读，发现了前五轮完全未触及的 **6 个感知层情感断路**——这些问题分布在召回层、决策层、观测层、通道层、视觉层，是情感信息在"感知→决策→输出→观测"全链路中的系统性断路。

---

### 🔴 深化优化31（P0）：`RecallFusionServiceContract.recall()` 无情感维度路由——情感记忆和普通记忆混在同一个召回池

**目标文件：** `host_brain/retrieval/recall_fusion.py`

**精读发现：**

`recall()` 的参数：

```python
def recall(
    self,
    *,
    request_id: str,
    query: str,
    query_embedding: list[float] | tuple[float, ...] | Any,
    intent_id: str | None = None,
    ts_ms: int | None = None,
    # ↑ 完全没有情感维度参数！
) -> RecallFusionResult:
```

`RecallRouteConfig` 的字段：

```python
@dataclass(frozen=True)
class RecallRouteConfig:
    route_id: str
    collection: str      # ← 只有 collection 区分，没有情感过滤
    top_k: int
    score_weight: float
    node_selector: str
```

`_normalize_candidate()` 的 metadata 处理：

```python
merged_metadata["collection"] = route.collection
merged_metadata["route_id"] = route.route_id
merged_metadata["route_score_weight"] = float(route.score_weight)
merged_metadata["base_score"] = base_score
merged_metadata["matched_routes"] = [route.route_id]
merged_metadata["recall_rank"] = int(rank)
# ↑ 没有 emotion_tag 过滤！候选结果不含情感维度
```

这意味着：
1. 召回时无法区分"情感记忆"和"普通记忆"——`EXCITED` 状态下产生的记忆和 `NEUTRAL` 状态下的记忆得到相同的召回权重
2. 当用户说"我们开心时聊过什么"，召回系统无法优先返回 `emotion_tag=HAPPY` 的记忆
3. 优化25（`EpisodicRecord` 增加情感字段）的成果无法被召回层利用——情感字段写入了 Chroma metadata，但召回时没有情感过滤

**方案：** 为 `recall()` 增加情感过滤参数，并在候选结果中保留情感维度：

```python
def recall(
    self,
    *,
    request_id: str,
    query: str,
    query_embedding: list[float] | tuple[float, ...] | Any,
    intent_id: str | None = None,
    ts_ms: int | None = None,
    # 🆕 情感维度参数
    emotion_filter: str | None = None,       # 按情感标签过滤（如 "HAPPY"）
    bond_stage_filter: str | None = None,    # 按 Bond 阶段过滤（如 "FRIEND"）
    emotion_boost: float = 0.0,              # 情感匹配的额外加分（0.0~0.3）
) -> RecallFusionResult:
    # ...
    # 在候选融合后，应用情感过滤和加权
    if emotion_filter or bond_stage_filter or emotion_boost > 0:
        candidates = _apply_emotion_rerank(
            candidates=candidates,
            emotion_filter=emotion_filter,
            bond_stage_filter=bond_stage_filter,
            emotion_boost=emotion_boost,
        )
```

**情感重排序实现：**

```python
def _apply_emotion_rerank(
    *,
    candidates: list[dict[str, Any]],
    emotion_filter: str | None,
    bond_stage_filter: str | None,
    emotion_boost: float,
) -> list[dict[str, Any]]:
    """对召回候选进行情感维度重排序。"""
    result = []
    for cand in candidates:
        metadata = cand.get("metadata") or {}
        cand_emotion = str(metadata.get("emotion_tag") or "NEUTRAL").upper()
        cand_bond = str(metadata.get("bond_stage") or "STRANGER").upper()

        # 情感过滤：不匹配的候选降权（不直接过滤，避免召回为空）
        emotion_match = (emotion_filter is None or cand_emotion == emotion_filter.upper())
        bond_match = (bond_stage_filter is None or cand_bond == bond_stage_filter.upper())

        # 情感加权：匹配的候选加分
        boost = 0.0
        if emotion_match and emotion_filter is not None:
            boost += emotion_boost
        if bond_match and bond_stage_filter is not None:
            boost += emotion_boost * 0.5

        new_score = round(min(1.0, float(cand["score"]) + boost), 6)
        new_cand = dict(cand)
        new_cand["score"] = new_score
        new_cand["metadata"] = dict(metadata)
        new_cand["metadata"]["emotion_boosted"] = boost > 0
        new_cand["metadata"]["emotion_boost_value"] = boost
        result.append(new_cand)

    # 重新按分数排序
    return sorted(result, key=lambda r: (-float(r["score"]), str(r["doc_id"])))
```

**预期效果：** 召回系统从"情感盲"变为"情感感知"，当用户说"我们开心时聊过什么"，系统能优先召回 `emotion_tag=HAPPY` 的记忆，情感记忆的利用率大幅提升。

---

### 🟠 深化优化32（P0）：`IntentDecisionEngine.evaluate_intent()` 的 `governance_snapshot` 不含情感状态——决策层情感盲

**目标文件：** `host_brain/governor/decision.py`

**精读发现：**

`evaluate_intent()` 的 `governance_snapshot` 参数：

```python
def evaluate_intent(
    self,
    raw_intent: str | dict[str, Any],
    *,
    request_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    current_ms: int | None = None,
    proposer_node_id: str = "core_reasoner_primary",
    model_snapshot: str = "unknown_model",
    prompt_profile_id: str = "default_profile",
    tool_visibility_hash: str = "",
    governance_snapshot: dict[str, Any] | None = None,
    # ↑ governance_snapshot 是自由格式 dict，没有情感字段的约定
) -> dict[str, Any]:
```

`_build_gate_input()` 的实现：

```python
def _build_gate_input(
    governance_snapshot: dict[str, Any] | None,
    *,
    target_scope: str,
) -> dict[str, Any]:
    source = dict(governance_snapshot or {})
    state = _hard_state_from_snapshot(governance_snapshot)
    gates_input = build_governance_snapshot(state, overrides=source)
    # ↑ build_governance_snapshot 只处理 energy/safety/trust 等字段
    # 没有 mood_state、emotion_tag 等情感字段
    if "scope" not in source:
        gates_input["scope"] = str(target_scope).strip().lower() or "host"
    return gates_input
```

`evaluate_governor_gates()` 的 R1-R7 门控规则（来自 `governor/rules.py`）：

```python
# R1: energy_bucket 检查
# R2: safety_flag 检查
# R3: rate_limit 检查
# R4: trust 检查
# R5: scope 检查
# R6: allowlist 检查
# R7: airlock_blocked 检查
# ↑ 没有任何情感相关的门控规则！
```

这意味着：
1. 决策层完全不感知情感状态——`critical` 能量状态下的意图和 `engaged` 状态下的意图经过相同的门控规则
2. 没有"情感保护门控"——当 AI 处于 `critical` 状态时，应该拒绝高消耗的技能调用，但当前没有这个门控
3. 没有"情感一致性检查"——当 AI 声称自己很开心但 `mood_state=sad` 时，没有任何机制检测这个矛盾

**方案：** 为 `governance_snapshot` 增加情感字段约定，并在 `evaluate_governor_gates()` 中增加情感保护门控 R8：

```python
# 🆕 情感保护门控 R8：critical 状态下拒绝高消耗技能
EMOTION_GATE_R8 = "R8_EMOTION_PROTECTION"

# 高消耗技能列表（critical 状态下应拒绝）
HIGH_ENERGY_SKILLS = frozenset({
    "deep_analysis",
    "long_story_generation",
    "multi_step_reasoning",
    "image_generation",
    "code_execution",
})

def _evaluate_emotion_gate(gates_input: dict[str, Any]) -> dict[str, Any]:
    """R8：情感保护门控——critical 状态下拒绝高消耗技能。"""
    mood_state = str(gates_input.get("mood_state") or "steady").lower()
    target_scope = str(gates_input.get("scope") or "host").lower()

    if mood_state == "critical":
        # critical 状态下，高消耗技能被拒绝
        if any(skill in target_scope for skill in HIGH_ENERGY_SKILLS):
            return {
                "gate_id": EMOTION_GATE_R8,
                "hit": True,
                "reason_hint": "EMOTION_CRITICAL_SKILL_BLOCKED",
                "threshold_summary": {"mood_state": "critical", "blocked_scope": target_scope},
                "snapshot_summary": {"mood_state": mood_state},
            }

    return {
        "gate_id": EMOTION_GATE_R8,
        "hit": False,
        "reason_hint": None,
        "threshold_summary": {"mood_state": mood_state},
        "snapshot_summary": {"mood_state": mood_state},
    }
```

**`governance_snapshot` 情感字段约定：**

```python
# 🆕 governance_snapshot 标准情感字段（v1.5 新增）
GOVERNANCE_SNAPSHOT_EMOTION_FIELDS = {
    "mood_state": str,      # AI 当前情感状态（"engaged"/"steady"/"calm"/"tired"/"critical"）
    "emotion_tag": str,     # AI 当前情感标签（"NEUTRAL"/"HAPPY"/"SAD" 等）
    "bond_stage": str,      # 当前 Bond 阶段（"STRANGER"/"FRIEND" 等）
    "emotional_intensity": float,  # 情感强度（0.0~1.0）
}
```

**预期效果：** 决策层从"情感盲"变为"情感感知"，`critical` 状态下的高消耗技能调用会被 R8 门控拒绝，保护 AI 的情感状态不被过度消耗。

---

### 🟡 深化优化33（P1）：`CrossTimelineAggregator` 的时间线不含情感事件——情感变化在时间线中不可见

**目标文件：** `host_brain/observability/cross_timeline.py`

**精读发现：**

`VALID_EDGE_EVENT_TYPES` 的定义：

```python
VALID_EDGE_EVENT_TYPES = frozenset({
    EDGE_EVENT_ASR_CHUNK,       # "ASR_CHUNK"
    EDGE_EVENT_SPEAK,           # "SPEAK"
    EDGE_EVENT_BARGE_IN,        # "BARGE_IN"
    EDGE_EVENT_RESOURCE_GATE,   # "VOICE_RESOURCE_GATE"
    EDGE_EVENT_FALLBACK_LLM,    # "FALLBACK_LLM"
    EDGE_EVENT_TTS_SYNTH,       # "TTS_SYNTH"
    EDGE_EVENT_VTS_EXPRESSION,  # "VTS_EXPRESSION"
    EDGE_EVENT_VTS_TALK_GATE,   # "VTS_TALK_GATE"
    # ↑ 没有情感事件类型！
})
```

`HOST_LOG_TABLES` 的定义：

```python
HOST_LOG_TABLES = (
    "intent_log",
    "governor_log",
    "skill_exec_log",
    "model_control_log",
    "channel_io_log",
    "avatar_action_log",
    "voice_event_log",
    "retrieval_event_log",
    "vision_event_log",
    "service_health_log",
    "error_log",
    "config_snapshot_log",
    # ↑ 没有 emotion_state_log、bond_event_log！
)
```

这意味着：
1. 跨端时间线中完全没有情感事件——无法看到"AI 在什么时刻情感发生了变化"
2. 无法做"情感时间线分析"——无法回答"这次对话中 AI 的情感是如何变化的"
3. Bond 事件（`bond_dynamics.py` 写入 Neo4j）不在时间线中——无法看到"Bond 阶段在什么时刻发生了变化"
4. 情感状态变化（`metabolism.py` 的 `HardState` 更新）不在时间线中——情感变化对观测层完全不可见

**方案：** 增加情感事件类型，并将情感相关的审计表纳入时间线：

```python
# 🆕 情感事件类型（v1.5 新增）
EDGE_EVENT_EMOTION_CHANGE = "EMOTION_CHANGE"
EDGE_EVENT_BOND_UPDATE = "BOND_UPDATE"
EDGE_EVENT_MOOD_TRANSITION = "MOOD_TRANSITION"

VALID_EDGE_EVENT_TYPES = frozenset({
    EDGE_EVENT_ASR_CHUNK,
    EDGE_EVENT_SPEAK,
    EDGE_EVENT_BARGE_IN,
    EDGE_EVENT_RESOURCE_GATE,
    EDGE_EVENT_FALLBACK_LLM,
    EDGE_EVENT_TTS_SYNTH,
    EDGE_EVENT_VTS_EXPRESSION,
    EDGE_EVENT_VTS_TALK_GATE,
    EDGE_EVENT_EMOTION_CHANGE,    # 🆕 情感状态变化事件
    EDGE_EVENT_BOND_UPDATE,       # 🆕 Bond 积分更新事件
    EDGE_EVENT_MOOD_TRANSITION,   # 🆕 mood_state 转换事件
})

# 🆕 情感相关审计表纳入时间线
HOST_LOG_TABLES = (
    "intent_log",
    "governor_log",
    "skill_exec_log",
    "model_control_log",
    "channel_io_log",
    "avatar_action_log",
    "voice_event_log",
    "retrieval_event_log",
    "vision_event_log",
    "service_health_log",
    "error_log",
    "config_snapshot_log",
    "emotion_state_log",    # 🆕 情感状态变化日志
    "bond_event_log",       # 🆕 Bond 事件日志
)
```

**同时为 `UnifiedTimelineEntry` 增加情感字段：**

```python
@dataclass(frozen=True)
class UnifiedTimelineEntry:
    event_id: str
    source: str
    source_table: str
    event_type: str | None
    stage: str | None
    status: str | None
    reason_code: str | None
    ts_ms: int
    detail: str | None
    request_id: str
    session_id: str | None = None
    clock_drift_flag: bool = False
    # 🆕 情感字段
    emotion_tag: str | None = None       # 事件发生时的情感标签
    mood_state: str | None = None        # 事件发生时的 mood_state
    bond_stage: str | None = None        # 事件发生时的 Bond 阶段
```

**预期效果：** 跨端时间线从"情感盲"变为"情感感知"，可以清晰看到"AI 在什么时刻情感发生了变化"、"Bond 阶段在什么时刻发生了变化"，为情感分析和调试提供完整的可观测性。

---

### 🟢 深化优化34（P1）：`EMOTION_TO_EXPRESSION_ID` 只有6种情感——VTS 表情映射严重不足

**目标文件：** `host_brain/channels/vts.py`

**精读发现：**

```python
EMOTION_TO_EXPRESSION_ID: dict[str, str] = {
    "NEUTRAL": "exp_neutral_01",
    "HAPPY": "exp_happy_01",
    "SAD": "exp_sad_01",
    "ANGRY": "exp_angry_01",
    "SURPRISED": "exp_surprised_01",
    "THINKING": "exp_thinking_01",
    # ↑ 只有6种！
}
```

但 `metabolism.py` 的 `EmotionVector` 支持的情感标签远不止这6种：

```python
# metabolism.py 中的情感标签（来自前几轮分析）
ALLOWED_EMOTION_TAGS = frozenset({
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING",
    "EXCITED", "CALM", "ANXIOUS", "CONFUSED", "EMBARRASSED",
    "GRATEFUL", "LONELY", "PROUD", "SCARED", "DISGUSTED",
    # ↑ 至少16种情感标签
})
```

这意味着：
1. 当 AI 处于 `EXCITED`、`CALM`、`ANXIOUS` 等状态时，`resolve_vts_expression()` 会返回 `STATUS_DEGRADED` + neutral 降级
2. 超过60%的情感标签无法映射到对应的 VTS 表情，全部降级为 neutral
3. 用户看到的虚拟形象表情和 AI 的实际情感状态严重不符——AI 说自己很兴奋，但虚拟形象面无表情

**方案：** 扩展 `EMOTION_TO_EXPRESSION_ID` 映射表，覆盖所有 `EmotionVector` 支持的情感标签：

```python
EMOTION_TO_EXPRESSION_ID: dict[str, str] = {
    # 基础情感（原有）
    "NEUTRAL":    "exp_neutral_01",
    "HAPPY":      "exp_happy_01",
    "SAD":        "exp_sad_01",
    "ANGRY":      "exp_angry_01",
    "SURPRISED":  "exp_surprised_01",
    "THINKING":   "exp_thinking_01",
    # 🆕 扩展情感（v1.5 新增）
    "EXCITED":    "exp_excited_01",      # 兴奋
    "CALM":       "exp_calm_01",         # 平静
    "ANXIOUS":    "exp_anxious_01",      # 焦虑
    "CONFUSED":   "exp_confused_01",     # 困惑
    "EMBARRASSED":"exp_embarrassed_01",  # 尴尬
    "GRATEFUL":   "exp_grateful_01",     # 感激
    "LONELY":     "exp_lonely_01",       # 孤独
    "PROUD":      "exp_proud_01",        # 自豪
    "SCARED":     "exp_scared_01",       # 害怕
    "DISGUSTED":  "exp_disgusted_01",    # 厌恶
    # 🆕 mood_state 到表情的映射（当 emotion_tag 不可用时的备选）
    "ENGAGED":    "exp_engaged_01",      # 精力充沛
    "TIRED":      "exp_tired_01",        # 疲惫
    "CRITICAL":   "exp_critical_01",     # 能量极低
}

# 🆕 mood_state 到 emotion_tag 的映射（用于 mood_state 驱动表情）
MOOD_STATE_TO_EMOTION_TAG: dict[str, str] = {
    "engaged":  "EXCITED",
    "steady":   "NEUTRAL",
    "calm":     "CALM",
    "tired":    "SAD",
    "critical": "ANXIOUS",
}
```

**同时修复 `resolve_vts_expression()` 的降级逻辑：**

```python
def resolve_vts_expression(
    *,
    emotion_tag: str | None,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    trigger: str = TRIGGER_LLM,
    raw_expression_id: str | None = None,
    raw_action_params: dict[str, Any] | None = None,
    ts_ms: int | None = None,
    edge_available: bool = True,
    mood_state: str | None = None,   # 🆕 备选：当 emotion_tag 不可用时用 mood_state 推断
) -> VTSActionResult:
    # ...
    # 🆕 emotion_tag 为空时，尝试从 mood_state 推断
    if not normalized_tag and mood_state:
        inferred_tag = MOOD_STATE_TO_EMOTION_TAG.get(str(mood_state).lower())
        if inferred_tag:
            normalized_tag = inferred_tag
```

**预期效果：** VTS 表情映射从6种扩展到16+种，覆盖所有 `EmotionVector` 支持的情感标签，虚拟形象表情和 AI 情感状态的一致性从40%提升到接近100%。

---

### 🔵 深化优化35（P1）：`CameraRecognitionServiceContract.detect()` 的视觉识别结果不触发情感响应——视觉感知与情感系统完全断开

**目标文件：** `host_brain/vision/camera_event.py`

**精读发现：**

`detect()` 的输出结构：

```python
output = {
    "request_id": request_id,
    "intent_id": intent_id,
    "ts_ms": now_value,
    "evidence_ref": evidence_value,
    "detector_id": str(detector_id or CAMERA_DETECTOR_ID),
    "objects": objects,
    "status": STATUS_OK,
    "stage": S_VISION_CAMERA,
    "reason_code": None,
    "degraded": False,
    "latency_ms": latency_ms,
    "summary": {
        "object_count": len(objects),
        "low_confidence_count": low_conf_count,
    },
    "quality": {"stage": S_VISION_CAMERA, "reason_code": None},
    # ↑ 没有任何情感相关字段！
}
```

`_log_event()` 写入审计的 payload：

```python
payload={
    "detector_id": output["detector_id"],
    "object_count": len(objects),
    "low_confidence_count": low_conf_count,
    "latency_ms": latency_ms,
    # ↑ 没有情感触发信息！
},
```

这意味着：
1. 视觉识别结果（检测到用户的表情、姿态、物体）完全不触发情感响应
2. 如果摄像头检测到用户在哭泣（`label="crying_face"`），AI 不会因此产生情感反应
3. 如果检测到用户在微笑（`label="smiling_face"`），AI 不会因此感到开心
4. 视觉感知和情感系统之间没有任何连接——视觉是"情感盲"的

**方案：** 为视觉识别结果增加情感推断，并在检测到情感相关物体时触发情感响应：

```python
# 🆕 视觉情感触发规则（v1.5 新增）
VISION_EMOTION_TRIGGERS: dict[str, tuple[str, float]] = {
    # label → (emotion_tag, intensity)
    "crying_face":    ("SAD",      0.8),
    "smiling_face":   ("HAPPY",    0.7),
    "angry_face":     ("ANXIOUS",  0.6),
    "surprised_face": ("SURPRISED", 0.7),
    "sleeping":       ("CALM",     0.5),
    "laughing":       ("EXCITED",  0.8),
    "worried_face":   ("ANXIOUS",  0.6),
    "confused_face":  ("CONFUSED", 0.5),
}

def _infer_emotion_from_objects(
    objects: list[dict[str, Any]],
) -> tuple[str | None, float]:
    """从视觉识别结果推断情感触发。

    Returns:
        (emotion_tag, intensity) 或 (None, 0.0) 如果无情感触发
    """
    best_emotion: str | None = None
    best_intensity: float = 0.0

    for obj in objects:
        label = str(obj.get("label") or "").lower()
        conf = float(obj.get("conf") or 0.0)
        if conf < 0.6:  # 低置信度不触发情感
            continue
        trigger = VISION_EMOTION_TRIGGERS.get(label)
        if trigger:
            emotion_tag, base_intensity = trigger
            weighted_intensity = round(base_intensity * conf, 4)
            if weighted_intensity > best_intensity:
                best_emotion = emotion_tag
                best_intensity = weighted_intensity

    return best_emotion, best_intensity
```

**在 `detect()` 中集成情感推断：**

```python
objects = _normalize_objects(raw_objects, low_conf_threshold=self.low_conf_threshold)
# 🆕 从视觉识别结果推断情感触发
inferred_emotion, emotion_intensity = _infer_emotion_from_objects(objects)

output = {
    # ... 原有字段 ...
    # 🆕 情感推断结果
    "inferred_emotion": inferred_emotion,
    "emotion_intensity": emotion_intensity,
    "emotion_triggered": inferred_emotion is not None,
}
```

**预期效果：** 视觉感知从"情感盲"变为"情感感知"，摄像头检测到用户哭泣时 AI 会产生 `SAD` 情感响应，检测到用户微笑时 AI 会产生 `HAPPY` 情感响应，视觉感知和情感系统实现真正的连接。

---

### 🟣 深化优化36（P2）：`HybridFusionStrategy.fuse()` 的融合分数不感知情感强度——高情感强度的记忆和低情感强度的记忆得到相同的融合权重

**目标文件：** `host_brain/retrieval/recall_fusion.py`

**精读发现：**

`HybridFusionStrategy.fuse()` 的融合公式：

```python
final_score = round(
    vector_score * self.vector_weight + fts_score * self.fts_weight,
    6,
)
# ↑ 只有向量分数和全文分数，没有情感强度加权！
```

`_normalize_candidate()` 的 metadata 处理：

```python
merged_metadata["base_score"] = base_score
merged_metadata["matched_routes"] = [route.route_id]
merged_metadata["recall_rank"] = int(rank)
# ↑ 没有 emotional_intensity 字段！
```

这意味着：
1. 一条"情感强度=0.9"的记忆（用户哭泣时产生的记忆）和一条"情感强度=0.1"的记忆（普通闲聊时产生的记忆）在融合时得到相同的权重
2. 高情感强度的记忆应该在召回时得到更高的优先级，但当前融合策略完全忽略了这一点
3. 这导致 AI 在回忆时，普通闲聊的记忆和深度情感互动的记忆被同等对待

**方案：** 为 `HybridFusionStrategy` 增加情感强度加权参数：

```python
class HybridFusionStrategy:
    def __init__(
        self,
        *,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = DEFAULT_FTS_WEIGHT,
        emotion_intensity_weight: float = 0.0,   # 🆕 情感强度加权（默认0，不影响现有行为）
    ) -> None:
        # ...
        self.emotion_intensity_weight = float(emotion_intensity_weight)

    def fuse(
        self,
        *,
        vector_candidates: list[dict[str, Any]],
        fts_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # ...
        # 🆕 情感强度加权
        if self.emotion_intensity_weight > 0:
            for doc_id, entry in fused.items():
                metadata = entry.get("metadata") or {}
                emotional_intensity = float(metadata.get("emotional_intensity") or 0.0)
                emotion_boost = round(emotional_intensity * self.emotion_intensity_weight, 6)
                entry["score"] = round(
                    min(1.0, float(entry["score"]) + emotion_boost),
                    6,
                )
                entry["metadata"] = dict(metadata)
                entry["metadata"]["emotion_intensity_boost"] = emotion_boost
```

**预期效果：** 混合融合策略从"情感盲"变为"情感感知"，高情感强度的记忆（深度情感互动时产生的记忆）在召回时得到更高的优先级，AI 的回忆更倾向于情感深刻的时刻。

---

## 二十一、第六轮优化的核心价值总结

第六轮优化聚焦于**感知层情感断路**——情感信息在"感知→决策→输出→观测"全链路中的系统性断路：

| 优化项 | 发现的"感知层断路" | 影响范围 | 修复方案 |
|--------|-----------------|---------|---------|
| **优化31** 🔴 | 召回系统无情感维度路由，情感记忆和普通记忆混在同一召回池 | `recall_fusion.py` → 记忆召回 | 增加情感过滤参数和情感重排序 |
| **优化32** 🟠 | 决策层不感知情感状态，critical 状态下高消耗技能不被拒绝 | `decision.py` → 门控规则 | 增加 R8 情感保护门控 |
| **优化33** 🟡 | 跨端时间线不含情感事件，情感变化对观测层不可见 | `cross_timeline.py` → 可观测性 | 增加情感事件类型和情感审计表 |
| **优化34** 🟢 | VTS 表情映射只有6种，60%+情感标签降级为 neutral | `vts.py` → 虚拟形象 | 扩展映射表到16+种，增加 mood_state 备选 |
| **优化35** 🔵 | 视觉识别结果不触发情感响应，视觉感知与情感系统完全断开 | `camera_event.py` → 视觉感知 | 增加视觉情感触发规则，检测到用户表情时触发情感响应 |
| **优化36** 🟣 | 混合融合分数不感知情感强度，高情感强度记忆和普通记忆同等对待 | `recall_fusion.py` → 融合策略 | 增加情感强度加权参数 |

---

## 二十二、完整优化全景图（v1.5，36项）

### 六轮优化的层次递进

```
第一轮（v1.0）：发现5个"缺失功能"     → 需要新增什么
第二轮（v1.1）：发现6个"深层问题"     → 现有机制哪里不够深
第三轮（v1.2）：发现6个"假功能空壳"   → 现有代码哪里看起来有但实际没用
第四轮（v1.3）：发现6个"架构断层"     → 功能之间的连接在哪里断裂
第五轮（v1.4）：发现6个"跨模块盲区"   → 情感信息在全链路中的系统性缺失
第六轮（v1.5）：发现6个"感知层断路"   → 情感信息在感知→决策→输出→观测中的断路
```

### 按优先级分类（36项全景）

| 优先级 | 优化项 | 核心问题 |
|--------|--------|---------|
| **P0（必须修复）** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27, OPT31, OPT32 | 架构断层 + 跨模块盲区 + 感知层断路中的关键路径 |
| **P1（重要优化）** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29, OPT33, OPT34, OPT35 | 情感内核 + 关系层 + 记忆层 + 感知层 |
| **P2（体验增强）** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30, OPT36 | 输出层 + 细节打磨 |

### 情感信息全链路覆盖图（v1.5）

```
用户输入
    ↓
[视觉感知层] camera_event.py
    OPT35: 视觉识别 → 情感触发（新增）
    ↓
[召回层] recall_fusion.py
    OPT31: 情感维度路由（新增）
    OPT36: 情感强度加权融合（新增）
    ↓
[决策层] governor/decision.py
    OPT32: R8 情感保护门控（新增）
    ↓
[情感内核] metabolism.py
    OPT6/7/8: EmotionVector 深化（前几轮）
    ↓
[关系层] bond/
    OPT26: 情感加权 Bond 积分（第五轮）
    OPT27: derive_bond_summary 修复（第五轮）
    ↓
[记忆层] memory/
    OPT25: EpisodicRecord 情感字段（第五轮）
    OPT28: 压缩前保存用户情感（第五轮）
    OPT29: 情感关系蒸馏主题（第五轮）
    ↓
[输出层] qa.py / context_pack.py
    OPT19/20/22: 架构断层修复（第四轮）
    ↓
[通道层] channels/vts.py
    OPT34: VTS 表情映射扩展（第六轮）
    ↓
[观测层] observability/cross_timeline.py
    OPT33: 情感事件纳入时间线（第六轮）
    ↓
用户感知到的情感输出
```
