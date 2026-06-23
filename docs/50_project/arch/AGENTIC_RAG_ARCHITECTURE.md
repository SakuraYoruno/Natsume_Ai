受众：MASTER
标题：检索与召回架构（当前代码实况；旧 Agentic RAG 文件名兼容保留）

版本：v3.0
日期：2026-06-23

---

定位：本文描述当前代码里的检索与召回主链。文件名中的 `AGENTIC_RAG` 是历史兼容名；旧的 `RetrievalOrchestrator / RetrievalPipeline / QueryPlanner / AgenticRetrievalLoop` 栈不再代表当前运行链路。

若与旧文档中的“七路全量召回 + Agentic RAG 多轮规划 + Graph 注入点”口径冲突，以本文和代码为准。

## 0. 代码证据入口

- `host_brain/app/chat_retrieval_helpers.py`
- `host_brain/persona/surface_memory_recall.py`
- `host_brain/retrieval/recall_fusion.py`
- `host_brain/retrieval/recall_fusion_config.py`
- `host_brain/retrieval/recall_fusion_stores.py`
- `host_brain/memory/life_memory.py`
- `host_brain/memory/episodic.py`
- `host_brain/memory/canon_world_store.py`
- `host_brain/core/prompt_context.py`
- `host_brain/app/chat_generation_payload.py`
- `host_brain/app/observability_persist.py`

---

## 一、当前检索入口

当前 normal chat 的检索入口不在旧 `main_chain.py`，也不在旧 Agentic RAG orchestrator。真实链路是：

```text
/v1/chat
  -> chat_handler
  -> cortex_orchestrator
  -> agent_runtime_shared._cortex_orchestrator_sse_generator_impl
  -> chat_retrieval_helpers._run_retrieval
  -> RecallFusionServiceContract.recall(...)
  -> surface_memory_recall / packet compiler
  -> SurfaceMainChain facts / memory packet
```

`surface_memory_recall.py` 负责把召回候选压缩成 `MemoryRecallPacket`，只允许 facts、style_anchor、relationship_clues、must_not_do、confidence、evidence_refs 等可控字段进入 SurfaceMainChain。

---

## 二、当前 Surface Recall Routes

当前 surface 主链使用的召回 route 是：

| route | 当前职责 | 事实效力 |
|---|---|---|
| `life_memory_collection` | LifeMemoryStore collection / entity 线索 | 记忆事实候选，需带 provenance |
| `life_episode` | life episode + evidence 召回 | 当前记忆 SSOT 的主事实来源 |
| `semantic_memory` | 由生活记忆蒸馏出的长期摘要 | 衍生事实，需回溯 source refs |
| `episodic_vector` | Chroma / episodic 向量兼容召回 | 线索层，不是 SSOT |
| `sqlite_ledger` | 审计账本查询 | 可回放事实线索，按表语义判断 |
| `neo4j_bond` | 关系图谱线索 | 关系状态线索，不直接写最终文本 |

`world_canon` 与 `artifact_vault` 在通用 store/config 能力里仍可作为扩展存在，但不是当前 surface 主链的默认 route。WorldCanon 的当前主要入口是 prompt authoritative slot。

---

## 三、LifeMemoryStore 是记忆 SSOT

当前记忆事实主源是 `host_brain/memory/life_memory.py`：

```text
LifeMemoryStore.write_chat_episode(...)
LifeMemoryStore.write_background_episode(...)
LifeMemoryStore.recall_episode_packet(...)
LifeMemoryConsolidator.distill_semantic_memories(...)
```

SQLite 是 source of truth；vector、semantic、episodic graph 都是衍生层或兼容层。向量命中不能单独证明“发生过什么”，必须回到 life episode / evidence / ledger provenance。

聊天落库路径大致是：

```text
SurfaceMainChain COMMIT
  -> ReplyEnvelope
  -> post process
  -> LifeMemoryStore / SQLiteLedger
  -> optional episodic vector write
```

ACK、任务状态、失败降级和不可发送输出不得写入人格记忆。

---

## 四、WorldCanon 的真实入口

WorldCanon SSOT 是 `host_brain/memory/canon_world_store.py::WorldCanonStore`，默认文件根为 `host_brain/canon/world`。

它进入 prompt 的主要路径是：

```text
core.prompt_context.build_world_canon_slot(...)
  -> WorldCanonStore.scan_documents()
  -> authoritative slot
  -> kind = world_canon
  -> unsafe_as_fact = True
  -> reference_mode = citation_only
```

WorldCanon 只解决系统术语、架构设定、关键不变量与必要世界观条目的内容一致性。它不能证明“已执行”“已保存”“用户已确认”，不得替代 audit facts、LifeMemoryStore 或 Governor 裁决。

当前源码没有 `host_brain/canon/style/`，也没有 `host_brain/memory/canon_style_store.py`。不要把 StyleCanon 写成召回 route、StateArbiter 优先级或事实源。

---

## 五、当前 RecallFusion 契约

`RecallFusionServiceContract.recall(...)` 是当前多路召回的统一契约。它负责：

- 接收 query、intent、session/person、top_k、route 配置等输入；
- 调用 `recall_fusion_stores.py` 中的 store adapter；
- 做候选归一化、排序、截断与 route 级状态汇总；
- 写入 `retrieval_event_log`，用于证明“召回运行过”。

`retrieval_event_log` 只能证明召回执行、route 状态和候选预览，不能单独证明候选内容为真。

---

## 六、与 SurfaceMainChain 的边界

召回层不直接写最终回复。当前边界是：

```text
RecallFusion result
  -> surface_memory_recall builds MemoryRecallPacket
  -> surface_packet_compiler compiles facts / must_not_do / style anchors
  -> SurfaceMainChain generates selected_text
  -> ReplyEnvelope final_reply
```

因此：

1. 检索层提供候选事实和证据引用，不负责最终表达。
2. SurfaceMainChain 是 normal chat 文本最终作者。
3. ReplyEnvelope 是可发送、可持久化、可缓存的收口契约。
4. 后效写入发生在 COMMIT 后，不能让召回候选越权变成已执行事实。

---

## 七、旧 Agentic RAG 栈的当前状态

以下说法不再代表当前代码事实：

| 旧口径 | 当前事实 |
|---|---|
| `RetrievalOrchestrator.arun` 是当前检索入口 | 当前入口是 `chat_retrieval_helpers._run_retrieval` + `RecallFusionServiceContract.recall(...)` |
| `RetrievalPipelineContract.run()` 四阶段检索 | 当前没有这条主链；召回由 RecallFusion 一次契约收口 |
| `QueryPlanner` 决定 store map | 当前由 surface recall 上下文、intent 与 recall_fusion_config/store adapter 决定 |
| `AgenticRetrievalLoop` 多轮 agent 检索 | 当前 surface recall 不运行 agent loop |
| `EvidenceEvaluator` 决定是否扩展 stores | 当前候选过滤与 prompt 注入由 recall fusion / surface memory recall / packet compiler 控制 |
| `style_canon` 是检索 route | 当前源码没有独立 StyleCanon store/route |

---

## 八、不变量

1. LifeMemoryStore + SQLiteLedger 是当前记忆事实主源；vector / semantic / graph 是衍生层。
2. RecallFusion 只提供候选与 evidence refs，不直接生成最终文本。
3. WorldCanon 通过 authoritative slot 进入 prompt，不是执行事实。
4. SurfaceMainChain 与 ReplyEnvelope 才是 normal chat 的最终表达与发送收口。
5. 旧 Agentic RAG 文件、阶段方案或 release 留痕不得作为当前执行依据。
