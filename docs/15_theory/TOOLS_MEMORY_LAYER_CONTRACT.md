受众：MASTER
标题：host_brain/tools/ + memory/ 层接口契约（当前代码实况）

版本：v3.3
日期：2026-06-30
归属：docs/15_theory/

---

定位：本文描述当前代码里的 `host_brain/tools/` 与 `host_brain/memory/` 层。**memory 层的记忆事实主源已迁移到 `LifeMemoryStore`，旧的 `episodic` 主作者地位已被取代**；执行与写入事实以 life_episode / life_evidence、intent / governor / skill_exec、memory_ops、canon_change 等可回放证据为准；tools 层已扩展 6 个新工具实现。请以本文与代码为准。

## 0. 代码证据入口

Tools 层：
- `host_brain/tools/__init__.py`
- `host_brain/tools/catalog.py`（`ToolCatalog` / `ToolMeta` / `compute_tool_visibility_hash`）
- `host_brain/tools/broker.py`（`ToolBroker` / `ToolExecutionResult` / 4 个 result_code 常量）
- `host_brain/tools/selector.py`（`select_tool_meta_pack` / `_rank_tools`）
- `host_brain/tools/mcp_adapter.py`（MCP 工具桥接）
- `host_brain/tools/host_command_executor.py`、`browser_automation.py`、`file_forensics.py`、`file_organizer.py`、`secret_vault.py`、`system_monitor.py`（具体 tool 实现）

Memory 层：
- `host_brain/memory/life_memory/store.py`（`LifeMemoryStore` —— 当前 SSOT，`life_memory/` 子包 7 文件，8,718 行）
- `host_brain/memory/life_memory/semantic.py`（语义记忆操作，2,411 行）
- `host_brain/memory/life_memory/consolidator.py`（`LifeMemoryConsolidator`，925 行）
- `host_brain/memory/life_memory/sleep.py`（`MemorySleepReplayTask`，183 行）
- `host_brain/memory/life_memory_query.py`（`plan_life_memory_query`）
- `host_brain/memory/long_term_distiller.py`（持久化语义蒸馏）
- `host_brain/memory/distillation.py`
- `host_brain/memory/episodic.py`（兼容层）
- `host_brain/memory/episodic_memory_graph.py`
- `host_brain/memory/canon_world_store.py` / `canon_helpers.py` / `canon_schemas.py` / `canon_sync.py` / `canon_vector_projection.py` / `canon_vector_auto_sync.py`
- `host_brain/memory/chroma_client.py`
- `host_brain/memory/context_compressor.py`
- `host_brain/memory/consolidation_trigger.py`
- `host_brain/memory/memory_worth_evaluator.py`

---

## 一、当前层定位

### 1.1 Tools 层

Tools 层是"经过 catalog 受控 + broker 单一执行入口"的**工具调用治理层**：

```
caller (surface_reply / executive / skill 实现)
  → ToolBroker.execute(...)         # 唯一合法执行入口
       ├── ToolCatalog.get(tool_id)  # SSOT，决定 side_effect_level / auth_level
       ├── handler = self._handlers[tool_id]
       ├── WRITE 门控：token_service.validate_execution_tokens(...)
       └── handler(args, exec_context)
  → ToolExecutionResult（result_code + status + evidence_ref + ledger 落盘）
```

**不允许**绕过 broker 直接调 handler。

### 1.2 Memory 层

memory 层当前是**多源记忆系统**，但**记忆事实 SSOT 唯一**：

| 子系统 | 角色 | 文件 |
|---|---|---|
| `LifeMemoryStore` | **当前记忆事实 SSOT**，所有 user / perception / scheduler / inner item 入库为 life episode + evidence；不替代执行审计链 | `life_memory/store.py` |
| Long-term distillation | 长期蒸馏（防递归），把 episodic → semantic | `long_term_distiller.py` / `distillation.py` |
| `episodic` | **兼容层**，仍被部分查询路径使用，不再是主作者 | `episodic.py` |
| World Canon / Persona 边界 | World Canon 维护最小权威内容集合；Soul/Persona 是语言与人格锚；当前源码没有独立风格 canon 存储模块 | `canon_world_store.py` / `soul_runtime.py` |
| Vector projection | Canon → Chroma 自动同步 | `canon_vector_projection.py` / `canon_vector_auto_sync.py` |
| Context compressor | surface chain 之前的上下文压缩（不是最终作者） | `context_compressor.py` |
| Consolidation trigger | 何时触发 sleep replay | `consolidation_trigger.py` |
| Memory worth evaluator | 决定哪些事件值得长期保存 | `memory_worth_evaluator.py` |
| Chroma client | 向量库封装 | `chroma_client.py` |

---

## 二、Tools 层当前契约

### 2.1 当前模块分组

| 文件 | 当前职责 |
|---|---|
| `catalog.py` | `ToolCatalog` SSOT、`ToolMeta` schema、`normalize_tool_meta` 规整、`compute_tool_visibility_hash` 可见性哈希 |
| `broker.py` | `ToolBroker` 单一合法执行入口、`ToolExecutionResult`、4 个 result_code 常量、WRITE 门控 |
| `selector.py` | `select_tool_meta_pack(...)` 给 prompt 选 top-K 工具元信息 |
| `mcp_adapter.py` | MCP 工具桥接（不是薄映射器，含安全策略与 schema 收敛） |
| `host_command_executor.py` | 受控 shell 命令执行 |
| `browser_automation.py` | 浏览器自动化工具 |
| `file_forensics.py` | 文件证据采集 |
| `file_organizer.py` | 文件归类整理 |
| `secret_vault.py` | 密钥保管 |
| `system_monitor.py` | 系统监控指标采集 |

### 2.2 `ToolMeta` 当前 schema

至少包含：

- `tool_id: str`
- `display_name: str`
- `description: str`
- `tags: list[str]`（小写、去重、规整）
- `side_effect_level: "READ" | "WRITE"`
- `auth_level: "AUTO" | "APPROVE" | "CONFIRM"`
- 可选 `output_schema` / `input_schema` / `mcp_meta`

`normalize_tool_meta(payload)` 把外部传入的 payload 规整为标准格式。

### 2.3 `ToolCatalog`

- 注册：`ToolCatalog.register(meta)` / `ToolCatalog.bulk_register(metas)`
- 查询：`ToolCatalog.get(tool_id)` / `ToolCatalog.iter_metas()`
- 可见性：`compute_tool_visibility_hash(tool_meta_pack)` —— 用于 prompt 缓存命中

### 2.4 `ToolBroker.execute(...)`

```python
ToolExecutionResult = ToolBroker.execute(
    *,
    request_id: str | None,
    intent_id: str,
    session_id: str,
    seq: int,
    tool_id: str,
    args: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    approval_token_ref: str | None = None,
    confirm_token_ref: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    ts_ms: int | None = None,
)
```

执行步骤：

1. `catalog.get(tool_id)` —— 不存在 → `RESULT_REJECTED + TOOL_NOT_FOUND`
2. `self._handlers[tool_id]` —— 缺失 → `RESULT_REJECTED + TOOL_HANDLER_MISSING`
3. `meta.side_effect_level == "WRITE"`：
   - 调 `token_service.validate_execution_tokens(...)`
   - `auth_level == "CONFIRM"` → `require_confirm=True`
   - 校验失败 → `RESULT_REJECTED + (TOKEN_INVALID / CONFIRM_REQUIRED / ...)`
4. `exec_context` 默认注入 `emotion_tag="NEUTRAL"` / `mood_state="steady"`（M27-J）
5. `handler(args, exec_context)` 执行：
   - 抛异常 → `RESULT_FAILED + TOOL_EXECUTION_ERROR`
   - 正常返回 → `RESULT_SUCCEEDED + TOOL_EXECUTED`
6. 所有路径都通过 `_log_and_return` 写 `ledger`（落盘 evidence + result_digest + result_code）

### 2.5 当前 `result_code` 常量（broker.py 顶部）

| 常量 | 含义 |
|---|---|
| `TOOL_NOT_FOUND` | catalog 中未注册 |
| `TOOL_HANDLER_MISSING` | catalog 有 meta 但未注册 handler |
| `TOOL_EXECUTED` | 成功执行 |
| `TOOL_EXECUTION_ERROR` | handler 抛异常 |
| 其它（来自 token_service） | `TOKEN_INVALID` / `CONFIRM_REQUIRED` / ... |

### 2.6 `selector.select_tool_meta_pack`

- 输入：`query` + `available tools`
- `_filter_candidates`：按 tag / availability 过滤
- `_rank_tools`：按 query token 与 tool tags / description 的命中数打分
- 输出 top-K 个 `ToolMeta`，作为 prompt context 的 `tool_meta_pack` 槽位

### 2.7 `MCPAdapter`

- 不是薄映射器；含 schema 收敛、安全策略、超时与重试
- 把 MCP server 暴露的 tool 注册到 `ToolCatalog`
- 调用时仍走 `ToolBroker.execute(...)`，不绕过

---

## 三、Memory 层当前契约

### 3.1 `LifeMemoryStore`（当前 SSOT）

存储后端：SQLite，`life_memory/` 子包（7 文件，8,718 行，已从单文件 8,000+ 行分解为 store/semantic/consolidator/sleep/utils/constants/models）。

**记忆条目核心字段**：

- `episode_id`、`session_id`、`person_id`
- `category`：`chat / perception / action / background / inner / reflection / semantic`
- `content`、`evidence`（多条 evidence 关联到同一 episode）
- `truth_state`：`CANDIDATE / VERIFIED / CORRECTED / SUPPRESSED / NARRATIVE_ONLY`
- `state`：`ACTIVE / SUPPRESSED`
- `open_loop`：`OPEN / CLOSED / CANCELLED / SUPERSEDED`（开环状态机）
- `memory_strength`、`decay_profile_id`、`retention_state`（衰减档位）
- `created_ts_ms`、`updated_ts_ms`、`access_log`

**核心入口**：

- `LifeMemoryStore.append_episode(...)`：写入新 episode
- `LifeMemoryStore.append_evidence(...)`：附加 evidence
- `LifeMemoryStore.update_truth_state(...)` / `update_state(...)` / `update_open_loop(...)`
- `LifeMemoryStore.search(...)`：通用查询
- `LifeMemoryStore.recall_*`：各种召回 helper（被 `recall_fusion_stores.search_life_episode` 调用）

**guard 与过滤**：

- synthetic 过滤（防 sample / template 串入）
- anaphoric selection（"那个 / 之前那本"指代识别）
- 时间窗口（today / yesterday / day_before / recent_days / all_time）
- 语料范围（current session / cross-session）
- domain（lightnovel_recommendation / recommendation 等）

### 3.2 `LifeMemoryConsolidator`（`life_memory/consolidator.py`）

- sleep replay 压缩：选 episode 子集合并
- 语义蒸馏：把 episodic 内容浓缩到 semantic 层，避免无限增长
- 衰减触发：定期把低 `memory_strength` 的 episode 移到低留存档

### 3.3 `plan_life_memory_query(query, ...)`

`life_memory_query.py` 提供查询规划器：

- 解析时间锚：`today / yesterday / day_before / recent_days / date_prefix / unspecified / all_time`
- 推断 domain（lightnovel_recommendation / recommendation 等）
- 输出可执行的 query plan，被上游记忆 recall 路径使用

### 3.4 长期蒸馏

- `long_term_distiller.py`：持久化的语义蒸馏（防止递归）
- `distillation.py`：把 episodic memory 蒸馏成 semantic 层
- 触发器：`consolidation_trigger.py` 决定何时跑

### 3.5 Episodic 兼容层

- `episodic.py`：旧 episodic store（仍被部分查询路径使用）
- `episodic_memory_graph.py`：episodic 关联图
- **不再是 chat 主链记忆主作者**；新写入应走 `LifeMemoryStore`

### 3.6 Canon 与 Soul/Persona 边界

- `canon_world_store.py`：世界 canon 存储
- `canon_helpers.py` / `canon_schemas.py`：辅助与 schema
- `canon_sync.py`：canon 同步入口
- `canon_vector_projection.py`：把 canon 投影到向量空间（给 retrieval 层 `world_canon` 路由用）
- `canon_vector_auto_sync.py`：canon 写入时的自动向量同步

World Canon 的显式权威只限于系统术语、架构设定、关键不变量与必要世界观条目的内容一致性；它不是执行事实，不替代审计链。当前源码没有独立风格 canon 存储模块；Soul/Persona 提供语言与人格锚，也不是事实源、政策源、长期记忆源或审计源。

### 3.7 Chroma client

- `chroma_client.py`：向量库封装（`ChromaClientProtocol` 实现）
- 被 retrieval 层与 canon 同步层共同使用

### 3.8 Context compressor

- `context_compressor.py`：surface chain 之前的"上下文压缩"
- 把过长的对话历史压缩成短摘要，给 SurfacePacketCompiler 使用
- **不是最终回复作者**

### 3.9 Memory worth evaluator

- `memory_worth_evaluator.py`：决定哪些事件值得长期保存
- 输入：候选事件（user turn / inner activity / scheduler item ...）
- 输出：`worth_score` + `recommend_truth_state`

### 3.10 Consolidation trigger

- `consolidation_trigger.py`：何时触发 sleep replay 与蒸馏
- 由 ANS / circadian / glymphatic agent 间接调度

---

## 四、Tools ↔ Memory ↔ Retrieval 三层边界

```
┌─────────────────────────────────────────────────────────┐
│ surface_main_chain（最终作者）                          │
└────────┬────────────────────────────────────────────────┘
         │ 注入 fact list（已压缩）
         │
┌────────▼────────────────────────────────────────────────┐
│ persona/surface_memory_recall（决定 RECALL_POLICY）     │
└────────┬────────────────────────────────────────────────┘
         │ 调 recall(...)
         │
┌────────▼────────────────────────────────────────────────┐
│ retrieval/recall_fusion（多路召回 + 混合融合 + 过滤）   │
└────────┬────────────────────────────────────────────────┘
         │ 委托各路 store
         │
┌────────▼────────────────────────────────────────────────┐
│ memory/life_memory（life episode SSOT）                 │
│ memory/canon_world_store（内容一致性）                  │
│ memory/episodic（兼容层）                               │
│ memory/chroma_client（向量库）                           │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ skill / tool 调用：                                     │
│ 任何 caller → ToolBroker.execute(...)                   │
│              ├── ToolCatalog SSOT                       │
│              └── handler 实现（tools/*.py）              │
└─────────────────────────────────────────────────────────┘
```

---

## 五、不变量

1. 任何 tool 调用必须经过 `ToolBroker.execute(...)`，不允许绕过 broker 直接调 handler。
2. `ToolCatalog` 是 tool meta SSOT；任何注册必须经过 `ToolCatalog.register` 或 `bulk_register`。
3. WRITE 类工具必须通过 `token_service.validate_execution_tokens` 校验。
4. `LifeMemoryStore` 是当前**记忆事实 SSOT**；新写入应走 LifeMemory，不应直接写 episodic；ACK、任务状态与失败降级不得写入人格记忆。
5. World Canon 是 Host 维护的最小权威内容集合，只负责内容一致性与冲突裁决；它不是执行事实，不替代 life_episode / life_evidence、intent / governor / skill_exec、memory_ops、canon_change 等可回放证据。
6. 向量库（Chroma）只是衍生层；canon / life_memory 是源，向量是投影。
7. `context_compressor` 不写最终回复，只产出压缩摘要。
8. `episodic` 是兼容层，新代码不应将其作为主入口。
9. Soul/Persona 与风格材料只影响表达，不提供事实、政策、长期记忆或裁决；SurfaceMainChain 仍必须受 necessary_facts、must_not_do 与 ReplyEnvelope 收口约束。

---

## 六、不再代表当前事实的旧口径

| 旧描述 | 当前事实 |
|---|---|
| Memory 层主作者是 `episodic` | 已迁移到 `LifeMemoryStore`；`episodic` 是兼容层 |
| Tools 层只有 catalog + broker 骨架 | 已扩展为 catalog + broker + selector + mcp_adapter + 6 个具体 tool 实现 |
| `MCPAdapter` 是薄映射器 | 已含 schema 收敛、安全策略、超时与重试 |
| Tool 调用可以绕过 broker（用于性能） | 不允许；任何 tool 调用必走 broker |
| Memory 层只有 distillation 与 canon | 已扩展为 life_memory + life_memory_query + long_term_distiller + World Canon + chroma + context_compressor + consolidation_trigger + memory_worth_evaluator |
| 向量库是事实 SSOT | 不是；向量是投影层，源是 life_memory 与 Host 维护的权威内容 |
| 独立风格 canon 存储是活动主链或独立事实源 | 不是；当前源码没有独立风格 canon 存储模块，表达主链由 SurfaceMainChain 收口 |
| World Canon 可声明执行事实 | 不是；World Canon 只解决内容一致性，执行与写入事实必须回到审计证据 |
