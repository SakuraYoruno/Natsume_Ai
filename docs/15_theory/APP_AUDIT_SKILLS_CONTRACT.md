受众：MASTER
标题：host_brain 核心应用层契约（app / audit / skills / observability）

版本：v1.0
日期：2026-03-19
归属：docs/15_theory/

---

## 概述

本文档覆盖 `host_brain/` 下四个核心应用层的接口契约：

| 层 | 目录 | 核心职责 |
|---|------|---------|
| App | `app/` | FastAPI 应用入口、聊天处理、Prompt 构建、场景路由 |
| Audit | `audit/` | SQLiteLedger 审计账本（全系统唯一写入口） |
| Skills | `skills/` | 技能注册表与执行层（SkillRegistry） |
| Observability | `observability/` | 指标采集与观测面 HTTP 路由 |

---

## 一、App 层（FastAPI 应用）

### 1.1 模块清单（核心）

| 模块文件 | 职责 |
|---------|------|
| `fastapi_app.py` | FastAPI 应用实例与路由注册 |
| `main.py` | 应用启动入口 |
| `chat_handler.py` | POST /v1/chat 统一入口（Governor 鉴权 + 场景分类 + 路由分发） |
| `chat_handler_complex.py` | 复杂场景 LangGraph 编排（`_complex_scene_sse_generator`） |
| `chat_handler_edge.py` | Gaming Edge RPC 路由（`_gaming_edge_chat_sse_generator`） |
| `chat_handler_vision.py` | 视觉界面场景（`_vision_ui_sse_generator`） |
| `top_agent.py` | SSE 流式输出处理 |
| `prompt_builder.py` | LLM 提示词渲染（从 fastapi_app.py 拆分） |
| `entry_routing.py` | 入口路由工具（fast-pass、narrative 判定） |
| `graph_config_builder.py` | 场景分类关键词配置与 pre_intent_classifier |
| `emotion_context.py` | 情感快照构建（build_chat_emotion_snapshot 等） |
| `chat_context_builder.py` | 聊天上下文构建 |
| `chat_runtime_input.py` | 运行时输入解析（prompt_context/emotion/retrieval 等） |
| `execution_profile.py` | 执行配置文件 |
| `failure_matrix.py` | 失败矩阵（三图失败收口） |
| `dependencies.py` | FastAPI 依赖注入（get_intent_engine 等） |
| `startup_snapshot.py` | 启动时配置快照 |
| `observability.py` | 观测面集成 |
| `observability_persist.py` | 情节记忆持久化（persist_chat_episodic_memory） |
| `provider_factory.py` | LLM 提供商工厂 |
| `sentence_splitter.py` | 句子分割器（流式输出用） |
| `system_handler.py` | 系统指令处理 |
| `web_ui.py` | Web UI 路由 |
| `webui_aggregation.py` | Web UI 数据聚合 |

### 1.2 场景分类（SceneType）

```python
SCENE_CHAT          = "CHAT"           # 纯叙事对话
SCENE_EXEC_INTENT   = "EXEC_INTENT"    # 执行意图（工具调用）
SCENE_WORK_CODE     = "WORK_CODE"      # 代码工作
SCENE_WORK_TRANSLATE = "WORK_TRANSLATE" # 翻译工作
SCENE_VISION_UI     = "VISION_UI"      # 视觉界面
```

**场景分类流程**（`pre_intent_classifier`）：

```
用户消息
  ↓ is_narrative_only_fast_pass()
  ├── True → SCENE_CHAT（fast-pass，锁 CHAT_FAST）
  └── False → pre_intent_classifier()
        ├── 含 EXEC_INTENT 关键词 → SCENE_EXEC_INTENT
        ├── 含 WORK_CODE 关键词 → SCENE_WORK_CODE
        ├── 含 WORK_TRANSLATE 关键词 → SCENE_WORK_TRANSLATE
        └── 其余 → SCENE_CHAT
```

**fast-pass 条件**（`is_narrative_only_fast_pass`）：
- 消息长度 ≤ 120 字符
- 不含 `EXEC_INTENT`/`WORK_CODE`/`WORK_TRANSLATE` 关键词

### 1.3 聊天处理链路（handle_chat）

```
POST /v1/chat
  ↓ handle_chat()
  ├── 1. 构建 GateSnapshot（build_chat_governance_snapshot）
  ├── 2. Governor 鉴权（evaluate_governor_gates）
  │     └── REJECT → 返回 SSE 错误流
  ├── 3. 场景分类（pre_intent_classifier）
  ├── 4. 路由分发（SceneRouter.route_sse）
  │     ├── SCENE_CHAT → chat_direct / chat_fast
  │     ├── SCENE_EXEC_INTENT → _complex_scene_sse_generator（LangGraph）
  │     ├── SCENE_WORK_CODE → _complex_scene_sse_generator（LangGraph）
  │     ├── SCENE_WORK_TRANSLATE → _complex_scene_sse_generator（LangGraph）
  │     └── SCENE_VISION_UI → _vision_ui_sse_generator
  └── 5. 返回 StreamingResponse（SSE）
```

### 1.4 Prompt 构建（prompt_builder.py）

**场景节点 ID 映射**：

| 场景 | 直接节点 | 渲染节点 | 意图来源节点 |
|------|---------|---------|------------|
| CHAT | `chat_direct_ja` | `renderer_ja_c_style` | `core_reasoner_primary` |

**运行时输入解析**（`chat_runtime_input.py`）：

| 函数 | 返回值 | 说明 |
|------|--------|------|
| `get_chat_runtime_prompt_context` | `PromptContext` | 当前 Prompt 上下文 |
| `get_chat_runtime_emotion_snapshot` | `dict` | 情感快照 |
| `get_chat_runtime_emotion_state` | `dict` | 情感状态（VAD） |
| `get_chat_runtime_retrieval` | `dict` | 检索结果包 |
| `get_chat_runtime_skill_manifest` | `dict` | 技能清单 |
| `get_chat_runtime_user_message` | `str` | 归一化用户消息 |
| `resolve_prompt_context_style_row` | `dict` | 风格 Canon 行 |
| `get_chat_runtime_render_target_lang` | `str` | 渲染目标语言 |

**注入检测（MemoryGuard）**：
- `prompt_builder.py` 内置线程局部变量 `_injection_meta_local`
- 每次构建 Prompt 后，注入检测结果可通过 `get_last_injection_meta()` 读取
- 调用方负责将结果写入 `audit_tags`（TODO-70）

### 1.5 情感快照构建（emotion_context.py）

```python
# 构建聊天情感快照（用于 Prompt 构建）
snapshot = build_chat_emotion_snapshot(
    session_id: str,
    request_id: str,
    ...
) -> dict

# 构建治理快照（用于 Governor 鉴权）
gate_snapshot = build_chat_governance_snapshot(
    session_id: str,
    request_id: str,
    ...
) -> GateSnapshot

# 解析情感上下文字符串（用于检索层 OPT-41）
emotion_ctx = resolve_chat_emotion_context(
    emotion_snapshot: dict,
) -> str | None
```

### 1.6 失败矩阵（failure_matrix.py）

三图失败收口矩阵（P7 阶段引入）：

| 图类型 | 失败场景 | 收口策略 |
|--------|---------|---------|
| 主图（main_graph） | 节点超时/异常 | fallback → chat_direct |
| 意图子图（intent_subgraph） | 工具执行失败 | 重试 N 次后降级 |
| Judge 图（judge_graph） | 评判超时 | 跳过 Judge，直接输出 |

---

## 二、Audit 层（SQLiteLedger 审计账本）

### 2.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `sqlite_ledger.py` | SQLiteLedger 主体（WAL 模式，线程安全） |
| `ledger_constants.py` | 所有表的列定义与 WAL 配置 |
| `ledger_log_mixin.py` | 写入方法（log_intent/log_governor/log_skill_exec 等） |
| `ledger_fetch_mixin.py` | 查询方法（fetch_intent/fetch_governor 等） |
| `ledger_query_mixin.py` | 复杂查询方法 |
| `ledger_scheduler_mixin.py` | 调度器相关写入/查询 |
| `ledger_wal_mixin.py` | WAL 模式管理 |
| `ledger_schema.py` | 建表 DDL |
| `migration_guard.py` | 迁移守卫（防止重复迁移） |
| `migration_runner.py` | 迁移执行器 |
| `drill.py` | 审计钻取工具 |
| `retention.py` | 数据保留策略 |

### 2.2 SQLiteLedger（审计账本）

```python
ledger = SQLiteLedger(
    db_path: str | Path = "host_brain/data/ledger.db",
    wal_config: WalConfig | None = None,
)
```

**WAL 模式**：默认启用 WAL（Write-Ahead Logging），提升并发读写吞吐量。WAL 激活失败时降级为 DELETE 日志模式，写入 `AUDIT_WAL_FAILED`。

### 2.3 审计表清单（完整 26 张表）

| 表名 | 对应列常量 | 写入方法 | 说明 |
|------|-----------|---------|------|
| `intent_log` | `INTENT_COLUMNS` | `log_intent()` | 意图解析记录 |
| `governor_log` | `GOVERNOR_COLUMNS` | `log_governor()` | 治理决策记录 |
| `skill_exec_log` | `SKILL_EXEC_COLUMNS` | `log_skill_exec()` | 工具执行记录 |
| `model_control_log` | `MODEL_CONTROL_COLUMNS` | `log_model_control()` | 模型控制记录 |
| `canon_proposal_log` | `CANON_PROPOSAL_COLUMNS` | `log_canon_proposal()` | Canon 提案记录 |
| `canon_change_log` | `CANON_CHANGE_COLUMNS` | `log_canon_change()` | Canon 变更记录 |
| `world_canon_doc` | `WORLD_CANON_DOC_COLUMNS` | `upsert_world_canon_doc()` | World Canon 索引 |
| `memory_op_log` | `MEMORY_OP_COLUMNS` | `log_memory_op()` | 记忆操作记录 |
| `vault_manifest_log` | `VAULT_MANIFEST_COLUMNS` | `log_vault_manifest()` | 工件清单记录 |
| `vault_gc_log` | `VAULT_GC_COLUMNS` | `log_vault_gc()` | 工件 GC 记录 |
| `airlock_log` | `AIRLOCK_COLUMNS` | `log_airlock_event()` | 气闸隔离记录 |
| `channel_io_log` | `CHANNEL_IO_COLUMNS` | `log_channel_io()` | 通道 I/O 记录 |
| `avatar_action_log` | `AVATAR_ACTION_COLUMNS` | `log_avatar_action()` | Avatar 动作记录 |
| `clawd_action_log` | `CLAWD_ACTION_COLUMNS` | `log_clawd_action()` | Clawd UI 动作记录 |
| `agent_loop_log` | `AGENT_LOOP_COLUMNS` | `log_agent_loop()` | Agent 循环记录 |
| `service_health_log` | `SERVICE_HEALTH_COLUMNS` | `log_service_health()` | 服务健康记录 |
| `migration_log` | `MIGRATION_COLUMNS` | `log_migration()` | 迁移记录 |
| `config_snapshot_log` | `CONFIG_SNAPSHOT_COLUMNS` | `log_config_snapshot()` | 配置快照记录 |
| `voice_event_log` | `VOICE_EVENT_COLUMNS` | `log_voice_event()` | 语音事件记录 |
| `retrieval_event_log` | `RETRIEVAL_EVENT_COLUMNS` | `log_retrieval_event()` | 检索事件记录 |
| `vision_event_log` | `VISION_EVENT_COLUMNS` | `log_vision_event()` | 视觉事件记录 |
| `bond_update_log` | `BOND_UPDATE_COLUMNS` | `log_bond_update()` | Bond 更新记录 |
| `sandbox_exec_log` | `SANDBOX_EXEC_COLUMNS` | `log_sandbox_exec()` | 沙箱执行记录 |
| `scheduler_event_log` | `SCHEDULER_EVENT_COLUMNS` | `log_scheduler_event()` | 调度器事件记录 |
| `p8_subgraph_failure_log` | `P8_SUBGRAPH_FAILURE_LOG_COLUMNS` | `log_p8_subgraph_failure()` | P8 子图失败记录 |
| `p8_graph_run_log` | `P8_GRAPH_RUN_LOG_COLUMNS` | `log_p8_graph_run()` | P8 图运行记录 |

### 2.4 关键约束

1. **单一写入口**：所有审计写入必须通过 `SQLiteLedger` 的 `log_*` 方法，不允许直接操作 SQLite
2. **线程安全**：`SQLiteLedger` 内置 `threading.Lock`，所有写入操作加锁
3. **WAL 降级**：WAL 激活失败时自动降级，不崩溃
4. **append-only**：所有 `log_*` 方法只追加，不更新/删除（`world_canon_doc` 除外，使用 upsert）
5. **intent_log 字段**：`tool_visibility_hash` 字段用于追踪工具集变更（见 `TOOLS_MEMORY_LAYER_CONTRACT.md`）

---

## 三、Skills 层（技能注册表）

### 3.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `registry.py` | SkillRegistry：技能注册表（核心） |
| `loader.py` | 技能加载器（从注册文件加载） |
| `hot_loader.py` | 热加载器（运行时动态加载） |
| `mcp_skill_bridge.py` | MCP 技能桥接器 |
| `builtin_registry.py` | 内置技能注册 |
| `browser_registry.py` | 浏览器技能注册 |
| `desktop_registry.py` | 桌面技能注册 |
| `emotion_registry.py` | 情感技能注册 |
| `file_registry.py` | 文件技能注册 |
| `ide_registry.py` | IDE 技能注册 |
| `memory_registry.py` | 记忆技能注册 |
| `notification_registry.py` | 通知技能注册 |
| `personality_registry.py` | 人格技能注册 |
| `scheduler_registry.py` | 调度器技能注册 |
| `shell_registry.py` | Shell 技能注册 |
| `system_registry.py` | 系统技能注册 |
| `web_registry.py` | Web 技能注册 |

### 3.2 SkillMeta（技能元数据契约）

```python
@dataclass(frozen=True)
class SkillMeta:
    skill_name: str           # 技能唯一名称（非空）
    clearance: str            # "LOW" | "MEDIUM" | "HIGH"
    risk_level: int           # [1, 5]（≥4 必须 require_confirm=True）
    scope: str                # "host" | "memory" | "system"
    reversible: bool          # 是否可撤销
    handler: SkillHandler     # 执行回调 (args, context) -> dict
    require_confirm: bool = False
    description: str = ""
    scheduler_allowed: bool = True   # 是否允许 SCHEDULER 来源调用
    access_level: AccessLevel = AccessLevel.PUBLIC
```

**验证规则**（`validate()`）：
- `skill_name` 非空字符串
- `clearance` ∈ `{"LOW", "MEDIUM", "HIGH"}`
- `risk_level` ∈ `[1, 5]`（整数）
- `scope` ∈ `{"host", "memory", "system"}`
- `reversible` 为 bool
- `handler` 为 callable
- `risk_level >= 4` 时 `require_confirm` 必须为 `True`

### 3.3 SkillPermissionModel（权限模型）

```python
@dataclass(frozen=True)
class SkillPermissionModel:
    clearance: str        # "LOW" | "MEDIUM" | "HIGH"
    scope: str            # "host" | "memory" | "system"
    require_confirm: bool
```

### 3.4 SkillResult（执行结果）

```python
@dataclass(frozen=True)
class SkillResult:
    status: str           # "SUCCESS" | "FAILED" | "REJECTED"
    result_code: str      # 具体结果码
    payload: dict         # 执行输出
    detail: str | None    # 详细说明
```

### 3.5 技能实现清单（`skills/impl/`）

| 实现文件 | 技能类别 |
|---------|---------|
| `browser_executor.py` | 浏览器操作（打开/搜索/截图） |
| `clawd_ui_action.py` | Clawd UI 动作（界面控制） |
| `desktop_executor.py` | 桌面操作（窗口/鼠标/键盘） |
| `desktop_note.py` | 桌面便签 |
| `diary_writer.py` | 日记写入 |
| `dream_generator.py` | 梦境生成 |
| `emotion_executor.py` | 情感状态操作 |
| `file_executor.py` | 文件操作（读/写/删除） |
| `focus_mode.py` | 专注模式控制 |
| `ide_executor.py` | IDE 操作（打开/编辑/运行） |
| `memory_executor.py` | 记忆操作（写入/查询） |
| `notification_executor.py` | 系统通知 |
| `proactive_talk.py` | 主动发言触发 |
| `scheduler_executor.py` | 调度器任务管理 |
| `shell_executor.py` | Shell 命令执行 |
| `system_executor.py` | 系统操作（关机/重启等） |
| `web_executor.py` | Web 请求执行 |

### 3.6 Skills 与 Tools 的关系

```
SkillRegistry（skills/）
  ↓ 注册 handler
ToolCatalog（tools/catalog.py）
  ↓ register(ToolMeta)
ToolBroker（tools/broker.py）
  ↓ execute()
SkillRegistry.execute(skill_name, args, context)
```

- `SkillRegistry` 是技能的**元数据注册与执行层**
- `ToolCatalog` 是工具的**对外暴露层**（供 LLM 选择）
- `ToolBroker` 是工具的**执行门控层**（含令牌验证）
- 三者协作：Skill 注册到 Catalog，Broker 执行时回调 Skill handler

---

## 四、Observability 层（观测面）

### 4.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `observability/metrics.py` | MetricsCollector：指标采集（核心） |
| `observability/metrics_timeseries.py` | 时序指标存储 |
| `observability/routes.py` | GET /obs/metrics HTTP 路由 |
| `observability/service_health_aggregator.py` | 服务健康聚合 |
| `observability/service_health_heartbeat.py` | 服务健康心跳 |
| `observability/session_query.py` | 会话查询 |
| `observability/trace_node_builder.py` | 追踪节点构建 |
| `observability/cross_timeline.py` | 跨时间线分析 |
| `observability/p8_extended_metrics.py` | P8 扩展指标 |
### 4.2 ObsMode（观测模式）

```python
class ObsMode(str, Enum):
    PROD = "prod"   # 生产模式：debug_trace 强制为 None
    DEV  = "dev"    # 开发模式：debug_trace 包含 intent_draft/rules_hit/skill_params
```

**模式切换**：通过环境变量 `OBS_MODE` 控制（默认 `prod`）。

### 4.3 build_obs_metrics_response（指标响应构建）

```python
response = build_obs_metrics_response(
    collector: MetricsCollector,
    request_id: str | None = None,
    obs_mode: ObsMode | None = None,
    recent_chains_n: int = 5,
    ts_ms: int | None = None,
) -> dict
```

**响应结构**：

```json
{
  "status": "OK",
  "ts_ms": 1234567890,
  "metrics": {
    "request_count": 100,
    "error_count": 2,
    "avg_latency_ms": 450,
    ...
  },
  "service_health": {
    "neo4j": "OK",
    "chroma": "OK",
    "vts": "DEGRADED",
    ...
  },
  "recent_chains": [...],
  "debug_trace": null   // prod 模式强制为 null
}
```

**安全约束**：`prod` 模式下 `debug_trace` 强制清除，防止敏感信息泄露。

### 4.4 Observability 规范路径

`host_brain/observability/` 是唯一保留的观测实现路径。

所有新旧代码均应直接使用 `host_brain.observability`；历史 `host_brain/obs/` 兼容层已删除。

---

## 五、与其他层的接口关系

```
App/chat_handler.py
  ├── Governor（governor/rules.py）← 鉴权
  ├── Retrieval（retrieval/orchestrator.py）← 检索
  ├── Integrations（integrations/graph_executor.py）← LangGraph 编排
  ├── Channels/VTS（channels/vts.py）← 表情控制
  ├── Audit（audit/sqlite_ledger.py）← 审计写入
  └── Observability（observability/metrics.py）← 指标上报

Skills/impl/*.py
  ├── Audit（audit/sqlite_ledger.py）← 执行审计
  ├── Memory（memory/episodic.py）← 记忆读写
  ├── Bond（bond/bond_dynamics.py）← Bond 更新
  └── Channels（channels/vts.py）← Avatar 动作

Audit/sqlite_ledger.py
  ← 被所有层调用（全系统唯一审计写入口）
  → 数据供 Observability 层读取与聚合
```
