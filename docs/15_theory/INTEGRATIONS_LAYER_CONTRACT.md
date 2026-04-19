受众：MASTER
标题：host_brain/integrations/ 层接口契约（当前代码实况）

版本：v2.0
日期：2026-04-19
归属：docs/15_theory/

---

定位：本文描述当前代码里的 `host_brain/integrations/` 层。
需要特别注意：它现在是“自研 GraphRuntime + LangGraph 适配 + LLM Gateway + Evidence Plane”的混合层，而不只是早期文档中那种抽象的“LangGraph 治理层”。

## 0. 代码证据入口

- `host_brain/integrations/graph_contract.py`
- `host_brain/integrations/graph_runtime.py`
- `host_brain/integrations/graph_executor.py`
- `host_brain/integrations/graph_fallback.py`
- `host_brain/integrations/graph_branch.py`
- `host_brain/integrations/graph_clock.py`
- `host_brain/integrations/graph_streaming.py`
- `host_brain/integrations/graph_checkpoint.py`
- `host_brain/integrations/graph_langgraph_checkpoint.py`
- `host_brain/integrations/graph_subgraph.py`
- `host_brain/integrations/graph_langgraph_subgraph.py`
- `host_brain/integrations/graph_governed_tool_node.py`
- `host_brain/integrations/evidence_bench.py`
- `host_brain/integrations/evidence_write_queue.py`
- `host_brain/integrations/evidence_circuit_breaker.py`
- `host_brain/integrations/llm_gateway.py`
- `host_brain/integrations/llm_gateway_schemas.py`
- `host_brain/integrations/llm_helpers.py`

---

## 一、当前层定位

当前 `integrations/` 层主要承担四类职责：

1. 图运行时契约与执行
2. LangGraph checkpoint / subgraph 适配
3. LLM Gateway 与模型调用治理
4. Evidence Plane 与异步写入缓冲

因此它现在更准确的描述应是：

```text
Governed Runtime Integration Layer
  = GraphRuntimeContract
  + LangGraph adapters
  + LLM Gateway
  + Evidence Plane
```

而不是单一的“LangGraph 治理层”。

---

## 二、当前主模块一览

| 文件 | 当前职责 |
|---|---|
| `graph_contract.py` | GraphState、常量、配置校验、P4/P5/P6/P7 契约、受保护字段 |
| `graph_runtime.py` | `GraphRuntimeContract`：入口编排、状态初始化、恢复执行、结果收口 |
| `graph_executor.py` | LangGraph `StateGraph` 的 step-by-step 执行器 |
| `graph_fallback.py` | timeout / dependency-down / execution-error 统一降级 |
| `graph_branch.py` | 只读并行分支与 barrier_join |
| `graph_clock.py` | Dual-Clock 超时监督 |
| `graph_streaming.py` | Presence / Commit / Render 三阶段流 |
| `graph_checkpoint.py` | checkpoint / interrupt / resume / policy guard |
| `graph_langgraph_checkpoint.py` | checkpoint 公共 facade，转发到 `graph_checkpoint.py` |
| `graph_subgraph.py` | 透明子图公共入口，转发到 `graph_langgraph_subgraph.py` |
| `graph_langgraph_subgraph.py` | LangGraph 原生子图构造与字段保护 |
| `graph_governed_tool_node.py` | Governor 门控后的工具执行节点 |
| `evidence_bench.py` | 写入延迟基准测量 |
| `evidence_write_queue.py` | NON_CRITICAL 异步写入队列 |
| `evidence_circuit_breaker.py` | 写入熔断保护 |
| `llm_gateway.py` | 模型调用网关、探测、路由、QA/Airlock |
| `llm_gateway_schemas.py` | `GatewayResult` / `LLMHealthProbeResult` / `LLMRouteResult` |

---

## 三、graph_contract.py 当前契约

### 3.1 GraphState

`GraphState` 仍是当前图执行共享状态。

关键字段分组仍包括：

- 基础执行字段：`request_id / graph_id / timeout_ms / payload / trace / status / reason_code / detail`
- 路由字段：`pending_node_id / edge_map / fallback_map / committed_route_token / committed_route_token_detail`
- 模型字段：`model_nodes / model_profile_id / model_calls / dependency_statuses`
- 情感字段：`emotion_context / emotion_tag / mood_state / emotion_range_hint`
- Clock 字段：`policy_start_ms / policy_timeout_ms / clock_events / node_exec_stats`
- 并行分支字段：`branch_specs / branch_manifest`
- Streaming 字段：`presence_events / commit_snapshot / render_snapshot_ref / streaming_phase`
- Checkpoint 字段：`gov_checkpoint_id / interrupt_reason / resume_allowed / resume_deadline_at`
- Multi-Agent 字段：`active_agent_id / handoff_history / agent_budget / agent_artifacts / judge_result`
- 入口控制字段：`execution_profile`

### 3.2 当前核心函数

当前最关键的契约函数有：

- `validate_graph_config()`
- `build_edge_map()`
- `pick_model_profile_id()`
- `build_model_param_hash()`
- `extract_governance_refs()`
- `extract_agent_runtime()`
- `build_subgraph_commit_summary()`
- `validate_supervisor_proposal_for_graph()`

### 3.3 P4/P5/P6/P7 契约仍在代码中

`graph_contract.py` 当前仍是以下阶段性契约的真实落点：

- P4：`subgraph_commit_summary` / `subgraph_failure`
- P5：Supervisor proposal allowlist/forbidden key/risk_mode 校验
- P6：局部循环配置键与失败码
- P7：预算突破失败码集合

### 3.4 SUBGRAPH_PROTECTED_FIELDS

当前子图显式受保护字段包括：

- `committed_route_token`
- `committed_route_token_detail`
- `gov_checkpoint_id`
- `interrupt_reason`
- `resume_allowed`
- `resume_deadline_at`
- `execution_profile`
- `tribe_governor_params`
- `immune_threat_level`
- `endocrine_state`

这比旧文档“只看 committed_* 前缀”更严格，也更贴近当前实现。

---

## 四、GraphRuntimeContract 当前执行路径

### 4.1 入口顺序

`graph_runtime.py` 当前 `run_graph()` 的主流程是：

```text
validate_graph_config
  -> resolve_model_nodes
  -> GraphExecutor.compile_runtime_graph()
  -> 读取 payload 中 graph/emotion runtime section
  -> 构造 initial_state
  -> LangGraph compiled.invoke(...)
  -> _finalize(...)
```

### 4.2 当前状态注入

`run_graph()` 当前会从：

- `host_brain.app.chat_runtime_input.get_chat_runtime_graph()`
- `host_brain.app.chat_runtime_input.get_chat_runtime_emotion_state()`

读取图运行时上下文，并把这些内容注入 `initial_state`。

这意味着：

- `integrations/` 当前并不是完全不依赖 `app/`
- `graph_runtime.py` 明确会读取 app 层整理后的运行时输入

### 4.3 GraphRunResult 当前输出

`_finalize()` 最终会输出：

- `request_id`
- `graph_id`
- `graph_version`
- `graph_status`
- `reason_code`
- `stage`
- `replay_ref`
- `runtime_backend = "langgraph+langchain"`
- `model_profile_id`
- `model_control`
- `graph_trace`
- `payload`
- `ts_ms`
- `subgraph_trace`（有时）

因此当前图运行时已经把 replay / model control / trace 全部统一收口。

---

## 五、GraphExecutor 当前行为

### 5.1 运行时后端

`GraphExecutor.compile_runtime_graph()` 当前直接构建：

- LangGraph `StateGraph(GraphState)`
- `step` 节点
- `finish` 节点

然后通过 `compile_graph_with_checkpoint()` 编译。

### 5.2 当前执行特征

`graph_executor.py` 当前不是简单节点串行器，而是同时负责：

- handler 节点执行
- model node 调用
- dependency 检查
- timeout / fallback 路由
- Phase-Locked Streaming
- barrier_join
- 动态 agent handler 路由

### 5.3 Trusted handler 与 Multi-Agent

当前保留：

- `_TRUSTED_HANDLER_ALLOWLIST`
- `_AGENT_HANDLER_MAP`

并且：

- `active_agent_id` 已提升为 GraphState 顶层字段
- model node 输出中的控制字段会被统一剥离

这说明当前图执行器仍在承担多 Agent 动态控图的核心工作。

---

## 六、Fallback / Branch / Streaming / Clock

### 6.1 graph_fallback.py

当前统一处理：

- dependency unavailable
- timeout
- execution error
- hard failure

主要出口函数：

- `handle_dependency_unavailable()`
- `handle_timeout()`
- `handle_execution_error()`
- `fail_hard()`
- `pick_fallback_node()`
- `pick_next_node()`

### 6.2 graph_branch.py

当前只读并行分支仍是真实实现，不是纸面设计。

核心点：

- `BranchSpec`
- `BranchResult`
- `BranchManifest`
- `run_parallel_branches()`
- `barrier_join()`

分支执行规则当前包括：

- 分支节点不能写 `committed_*`
- 分支结果只能写入 `branch_manifest`
- 分支失败静默降级，不直接打断主图

### 6.3 graph_streaming.py

当前仍实现三阶段流：

- Presence
- Commit
- Render

核心类：

- `PresenceStreamEmitter`
- `CommitStreamEmitter`
- `RenderStreamEmitter`
- `PhaseLockGuard`
- `RenderGuard`

关键规则：

- Presence 只给 UI，不回流下游
- Commit 只有 `committed_route_token = GOV_OK_RENDER` 时才产生
- Render 必须建立在 commit snapshot 之上

### 6.4 graph_clock.py

当前 Dual-Clock 仍由专门模块负责：

- 节点级 exec timeout
- 图级 policy timeout

并把结果写入：

- `clock_events`
- `node_exec_stats`

---

## 七、Checkpoint / Resume 当前契约

### 7.1 当前公共接口

`graph_checkpoint.py` 当前主要提供：

- `get_checkpoint_db_path()`
- `get_langgraph_checkpointer()`
- `compile_graph_with_checkpoint()`
- `build_interrupt_signal()`
- `check_resume_allowed()`
- `HostPolicyGuard`

### 7.2 当前真实行为

需要注意一个代码事实：

- `get_langgraph_checkpointer()` 优先使用 LangGraph `SqliteSaver`
- 若 sqlite backend 包不可用，会回退到 `MemorySaver`

因此当前运行时是：

- 优先持久化 checkpoint
- 在缺依赖环境下允许内存降级

这比旧文档里“只有单一路径、无任何 fallback”的描述更符合实际代码。

### 7.3 graph_langgraph_checkpoint.py

该文件当前只是 facade：

- `compile_graph_with_checkpoint`
- `get_checkpoint_db_path`
- `get_langgraph_checkpointer`

实际逻辑仍在 `graph_checkpoint.py`。

---

## 八、Subgraph 当前契约

### 8.1 graph_subgraph.py

`graph_subgraph.py` 当前不再自己实现子图编译，而是把公共接口转发到：

- `graph_langgraph_subgraph.build_governed_subgraph()`

并保留：

- `compile_governed_subgraph()`
- `compile_intent_agent_subgraph()`
- `compile_code_refine_subgraph()`

### 8.2 graph_langgraph_subgraph.py

当前它直接使用 LangGraph 原生 `StateGraph` 构建子图。

子图关键约束：

- 使用独立 `SubgraphState`
- 子图结果只把安全 payload 合并回主图
- 受保护字段由 `SUBGRAPH_PROTECTED_FIELDS` 黑名单剥离
- 自动生成 `subgraph_commit_summary`

---

## 九、GovernedToolNode 当前契约

`graph_governed_tool_node.py` 当前实现的是“Governor 门控下的直接工具执行节点”。

执行顺序：

```text
Governor evaluate_tool_call
  -> 失败则只返回 route token，不执行工具
  -> 成功则直接按 tool_name 执行工具
  -> 写 Evidence Plane 审计
```

这说明当前工具调用链并不依赖 LangGraph 自带 ToolNode，而是走自定义节点实现。

---

## 十、Evidence Plane 当前契约

### 10.1 evidence_bench.py

`EvidenceBench` 当前负责测量：

- `p50`
- `p95`
- `p99`
- `mean`
- `max`

### 10.2 evidence_write_queue.py

`EvidenceWriteQueue` 当前行为：

- 队列满时自动 `flush()`
- `flush()` 超时可提前返回剩余数量
- 失败任务最多重入队一次
- 可 `clear()`

### 10.3 evidence_circuit_breaker.py

当前仍负责连续失败熔断，保护 SQLite/写入平面。

---

## 十一、LLM Gateway 当前契约

### 11.1 当前核心结构

`llm_gateway.py` 当前主类是：

- `LlmGatewayContract`

返回结构由 `llm_gateway_schemas.py` 定义：

- `GatewayResult`
- `LLMHealthProbeResult`
- `LLMRouteResult`

### 11.2 当前能力

`LlmGatewayContract` 当前不仅是“单次模型调用网关”，还负责：

- `health_probe()`
- `route_call()`
- ingress quarantine
- egress sanitize/gate
- QA contract enforcement
- provider timeout 包装
- fallback provider 路由

当前 provider 常量包括：

- `openai`
- `codex`
- `gemini`
- `deepseek`
- `mock`
- `disabled`

### 11.3 当前路由现实

`route_call()` 的口径是：

- 主 provider
- fallback providers
- 全失败时稳定降级结果

因此它已经是当前模型调用治理的一部分，而不只是薄 wrapper。

---

## 十二、当前依赖关系应如何理解

当前更准确的理解是：

```text
integrations/
  = 运行时集成层
  = GraphRuntime + LangGraph adapters + LLM Gateway + Evidence Plane
```

而不是：

```text
integrations/
  = 完全独立、只面向 LangGraph、严格不碰 app 层的纯治理层
```

当前代码中至少有这些现实依赖：

- `graph_runtime.py -> app.chat_runtime_input`
- `llm_gateway.py -> airlock / audit / core.qa`
- `graph_runtime.py -> audit.sqlite_ledger`

所以它是“治理/图执行/模型调用的集成层”，而不是完全隔离的纯中间层。

---

## 十三、当前不再适用的旧口径

以下说法不再准确：

- `integrations/` 只是抽象 LangGraph 治理层
- 它严格不依赖 `app/`
- checkpoint 后端只有单一路径、没有降级
- 子图仍由自研 compile 逻辑主导
- 工具执行仍依赖 LangGraph ToolNode

当前正确口径应是：

1. `GraphRuntimeContract + GraphExecutor` 是当前图运行时主体。
2. `graph_checkpoint.py` 与 `graph_langgraph_subgraph.py` 是当前 LangGraph 适配核心。
3. `GovernedToolNode` 是当前工具治理节点实现。
4. `LlmGatewayContract` 与 Evidence Plane 都属于 `integrations/` 的现行职责范围。
