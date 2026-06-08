受众：MASTER
标题：host_brain 外围层接口契约（airlock / bond / channels / vision / lsp / mcp）

版本：v2.1
日期：2026-06-05
归属：docs/15_theory/

---

## 概述

本文档覆盖 `host_brain/` 下六个外围功能层的接口契约：

| 层 | 目录 | 核心职责 |
|---|------|---------|
| Airlock | `airlock/` | 入站内容隔离分类与沙箱执行 |
| Bond | `bond/` | 情感关系图谱（SSOT）写入与阶段晋级 |
| Channels | `channels/` | VTube Studio / Edge RPC / 审计化通道 I/O |
| Vision | `vision/` | 摄像头多检测器并行执行与聚合 |
| LSP / Vault | `lsp/` | 可逆工件库（ArtifactVault）管理 |
| MCP Registry | `mcp/` | MCP 工具元数据注册与同步 |

---

## 一、Airlock 层（入站隔离）

### 1.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `quarantine.py` | 入站内容分类与草稿降级（核心） |
| `sandbox.py` | DRAFT_CODE 沙箱隔离执行 |
| `docker_sandbox.py` | Docker 容器沙箱后端 |
| `image_filter.py` | 图像内容过滤 |
| `sanitizer.py` | 输入净化器 |
| `sandbox_config.py` | 沙箱运行时配置 |
| `pii_filter.py` | PII（个人身份信息）过滤 |

### 1.2 quarantine_ingress（入站隔离）

```python
result = quarantine_ingress(
    inbound_payload: Any,
    *,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> IngressQuarantineResult
```

**分类集合**：

| 分类常量 | 含义 | allow_execute |
|---------|------|--------------|
| `CLASS_FACT` | 事实性内容（无代码/指令特征） | `True` |
| `CLASS_DRAFT_CODE` | 含代码特征（``` / def / class / import 等） | `False` |
| `CLASS_DRAFT_INSTRUCTION` | 含执行指令特征（run/execute/sudo/rm 等） | `False` |
| `CLASS_UNKNOWN` | 无法分类 | `False` |

**IngressQuarantineResult**：

```python
@dataclass(frozen=True)
class IngressQuarantineResult:
    classification: str       # CLASS_FACT | CLASS_DRAFT_CODE | ...
    draft_only: bool          # True 时下游不得执行
    allow_execute: bool       # 仅 CLASS_FACT 为 True
    reason_code: str          # AIRLOCK_BLOCKED_INGRESS 或 ""
    quarantine_digest: str    # 隔离结果摘要（SHA-256）
    payload_digest: str       # 原始 payload 摘要（SHA-256）
    quarantined_payload: dict # 归一化后的 payload
```

**分类规则**（`_classify_content`）：
- 检测 ` ``` ` 或 `def/class/import/function/SELECT/INSERT/UPDATE/DELETE` → `DRAFT_CODE`
- 检测 `run/execute/delete/drop/sudo/rm -rf/powershell/cmd/pip install/npm install/curl/wget` → `DRAFT_INSTRUCTION`
- 其余 → `FACT`

**约束**：
- `draft_only == True` 时，下游所有执行路径必须拒绝执行（`allow_execute == False`）
- 隔离结果写入 `ledger.log_airlock_event()`（审计链）

### 1.3 SandboxExecutor（沙箱执行）

```python
executor = SandboxExecutor(
    isolation_backend: str = "auto",  # "subprocess" | "docker" | "auto"
    timeout_s: int = 10,              # 范围 [1, 30]
    max_stdout_bytes: int = 65536,    # 64KB
    max_stderr_bytes: int = 16384,    # 16KB
    max_code_bytes: int = 32768,      # 32KB
    ledger: SQLiteLedger | None = None,
)

result = executor.execute(
    code: str,
    *,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> SandboxResult
```

**SandboxResult.status**：

| 状态 | 含义 |
|------|------|
| `OK` | 执行成功 |
| `TIMEOUT` | 超时（`SANDBOX_TIMEOUT`） |
| `RESOURCE_EXCEEDED` | 资源超限（`SANDBOX_RESOURCE_EXCEEDED`） |
| `EXEC_FAILED` | 执行失败（`SANDBOX_EXEC_FAILED`） |
| `UNAVAILABLE` | 沙箱不可用（`SANDBOX_UNAVAILABLE`） |

**隔离后端选择**（`auto` 模式）：
- Docker 可用 → `docker`
- Docker 不可用 → `subprocess`

---

## 二、Bond 层（情感关系图谱）

### 2.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `bond_dynamics.py` | BondDynamicsWriter：Bond SSOT 唯一写入路径 |
| `bond_stage_engine.py` | BondStageEngine：阶段积分计算与自动晋级 |
| `neo4j_client.py` | InMemoryNeo4jBondClient：Neo4j 内存适配器 |
| `neo4j_schemas.py` | Bond 图谱 Schema 定义 |
| `summarizer.py` | Bond 摘要生成器 |
| `async_queue.py` | 异步写入队列 |
| `migration_runner.py` | Bond 数据库迁移执行器 |
| `bond_narrative.py` | Bond 关系叙事生成 |
| `milestone_tracker.py` | 关系里程碑追踪 |
| `shared_memory_index.py` | 共享记忆索引 |
| `user_identity.py` | 用户身份画像 |

### 2.2 BondDynamicsWriter（唯一写入路径）

```python
writer = BondDynamicsWriter(
    ledger: SQLiteLedger | None = None,
    neo4j_client: InMemoryNeo4jBondClient | None = None,
    stage_engine: BondStageEngine | None = None,
)

result = writer.apply_update(
    update_kind: str,
    payload: dict,
    *,
    evidence_ref: str,          # 必填：写入 Bond 必须提供证据引用
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> BondUpdateResult
```

**约束**：
- `evidence_ref` 为空 → `BOND_EVIDENCE_REQUIRED`（拒绝写入）
- 并发写入检测 → `BOND_SINGLE_WRITER_VIOLATION`（拒绝写入）
- 写入成功后自动触发 `stage_engine.compute_stage_score()`（若已注入）

**BondUpdateResult**：

```python
@dataclass(frozen=True)
class BondUpdateResult:
    success: bool
    status: str          # "OK" | "REJECTED"
    reason_code: str | None
    stage: str           # "S_BOND_UPDATE"
    detail: str
    event: dict          # 完整审计事件
```

### 2.3 BondStageEngine（阶段晋级引擎）

**阶段枚举**（从低到高）：

```
STRANGER → ACQUAINTANCE → FRIEND → CLOSE_FRIEND → CONFIDANT → SOULMATE
```

```python
engine = BondStageEngine(
    ledger: SQLiteLedger | None = None,
    score_config: dict | None = None,   # update_kind → 积分映射
    threshold_config: dict | None = None,  # 阶段 → 晋级阈值
    decay_config: dict | None = None,   # 衰减配置
)

result = engine.compute_stage_score(
    session_id: str,
    update_kind: str,
    payload: dict,
    *,
    ts_ms: int | None = None,
) -> StageTransitionResult
```

**StageTransitionResult**：

```python
@dataclass
class StageTransitionResult:
    current_stage: str
    previous_stage: str | None
    transitioned: bool          # 是否发生阶段变化
    score_delta: float          # 本次积分增量
    total_score: float          # 累计积分
    reason_code: str | None
```

**晋级规则**：
- 每次 Bond 事件按 `update_kind` 贡献积分（可配置）
- 积分累积到阈值时触发晋级，写入 `bond_stage_transition_log`
- 长期无互动触发衰减（可配置 `decay_config`）

---

## 三、Channels 层（双通道 I/O）

### 3.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `vts.py` | VTube Studio 通道（表情/动作控制） |
| `vts_mapping_store.py` | VTS 情感→表情 ID 映射存储 |
| `vts_rate_limiter.py` | VTS 速率限制器 |
| `vts_runtime_mapping.py` | VTS 运行时映射 |
| `edge_rpc_client.py` | Host → Edge / Gateway HTTP 推送客户端（VTS / TTS / `/v1/gateway/reply`） |
| `tts_context_router.py` | TTS 情感/语速/语言路由 |
| `multimodal_processor.py` | 多模态信号加工 |
| `wechat.py` | WeChat 审计化通道 I/O |
| `wechat_health.py` | WeChat 健康检查 |
| `mateengine.py` | MateEngine UDP 桥接（姿态/表情/动作控制） |

### 3.2 VTS 通道（VTube Studio）

#### 情感→表情 ID 映射（完整 16 种）

| 情感标签 | 表情 ID |
|---------|--------|
| `NEUTRAL` | `exp_neutral_01` |
| `HAPPY` | `exp_happy_01` |
| `SAD` | `exp_sad_01` |
| `ANGRY` | `exp_angry_01` |
| `SURPRISED` | `exp_surprised_01` |
| `THINKING` | `exp_thinking_01` |
| `EXCITED` | `exp_excited_01` |
| `CALM` | `exp_calm_01` |
| `FRUSTRATED` | `exp_frustrated_01` |
| `ANXIOUS` | `exp_anxious_01` |
| `LOVING` | `exp_loving_01` |
| `EMBARRASSED` | `exp_embarrassed_01` |
| `MELANCHOLY` | `exp_melancholy_01` |
| `PLAYFUL` | `exp_playful_01` |
| `DETERMINED` | `exp_determined_01` |
| `CONFUSED` | `exp_confused_01` |

#### VTS 触发门控

```python
# 触发来源
TRIGGER_HOST = "HOST"   # 系统主动触发
TRIGGER_LLM  = "LLM"   # LLM 输出触发

# 状态
STATUS_APPLIED  = "APPLIED"   # 成功应用
STATUS_REJECTED = "REJECTED"  # 被门控拒绝
STATUS_DEGRADED = "DEGRADED"  # 降级（VTS 不可用）
```

**门控规则**（`S_AVATAR_TALK_GATE`）：
- `TRIGGER_LLM` 触发的自由动作被 `AVATAR_FREE_ACTION_BLOCKED` 拒绝（防止 LLM 直接控制 Avatar）
- 无效 `emotion_tag` → `AVATAR_TAG_INVALID`
- 无效 `trigger_name` → `AVATAR_TRIGGER_INVALID`
- VTS 连接失败 → `VTS_CONNECTION_FAILED`（降级为 `STATUS_DEGRADED`）

**降级行为**：VTS 不可用时返回 `VTS_DEGRADED_FALLBACK`，不崩溃，写入审计。

### 3.3 WeChat 通道

```python
# 入站（INGRESS）
result = wechat_channel.receive(
    raw_payload: dict,
    *,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> WeChatIngressResult

# 出站（EGRESS）
result = wechat_channel.send(
    intent_id: str,
    thread_key: str,
    content: str,
    *,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> WeChatEgressResult
```

**错误码**：

| 错误码 | 场景 |
|--------|------|
| `CHANNEL_DUPLICATE_MESSAGE` | 重复消息（幂等去重） |
| `CHANNEL_SEND_FAILED` | 发送失败 |
| `CHANNEL_SEND_UNAUTHORIZED` | 未授权发送 |
| `CHANNEL_INTENT_MISSING` | intent_id 缺失 |
| `CHANNEL_PAYLOAD_INVALID` | payload 格式无效 |
| `CHANNEL_THREAD_KEY_MISSING` | thread_key 缺失 |

**幂等去重**：入站消息通过 `payload_digest`（SHA-256）去重，重复消息返回 `DUPLICATE_IGNORED`。

---

## 四、Vision 层（摄像头视觉）

### 4.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `detector_registry.py` | 多检测器注册表与并行聚合 |
| `camera_event.py` | 摄像头事件 Schema |
| `screen_ocr.py` | 屏幕 OCR 检测器 |

### 4.2 DetectorConfig（检测器配置）

```python
@dataclass
class DetectorConfig:
    detector_id: str
    detector_type: str    # "face" | "object" | "gesture" | "scene"
    enabled: bool = True
    timeout_s: float = 2.0
    weight: float = 1.0   # 聚合权重
```

**有效检测器类型**：`face`、`object`、`gesture`、`scene`

### 4.3 MultiDetectorAggregator（并行聚合）

```python
aggregator = MultiDetectorAggregator(
    configs: list[DetectorConfig],
    handlers: dict[str, Callable],  # detector_id → handler
)

result = aggregator.run(
    frame_data: Any,
    *,
    evidence_ref: str,
    ts_ms: int | None = None,
) -> AggregationResult
```

**AggregationResult.status**：

| 状态 | 含义 |
|------|------|
| `OK` | 所有检测器成功 |
| `PARTIAL` | 部分检测器失败，有结果 |
| `ALL_FAILED` | 全部检测器失败 |

**降级规则**：
- 单个检测器超时 → `VISION_DETECTOR_TIMEOUT`（跳过，继续聚合）
- 单个检测器不可用 → `VISION_DETECTOR_UNAVAILABLE`（跳过）
- 全部失败 → `VISION_DETECTOR_ALL_FAILED`
- 摄像头不可用 → `VISION_CAMERA_UNAVAILABLE`（整体失败）

---

## 五、LSP / ArtifactVault（可逆工件库）

### 5.1 ArtifactVault（工件库）

```python
vault = ArtifactVault(
    root_dir: str | Path = "host_brain/data/vault",
    ledger: SQLiteLedger | None = None,
)
```

#### commit_manifest（提交工件清单）

```python
result = vault.commit_manifest(
    intent_id: str,
    skill_name: str,
    artifacts: list[dict],   # [{"path": str, "content": str, ...}]
    *,
    approval_token_ref: str | None = None,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> VaultResult
```

- 成功时返回 `manifest_ref = "manifest::uuid"`
- 无效清单 → `MANIFEST_INVALID`
- 令牌无效 → `TOKEN_INVALID`

#### undo_manifest（撤销工件）

```python
result = vault.undo_manifest(
    manifest_ref: str,
    *,
    undo_token_ref: str | None = None,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> VaultResult
```

- 需要 `undo_token_ref` 才能撤销
- 缺少 undo token → `UNDO_REQUIRED`

#### gc_expired_manifests（GC 清理）

```python
gc_count = vault.gc_expired_manifests(
    max_age_ms: int = 86400000,  # 默认 24 小时
    *,
    ts_ms: int | None = None,
) -> int
```

**审计写入**：所有操作写入 `ledger.log_vault_manifest()` / `ledger.log_vault_gc()`。

### 5.2 LSP Policies（`lsp/policies/`）

`lsp/policies/` 目录存放 LSP（Language Server Protocol）相关策略配置，用于 IDE 集成场景的代码分析策略。

---

## 六、MCP Registry（MCP 工具注册）

### 6.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `tool_registry.py` | MCP ToolMeta 注册表（SQLite 持久化） |
| `tool_broker.py` | MCP 工具执行代理 |

### 6.2 MCP ToolMeta（元数据）

```python
@dataclass
class ToolMeta:
    provider_id: str       # MCP 提供商 ID
    tool_id: str           # 工具 ID（唯一键：provider_id + tool_id）
    name: str              # 工具名称
    description: str       # 工具描述
    access_level: str      # "PUBLIC" | "INTERNAL" | "RESTRICTED"
    risk_level: str        # "LOW" | "MEDIUM" | "HIGH"
    reversible: bool       # 是否可撤销
    version: str = "1.0.0"
    schema_hash: str = ""  # 参数 Schema 哈希
    domain_tags: list[str] = []            # 领域标签
    emotion_requirement: list[str] = []    # 情感适用性约束；空列表表示无限制
```

### 6.3 MCPToolRegistry（注册表）

```python
registry = MCPToolRegistry(
    db_path: str | Path = ":memory:",
    check_same_thread: bool = True,
)

# 注册 / 更新单个工具（upsert + 变更日志）
sync_entry = registry.register(meta: ToolMeta) -> dict[str, Any]

# 查询工具
tool = registry.get(provider_id: str, tool_id: str) -> ToolMeta | None

# 列出提供商所有工具
tools = registry.list_by_provider(provider_id: str) -> list[ToolMeta]

# 列出全部已注册工具
all_tools = registry.list_all() -> list[ToolMeta]

# 读取变更历史
history = registry.sync_log() -> list[dict[str, Any]]
```

**变更日志**（`mcp_sync_log` 表）：
- 每次 `register()` 都会写入 append-only 的 `INSERT` / `UPDATE` 记录
- 日志项包含 `before / after / diff / ts_ms`
- 用于审计 MCP 工具集的历史变更

### 6.4 MCP 与 host_brain/tools/ 的关系

```
MCPToolRegistry（mcp/）
  ↓ register() / sync_log()
MCP 工具元数据（SQLite）
  ↓ mcp_adapter.py（tools/）
ToolCatalog（tools/catalog.py）
  ↓ register()
ToolBroker（tools/broker.py）
  ↓ execute()
```

- `mcp/tool_registry.py` 负责 MCP 工具的**持久化注册与变更追踪**
- `tools/mcp_adapter.py` 负责将 MCP provider 工具**适配注册到 ToolCatalog**（见 `TOOLS_MEMORY_LAYER_CONTRACT.md`）
- 两者职责不同，不可混用

---

## 七、与其他层的接口关系

```
Airlock → app/chat_handler.py
  入站内容先经 quarantine_ingress() 分类，draft_only=True 时拒绝执行

Bond → integrations/graph_contract.py
  BondDynamicsWriter.apply_update() 由 graph 节点调用，evidence_ref 来自 EvidencePlane

Channels/VTS/Gateway → app/chat_agent.py + app/input_routes.py + channels/edge_rpc_client.py
  ChatAgent 决定文字/语音输出策略；Gateway/ASR 回流由 input_routes 注入 `reply_channel / reply_target`；真正的外部平台回复与 VTS/TTS 推送通过 `edge_rpc_client.py` 发往 Edge / Gateway。

Vision → app/chat_handler_vision.py
  摄像头帧数据经 MultiDetectorAggregator 处理后注入 GraphState

LSP/Vault → tools/broker.py
  WRITE 工具执行后通过 vault.commit_manifest() 归档工件

MCP → tools/mcp_adapter.py
  MCP 工具经 MCPToolRegistry 注册后适配到 ToolCatalog
```
