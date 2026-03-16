# M40 代码审查报告：barrier_join() + RenderGuard

- **文档编号**：`CODE_REVIEW_M40`
- **生成日期**：2026-03-06
- **里程碑**：M40 — barrier_join() + RenderGuard（并行分支 fan-in 校验 + 渲染输出授权）
- **任务范围**：TASK-0397 ~ TASK-0416（共 20 个任务）
- **前置依赖**：M39 DONE（Checkpoint / Interrupt / Resume）

---

## 一、执行摘要

M40 里程碑完成了两项核心治理能力的落地：

1. **`barrier_join()`**：并行分支 fan-in 校验，确保所有并行分支在 fan-in 时均持有有效的 `audit_commit`，缺任一证据则 fail closed（strict 模式）。
2. **`RenderGuard`**：渲染输出授权守卫，验证 render 输出只修改允许的派生字段（`render_text`、`render_html`、`render_markdown`、`display_*` 前缀），不允许修改 `locked_fields`。

全量测试：**27 个新增测试，全部通过；契约测试 22 个，全部通过；无回归。**

---

## 二、变更摘要（TASK-0397 ~ TASK-0415）

### TASK-0397 M40-A：graph_branch.py 新增 AuditTicket、BarrierJoinResult、barrier_join()

**文件**：`host_brain/integrations/graph_branch.py`

**新增内容**：
- `AuditTicket` 数据类：审计工单（`branch_id`、`audit_commit`、`input_digest`、`output_digest`、`issued_at_ms`）
- `BarrierJoinResult` 数据类：fan-in 校验结果（`passed`、`failed_branches`、`join_policy_ref`、`reason`）
- `build_audit_ticket()` 辅助函数：在 fan-out 前由 Host 签发审计工单，计算 `input_digest` 和 `audit_commit`（SHA-256）
- `barrier_join()` 函数：校验所有分支是否存在 `audit_commit`，支持 strict/partial 两种策略

**关键设计**：
- strict 模式：任一分支缺 `audit_commit` 则 `passed=False`，`join_policy_ref` 为空
- partial 模式：允许部分失败，但必须记录 `join_policy_ref`（含时间戳和失败分支列表）
- 若提供 `audit_tickets`，额外校验 `output_digest` 一致性

---

### TASK-0398 M40-B：graph_streaming.py 新增 RenderGuard

**文件**：`host_brain/integrations/graph_streaming.py`

**新增内容**：
- `RenderGuardResult` 数据类：渲染守卫结果（`passed`、`violated_fields`、`reason`）
- `RenderGuard` 类：渲染输出授权守卫
  - `is_allowed_render_field(field_name)` 辅助方法：判断字段是否允许被 render 修改
  - `validate_render_output(render_output, commit_snapshot)` 核心方法：校验 render 输出

**关键设计**：
- 绝对禁止修改的字段：`intent_commit`、`s1_output_hash`、`committed_route_token`、`committed_route_token_detail`
- `locked_fields`（来自 `commit_snapshot`）中的字段不允许修改
- 允许修改的字段：`RENDER_ALLOWED_FIELDS`（`render_text`、`render_html`、`render_markdown`）+ `display_` 前缀字段
- `commit_snapshot` 为 None 时 fail closed

---

### TASK-0399 M40-C：GraphState 新增 barrier_join_result、render_guard_result 字段

**文件**：`host_brain/integrations/graph_contract.py`

**新增字段**：
- `barrier_join_result: dict[str, Any] | None`：barrier_join 结果（由 `barrier_join()` 写入）
- `render_guard_result: dict[str, Any] | None`：RenderGuard 结果（由 `RenderGuard.validate_render_output()` 写入）

---

### TASK-0400 M40-D：graph_contract.py 新增 barrier_join 和 RenderGuard 相关常量

**文件**：`host_brain/integrations/graph_contract.py`

**新增常量**：
- `BARRIER_JOIN_POLICY_STRICT = "strict"`
- `BARRIER_JOIN_POLICY_PARTIAL = "partial"`
- `BARRIER_JOIN_STATUS_PASSED = "PASSED"`
- `BARRIER_JOIN_STATUS_FAILED = "FAILED"`
- `RENDER_GUARD_STATUS_PASSED = "PASSED"`
- `RENDER_GUARD_STATUS_FAILED = "FAILED"`
- `RENDER_ALLOWED_FIELDS: frozenset[str]`（`render_text`、`render_html`、`render_markdown`）
- `RENDER_ALLOWED_FIELD_PREFIX = "display_"`

---

### TASK-0401 M40-E：graph_runtime.py 初始化 barrier_join_result、render_guard_result 字段

**文件**：`host_brain/integrations/graph_runtime.py`

**变更**：在 `run_graph` 的 `initial_state` 构建中新增：
```python
"barrier_join_result": None,
"render_guard_result": None,
```

---

### TASK-0402 M40-F：graph_executor.py 集成 barrier_join()

**文件**：`host_brain/integrations/graph_executor.py`

**变更**：在 `_run_single_step` 的并行分支执行完成后，调用 `barrier_join()`：
- `branch_manifest` 非空时调用 `barrier_join()`
- 结果写入 `state.barrier_join_result`
- 失败时静默降级（不影响主图执行）

---

### TASK-0403 M40-G：graph_executor.py 集成 RenderGuard

**文件**：`host_brain/integrations/graph_executor.py`

**变更**：在 `_run_single_step` 的 Render Stream 发射后，调用 `RenderGuard.validate_render_output()`：
- `render_snapshot_ref` 非空时调用 `RenderGuard`
- 结果写入 `state.render_guard_result`
- 失败时写入降级通知到 `presence_events`，静默降级

---

### TASK-0404 M40-H：graph_evidence.py 新增 barrier_join_log 表和写入方法

**文件**：`host_brain/integrations/graph_evidence.py`

**新增内容**：
- `barrier_join_log` 表（`id`、`request_id`、`graph_id`、`passed`、`failed_branches_json`、`join_policy_ref`、`reason`、`ts_ms`）
- `write_barrier_join(request_id, graph_id, barrier_join_result)` 方法：写入 barrier_join 结果
- `query_barrier_join(request_id)` 方法：查询记录（测试用）

---

### TASK-0405 M40-I：graph_runtime.py 集成 barrier_join_log 写入 Evidence Plane

**文件**：`host_brain/integrations/graph_runtime.py`

**变更**：在 `_write_evidence` 方法中新增：
```python
barrier_join_result = (state or {}).get("barrier_join_result")
if barrier_join_result and isinstance(barrier_join_result, dict):
    ep.write_barrier_join(request_id=request_id, graph_id=graph_id, barrier_join_result=barrier_join_result)
```

---

### TASK-0406 M40-J：barrier_join() 核心专项测试（T-BJ-01~10）

**文件**：`tests/unit/test_barrier_join_m40.py`（新增）

| 测试编号 | 测试内容 | 结果 |
|---------|---------|------|
| T-BJ-01 | 所有分支有 audit_commit 时 passed=True | ✅ PASSED |
| T-BJ-02 | 任一分支缺 audit_commit 时 passed=False | ✅ PASSED |
| T-BJ-03 | failed_branches 包含所有缺证据的分支 ID | ✅ PASSED |
| T-BJ-04 | 空 branch_manifest 时 passed=True | ✅ PASSED |
| T-BJ-05 | join_policy=strict 时任一失败则 passed=False | ✅ PASSED |
| T-BJ-06 | join_policy=partial 时记录 join_policy_ref | ✅ PASSED |
| T-BJ-07 | build_audit_ticket 返回有效 AuditTicket | ✅ PASSED |
| T-BJ-08 | AuditTicket.audit_commit 非空（SHA-256 64位） | ✅ PASSED |
| T-BJ-09 | output_digest 不一致时 passed=False | ✅ PASSED |
| T-BJ-10 | output_digest 一致时 passed=True | ✅ PASSED |

---

### TASK-0407 M40-K：RenderGuard 核心专项测试（T-RG-01~08）

**文件**：`tests/unit/test_render_guard_m40.py`（新增）

| 测试编号 | 测试内容 | 结果 |
|---------|---------|------|
| T-RG-01 | render 输出只修改允许字段时 passed=True | ✅ PASSED |
| T-RG-02 | render 输出修改 locked_fields 中的字段时 passed=False | ✅ PASSED |
| T-RG-03 | render 输出修改 intent_commit 时 passed=False | ✅ PASSED |
| T-RG-04 | violated_fields 包含所有违规字段名 | ✅ PASSED |
| T-RG-05 | render 输出修改 display_ 前缀字段时 passed=True | ✅ PASSED |
| T-RG-06 | render 输出修改 render_text 时 passed=True | ✅ PASSED |
| T-RG-07 | commit_snapshot 为 None 时 passed=False | ✅ PASSED |
| T-RG-08 | render 输出为空时 passed=True | ✅ PASSED |

---

### TASK-0408 M40-L：barrier_join executor 集成测试（T-BJ-INT-01~03）

**文件**：`tests/unit/test_barrier_join_executor_integration_m40.py`（新增）

| 测试编号 | 测试内容 | 结果 |
|---------|---------|------|
| T-BJ-INT-01 | branch_manifest 非空时 state.barrier_join_result 非空 | ✅ PASSED |
| T-BJ-INT-02 | 所有分支有 audit_commit 时 barrier_join_result.passed=True | ✅ PASSED |
| T-BJ-INT-03 | 任一分支缺 audit_commit 时 barrier_join_result.passed=False | ✅ PASSED |

---

### TASK-0409 M40-M：RenderGuard executor 集成测试（T-RG-INT-01~03）

**文件**：`tests/unit/test_render_guard_executor_integration_m40.py`（新增）

| 测试编号 | 测试内容 | 结果 |
|---------|---------|------|
| T-RG-INT-01 | render_snapshot_ref 非空时 state.render_guard_result 非空 | ✅ PASSED |
| T-RG-INT-02 | render 输出合法时 render_guard_result.passed=True | ✅ PASSED |
| T-RG-INT-03 | render 输出违规时 render_guard_result.passed=False | ✅ PASSED |

---

### TASK-0410 M40-N：Evidence Plane barrier_join_log 写入专项测试（T-EVP-18~20）

**文件**：`tests/unit/test_evidence_barrier_join_m40.py`（新增）

| 测试编号 | 测试内容 | 结果 |
|---------|---------|------|
| T-EVP-18 | write_barrier_join 将结果写入 SQLite barrier_join_log 表 | ✅ PASSED |
| T-EVP-19 | barrier_join_log 表包含正确的字段 | ✅ PASSED |
| T-EVP-20 | write_barrier_join 写入失败时静默降级 | ✅ PASSED |

---

### TASK-0411 M40-O：ModelPlane.md §10 更新实现状态

**文件**：`docs/30_devspec/ModelPlane.md`

**变更**：在 §10.3 能力优先级三档分类表的第二档中新增：
- `barrier_join()`（并行分支 fan-in 校验）：✅ M40 已实现
- `RenderGuard`（渲染输出授权）：✅ M40 已实现

---

### TASK-0412~0415 M40-P~S：契约测试行数限制检查

**文件**：`tests/contract/test_orch_refactor_contract.py`

**结论**：M40 新增代码后，各文件行数均在现有限制内，无需更新：
- `graph_runtime.py`：490 行（限制 530）✅
- `graph_contract.py`：441 行（限制 500）✅
- `graph_executor.py`：423 行（限制 450）✅
- `graph_checkpoint.py`：在限制内 ✅

契约测试 22 个全部通过，无回归。

---

## 三、测试汇总

### 新增测试（27 个，全部通过）

| 测试文件 | 测试数量 | 通过 |
|---------|---------|------|
| `test_barrier_join_m40.py` | 10 | 10 ✅ |
| `test_render_guard_m40.py` | 8 | 8 ✅ |
| `test_barrier_join_executor_integration_m40.py` | 3 | 3 ✅ |
| `test_render_guard_executor_integration_m40.py` | 3 | 3 ✅ |
| `test_evidence_barrier_join_m40.py` | 3 | 3 ✅ |
| **合计** | **27** | **27 ✅** |

### 契约测试（22 个，全部通过）

`tests/contract/test_orch_refactor_contract.py`：22 passed ✅

---

## 四、架构影响分析

### 4.1 barrier_join() 的治理语义

`barrier_join()` 实现了 `LangGraph_Governed_Runtime_Solution §4.2` 中的"事后 barrier 校验"：

- **事前**：`build_audit_ticket()` 在 fan-out 前签发审计工单
- **事中**：各分支在 envelope 定义范围内执行
- **事后**：`barrier_join()` 校验所有分支的 `audit_commit`，缺任一证据则 fail closed

这确保了并行分支的审计闭环，防止未经审计的分支输出进入主图。

### 4.2 RenderGuard 的边界锁定

`RenderGuard` 实现了 `LangGraph_Governed_Runtime_Solution §4.5` 中的"分相流式 + 快照锁定"：

- Render Stream 只能读取 Commit Stream 对应的快照（`commit_snapshot`）
- `locked_fields` 中的字段不允许被 render 修改（保护 `intent_commit`、`s1_output` 等核心字段）
- 允许修改的派生字段：`render_text`、`render_html`、`render_markdown`、`display_*` 前缀

### 4.3 Evidence Plane 扩展

新增 `barrier_join_log` 表，完整记录每次 fan-in 校验的结果，支持：
- 审计追溯：通过 `request_id` 查询所有 barrier_join 记录
- 失败分析：`failed_branches_json` 记录所有缺证据的分支 ID
- 策略追踪：`join_policy_ref` 记录 partial 模式的策略引用

---

## 五、不变量验证

| 不变量 | 验证结果 |
|--------|---------|
| LLM 节点不裁决、不执行 | ✅ barrier_join 和 RenderGuard 均由 Host 执行 |
| committed_* 字段只能由 Host 写入 | ✅ 分支节点输出中 committed_* 字段被 _strip_committed_fields 移除 |
| 并行分支 fail closed | ✅ strict 模式下任一分支缺 audit_commit 则 passed=False |
| Render 不消费未提交状态 | ✅ commit_snapshot 为 None 时 RenderGuard fail closed |
| 写入失败静默降级 | ✅ write_barrier_join 异常时 logger.warning，不抛出 |

---

## 六、后续里程碑建议

M40 完成后，Governed Runtime Contract 的五条规则均已有对应实现：

| 规则 | 实现里程碑 |
|------|-----------|
| Rule 1：Route Commitment Rule | ✅ M34/M35 |
| Rule 2：Parallel Branch Pre-Issuance Rule | ✅ M37（branch_manifest）+ M40（barrier_join）|
| Rule 3：Transparent Subgraph Rule | ✅ M35/M37 |
| Rule 4：Dual-Clock Rule | ✅ M36/M39 |
| Rule 5：Phase-Locked Streaming Rule | ✅ M38 + M40（RenderGuard）|

建议下一步关注：
1. Evidence Plane 写入压力评估（分级写入、异步落库）
2. 带副作用的并行分支（需要更严格的 audit_ticket 预签发机制）
3. 跨阶段 token 级联动 streaming（第三档，暂不建议）
