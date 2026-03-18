受众：PROJECT
标题：P7：治理与稳定性基线 — 范围与验收标准

# P7：治理与稳定性基线 — 范围与验收标准

**日期**：2026-03  
**状态**：完成并正式验收通过（2026-03 封板）；本文档为 P7 范围与验收的单一依据  
**定位**：在不改三图结构、不扩 executor、不新增 Agent 的前提下，补齐系统级治理、稳定性、观测与故障注入能力，使当前架构基线进入“可长期运行”的稳定态  
**总原则**：不再做大结构演进；只做治理面和运行面收口，不把能力扩张伪装成稳定性工作。  

---

## 0. 基线前提（已完成并冻结）

以下视为**当前正式基线**，P7 中不回改：

- **第一轮正式基线**：P1～P4，已封板  
  - execution_profile / 三图分治  
  - Supervised Graph 主路径  
  - Intent 透明子图  
  - subgraph_commit_summary / subgraph_failure 契约固化  

- **第二轮正式基线**：P5，已封板  
  - Supervisor rule / llm 双模  
  - proposal 契约、失败码、灰度与回退  
  - supervisor_decision_log / supervisor_metrics  

- **第三轮正式基线**：P6，已封板  
  - Intent 子图局部循环  
  - local loop 边界、失败码、step 级 subgraph_trace  
  - 局部循环仍只发生在 intent_agent_subgraph 内  

已冻结架构事实：

1. **先选图再跑图**；Chat / Supervised / Intent 三图分治  
2. **executor 为通用执行器**，不承载业务分派或业务循环语义  
3. **Supervised Graph** 仍以 `supervisor_dispatch -> agent_dispatch -> judge_agent -> renderer` 为主链  
4. **Intent 图** 外层仍为 `intent_agent_entry -> safety_verifier -> judge_agent -> renderer`  
5. **subgraph_commit_summary / subgraph_failure / subgraph_trace** 契约已固化  
6. **P1～P6 已全部封板**，后续工作不得以旧阶段补丁名义混入  

---

## 1. 项目阶段状态

| 阶段 | 状态 |
|------|------|
| P1 | 完成 |
| P2 | 完成 |
| P3 | 完成 |
| P3.5 | 完成并验收通过 |
| P4 | 完成并正式验收通过 |
| P5 | 完成并正式验收通过 |
| P6 | 完成并正式验收通过 |
| **P7** | **完成并正式验收通过**（2026-03 封板） |

**第一轮正式基线**：P1～P4，已封板。  
**第二轮正式基线**：P5，已封板。  
**第三轮正式基线**：P6，已封板。  
**第四轮正式基线**：P7，完成并正式验收通过，已封板。验收结论以 **MULTI_AGENT_P7_SEAL_RESULT_2026-03.md** 为准。  

---

## 2. P7 唯一目标（窄定义）

**一句话定位**：  
在不改变现有三图、契约和执行边界的前提下，建立**预算治理、统一回退、稳定观测、压测与故障注入**的正式基线，使 P1～P6 形成可长期运行、可量化验收、可故障定位的稳定架构。

### 2.1 范围内

P7 仅包含以下治理与稳定性收口事项：

1. **预算治理**
   - 统一定义图级 / 子图级 / 节点级预算
   - 明确超时、超步数、重试耗尽的治理口径
   - 建立 budget breach 的可观测输出

2. **统一回退策略**
   - Chat / Supervised / Intent 三图分别固定失败收口矩阵
   - rule / llm / local loop 的失败语义统一化
   - 降级路径、回退原因、最终输出口径统一

3. **稳定观测**
   - 将 supervisor、subgraph、local loop、fallback、budget breach 纳入统一观测面
   - 建立可查询的聚合指标与审计字段
   - 让“为什么失败 / 在哪里失败 / 如何回退”可被稳定追踪

4. **压测与故障注入**
   - 为三图建立最小压测样例和故障注入样例
   - 证明系统在超时、异常、下游不可用、LLM 异常返回下仍可收口
   - 形成最小运行基线而不是一次性临时脚本

### 2.2 范围外

P7 **不**做以下任何事项：

- 不新增业务图
- 不改 execution_profile
- 不改三图主链结构
- 不新增 Agent 类型
- 不做全局 ReAct
- 不做新的多 Agent handoff
- 不做并行 agent
- 不做 executor 业务扩权
- 不把治理工作包装成“顺手扩能力”
- 不把 P8 级别能力提前混入 P7

---

## 3. P7 验收标准（实施前写死）

以下为 P7 验收必要条件，缺一即不通过。

### 3.1 三图结构与阶段边界保持不变

Chat / Supervised / Intent 三图的主链节点与 execution_profile 语义不变；  
P7 只能补治理与稳定性，不得顺手修改图结构或图语义。

### 3.2 预算治理形成统一契约

必须存在统一的预算字段或预算契约，至少覆盖：

- 图级执行预算
- 子图级预算
- 节点级超时 / 局部循环上限 / 重试上限

并且预算突破时有稳定失败码、稳定 trace 和稳定 fallback。

### 3.3 三图失败收口矩阵固定

必须为 Chat / Supervised / Intent 分别定义并固化：

- 失败位置
- 失败类型
- 收口策略
- 可见输出策略
- 不跨图逃逸规则

不得出现“由实现者临场决定如何回退”的情况。

### 3.4 观测面统一

必须能稳定观测并聚合以下信息：

- execution_profile 分布
- supervisor 决策来源与失败码
- subgraph_failure / local_loop failure 分布
- budget breach 分布
- fallback 次数与类型
- 最终输出是否成功生成
- 各图的延迟分布与错误分布

**当前满足口径**：P7 阶段按「**最小统一观测面**」验收；统一 ledger 聚合已包含 Supervisor 路径与 budget_breach_metrics（来自 supervisor_decision_log）。subgraph/local loop 的**全量 ledger 聚合**不在 P7 范围，仍通过 run output / subgraph_trace / subgraph_failure 审计。详见 **MULTI_AGENT_P7_3_OBSERVABILITY_SCOPE_2026-03.md**。

### 3.5 故障注入可重复运行

必须至少对以下故障类型具备可重复运行的注入样例：

- LLM timeout / bad json / internal error
- 子图节点持续失败
- local loop timeout / max steps / internal error
- ledger / metrics 写入失败的静默降级
- handler 内部异常或依赖缺失

且注入后系统仍按既定矩阵收口。

### 3.6 压测口径固定

必须形成最小压测口径，至少包含：

- Chat 图基线路径
- Supervised 图 rule / llm 路径
- Intent 图成功 / 失败 / 局部循环路径

输出至少包含延迟、成功率、fallback 率、关键失败码分布。

**当前满足口径**：压测口径与基线文档/索引已固定（P7_4_FAULT_INJECTION_BASELINE）；验收级输出需至少一份**最小压测结果摘要**（模板与示例见该文档 §4、§5）。补齐后可按本条满足；或 seal result 中写明按「压测口径与基线文档已固定」满足并引用 §5。

### 3.7 executor 不扩权

P7 不得通过“为了稳定性方便”把预算、回退、循环、分派等业务语义重新下沉到 executor。  
executor 仍只做通用执行器和通用状态推进。

### 3.8 可封板、可维护

P7 实施完成后，必须形成：

- 范围文档
- 契约/实施文档
- 集成验收文档
- seal result 文档

并明确冻结非目标，避免后续把能力扩张混入治理基线。

---

## 4. 实现约束（防止治理工作变相扩能力）

### 4.1 不改三图主链

不得修改：

- Chat 图主链
- Supervised 图主链
- Intent 图主链

如需新增观测节点、治理节点、额外分支，视为超范围。

### 4.2 不新增业务 Agent

P7 不新增任何业务 Agent，包括但不限于：

- retrieval_agent
- planner_agent
- review_agent
- memory_agent
- multi_judge

### 4.3 不做全局统一 ReAct runtime

不得引入：

- 全局 scratchpad
- 全局 loop runtime
- 跨图 handoff
- executor 感知子图循环语义

### 4.4 不改变已固化契约

不得破坏以下契约的字段与消费口径：

- supervisor proposal
- subgraph_commit_summary
- subgraph_failure
- subgraph_trace
- supervisor_decision_log

如需扩展，只允许向后兼容扩展，且不得改变既有消费优先级。

### 4.5 治理信号只作观测与收口，不作新路由入口

P7 中新增的预算、压测、故障注入信号，只能用于：

- 观测
- 失败收口
- 封板验收

不得演变成新的 execution_profile、图选择器或全局业务路由开关。

---

## 5. P7 建议范围拆解

### P7-1：预算契约统一化

目标：
- 固定图级 / 子图级 / 节点级预算字段
- 定义 budget breach 失败码
- 明确预算突破后如何写 trace / failure / fallback

建议产出：
- `graph_contract.py` 中预算常量与失败码
- 预算校验辅助函数
- 预算 breach 的单测

### P7-2：统一失败收口矩阵

目标：
- 固定三图失败收口矩阵
- 将当前分散在 handler / 文档中的失败策略整理为单一治理表
- 明确“失败时输出什么、保留什么、不保留什么”

建议产出：
- `docs/50_project/MULTI_AGENT_P7_2_FAILURE_MATRIX_2026-03.md`
- 对应 contract/常量
- 结构断言与集成测试

### P7-3：统一观测与聚合

目标：
- 把 supervisor、subgraph、local loop、fallback、budget breach 纳入统一 ledger/metrics 视图
- 提供稳定的观测字段说明

建议产出：
- ledger 表或聚合扩展
- metrics 聚合项
- 观测口径文档

### P7-4：压测与故障注入样例固化

目标：
- 不再依赖临时 patch 和临时脚本
- 为三图建立最小可重复故障注入和压测样例

建议产出：
- integration tests / fault injection tests
- load/smoke baseline 文档
- 压测结果摘要模板

### P7-5：封板验收

目标：
- 汇总 3.1～3.8
- 明确哪些属于后续阶段，不纳入 P7
- 产出 seal result

---

## 6. 与现有文档的关系

- **MULTI_AGENT_ARCH_REFACTOR_2026-03.md**：架构总则、阶段顺序与文档关系仍以此为总表；P7 的范围与验收以本文档为准。  
- **MULTI_AGENT_P4_SCOPE_2026-03.md**：P7 不得破坏 Intent 子图透明封装及其既有契约。  
- **MULTI_AGENT_P5_SCOPE_2026-03.md / P5_SEAL_RESULT**：P7 不得回改 Supervisor 双模决策边界，只能补治理与观测。  
- **MULTI_AGENT_P6_SCOPE_2026-03.md / P6_1 / P6_2 / P6_3 / P6_SEAL_RESULT**：P7 不得改变局部循环的范围、边界和出口契约，只能补预算、观测、回退与验收能力。  
- **MULTI_AGENT_P7_2_FAILURE_MATRIX_2026-03.md**：P7-2 三图失败收口矩阵；失败位置、类型、收口策略、可见输出、不跨图；与 P4/P5/P6 契约一致。  
- **MULTI_AGENT_P7_4_FAULT_INJECTION_BASELINE_2026-03.md**：P7-4 压测与故障注入基线；三图故障注入用例索引、压测口径、结果摘要模板与最小结果示例。  
- **MULTI_AGENT_P7_3_OBSERVABILITY_SCOPE_2026-03.md**：P7-3 当前统一观测面范围边界；最小观测面 vs 全量 ledger 聚合（subgraph/local loop 留待后续）。  
- **MULTI_AGENT_P7_ACCEPTANCE_PREVIEW_2026-03.md**：P7 验收汇总预审；8 条逐条定性（满足/基本满足），3.4/3.6 口径说明；建议补齐后再封板。

若 P7_SCOPE 与其它旧文档在 P7 范围上冲突，以本文档为准；  
若涉及 P1～P6 已封板事实，以对应 seal result 为准，不得通过 P7 回改。

---

## 7. 冻结非目标（不纳入 P7 本轮）

以下事项**不属于 P7**，不得以“治理增强”“稳定性优化”“方便观测”为名混入：

- 全局 ReAct
- 新 Agent
- 并行 agent
- 多 Agent handoff 扩展
- executor 业务扩权
- 三图结构变更
- execution_profile 变更
- Chat / Supervised / Intent 主链变更
- 记忆系统扩张
- RAG / retrieval 体系扩张
- 新业务场景
- 前端交互层改造
- 把 P8 级能力包装成 P7 稳定性工作

---

## 8. 结论

- **P1～P6 已全部完成并封板。**
- **P7** 定位为：治理与稳定性基线，而不是能力扩张基线。
- P7 的唯一目标是：在不改现有结构和能力边界的前提下，补齐预算治理、统一回退、统一观测、压测与故障注入，使当前架构进入可长期运行的稳定态。
- 实施前应以本文档锁定范围、验收标准与冻结非目标；实施完成后应产出 seal result 并同步更新 REFACTOR。  
- **当前状态**：P7 已完成并正式验收通过；观测边界与压测口径收窄说明已就绪，**MULTI_AGENT_P7_SEAL_RESULT_2026-03.md** 已产出并更新阶段为「完成并正式验收通过」。
