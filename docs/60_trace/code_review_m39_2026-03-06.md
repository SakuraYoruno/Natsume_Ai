# M39 代码审查报告

- **里程碑**：M39 Checkpoint / Interrupt / Resume（双时钟模型下的断点续跑）
- **生成日期**：2026-03-06
- **审查范围**：TASK-0379 ~ TASK-0396（共 18 个任务）
- **全量测试**：2321 passed, 0 failed（+22 新增专项测试）

---

## 一、执行摘要

M39 完成了 Checkpoint / Interrupt / Resume 在双时钟模型下的完整落地，实现了 `LangGraph_Governed_Runtime_Solution` §4.4 的设计规范。

**核心成果**：

- 新建 `graph_checkpoint.py`，实现 `CheckpointStore`、`HostPolicyGuard`、`CheckpointManager`、`InterruptSignal`、`ResumeDecision`、`SyntheticTimeoutEvent`
- **双时钟职责分离**：LangGraph 负责"能不能恢复"（Execution Clock），Host 负责"允不允许恢复"（Policy Clock）
- `HostPolicyGuard` 恢复前置检查：超时时写入 `SyntheticTimeoutEvent`（`TIMEOUT_SYNTHETIC + DEADLINE_EXCEEDED + GOV_FALLBACK_SECONDARY`）而非直接恢复
- `graph_executor.py` 集成 Interrupt 触发：`GOV_AIRLOCK_REVIEW` 时自动保存 checkpoint 并写入 `InterruptSignal`
- `graph_runtime.py` 新增 `resume_graph()` 方法：从 checkpoint 恢复图执行
- `graph_evidence.py` 新增 `checkpoints` 表和 `write_checkpoint()` 方法
- `GraphState` 新增 4 个 checkpoint 字段（`gov_checkpoint_id`、`interrupt_reason`、`resume_allowed`、`resume_deadline_at`）
- **关键设计决策**：`checkpoint_id` 是 LangGraph 保留字段，改用 `gov_checkpoint_id` 避免冲突
- ModelPlane.md §10 第一档能力表更新，M39 实现项标注 ✅

---

## 二、任务完成情况

| 任务 | 状态 | 变更摘要 |
|------|------|---------|
| TASK-0379 M39-A | ✅ DONE | 新建 `graph_checkpoint.py`（CheckpointStore + HostPolicyGuard + CheckpointManager + InterruptSignal + ResumeDecision + SyntheticTimeoutEvent）|
| TASK-0380 M39-B | ✅ DONE | `GraphState` 新增 `gov_checkpoint_id`、`interrupt_reason`、`resume_allowed`、`resume_deadline_at` 字段 |
| TASK-0381 M39-C | ✅ DONE | `graph_contract.py` 新增 9 个 checkpoint 常量（CHECKPOINT_STATUS_* + INTERRUPT_REASON_* + RESUME_DENIED_* + SYNTHETIC_EVENT_TIMEOUT + DEFAULT_RESUME_DEADLINE_MS）|
| TASK-0382 M39-D | ✅ DONE | `graph_runtime.py` 初始化 4 个 checkpoint 字段 |
| TASK-0383 M39-E | ✅ DONE | `graph_executor.py` 集成 Interrupt 触发（GOV_AIRLOCK_REVIEW 时保存 checkpoint 并写入 InterruptSignal）|
| TASK-0384 M39-F | ✅ DONE | `graph_runtime.py` 新增 `resume_graph()` 方法（HostPolicyGuard 验证 + 恢复执行）|
| TASK-0385 M39-G | ✅ DONE | `graph_evidence.py` 新增 `checkpoints` 表 + `write_checkpoint()` + `query_checkpoint()` |
| TASK-0386 M39-H | ✅ DONE | `graph_runtime.py._write_evidence` 新增 checkpoint 信息写入 |
| TASK-0387 M39-I | ✅ DONE | 新增 `test_checkpoint_m39.py`（T-CKP-01~12，12 个测试）|
| TASK-0388 M39-J | ✅ DONE | 新增 `test_checkpoint_executor_integration_m39.py`（T-CKP-INT-01~04，4 个测试）|
| TASK-0389 M39-K | ✅ DONE | 新增 `test_evidence_checkpoint_m39.py`（T-EVP-15~17，3 个测试）|
| TASK-0390 M39-L | ✅ DONE | 新增 `test_resume_graph_m39.py`（T-CKP-RES-01~03，3 个测试）|
| TASK-0391 M39-M | ✅ DONE | ModelPlane.md §10 第一档能力表更新（M39 实现项标注 ✅）|
| TASK-0392 M39-N | ✅ DONE | 契约测试行数限制更新（`graph_contract.py` 上限 500）|
| TASK-0393 M39-O | ✅ DONE | 契约测试行数限制更新（`graph_executor.py` 上限 450）|
| TASK-0394 M39-P | ✅ DONE | 契约测试行数限制更新（`graph_runtime.py` 上限 530）|
| TASK-0395 M39-Q | ✅ DONE | 契约测试新增 `graph_checkpoint.py` 行数限制（上限 400 行）|
| TASK-0396 M39-R | ✅ DONE | 本报告 |

---

## 三、核心变更详情

### 3.1 双时钟模型架构（TASK-0379）

**新文件**：`host_brain/integrations/graph_checkpoint.py`

```
CheckpointStore（内存 + 可选 SQLite 双层存储）
├── save(state) -> checkpoint_id（UUID4）
├── load(checkpoint_id) -> GraphState | None
└── exists(checkpoint_id) -> bool

HostPolicyGuard（Host 策略守卫）
└── check_resume_allowed(interrupt_signal, now_ms, policy_timeout_ms, policy_start_ms)
    ├── 检查 resume_deadline_at 是否已超时
    ├── 检查 Policy Clock 是否已超时（可选）
    ├── 超时 → ResumeDecision(allowed=False, synthetic_event=SyntheticTimeoutEvent)
    └── 未超时 → ResumeDecision(allowed=True)

CheckpointManager（组合 CheckpointStore + HostPolicyGuard）
├── interrupt(state, interrupt_reason, resume_deadline_ms) -> InterruptSignal
│   ├── 保存 checkpoint（CheckpointStore.save）
│   └── 返回 InterruptSignal（含 checkpoint_id、interrupt_reason、resume_deadline_at）
├── resume(interrupt_signal, now_ms, ...) -> ResumeDecision
│   └── 调用 HostPolicyGuard.check_resume_allowed
└── load_state(checkpoint_id) -> GraphState | None
```

### 3.2 双时钟职责分离

| 时钟 | 职责 | 实现 |
|------|------|------|
| Execution Clock（LangGraph） | 保存现场、恢复状态 | `CheckpointStore.save/load` |
| Policy Clock（Host Supervisor） | 决定允不允许恢复 | `HostPolicyGuard.check_resume_allowed` |

**关键原则**：
- LangGraph 负责"能不能恢复"（技术可行性）
- Host 负责"允不允许恢复"（治理决策）
- 两者职责分离后，断点续跑与时间驱动降级不再冲突

### 3.3 SyntheticTimeoutEvent 设计

超时时写入 `SyntheticTimeoutEvent` 而非直接恢复：

```python
SyntheticTimeoutEvent(
    node_result = "TIMEOUT_SYNTHETIC",
    error_code = "DEADLINE_EXCEEDED",
    route_token = "GOV_FALLBACK_SECONDARY",
    checkpoint_id = "...",
    ts_ms = ...,
)
```

这确保：
- 超时不会被静默忽略
- 下游可以根据 `route_token = GOV_FALLBACK_SECONDARY` 触发降级链路
- Evidence Plane 有完整的超时证据

### 3.4 GOV_AIRLOCK_REVIEW 触发 Interrupt

`graph_executor.py` 集成：

```
_run_single_step(state)
├── token = GOV_AIRLOCK_REVIEW
├── [M39-E] CheckpointManager.interrupt(state, INTERRUPT_REASON_AIRLOCK)
│   ├── 保存 checkpoint → gov_checkpoint_id
│   ├── 写入 interrupt_reason = AIRLOCK
│   └── 写入 resume_deadline_at（5分钟后）
└── handle_airlock_pending(state)
```

### 3.5 resume_graph 方法

`graph_runtime.py` 新增 `resume_graph(checkpoint_id, request_id, now_ms)`:

```
resume_graph(checkpoint_id, request_id)
├── CheckpointManager.load_state(checkpoint_id)
│   └── 不存在 → STATUS_FAILED + CHECKPOINT_NOT_FOUND
├── 构建 InterruptSignal（从 state 中恢复）
├── HostPolicyGuard.check_resume_allowed()
│   ├── 验证失败 → STATUS_FAILED + SyntheticTimeoutEvent
│   └── 验证通过 → 继续
└── compiled.invoke(resumed_state)
    └── 返回 GraphRunResult（含 resume_decision）
```

### 3.6 关键设计决策：gov_checkpoint_id

LangGraph 内部保留了 `checkpoint_id` 作为 channel 名，直接使用会导致：
```
ValueError: Channel name 'checkpoint_id' is reserved
```

解决方案：将 `GraphState` 中的字段命名为 `gov_checkpoint_id`（Gov = Governance），明确表示这是治理层的 checkpoint ID，与 LangGraph 内部 checkpoint 机制区分。

### 3.7 GraphState 新增字段

```python
gov_checkpoint_id: str | None    # 治理层 checkpoint ID（避免与 LangGraph 保留字段冲突）
interrupt_reason: str | None     # 中断原因（HUMAN_REVIEW / POLICY_TIMEOUT / AIRLOCK）
resume_allowed: bool | None      # 是否允许恢复（由 HostPolicyGuard 写入）
resume_deadline_at: int | None   # 恢复截止时间戳（毫秒）
```

### 3.8 Evidence Plane 扩展（TASK-0385）

**新增表**：`checkpoints`

| 字段 | 类型 | 说明 |
|------|------|------|
| `checkpoint_id` | TEXT PRIMARY KEY | checkpoint ID |
| `request_id` | TEXT | 请求 ID |
| `graph_id` | TEXT | 图 ID |
| `interrupt_reason` | TEXT | 中断原因 |
| `resume_deadline_at` | INTEGER | 恢复截止时间戳（毫秒）|
| `created_at_ms` | INTEGER | 创建时间戳（毫秒）|

### 3.9 契约测试行数限制更新

| 文件 | 原限制 | 新限制 | 实际行数 |
|------|--------|--------|---------|
| `graph_runtime.py` | 380 | 530 | 479 |
| `graph_contract.py` | 450 | 500 | 418 |
| `graph_executor.py` | 420 | 450 | 400 |
| `graph_checkpoint.py` | 新增 | 400 | 374 |

---

## 四、测试覆盖

### 新增专项测试（+22）

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_checkpoint_m39.py` | 12 | T-CKP-01~12：CheckpointStore + HostPolicyGuard + CheckpointManager + SyntheticTimeoutEvent |
| `test_checkpoint_executor_integration_m39.py` | 4 | T-CKP-INT-01~04：GOV_AIRLOCK_REVIEW 触发 interrupt 集成 |
| `test_evidence_checkpoint_m39.py` | 3 | T-EVP-15~17：checkpoints SQLite 写入 |
| `test_resume_graph_m39.py` | 3 | T-CKP-RES-01~03：resume_graph 集成 |

### 全量测试结果

```
2321 passed, 0 failed（+22 新增，0 回归）
```

---

## 五、设计决策记录

### 5.1 为什么用 gov_checkpoint_id 而非 checkpoint_id

LangGraph 内部使用 `checkpoint_id` 作为保留 channel 名，直接使用会导致 `ValueError: Channel name 'checkpoint_id' is reserved`。

命名为 `gov_checkpoint_id`（Gov = Governance）有以下优点：
- 避免与 LangGraph 内部机制冲突
- 明确表示这是治理层的 checkpoint，不是 LangGraph 的 checkpoint
- 语义更清晰：治理层 checkpoint 与执行层 checkpoint 是两个不同的概念

### 5.2 CheckpointStore 的内存 + SQLite 双层设计

- **内存层**：快速读写，进程内有效，适合同一进程内的 interrupt/resume
- **SQLite 层**：持久化，跨进程/重启有效，适合人工介入场景（`db_path` 可选）
- 内存层优先：`load()` 先查内存，再查 SQLite，并回填内存层

### 5.3 DEFAULT_RESUME_DEADLINE_MS = 300_000（5 分钟）

默认恢复截止时间为 5 分钟，这是一个保守的默认值：
- 足够人工介入（AIRLOCK 场景）
- 不会无限期等待
- 可以通过 `resume_deadline_ms` 参数覆盖

### 5.4 SyntheticTimeoutEvent 的 route_token = GOV_FALLBACK_SECONDARY

超时时的 `route_token = GOV_FALLBACK_SECONDARY` 而非 `GOV_FAIL_FALLBACK`，原因：
- `GOV_FAIL_FALLBACK` 表示 Governor 主动裁决失败
- `GOV_FALLBACK_SECONDARY` 表示超时后降级到 secondary reasoner
- 两者语义不同，超时应该尝试 secondary reasoner 而非直接失败

---

## 六、M34~M39 六个里程碑能力全景

```
M34 ──→ pre_intent_classifier + committed_route_token 编译写入
M35 ──→ Route Commitment Rule 条件边 + transparent_subgraph
M36 ──→ Dual-Clock Supervision + Evidence Plane SQLite
M37 ──→ 只读并行分支 + Subgraph Evidence Plane 实时写入
M38 ──→ Phase-Locked Streaming（Presence/Commit/Render 三类流隔离）
M39 ──→ Checkpoint / Interrupt / Resume（双时钟模型下断点续跑）← 当前
```

**第一档能力**（全部 ✅）：`committed_route_token`、`transparent_subgraph`、`dual-clock supervision`、Evidence Plane、Checkpoint/Interrupt/Resume

**第二档能力**（全部 ✅）：只读并行分支 ✅、Subgraph 实时写入 ✅、Phase-Locked Streaming ✅

**第三档能力**（保持默认关闭）：图内自主降级、带副作用的并行执行

---

## 七、后续里程碑建议（M40）

| 优先级 | 内容 |
|--------|------|
| 高 | barrier_join()（并行分支 fan-in 校验，升级为带副作用的并行）|
| 中 | RenderGuard（渲染输出授权，验证 render 输出只修改允许的派生字段）|
| 低 | Evidence Plane 异步写入优化（分级写入、异步落库、分区索引）|
