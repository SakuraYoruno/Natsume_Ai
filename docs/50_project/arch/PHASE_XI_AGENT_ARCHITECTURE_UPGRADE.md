受众：CODEX
标题：Phase XI 智能体架构升级 — 七大方案技术文档（阶段方案留痕）

# Phase XI 智能体架构升级 — 七大方案

当前代码校准说明（2026-04-19）：
- 本文是 2026-03-29 的阶段方案文档，不应直接当作当前运行时事实或实现任务清单。
- 文中七个模块目前均已真实落库：`reflection_chain.py`、`episodic_memory_graph.py`、`predictive_state.py`、`planning_agent.py`、`meta_learning_loop.py`、`skill_composer.py`、`multimodal_processor.py`。
- 当前接线应以实际代码为准：这些能力统一经 `host_brain/app/dependencies.py` 注册；`host_brain/app/intelligent_runtime.py` 连接 `MultimodalProcessor / PredictiveStateEngine / PlanningAgent / SkillComposer / ReflectionChain / MetaLearningLoop`，`host_brain/app/observability_persist.py` 连接 `PredictiveStateEngine / EpisodicMemoryGraph / MetaLearningLoop`，`host_brain/app/prompt_builder_node.py` 消费 `predictive_state`。
- 这些能力并非每次 `/v1/chat` 请求都必经；当前主链仍应以 `host_brain/app/chat_handler.py -> cortex_orchestrator.py -> agent_runtime_shared.py -> executive_router / executive_runtime*` 为准。
- 其中 `planning_agent.py` 的部分旧接口已转向 `executive_runtime_core.py` 与 `plan_graph.py` 协同使用，阅读时应以实际代码签名和调用点为准。

## 概述

在 Phase X（智能编排层）基础上，向推理、记忆、预测、规划、元学习、技能协作、多模态七个方向深度升级，使 Natsume_Ai 从"应答型 AI 伴侣"进化为"自主推理+自我优化+多模态感知的智能体"。

## 方案 1：ReflectionChain（自我反思链）

**文件**：`host_brain/reasoning/reflection_chain.py`

Generate → Reflect → Revise 循环。仅在高风险场景启用（负面情绪/深度对话/多轮亲密对话）。

5 维结构化评估（权重）：

| 维度 | 权重 | 评估内容 |
|------|------|---------|
| 情绪契合度 | 0.30 | 回复基调是否匹配用户情绪 |
| Canon 合规性 | 0.25 | 有没有说"我是AI"等违规 |
| 风格一致性 | 0.20 | 是否符合 Bond 阶段说话方式 |
| 信息准确性 | 0.15 | 有没有编造记忆 |
| 长度适当性 | 0.10 | 回复不过短不过长 |

通过阈值 0.75，不通过则生成修正提示（revision_hints）供 LLM 重新生成。

## 方案 2：EpisodicMemoryGraph（情景记忆图谱）

**文件**：`host_brain/memory/episodic_memory_graph.py`

将扁平记忆连成主题线：`(:Memory)-[:CONTINUES]->(:Memory)`

核心能力：
- `add_memory()`：写入时自动做话题聚类（Jaccard 关键词重叠 + 72h 时间窗口）
- `walk()`：从一条记忆沿主题线走，召回完整上下文（前后各 5 步）
- `get_active_topics()`：获取近 7 天活跃话题

聚类算法：中文 2-gram 关键词提取 → Jaccard 重叠 ≥ 0.3 → 同主题链接。单条链最长 10 节点。

## 方案 3：PredictiveState（预测性状态）

**文件**：`host_brain/core/predictive_state.py`

基于历史 emotion_tag + 时间戳提取周期性模式，预测用户当前可能的状态。

6 个预定义时段：工作日早晨/工作时间/工作日晚间/工作日深夜/周末白天/周末夜晚

工作原理：统计各时段的情绪分布 → 主导情绪占比 ≥ 40% → 生成主动行为提示

示例：周一 23:00，历史显示该时段 65% 疲惫 → `proactive_hint: "用户这个时段经常疲惫，语气要温柔，主动关心休息。"`

## 方案 4：PlanningAgent（任务规划代理）

**文件**：`host_brain/agents/planning_agent.py`

ReAct 式任务分解：Reasoning + Acting 交替。

流程：`needs_planning()` 判断复杂度 → `create_plan()` 分解为步骤 → `advance_step()` 逐步执行

步骤类型：reason（理解）/ act（执行）/ ask（追问）/ synthesize（综合）

复杂度判定：复杂度指标关键词 + 多部分连接词 + 长度 + 多问号，得分 ≥ 2 触发规划。

## 方案 5：MetaLearningLoop（元学习循环）

**文件**：`host_brain/core/meta_learning_loop.py`

系统级自我优化：评估各模块效果 → 自动调参 → 生成 Evolution Report。

指标收集：每次模块调用后 `record()` → 自定义指标 EMA 滑动平均

参数调整规则（示例）：
- QueryPlanner 跳过率 < 5% → 放宽 greeting 判定
- SemanticDedup 去重率 > 50% → 提高相似度阈值
- MemoryWorth 跳过率 > 70% → 降低写入阈值
- ReflectionChain 修订率 > 50% → 降低通过阈值

Evolution Report：健康度 + 模块指标 + 参数建议 + 人类可读摘要。

## 方案 6：SkillComposer（技能组合编排）

**文件**：`host_brain/skills/skill_composer.py`

Skill DAG Planner → 拓扑排序 → 并行/串行混合执行。

流程：
1. `register_skill()` 注册技能（含 input_types/output_types/capabilities）
2. `plan()` 分析请求 → 提取所需能力 → 匹配技能 → 建立依赖 → 拓扑排序
3. `execute()` 按层级执行（同层可并行）

示例："用日语写诗然后读给我听" → DAG: [translation] → [tts]，串行执行。

## 方案 7：MultimodalProcessor（多模态上下文）

**文件**：`host_brain/channels/multimodal_processor.py`

输入侧：
- 解析用户消息中的 emoji → 情绪信号
- 识别图片/表情包附件 → 调用 Vision LLM 分析（可选）
- 判断主模态（text/image）

输出侧：
- 根据 emotion_tag + bond_stage 建议合适的 emoji
- 高亲密度/日语场景建议颜文字（kaomoji）
- STRANGER 阶段不加 emoji（保持距离感）

情感→emoji 映射：happiness→😊🥰 / sadness→😢🥺 / comfort→🫂💗 / playful→😝🤭

## 文件清单

| # | 文件 | 方案 | 行数 | 位置 |
|---|------|------|------|------|
| 1 | `reflection_chain.py` | 自我反思链 | ~290 | reasoning/ |
| 2 | `episodic_memory_graph.py` | 记忆图谱 | ~270 | memory/ |
| 3 | `predictive_state.py` | 预测性状态 | ~240 | core/ |
| 4 | `planning_agent.py` | 任务规划 | ~280 | agents/ |
| 5 | `meta_learning_loop.py` | 元学习循环 | ~280 | core/ |
| 6 | `skill_composer.py` | 技能编排 | ~310 | skills/ |
| 7 | `multimodal_processor.py` | 多模态上下文 | ~300 | channels/ |

## 系统全景图（Phase X + XI 合并）

```
感知层    ✅ 情绪 VAD / ✅ 意图分类 / ✅ QueryPlanner / 🆕 MultimodalProcessor
记忆层    ✅ 三层分级 / ✅ 蒸馏 / ✅ 智能写入 / ✅ 主动整合 / 🆕 EpisodicGraph
检索层    ✅ 7路召回 / ✅ Agentic RAG / ✅ 语义去重 / ✅ 自适应预算
推理层    ✅ LLM Gateway / ✅ RoutingMatrix / 🆕 ReflectionChain
表达层    ✅ Style Canon / ✅ Bond风格 / ✅ TTS路由
治理层    ✅ Canon 宪法 / ✅ 分层治理 / ✅ 审计链
进化层    ✅ 人格成长 / ✅ 交互驱动 / ✅ 反馈循环 / 🆕 MetaLearning
协作层    ✅ Multi-Agent / ✅ Supervisor / 🆕 PlanningAgent / 🆕 SkillComposer
预测层    🆕 PredictiveState（周期性模式预测）
```
