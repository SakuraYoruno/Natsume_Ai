受众：MASTER
标题：LangGraph 完整编排能力 vs 核心约束冲突分析报告

# LangGraph 完整编排能力 vs 核心约束冲突分析报告

- **文档编号**：LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS
- **生成日期**：2026-03-06
- **规格依据**：Host 治理约束、`ReplyEnvelope` 收口契约与当前集成层代码
- **分析对象**：`host_brain/integrations/graph_executor.py` / `graph_runtime.py`
- **结论摘要**：LangGraph 的 5 项高级编排能力与 Host 治理约束存在结构性冲突，当前架构为有意降级使用，属于治理优先于框架能力的主动取舍。

---

## 一、LangGraph 完整编排能力清单

LangGraph 原生支持的完整图编排能力：

```
真正的多节点图结构
├── 条件边（conditional_edges）—— 依赖节点输出动态路由
├── 并行分支（fan-out / fan-in）—— 多节点同时执行
├── 子图（SubGraph）—— 嵌套编排，父图调用子图
├── 流式输出（stream_mode）—— 逐 token 流式推送
└── 断点续跑（Checkpointer）—— interrupt_before/after + 人工介入
```

---

## 二、当前实际使用的图结构

`compile_runtime_graph()` 编译出的实际图：

```python
graph.add_node("step",   RunnableLambda(self._run_single_step))
graph.add_node("finish", RunnableLambda(lambda state: state))
graph.add_edge(START, "step")
graph.add_conditional_edges("step", self._route_after_step,
                            {"step": "step", "finish": "finish"})
graph.add_edge("finish", END)
```

**实际拓扑**：

```
START → step ⟲ (while loop) → finish → END
```

LangGraph 在此仅作为 **状态传播容器** 和 **可观测上下文**，所有编排决策权收回 Host 侧 `edge_map` 字典。

---

## 三、逐条冲突分析

### 冲突 1：条件边（LLM 驱动路由）vs §0.1 主权约束

| 维度 | LangGraph 原生 | 当前实现 |
|------|---------------|---------|
| **路由决策者** | LLM 节点输出 → `conditional_edges` 动态决定下一节点 | `edge_map` 字典（Python dict，Host 侧静态定义） |
| **冲突条款** | §0.1："LLM 节点永远不裁决、不执行" | — |
| **风险** | 若 `safety_verifier` 输出 `verdict=FAIL` 后由 LangGraph 条件边决定路由，则路由决策权在 LLM 输出，违反主权约束 | — |

**根本矛盾**：LangGraph 的条件边天然设计为"让节点输出决定下一步"，而 §0.1 要求"LLM 不得裁决"。

**当前替代方案**：`edge_map` 字典由 Host 侧静态定义，LLM 输出只影响 payload，不影响图结构。

---

### 冲突 2：并行分支（fan-out）vs §0.2 AIRLOCK 串行审计

**LangGraph 并行写法**：

```python
graph.add_edge("node_a", "node_b")
graph.add_edge("node_a", "node_c")  # b 和 c 并行执行
```

**冲突条款**：§0.2 要求每次出站必须生成 `sanitization_report_ref` 与 `payload_digest`（写入 `airlock_log`），否则**阻断外发**。

**具体风险**：
- 并行出网时无法保证每个分支都有独立的 `sanitization_report_ref`
- 无法保证审计写入在出网前完成（race condition）
- `payload_digest` 需要对每个分支的 `context_pack` 单独计算，并发场景下计算顺序不可控

**当前替代方案**：`while` 循环强制串行，每个节点执行后同步写审计，彻底规避并发审计问题。

---

### 冲突 3：子图（SubGraph）vs §6 审计链完整性 / 可回放性

**LangGraph 子图写法**：

```python
subgraph = StateGraph(SubState)
# ... 子图内部逻辑
parent_graph.add_node("sub", subgraph.compile())
```

**冲突条款**：§6 要求每次调用必须写入：

```
model_profile_id / node_id / provider / model_id
prompt_profile_id（仅存引用/哈希）
input_digest / output_digest
tool_visibility_hash
sanitization_report_ref
```

**具体风险**：
- 子图内部节点对父图是**黑盒**，`graph_trace` 无法捕获子图内部每个节点的调用
- 审计链断裂：子图内部节点不写 `llm_call_log`
- 无法回放：`replay_ref` 只能定位到子图节点，不能定位到子图内部节点

**当前替代方案**：所有节点扁平化在同一个 `_run_single_step` 循环中，`trace` 列表完整记录每个节点执行，`model_calls` 完整记录每次 LLM 调用。

---

### 冲突 4：断点续跑（Checkpointer）vs §4.3 时间驱动降级链路

**LangGraph Checkpointer 写法**：

```python
graph.compile(checkpointer=MemorySaver())
# interrupt_before=["node_x"] 暂停，人工介入后继续
```

**冲突条款**：§4.3 降级链路为时间驱动：

```
t0: Host 立即输出"在场回应模板"
t1: core_reasoner_primary 超时 → core_reasoner_secondary
t2: 仍失败 → host_fallback_policy
```

**具体风险**：
- LangGraph checkpointer 是**状态驱动**（等待人工介入或外部事件），而降级是**时间驱动**（`timeout_ms`）
- checkpointer 暂停时，`t1` 超时计时器已经失效
- 降级写入 `error_log/quality.stage` 需要在 Host 侧精确控制，checkpointer 的恢复时机不可控

**当前替代方案**：`handle_timeout()` / `handle_dependency_unavailable()` / `pick_fallback_node()` 全部在 `_run_single_step` 内部同步处理，降级逻辑完全在 Host 侧可控。

---

### 冲突 5：流式输出（stream_mode）vs Surface Main Chain / ReplyEnvelope 输出收口

**LangGraph 流式写法**：

```python
for chunk in graph.stream(input, stream_mode="values"):
    yield chunk  # 逐 token 推送
```

**冲突约束**：用户可见文本必须先完成 Host 侧表达收口：

```
candidate expression / tool result / fallback
  → Surface Main Chain / QA / postprocess
  → ReplyEnvelope(kind=final_reply)
  → send / persist / cache
```

**具体风险**：
- 原生逐 token 输出可能绕过 Surface Main Chain 与 QA，将未收口文本提前暴露给用户
- 尚未形成 `ReplyEnvelope(kind=final_reply)` 时，系统无法判断该内容是否允许发送、持久化或缓存
- 含有“已执行/已保存/已确认”的片段如果先于审计链完成而被推送，会形成伪执行宣告

**当前替代方案**：图运行可以流式输出进度、trace 或安全状态片段；用户可见最终文本必须等待 Host 侧生成 `ReplyEnvelope(final_reply)` 后再发送。非 final_reply 的 ACK、任务状态、降级失败或内部候选不得进入人格记忆。

---

## 四、冲突矩阵总览

| LangGraph 能力 | 被阻断的约束条款 | 冲突类型 | 当前替代方案 |
|---------------|----------------|---------|------------|
| 条件边（LLM 驱动路由） | §0.1 主权约束 | 决策权归属冲突 | `edge_map` 字典（Host 静态定义） |
| 并行分支（fan-out） | §0.2 AIRLOCK 串行审计 | 并发安全冲突 | `while` 循环强制串行 |
| 子图（SubGraph） | §6 审计链完整性/可回放性 | 可观测性冲突 | 扁平化 `trace` 列表 |
| 断点续跑（Checkpointer） | §4.3 时间驱动降级链路 | 触发机制冲突 | 同步 `handle_timeout()` |
| 流式输出（stream_mode） | Surface Main Chain / ReplyEnvelope 收口 | 输出边界冲突 | 只流式安全状态；最终文本等待 `final_reply` |

---

## 五、架构决策结论

### 5.1 当前架构的本质

LangGraph 在当前系统中被**主动降级使用**：

- **保留**：`StateGraph` 状态传播、`compile()` 可观测上下文、`RunnableLambda` 节点封装
- **放弃**：条件边、并行分支、子图、断点续跑、流式输出

### 5.2 这是技术债吗？

**否**。这是治理优先于框架能力的有意取舍：

> LangGraph 的每一项高级能力都与 Host 治理约束的某条边界存在结构性冲突。
> 在 AI 治理合规要求下，框架能力必须服从约束体系，而非反过来。

### 5.3 未来扩展路径

若未来需要引入 LangGraph 高级能力，需先在 Host 治理契约中为对应约束增加明确协议，并通过完整的安全评审，方可解锁对应能力。

---

## 六、相关文件索引

| 文件 | 说明 |
|------|------|
| `docs/30_devspec/ModelPlane.md` | 核心约束规格文档 |
| `host_brain/integrations/graph_executor.py` | LangGraph 图编译与执行 |
| `host_brain/integrations/graph_runtime.py` | 运行时合约层 |
| `docs/45_testing/test_reports/host_chat/HOST_CHAT_REAL_CHAIN_TRACE_req_bc56917feac740b4adea1486d88a6435.md` | 真实调用链路 Trace |
| `docs/45_testing/test_reports/host_chat/HOST_CHAT_CHAIN_REAL_REPORT_2026-03-06.md` | 节点编排问题分析报告 |
