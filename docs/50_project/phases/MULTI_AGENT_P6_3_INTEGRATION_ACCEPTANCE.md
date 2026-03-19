受众：PROJECT
标题：P6-3：集成验收补齐

# P6-3：集成验收补齐

**日期**：2026-03  
**状态**：验收说明；P6-2 实现已完成并通过单测，P6 待做最终集成证据后再封板  
**定位**：在真实 runtime + Intent 图下证明 P6 特有失败路径与成功路径、结构不变，补齐后即可进入封板判断。

---

## 0. 当前阶段（不建议直接封板）

- **P6_SCOPE**：完成（边界冻结）  
- **P6-1**：完成（契约冻结）  
- **P6-2**：完成并通过单测（LOCAL_LOOP_MAX_STEPS、step 级 subgraph_trace、P4 集成未破坏）  
- **P6**：待做**最终集成验收**，不建议直接封板  

缺的一层：**P6 特有失败路径在真实 runtime + Intent 图里的集成证明**。

---

## 1. P6-3 唯一目标

只补 **4 条**集成验收用例，做完即可进入封板判断；若 4 条均通过，建议直接进入 MULTI_AGENT_P6_SEAL_RESULT_2026-03.md 并更新 P6_SCOPE / REFACTOR。

---

## 2. 四条集成验收

### 2.1 超时集成用例

- **手段**：真实 runtime + Intent 图配置，将 **local_loop_timeout_ms** 压到极小（如 10～50ms），使子图内首步或第二步前总耗时超限。  
- **断言**：  
  - `subgraph_failure.error_code == LOCAL_LOOP_TIMEOUT`  
  - `execution_profile` 仍为 `INTENT_EXEC`  
  - 主图仍只走 `intent_agent_entry → safety_verifier → judge_agent → renderer`  
  - 不跨图逃逸（主图 trace 仅主图节点）

### 2.2 重试耗尽集成用例

- **手段**：patch 某个子图内部节点（如 core_reasoner_primary）持续失败，超过 **max_retry_count** 后收口。  
- **断言**：  
  - `subgraph_failure.error_code == LOCAL_LOOP_INTERNAL_ERROR`  
  - `failed_node` 为实际子图节点（如 core_reasoner_primary），而非主图节点  
  - `subgraph_trace` 有多次失败 step 记录（重试产生的 step）  
  - 仍在 Intent 图内收口，无跨图逃逸  

### 2.3 成功路径集成用例

- **手段**：在启用局部循环实现的前提下跑一次正常 **INTENT_EXEC**（不 patch 失败）。  
- **断言**：  
  - 成功输出 **subgraph_commit_summary**  
  - 不输出 LOCAL_LOOP_* 失败码（无 subgraph_failure 或 error_code 非 LOCAL_LOOP_*）  
  - **subgraph_trace** 含 step 级记录（step_index / node_id / ts_ms / status）  
  - 下游 safety_verifier / judge_agent 消费口径不变（可读 subgraph_commit_summary，无异常形态）  

### 2.4 结构不变断言

- **手段**：在 P6 集成包内做一条结构性集成/配置断言（可与上述用例同用 build_intent_graph 或 runtime 产出配置）。  
- **断言**：  
  - 主图 **active_nodes** 仍是 4 节点（intent_agent_entry, safety_verifier, judge_agent, renderer）  
  - 主图 **edges** 不出现 `local_loop` 或子图内部节点（core_reasoner_primary, planner_constraints, tool_planner）  
  - **executor** 未新增 P6 业务特判（无对 LOCAL_LOOP / local_loop 的分支逻辑；仅 handler 内实现）  

---

## 3. 通过后动作

若上述 4 条均通过：

1. 撰写 **MULTI_AGENT_P6_SEAL_RESULT_2026-03.md**（P6 验收结论）。  
2. 更新 **P6_SCOPE** 阶段状态为「完成并正式验收通过」。  
3. 更新 **REFACTOR** 阶段表为「P6 完成并正式验收通过」。  

---

## 4. 结论

- P6 当前不缺实现，缺的是**最后一轮集成证据**。  
- 下一步最合理的是做 **P6-3 集成用例**，不建议现在直接封板；补齐这轮后，P6 即可进入正式验收与封板。
