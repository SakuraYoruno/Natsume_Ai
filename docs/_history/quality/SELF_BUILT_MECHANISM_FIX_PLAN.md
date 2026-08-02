受众：CODEX
标题：自研机制架构修复方案（v2.0）

# 自研机制架构修复方案（v2.0）

**文档编号**：FIX-PLAN-001
**日期**：2026-04-14
**版本**：v2.0（基于类人体架构分层洞察重写）
**状态**：草案（待评审）
**受众**：核心开发团队
**关联模块**：`graph_checkpoint.py` / `react_loop.py` / `graph_subgraph.py` / `tribe/` 族

---

## 0. 架构认知前提（重要，必读）

在阅读具体修复方案之前，必须先建立正确的架构认知。v1.0 文档将自研机制定性为"LangGraph 功能的替代品"，这个视角是**错误的**。正确的理解如下：

### 0.1 两层治理的本质区别

```
┌──────────────────────────────────────────────────────────────┐
│  L2 接入治理层（基础设施级）                                    │
│                                                              │
│  Governor ── 同步阻断裁决，执行前的最后防线                     │
│  AIRLOCK  ── 数据管道净化，出站/入站数据清洗                    │
│  Evidence Plane ── 原子性审计事务，不可分散                     │
│                                                              │
│  → 这层是"海关 + 防火墙 + 公证处"，不是认知决策                 │
│  → 不能交给 TRIBE 族，因为它需要同步阻断和事务原子性             │
└──────────────────────────────────────────────────────────────┘
         ↕ 事件通知（非阻断，异步）
┌──────────────────────────────────────────────────────────────┐
│  L3 认知人格层（TRIBE 族，认知级治理）                           │
│                                                              │
│  ImmuneAgent    ── 威胁记忆、模式识别、策略升级                  │
│  PrefrontalAgent── 长期自我约束、漂移检测                       │
│  EndocrineAgent ── 压力/激素状态，影响能量阈值                   │
│  CortexAgent    ── 路由意图聚合、上下文决策                      │
│                                                              │
│  → 这层做"认知级治理"，影响 Governor 的规则参数                  │
│  → 不是替代 Governor，而是 Governor 规则的动态提供者             │
└──────────────────────────────────────────────────────────────┘
```

### 0.2 正确的协作关系

```
TRIBE 族（认知治理）──影响──▶ Governor 规则参数缓存
                                      │
                                      ▼
                            Governor（基础设施治理）──裁决──▶ 执行
```

具体映射：

| TRIBE 族行为 | 影响的 Governor 参数 |
|---|---|
| `ImmuneAgent` 发现新攻击模式 | 更新 `safety_gate` 规则集 |
| `EndocrineAgent` 报告高压力 | 调低 `energy_gate` 阈值 |
| `PrefrontalAgent` 检测到漂移 | 收紧 `scope_policy_gate` |
| `CortexAgent` 更新意图模型 | 扩展 `intent_allowlist` |

### 0.3 为什么不能把审计交给 TRIBE 族

| 维度 | Governor/Evidence Plane（L2） | TRIBE 族（L3） |
|---|---|---|
| **执行时序** | 同步阻断，执行前裁决 | 异步事件驱动，执行后感知 |
| **原子性** | SQLite 事务，三联画原子写入 | 事件分散，无原子性保证 |
| **失败语义** | REJECT → 图停止 | 发布事件 → 谁来停图？ |
| **数据访问** | 直接访问完整 GraphState | 只能通过事件 payload 获取片段 |

**结论**：AIRLOCK 是数据管道操作，不是认知决策；Evidence Plane 需要事务原子性，不能分散；Governor 需要同步阻断，不能异步化。三者都不能交给 TRIBE 族。

### 0.4 可以完整使用 LangGraph 的正确路径

自研 `CheckpointManager`、`run_react_loop()`、`compile_governed_subgraph()` 的根本原因不是"LangGraph 不够用"，而是**历史原因**：这三套机制在 TRIBE 族化之前就已建立，且当时没有充分利用 LangGraph 原生能力。

正确的做法是：**让 LangGraph 承担执行编排，Host 持有路由主权**：

```python
# 治理协议化：LangGraph 执行，Host 主权不变
from langgraph.graph import StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

graph = StateGraph(GraphState)
graph.add_node("governor_node", governor_node_fn)
graph.add_node("execute_node", execute_node_fn)

# 条件边只读 committed_route_token（Host 写入的主权字段）
graph.add_conditional_edges(
    "governor_node",
    lambda s: s["committed_route_token"],   # ← Host 主权不放弃
    {
        "GOV_APPROVE":         "execute_node",
        "GOV_AIRLOCK_REVIEW":  END,
        "GOV_FAIL_FALLBACK":   "fallback_node",
    }
)

# LangGraph 原生 checkpoint（Execution Clock 真正生效）
checkpointer = SqliteSaver.from_conn_string("checkpoints.db")
compiled = graph.compile(checkpointer=checkpointer)
```

**保留不变的三个核心机制**：
1. `committed_route_token`（路由主权字段）
2. `Evidence Plane`（审计原子性）
3. `AIRLOCK`（数据净化管道）

**可以替换的三个自研机制**：
1. `CheckpointManager` → LangGraph `SqliteSaver`
2. `run_react_loop()` → LangGraph `ToolNode`（但工具调用仍需经过 Governor 门控）
3. `compile_governed_subgraph()` → LangGraph 原生 Subgraph

本文档的修复方案分为两个维度：
- **短期修复**：在不重构的前提下，修复现有自研机制的工程健壮性问题
- **中期迁移**：逐步迁移到完整 LangGraph 执行编排（§5）

---

## 1. CheckpointManager 修复方案

> **定性**：`CheckpointManager` 是历史遗留的自研 Execution Clock 实现。短期修复其工程问题；中期目标是迁移到 LangGraph `SqliteSaver`（见 §5.1）。

### 1.1 内存存储无 TTL，长期运行内存泄漏

**根因**：

```python
# graph_checkpoint.py - CheckpointStore.__init__
self._store: dict[str, dict[str, Any]] = {}  # 纯内存字典，无过期清理
```

每次 `GOV_AIRLOCK_REVIEW` 触发都写入一条 checkpoint，长期运行后内存持续增长，无法自动回收。

**修复方案**：增加 TTL 清理机制和容量上限

```python
class CheckpointStore:
    def __init__(
        self,
        *,
        db_path: str | None = None,
        max_memory_entries: int = 500,
        ttl_ms: int = 30 * 60 * 1000,      # 默认 TTL：30 分钟
    ) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._timestamps: dict[str, int] = {}
        self._max_memory_entries = max_memory_entries
        self._ttl_ms = ttl_ms
        self._write_lock = threading.Lock()  # 同时修复 1.2 的并发问题

    def save(self, state: dict[str, Any]) -> str:
        checkpoint_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        with self._write_lock:
            self._evict_expired(now)
            if len(self._store) >= self._max_memory_entries:
                oldest_id = min(self._timestamps, key=self._timestamps.get)
                del self._store[oldest_id]
                del self._timestamps[oldest_id]
            self._store[checkpoint_id] = dict(state)
            self._timestamps[checkpoint_id] = now
        if self._db_path:
            self._write_sqlite(checkpoint_id, state, now)
        return checkpoint_id

    def _evict_expired(self, now_ms: int) -> None:
        expired = [
            cid for cid, ts in self._timestamps.items()
            if now_ms - ts > self._ttl_ms
        ]
        for cid in expired:
            self._store.pop(cid, None)
            self._timestamps.pop(cid, None)
```

**SQLite 层同步增加定期清理**：

```sql
DELETE FROM checkpoints
WHERE created_at_ms < (strftime('%s', 'now') * 1000 - 1800000);  -- 30分钟
```

**涉及文件**：`host_brain/integrations/graph_checkpoint.py`
**优先级**：P2
**验收测试**：T-CKP-TTL-01（超过 max_memory_entries 后旧条目被驱逐）、T-CKP-TTL-02（TTL 过期后条目被清理）

---

### 1.2 内存层无并发写入保护

**根因**：`save()` 包含多步操作（生成 UUID → 写内存 → 写 SQLite），无锁保护，多线程并发时 SQLite 层触发 `database is locked`。

**修复方案**：已合并到 1.1 的 `_write_lock` 修复中，无需单独处理。

**涉及文件**：`host_brain/integrations/graph_checkpoint.py`
**优先级**：P2（与 1.1 合并为一个 PR）
**验收测试**：T-CKP-CONC-01（10 线程并发 save() 无异常，所有 checkpoint_id 唯一）

---

### 1.3 resume_graph() 缺少 GraphState schema 版本兼容 ⚠️ P1

**根因**：

```python
# graph_runtime.py - resume_graph()
state = manager.load_state(checkpoint_id)
compiled = executor.compile_runtime_graph()
result_state = compiled.invoke(state)  # 旧 checkpoint 缺少新字段 → KeyError
```

每次 `GraphState` 新增必填字段，旧 checkpoint 恢复时必然失败。

**修复方案**：在 `resume_graph()` 中增加 state 字段补全

```python
def resume_graph(self, *, checkpoint_id: str, request_id: str, ...) -> GraphRunResult:
    state = manager.load_state(checkpoint_id)
    if state is None:
        return GraphRunResult(status=STATUS_FAILED, reason_code="CHECKPOINT_NOT_FOUND", ...)

    # 新增：向前兼容旧 checkpoint
    state = _backfill_graph_state_defaults(state)
    ...

def _backfill_graph_state_defaults(state: dict[str, Any]) -> dict[str, Any]:
    """为旧版 checkpoint 补全新增的 GraphState 字段默认值。"""
    defaults: dict[str, Any] = {
        "active_agent_id": None,
        "handoff_history": [],
        "agent_budget": None,
        "agent_artifacts": None,
        "judge_result": None,
        "execution_profile": None,
        "presence_events": [],
        "commit_snapshot": None,
        "render_snapshot_ref": None,
        "streaming_phase": None,
        "gov_checkpoint_id": None,
        "interrupt_reason": None,
        "resume_allowed": None,
        "resume_deadline_at": None,
        "barrier_join_result": None,
        "render_guard_result": None,
        # TRIBE 族新增字段（L3 认知层，默认空）
        "tribe_governor_params": {},    # TRIBE 族写入的 Governor 规则参数
        "immune_threat_level": None,    # ImmuneAgent 威胁等级
        "endocrine_state": None,        # EndocrineAgent 激素状态
    }
    result = dict(state)
    for key, default_val in defaults.items():
        if key not in result:
            result[key] = default_val
    return result
```

> **注意**：`tribe_governor_params` 字段是新增的，用于承载 TRIBE 族对 Governor 规则的动态影响（见 §4.1）。

**涉及文件**：`host_brain/integrations/graph_runtime.py`
**优先级**：**P1**
**验收测试**：T-CKP-COMPAT-01（旧版 checkpoint 恢复后不抛 KeyError）

---

### 1.4 双时钟设计中 Execution Clock 被架空（文档问题）

**根因**：文档声称"LangGraph 负责 Execution Clock"，但实际 `resume_graph()` 是从 `pending_node_id` 重新执行整张图，LangGraph 的断点恢复能力完全未使用。

**修复方案**：

**短期（文档对齐）**：在 `resume_graph()` 注释中明确说明当前实现是有意简化：

```python
def resume_graph(self, ...) -> GraphRunResult:
    """从 checkpoint 恢复图执行。

    实现说明（重要）：
    当前实现从 state.pending_node_id 重新执行图，而非使用 LangGraph 原生
    checkpoint 恢复机制。这是历史遗留的简化实现：
    - 优点：不依赖 LangGraph 内部 checkpoint 格式，版本升级无兼容问题
    - 代价：有副作用的节点重新执行可能导致重复写入
    - 缓解：所有有副作用的节点必须实现幂等性（idempotent）
    - 中期目标：迁移到 LangGraph SqliteSaver（见 FIX-PLAN §5.1）
    """
```

**中期（真正修复）**：迁移到 LangGraph `SqliteSaver`，见 §5.1。

**涉及文件**：`host_brain/integrations/graph_runtime.py`（注释）、`docs/30_devspec/ModelPlane.md`（§10 Rule 6）
**优先级**：P3（短期文档对齐）→ P1（中期迁移）

---

## 2. ReAct 循环修复方案

> **定性**：`run_react_loop()` 是历史遗留的自研 ToolNode 实现。工具调用必须经过 Governor 门控这一约束是正确的，但实现方式可以更优雅（见 §5.2）。

### 2.1 同步串行执行，无并行工具调用支持

**根因**：

```python
# react_loop.py
for step_num in range(1, max_steps + 1):
    llm_response = gateway.complete(messages)   # 同步阻塞
    observation = execute_tool(tool_id, ...)    # 同步阻塞
```

LLM 提案多个工具调用时，只能顺序处理，无法并发。

**修复方案**：增加 `_execute_tools_parallel()` 支持多工具并发

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _execute_tools_parallel(
    tool_calls: list[dict[str, Any]],
    agent_id: str,
    allow_side_effects: bool,
    context: dict[str, Any],
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """并发执行多个工具调用。注意：WRITE 工具仍需串行（避免竞态）。"""
    # 分离 READ 和 WRITE 工具
    read_calls = [(i, c) for i, c in enumerate(tool_calls)
                  if not _is_write_tool(c.get("tool", ""))]
    write_calls = [(i, c) for i, c in enumerate(tool_calls)
                   if _is_write_tool(c.get("tool", ""))]

    results: list[dict[str, Any]] = [None] * len(tool_calls)

    def _run_one(idx: int, call: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        tool_id = str(call.get("tool") or "").strip()
        if not is_tool_allowed(tool_id, agent_id, allow_side_effects=allow_side_effects):
            return idx, {"error": f"TOOL_NOT_ALLOWED:{tool_id}", "result": None}
        runtime_args = dict(call.get("args") or {})
        if context:
            runtime_args["_react_context"] = dict(context)
        return idx, execute_tool(tool_id, runtime_args)

    # READ 工具并发执行
    if read_calls:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(read_calls))) as executor:
            futures = {executor.submit(_run_one, i, c): i for i, c in read_calls}
            for future in as_completed(futures):
                idx, obs = future.result()
                results[idx] = obs

    # WRITE 工具串行执行（保证顺序和安全）
    for idx, call in write_calls:
        _, obs = _run_one(idx, call)
        results[idx] = obs

    return results
```

**涉及文件**：`host_brain/app/agents/react_loop.py`
**优先级**：P2
**验收测试**：T-REACT-PARALLEL-01（多个 READ 工具并发执行，总耗时 < 串行 × 2）

---

### 2.2 消息历史无 token 预算管理

**根因**：消息列表无限增长，使用本地小模型时可能超出 context window。

**修复方案**：增加 `_trim_messages()` 修剪策略

```python
_MAX_REACT_HISTORY_CHARS = 8000  # 约 2000 tokens

def _trim_messages(
    messages: list[dict[str, str]],
    *,
    max_chars: int = _MAX_REACT_HISTORY_CHARS,
) -> list[dict[str, str]]:
    """修剪消息历史，始终保留 system prompt + 原始 user 问题。"""
    total_chars = sum(len(m.get("content") or "") for m in messages)
    if total_chars <= max_chars:
        return messages

    preserved_head = messages[:2]   # system + 原始 user 问题
    tail = messages[2:]
    kept_tail: list[dict[str, str]] = []
    remaining = max_chars - sum(len(m.get("content") or "") for m in preserved_head)

    for msg in reversed(tail):
        content_len = len(msg.get("content") or "")
        if remaining - content_len < 0:
            break
        kept_tail.insert(0, msg)
        remaining -= content_len

    if len(kept_tail) < len(tail):
        kept_tail.insert(0, {
            "role": "user",
            "content": f"[历史工具调用记录已截断，共 {len(tail) - len(kept_tail)} 轮被省略]",
        })

    return preserved_head + kept_tail

# 在每步 LLM 调用前修剪
messages = _trim_messages(messages)
llm_response = gateway.complete(messages)
```

**涉及文件**：`host_brain/app/agents/react_loop.py`
**优先级**：P2（使用本地小模型时为 P1）
**验收测试**：T-REACT-TRIM-01（消息历史超过阈值时被修剪，不抛 context_length_exceeded）

---

### 2.3 工具注册表静态，与 ToolCatalog 割裂

**根因**：

```python
# tool_registry.py - 模块级静态字典，进程启动后不可变
_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_knowledge": ToolSpec(...),
    ...
}
```

与 `host_brain/tools/catalog.py` 中已有的 `ToolCatalog`（支持运行时 `register()`）形成割裂，MCP 动态注册的工具无法进入 ReAct 循环。

**修复方案**：`get_tools_for_agent()` 增加 `catalog` 参数，支持从 `ToolCatalog` 动态获取

```python
def get_tools_for_agent(
    agent_id: str,
    *,
    catalog: "ToolCatalog | None" = None,
) -> list[ToolSpec]:
    """获取指定 agent 可用的工具列表。

    优先级：catalog（动态）> 静态 _TOOL_REGISTRY（向后兼容）
    """
    if catalog is not None:
        allowlist = _AGENT_TOOLS.get(agent_id, [])
        result = []
        for tool_id in allowlist:
            try:
                meta = catalog.get(tool_id)
                result.append(_tool_meta_to_spec(meta))
            except KeyError:
                pass
        return result
    allowlist = _AGENT_TOOLS.get(agent_id, [])
    return [_TOOL_REGISTRY[tid] for tid in allowlist if tid in _TOOL_REGISTRY]
```

**涉及文件**：`host_brain/app/agents/tool_registry.py`
**优先级**：P3
**验收测试**：T-REACT-DYN-01（运行时向 catalog 注册新工具后，get_tools_for_agent 能返回该工具）

---

### 2.4 ReAct 循环无流式输出支持

**根因**：`run_react_loop()` 是同步函数，整个 ReAct 过程对外是黑盒，用户无进度反馈。

**修复方案**：增加异步流式版本 `astream_react_loop()`

```python
from typing import AsyncGenerator

async def astream_react_loop(
    *,
    agent_id: str,
    user_input: str,
    tools: list[ToolSpec],
    gateway: Any,
    max_steps: int = 3,
    allow_side_effects: bool = False,
    context: dict[str, Any] | None = None,
) -> AsyncGenerator[ReactStep | ReactResult, None]:
    """ReAct 循环的异步流式版本，逐步 yield 每个 ReactStep。

    注意：当前为简化实现（等待完整结果后批量 yield）。
    真正的逐步流式需要修改 run_react_loop 内部，见 §5.2。
    """
    import asyncio, threading

    step_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _sync_runner():
        result = run_react_loop(
            agent_id=agent_id, user_input=user_input, tools=tools,
            gateway=gateway, max_steps=max_steps,
            allow_side_effects=allow_side_effects, context=context,
        )
        asyncio.run_coroutine_threadsafe(step_queue.put(result), loop)

    threading.Thread(target=_sync_runner, daemon=True).start()
    final_result = await step_queue.get()
    for step in final_result.steps:
        yield step
    yield final_result
```

**涉及文件**：`host_brain/app/agents/react_loop.py`
**优先级**：P3
**验收测试**：T-REACT-STREAM-01（astream_react_loop 正确 yield 所有 steps 后 yield ReactResult）

---

## 3. compile_governed_subgraph() 修复方案

> **定性**：`compile_governed_subgraph()` 是历史遗留的自研 Subgraph 实现。`committed_*` 字段的权限隔离约束是正确的，但可以在 LangGraph 原生 Subgraph 上实现同等约束（见 §5.3）。

### 3.1 子图仅支持线性序列，无条件分支能力

**根因**：

```python
# graph_subgraph.py
for node_id in self.node_ids:   # 严格顺序，无分支
    handler = self.handlers.get(node_id)
    output = handler(payload, context)
```

**修复方案**：增加可选的 `edge_map`（正常路由）和 `fallback_map`（降级路由）

```python
@dataclass
class CompiledSubgraph:
    subgraph_id: str
    node_ids: list[str]
    handlers: dict[str, NodeHandler]
    edge_map: dict[str, str] = field(default_factory=dict)      # 正常路由
    fallback_map: dict[str, str] = field(default_factory=dict)  # 降级路由

    def invoke(self, state: GraphState) -> GraphState:
        if self.edge_map:
            return self._invoke_with_routing(state)
        return self._invoke_linear(state)   # 向后兼容

    def _invoke_with_routing(self, state: GraphState) -> GraphState:
        subgraph_trace = list(state.get("subgraph_trace") or [])
        payload = dict(state.get("payload") or {})
        current_status = str(state.get("status") or STATUS_OK)
        current_node = self.node_ids[0] if self.node_ids else None
        visited: set[str] = set()
        max_hops = len(self.node_ids) * 2

        while current_node and len(visited) < max_hops:
            if current_node in visited:
                break
            visited.add(current_node)
            ts_ms = int(time.time() * 1000)
            handler = self.handlers.get(current_node)

            if handler is None:
                subgraph_trace.append({
                    "node_id": current_node, "status": STATUS_FAILED,
                    "ts_ms": ts_ms, "reason_code": "SUBGRAPH_HANDLER_MISSING",
                })
                current_status = STATUS_DEGRADED
                current_node = self.fallback_map.get(current_node)
                continue

            try:
                output = handler(payload, {
                    "request_id": str(state.get("request_id") or ""),
                    "node_id": current_node,
                    "subgraph_id": self.subgraph_id,
                    "timeout_ms": int(state.get("timeout_ms") or 5000),
                })
                _strip_committed_fields(output)
                payload.update(output or {})
                subgraph_trace.append({
                    "node_id": current_node, "status": STATUS_OK,
                    "ts_ms": ts_ms, "reason_code": None,
                })
                current_node = self.edge_map.get(current_node)
            except Exception as exc:
                subgraph_trace.append({
                    "node_id": current_node, "status": STATUS_FAILED,
                    "ts_ms": ts_ms, "reason_code": "SUBGRAPH_NODE_ERROR",
                    "detail": str(exc),
                })
                current_status = STATUS_DEGRADED
                current_node = self.fallback_map.get(current_node)

        return self._build_result(state, payload, subgraph_trace, current_status)
```

**涉及文件**：`host_brain/integrations/graph_subgraph.py`
**优先级**：P2
**验收测试**：T-SUBGRAPH-BRANCH-01（节点失败时走 fallback_map 路由，不中断子图执行）

---

### 3.2 `committed_*` 字段保护依赖字符串前缀，脆弱 ⚠️ P1

**根因**：

```python
# graph_subgraph.py
def _strip_committed_fields(output: dict[str, Any]) -> None:
    committed_keys = [k for k in list(output.keys()) if str(k or "").startswith("committed_")]
    for key in committed_keys:
        del output[key]
```

新增 Governor 字段（如 `gov_checkpoint_id`、`interrupt_reason`）若忘记加 `committed_` 前缀，保护就失效了。

**修复方案**：改为显式黑名单，从 `graph_contract.py` 统一维护

```python
# graph_contract.py 新增常量
SUBGRAPH_PROTECTED_FIELDS: frozenset[str] = frozenset({
    # committed_* 系列（路由主权）
    "committed_route_token",
    "committed_route_token_detail",
    # Governor 控制字段
    "gov_checkpoint_id",
    "interrupt_reason",
    "resume_allowed",
    "resume_deadline_at",
    "execution_profile",
    # TRIBE 族写入的 Governor 参数（L3 → L2 单向通道，子图不可覆写）
    "tribe_governor_params",
    "immune_threat_level",
    "endocrine_state",
    # 未来新增受保护字段在此处统一维护
})

# graph_subgraph.py
from host_brain.integrations.graph_contract import SUBGRAPH_PROTECTED_FIELDS

def _strip_committed_fields(output: dict[str, Any]) -> None:
    """移除受保护字段（显式黑名单，不依赖字段名前缀）。"""
    if not isinstance(output, dict):
        return
    for key in SUBGRAPH_PROTECTED_FIELDS:
        output.pop(key, None)
```

> **重要**：`tribe_governor_params`、`immune_threat_level`、`endocrine_state` 也加入保护集合，确保子图节点无法覆写 TRIBE 族写入的 Governor 参数（L3 → L2 的单向通道）。

**涉及文件**：`host_brain/integrations/graph_contract.py`、`host_brain/integrations/graph_subgraph.py`
**优先级**：**P1**
**验收测试**：T-SUBGRAPH-PROTECT-01（所有 SUBGRAPH_PROTECTED_FIELDS 字段被剥离）

---

### 3.3 子图无节点级 fallback 路由

此问题已被 3.1 的 `fallback_map` 修复覆盖。工厂函数同步更新：

```python
def compile_intent_agent_subgraph(
    handlers: dict[str, NodeHandler],
    evidence_plane: Any = None,
) -> CompiledSubgraph:
    return compile_governed_subgraph(
        subgraph_id="intent_agent_subgraph",
        node_ids=["core_reasoner_primary", "planner_constraints", "tool_planner"],
        handlers=handlers,
        evidence_plane=evidence_plane,
        fallback_map={
            "core_reasoner_primary": "planner_constraints",  # primary 失败 → 跳过，直接到 planner
        },
    )
```

**涉及文件**：`host_brain/integrations/graph_subgraph.py`
**优先级**：P2（依赖 3.1）
**验收测试**：T-SUBGRAPH-FALLBACK-01（core_reasoner_primary 失败时走 fallback_map）

---

## 4. TRIBE 族与 Governor 协作修复方案（新增）

> **这是 v2.0 新增的核心章节**，基于"TRIBE 族是 Governor 规则参数的动态提供者"这一架构洞察。

### 4.1 当前问题：TRIBE 族对 Governor 的影响路径缺失

**根因**：

当前 TRIBE 族（ImmuneAgent、EndocrineAgent、PrefrontalAgent）通过 `TribeEventBus` 发布事件，但 Governor 在裁决时**不感知任何 TRIBE 族的状态**，两层治理完全割裂。

```python
# 当前 governor_node_fn（简化）
def governor_node_fn(state: GraphState) -> dict:
    # 只读取静态规则，不感知 TRIBE 族状态
    result = governor.evaluate(
        intent=state["intent"],
        rules=STATIC_GOVERNOR_RULES,   # ← 静态规则，TRIBE 族无法影响
    )
    return {"committed_route_token": result.token}
```

**修复方案**：引入 `GovernorRulesCache`，作为 TRIBE 族 → Governor 的单向参数通道

```python
# host_brain/integrations/governor_rules_cache.py（新增）
import threading
from dataclasses import dataclass, field
from typing import Any

@dataclass
class GovernorRulesCache:
    """TRIBE 族写入、Governor 读取的规则参数缓存。

    这是 L3（认知层）→ L2（基础设施层）的单向通道：
    - TRIBE 族通过 update_*() 方法写入参数
    - Governor 通过 get_effective_rules() 读取合并后的规则
    - 写入是异步的（TRIBE 族事件驱动），读取是同步的（Governor 裁决时）
    """
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ImmuneAgent 写入
    _immune_threat_level: str = "NORMAL"        # "NORMAL" | "ELEVATED" | "CRITICAL"
    _immune_blocked_patterns: list[str] = field(default_factory=list)

    # EndocrineAgent 写入
    _energy_level: float = 1.0                  # 0.0 ~ 1.0，影响 energy_gate 阈值
    _stress_level: float = 0.0                  # 0.0 ~ 1.0，高压力时收紧策略

    # PrefrontalAgent 写入
    _drift_detected: bool = False               # 检测到人格漂移时收紧 scope_policy_gate
    _scope_override: str | None = None          # 临时 scope 限制

    def update_immune_state(self, threat_level: str, blocked_patterns: list[str]) -> None:
        with self._lock:
            self._immune_threat_level = threat_level
            self._immune_blocked_patterns = list(blocked_patterns)

    def update_endocrine_state(self, energy_level: float, stress_level: float) -> None:
        with self._lock:
            self._energy_level = max(0.0, min(1.0, energy_level))
            self._stress_level = max(0.0, min(1.0, stress_level))

    def update_prefrontal_state(self, drift_detected: bool, scope_override: str | None) -> None:
        with self._lock:
            self._drift_detected = drift_detected
            self._scope_override = scope_override

    def get_effective_rules(self) -> dict[str, Any]:
        """返回合并后的有效规则参数（Governor 裁决时调用）。"""
        with self._lock:
            return {
                "immune_threat_level": self._immune_threat_level,
                "immune_blocked_patterns": list(self._immune_blocked_patterns),
                "energy_gate_threshold": self._energy_level,
                "stress_tightening": self._stress_level > 0.7,
                "scope_policy_tightened": self._drift_detected,
                "scope_override": self._scope_override,
            }

# Governor 裁决时读取 TRIBE 族参数
def governor_node_fn(state: GraphState) -> dict:
    tribe_params = _governor_rules_cache.get_effective_rules()
    result = governor.evaluate(
        intent=state["intent"],
        rules={**STATIC_GOVERNOR_RULES, **tribe_params},  # 合并静态规则 + TRIBE 动态参数
    )
    return {
        "committed_route_token": result.token,
        "tribe_governor_params": tribe_params,  # 写入 state 供 Evidence Plane 审计
    }
```

**TRIBE 族事件处理器同步更新**：

```python
# ImmuneAgent 事件处理
@event_handler("immune_threat_detected")
def on_immune_threat(event: TribeEvent) -> None:
    _governor_rules_cache.update_immune_state(
        threat_level=event.payload.get("threat_level", "ELEVATED"),
        blocked_patterns=event.payload.get("blocked_patterns", []),
    )

# EndocrineAgent 事件处理
@event_handler("endocrine_state_updated")
def on_endocrine_update(event: TribeEvent) -> None:
    _governor_rules_cache.update_endocrine_state(
        energy_level=event.payload.get("energy_level", 1.0),
        stress_level=event.payload.get("stress_level", 0.0),
    )
```

**涉及文件**：`host_brain/integrations/governor_rules_cache.py`（新增）、`host_brain/integrations/graph_runtime.py`、`host_brain/tribe/immune_agent.py`、`host_brain/tribe/endocrine_agent.py`
**优先级**：**P1**（这是两层治理协作的核心缺失）
**验收测试**：T-TRIBE-GOV-01（ImmuneAgent 更新威胁等级后，Governor 下一次裁决使用新参数）

---

### 4.2 当前问题：Evidence Plane 不记录 TRIBE 族参数来源

**根因**：审计链只记录 Governor 的裁决结果，不记录裁决时使用的 TRIBE 族参数，无法追溯"为什么这次 Governor 更严格"。

**修复方案**：在 Evidence Plane 的 `governor_log` 中增加 `tribe_params_snapshot` 字段

```python
# audit/sqlite_ledger.py - log_governor_decision() 新增参数
def log_governor_decision(
    self,
    *,
    request_id: str,
    intent_id: str,
    route_token: str,
    reason_code: str,
    tribe_params_snapshot: dict | None = None,  # 新增：裁决时的 TRIBE 参数快照
    ...
) -> None:
    self._execute(
        """INSERT INTO governor_log (
            request_id, intent_id, route_token, reason_code,
            tribe_params_snapshot, ts_ms
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            request_id, intent_id, route_token, reason_code,
            json.dumps(tribe_params_snapshot or {}, ensure_ascii=False),
            now_ms(),
        ),
    )
```

**涉及文件**：`host_brain/audit/sqlite_ledger.py`、`host_brain/audit/migrations/`（新增迁移 SQL）
**优先级**：P2
**验收测试**：T-AUDIT-TRIBE-01（governor_log 包含 tribe_params_snapshot 字段）

---

## 5. 中期迁移方案：完整使用 LangGraph 执行编排

> 以下是中期目标，不是立即执行的修复，而是架构演进路径。前提：保留 `committed_route_token` + `Evidence Plane` + `AIRLOCK` 三个核心机制。

### 5.1 CheckpointManager → LangGraph SqliteSaver

```python
from langgraph.checkpoint.sqlite import SqliteSaver

# 迁移后：LangGraph 原生 checkpoint（Execution Clock 真正生效）
checkpointer = SqliteSaver.from_conn_string(
    os.environ.get("HOST_CHECKPOINT_DB", "host_brain/data/checkpoints.db")
)
compiled = graph.compile(checkpointer=checkpointer)

# resume_graph() 改为使用 LangGraph 原生恢复
result = compiled.invoke(
    None,  # 不传 state，由 checkpointer 从 thread_id 恢复
    config={"configurable": {"thread_id": checkpoint_id}},
)
```

**收益**：真正的断点恢复（不重新执行整张图）、节点级幂等性不再是必须要求
**前提**：`GraphState` 必须完全可序列化（当前已满足）
**风险**：LangGraph checkpoint 格式与版本绑定，升级时需要迁移脚本

---

### 5.2 run_react_loop() → LangGraph ToolNode（带 Governor 门控）

```python
from langgraph.prebuilt import ToolNode

# 关键：工具执行前仍需经过 Governor 门控
class GovernedToolNode(ToolNode):
    """在 LangGraph ToolNode 基础上增加 Governor 门控。"""

    def __call__(self, state: GraphState) -> dict:
        # 1. Governor 门控（保留，不可绕过）
        gov_result = governor.evaluate_tool_call(
            tool_calls=state.get("tool_calls", []),
            tribe_params=_governor_rules_cache.get_effective_rules(),
        )
        if gov_result.token != "GOV_APPROVE":
            return {"committed_route_token": gov_result.token}

        # 2. LangGraph 原生 ToolNode 执行
        result = super().__call__(state)

        # 3. Evidence Plane 审计（保留，不可绕过）
        _evidence_plane.log_tool_execution(...)

        return result
```

**收益**：利用 LangGraph 原生的工具调用解析、错误处理、重试机制
**前提**：Governor 门控必须保留，不能绕过

---

### 5.3 compile_governed_subgraph() → LangGraph 原生 Subgraph（带字段保护）

```python
from langgraph.graph import StateGraph

def build_governed_subgraph(
    subgraph_id: str,
    node_fns: dict[str, Callable],
) -> Any:
    """构建带 Governor 字段保护的 LangGraph 子图。"""

    # 子图使用独立的 SubgraphState（不包含 committed_* 字段）
    class SubgraphState(TypedDict):
        payload: dict
        subgraph_trace: list
        # 注意：不包含 committed_route_token 等受保护字段

    sub = StateGraph(SubgraphState)
    for node_id, fn in node_fns.items():
        sub.add_node(node_id, fn)
    # ... 添加边

    # 子图编译后，在主图中通过适配器调用
    compiled_sub = sub.compile()

    def subgraph_adapter(state: GraphState) -> dict:
        """适配器：隔离子图 state，防止子图写入受保护字段。"""
        sub_state = {
            "payload": dict(state.get("payload") or {}),
            "subgraph_trace": list(state.get("subgraph_trace") or []),
        }
        sub_result = compiled_sub.invoke(sub_state)
        # 只将非受保护字段合并回主图 state
        return {
            k: v for k, v in sub_result.items()
            if k not in SUBGRAPH_PROTECTED_FIELDS
        }

    return subgraph_adapter
```

**收益**：利用 LangGraph 原生的子图可视化、调试工具、条件边
**前提**：`SUBGRAPH_PROTECTED_FIELDS` 黑名单必须保留（3.2 的修复是前提）

---

## 6. 修复优先级汇总

| 编号 | 问题 | 优先级 | 涉及文件 | 工作量 |
|---|---|---|---|---|
| **1.3** | resume_graph() 缺少 schema 版本兼容 | **P1** | `graph_runtime.py` | 1d |
| **3.2** | 子图字段保护依赖字符串前缀，脆弱 | **P1** | `graph_contract.py`、`graph_subgraph.py` | 0.5d |
| **4.1** | TRIBE 族对 Governor 的影响路径缺失 | **P1** | `governor_rules_cache.py`（新增）、`graph_runtime.py`、各 tribe agent | 2d |
| 1.1+1.2 | CheckpointStore 无 TTL + 无写锁（合并） | P2 | `graph_checkpoint.py` | 1d |
| 2.1 | ReAct 无并行工具调用 | P2 | `react_loop.py` | 1d |
| 2.2 | ReAct 消息历史无 token 预算管理 | P2 | `react_loop.py` | 0.5d |
| 3.1+3.3 | 子图无条件分支 + 无 fallback 路由（合并） | P2 | `graph_subgraph.py` | 2d |
| 4.2 | Evidence Plane 不记录 TRIBE 参数来源 | P2 | `sqlite_ledger.py` | 0.5d |
| 1.4 | 双时钟文档对齐 | P3 | `graph_runtime.py`（注释） | 0.5d |
| 2.3 | 工具注册表静态，与 ToolCatalog 割裂 | P3 | `tool_registry.py` | 1d |
| 2.4 | ReAct 无流式输出 | P3 | `react_loop.py` | 1d |
| 5.1 | 迁移到 LangGraph SqliteSaver | P3（中期） | `graph_runtime.py` | 3d |
| 5.2 | 迁移到 LangGraph GovernedToolNode | P3（中期） | `react_loop.py` | 3d |
| 5.3 | 迁移到 LangGraph 原生 Subgraph | P3（中期） | `graph_subgraph.py` | 3d |

---

## 7. 建议执行顺序

```
第一阶段（P1，本周）：
  ├── FIX-1.3：resume_graph() schema 兼容（_backfill_graph_state_defaults）
  ├── FIX-3.2：子图字段保护改为显式黑名单（SUBGRAPH_PROTECTED_FIELDS）
  └── FIX-4.1：GovernorRulesCache（TRIBE 族 → Governor 参数通道）⭐ 新增

第二阶段（P2，下一迭代）：
  ├── FIX-1.1+1.2：CheckpointStore TTL + 写锁（合并一个 PR）
  ├── FIX-2.2：ReAct 消息历史修剪（_trim_messages）
  ├── FIX-3.1+3.3：子图条件分支 + fallback 路由（合并一个 PR）
  ├── FIX-4.2：Evidence Plane 增加 tribe_params_snapshot
  └── FIX-2.1：ReAct 并行工具调用

第三阶段（P3，规划中）：
  ├── FIX-1.4：双时钟文档对齐
  ├── FIX-2.3：工具注册表动态化（对接 ToolCatalog）
  ├── FIX-2.4：ReAct 流式输出
  └── 中期迁移评估：FIX-5.1 → FIX-5.2 → FIX-5.3（按顺序，逐步迁移）
```

---

## 8. 不修复的内容（有意为之）

| 问题 | 不修复原因 |
|---|---|
| 把 AIRLOCK 交给 ImmuneAgent | AIRLOCK 是数据管道操作（出站净化），ImmuneAgent 是认知决策层；职责不同，不能混淆 |
| 把 Evidence Plane 交给 TRIBE 族 | Evidence Plane 需要 SQLite 事务原子性；TRIBE 族是异步事件驱动，无法保证原子性 |
| 用 CortexAgent 替代 Governor 裁决 | Governor 需要同步阻断；CortexAgent 是异步认知循环，时序语义不同 |
| 立即迁移到完整 LangGraph | 三个自研机制的迁移需要逐步验证，不能一次性替换；§5 给出了安全的迁移路径 |

---

## 9. 架构演进目标状态（中期）

```
用户请求
    │
    ▼
AIRLOCK（入站净化）── 保留，不变
    │
    ▼
LangGraph 图执行（原生 StateGraph + SqliteSaver）── 替换自研 CheckpointManager
    │
    ├── governor_node（Host 写入 committed_route_token）── 保留，不变
    │       │
    │       │ 读取 GovernorRulesCache（TRIBE 族动态参数）── 新增
    │
    ├── GovernedToolNode（LangGraph ToolNode + Governor 门控）── 替换自研 ReAct
    │
    └── LangGraph 原生 Subgraph（+ SUBGRAPH_PROTECTED_FIELDS 适配器）── 替换自研 Subgraph
    │
    ▼
AIRLOCK（出站净化）── 保留，不变
    │
    ▼
Evidence Plane（原子性审计，含 tribe_params_snapshot）── 保留 + 增强
```

---

*文档版本：v2.0 | 基于类人体架构分层洞察重写 | 最后更新：2026-04-14*
