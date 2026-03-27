受众：MASTER
标题：情感层管线修复开发实测报告（2026-03-27）

# 情感层管线修复开发实测报告

| 项目    | 值                                 |
|---------|-----------------------------------|
| 日期    | 2026-03-27                         |
| 节点    | DEV（开发机，无运行时）              |
| 修改文件 | `host_brain/app/chat_context_builder.py` |
| Lint    | 0 error / 0 warning                |

---

## 1. 修复概述

审查发现情感层 10 个子系统中，仅 2 个正常工作，其余均存在管线断裂或完全未接入的问题。本次修复全部 8 个问题子系统。

### 1.1 P0-瓶颈1：emotion_context 传递不完整（高优）

**问题**：`build_chat_generation_prompt_payload()` L615-620 构建 `generation_payload["emotion_context"]` 时只传了 4 个基础字段：
- `mood_state` / `emotion_tag` / `emotion_range_hint` / `emotion_intensity`

丢弃了 `normalize_chat_emotion_context()` 产出的其余 8 个字段：
- `vad_vector` / `emotion_echo` / `inner_monologue` / `triggered_emotions`
- `user_emotion_profile` / `emotion_ts_ms` / `_prev_emotion_tag` / `allowed_emotion_tags`

**修复**：将所有字段完整传递。

### 1.2 P0-瓶颈2：runtime_payload 顶层字段缺失（高优）

**问题**：`prompt_builder.render_node_generation_prompt()` 直接从 `runtime_payload` 顶层读取以下 key：
- `emotion_vector` / `emotion_timeline` / `emotion_echo` / `inner_monologue`
- `inferred_emotion` / `user_emotion_profile` / `bond_narrative`
- `self_reflection_pulse` / `persona_expression_hint`

但 `build_chat_prompt_runtime_payload()` 从未将这些字段写入 runtime_payload 顶层，导致 prompt 中对应区块永远为空。

**修复**：在 `build_chat_prompt_runtime_payload()` 返回前，从 `generation_payload["emotion_context"]`（已在 P0-1 中修复为完整字段）和全局单例中提取数据，注入 runtime_payload 顶层。包括：
- `emotion_vector` — 来自 vad_vector
- `emotion_echo` — 来自 emotion_echo
- `inner_monologue` — 来自 inner_monologue
- `user_emotion_profile` — 来自 user_emotion_profile
- `triggered_emotions` — 来自 triggered_emotions
- `emotion_timeline` — 来自全局 EmotionTimeline 单例的 rebuild_summary()

### 1.3 P1：PersonaExpressionAdapter 接入

**问题**：类已定义（`host_brain/persona/expression_adapter.py`）、prompt 渲染已就绪（prompt_builder L826-851），但 main_chain 从未调用。

**修复**：在 `build_chat_prompt_runtime_payload()` 中实例化 `PersonaExpressionAdapter`，传入当前 `PersonalityTraits`（default）、`EmotionVector`、`bond_stage`，产出 `ExpressionHint` 写入 `runtime_payload["persona_expression_hint"]`。

### 1.4 P1：SelfReflection 接入

**问题**：同上。类已定义（`host_brain/persona/self_reflection.py`），prompt 渲染已就绪（prompt_builder L805-823），中间驱动层缺失。

**修复**：在 `build_chat_prompt_runtime_payload()` 中维护进程级 `SelfReflection` 单例和轮次计数器，每轮调用 `maybe_generate()`。概率触发时（每 5-10 轮一次）产出 `self_reflection_pulse` 写入 runtime_payload。

### 1.5 P1：BondNarrativeLayer 接入

**问题**：同上。类已定义（`host_brain/bond/bond_narrative.py`），prompt 渲染已就绪（prompt_builder L781-803），中间驱动层缺失。

**修复**：在 `build_chat_prompt_runtime_payload()` 中尝试获取 `SharedMemoryIndex` 和 `MilestoneTracker`（通过 dependencies），传入 `BondNarrativeLayer.build_narrative()`，产出 `bond_narrative` 写入 runtime_payload。

### 1.6 P2：UserEmotionProfile 显式传递

**问题**：原来通过隐式路径（prompt_builder 内部重新 normalize）脆弱接入。

**修复**：在 P0-瓶颈2 修复中已一并完成——显式将 `user_emotion_profile` 写入 runtime_payload 顶层。

---

## 2. 降级策略

所有 P1 接入均采用 try/except 包裹，任何依赖不可用时静默降级：
- **PersonaExpressionAdapter**：降级 → 不注入 `persona_expression_hint`，prompt 中该区块为空
- **SelfReflection**：降级 → 不注入 `self_reflection_pulse`
- **BondNarrativeLayer**：降级 → 不注入 `bond_narrative`
- **EmotionTimeline**：降级 → 不注入 `emotion_timeline`

主链路不受影响。

---

## 3. 修复前后对比

| 子系统 | 修复前 | 修复后 |
|--------|--------|--------|
| EmotionVector VAD | ⚠️ 管线断裂 | ✅ 通过 emotion_context + runtime_payload 双通道传递 |
| EmotionTimeline | ⚠️ 管线断裂 | ✅ 从全局单例获取摘要写入 runtime_payload |
| EmotionEcho | ⚠️ 管线断裂 | ✅ 从 emotion_context 写入 runtime_payload |
| InferredEmotion | ⚠️ 管线断裂 | ⚠️ 仍需 main_chain 后置写入（LLM 执行后才有） |
| InnerMonologue | ✅ 正常（隐式） | ✅ 正常（显式+隐式双保险） |
| UserEmotionProfile | ⚠️ 脆弱隐式 | ✅ 显式传递 |
| PersonaExpression | ❌ 未接入 | ✅ 已接入 |
| SelfReflection | ❌ 未接入 | ✅ 已接入（概率触发） |
| BondNarrative | ❌ 未接入 | ✅ 已接入 |
| EmotionAgent | ✅ 正常 | ✅ 正常（不变） |

**注意**：`InferredEmotion` 由于在 LLM 执行**之后**才产出（需要 LLM 输出内容来推理），无法在 prompt 构建阶段注入。但 main_chain 中已正确写入 `ctx.metadata["inferred_emotion"]`，高置信度时会覆盖 `emotion_tag`，间接影响下一轮对话。这是预期设计，不需要修复。

---

## 4. 待真机验证测试用例

| 编号 | 验证内容 | 预期结果 |
|------|---------|---------|
| T-EMO-01 | 日志中检查 `emotion_vector` 字段是否非空 | VAD 三维数值 ≠ 全零 |
| T-EMO-02 | 日志中检查 `emotion_timeline` 字段 | entry_count > 0，dominant_emotion 非 NONE |
| T-EMO-03 | 日志中检查 `emotion_echo` 字段 | echo_emotion_tag 非空 |
| T-EMO-04 | 日志中检查 `inner_monologue` 字段 | 非空中文自然语言描述 |
| T-EMO-05 | 日志中检查 `user_emotion_profile` 字段 | status=ACTIVE（多轮对话后） |
| T-EMO-06 | 日志中检查 `persona_expression_hint` 字段 | style_hint 非空，tone ∈ {caring/playful/serious/gentle} |
| T-EMO-07 | 5-10 轮对话后检查 `self_reflection_pulse` | 至少出现一次非空 pulse |
| T-EMO-08 | 检查 `bond_narrative`（需要 SharedMemoryIndex 可用） | 有共同记忆时应包含 shared_memories 列表 |
| T-EMO-09 | 发送悲伤内容检查 prompt 中 EMOTION_BINDING 区块 | emotion_echo_tag 应为 SAD/EMPATHETIC 相关 |
| T-EMO-10 | 检查 prompt 中 PERSONA_EXPRESSION_HINT 区块 | 应包含 style_hint / tone / guidance |
| T-EMO-11 | 检查 prompt 中 VAD 区块 | valence/arousal/dominance 应有非零值 |
