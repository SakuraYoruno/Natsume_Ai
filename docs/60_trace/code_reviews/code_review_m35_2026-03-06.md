# M35 代码审查报告

- **里程碑**：M35 Route Commitment Rule 完整落地 + Transparent Subgraph 审计透明子图
- **生成日期**：2026-03-06
- **审查范围**：TASK-0323 ~ TASK-0334（共 12 个任务）
- **全量测试**：2238 passed, 0 failed（+20 新增专项测试）

---

## 一、执行摘要

M35 完成了两条主线的落地：

**主线 A（Route Commitment Rule 完整落地）**：LangGraph 条件边现在真正读取 `committed_route_token`，不再直接消费 LLM 原始输出。`GOV_OK_RENDER` → 正常执行；`GOV_FAIL_FALLBACK` → 跳转 fallback；`GOV_AIRLOCK_REVIEW` → AIRLOCK 暂停；`GOV_SECONDARY_REASONER` → 安全终止。

**主线 B（Transparent Subgraph 第一阶段）**：实现 `compile_governed_subgraph()` 工厂，子图内部节点对 Evidence Plane 透明，每个节点执行后写入 `subgraph_trace`，子图结束时输出 `subgraph_commit_summary`，子图不允许写入 `committed_*` 字段。

**规范固化**：ModelPlane.md §10 能力三档分类表更新，M35 实现项标注 ✅。

---

## 二、任务完成情况

| 任务 | 状态 | 变更摘要 |
|------|------|---------|
| TASK-0323 M35-A | ✅ DONE | `GraphState` 新增 `committed_route_token`、`committed_route_token_detail`、`subgraph_trace` 字段 |
| TASK-0324 M35-B | ✅ DONE | `graph_runtime.py` 从 payload 提取 `committed_route_token` 到 `initial_state` |
| TASK-0325 M35-C | ✅ DONE | `fastapi_app.py` 在 `run_graph` 前注入 `committed_route_token` 到 payload |
| TASK-0326 M35-D | ✅ DONE | `graph_executor.py` `_route_after_step` 读取 `committed_route_token` 做条件路由 |
| TASK-0327 M35-E | ✅ DONE | 新建 `graph_subgraph.py`，实现 `compile_governed_subgraph()` 工厂 |
| TASK-0328 M35-F | ✅ DONE | `graph_contract.py` 新增 `build_subgraph_commit_summary()` 辅助函数 |
| TASK-0329 M35-G | ✅ DONE | 新增 `test_route_commitment_rule_m35.py`（T-RCR-01~10，10 个测试） |
| TASK-0330 M35-H | ✅ DONE | 新增 `test_transparent_subgraph_m35.py`（T-TSG-01~08 + 额外，10 个测试） |
| TASK-0331 M35-I | ✅ DONE | ModelPlane.md §10 能力三档分类表更新（M35 实现项标注 ✅） |
| TASK-0332 M35-J | ✅ DONE | `graph_fallback.py` 新增 `handle_airlock_pending()` 函数 |
| TASK-0333 M35-K | ✅ DONE | `graph_contract.py` 新增 `STATUS_AIRLOCK_PENDING = "AIRLOCK_PENDING"` 常量 |
| TASK-0334 M35-L | ✅ DONE | 本报告 |

---

## 三、核心变更详情

### 3.1 GraphState 新增字段（TASK-0323 M35-A）

**文件**：`host_brain/integrations/graph_contract.py`

```python
# M35-A：Route Commitment Rule 字段（ModelPlane.md §10 Rule 1）
committed_route_token: str | None          # Governor 已提交的路由令牌
committed_route_token_detail: dict | None  # 完整 CommittedRouteDecision 字典（审计用）
# M35-A：Transparent Subgraph 字段（ModelPlane.md §10 Rule 3）
subgraph_trace: list[dict[str, Any]]       # 透明子图节点执行追踪

# M35-K：AIRLOCK 暂停状态常量
STATUS_AIRLOCK_PENDING = "AIRLOCK_PENDING"
```

### 3.2 Route Commitment Rule 完整落地（TASK-0324/0325/0326）

**注入链路**（fastapi_app.py → graph_runtime.py → graph_executor.py）：

```
Governor.evaluate_intent()
    ↓ committed_route_token_ref = "GOV_OK_RENDER"
fastapi_app.py 注入到 payload
    ↓ payload["committed_route_token"] = "GOV_OK_RENDER"
graph_runtime.py 提取到 initial_state
    ↓ initial_state.committed_route_token = "GOV_OK_RENDER"
graph_executor._route_after_step() 读取令牌
    ↓ token == "GOV_OK_RENDER" → 正常执行
    ↓ token == "GOV_FAIL_FALLBACK" → finish（fallback）
    ↓ token == "GOV_AIRLOCK_REVIEW" → finish（AIRLOCK 暂停）
    ↓ token == None → 保持原有路由逻辑（向后兼容）
```

**条件边路由规则**（`_route_after_step`）：

| `committed_route_token` | 路由结果 | 说明 |
|------------------------|---------|------|
| `None` | 原有逻辑 | 向后兼容 |
| `GOV_OK_RENDER` | 原有逻辑 | 正常执行 |
| `GOV_FAIL_FALLBACK` | `"finish"` | 跳转 fallback 链路 |
| `GOV_AIRLOCK_REVIEW` | `"finish"` | AIRLOCK 暂停，status = AIRLOCK_PENDING |
| `GOV_SECONDARY_REASONER` | `"finish"` | 安全终止 |

**节点执行前拦截**（`_run_single_step`）：

- `GOV_AIRLOCK_REVIEW` → 调用 `handle_airlock_pending()`，不执行任何节点
- `GOV_FAIL_FALLBACK` → 跳转 fallback 节点，令牌消费后清空（避免 fallback 节点再次被拦截）

### 3.3 Transparent Subgraph 第一阶段（TASK-0327/0328）

**新文件**：`host_brain/integrations/graph_subgraph.py`

```python
def compile_governed_subgraph(
    subgraph_id: str,
    node_ids: list[str],
    handlers: dict[str, NodeHandler],
) -> CompiledSubgraph:
    """编译透明子图工厂函数（ModelPlane.md §10 Rule 3）"""
```

**透明性保证**：
- 每个节点执行后写入 `subgraph_trace`（含 `node_id`、`status`、`ts_ms`、`reason_code`）
- 子图结束时生成 `subgraph_commit_summary`（含 `node_count`、`success_count`、`fail_count`、`graph_trace_flat`）
- 权限约束：`_strip_committed_fields()` 自动移除子图节点输出中的 `committed_*` 字段
- 子图失败时 `status = STATUS_DEGRADED`，不影响主图继续执行

### 3.4 AIRLOCK 暂停处理（TASK-0332 M35-J）

**文件**：`host_brain/integrations/graph_fallback.py`

```python
def handle_airlock_pending(*, state: GraphState, node_id: str) -> GraphState:
    """AIRLOCK 暂停：status = AIRLOCK_PENDING，安全终止，不触发 fallback"""
```

与 `GOV_FAIL_FALLBACK` 的区别：
- `GOV_FAIL_FALLBACK` → 跳转 fallback 链路（降级执行）
- `GOV_AIRLOCK_REVIEW` → 安全终止（等待人工审核，不降级）

---

## 四、测试覆盖

### 新增专项测试（+20）

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_route_commitment_rule_m35.py` | 11 | T-RCR-01~10 + T-RCR-04b：条件边路由、AIRLOCK 暂停、payload 注入、Governor 令牌映射 |
| `test_transparent_subgraph_m35.py` | 9 | T-TSG-01~08 + 额外：子图透明性、committed_* 权限约束、失败降级、统计正确性 |

### 全量测试结果

```
2238 passed, 0 failed（+20 新增，0 回归）
```

---

## 五、设计决策记录

### 5.1 GOV_FAIL_FALLBACK 令牌消费后清空

在 `_run_single_step` 中，`GOV_FAIL_FALLBACK` 令牌触发 fallback 跳转后，将 `committed_route_token` 清空为 `None`。这是为了避免 fallback 节点在下一步执行时再次被令牌拦截（fallback 节点本身是合法的降级执行路径）。

### 5.2 子图不影响主图 committed_route_token

`_strip_committed_fields()` 确保子图节点输出中的 `committed_*` 字段被移除，防止子图节点绕过 Host Governor 写入路由令牌。这是 ModelPlane.md §10 Rule 3 的核心权限约束。

### 5.3 Transparent Subgraph 第一阶段的局限

M35 实现的是子图透明性的**基础层**：
- ✅ 节点执行追踪（subgraph_trace）
- ✅ 执行摘要（subgraph_commit_summary）
- ✅ committed_* 权限约束
- ❌ Evidence Plane 实际写入（需要 SQLiteLedger 集成，留给后续里程碑）
- ❌ 子图 checkpoint/interrupt/resume（需要 LangGraph 子图编译，留给后续里程碑）

---

## 六、后续里程碑建议

| 优先级 | 内容 | 依赖 |
|--------|------|------|
| 高 | M36：`dual-clock supervision` 双时钟监督（Execution Clock + Policy Clock） | M35 Route Commitment Rule |
| 中 | M36：Transparent Subgraph Evidence Plane 写入（SQLiteLedger 集成） | M35 subgraph_commit_summary |
| 中 | M37：只读并行分支 + `branch_manifest` | M36 双时钟 |
| 低 | M38：Phase-Locked Streaming（三类流隔离） | M37 并行分支 |
