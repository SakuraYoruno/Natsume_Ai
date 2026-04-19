受众：MASTER
标题：Phase XIII — 风格系统彻底重构：台词蒸馏+情感检索+Prompt直注入（阶段方案留痕）

# Phase XIII：风格系统彻底重构

当前代码校准说明（2026-04-19）：
- 本文是 2026-03-30 的阶段设计文档，不是当前运行时事实的唯一入口。
- 文中核心模块目前均已真实落库：`host_brain/retrieval/style_distiller.py`、`host_brain/retrieval/emotion_utterance_index.py`、`host_brain/app/prompt_builder.py`、`host_brain/app/prompt_builder_node.py`、`host_brain/retrieval/reranker.py`、`scripts/runtime/run_style_distill_v5.py`。
- 当前接线应以实际代码为准：`host_brain/retrieval/query_planner.py` 已把 `style_canon / persona_utterance` 排除在 5 路 store map 之外；`host_brain/app/prompt_builder_node.py` 已通过 `EmotionTaggedUtteranceIndex` 独立注入 `persona_examples_block`，并过滤 `persona_utterance` evidence；`host_brain/retrieval/reranker.py` 已明确这两类内容不再走 Reranker 竞争；`host_brain/app/chat_context_builder.py` / `chat_context_style.py` 负责当前 style canon 常驻装配。
- 运行时资产当前以 `runtime/style_canon/*.jsonl`、`runtime/style_canon/*style_profile*.json` 等文件为主；文中示例产物名应按阶段方案理解，不保证与当前仓库文件集完全一致。

> 从根本上修复"台词库/风格指导对最终生成文本无影响"的问题。
> 不是调参数，而是改用法——台词库不是语义检索源，是说话方式训练数据。

## 问题诊断

### 旧系统的完整杀伤链

```
❌ Step 1: 台词用 embedding 语义相似度检索（台词和用户消息不在同一语义空间）
  ↓
❌ Step 2: score_weight=0.82 是 7 路中最低（先天劣势）
  ↓
❌ Step 3: Reranker top_k=5，用词汇重叠度打分（台词 vs 用户消息≈0 重叠）→ 台词被淘汰
  ↓
❌ Step 4: 即使通过 Reranker，token 预算仅 200，截断优先级倒数第二 → 被清空
  ↓
❌ Step 5: 在 prompt 中排第 16 位（WORLD_CANON_BINDING 之后）→ LLM 不重视
```

### Style Canon v4 的问题

旧版风格指导只有统计数字：
```
- 句长：均值约 22.14 字
- 疑问句：占比约 22%
```

**对 LLM 几乎无指导力——LLM 不是按百分比分配句式的机器。**

## 新架构

### 三层风格注入体系

```
层 1: Style Canon v5（常驻 prompt）
  └─ 从 1742 条台词深度蒸馏的风格规则
  └─ 句式模板 + 情感回应模式 + 真实台词示例
  └─ 位置：紧跟 NODE_ROLE_BINDING 之后

层 2: Emotion-Tagged Utterance Examples（按场景检索）
  └─ 按当前 emotion_tag 匹配 → 随机取 3-5 条同场景台词
  └─ 不走 embedding / 不走 Reranker → 独立通道直注入
  └─ 位置：紧跟 STYLE_CANON_BINDING 之后

层 3: Reranker 保底（兜底）
  └─ style_canon 类型 candidates 至少保留 1 条不被淘汰
```

## 新增模块

### 1. StyleDistiller（`host_brain/retrieval/style_distiller.py`）

从原始台词中深度蒸馏以下维度（旧版只有统计数字）：

| 维度 | 旧版 | v5 |
|------|------|-----|
| 句式模板 | ❌ 无 | 6 种模板 + 真实台词示例 |
| 情感回应模式 | ❌ 无 | 8 种场景 × 策略 + 句式特点 + 示例 |
| 特征词汇 | ❌ 无 | 高频词 30 + 口癖 15 |
| 节奏模式 | "均值 22 字" | 短/中/长占比 + 相邻句过渡模式 |
| 语气词 | "吧/啊/呢" | 频率精确到 % |
| 开场/收束 | ❌ 无 | Top 10 模式 |

蒸馏方法：
- 情感标签打标：中文 7 种关键词组 × 日语 7 种 → 8 个场景
- 句式识别：短句应答/省略号停顿/轻追问/情绪感叹/温柔转折/口语化
- 词汇分析：极简 2-gram 分词 + 停用词过滤 + 频率排序
- 节奏分析：长度分布 + 相邻台词过渡模式（S→M, M→S 等）

### 2. EmotionTaggedUtteranceIndex（`host_brain/retrieval/emotion_utterance_index.py`）

**替代 embedding 语义检索**的台词访问方式：

| 维度 | 旧方式 | 新方式 |
|------|--------|--------|
| 检索方法 | embedding 语义相似度 | 情感标签匹配 |
| 依赖 | ChromaDB + Reranker | 纯内存，无外部依赖 |
| 延迟 | 30-100ms（embed+search） | <1ms（字典查找+随机采样） |
| 匹配逻辑 | 台词文本 vs 用户消息 | emotion_tag → 场景 → 随机取样 |
| 结果变化性 | 同一 query 总是同样结果 | 每次随机不同（LLM 看到多样参考） |

emotion_tag 到场景的映射链（带 fallback）：
```
HAPPY → happy → daily
SAD → sadness → comfort → daily
TSUNDERE → tsundere → daily
NEUTRAL → daily → comfort
```

混合检索模式：主场景 3 条 + 日常 2 条 = 5 条参考台词。

### 3. PromptBuilder 改造

改动点：
1. **台词走独立通道**：从 `EmotionTaggedUtteranceIndex` 直接获取，不从 Reranker 后的 rows 提取
2. **位置上移**：从第 16 位（WORLD_CANON_BINDING 之后）移到第 3 位（STYLE_CANON_BINDING 之后）
3. **从 evidence 中过滤掉 persona_utterance 类型**：避免重复注入

prompt 结构变化：
```
1. NODE_ROLE_BINDING
2. DIRECT_RESPONSE_PRIORITY
3. STYLE_CANON_BINDING         ← 风格规则（v5 深度蒸馏）
4. PERSONA_STYLE_EXAMPLES      ← 台词参考（独立通道，紧跟风格）
5. BOND_AWARE_STYLE
6. ...（其他）
15. WORLD_CANON_BINDING
16. CONSTRAINT_BINDING_JSON     ← persona_examples 从这里移走了
```

### 4. Reranker 保底

`_build_topk()` 中增加保底逻辑：
- `style_canon` 类型 candidates 至少保留 1 条（不参与竞争排序）
- `persona_utterance` 已走独立通道，不需要在 Reranker 保底

## 蒸馏脚本

```bash
# 运行风格蒸馏（需要台词 JSONL 文件）
python scripts/runtime/run_style_distill_v5.py

# 输出：
# runtime/style_canon/style_profile_v5.json    — 完整风格画像
# runtime/style_canon/style_instruction_v5.txt  — v5 风格指导文本
```

## 性能影响

| 场景 | 旧系统 | 新系统 | 变化 |
|------|--------|--------|------|
| 台词检索延迟 | 30-100ms (embed+chroma) | <1ms (dict lookup) | **快 30-100x** |
| 台词到达 prompt | ~0%（被 Reranker 杀死） | 100%（独立通道） | **从 0 到 100%** |
| 风格指导质量 | 统计数字 | 具体模板+示例 | **质量飞跃** |
| prompt 位置 | 第 16 位 | 第 3-4 位 | **权重大幅提升** |

## 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `host_brain/retrieval/style_distiller.py` | 新增 | 深度台词分析器 |
| `host_brain/retrieval/emotion_utterance_index.py` | 新增 | 情感标签台词索引 |
| `host_brain/app/prompt_builder.py` | 修改 | 台词独立通道+位置上移 |
| `host_brain/retrieval/reranker.py` | 修改 | style_canon 保底机制 |
| `scripts/runtime/run_style_distill_v5.py` | 新增 | 蒸馏运行脚本 |
| `runtime/style_canon/style_profile_v5.json` | 产物 | 风格画像 JSON |
| `runtime/style_canon/style_instruction_v5.txt` | 产物 | v5 风格指导文本 |
