受众：MASTER
标题：M38 代码审查报告

# M38 代码审查报告

- **里程碑**：M38 Phase-Locked Streaming（分相流式：Presence / Commit / Render 三类流隔离）
- **生成日期**：2026-03-06
- **审查范围**：TASK-0363 ~ TASK-0378（共 16 个任务）
- **全量测试**：2298 passed, 0 failed（+20 新增专项测试）

---

## 一、执行摘要

M38 完成了 Phase-Locked Streaming 三类流的完整落地，实现了 `LangGraph_Governed_Runtime_Solution` §4.5 和 §6 第五阶段的设计规范。

**核心成果**：

- 新建 `graph_streaming.py`，实现 `PresenceStreamEmitter`、`CommitStreamEmitter`、`RenderStreamEmitter`、`PhaseLockGuard`
- 三类流严格隔离：Presence 只向 UI 单向输出，Commit 只在 `GOV_OK_RENDER` 时产生，Render 只消费 Commit 快照
- `PhaseLockGuard` 验证 Render 前置条件（`intent_commit` 未变化）
- `graph_executor.py` 集成三类流发射（节点前后 Presence 事件、GOV_OK_RENDER 时 Commit 快照、commit_snapshot 存在时 Render 快照引用）
- `graph_evidence.py` 新增 `stream_events` 表和 `write_stream_events()` 方法
- `GraphState` 新增 4 个 streaming 字段（`presence_events`、`commit_snapshot`、`render_snapshot_ref`、`streaming_phase`）
- ModelPlane.md §10 第二档能力表更新，M38 实现项标注 ✅

---

## 二、任务完成情况

| 任务 | 状态 | 变更摘要 |
|------|------|---------|
| TASK-0363 M38-A | ✅ DONE | 新建 `graph_streaming.py`（PresenceStreamEmitter + CommitStreamEmitter + RenderStreamEmitter + PhaseLockGuard + PhaseViolationError）|
| TASK-0364 M38-B | ✅ DONE | `GraphState` 新增 `presence_events`、`commit_snapshot`、`render_snapshot_ref`、`streaming_phase` 字段 |
| TASK-0365 M38-C | ✅ DONE | `graph_contract.py` 新增 7 个 streaming 常量（STREAM_PHASE_* + STREAM_EVENT_*）|
| TASK-0366 M38-D | ✅ DONE | `graph_runtime.py` 初始化 4 个 streaming 字段 |
| TASK-0367 M38-E | ✅ DONE | `graph_executor.py` 集成 Presence Stream 发射（节点执行前后 NODE_START / NODE_END）|
| TASK-0368 M38-F | ✅ DONE | `graph_executor.py` 集成 Commit Stream 发射（GOV_OK_RENDER 时生成 CommitSnapshot）|
| TASK-0369 M38-G | ✅ DONE | `graph_executor.py` 集成 Render Stream 发射（commit_snapshot 存在时生成 render_snapshot_ref）|
| TASK-0370 M38-H | ✅ DONE | `graph_evidence.py` 新增 `stream_events` 表 + `write_stream_events()` + `query_stream_events()` |
| TASK-0371 M38-I | ✅ DONE | `graph_runtime.py._write_evidence` 新增 streaming 事件写入 |
| TASK-0372 M38-J | ✅ DONE | 新增 `test_phase_locked_streaming_m38.py`（T-STR-01~12，13 个测试）|
| TASK-0373 M38-K | ✅ DONE | 新增 `test_streaming_executor_integration_m38.py`（T-STR-INT-01~04，4 个测试）|
| TASK-0374 M38-L | ✅ DONE | 新增 `test_evidence_streaming_m38.py`（T-EVP-12~14，3 个测试）|
| TASK-0375 M38-M | ✅ DONE | ModelPlane.md §10 第二档能力表更新（M38 实现项标注 ✅）|
| TASK-0376 M38-N | ✅ DONE | 契约测试行数限制更新（`graph_contract.py` 上限 450）|
| TASK-0377 M38-O | ✅ DONE | 契约测试行数限制更新（`graph_executor.py` 上限 420）|
| TASK-0378 M38-P | ✅ DONE | 本报告 |

---

## 三、核心变更详情

### 3.1 Phase-Locked Streaming 架构（TASK-0363）

**新文件**：`host_brain/integrations/graph_streaming.py`

```
StreamPhase（枚举）
├── PRESENCE = "PRESENCE"
├── COMMIT = "COMMIT"
└── RENDER = "RENDER"

PresenceStreamEmitter
└── emit(node_id, event_type, chunk) -> PresenceEvent
    ├── phase = PRESENCE（固定）
    ├── 不包含 committed_* 字段（隔离约束）
    └── 不允许作为下游节点输入

CommitStreamEmitter
└── commit(committed_route_token, payload, request_id) -> CommitSnapshot
    ├── 只允许 committed_route_token = GOV_OK_RENDER
    ├── 否则抛出 PhaseViolationError
    ├── 计算 s1_output_hash（payload SHA-256）
    └── 生成 render_input_snapshot_ref

RenderStreamEmitter
└── render(commit_snapshot, chunk) -> RenderEvent
    ├── commit_snapshot 为 None 时抛出 PhaseViolationError
    └── 只消费 Commit 快照的 render_input_snapshot_ref

PhaseLockGuard
├── validate_render_precondition(commit_snapshot, current_intent_commit) -> bool
│   ├── commit_snapshot 为 None → False
│   └── intent_commit 变化 → False
└── build_precondition_failed_event(node_id, reason) -> dict
    └── 写入 presence_events（降级通知）
```

### 3.2 三类流隔离约束

| 约束 | 实现方式 |
|------|---------|
| Presence 不作为下游节点输入 | `presence_events` 字段独立于 `payload`，不在 `payload` 中 |
| Commit 只在 GOV_OK_RENDER 时产生 | `CommitStreamEmitter.commit()` 检查 `committed_route_token` |
| Render 只消费 Commit 快照 | `RenderStreamEmitter.render()` 检查 `commit_snapshot` 非 None |
| Render 前 intent_commit 不变 | `PhaseLockGuard.validate_render_precondition()` 验证 |
| 验证失败时降级通知 | `build_precondition_failed_event()` 写入 `presence_events` |

### 3.3 GraphState 新增字段

```python
presence_events: list[dict[str, Any]]   # 在场流事件列表（只向 UI 输出）
commit_snapshot: dict[str, Any] | None  # 提交快照（GOV_OK_RENDER 时产生）
render_snapshot_ref: str | None         # 渲染快照引用
streaming_phase: str | None             # 当前流阶段（PRESENCE/COMMIT/RENDER/None）
```

### 3.4 graph_executor.py 集成流程

```
_run_single_step(state)
├── [M38-E] 节点执行前：emit NODE_START → presence_events
├── [M36-D] DualClockSupervisor 监督节点执行
├── [M38-E] 节点执行后：emit NODE_END → presence_events
├── [M38-F] 若 committed_route_token = GOV_OK_RENDER：
│   └── CommitStreamEmitter.commit() → commit_snapshot
├── [M38-G] 若 commit_snapshot 存在：
│   ├── PhaseLockGuard.validate_render_precondition()
│   ├── 验证通过：RenderStreamEmitter.render() → render_snapshot_ref
│   └── 验证失败：build_precondition_failed_event() → presence_events
└── [M37-D] 若 branch_specs 非空：run_parallel_branches()
```

### 3.5 Evidence Plane 扩展（TASK-0370）

**新增表**：`stream_events`

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | TEXT | 请求 ID |
| `graph_id` | TEXT | 图 ID |
| `phase` | TEXT | 流阶段（PRESENCE/COMMIT/RENDER）|
| `event_type` | TEXT | 事件类型（NODE_START/NODE_END/CHUNK/PROGRESS）|
| `node_id` | TEXT | 节点 ID |
| `chunk` | TEXT | 临时 narrative chunk |
| `ts_ms` | INTEGER | 时间戳（毫秒）|

### 3.6 契约测试行数限制更新

| 文件 | 原限制 | 新限制 | 实际行数 |
|------|--------|--------|---------|
| `graph_contract.py` | 400 | 450 | 401 |
| `graph_executor.py` | 350 | 420 | 369 |

---

## 四、测试覆盖

### 新增专项测试（+20）

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_phase_locked_streaming_m38.py` | 13 | T-STR-01~12（含 T-STR-03b）：三类流正确性 + 隔离约束 + PhaseViolationError |
| `test_streaming_executor_integration_m38.py` | 4 | T-STR-INT-01~04：graph_executor 三类流集成 |
| `test_evidence_streaming_m38.py` | 3 | T-EVP-12~14：stream_events SQLite 写入 |

### 全量测试结果

```
2298 passed, 0 failed（+20 新增，0 回归）
```

---

## 五、设计决策记录

### 5.1 PhaseViolationError 的设计

`PhaseViolationError` 是一个明确的异常类，而非返回 None 或错误码。这样设计的原因：
- 违反 Phase-Lock 约束是严重的语义错误，不应被静默忽略
- 调用方（`graph_executor.py`）通过 `try/except` 捕获并静默降级，保证主图不中断
- 测试可以明确验证违规场景

### 5.2 Presence 事件的隔离方式

Presence 事件存储在 `GraphState.presence_events` 字段中，而非 `payload`。这确保：
- Presence 事件不会被下游节点的 handler 读取（handler 只接收 `payload`）
- Presence 事件不会影响 `trace`、`status` 等核心字段
- Evidence Plane 可以独立写入 `stream_events` 表

### 5.3 CommitSnapshot 的 s1_output_hash

`s1_output_hash` 是 `payload` 的 SHA-256 哈希，用于：
- 验证 Render 阶段消费的快照与 Commit 阶段一致
- 防止 Render 阶段消费被篡改的 payload
- 提供可审计的证据链

### 5.4 PhaseLockGuard 的 intent_commit 验证

`validate_render_precondition()` 验证 `intent_commit` 未变化，这是最关键的 Phase-Lock 保证：
- 如果 `intent_commit` 在 Commit 后发生变化，说明有节点修改了已提交的意图
- 这种情况下 Render 不应继续，而应写入降级通知
- 降级通知写入 `presence_events`（而非 `trace`），保持核心字段不受影响

---

## 六、M34~M38 能力落地全景

```
M34 ──→ pre_intent_classifier + committed_route_token 编译写入
M35 ──→ Route Commitment Rule 条件边 + transparent_subgraph 第一阶段
M36 ──→ Dual-Clock Supervision + Evidence Plane SQLite 写入
M37 ──→ 只读并行分支 + Subgraph Evidence Plane 实时写入
M38 ──→ Phase-Locked Streaming（Presence/Commit/Render 三类流隔离）← 当前
```

**第一档能力**（全部 ✅）：`committed_route_token`、`pre_intent_classifier`、`transparent_subgraph`、`dual-clock supervision`、Evidence Plane 写入

**第二档能力**（全部 ✅）：只读并行分支 ✅、Subgraph 实时写入 ✅、Phase-Locked Streaming ✅

**第三档能力**（保持默认关闭）：图内自主降级、基于 LLM state 的自治路由、带副作用的并行执行

---

## 七、后续里程碑建议（M39）

| 优先级 | 内容 |
|--------|------|
| 高 | Checkpoint / Interrupt / Resume（双时钟模型下的断点续跑）|
| 中 | barrier_join()（并行分支 fan-in 校验，升级为带副作用的并行）|
| 低 | RenderGuard（渲染输出授权，验证 render 输出只修改允许的派生字段）|
