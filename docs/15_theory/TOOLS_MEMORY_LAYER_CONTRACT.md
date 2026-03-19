受众：MASTER
标题：host_brain/tools/ + memory/ 层接口契约

版本：v1.0
日期：2026-03-19
归属：docs/15_theory/

---

## 概述

`host_brain/tools/` 是 Natsume AI 的工具执行层，提供工具元数据注册（ToolCatalog）、工具执行门控（ToolBroker）、MCP 工具适配（MCP Adapter）与工具选择（Selector）四个子系统。

`host_brain/memory/` 是记忆层，管理 Canon（风格/世界观）、情节记忆（Episodic）、长期蒸馏（Distillation）、上下文压缩（ContextCompressor）等记忆子系统，并通过 Canon 同步机制保持文件与 SQLite 索引的一致性。

---

## 一、Tools 层

### 1.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `catalog.py` | ToolCatalog：工具元数据注册表（SSOT） |
| `broker.py` | ToolBroker：工具执行单一入口（含 WRITE 令牌门控） |
| `selector.py` | 工具选择器（按意图/上下文筛选可见工具） |
| `mcp_adapter.py` | MCP 工具适配器（将 MCP 工具注册到 Catalog） |

### 1.2 ToolMeta（工具元数据契约）

```python
@dataclass(frozen=True)
class ToolMeta:
    id: str                    # 工具唯一 ID（非空）
    name: str                  # 工具显示名称（非空）
    desc: str                  # 工具描述（非空）
    schema_hash: str           # 参数 Schema 哈希（必须以 "sha256:" 开头）
    side_effect_level: str     # "READ" | "WRITE"
    auth_level: str            # "NONE" | "APPROVAL" | "CONFIRM"
    domain_tags: tuple[str]    # 领域标签（非空列表）
    version: str               # 版本号（非空）
```

**必填字段**：`id`、`name`、`desc`、`schema_hash`、`side_effect_level`、`auth_level`、`domain_tags`、`version`

**auth_level 别名映射**（兼容 MCP 来源）：
```
LOW    → NONE
MEDIUM → APPROVAL
HIGH   → CONFIRM
```

**约束**：
- `schema_hash` 必须以 `"sha256:"` 开头
- `side_effect_level` 只允许 `"READ"` 或 `"WRITE"`
- `auth_level` 只允许 `"NONE"`、`"APPROVAL"`、`"CONFIRM"`（别名自动转换）
- `domain_tags` 不允许为空列表

### 1.3 ToolCatalog（工具注册表）

```python
catalog = ToolCatalog(tools: list[ToolMeta] | None = None)

# 注册工具
catalog.register(tool: ToolMeta | dict)

# 查询工具（不存在时抛出 KeyError）
meta = catalog.get(tool_id: str) -> ToolMeta

# 列出所有工具（按 ID 排序）
metas = catalog.list_all() -> list[ToolMeta]

# 导出元数据（可过滤 WRITE 工具）
meta_list = catalog.export_meta(include_write=True) -> list[dict]
```

### 1.4 compute_tool_visibility_hash（可见性哈希）

```python
hash_str = compute_tool_visibility_hash(tool_meta_pack: list[dict]) -> str
```

- 对工具元数据列表计算稳定哈希（SHA-256）
- 用于检测工具集变更（注入 GraphState 供 Evidence Plane 追踪）
- 排序规则：按 `(id, version)` 升序

### 1.5 ToolBroker（工具执行入口）

```python
broker = ToolBroker(
    catalog: ToolCatalog,
    token_service: TokenService,
    ledger: SQLiteLedger,
)

# 注册处理函数
broker.register_handler(tool_id: str, handler: Callable[[dict, dict | None], Any])

# 执行工具
result = broker.execute(
    request_id: str | None,
    intent_id: str,
    session_id: str,
    seq: int,
    tool_id: str,
    args: dict | None = None,
    context: dict | None = None,
    approval_token_ref: str | None = None,
    confirm_token_ref: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    ts_ms: int | None = None,
) -> ToolExecutionResult
```

### 1.6 ToolExecutionResult

```python
@dataclass(frozen=True)
class ToolExecutionResult:
    status: str           # "SUCCESS" | "REJECTED" | "FAILED"
    result_code: str      # 见下表
    tool_id: str
    intent_id: str
    evidence_ref: str | None   # 成功时为 "evidence::uuid"
    detail: dict               # 完整审计行
    output: Any                # 工具执行输出
```

**result_code 对照**：

| result_code | status | 含义 |
|-------------|--------|------|
| `TOOL_NOT_FOUND` | REJECTED | 工具 ID 不在 Catalog 中 |
| `TOOL_HANDLER_MISSING` | REJECTED | 工具已注册但无处理函数 |
| `TOKEN_INVALID` | REJECTED | WRITE 令牌验证失败 |
| `CONFIRM_REQUIRED` | REJECTED | 需要 confirm_token 但未提供 |
| `TOOL_EXECUTED` | SUCCESS | 工具执行成功 |
| `TOOL_EXECUTION_ERROR` | FAILED | 工具执行抛出异常 |

### 1.7 WRITE 工具执行流程

```
broker.execute(tool_id, ...)
  ├── catalog.get(tool_id)  → 不存在: TOOL_NOT_FOUND
  ├── handlers.get(tool_id) → 不存在: TOOL_HANDLER_MISSING
  ├── meta.side_effect_level == "WRITE"?
  │     ├── token_service.validate_execution_tokens(...)
  │     │     └── 失败: TOKEN_INVALID | CONFIRM_REQUIRED
  │     └── 通过: 继续执行
  ├── 注入情感上下文（M27-J OPT-38）
  │     context["emotion_tag"] = "NEUTRAL"（若未提供）
  │     context["mood_state"] = "steady"（若未提供）
  ├── handler(args, context)
  │     └── 异常: TOOL_EXECUTION_ERROR
  ├── 生成 evidence_ref = "evidence::uuid"
  ├── 计算 result_digest（SHA-256）
  └── ledger.log_skill_exec(row)  → 写入审计
```

### 1.8 情感上下文注入（M27-J OPT-38）

执行工具前，`broker.execute` 自动向 `context` 注入情感字段（若未提供）：
```python
context["emotion_tag"] = "NEUTRAL"
context["mood_state"] = "steady"
```

工具处理函数可通过 `context["emotion_tag"]` 和 `context["mood_state"]` 感知当前情感状态。

### 1.9 审计写入

每次 `execute()` 调用（无论成功/失败/拒绝）均写入 `ledger.log_skill_exec`：

```json
{
  "request_id": "...",
  "thread_id": "...",
  "turn_id": "...",
  "intent_id": "...",
  "skill_name": "tool_id",
  "approval_token_ref": "...",
  "confirm_token_ref": "...",
  "result_status": "SUCCESS|REJECTED|FAILED",
  "result_code": "TOOL_EXECUTED",
  "result_digest": "sha256:...",
  "evidence_ref": "evidence::uuid",
  "manifest_ref": null,
  "undo_token_ref": null,
  "ts_ms": 1234567890
}
```

---

## 二、Memory 层

### 2.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `canon_sync.py` | World Canon 文件扫描与 SQLite 索引同步 |
| `canon_vector_auto_sync.py` | Canon 向量自动同步 |
| `canon_vector_projection.py` | Canon 向量投影 |
| `canon_style_store.py` | StyleCanonStore：风格 Canon 存储 |
| `canon_world_store.py` | WorldCanonStore：世界观 Canon 存储 |
| `canon_helpers.py` | Canon 辅助函数 |
| `canon_schemas.py` | Canon Schema 定义 |
| `chroma_client.py` | ChromaDB 客户端配置 |
| `context_compressor.py` | 上下文压缩器 |
| `distillation.py` | 记忆蒸馏（短期→长期） |
| `episodic.py` | 情节记忆存储 |
| `long_term_distiller.py` | 长期蒸馏器 |
| `persona_utterance_vector_projection.py` | 人格话语向量投影 |

### 2.2 Canon 子系统

Canon 是 Natsume AI 的"不变量真相层"，分为两类：

| Canon 类型 | 存储类 | 根目录环境变量 | 说明 |
|-----------|--------|--------------|------|
| Style Canon | `StyleCanonStore` | `Natsume_STYLE_CANON_ROOT` | 风格/人格/表达约束 |
| World Canon | `WorldCanonStore` | `Natsume_WORLD_CANON_ROOT` | 世界观/背景设定 |

#### StyleCanonStore

```python
store = StyleCanonStore(
    style_root: str = "host_brain/canon/style",
    ledger: SQLiteLedger | None = None,
)
```

#### WorldCanonStore

```python
store = WorldCanonStore(
    world_root: str = "host_brain/canon/world",
    ledger: SQLiteLedger | None = None,
)

# 扫描所有 Canon 文档
docs, issues = store.scan_documents()
# docs: list[WorldCanonDoc]
# issues: list[WorldCanonIssue]
```

### 2.3 Canon 同步机制（canon_sync.py）

#### sync_world_canon_index（全量同步）

```python
synced = sync_world_canon_index(
    world_root: str | Path = "host_brain/canon/world",
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> list[WorldCanonSyncRecord]
```

**同步流程**：
1. 扫描 `world_root` 下所有 Canon 文档
2. 对每个文档，与 SQLite 索引（`world_canon_doc` 表）对比
3. 若有变更（version/hash/status/source_path 任一不同）：
   - `ledger.upsert_world_canon_doc(...)` 更新索引
   - `ledger.log_canon_proposal(...)` 写入提案记录
   - `ledger.log_canon_change(...)` 写入变更记录
4. 返回 `WorldCanonSyncRecord` 列表（含 `changed` 标志）

**WorldCanonSyncRecord**：
```python
@dataclass(frozen=True)
class WorldCanonSyncRecord:
    doc_id: str
    version: int
    hash: str
    source_path: str
    changed: bool
```

#### detect_world_version_drift（漂移检测）

```python
drifts = detect_world_version_drift(
    world_root: str | Path = "host_brain/canon/world",
    ledger: SQLiteLedger | None = None,
) -> list[dict]
```

**漂移类型**：

| 漂移码 | 含义 |
|--------|------|
| `WORLD_CANON_VERSION_DRIFT` | 文件版本/哈希与索引不一致 |
| `WORLD_CANON_NOT_INDEXED` | 文件存在但未在索引中 |
| `WORLD_CANON_FILE_MISSING` | 索引中有记录但文件不存在 |
| `WORLD_CANON_INVALID_DOC` | 文档格式无效 |

### 2.4 Episodic Memory（情节记忆）

```python
# episodic.py
```

情节记忆存储在 ChromaDB 中，通过 `ChromaHttpSearchClient` 进行向量检索（见 `RETRIEVAL_LAYER_CONTRACT.md` §5.4）。

### 2.5 Distillation（记忆蒸馏）

```python
# distillation.py + long_term_distiller.py
```

蒸馏机制将短期情节记忆压缩为长期记忆，减少 Prompt 上下文长度。

### 2.6 ContextCompressor（上下文压缩）

```python
# context_compressor.py
```

对话历史压缩器，在 Prompt 构建时对超长上下文进行摘要压缩。

### 2.7 ChromaRuntimeConfig

```python
@dataclass
class ChromaRuntimeConfig:
    endpoint: str              # Chroma HTTP 端点（默认 "http://127.0.0.1:8000"）
    default_collection: str    # 默认集合名（默认 "episodic"）
```

从 `.env` 加载（`load_chroma_runtime_config`）。

---

## 三、Tools × Memory 协作关系

```
ToolBroker.execute(tool_id="search_knowledge", ...)
  ↓
handler(args, context)
  ↓
RetrievalOrchestrator.arun(query=args["query"], ...)
  ↓
RecallFusionServiceContract.recall(...)
  ├── ChromaHttpSearchClient.search(...)  ← episodic memory
  ├── StyleCanonStore.search(...)         ← style canon
  ├── WorldCanonStore.search(...)         ← world canon
  └── ArtifactVault.search(...)           ← artifact vault
```

- `search_knowledge` 工具是检索层的工具化入口
- 工具执行结果通过 `evidence_ref` 写入审计，供 Evidence Plane 追踪

---

## 四、与其他层的接口关系

- **Tools → Governor**：`ToolBroker` 依赖 `TokenService`（`governor/tokens.py`）进行 WRITE 令牌验证
- **Tools → integrations/**：工具执行结果通过 `evidence_ref` 注入 `GraphState`（见 `INTEGRATIONS_LAYER_CONTRACT.md`）
- **Memory → retrieval/**：`StyleCanonStore`、`WorldCanonStore`、`ChromaHttpSearchClient` 作为召回路由的存储后端（见 `RETRIEVAL_LAYER_CONTRACT.md`）
- **Memory → core/**：Canon 同步状态通过 `ledger` 写入 SQLite，供 `service_reporters.py` 健康报告读取（见 `CORE_LAYER_MODULE_REFERENCE.md`）
