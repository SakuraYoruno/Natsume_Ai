受众：MASTER
标题：Gateway 中文文字链路完整节点分析

# Gateway 中文文字链路完整节点分析

> 分析日期：2026-03-27
> 覆盖范围：从 HTTP 入口到最终 COMMIT 输出的全部节点，含召回层、情感层、Agent 层、Graph 编排层

---

## 一、链路总览

Gateway 中文链路当前在运行时已收敛为 `gateway_orchestrator_sse_generator` 薄适配层，实际回复生成转交 `top_agent.py`。

下文保留的是旧 Gateway 中文生成链分析，供历史排障参考：

```
路径A：POST /v1/chat（HTTP API 直连）
路径B：POST /v1/input（Edge 输入上报 — 微信/QQ/飞书）
```

### 完整执行时序图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        入口层（两条路径）                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  路径A: POST /v1/chat                路径B: POST /v1/input           │
│    │                                    │                            │
│    ▼                                    ▼                            │
│  fastapi_app.chat()              input_routes._dispatch_to_graph()   │
│    │                                    │                            │
│    ▼                                    ▼                            │
│  handle_chat()                   run_main_chain_from_input()         │
│    │                               source="GATEWAY"                  │
│    │                               pre_scene = SCENE_GATEWAY         │
│    │                                    │                            │
│    │                                    ▼                            │
│    │                             MainChain.run_as_sse_adapter()      │
│    │                               ctx.metadata["render_target_lang"]│
│    │                               = "zh"  ← ★强制中文★             │
│    │                                    │                            │
│    │                    ┌────────────────┘                            │
│    │                    │ 前置节点 (pre_node_order)                   │
│    │                    │  ① AirlockQuarantineNode                   │
│    │                    │  ② GovernorNode                            │
│    │                    │  ③ GraphExecutorNode                       │
│    │                    │  ④ RecallFusionNode ← ★七路召回★          │
│    │                    │  ⑤ LlmGatewayNode                         │
│    │                    │                                            │
│    ▼                    ▼                                            │
│  _unified_chat_sse_generator()                                       │
│    │                                                                 │
│    ├─ yield PRESENCE(THINKING)                                       │
│    ├─ resolve_chat_emotion_context() ← ★情感层入口★                 │
│    ├─ Governor 鉴权 (IntentDecisionEngine)                           │
│    ├─ yield PRESENCE(GOV_APPROVED)                                   │
│    ├─ pre_intent_classifier() → scene_type                           │
│    │                                                                 │
│    ▼                                                                 │
│  SceneRouter.route_sse(scene_type="GATEWAY")                         │
│    │                                                                 │
│    ▼                                                                 │
├──────────────────────────────────────────────────────────────────────┤
│              Gateway Orchestrator 核心（19 个步骤）                    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ① SCENE_ROUTED 事件                                                 │
│  ② 子场景意图识别 (pre_intent_classifier)                             │
│     ├─ SCENE_CHAT → 继续                                             │
│     └─ 非 CHAT → 委托 _complex_scene_sse_generator                   │
│  ③ 情感上下文完整解析 (normalize_chat_emotion_context)                │
│  ④ 预召回证据 + StateArbiter 四路并行查询                             │
│  ⑤ RecallPackBuilder 构建 (slot 分配 + token 预算)                    │
│  ⑥ Bond 上下文提取                                                   │
│  ⑦ build_chat_runtime_input (render_target_lang="zh")                │
│  ⑧ _build_stream_chat_prompt ← ★Prompt 完整构建★                   │
│     ├─ build_chat_prompt_runtime_payload()                           │
│     │   ├─ 注入 emotion_vector/timeline/echo (P0修复)                │
│     │   ├─ PersonaExpressionAdapter.adapt() (P1修复)                 │
│     │   ├─ SelfReflection.maybe_generate() (P1修复)                  │
│     │   └─ BondNarrativeLayer.build_narrative() (P1修复)             │
│     ├─ render_node_generation_prompt(CHAT_DIRECT_NODE_ID)            │
│     └─ _build_node_role_instruction(render_target_lang="zh")         │
│  ⑨ Gateway 回复深度策略注入                                          │
│  ⑩ 情感参数注入 (adjust_gen_params_for_emotion)                      │
│  ⑪ LLM 运行时解析 (resolve_host_llm_runtime_async)                  │
│  ⑫ 流式 LLM 生成 (astream_llm_chunks)                               │
│     ├─ yield TOKEN 事件 (逐 token)                                   │
│     └─ yield SENTENCE 事件 (句子边界)                                │
│  ⑬ 后置任务 (三路并行)                                               │
│     ├─ Airlock Ingress 检疫                                          │
│     ├─ 情节记忆写入 (persist_chat_episodic_memory)                   │
│     └─ Bond 动态写入 (BondDynamicsWriter)                            │
│  ⑭ TTS 异步生成 (可选, 默认关闭)                                     │
│  ⑮ 可观测性持久化 (persist_chat_observability_rows)                  │
│  ⑯ yield COMMIT 事件 ← ★最终输出★                                  │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│         路径B 专有：后置节点 (post_node_order)                        │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ⑥ QAContractNode (输出合规: 长度/安全词/情感一致性)                  │
│  ⑦ EpisodicStoreNode (情节记忆: MemoryGuard 宪法校验)                │
│  ⑧ AuditNode (ledger 审计快照)                                       │
│  ⑨ CanonProposalNode (Canon 提案检测和提交)                          │
│  ⑩ AirlockSanitizerNode (出口净化: null 字节清除)                    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 二、逐节点详解

### 2.1 入口层

#### 节点 0a：`fastapi_app.chat()` / `input_routes._dispatch_to_graph()`

| 项目 | 说明 |
|------|------|
| 文件 | `host_brain/app/fastapi_app.py` L259-262 / `host_brain/app/input_routes.py` |
| 执行条件 | 始终 |
| 输入 | HTTP POST body (ChatRequest) 或 Edge 输入载荷 |
| 输出 | StreamingResponse (SSE) 或 fire-and-forget |
| render_target_lang 设定 | 路径B: `run_main_chain_from_input` → source="GATEWAY" → `pre_scene = SCENE_GATEWAY` → `run_as_sse_adapter` → `ctx.metadata["render_target_lang"] = "zh"` |

### 2.2 MainChain 前置节点（路径 B 专有）

路径 B 通过 `MainChain.run_as_sse_adapter()` 在委托 SSE 生成器之前执行 5 个前置节点：

#### ① AirlockQuarantineNode

| 项目 | 说明 |
|------|------|
| 类 | `AirlockQuarantineNode(ChainNode)` L224 |
| name | `airlock_quarantine` |
| 超时 | 1.0s |
| 职责 | 输入消毒（去除 null 字节），空消息拦截 |
| 写入 ctx | `sanitized_user_message`, `airlock_quarantine{input_chars, output_chars, changed}` |
| 降级 | 空消息 → `GOVERNOR_BLOCK_EMPTY_MESSAGE` → 中止链路 |

#### ② GovernorNode

| 项目 | 说明 |
|------|------|
| 类 | `GovernorNode(ChainNode)` L257 |
| name | `governor` |
| 超时 | 2.0s |
| 职责 | R1-R7 规则检查 + R8 情感门控 |
| 执行条件 | `adapter_mode="sse"` 时跳过真实鉴权，仅做场景映射 |
| 写入 ctx | `governor{mode, scene_hint, task_type}` |
| 降级 | IntentDecisionEngine 不可用 → `DEGRADED_FALLBACK`（仅场景映射） |

#### ③ GraphExecutorNode

| 项目 | 说明 |
|------|------|
| 类 | `GraphExecutorNode(ChainNode)` L454 |
| name | `graph_executor` |
| 超时 | 5.0s |
| 职责 | 图执行模式判定 |
| 输出 | CHAT → `chat_single_stage`；其他 → `scene_graph_multi_stage` |
| 写入 ctx | `graph_executor{execution_mode, task_type}` |

#### ④ RecallFusionNode ★七路召回★

| 项目 | 说明 |
|------|------|
| 类 | `RecallFusionNode(ChainNode)` L471 |
| name | `recall_fusion` |
| 超时 | 5.0s |
| 职责 | 七路并行召回 + HybridFusion RRF + Reranker 重排序 |
| 核心调用 | `RetrievalOrchestrator.arun()` |
| 关键参数传递 | `emotion_context=_emotion_tag`, `render_target_lang=_render_target_lang` ← ★语种分离的关键★ |

**召回层完整管线：**

```
RecallFusionNode
  │
  ▼
RetrievalOrchestrator.arun(render_target_lang="zh")
  │
  ▼
RetrievalPipelineContract.run()
  │
  ├─ Stage 1: EmbeddingService.embed() → query_embedding
  │
  ├─ Stage 2: RecallFusionService.recall(render_target_lang="zh")
  │     │
  │     ▼
  │   resolve_recall_routes(render_target_lang="zh")
  │     │  ★语种路由筛选（今日修复）★
  │     │  zh → 匹配 selector="zh" 或 selector="*" 的路由
  │     │  过滤掉 selector="ja" 的路由
  │     │
  │     ▼ 活跃路由（zh 链路实际走的 7 路）：
  │     ├─ route_episodic (episodic_vector, selector="*")     → 情节记忆向量检索
  │     ├─ route_sqlite_ledger (sqlite_ledger, selector="*")  → 审计日志文本检索
  │     ├─ route_neo4j_bond (neo4j_bond, selector="*")        → Neo4j 关系图谱
  │     ├─ route_world_canon (world_canon, selector="*")      → 世界观典籍
  │     ├─ route_artifact_vault (artifact_vault, selector="*") → 工件库
  │     ├─ route_style_canon_zh (style_canon, selector="zh")  → ★中文风格典籍★
  │     └─ route_persona_utterance_zh (persona_utterance, selector="zh") → ★中文人格话语★
  │
  ├─ Stage 3: RerankerService.rerank() → 重排序 top-k
  │
  └─ Stage 4: EvidencePackService.build_pack() → 证据包
```

| 写入 ctx | `recall_fusion{mode, evidence_count, evidence_empty}`, `recall_payload{retrieval_summary, retrieval_evidence_items}` |
| 降级 | 熔断器 OPEN → `RECALL_CIRCUIT_OPEN`；异常 → `DEGRADED_FALLBACK` |
| 安全 | MemoryGuard 过滤 `NARRATIVE_ONLY` 证据 |

#### ⑤ LlmGatewayNode

| 项目 | 说明 |
|------|------|
| 类 | `LlmGatewayNode(ChainNode)` L943 |
| name | `llm_gateway` |
| 超时 | 30.0s |
| 职责 | 路由矩阵 + EmotionTimeline 追踪 + Phase 6 情感推理 |

**LlmGatewayNode 内部子步骤：**

```
LlmGatewayNode.execute()
  │
  ├─ RoutingMatrix 路由决策
  │   └─ 根据 task_type/latency_budget/risk_level 选模型
  │
  ├─ PermissionGuard 工具调用权限校验
  │   └─ 检查 pending_tool_calls 权限
  │
  ├─ EmotionTimeline 追踪 ★情感层关键节点★
  │   ├─ _global_emotion_timeline.append(emotion_tag, valence, arousal)
  │   ├─ _timeline.rebuild_summary(window_ms=300_000) → ctx.metadata["emotion_timeline"]
  │   └─ compute_emotion_echo() → ctx.metadata["emotion_echo"]
  │
  ├─ Phase 6：LLM 情感推理 ★三级级联★
  │   ├─ Level 1: 关键词检测（中日双语 POSITIVE/NEGATIVE/MILD_NEG 词表）
  │   ├─ Level 2: LLM 推理（EmotionInferenceCascade.infer）
  │   ├─ Level 3: LLM 回退（降级为关键词结果）
  │   ├─ 高置信度 (≥0.6) → 覆盖 ctx.metadata["emotion_tag"]
  │   └─ 低置信度 (<0.5) → 标记 needs_deep_emotion_analysis=True
  │       └─ ★触发 Supervisor 分派 EmotionAgent★
  │
  └─ StateArbiter SESSION_OVERRIDE 写入
      └─ 从 LLM 输出提取新事实 → session override
```

| 写入 ctx | `routing_matrix`, `permission_guard`, `emotion_timeline`, `emotion_echo`, `inferred_emotion`, `session_override`, `needs_deep_emotion_analysis` |
| 降级 | 各子步骤独立降级，不阻断主链路 |

---

### 2.3 SceneRouter 路由

| 项目 | 说明 |
|------|------|
| 文件 | `host_brain/app/graph/main_chain.py` L2242 |
| 判断逻辑 | `normalized_scene == SCENE_GATEWAY` → `gateway_orchestrator_sse_generator()` |
| 传递参数 | `_pre_recall_evidence`（来自 RecallFusionNode 的预召回结果） |

---

### 2.4 Gateway Orchestrator 核心节点

文件：`host_brain/app/handlers/gateway_orchestrator_handler.py`

#### 步骤 3a：情感上下文完整解析

```python
resolved_emotion_context = normalize_chat_emotion_context(emotion_context)
# 内部触发：
#   - VAD 映射（16种标签 → valence/arousal/dominance）
#   - 持久化情绪混合（时间衰减 + blend）
#   - EmotionTimeline.append()
#   - compute_emotion_echo()（共情响应）
#   - evaluate_emotion_triggers()（主动触发器）
#   - update_user_emotion_profile()（用户画像蒸馏）
#   - _vector_to_inner_monologue()（内心独白）
```

#### 步骤 3b：StateArbiter 四路并行查询

```python
_resolve_state_arbiter_all()
  │
  ├─ _resolve_state_arbiter_world_fact()   → world_canon 事实（优先级 1.0）
  ├─ _resolve_state_arbiter_style_fact()   → style_canon 风格（优先级 0.85）
  ├─ _resolve_state_arbiter_episodic_fact() → episodic 情节（优先级 0.75）
  └─ _resolve_state_arbiter_bond_fact()    → bond 关系（优先级 0.65）
```

#### 步骤 3c：RecallPackBuilder

```python
_build_recall_pack()
  │
  ├─ RecallPackBuilder.build(recall_results, task_type="chat")
  ├─ slot 分配：style_canon / world_canon / emotion_recall
  ├─ token 预算控制
  └─ 输出：rewritten_items + recall_slot_text + summary
```

#### 步骤 3e：build_chat_runtime_input

```python
build_chat_runtime_input(
    render_target_lang="zh",    # ★强制中文★
    scene_type="GATEWAY",
    ...
)
```

#### 步骤 ⑧：_build_stream_chat_prompt ★Prompt 完整构建★

```python
_build_stream_chat_prompt(runtime_input)
  │
  ├─ build_chat_prompt_runtime_payload(runtime_input)
  │   │
  │   ├─ build_chat_generation_prompt_payload()
  │   │   ├─ emotion_context: 完整 12 字段（P0修复后）
  │   │   ├─ build_chat_context_pack() → context_pack
  │   │   │   ├─ emotion_pulse (mood_state/emotion_tag/style_hint)
  │   │   │   ├─ emotion_recall (情感召回文本)
  │   │   │   ├─ memory_emotion_hint
  │   │   │   ├─ bond_texture (亲密温度/裂痕状态)
  │   │   │   └─ authoritative/retrieval/style_pack/constraints
  │   │   └─ prompt_context_observability
  │   │
  │   ├─ runtime_payload 顶层注入（P0修复后）：
  │   │   ├─ emotion_vector (VAD 三维)
  │   │   ├─ emotion_echo (共情响应)
  │   │   ├─ inner_monologue (内心独白)
  │   │   ├─ user_emotion_profile (用户画像)
  │   │   ├─ emotion_timeline (情感轨迹摘要)
  │   │   ├─ persona_expression_hint (P1修复: PersonaExpressionAdapter)
  │   │   ├─ self_reflection_pulse (P1修复: SelfReflection 概率触发)
  │   │   └─ bond_narrative (P1修复: BondNarrativeLayer)
  │   │
  │   └─ style_bindings (根据 render_target_lang="zh" 选择中文 canon)
  │
  ├─ adjust_gen_params_for_emotion(gen_params, emotion_context)
  │   └─ 根据情感状态微调 temperature/top_p
  │
  └─ render_node_generation_prompt(CHAT_DIRECT_NODE_ID, runtime_payload)
      │
      ├─ _build_node_role_instruction(render_target_lang="zh")
      │   └─ "Goal: generate the final user-facing reply directly
      │       in concise spoken Simplified Chinese."
      │
      ├─ CN_STYLE_BOOST_BINDING (zh 专有)
      │   └─ "Write naturally spoken Simplified Chinese;
      │       avoid formal support-style wording."
      │
      ├─ EMOTION_BINDING 区块：
      │   ├─ mood_state / emotion_tag / emotion_intensity
      │   ├─ inner_monologue (内心独白)
      │   ├─ bond_stage / bond_trend / texture_hint
      │   ├─ emotion_timeline (dominant/trend/entry_count)
      │   ├─ emotion_echo (tag/hint/intensity/bond_depth)
      │   ├─ VAD 连续体 (valence/arousal/dominance)
      │   ├─ inferred_emotion (user/natsume/reason/confidence)
      │   └─ user_emotion_profile (长期蒸馏画像)
      │
      ├─ PERSONA_EXPRESSION_HINT 区块
      │   └─ style_hint / tone / echo_weight_modifier / guidance
      │
      ├─ SELF_REFLECTION_PULSE 区块 (概率出现)
      │   └─ self_awareness / mood_awareness / growth_note
      │
      ├─ BOND_NARRATIVE 区块
      │   └─ shared_memories / milestone_count / narrative_style
      │
      └─ persona_utterance 证据过滤
          └─ render_target_lang="zh" → 仅保留 zh/cn/chinese 标签
```

---

### 2.5 后置任务（三路并行）

```python
_run_post_generation_tasks()
  │
  ├─ _run_airlock()      → Airlock Ingress 检疫（分类 + 摘要）
  ├─ _run_episodic()     → persist_chat_episodic_memory（情节记忆写入）
  └─ _run_bond()         → BondDynamicsWriter.classify_interaction
                            → bond_update_queue.enqueue → 直写回退
```

### 2.6 MainChain 后置节点（路径 B 专有）

| 顺序 | 节点 | 超时 | 职责 | 跳过条件 |
|------|------|------|------|---------|
| 6 | QAContractNode | 3.0s | 输出合规（长度/安全词/情感一致性） | `final_response` 为空 → `SKIPPED_NO_OUTPUT` |
| 7 | EpisodicStoreNode | 2.0s | 情节记忆写入（MemoryGuard 宪法校验） | 无响应/MemoryGuard 阻止/dream 内容 → SKIPPED |
| 8 | AuditNode | 1.0s | ledger 审计快照 | 降级不阻断 |
| 9 | CanonProposalNode | 2.0s | Canon 提案检测和提交 | 无 `[CANON_UPDATE]` 标记 → `NO_CANON_SIGNAL` |
| 10 | AirlockSanitizerNode | 1.0s | 出口净化（null 字节清除） | 无响应 → SKIPPED |

---

## 三、情感层完整接入分析

### 3.1 情感层数据流

```
用户消息到达
    │
    ▼
resolve_chat_emotion_context(request_body)  [chat_handler.py L148]
    │  从 request_body 提取 mood_state/emotion_tag
    ▼
normalize_chat_emotion_context(raw)  [emotion_context.py L144]
    │
    ├─ normalize_hard_state → 心情状态归一化
    ├─ map_emotion_tag_to_vad → 16种标签 → VAD 三维 (Phase 2)
    ├─ _update_persistent_emotion → 时间衰减 + 混合 (Phase 1)
    ├─ _global_emotion_timeline.append → 情感时间线追踪
    ├─ compute_emotion_echo → 情感回声/共情 (Phase 5)
    ├─ evaluate_emotion_triggers → 主动触发器
    ├─ update_user_emotion_profile → 用户画像蒸馏 (方向F)
    └─ _vector_to_inner_monologue → 内心独白 (Phase 3)
    │
    ▼
输出 12 字段 emotion_context:
  mood_state, emotion_tag, emotion_range_hint, emotion_intensity,
  allowed_emotion_tags, emotion_ts_ms, vad_vector, emotion_echo,
  triggered_emotions, inner_monologue, user_emotion_profile, _prev_emotion_tag
    │
    ├─► build_chat_generation_prompt_payload()
    │     └─ generation_payload["emotion_context"] = 完整 12 字段
    │
    ├─► build_chat_prompt_runtime_payload()
    │     └─ runtime_payload 顶层注入:
    │         emotion_vector, emotion_echo, inner_monologue,
    │         user_emotion_profile, emotion_timeline,
    │         persona_expression_hint, self_reflection_pulse,
    │         bond_narrative
    │
    └─► render_node_generation_prompt()
          └─ 渲染到 EMOTION_BINDING 区块 + 各情感子系统区块
```

### 3.2 情感子系统接入状态（修复后）

| # | 子系统 | 状态 | 数据流路径 |
|---|--------|------|-----------|
| 1 | EmotionVector VAD | ✅ | emotion_context → runtime_payload["emotion_vector"] → EMOTION_BINDING VAD 区块 |
| 2 | EmotionTimeline | ✅ | 全局单例 → runtime_payload["emotion_timeline"] → EMOTION_BINDING timeline 区块 |
| 3 | EmotionEcho | ✅ | emotion_context → runtime_payload["emotion_echo"] → EMOTION_BINDING echo 区块 |
| 4 | InnerMonologue | ✅ | emotion_context → runtime_payload["inner_monologue"] → EMOTION_BINDING inner_monologue |
| 5 | UserEmotionProfile | ✅ | emotion_context → runtime_payload["user_emotion_profile"] → EMOTION_BINDING profile |
| 6 | PersonaExpression | ✅ | PersonaExpressionAdapter.adapt() → runtime_payload["persona_expression_hint"] → 独立区块 |
| 7 | SelfReflection | ✅ | SelfReflection.maybe_generate() → runtime_payload["self_reflection_pulse"] → 独立区块 |
| 8 | BondNarrative | ✅ | BondNarrativeLayer.build_narrative() → runtime_payload["bond_narrative"] → 独立区块 |
| 9 | InferredEmotion | ⚠️ | LlmGatewayNode 后置产出 → ctx.metadata["inferred_emotion"]（下一轮生效） |
| 10 | EmotionAgent | ✅ | needs_deep_emotion_analysis → Supervisor → emotion_agent_handler → ReAct 循环 |

---

## 四、Agent 层分析

### 4.1 Agent 注册表

| Agent ID | Handler | 触发条件 | 模式 |
|----------|---------|---------|------|
| `chat_agent` | `chat_agent_handler.py` | SCENE_CHAT + Supervised 图 | 透传 chat_direct |
| `code_agent` | `code_agent_handler.py` | SCENE_WORK_CODE | ReAct |
| `translate_agent` | `translate_agent_handler.py` | SCENE_WORK_TRANSLATE | ReAct |
| `intent_agent` | `intent_agent_handler.py` + `intent_agent_entry_handler.py` | SCENE_EXEC_INTENT | 子图 ReAct |
| `retrieval_agent` | `retrieval_agent_handler.py` | SCENE_WORK_RETRIEVAL | ReAct |
| `emotion_agent` | `emotion_agent_handler.py` | needs_deep_emotion_analysis=True | ReAct (只读) |
| `judge_agent` | `judge_agent_handler.py` | Supervised 图 + need_judge=True | 裁判 |

### 4.2 Gateway 中文链路与 Agent 的关系

**通常情况**：Gateway 中文链路走 **SCENE_CHAT 快速路径**，不经过 Supervisor/Agent 分派。

**例外情况**：
1. 子场景识别结果为非 CHAT（如 WORK_CODE）→ 委托 `_complex_scene_sse_generator` → 走 Supervised 图 → Supervisor 分派 Agent
2. Phase 6 情感推理标记 `needs_deep_emotion_analysis=True` → Supervisor 分派 `emotion_agent`

### 4.3 EmotionAgent 详解

```
触发条件：
  LlmGatewayNode.Phase6 → _infer_confidence < 0.5 或 cascade_level == 3
    → ctx.metadata["needs_deep_emotion_analysis"] = True
    → Supervisor 路由时：if _needs_deep → selected_agent = AGENT_ID_EMOTION

执行流程：
  emotion_agent_handler()
    │
    ├─ 构建 system_prompt（含当前情感标签/心情状态/回声/用户画像）
    │
    ├─ get_tools_for_agent(AGENT_ID_EMOTION, allow_side_effects=False)
    │   可用工具（只读）：
    │   ├─ emotion_history_search: 检索历史情感记忆
    │   ├─ emotion_timeline_query: 查询情感变化轨迹
    │   └─ search_knowledge: 知识库检索
    │
    ├─ run_react_loop(max_steps=3, allow_side_effects=False)
    │   Observe → Think → Act → Observe → ... → final_answer
    │
    └─ 产出 AgentArtifact:
        ├─ recommended_emotion_tag
        ├─ emotion_reasoning
        └─ artifact_ref
```

---

## 五、Graph 编排层分析

### 5.1 MainChain 节点顺序

```python
_NODE_FACTORIES = (
    AirlockQuarantineNode,    # 入口隔离
    GovernorNode,             # 治理门控
    GraphExecutorNode,        # 图执行模式
    RecallFusionNode,         # 七路召回
    LlmGatewayNode,          # LLM 网关 + 情感追踪
    QAContractNode,           # 输出合规
    EpisodicStoreNode,        # 记忆存储
    AuditNode,                # 审计
    CanonProposalNode,        # Canon 提案
    AirlockSanitizerNode,     # 出口净化
)
```

### 5.2 run_as_sse_adapter 编排模式

```
前置节点 → 委托 SSE 生成器 → 后置节点

pre_node_order = (
    "airlock_quarantine",     # 输入消毒
    "governor",               # R1-R7 门控
    "graph_executor",         # 执行模式判定
    "recall_fusion",          # 七路召回 ← 预召回结果传给 Gateway Handler
    "llm_gateway",            # 路由矩阵 + EmotionTimeline + 情感推理
)

    ↓ 委托 _unified_chat_sse_generator → SceneRouter → Gateway Handler ↓

post_node_order = (
    "qa_contract",            # 输出合规
    "episodic_store",         # 记忆存储
    "audit",                  # 审计快照
    "canon_proposal",         # Canon 提案
    "airlock_sanitizer",      # 出口净化
)
```

### 5.3 Supervisor 分派逻辑

**文件**：`host_brain/app/handlers/supervisor_dispatch_handler.py`

```python
场景 → 默认 Agent 映射：
  SCENE_CHAT           → chat_agent    (risk=SAFE,     judge=False)
  SCENE_WORK_CODE      → code_agent    (risk=GUARDED,  judge=True)
  SCENE_WORK_TRANSLATE → translate_agent (risk=SAFE,    judge=True)
  SCENE_EXEC_INTENT    → intent_agent  (risk=STRICT,   judge=True)
  SCENE_VISION_UI      → intent_agent  (risk=STRICT,   judge=True)

特殊逻辑：
  needs_deep_emotion_analysis=True + rule_agent==CHAT
    → 覆盖为 emotion_agent (judge=False)

模式选择：
  SUPERVISOR_MODE=rule  → 纯规则路由（默认）
  SUPERVISOR_MODE=llm   → LLM 提案 + handler 校验 + allowlist 验证
```

---

## 六、render_target_lang="zh" 三重保障

| 设定位置 | 文件 | 行号 | 机制 |
|---------|------|------|------|
| 路径B 入口 | `main_chain.py` `run_as_sse_adapter()` | L2999-3000 | `if resolved_scene == "GATEWAY": ctx.metadata["render_target_lang"] = "zh"` |
| 路径B 降级 | `main_chain.py` `run_as_sse_adapter()` | L3004-3007 | `except: "zh" if GATEWAY else "ja"` |
| Gateway Handler | `gateway_orchestrator_handler.py` L741 | L741 | `render_target_lang="zh"` (硬编码) |

**render_target_lang="zh" 的下游影响：**

1. **角色指令**：`"generate the final user-facing reply directly in concise spoken Simplified Chinese"`
2. **语言规则**：`"Reply in concise spoken Simplified Chinese."`
3. **CN_STYLE_BOOST**：风格增强绑定，优先级 safety/facts > style canon > generic assistant tone
4. **人格话语过滤**：仅检索 zh/cn/chinese 标签的证据
5. **召回路由筛选**：只走 selector="zh" 和 selector="*" 的路由（今日修复）
6. **Style Canon 选择**：使用 `HOST_STYLE_RENDER_CN_CANON_ID` 或默认 `natsume_ai_cn`

---

## 七、条件执行/跳过/降级机制汇总

| 节点/步骤 | 跳过/降级条件 | 行为 |
|----------|--------------|------|
| Governor | `adapter_mode="sse"` → 跳过真实鉴权 | 复用预鉴权结果 |
| Governor | `_pre_approved_governor_result` → 跳过重复鉴权 | 直接复用 |
| 子场景路由 | `sub_scene != SCENE_CHAT` → 委托复杂场景 | 跳出 Gateway 主链路 |
| RecallFusion | 熔断器 OPEN | `RECALL_CIRCUIT_OPEN`，跳过召回 |
| RecallFusion | 异常 | `DEGRADED_FALLBACK`，空证据 |
| StateArbiter | 查询失败 | 警告日志，`bond_state_summary = {}` |
| RecallPackBuilder | 异常 | `status="DEGRADED"`，保留原始证据 |
| 情感参数注入 | 异常 | 保留原 gen_params |
| LLM 运行时 | `available=False` | REJECT "服务暂不可用" |
| LLM 流式生成 | 异常 | 降级回复 "抱歉，处理出了点问题" |
| 后置三路任务 | 各自独立异常 | `status="DEGRADED"`，不阻断 |
| TTS | `HOST_GATEWAY_TTS_ENABLED=False`(默认) | SKIPPED |
| QAContract | 无输出 | `SKIPPED_NO_OUTPUT` |
| EpisodicStore | MemoryGuard 阻止 / dream 内容 | `SKIPPED_MEMORY_GUARD` |
| CanonProposal | 无 Canon 更新信号 | `NO_CANON_SIGNAL` |
| AirlockSanitizer | 无响应 | `SKIPPED_NO_RESPONSE` |
| PersonaExpression | 依赖不可用 | 静默降级，不注入 |
| SelfReflection | 未到触发轮次 | 不注入 |
| BondNarrative | SharedMemoryIndex 不可用 | 静默降级，不注入 |

---

## 八、关键文件索引

| 文件 | 职责 |
|------|------|
| `host_brain/app/fastapi_app.py` | HTTP 路由入口 |
| `host_brain/app/chat_handler.py` | 统一 SSE 编排 + Governor 鉴权 |
| `host_brain/app/graph/main_chain.py` | MainChain 10 节点编排 + SceneRouter + SSE 适配层 |
| `host_brain/app/handlers/gateway_orchestrator_handler.py` | Gateway 薄适配层（委托 `top_agent.py`） |
| `host_brain/app/handlers/supervisor_dispatch_handler.py` | Supervisor Agent 分派 |
| `host_brain/app/handlers/emotion_agent_handler.py` | 深度情感分析 Agent |
| `host_brain/app/chat_stream_prompt_helpers.py` | 流式结构化输出的可见文本提取 |
| `host_brain/app/chat_context_builder.py` | context_pack + prompt_payload 构建 |
| `host_brain/app/prompt_builder.py` | Prompt 渲染 + 语言推断 + 角色指令 |
| `host_brain/app/emotion_context.py` | 情感状态归一化 + VAD + 时间线 + 回声 |
| `host_brain/retrieval/pipeline.py` | 检索管线 (embed → recall → rerank → evidence) |
| `host_brain/retrieval/orchestrator.py` | 检索编排器 (并发 + 缓存) |
| `host_brain/retrieval/recall_fusion.py` | 多路召回 + fusion |
| `host_brain/retrieval/recall_fusion_config.py` | 路由解析 + 语种筛选 |
| `host_brain/persona/expression_adapter.py` | 人格化表达适配器 |
| `host_brain/persona/self_reflection.py` | 自省控制器 |
| `host_brain/bond/bond_narrative.py` | 关系叙事层 |
