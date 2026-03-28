受众：CODEX
标题：Agentic RAG 智能检索升级 — 架构文档

# Agentic RAG 智能检索升级（Phase X）

## 1. 概述

将 Natsume_Ai 的检索系统从"单次全量七路并行召回"升级为"意图驱动的自适应多轮检索"。

**核心改进**：
- 简单寒暄跳过检索（省 ~3 秒）
- 按意图动态选择 Store 子集（2-3 路 vs 全量 7 路）
- 证据质量评估 + 自动改写重试（最多 3 轮）
- Query 改写提升召回质量

**设计原则**：
- 零侵入：在现有检索管线上层包装，不改动任何现有 Store 实现
- 零 LLM：所有规划/评估/改写都是规则驱动，<5ms 延迟
- 安全降级：任何模块不可用时静默回退到原有全量检索流程

## 2. 架构

### 2.1 升级前

```
用户消息 → Embed → 7路全量并行召回 → Rerank → EvidencePack → LLM → 回复
                    ↑ 每次都跑 7 路 ↑
```

### 2.2 升级后

```
用户消息 → QueryPlanner（意图分类 + Store 路由 + Query 改写）
  ├── greeting → 跳过检索 → LLM（仅用常驻上下文）→ 回复
  └── 其他意图 → 选择 Store 子集 → Retrieve
                  → EvidenceEvaluator（够了吗？）
                    ├── 够了 → Rerank → EvidencePack → LLM → 回复
                    └── 不够 → 改写 Query → Re-Retrieve（最多 2-3 轮）
```

### 2.3 常驻上下文 vs 检索上下文

| 类型 | 来源 | 是否走检索 | 说明 |
|------|------|-----------|------|
| World Canon (Core) | 内存直读 | ❌ | 核心人设永远注入 |
| Style Canon (Rules) | 内存直读 | ❌ | 语言风格规范永远注入 |
| 人格特质向量 | 内存直读 | ❌ | 7 维特质永远注入 |
| HardState | 内存直读 | ❌ | 情绪/心情/energy 永远注入 |
| Bond 状态 | 内存直读 | ❌ | 关系阶段/亲密度永远注入 |
| 时间上下文 | 系统注入 | ❌ | 时间/日期永远注入 |
| episodic_vector | ChromaDB | ✅ | 对话历史 embedding |
| sqlite_ledger | SQLite | ✅ | 意图日志/记忆操作 |
| neo4j_bond | Neo4j/SQLite | ✅ | Bond 事件历史 |
| style_canon (向量) | ChromaDB | ✅ | 风格典籍向量投影 |
| world_canon (向量) | ChromaDB | ✅ | 世界观典籍向量投影 |
| artifact_vault | SSOT | ✅ | 制品库 |
| persona_utterance | ChromaDB | ✅ | 中日台词向量库 |

**关键理解**：跳过检索 ≠ 跳过人格。AI 的灵魂（Canon + 情绪 + Bond + 人格）是常驻的。

## 3. 模块详解

### 3.1 QueryPlanner（查询规划器）

**文件**：`host_brain/retrieval/query_planner.py`

**意图分类规则**（优先级从高到低）：

| 意图 | 判定条件 | 示例 |
|------|---------|------|
| `greeting` | 正则匹配 + 新对话首轮 | "早上好""你好""hello" |
| `complex` | 长度>100 或 多个问号 | 长段落提问 |
| `recall_memory` | 含"还记得/上次/之前/以前" | "你还记得上次聊的吗" |
| `emotion` | 含"心情/难过/开心/累" | "我今天心情不好" |
| `knowledge` | 含"设定/你是谁/规则" | "你叫什么名字" |
| `tool` | 含"执行/搜索/帮我查" | "帮我搜一下" |
| `chat` | 以上都不匹配 | 日常聊天 |

**Store 路由表**：

| 意图 | 选择的 Store | 跳过的 Store |
|------|------------|------------|
| `greeting` | 无（跳过） | 全部 7 路 |
| `chat` | episodic_vector, persona_utterance | 5 路 |
| `recall_memory` | episodic_vector, sqlite_ledger, persona_utterance | 4 路 |
| `emotion` | neo4j_bond, episodic_vector, persona_utterance | 4 路 |
| `knowledge` | world_canon, style_canon | 5 路 |
| `tool` | artifact_vault | 6 路 |
| `complex` | 全部 7 路 | 无 |

**Query 改写**（三层，~40 条规则）：
1. 停用词去除：去掉"你/我/的/了/吗"等
2. 时间词归一化："昨天"→lookback 2天，"上次"→7天
3. 情绪同义词扩展："心情不好"→补充"难过 低落 安慰"

### 3.2 EvidenceEvaluator（证据评估器）

**文件**：`host_brain/retrieval/evidence_evaluator.py`

三维评估：

| 维度 | 阈值 | 权重 |
|------|------|------|
| 数量 | ≥ 2 条 | 0.3 |
| 质量（top_score） | ≥ 0.25 | 0.3 |
| 覆盖度（关键词重叠） | ≥ 0.4 | 0.4 |

评估建议：
- `proceed`：证据充足，继续生成
- `retry_with_rewrite`：质量不足，改写 query 重试
- `expand_stores`：覆盖不足，扩展到更多 Store

### 3.3 AgenticRetrievalLoop（检索循环）

**文件**：`host_brain/retrieval/agentic_retrieval_loop.py`

编排 Plan → Retrieve → Evaluate → Re-Retrieve 循环：

```python
for round_idx in range(plan.max_rounds):
    current_query = queries[round_idx]
    result = recall_fusion.recall(query=current_query, ...)
    all_evidence = merge_dedupe(all_evidence, result.candidates)
    evaluation = evaluator.evaluate(query, all_evidence)
    if evaluation.sufficient:
        break
    # 根据建议调整下一轮策略
```

最大轮次：greeting=0, chat=1, recall/emotion=2, complex=3

### 3.4 Graph 集成

**文件**：`host_brain/app/graph/main_chain.py`（RecallFusionNode）

在 RecallFusionNode.execute() 的 orchestrator 调用之前注入 QueryPlanner：
- `skip_retrieval=True` → 直接 return，跳过整个七路召回
- `skip_retrieval=False` → 继续原有流程（Agentic 元数据写入审计）
- QueryPlanner 不可用 → 静默降级到原有全量流程

审计标签：`AGENTIC_SKIP_RETRIEVAL`（跳过时）
审计元数据：`audit_refs.agentic_rag`（意图/Store/轮次等）

## 4. 性能影响

| 场景 | 原架构 | Agentic RAG | 变化 |
|------|-------|-------------|------|
| "早上好" | 7路全量 ~3s | 跳过 0ms | **-3s** |
| "今天好无聊" | 7路 ~3s | 2路 ~0.8s | **-2.2s** |
| "你还记得…" | 7路×1轮 ~3s | 3路×≤2轮 ~2s | **-1s + 质量↑** |
| 复杂多层问题 | 7路×1轮 ~3s | 7路×≤3轮 ~5s | +2s（质量↑↑） |
| **日均加权** | ~3s | **~1.5s** | **快 50%** |

## 5. 文件清单

| 文件 | 状态 | 说明 |
|------|------|------|
| `host_brain/retrieval/query_planner.py` | 🆕 新建 | 查询规划器 |
| `host_brain/retrieval/evidence_evaluator.py` | 🆕 新建 | 证据评估器 |
| `host_brain/retrieval/agentic_retrieval_loop.py` | 🆕 新建 | 检索循环编排器 |
| `host_brain/retrieval/__init__.py` | 修改 | 导出新模块 |
| `host_brain/app/graph/main_chain.py` | 修改 | RecallFusionNode 注入 QueryPlanner |

## 6. 配置

无新增环境变量。所有参数通过代码常量配置，后续可外部化到 Canon。

## 7. 降级保护

| 场景 | 行为 |
|------|------|
| QueryPlanner import 失败 | 静默降级到原有全量检索 |
| 意图分类不确定 | 回退到 `chat`（2路检索）或 `complex`（7路全量） |
| EvidenceEvaluator 失败 | 视为 sufficient，不重试 |
| AgenticRetrievalLoop 异常 | 降级到单次全量检索 |

## 8. 后续演进

- **Phase X.1**：Store 路由也外部化到 Canon（Master 可调）
- **Phase X.2**：AgenticRetrievalLoop 在 RecallFusionNode 中全量替代 orchestrator（当前仅实现 skip 逻辑）
- **Phase X.3**：LLM 辅助的高质量 Query 改写（仅在重试轮使用）
- **Phase X.4**：基于历史检索效果的自适应阈值调整
