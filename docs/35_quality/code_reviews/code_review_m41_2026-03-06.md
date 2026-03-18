受众：MASTER
标题：M41 代码审查报告

# M41 代码审查报告

- **里程碑**：M41 — Evidence Plane 写入压力评估与分级写入优化
- **生成日期**：2026-03-06
- **审查人**：AI 代码审查
- **全量测试结果**：2389 passed, 10 skipped, 0 failed

---

## 一、里程碑目标回顾

评估 Governed Runtime Contract 五条规则全部落地后 Evidence Plane 的写入压力，实现分级写入策略（Critical 同步 / Non-critical 异步队列）、写入失败熔断机制、分区索引优化，防止 Evidence Plane 成为生产吞吐瓶颈。

---

## 二、变更摘要

### TASK-0417 M41-A：Evidence Plane 写入延迟基准测量工具

**状态**：DONE

**新增文件**：
- `host_brain/integrations/evidence_bench.py`
- `tests/unit/test_evidence_bench_m41.py`

**实现要点**：
- `EvidenceBench` 类提供 `measure_write_latency(method, n=1000, *args, **kwargs)` 方法
- 内部使用 `time.perf_counter()` 精确计时，单位毫秒
- 返回 `{p50, p95, p99, mean, max}` 延迟统计字典
- `n=0` 时抛出 `ValueError`

**通过测试**：T-EVP-BENCH-01~04（+ 1 个边界测试），共 5 个

---

### TASK-0418 M41-B：Evidence Plane 写入优先级分级枚举

**状态**：DONE

**新增文件**：
- `host_brain/integrations/evidence_priority.py`
- `tests/unit/test_evidence_priority_m41.py`

**实现要点**：
- `WritePriority(Enum)` 枚举：`CRITICAL = "critical"`、`NON_CRITICAL = "non_critical"`
- `WRITE_METHOD_PRIORITY` 字典：完整映射所有 `write_*` 方法的默认优先级
- 分级规则：
  - `CRITICAL`：`write_route_token`、`write_branch_manifest`、`write_barrier_join`、`write_checkpoint`
  - `NON_CRITICAL`：`write_llm_call`、`write_subgraph_trace`、`write_render_event`、`write_trace`、`write_subgraph_summary`、`write_clock_events`、`write_stream_events`

**通过测试**：T-EVP-PRI-01~06（+ 2 个枚举值测试），共 8 个

---

### TASK-0419 M41-C：Evidence Plane 异步写入队列

**状态**：DONE

**新增文件**：
- `host_brain/integrations/evidence_write_queue.py`
- `tests/unit/test_evidence_write_queue_m41.py`

**实现要点**：
- `EvidenceWriteQueue` 类，内部使用 `collections.deque` 作为缓冲区
- `enqueue(write_fn, *args, **kwargs)`：入队，队满时自动触发 `flush()`
- `flush(timeout_ms=None)`：批量执行，支持超时限制，返回剩余任务数
- `queue_size()`：返回当前队列大小
- `clear()`：清空队列
- 默认 `max_size=500`，可配置
- 写入失败时静默降级（不影响后续任务）

**通过测试**：T-EVP-QUEUE-01~06（+ 1 个失败降级测试），共 7 个

---

### TASK-0420 M41-D：Evidence Plane 写入失败熔断机制

**状态**：DONE

**新增文件**：
- `host_brain/integrations/evidence_circuit_breaker.py`
- `tests/unit/test_evidence_circuit_breaker_m41.py`

**实现要点**：
- `CircuitState(Enum)`：`CLOSED`、`OPEN`、`HALF_OPEN` 三态
- `EvidenceCircuitBreaker` 类：
  - `failure_threshold`（默认 5）：连续失败阈值
  - `recovery_timeout_ms`（默认 10000）：OPEN → HALF_OPEN 等待时间
  - `call(write_fn, record=None, *args, **kwargs)`：通过熔断器执行写入
  - `get_buffer_snapshot()`：返回内存缓冲副本
  - `clear_buffer()`：清空内存缓冲
  - `reset()`：重置到 CLOSED 状态（测试用）
- 状态转换：
  - `CLOSED` → `OPEN`：连续失败 ≥ `failure_threshold`
  - `OPEN` → `HALF_OPEN`：经过 `recovery_timeout_ms` 毫秒
  - `HALF_OPEN` → `CLOSED`：探测写入成功
  - `HALF_OPEN` → `OPEN`：探测写入失败
- `OPEN` 状态下写入转入 `_memory_buffer`，不丢失

**通过测试**：T-EVP-CB-01~08（+ 1 个 reset 测试），共 9 个

---

### TASK-0421 M41-E：Evidence Plane 分区索引优化

**状态**：DONE

**修改文件**：
- `host_brain/integrations/graph_evidence.py`

**新增文件**：
- `tests/unit/test_evidence_index_m41.py`

**实现要点**：
- 在 `_init_db()` 的 `executescript` 中新增 `subgraph_trace` 和 `llm_call_log` 两张新表
- 新增 6 个分区索引（全部使用 `IF NOT EXISTS` 保证幂等）：
  - `idx_bjl_run_id`：`barrier_join_log(request_id)`
  - `idx_bjl_ts`：`barrier_join_log(ts_ms)`
  - `idx_sgt_run_id`：`subgraph_trace(run_id)`
  - `idx_sgt_parent_path`：`subgraph_trace(parent_path)`
  - `idx_lcl_run_id`：`llm_call_log(run_id)`
  - `idx_lcl_node_id`：`llm_call_log(node_id)`
- 同步修复 `:memory:` 模式下连接复用问题（新增 `_memory_conn` 缓存）

**通过测试**：T-EVP-IDX-01~06（+ 1 个幂等测试），共 7 个

---

### TASK-0422 M41-F：EvidencePlane 写入压力专项测试

**状态**：DONE

**新增文件**：
- `tests/unit/test_evidence_perf_m41.py`

**实现要点**：
- 全部使用 mock 模拟，不依赖真实环境
- 覆盖场景：
  - 100 次 CRITICAL 写入全部同步完成
  - 100 次 NON_CRITICAL 写入全部进入队列，flush 后落库
  - 混合 50+50 写入，CRITICAL 同步、NON_CRITICAL 入队
  - 写入失败注入触发熔断，CRITICAL 写入转入内存缓冲不丢失
  - 熔断后内存缓冲可通过 `get_buffer_snapshot()` 取出重放

**通过测试**：T-EVP-PERF-01~05，共 5 个

---

### TASK-0423 M41-G：契约测试行数限制更新

**状态**：DONE

**结论**：`graph_evidence.py` 行数（约 595 行）未被契约测试 `TestOrchRefactorModuleSize` 检查（该测试只检查 `graph_runtime`、`graph_contract`、`graph_executor`、`graph_checkpoint`、`graph_fallback`、`graph_model_resolve`），无需修改契约测试。契约测试 22 个全部通过。

---

## 三、新增测试统计

| 测试文件 | 测试数 | 测试 ID |
|---------|--------|---------|
| `test_evidence_bench_m41.py` | 5 | T-EVP-BENCH-01~04 + 边界 |
| `test_evidence_priority_m41.py` | 8 | T-EVP-PRI-01~06 + 枚举值 |
| `test_evidence_write_queue_m41.py` | 7 | T-EVP-QUEUE-01~06 + 失败降级 |
| `test_evidence_circuit_breaker_m41.py` | 9 | T-EVP-CB-01~08 + reset |
| `test_evidence_index_m41.py` | 7 | T-EVP-IDX-01~06 + 幂等 |
| `test_evidence_perf_m41.py` | 5 | T-EVP-PERF-01~05 |
| **合计** | **41** | |

---

## 四、新增/修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `host_brain/integrations/evidence_bench.py` | 新增 | EvidenceBench 延迟测量工具 |
| `host_brain/integrations/evidence_priority.py` | 新增 | WritePriority 枚举 + 方法优先级映射 |
| `host_brain/integrations/evidence_write_queue.py` | 新增 | EvidenceWriteQueue 异步写入队列 |
| `host_brain/integrations/evidence_circuit_breaker.py` | 新增 | EvidenceCircuitBreaker 熔断器 |
| `host_brain/integrations/graph_evidence.py` | 修改 | 新增 subgraph_trace/llm_call_log 表、6 个分区索引、:memory: 连接复用修复 |
| `tests/unit/test_evidence_bench_m41.py` | 新增 | T-EVP-BENCH 测试 |
| `tests/unit/test_evidence_priority_m41.py` | 新增 | T-EVP-PRI 测试 |
| `tests/unit/test_evidence_write_queue_m41.py` | 新增 | T-EVP-QUEUE 测试 |
| `tests/unit/test_evidence_circuit_breaker_m41.py` | 新增 | T-EVP-CB 测试 |
| `tests/unit/test_evidence_index_m41.py` | 新增 | T-EVP-IDX 测试 |
| `tests/unit/test_evidence_perf_m41.py` | 新增 | T-EVP-PERF 测试 |

---

## 五、架构决策记录

### ADR-M41-01：WritePriority 独立模块

`WritePriority` 枚举和 `WRITE_METHOD_PRIORITY` 映射放在独立的 `evidence_priority.py` 中，而非直接修改 `graph_evidence.py`，原因：
1. 避免 `graph_evidence.py` 行数进一步膨胀
2. 优先级映射可被 `EvidenceWriteQueue` 和 `EvidenceCircuitBreaker` 独立引用，无循环依赖

### ADR-M41-02：:memory: 连接复用

SQLite `:memory:` 数据库每次 `connect()` 都创建独立实例，导致测试中 `_init_db()` 创建的表对后续连接不可见。通过在 `__init__` 中缓存 `_memory_conn` 并在 `_connect()` 中复用，解决了测试隔离问题，同时不影响文件数据库的多连接行为。

### ADR-M41-03：熔断器不集成到 EvidencePlane 内部

`EvidenceCircuitBreaker` 作为独立组件提供，而非直接集成到 `EvidencePlane` 的每个 `write_*` 方法中，原因：
1. 保持 `EvidencePlane` 的单一职责（SQLite 写入）
2. 调用方可按需选择是否启用熔断保护
3. 便于单独测试熔断逻辑

---

## 六、全量测试结果

```
2389 passed, 10 skipped, 0 failed
```

- M41 新增测试：41 个，全部通过
- 历史测试无回归
- 契约测试：22 个，全部通过
