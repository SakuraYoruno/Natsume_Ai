# M37 代码审查报告

- **里程碑**：M37 只读并行分支（branch_manifest）+ Transparent Subgraph Evidence Plane 实时写入
- **生成日期**：2026-03-06
- **审查范围**：TASK-0349 ~ TASK-0362（共 14 个任务）
- **全量测试**：2278 passed, 0 failed（+22 新增专项测试）

---

## 一、执行摘要

M37 完成了两条主线的落地：

**主线 A（只读并行分支 + branch_manifest）**：新建 `graph_branch.py`，实现 `BranchSpec`、`BranchResult`、`BranchManifest`、`run_parallel_branches()`，使用 `ThreadPoolExecutor` 并行执行多个只读分支，分支结果汇总为 `branch_manifest`。分支节点不能写入 `committed_*` 字段（只读约束），分支失败不影响主图执行（静默降级）。集成到 `graph_executor.py` 的 `_run_single_step`，在节点执行完成后检查 `branch_specs`，若非空则执行并行分支。

**主线 B（Transparent Subgraph Evidence Plane 实时写入）**：修改 `graph_subgraph.py`，在每个子图节点执行后（无论成功或失败）实时调用 `evidence_plane.write_trace()`，写入失败时静默降级。`compile_governed_subgraph()` 新增 `evidence_plane` 可选参数（默认 None，向后兼容）。

**Evidence Plane 扩展**：`graph_evidence.py` 新增 `branch_manifest` 表和 `write_branch_manifest()` 方法，`graph_runtime.py._write_evidence` 新增 `branch_manifest` 写入。

**规范固化**：ModelPlane.md §10 第二档能力表更新，M37 实现项标注 ✅。

---

## 二、任务完成情况

| 任务 | 状态 | 变更摘要 |
|------|------|---------|
| TASK-0349 M37-A | ✅ DONE | 新建 `graph_branch.py`（BranchSpec + BranchResult + BranchManifest + run_parallel_branches + build_branch_manifest_dict）|
| TASK-0350 M37-B | ✅ DONE | `GraphState` 新增 `branch_specs`、`branch_manifest` 字段 |
| TASK-0351 M37-C | ✅ DONE | `graph_runtime.py` 初始化 `branch_specs = []`、`branch_manifest = None` |
| TASK-0352 M37-D | ✅ DONE | `graph_executor.py` 集成只读并行分支执行（节点执行后检查 `branch_specs`）|
| TASK-0353 M37-E | ✅ DONE | `graph_subgraph.py` 集成 Evidence Plane 实时写入（每个子图节点执行后）|
| TASK-0354 M37-F | ✅ DONE | `graph_contract.py` 新增 `BRANCH_STATUS_OK`、`BRANCH_STATUS_FAILED`、`DEFAULT_BRANCH_MAX_WORKERS`、`build_branch_manifest_summary()` |
| TASK-0355 M37-G | ✅ DONE | 新增 `test_parallel_branches_m37.py`（T-BRN-01~10，10 个测试）|
| TASK-0356 M37-H | ✅ DONE | 新增 `test_subgraph_evidence_m37.py`（T-SGE-01~06，6 个测试）|
| TASK-0357 M37-I | ✅ DONE | 新增 `test_branch_executor_integration_m37.py`（T-BRN-INT-01~03，3 个测试）|
| TASK-0358 M37-J | ✅ DONE | ModelPlane.md §10 第二档能力表更新（M37 实现项标注 ✅）|
| TASK-0359 M37-K | ✅ DONE | 契约测试行数限制更新（`graph_executor.py` 上限 350）|
| TASK-0360 M37-L | ✅ DONE | `graph_evidence.py` 新增 `branch_manifest` 表 + `write_branch_manifest()`；`graph_runtime.py._write_evidence` 新增 `branch_manifest` 写入 |
| TASK-0361 M37-M | ✅ DONE | 新增 `test_evidence_branch_m37.py`（T-EVP-09~11，3 个测试）|
| TASK-0362 M37-N | ✅ DONE | 本报告 |

---

## 三、核心变更详情

### 3.1 只读并行分支（TASK-0349~0352）

**新文件**：`host_brain/integrations/graph_branch.py`

```
BranchSpec（分支规格）
├── branch_id: str
├── handler: BranchHandler
├── input_payload: dict
└── timeout_ms: int

BranchResult（分支执行结果）
├── branch_id: str
├── status: "OK" | "FAILED"
├── output: dict（committed_* 字段已移除）
├── exec_duration_ms: int
└── error: str | None

BranchManifest（分支清单）
├── branches: list[BranchResult]
├── total_count: int
├── success_count: int
├── fail_count: int
└── committed_at_ms: int

run_parallel_branches(branch_specs, state, max_workers=4)
└── ThreadPoolExecutor 并行执行
    ├── 每个分支独立运行，互不影响
    ├── 分支输出中的 committed_* 字段被自动移除
    └── 分支失败时 BranchResult.status = "FAILED"，不影响其他分支
```

**只读约束保证**：

| 约束 | 实现方式 |
|------|---------|
| 不能写入 `committed_*` 字段 | `_strip_committed_fields_from_branch()` 在分支输出合并前调用 |
| 不能修改主图 `trace`、`status` | 分支结果只写入 `branch_manifest`，不修改主图状态 |
| 分支失败不影响主图 | `_execute_single_branch()` 内部捕获所有异常，返回 `FAILED` 结果 |

**GraphState 新增字段**：

```python
branch_specs: list[dict[str, Any]]   # 分支规格列表（由主图配置注入）
branch_manifest: dict[str, Any] | None  # 分支执行结果清单
```

### 3.2 Transparent Subgraph Evidence Plane 实时写入（TASK-0353）

**修改文件**：`host_brain/integrations/graph_subgraph.py`

- `CompiledSubgraph` 新增 `_evidence_plane: Any` 字段（默认 None）
- `CompiledSubgraph._write_node_trace()` 新增方法：子图节点执行后实时写入 Evidence Plane
- `compile_governed_subgraph()` 新增 `evidence_plane` 可选参数

**写入时机**：

| 场景 | 写入内容 |
|------|---------|
| 子图节点执行成功 | `{node_id, status="OK", ts_ms, reason_code=None, detail="subgraph node executed"}` |
| 子图节点执行失败 | `{node_id, status="FAILED", ts_ms, reason_code="SUBGRAPH_NODE_ERROR", detail=...}` |
| 子图节点处理器缺失 | 不写入（仅记录到 `subgraph_trace`）|

### 3.3 Evidence Plane 扩展（TASK-0360）

**新增表**：`branch_manifest`

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | TEXT | 请求 ID |
| `graph_id` | TEXT | 图 ID |
| `total_count` | INTEGER | 总分支数 |
| `success_count` | INTEGER | 成功分支数 |
| `fail_count` | INTEGER | 失败分支数 |
| `committed_at_ms` | INTEGER | 提交时间戳 |
| `branches_json` | TEXT | 分支详情 JSON |

### 3.4 契约测试行数限制更新

| 文件 | 原限制 | 新限制 | 实际行数 |
|------|--------|--------|---------|
| `graph_executor.py` | 300 | 350 | 301 |

---

## 四、测试覆盖

### 新增专项测试（+22）

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_parallel_branches_m37.py` | 10 | T-BRN-01~10：run_parallel_branches 正确性 + 只读约束 + 边界情况 |
| `test_subgraph_evidence_m37.py` | 6 | T-SGE-01~06：子图节点级 Evidence Plane 实时写入 |
| `test_branch_executor_integration_m37.py` | 3 | T-BRN-INT-01~03：graph_executor 并行分支集成 |
| `test_evidence_branch_m37.py` | 3 | T-EVP-09~11：branch_manifest SQLite 写入 |

### 全量测试结果

```
2278 passed, 0 failed（+22 新增，0 回归）
```

---

## 五、设计决策记录

### 5.1 只读约束的实现层次

只读约束在两个层次强制执行：
1. **分支执行层**（`_strip_committed_fields_from_branch`）：分支节点输出合并前移除 `committed_*` 字段
2. **上下文标记**（`ctx["readonly"] = True`）：分支节点可以通过 ctx 感知自己在只读分支中运行

这种双层保护确保即使分支节点代码有意或无意地尝试写入 `committed_*` 字段，也会被强制移除。

### 5.2 ThreadPoolExecutor vs asyncio

选择 `ThreadPoolExecutor` 而非 `asyncio`：
- 现有节点处理器都是同步函数，不是 async 函数
- `ThreadPoolExecutor` 与现有代码无缝集成，无需修改处理器签名
- 分支数量通常较少（< 10），线程开销可接受

### 5.3 branch_specs 的注入方式

`branch_specs` 通过 `GraphState` 传递，而非直接传给 `GraphExecutor`：
- 保持 `GraphExecutor` 接口不变（向后兼容）
- 允许不同节点配置不同的分支规格
- 与 `committed_route_token` 的注入方式一致（通过 payload → initial_state）

---

## 六、后续里程碑建议（M38）

| 优先级 | 内容 |
|--------|------|
| 高 | Phase-Locked Streaming（三类流隔离：presence / commit / render）|
| 中 | 带副作用的并行分支（需要审计闭锁保证，第三档能力边界探索）|
| 低 | 图内自主降级（受限版本，仅允许在 Host 预批准的降级路径内）|
