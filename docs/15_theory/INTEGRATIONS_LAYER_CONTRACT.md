受众：MASTER
标题：host_brain/integrations/ 层接口契约

# host_brain/integrations/ 层接口契约

版本：v1.0
日期：2026-03-19
归属：docs/15_theory/

---

## 概述

`host_brain/integrations/` 是 **LangGraph 治理层**的核心实现，负责：

1. **图编排**：图配置验证、节点执行、边路由、回退
2. **治理约束**：Route Commitment、Dual-Clock、Airlock、Checkpoint/Resume
3. **证据平面**：所有治理决策的可审计写入
4. **流式输出**：Phase-Locked Streaming（PRESENCE → COMMIT → RENDER）
5. **Multi-Agent**：Supervisor 编排、子图透明执行、预算治理

---

## 模块一览

### graph_* 系列（图编排与治理）

| 文件 | 职责 |
|---|---|
| `graph_contract.py` | 共享类型、常量、GraphState、配置验证、辅助函数 |
| `graph_runtime.py` | 图运行时入口（`run_graph()`） |
| `graph_executor.py` | 节点执行器（`execute_node()`） |
| `graph_model_resolve.py` | 模型节点解析（从 ModelPlane 配置解析模型参数） |
| `graph_branch.py` | 只读并行分支（`run_parallel_branches()`，M37-F） |
| `graph_checkpoint.py` | Checkpoint / Interrupt / Resume（M39-C） |
| `graph_clock.py` | Dual-Clock 超时监控（M36-G） |
| `graph_evidence.py` | 图级 Evidence Plane 写入 |
| `graph_fallback.py` | 回退策略执行 |
| `graph_streaming.py` | Phase-Locked Streaming 实现（M38-C） |
| `graph_subgraph.py` | 透明子图编译与执行（M35-F） |

### evidence_* 系列（证据平面）

| 文件 | 职责 |
|---|---|
| `evidence_bench.py` | Evidence Bench：统一写入入口，区分 CRITICAL / NON_CRITICAL |
| `evidence_circuit_breaker.py` | 熔断器：连续失败时暂停写入，保护 SQLite |
| `evidence_priority.py` | 写入优先级定义（CRITICAL / NON_CRITICAL） |
| `evidence_write_queue.py` | 异步写入队列（M41-C），NON_CRITICAL 写入缓冲 |

### LLM 网关

| 文件 | 职责 |
|---|---|
| `llm_gateway.py` | LLM 调用网关（多 provider 统一接口） |
| `llm_gateway_schemas.py` | 网关请求/响应 schema |
| `llm_helpers.py` | LLM 调用辅助函数 |

---

## GraphState 字段语义

`GraphState` 是贯穿整个图执行的共享状态（TypedDict），字段按功能分组：

### 基础字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `request_id` | `str` | 请求唯一 ID |
| `graph_id` | `str` | 图配置 ID |
| `timeout_ms` | `int` | 节点级超时（毫秒） |
| `payload` | `dict` | 请求载荷（用户输入、intent 等） |
| `trace` | `list` | 节点执行追踪列表 |
| `status` | `str` | 当前状态（OK / DEGRADED / FAILED / AIRLOCK_PENDING / POLICY_TIMEOUT） |
| `reason_code` | `str \| None` | 失败原因码 |
| `detail` | `str` | 详细描述 |
| `execution_profile` | `str \| None` | 入口层写入，决定跑哪张图；**仅入口可写，下游只读** |

### 路由字段（M35-A Route Commitment Rule）

| 字段 | 写入方 | 语义 |
|---|---|---|
| `committed_route_token` | Host Governor（唯一） | 已提交的路由令牌 |
| `committed_route_token_detail` | Host Governor（唯一） | 完整 CommittedRouteDecision（审计用） |
| `pending_node_id` | 运行时 | 当前待执行节点 |
| `edge_map` | 运行时 | from→to 边映射 |
| `fallback_map` | 运行时 | 回退边映射 |

### Dual-Clock 字段（M36-B）

| 字段 | 语义 |
|---|---|
| `policy_start_ms` | 图级策略时钟启动时间戳（毫秒） |
| `policy_timeout_ms` | 全局策略超时阈值（毫秒），默认 30,000 |
| `clock_events` | 时钟事件列表（EXEC_TIMEOUT / POLICY_TIMEOUT） |
| `node_exec_stats` | 节点执行统计（exec_start_ms / exec_end_ms / exec_duration_ms） |

### 并行分支字段（M37-B）

| 字段 | 写入方 | 语义 |
|---|---|---|
| `branch_specs` | 主图配置注入（只读） | 分支规格列表 |
| `branch_manifest` | `run_parallel_branches()` | 分支执行结果清单 |

### Phase-Locked Streaming 字段（M38-B）

| 字段 | 写入方 | 语义 |
|---|---|---|
| `presence_events` | PresenceStreamEmitter | 在场流事件（只向 UI 输出，不作为下游节点输入） |
| `commit_snapshot` | CommitStreamEmitter | 提交快照（GOV_OK_RENDER 时产生） |
| `render_snapshot_ref` | RenderStreamEmitter | 渲染快照引用 |
| `streaming_phase` | 运行时 | 当前流阶段（PRESENCE / COMMIT / RENDER / None） |

### Checkpoint / Interrupt / Resume 字段（M39-B）

| 字段 | 写入方 | 语义 |
|---|---|---|
| `gov_checkpoint_id` | CheckpointManager | 当前 checkpoint ID |
| `interrupt_reason` | 运行时 | 中断原因（HUMAN_REVIEW / POLICY_TIMEOUT / AIRLOCK） |
| `resume_allowed` | HostPolicyGuard | 是否允许恢复 |
| `resume_deadline_at` | 运行时 | 恢复截止时间戳（毫秒），超过则写入 SyntheticTimeoutEvent |

### barrier_join / RenderGuard 字段（M40-C）

| 字段 | 写入方 | 语义 |
|---|---|---|
| `barrier_join_result` | `barrier_join()` | barrier_join 结果 |
| `render_guard_result` | `RenderGuard.validate_render_output()` | RenderGuard 结果 |

**RenderGuard 约束**：render 输出只能修改 `RENDER_ALLOWED_FIELDS`（`render_text`、`render_html`、`render_markdown`）或 `display_` 前缀字段。

### 情感上下文字段（M45）

| 字段 | 语义 |
|---|---|
| `emotion_context` | 情感上下文（chat → runtime → executor → gateway 透传） |
| `emotion_tag` | 当前情感标签 |
| `mood_state` | 当前 mood 状态 |
| `emotion_range_hint` | 情感范围提示（中文） |

### Multi-Agent 字段（M-MA3）

> 第三阶段已统一到 GraphState 顶层，不再写 `payload["agent_runtime"]`。

| 字段 | 写入方 | 语义 |
|---|---|---|
| `active_agent_id` | Supervisor | 当前活跃子 agent ID |
| `handoff_history` | Supervisor | agent handoff 历史记录 |
| `agent_budget` | 入口层 | 预算（max_hops / per_agent_timeout_ms / total_budget_ms） |
| `agent_artifacts` | 子 agent | 子 agent 输出工件集合 `{agent_id: artifact_dict}` |
| `judge_result` | JudgeAgent | 裁判结果 |

---

## graph_contract.py 核心函数

### validate_graph_config(graph_config)

验证图配置，检查：
- `graph_id`、`version`、`entry_node` 非空
- `entry_node` 必须在 `node_bindings` 中定义
- 所有边的 `from`/`to` 必须指向已定义节点
- 每个节点必须有 `handler_id` 或 `model_node_id`（或图级 `model_profile_id`）

返回：`{stage, status, reason_code, detail, summary, issues}`

### pick_api_key(state, provider_name, model_node_id)

API Key 查找优先级（确定性回退）：
1. `model_nodes[model_node_id].api_key`
2. `dependency_statuses[provider_name].api_key`
3. 环境变量（`OPENAI_API_KEY` / `GEMINI_API_KEY` / `DEEPSEEK_API_KEY`）

### validate_supervisor_proposal_for_graph(proposal, allowlist)

P5 Supervisor proposal 校验：
- 禁止键：`__next_node__`、`committed_route_token`、`branch_specs`、`execution_profile`、`active_agent_id`，以及所有 `committed_` 前缀键
- 必填：`selected_agent`（且必须在 `allowlist` 内）
- `risk_mode` 若存在，必须为 `safe` / `guarded` / `strict`

---

## Evidence Plane 写入机制

### 写入优先级（evidence_priority.py）

| 级别 | 含义 | 写入方式 |
|---|---|---|
| `CRITICAL` | 治理决策、安全事件、路由提交 | 同步写入，失败则阻断 |
| `NON_CRITICAL` | 性能指标、调试追踪、统计数据 | 异步队列写入（EvidenceWriteQueue） |

### EvidenceWriteQueue（M41-C）

```python
queue = EvidenceWriteQueue(max_size=500)
queue.enqueue(write_fn, *args, **kwargs)  # NON_CRITICAL 写入入队
remaining = queue.flush(timeout_ms=100)   # 批量执行，支持超时
```

- 队列满（500）时自动触发 `flush()` 再入队
- `flush()` 失败的任务重试一次，仍失败则丢弃（避免无限循环）
- `clear()` 丢弃所有待执行任务

### EvidenceCircuitBreaker（evidence_circuit_breaker.py）

熔断器保护 SQLite 写入：
- 连续失败达到阈值时进入 OPEN 状态，暂停写入
- 冷却期后进入 HALF_OPEN 状态，允许探测写入
- 探测成功则恢复 CLOSED 状态

---

## 治理约束规则（ModelPlane.md §10 对应实现）

| 规则 | 实现模块 | 核心约束 |
|---|---|---|
| Rule 1：Route Commitment | `graph_contract.py` | `committed_route_token` 只能由 Host Governor 写入 |
| Rule 2：Dual-Clock | `graph_clock.py` | 节点超时（exec）+ 全局策略超时（policy），双重保障 |
| Rule 3：Transparent Subgraph | `graph_subgraph.py` | 子图执行追踪写入 `subgraph_trace`，由 `compile_governed_subgraph` 写入 |
| Rule 4：Parallel Branch | `graph_branch.py` | `branch_specs` 只读注入，`branch_manifest` 由执行器写入；barrier_join 支持 strict / partial 策略 |
| Rule 5：Phase-Locked Streaming | `graph_streaming.py` | PRESENCE → COMMIT → RENDER 三阶段，`presence_events` 只向 UI 输出 |
| Rule 6：Checkpoint/Resume | `graph_checkpoint.py` | 中断原因：HUMAN_REVIEW / POLICY_TIMEOUT / AIRLOCK；恢复截止时间超过则写入 SyntheticTimeoutEvent |

---

## Multi-Agent 契约（P4–P7）

### P4：子图 payload 契约

子图出口写入 `payload` 的固定键：

| 键 | 含义 |
|---|---|
| `subgraph_commit_summary` | 子图成功摘要（由 `build_subgraph_commit_summary()` 产出） |
| `subgraph_failure` | 子图失败归一化输出（必含 `failed_node`、`error_code`、`safe_fallback_reason`、`trace_ref`） |

### P5：Supervisor proposal 契约

LLM 只产出提案，handler 校验后编译。提案允许的键：`selected_agent`、`reason`、`need_judge`、`risk_mode`、`budget`。

失败码：`LLM_TIMEOUT` / `LLM_BAD_JSON` / `LLM_SCHEMA_INVALID` / `LLM_AGENT_NOT_ALLOWED` / `LLM_INTERNAL_ERROR`

决策来源审计字段：`rule` / `llm` / `llm_fallback_rule`

### P6：局部循环契约

循环边界配置键（由 context / node_binding 注入）：
- `max_local_steps`、`max_retry_count`、`local_loop_timeout_ms`

失败码：`LOCAL_LOOP_MAX_STEPS` / `LOCAL_LOOP_TIMEOUT` / `LOCAL_LOOP_INTERNAL_ERROR`

### P7：预算治理契约

预算层级：`graph`（policy_timeout_ms）→ `subgraph`（local_loop_timeout_ms）→ `node`（timeout_ms）

预算突破失败码集合（`BUDGET_BREACH_FAILURE_CODES`）：
`ORCH_TIMEOUT`、`POLICY_TIMEOUT`、`EXEC_TIMEOUT`、`LOCAL_LOOP_TIMEOUT`、`LOCAL_LOOP_MAX_STEPS`、`LLM_TIMEOUT`

---

## 依赖关系

```
host_brain/integrations/
  ├── graph_contract.py      ← 依赖 core/errors.py（无其他 integrations 依赖）
  ├── graph_runtime.py       ← 依赖 graph_contract, graph_executor, graph_clock, graph_evidence
  ├── graph_executor.py      ← 依赖 graph_contract, llm_gateway, graph_model_resolve
  ├── graph_branch.py        ← 依赖 graph_contract, graph_evidence
  ├── graph_checkpoint.py    ← 依赖 graph_contract, evidence_bench
  ├── graph_clock.py         ← 依赖 graph_contract
  ├── graph_evidence.py      ← 依赖 evidence_bench, graph_contract
  ├── graph_streaming.py     ← 依赖 graph_contract
  ├── graph_subgraph.py      ← 依赖 graph_contract, graph_executor
  ├── evidence_bench.py      ← 依赖 evidence_priority, evidence_write_queue, evidence_circuit_breaker
  ├── evidence_write_queue.py← 无 integrations 依赖
  ├── evidence_circuit_breaker.py ← 无 integrations 依赖
  └── llm_gateway.py         ← 依赖 llm_gateway_schemas, llm_helpers
```

**禁止**：`integrations/` 模块不得导入 `app/`、`lsp/` 中的任何模块（单向依赖）。
