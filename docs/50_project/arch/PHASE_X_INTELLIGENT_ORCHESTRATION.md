受众：CODEX
标题：Phase X 智能编排层升级 — 六大方案技术文档（阶段方案留痕）

# Phase X 智能编排层升级 — 六大方案

当前代码校准说明（2026-04-19）：
- 本文是 2026-03-29 的阶段方案文档，不应直接当作当前实现任务清单。
- 文中六个模块目前均已真实落库：`memory_worth_evaluator.py`、`adaptive_prompt_budget.py`、`semantic_dedup.py`、`bond_aware_style.py`、`consolidation_trigger.py`、`tts_context_router.py`。
- 当前接线应以实际代码为准：`host_brain/retrieval/pipeline.py` 已接入 `AdaptivePromptBudget + SemanticDedup`，`host_brain/app/observability_persist.py` 已接入 `MemoryWorthEvaluator + ConsolidationTrigger`，`host_brain/app/chat_context_builder.py` / `host_brain/app/intelligent_runtime.py` 已接入 `BondAwareStyleEngine + TtsContextRouter`。
- 若要查看现行接口与责任边界，请优先阅读 `docs/15_theory/RETRIEVAL_LAYER_CONTRACT.md`、`docs/15_theory/TOOLS_MEMORY_LAYER_CONTRACT.md`、`docs/15_theory/CORE_LAYER_MODULE_REFERENCE.md` 与实际代码。

## 概述

在 Agentic RAG 的基础上，进一步在记忆写入、Prompt 预算、召回质量、风格表达、蒸馏调度、语音合成六个维度加装智能编排层。所有方案共享同一设计哲学：**在现有管线上层包装，零侵入，纯规则驱动，安全降级**。

## 方案 1：智能记忆写入（MemoryWorthEvaluator）

**文件**：`host_brain/memory/memory_worth_evaluator.py`

**问题**：每轮对话无差别写入记忆，大量低价值记忆（"嗯""好的""早上好"）浪费 embedding 计算和存储。

**方案**：写入前用 MemoryWorthEvaluator 评估价值，决定 write / skip / write_light。

| 评估维度 | 权重 | 说明 |
|---------|------|------|
| 消息长度 | 基础分 | 短对话（<5字+<20字）→跳过 |
| 情感信号 | +0.3 | 含情感关键词（20个）→高价值 |
| 新信息信号 | +0.25 | 含事件/分享词（14个）→高价值 |
| 深度对话 | +0.2 | 含思考类词（12个）→高价值 |
| 多轮对话 | +0.1 | ≥3轮→实质交流 |
| emotion_tag | +0.15 | 非NEUTRAL→有情感 |

决策：score≥0.3→write，0.15-0.3→write_light，<0.15→skip

**预估收益**：减少 30-50% 的无效写入，显著降低 CPU 占用。

## 方案 2：自适应 Prompt 预算（AdaptivePromptBudget）

**文件**：`host_brain/retrieval/adaptive_prompt_budget.py`

**问题**：RecallPack 7 个槽位固定预算（world_canon=800, style_canon=400...），不管意图是什么。

**方案**：复用 QueryPlanner 意图，动态调整预算（零和调整，总量 2700 不变）。

| 意图 | 增加 | 减少 |
|------|------|------|
| chat | episodic+200, utterance+100, ledger+100 | world_canon-300, artifact-100 |
| recall_memory | episodic+300, ledger+200 | world_canon-400, artifact-100 |
| emotion | episodic+200, bond+200, utterance+100 | world_canon-300, artifact-200 |
| knowledge | world_canon+200, style+100 | episodic-200, ledger-100 |
| tool | artifact+300 | world_canon-200, style-100 |

## 方案 3：语义去重（SemanticDedup）

**文件**：`host_brain/retrieval/semantic_dedup.py`

**问题**：同一事件可能从不同 Store 召回多次（episodic 和 ledger 都有），浪费 token 预算。

**方案**：Rerank 后用 Jaccard 字符 3-gram 相似度检测近似重复，合并保留高分。

- 阈值：相似度 ≥ 0.65 视为重复
- 最少保留 3 条（不过度去重）
- 不依赖 embedding 模型，纯文本操作

## 方案 4：关系感知回复风格（BondAwareStyleEngine）

**文件**：`host_brain/persona/bond_aware_style.py`

**问题**：Bond 阶段对风格的影响粗糙，只有一行 stage_instruction。

**方案**：6 级 Bond 阶段各有完整的风格 profile（正式度、亲密度、是否允许撒娇/吐槽/示弱），叠加情绪调整和裂痕调整。

| Bond 阶段 | 亲密度 | 撒娇 | 吐槽 | 示弱 | 禁用词 |
|----------|--------|------|------|------|--------|
| STRANGER | 0.1 | ❌ | ❌ | ❌ | 撒娇/吐槽/哼/笨蛋/人家 |
| FRIEND | 0.5 | ❌ | ✅ | ❌ | 无 |
| CLOSE_FRIEND | 0.7 | ✅ | ✅ | ✅ | 无 |
| SOULMATE | 1.0 | ✅ | ✅ | ✅ | 无 |

## 方案 5：主动记忆整合（ConsolidationTrigger）

**文件**：`host_brain/memory/consolidation_trigger.py`

**问题**：蒸馏只在凌晨 3 点定时跑一次，聊得多的日子记忆堆积到第二天才处理。

**方案**：双触发条件 + 冷却机制。

| 触发类型 | 条件 | 说明 |
|---------|------|------|
| VOLUME | 24h 内 ≥50 条新记忆 | 聊得多→提前蒸馏 |
| TOPIC | 同一话题 ≥5 次 | 热门话题→专项蒸馏 |
| 冷却 | 两次触发间隔 ≥4h | 防止频繁蒸馏 |

接口：每次记忆写入后调用 `on_memory_written()`，需要蒸馏时 `check()` 返回 `should_trigger=True`。

## 方案 6：上下文感知 TTS 路由（TtsContextRouter）

**文件**：`host_brain/channels/tts_context_router.py`

**问题**：TTS 参数固定，一个语气读所有——安慰和兴奋听起来一样。

**方案**：从 LLM 输出文本分析情感色彩，动态调整 speed / pitch / energy。

| 情感风格 | 语速 | 音调 | 能量 | 场景 |
|---------|------|------|------|------|
| comfort | 0.85x | -0.5 | 0.9 | 安慰、陪伴 |
| happiness | 1.1x | +0.5 | 1.1 | 开心、庆祝 |
| playful | 1.05x | +0.3 | 1.05 | 俏皮、逗趣 |
| coquettish | 0.95x | +0.8 | 0.95 | 撒娇、软萌 |
| sadness | 0.85x | -0.5 | 0.85 | 难过、低沉 |
| serious | 0.95x | -0.3 | 1.0 | 严肃、认真 |

路由优先级：显式 emotion_tag > 文本关键词分析 > VAD 推断 > 默认
Bond 微调：亲密度越高→语速略慢、音调略升（更温柔）
语言检测：自动识别日语（假名检测），切换 lang_hint

## 文件清单

| # | 文件 | 方案 | 行数 |
|---|------|------|------|
| 1 | `host_brain/memory/memory_worth_evaluator.py` | 智能记忆写入 | ~190 |
| 2 | `host_brain/retrieval/adaptive_prompt_budget.py` | 自适应预算 | ~140 |
| 3 | `host_brain/retrieval/semantic_dedup.py` | 语义去重 | ~160 |
| 4 | `host_brain/persona/bond_aware_style.py` | 关系感知风格 | ~260 |
| 5 | `host_brain/memory/consolidation_trigger.py` | 主动记忆整合 | ~180 |
| 6 | `host_brain/channels/tts_context_router.py` | TTS 路由 | ~270 |

## 降级策略

所有方案统一降级模式：模块 import 失败或运行异常 → 静默跳过，不阻断主链路。
