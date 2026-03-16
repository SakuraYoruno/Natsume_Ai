# 第二轮正式基线规划：P5 Supervisor 决策升级

**日期**：2026-03  
**状态**：规划稿；用于承接 P1～P4 已验收封板后的下一轮正式基线  
**定位**：在不破坏第一轮收口成果的前提下，开启下一轮可控演进  
**总原则**：不同时推进两种复杂性；先提升决策质量，再增加执行复杂度。

---

## 0. 基线前提（已完成并冻结）

以下内容视为**上一轮正式基线**，不再在本轮中回改：

- **P1**：execution_profile + 三图选图完成
- **P2**：开关收敛完成
- **P3**：Supervised Graph 主路径完成
- **P3.5**：agent_dispatch 边界收口完成
- **P4**：Intent 子图透明封装完成并正式验收通过

当前已冻结架构事实：

1. **先选图再跑图**
2. **Chat / Supervised / Intent 三图分治**
3. **executor 为通用执行器，不承载业务分派语义**
4. **Supervised Graph 内以 `agent_dispatch_handler` 为唯一显式 Agent 分派边界**
5. **Intent 图内部复杂链已折叠为透明子图**
6. **契约、失败收口、trace 已固化**

---

## 1. 新一轮正式基线的定义

**不要把 P5 与 P6 合并为同一轮正式基线。**

### 结论

本轮正式基线定义为：

**P5：Supervisor 决策升级基线**

即：  
在不改变三图结构、不增加新 Agent、不引入 ReAct 的前提下，将 Supervised Graph 的 Supervisor 从纯 rule 模式升级为 rule/llm 双模，并完成灰度、回退、验收。

### 为什么不把 P6 一起纳入本轮

P5 和 P6 引入的是两种不同的复杂性：

- **P5** 引入的是：**决策复杂性** — Supervisor 从规则决策升级为 LLM proposal 决策
- **P6** 引入的是：**执行复杂性** — Intent 子图内局部循环 / 局部 ReAct / 多步工具观察

若两者同时推进，会造成故障归因困难、性能/质量波动来源不清、刚收好的边界再次模糊。

因此：

- **本轮正式基线：P5**
- **下一轮候选基线：P6**（单独规划）

---

## 2. 本轮唯一目标（窄定义）

**只升级 Supervisor 的决策机制，不改三图结构，不扩执行能力面。**

### 2.1 外层结构保持不变

Supervised Graph 外层仍为：

`START → supervisor_dispatch → agent_dispatch → judge_agent → renderer → END`

### 2.2 仅替换的部分

将 `supervisor_dispatch_handler` 的内部决策机制，从「规则分派（rule）」扩展为：

- 规则分派（rule）
- LLM proposal 分派（llm）

即：

- `SUPERVISOR_MODE=rule`：沿用当前行为
- `SUPERVISOR_MODE=llm`：调用 LLM 产出 proposal，再由 handler 校验并编译为 `active_agent_id + need_judge + plan`

### 2.3 本轮不做

P5 **不**做：

- 不新增新图
- 不改 execution_profile
- 不改 Chat Graph / Intent Graph 外层结构
- 不新增新 Agent 类型
- 不做全局 ReAct
- 不做 Intent 子图局部循环
- 不做多 Agent handoff
- 不做并行 agent
- 不让 Supervisor 直接控图或直接写 committed_route_token

---

## 3. P5 验收标准（实施前写死）

以下 8 条为 P5 验收必要条件，缺一即不通过。

### 3.1 外层图结构不变

Supervised Graph 主图节点与边保持不变：  
`supervisor_dispatch → agent_dispatch → judge_agent → renderer`  
不得新增旁路主链或把 code/translate 拉回旧主图。

### 3.2 `SUPERVISOR_MODE=rule` 完全保持现有行为

在 `rule` 模式下，输出、选派、延迟、失败路径与当前基线保持一致或等价，不得出现行为回归。

### 3.3 `SUPERVISOR_MODE=llm` 仅影响 proposal 生成，不直接控图

LLM 输出只能是 proposal，不能直接写：  
`__next_node__`、`committed_route_token`、`committed_*`、任何运行时控制字段。

### 3.4 Supervisor proposal 必须经 handler 校验

handler 必须对 proposal 至少做：  
`selected_agent` 是否在当前 graph allowlist 内；`need_judge` / `risk_mode` 是否为允许值；proposal JSON/schema 是否合法。  
不合法时必须安全回退为 rule 行为或默认安全选派。

### 3.5 `active_agent_id` 仍只在 Supervised Graph 内生效

LLM proposal 不得把执行意图场景、聊天场景重新拉入 Supervised Graph；作用域不扩大，不跨图泄漏。

### 3.6 失败只在 Supervised Graph 内收口

LLM proposal 失败、超时、解析失败、校验失败，只能在 Supervised Graph 内回退；不允许切到 Chat/Intent 图、改 execution_profile 重跑。

### 3.7 可观测性完整

必须可观测：当前 `SUPERVISOR_MODE`；rule/llm 使用次数；llm proposal 成功率与校验失败率；rule fallback 次数；选派到各 agent 的分布；延迟与错误统计。

### 3.8 可灰度、可回退

必须支持 `SUPERVISOR_MODE=rule` 与 `SUPERVISOR_MODE=llm`；任何异常时可通过配置回退到 `rule`，不需要改代码、不需要改图结构。

---

## 4. P5 实现约束（防止边界再长回来）

### 4.1 Supervisor 仍是 handler，不变成 model node

形态为 **handler + proposal generation**，而非纯 LLM model node。

### 4.2 LLM 只有提案权，没有路由权

LLM 只能输出：`selected_agent`、`reason`、`need_judge`、`risk_mode`、`budget`（若保留）。  
由 handler 校验并写入：`active_agent_id`、`agent_runtime.plan`、`__next_node__`（如当前设计仍由 handler 控制）。

### 4.3 不引入全局动态路由

业务分派仍只存在于：`supervisor_dispatch_handler`、`agent_dispatch_handler`、graph-local allowlist。

### 4.4 不扩 agent 能力面

P5 不新增 retrieval_agent、planner_agent、review_agent、multi-judge 等；先只在现有 code_agent / translate_agent 基础上验证 Supervisor 决策升级。

### 4.5 不改变 Judge / Renderer 语义

不改 judge_agent_handler、renderer 的职责与 need_judge 的消费方式；只允许改变 Supervisor 决策来源。

---

## 5. 建议的 proposal 契约

建议在 `graph_contract.py` 或等价 contract 模块中固定 Supervisor proposal 结构。

```json
{
  "selected_agent": "code_agent",
  "reason": "用户请求代码帮助",
  "need_judge": true,
  "risk_mode": "guarded"
}
```

### 5.1 字段约束

- **selected_agent**：必填，且必须在当前 graph allowlist 内
- **reason**：可选，用于审计和观测
- **need_judge**：必填或有默认值
- **risk_mode**：必填或有默认值，仅允许既定枚举值

### 5.2 禁止字段

proposal 中禁止出现：  
`__next_node__`、`committed_route_token`、`committed_*`、`branch_specs`、`execution_profile`、`active_agent_id`、任何 graph 结构控制键。

---

## 6. 建议的失败与回退语义

### 6.1 LLM proposal 失败类型（口径固化）

| 情况 | 失败码 |
|------|--------|
| JSON 解析失败 / 非 JSON | `LLM_BAD_JSON` |
| JSON 可解析但缺 selected_agent / 禁止键 / risk_mode 非法 | `LLM_SCHEMA_INVALID` |
| selected_agent 不在当前图 allowlist | `LLM_AGENT_NOT_ALLOWED` |
| 超时 | `LLM_TIMEOUT` |
| 无 gateway / 其它异常 | `LLM_INTERNAL_ERROR` |

### 6.1.1 risk_mode 处理口径（缺失 vs 非法）

- **缺失** need_judge / risk_mode：允许，handler 用当前 scene 的 rule 默认值补齐，不判失败。
- **显式给出但非法** risk_mode（不在 safe/guarded/strict）：判 `LLM_SCHEMA_INVALID`，统一 fallback 到 rule。  
不把「缺失」与「非法」混为同一种行为。

### 6.2 统一回退策略

任一失败发生时：记录 proposal_failure；回退到 rule 决策逻辑；保持 execution_profile 不变；不跨图逃逸。

### 6.3 审计字段建议

建议写入统一观测字段，例如：  
`supervisor_mode`、`supervisor_decision_source`（rule / llm / llm_fallback_rule）、`supervisor_failure_code`、`supervisor_selected_agent`。

---

## 7. 文件级改造建议（P5）

### 7.1 主要修改文件

| 文件 | 改造方向 |
|------|----------|
| `host_brain/app/handlers/supervisor_dispatch_handler.py` | 增加 llm proposal 模式；schema 校验、allowlist 校验、fallback 到 rule |
| `host_brain/integrations/graph_contract.py` | 固定 Supervisor proposal schema / 常量 / 校验辅助 |
| `host_brain/observability/metrics.py` | 增加 supervisor_mode / decision_source / proposal_failure_code / selected_agent 维度 |
| `tests/unit/test_supervisor_dispatch_handler.py`（新增或扩展） | 覆盖 rule / llm / llm 失败 fallback / allowlist 拒绝 |
| `tests/integration/test_p5_supervisor_llm_mode.py`（新增） | 真实 runtime 下验证 SUPERVISOR_MODE=llm 的完整链路与失败回退 |

### 7.2 明确不应修改的文件

P5 中原则上不应主动改动：  
`graph_executor.py`、`agent_dispatch_handler.py`、`intent_agent_entry_handler.py`、`execution_profile.py`、Chat/Intent 图结构定义。  
除非是修复与 P5 直接冲突的 bug，而不是顺手重构。

---

## 8. 建议实施顺序

1. **P5-1：契约先行** — 固定 proposal schema、失败码、allowlist 校验规则  
2. **P5-2：handler 内实现 llm proposal** — SUPERVISOR_MODE=llm 时调用 LLM，解析与校验 proposal，编译到运行时字段  
3. **P5-3：回退路径打通** — 超时/非法 JSON/非 allowlist agent/schema 失败全部回退为 rule  
4. **P5-4：可观测性补齐** — metrics / logs / trace，决策来源与失败码上报  
5. **P5-5：集成 smoke 与灰度** — 先对 WORK_CODE / WORK_TRANSLATE 做 smoke，AUTO 最后验证；默认基线仍保留 rule  

---

## 9. 与下一轮候选基线（P6）的关系

**P6 建议定义为**：Intent 子图局部循环基线 — 仅在 intent_agent_subgraph 内探索局部循环/局部 ReAct 占位或最小闭环；不做全局 ReAct、不改三图结构、不扩大 executor 职责。

**P6 不并入 P5 的原因**：P5 解决「谁来决策选哪个 agent」，P6 解决「Intent 内部如何多步执行」；两者故障面不同，必须拆轮次推进。

---

## 10. 阶段状态

| 阶段   | 状态     |
|--------|----------|
| P1     | 完成     |
| P2     | 完成     |
| P3     | 完成     |
| P3.5   | 完成并验收通过 |
| P4     | 完成并正式验收通过 |
| **P5** | **完成并正式验收通过**（2026-03 封板；验收结论见 MULTI_AGENT_P5_SEAL_RESULT_2026-03.md） |
| P6     | 待启动（下一轮候选基线，单独规划） |

**第二轮正式基线 = P5，已封板。下一轮候选基线 = P6。**

---

## 11. P5 冻结非目标（封板后不得纳入 P5）

以下**不属于 P5**，不得作为 P5 补丁或扩展纳入：

- Intent 子图局部循环 / 局部 ReAct
- 全局 ReAct、全局 dynamic executor routing
- 新 Agent 类型（如 retrieval_agent、planner_agent）
- 并行 agent、多 Agent handoff 扩展
- executor 业务扩权、三图结构变更

上述归属 P6 或后续阶段，须单独规划。

---

## 12. 核心结论

- 当前 P1～P4 已是稳定正式基线。  
- **第二轮正式基线 = P5：Supervisor 决策升级基线，已封板。**  
- **P6 不并入 P5，作为下一轮候选基线单独规划。**
