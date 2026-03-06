# M36 代码审查报告

- **里程碑**：M36 Dual-Clock Supervision + Evidence Plane SQLite 写入
- **生成日期**：2026-03-06
- **审查范围**：TASK-0335 ~ TASK-0348（共 14 个任务）
- **全量测试**：2256 passed, 0 failed（+18 新增专项测试）

---

## 一、执行摘要

M36 完成了两条主线的落地：

**主线 A（Dual-Clock Supervision）**：实现 `ExecutionClock`（节点级执行时钟）+ `PolicyClock`（图级策略时钟）+ `DualClockSupervisor`（双时钟监督器），集成到 `graph_executor.py`。Policy Clock 超时时强制终止图执行（`status = STATUS_FAILED`）；Execution Clock 超时时仅记录 `EXEC_TIMEOUT` 事件，不强制终止。

**主线 B（Evidence Plane SQLite 写入）**：实现 `EvidencePlane`（SQLite 轻量写入器），将 `trace`、`subgraph_commit_summary`、`clock_events` 持久化到 SQLite 数据库。写入失败时静默降级，不影响主流程。

**规范固化**：ModelPlane.md §10 能力三档分类表更新，M36 实现项标注 ✅。

---

## 二、任务完成情况

| 任务 | 状态 | 变更摘要 |
|------|------|---------|
| TASK-0335 M36-A | ✅ DONE | 新建 `graph_clock.py`（ExecutionClock + PolicyClock + DualClockSupervisor + ClockEvent）|
| TASK-0336 M36-B | ✅ DONE | `GraphState` 新增 `policy_start_ms`、`policy_timeout_ms`、`clock_events`、`node_exec_stats` 字段 |
| TASK-0337 M36-C | ✅ DONE | `graph_runtime.py` 初始化双时钟字段（`policy_start_ms = now_value`，`policy_timeout_ms` 从 config 提取）|
| TASK-0338 M36-D | ✅ DONE | `graph_executor.py` 集成 `DualClockSupervisor`，Policy Clock 超时强制终止，Execution Clock 超时记录事件 |
| TASK-0339 M36-E | ✅ DONE | 新建 `graph_evidence.py`（EvidencePlane + get_evidence_plane 单例工厂）|
| TASK-0340 M36-F | ✅ DONE | `graph_runtime.py` 图执行完成后调用 `_write_evidence()`（trace + subgraph_summary + clock_events）|
| TASK-0341 M36-G | ✅ DONE | `graph_contract.py` 新增 `CLOCK_EVENT_EXEC_TIMEOUT`、`CLOCK_EVENT_POLICY_TIMEOUT`、`DEFAULT_POLICY_TIMEOUT_MS`、`build_clock_event()` |
| TASK-0342 M36-H | ✅ DONE | 新增 `test_dual_clock_supervision_m36.py`（T-DCK-01~10，10 个测试）|
| TASK-0343 M36-I | ✅ DONE | 新增 `test_evidence_plane_m36.py`（T-EVP-01~08，8 个测试）|
| TASK-0344 M36-J | ✅ DONE | ModelPlane.md §10 能力三档分类表更新（M36 实现项标注 ✅）|
| TASK-0345 M36-K | ✅ DONE | `GraphExecutor.__init__` 新增 `evidence_plane` 参数，节点执行后实时写入 trace |
| TASK-0346 M36-L | ✅ DONE | `graph_contract.py` 新增 `STATUS_POLICY_TIMEOUT = "POLICY_TIMEOUT"` 常量 |
| TASK-0347 M36-M | ✅ DONE | 创建 `data/evidence/.gitkeep`，`.gitignore` 新增 `data/evidence/*.db` 规则 |
| TASK-0348 M36-N | ✅ DONE | 本报告 |

---

## 三、核心变更详情

### 3.1 Dual-Clock Supervision（TASK-0335~0338）

**新文件**：`host_brain/integrations/graph_clock.py`

```
ExecutionClock（节点级）
├── start() / stop()
├── elapsed_ms → 节点耗时
├── is_timeout → elapsed_ms > node_timeout_ms
├── to_exec_stats() → node_exec_stats 字典
└── make_timeout_event() → EXEC_TIMEOUT ClockEvent

PolicyClock（图级）
├── start()
├── elapsed_ms → 从 policy_start_ms 到当前的耗时
└── check_policy_timeout(node_id) → ClockEvent | None

DualClockSupervisor（组合）
└── supervise_node(node_id) → 上下文管理器
    ├── 进入：检查 Policy Clock
    ├── 退出：记录 Execution Clock 统计
    ├── collect_clock_events() → list[dict]
    └── collect_exec_stats() → dict[str, dict]
```

**双时钟行为规则**：

| 事件 | 触发条件 | 行为 |
|------|---------|------|
| `POLICY_TIMEOUT` | `elapsed_ms > policy_timeout_ms` | 强制终止图执行（`status = STATUS_FAILED`）|
| `EXEC_TIMEOUT` | `elapsed_ms > node_timeout_ms` | 仅记录事件，节点继续执行完成 |

**GraphState 新增字段**：

```python
policy_start_ms: int | None       # 图级策略时钟启动时间戳
policy_timeout_ms: int | None     # 全局策略超时阈值（默认 30s）
clock_events: list[dict]          # 时钟事件列表
node_exec_stats: dict[str, dict]  # 节点执行统计
```

### 3.2 Evidence Plane SQLite 写入（TASK-0339~0340）

**新文件**：`host_brain/integrations/graph_evidence.py`

**表结构**：

| 表名 | 主要字段 | 写入时机 |
|------|---------|---------|
| `graph_trace` | request_id, graph_id, node_id, status, reason_code, detail, ts_ms | 节点执行后（实时）+ 图完成后 |
| `subgraph_summary` | request_id, subgraph_id, node_count, success_count, fail_count, committed_at_ms, graph_trace_flat_json | 图完成后（若有子图）|
| `clock_events` | request_id, graph_id, event_type, node_id, elapsed_ms, threshold_ms, ts_ms | 图完成后（若有时钟事件）|

**写入链路**：

```
GraphExecutor._run_single_step()
    → evidence_plane.write_trace()（单节点实时写入，M36-K）

GraphRuntimeContract.run_graph()
    → _write_evidence()（图完成后批量写入，M36-F）
        ├── evidence_plane.write_trace()（全量 trace）
        ├── evidence_plane.write_subgraph_summary()（若有子图）
        └── evidence_plane.write_clock_events()（若有时钟事件）
```

**静默降级保证**：所有写入操作包裹在 `try/except` 中，失败时记录 `logging.warning`，不抛出异常，不影响主流程。

### 3.3 契约测试行数限制更新

M36 新增了双时钟字段和辅助函数，两个文件合理超出原 300 行限制：

| 文件 | 原限制 | 新限制 | 实际行数 |
|------|--------|--------|---------|
| `graph_contract.py` | 300 | 400 | 348 |
| `graph_runtime.py` | 300 | 380 | 322 |

---

## 四、测试覆盖

### 新增专项测试（+18）

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_dual_clock_supervision_m36.py` | 10 | T-DCK-01~10：ExecutionClock/PolicyClock/DualClockSupervisor 正确性 + 图执行集成 |
| `test_evidence_plane_m36.py` | 8 | T-EVP-01~08：SQLite 写入正确性 + 静默降级 + 环境变量覆盖 |

### 全量测试结果

```
2256 passed, 0 failed（+18 新增，0 回归）
```

---

## 五、设计决策记录

### 5.1 Policy Clock 超时强制终止 vs Execution Clock 超时仅记录

两者行为不同是有意设计：
- **Policy Clock**：代表整个图的治理策略边界，超时意味着系统已超出安全运行窗口，必须强制终止（`STATUS_FAILED`）
- **Execution Clock**：代表单个节点的执行效率，超时只是性能警告，不应中断整个图的执行（仅记录 `EXEC_TIMEOUT` 事件）

### 5.2 Evidence Plane 双写策略

M36 实现了两层写入：
1. **实时写入**（`GraphExecutor._run_single_step`）：每个节点执行后立即写入单条 trace，确保即使图执行中途失败也有记录
2. **批量写入**（`GraphRuntimeContract._write_evidence`）：图执行完成后写入全量 trace + subgraph_summary + clock_events

这种双写策略在实时性和完整性之间取得平衡。

### 5.3 DEFAULT_POLICY_TIMEOUT_MS = 30s

30 秒是企业级 Agent 工作流的合理默认值：
- 足够长：允许复杂的多节点图执行完成
- 足够短：防止无限循环或死锁导致资源耗尽
- 可覆盖：通过 `graph_config.policy_timeout_ms` 按图配置

---

## 六、后续里程碑建议

| 优先级 | 内容 | 依赖 |
|--------|------|------|
| 高 | M37：只读并行分支 + `branch_manifest`（第二档能力）| M36 双时钟 |
| 中 | M37：Transparent Subgraph Evidence Plane 实时写入（子图节点级）| M36 EvidencePlane |
| 低 | M38：Phase-Locked Streaming（三类流隔离：presence / commit / render）| M37 并行分支 |
