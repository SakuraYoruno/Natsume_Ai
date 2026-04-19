受众：MASTER
标题：host_brain/tools/ + memory/ 层接口契约（当前代码实况）

版本：v2.0
日期：2026-04-19
归属：docs/15_theory/

---

定位：本文描述当前代码里的 `host_brain/tools/` 与 `host_brain/memory/` 层，而不是早期只覆盖“ToolCatalog + Canon/Distillation”骨架的简化口径。
若与旧文档冲突，以本文和代码为准。

## 0. 代码证据入口

- `host_brain/tools/catalog.py`
- `host_brain/tools/broker.py`
- `host_brain/tools/selector.py`
- `host_brain/tools/mcp_adapter.py`
- `host_brain/tools/browser_automation.py`
- `host_brain/tools/host_command_executor.py`
- `host_brain/tools/secret_vault.py`
- `host_brain/tools/file_forensics.py`
- `host_brain/tools/file_organizer.py`
- `host_brain/tools/system_monitor.py`
- `host_brain/memory/canon_style_store.py`
- `host_brain/memory/canon_world_store.py`
- `host_brain/memory/canon_sync.py`
- `host_brain/memory/episodic.py`
- `host_brain/memory/context_compressor.py`
- `host_brain/memory/distillation.py`
- `host_brain/memory/memory_worth_evaluator.py`
- `host_brain/memory/consolidation_trigger.py`
- `host_brain/memory/episodic_memory_graph.py`

---

## 一、当前层定位

### 1.1 Tools 层

当前 `host_brain/tools/` 不是只有“元数据注册 + 执行门控”两层，还包括实际的 Host 工具实现模块。

更准确的理解是：

```text
tools/
  = Tool metadata contract
  + governed execution entry
  + MCP adapter
  + concrete host tools
```

### 1.2 Memory 层

当前 `host_brain/memory/` 也不再只是：

- Canon
- Episodic
- Distillation

而是已经扩展到：

- Canon SSOT 与索引同步
- Episodic write/merge/delete/recall
- Context compression
- Distillation
- 记忆写入价值评估
- 主动整合触发
- Episodic graph / topic chain

---

## 二、Tools 层当前契约

### 2.1 当前模块分组

| 类型 | 模块 | 当前职责 |
|---|---|---|
| 元数据契约 | `catalog.py` | `ToolMeta`、`ToolCatalog`、`compute_tool_visibility_hash()` |
| 统一执行入口 | `broker.py` | `ToolBroker.execute()`、WRITE token 校验、审计写入 |
| 可见性筛选 | `selector.py` | Top-K 工具元数据选择，默认隐藏 WRITE 工具 |
| MCP 适配 | `mcp_adapter.py` | provider 工具同步、Airlock 出站净化、经 ToolBroker 执行 |
| 具体工具实现 | `browser_automation.py` 等 | 实际 Host 能力实现 |

### 2.2 catalog.py：当前 SSOT

`catalog.py` 当前仍是 canonical `ToolMeta` 定义所在：

```python
ToolMeta(
    id: str,
    name: str,
    desc: str,
    schema_hash: str,
    side_effect_level: str,
    auth_level: str,
    domain_tags: tuple[str, ...],
    version: str,
)
```

当前规则仍包括：

- `schema_hash` 必须以 `sha256:` 开头
- `side_effect_level` 只允许 `READ` / `WRITE`
- `auth_level` 只允许 `NONE` / `APPROVAL` / `CONFIRM`
- `LOW / MEDIUM / HIGH` 会被映射到上述 auth level
- `domain_tags` 必须非空
- 未知字段会直接报错

### 2.3 ToolCatalog

当前 `ToolCatalog` 提供：

- `register()`
- `get()`
- `list_all()`
- `export_meta(include_write=True)`

特点：

- 以 `tool_id` 为键
- 覆盖式注册
- 导出时可按 `include_write` 过滤 WRITE 工具

### 2.4 工具可见性哈希

`compute_tool_visibility_hash()` 当前会：

- 对 metadata 做 `normalize_tool_meta()`
- 按 `(id, version)` 排序
- 计算稳定 `sha256`

用于当前上下文打包与观测侧追踪“这轮模型能看到哪批工具”。

---

## 三、ToolBroker 当前契约

### 3.1 当前定位

`broker.py` 仍是当前受管工具执行的单一合法入口：

```text
catalog lookup
  -> handler lookup
  -> WRITE token validate
  -> inject emotion context defaults
  -> execute handler
  -> log_skill_exec
```

### 3.2 ToolExecutionResult

当前返回结构：

```python
ToolExecutionResult(
    status: str,
    result_code: str,
    tool_id: str,
    intent_id: str,
    evidence_ref: str | None,
    detail: dict[str, Any],
    output: Any = None,
)
```

### 3.3 当前 result_code

当前代码里的主要结果码：

- `TOOL_NOT_FOUND`
- `TOOL_HANDLER_MISSING`
- `TOKEN_INVALID`
- `CONFIRM_REQUIRED`
- `TOOL_EXECUTED`
- `TOOL_EXECUTION_ERROR`

### 3.4 WRITE 门控

当前规则是：

- `meta.side_effect_level == "WRITE"` 时一定走 `token_service.validate_execution_tokens()`
- `meta.auth_level == "CONFIRM"` 时要求 confirm token
- 验证失败直接拒绝，不执行 handler

### 3.5 情感上下文默认注入

当前 `ToolBroker.execute()` 若调用方没传，会默认注入：

- `emotion_tag = "NEUTRAL"`
- `mood_state = "steady"`

所以工具 handler 当前可以稳定读取情感上下文，即使上游未显式补全。

### 3.6 审计写入

每次执行最终写入的是：

- `ledger.log_skill_exec(...)`

并不是单独的 `log_tool_exec`。

写入字段包括：

- `request_id`
- `thread_id`
- `turn_id`
- `intent_id`
- `skill_name`（这里写的是 `tool_id`）
- `approval_token_ref`
- `confirm_token_ref`
- `result_status`
- `result_code`
- `result_digest`
- `evidence_ref`
- `undo_token_ref`
- `ts_ms`

---

## 四、selector.py 当前契约

`selector.py` 当前的关键现实是：

- 默认 `include_write=False`
- Top-K 默认 `k=6`
- 可按 `required_tags` 过滤
- 排序使用 query token 与 `name/desc/domain_tags` 的轻量匹配

这意味着当前模型默认更容易看到 READ 工具，而不是所有工具全量暴露。

---

## 五、MCPAdapter 当前契约

### 5.1 当前不是薄映射器

`mcp_adapter.py` 当前除了同步 provider metadata，还负责：

- WRITE 校验前置要求
- Airlock 出站净化
- evidence_ref 校验
- 经 ToolBroker 单入口执行

### 5.2 当前关键接口

- `sync_provider()`
- `sync_provider_tools()`
- `resolve_tool_id()`
- `call_tool()`
- `execute_mcp_tool()`
- `get_provider_tools()`

### 5.3 当前 ToolMeta 视图有两层

需要注意：

- `catalog.py` 里有 canonical `ToolMeta`
- `mcp_adapter.py` 里还有一份面向 provider 视图的 `ToolMeta`

它们不是同一个数据类，但目的不同：

- 前者给 ToolCatalog / Selector / Broker 用
- 后者给 MCP provider 同步结果回传与缓存用

### 5.4 execute_mcp_tool() 当前流程

```text
resolve mapped tool_id
  -> 检查 catalog 中是否存在
  -> WRITE 工具要求 approval_token_ref
  -> _airlock_sanitize(args)
  -> broker.execute(...)
  -> 若 WRITE 成功但无 evidence_ref，则报错
```

### 5.5 当前 Airlock 约束

出站净化当前会移除敏感字段，例如：

- `hard_state`
- `constitution`
- `rule_trace`
- `approval_token`
- `confirm_token`
- `undo_token`
- `secret`
- `password`
- `api_key`

因此现在 MCP 工具适配层本身就带有强制最小化出站约束。

---

## 六、Concrete Tools 的当前现实

旧文档只描述了 contract 模块，但当前 `tools/` 目录里真实还有这些 Host 能力模块：

- `browser_automation.py`
- `host_command_executor.py`
- `secret_vault.py`
- `file_forensics.py`
- `file_organizer.py`
- `system_monitor.py`

也就是说，`tools/` 现在不只是元数据层，而是“契约 + 具体执行能力”并存。

---

## 七、Memory 层当前契约

### 7.1 当前主模块分组

| 类型 | 模块 | 当前职责 |
|---|---|---|
| Canon SSOT | `canon_style_store.py`, `canon_world_store.py` | 风格/世界观 Canon 单一真相源 |
| Canon 同步 | `canon_sync.py`, `canon_vector_auto_sync.py`, `canon_vector_projection.py` | 文件、索引、向量同步 |
| Episodic | `episodic.py` | 记忆写入、合并、删除、召回 |
| 压缩/蒸馏 | `context_compressor.py`, `distillation.py`, `long_term_distiller.py` | 多轮压缩与长期语义蒸馏 |
| 质量/触发 | `memory_worth_evaluator.py`, `consolidation_trigger.py` | 写入价值评估与主动蒸馏触发 |
| 图谱增强 | `episodic_memory_graph.py` | 主题链与 graph walk |

---

## 八、Canon 当前契约

### 8.1 StyleCanonStore

`canon_style_store.py` 当前口径已经不是多散文件随意拼装，而是：

- 以单个 v5 canonical profile file 为主
- 当前核心文件名：`style_profile_v5.json`
- `STYLE_CANON_KEY_MAP` 当前包含中日双通道 key

它仍提供：

- `get(canon_id)`
- `list_entries()`
- `upsert(...)`

并且会触发：

- `trigger_canon_vector_auto_sync(...)`

### 8.2 WorldCanonStore

`canon_world_store.py` 当前是：

- versioned
- auditable
- single-writer

关键现实：

- `upsert_document(...)` 要求 `proposal_id`
- `body` 不能断言执行事实
- 支持跨进程 file lock（可选依赖）
- 写入后同样会触发 Canon vector auto sync

### 8.3 Canon 同步

`canon_sync.py` 当前两个关键函数仍是真实主入口：

- `sync_world_canon_index(...)`
- `detect_world_version_drift(...)`

当前 drift code：

- `WORLD_CANON_VERSION_DRIFT`
- `WORLD_CANON_NOT_INDEXED`
- `WORLD_CANON_FILE_MISSING`
- `WORLD_CANON_INVALID_DOC`

同步时还会写：

- `upsert_world_canon_doc`
- `log_canon_proposal`
- `log_canon_change`

---

## 九、EpisodicStore 当前契约

### 9.1 当前定位

`episodic.py` 现在不是一个简单 KV 记忆表，而是带：

- evidence gate
- Chroma 向量存储
- lifecycle audit
- 情感标注
- suppression / distortion

的记忆存储器。

### 9.2 EpisodicRecord 当前字段

当前重要字段包括：

- `memory_id`
- `text`
- `ts_ms`
- `text_hash`
- `decay_profile_id`
- `evidence_refs`
- `visibility`
- `embedding_model_id`
- `merged_into`
- `deleted_at`
- `emotion_tag`
- `emotion_valence`
- `emotion_arousal`
- `emotion_color_tag`
- `suppression_flag`
- `distortion_level`

### 9.3 写入门禁

`write_memory()` / `merge_memories()` 当前都要求：

- `evidence_refs` 非空

否则返回：

- `MEMORY_EVIDENCE_REQUIRED`

### 9.4 recall() 当前行为

`recall()` 现在不仅做相似度检索，还支持：

- `emotion_context`
- `emotion_boost_factor`
- `apply_decay`
- `apply_suppression`
- `apply_distortion`

当前现实包括：

- `suppression_flag` 会把高冲突/痛苦记忆降权
- `distortion_level` 会让细节模糊记忆轻微降权
- `emotion_context` 匹配时会做情感 boost

因此 episodic recall 已经带有当前“人性化记忆”逻辑，而不再是纯向量 top-k。

---

## 十、Context Compressor 与 Distillation

### 10.1 ContextCompressor

`context_compressor.py` 当前支持三种策略：

- `sliding_window`
- `summary`
- `hybrid`

主要结构：

- `CompressedContext`
- `EvidenceCandidate`
- `EvidencePack`

而且：

- `summary / hybrid` 会触发压缩前静默保存
- EvidencePack 可叠加 episodic hits

### 10.2 MemoryDistillationEngine

`distillation.py` 当前主要实现：

- `MemoryDistillationEngine.distill_top_k(...)`

它会：

- 从 `EpisodicStore.recall()` 召回 top-k
- 优先走外部 LLM summary client
- 不可用时回退到 deterministic merge
- 根据 `source_hash` 做幂等保护
- 最终写入 semantic memory ledger

---

## 十一、Worth / Consolidation / Episodic Graph

### 11.1 MemoryWorthEvaluator

`memory_worth_evaluator.py` 当前是纯规则、低延迟的写入价值评估器。

决策值：

- `write`
- `skip`
- `write_light`

评估因素包括：

- 寒暄/简单应答过滤
- 用户/回复长度
- 情感关键词
- 新信息信号
- 深度对话信号
- 多轮加分

### 11.2 ConsolidationTrigger

`consolidation_trigger.py` 当前提供主动蒸馏触发，不只依赖定时任务。

触发类型：

- `volume`
- `topic`
- `scheduled`

它维护：

- 24h 写入量计数
- 话题频次计数
- cooldown

### 11.3 EpisodicMemoryGraph

`episodic_memory_graph.py` 当前实现轻量主题链：

- 新记忆写入时自动聚类
- 支持 graph walk 召回上下文
- 基于关键词 overlap + 时间窗口

所以 memory 层现在已经显式包含“主题线记忆图”，不只是扁平 episodic list。

---

## 十二、当前依赖关系应如何理解

当前更准确的理解是：

```text
tools/
  = metadata + broker + selector + mcp adapter + concrete host tools

memory/
  = canon + episodic + compression + distillation + worth + consolidation + graph
```

而不是：

```text
tools/
  = 只有 ToolCatalog/ToolBroker

memory/
  = 只有 Canon/Episodic/Distillation
```

---

## 十三、当前不再适用的旧口径

以下说法不再准确：

- `tools/` 只有四个抽象 contract 模块
- MCP 适配层只做 metadata 映射，不做治理
- Episodic recall 只是简单 top-k 向量召回
- Memory 层没有写入价值评估、主动整合触发和主题图谱

当前正确口径应是：

1. `ToolBroker` 仍是受管执行单入口，但 `tools/` 目录里已经有大量具体 Host 工具实现。
2. `MCPAdapter` 当前带有 Airlock 出站净化、WRITE 授权和 evidence_ref 校验。
3. `EpisodicStore` 已经带 evidence gate、emotion boost、suppression、distortion 等逻辑。
4. `memory/` 当前已包含 `MemoryWorthEvaluator`、`ConsolidationTrigger`、`EpisodicMemoryGraph` 等落地模块。
