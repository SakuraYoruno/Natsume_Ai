受众：PROJECT
标题：P6-2：实现最小局部循环闭环

# P6-2：实现最小局部循环闭环

**日期**：2026-03  
**状态**：实施说明；P6 范围与 P6-1 契约已冻结，本文档为 P6-2 实现与验收依据  
**定位**：在 intent_agent_entry 内将当前线性子图执行升级为带硬边界的局部循环，不改主图、出口契约、executor、审计口径。

---

## 0. 依据文档

- **MULTI_AGENT_P6_SCOPE_2026-03.md**：P6 唯一目标、§7 冻结点（边界字段、失败码、审计落点）。  
- **MULTI_AGENT_P6_1_LOCAL_LOOP_CONTRACT_2026-03.md**：边界默认值、失败码、subgraph_trace step 级、入口/出口不变。

---

## 1. P6-2 唯一目标

在 **intent_agent_entry** 内，把当前线性子图执行升级成**带硬边界的局部循环实现**，同时保持：

- 主图不变（仍为 4 节点）
- 出口契约不变（subgraph_commit_summary / subgraph_failure）
- executor 不变（不下沉到 executor，不改主图）
- subgraph_trace 审计口径不变（统一落点 + step 级）

---

## 2. P6-2 最小实现面（只做四件事）

1. **读取三项循环边界配置**  
   `max_local_steps` / `max_retry_count` / `local_loop_timeout_ms`（从 context 或 node_binding 注入，缺省用 P6-1 建议默认值）。

2. **在 intent_agent_entry_handler 内实现局部循环驱动**  
   不下沉到 executor，不改主图；子图内按步执行节点，每步前检查步数上限与总超时。

3. **在超步数 / 超时 / 内部异常时，统一写 subgraph_failure.error_code = LOCAL_LOOP_***  
   使用 P6-1 §2 三码：LOCAL_LOOP_MAX_STEPS、LOCAL_LOOP_TIMEOUT、LOCAL_LOOP_INTERNAL_ERROR。

4. **在 subgraph_trace 中写 step 级信息**  
   至少包含：step 序号、节点、时间戳、状态（与 P6-1 §3 一致）。

---

## 3. P6-2 验收最小集合（5 条）

1. **外层 Intent 图仍是 4 节点**  
   intent_agent_entry → safety_verifier → judge_agent → renderer；主图 active_nodes / edges 不变。

2. **子图成功时仍输出 subgraph_commit_summary**  
   形态与 P4 一致，下游消费不变。

3. **超过 max_local_steps 时输出 LOCAL_LOOP_MAX_STEPS**  
   subgraph_failure.error_code = LOCAL_LOOP_MAX_STEPS；trace_ref 可定位 subgraph_trace。

4. **超过 local_loop_timeout_ms 时输出 LOCAL_LOOP_TIMEOUT**  
   subgraph_failure.error_code = LOCAL_LOOP_TIMEOUT。

5. **subgraph_trace 有 step 级记录，且不污染主图 active_nodes / edges**  
   每条 step 至少含 step_index、node_id、ts_ms、status；主图不暴露子图内部节点。

---

## 4. 结论

- P6 范围与 P6-1 契约已冻结；P6-2 按本文档实现与验收，不再回头改范围文档。  
- 实现完成后可补充集成用例（如 max_steps=2 触发 LOCAL_LOOP_MAX_STEPS、短超时触发 LOCAL_LOOP_TIMEOUT），并更新 P6 阶段状态。
