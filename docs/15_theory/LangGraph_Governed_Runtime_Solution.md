受众：MASTER
标题：LangGraph 高级编排能力与核心约束冲突的更优解法

# LangGraph 高级编排能力与核心约束冲突的更优解法

- **文档编号**：`LANGGRAPH_GOVERNED_RUNTIME_SOLUTION`
- **生成日期**：2026-03-06
- **适用范围**：`docs/30_devspec/ModelPlane.md`、`host_brain/integrations/graph_executor.py`、`graph_runtime.py`
- **主题**：在**不放松 ModelPlane 硬约束**的前提下，如何更好地吸收 LangGraph 的高级编排能力

---

## 一、执行摘要

当前系统并不是“LangGraph 天生不可用”，而是 **LangGraph 的默认控制语义** 不能直接等同于 **Host 的治理语义**。

现有方案将 LangGraph 主动降级为：

- 状态传播容器
- 节点执行包装器
- 可观测上下文承载层

同时放弃了：

- 条件边
- 并行分支
- 子图
- 断点续跑
- 流式输出

这并非完全错误，但仍然偏保守。更优的方向不是继续把图压成 `while loop + edge_map`，而是把系统明确拆成三层：

1. **Policy Plane（治理平面）**
   负责裁决、审计、边界锁、预算与降级规则。
   典型组件：Governor、AIRLOCK、RenderGuard、Deadline Supervisor。

2. **Execution Plane（执行平面）**
   由 LangGraph / Functional API 承担节点调度、状态传播、并行、checkpoint、interrupt、stream。

3. **Evidence Plane（证据平面）**
   专门记录 `route_token / branch_manifest / llm_call_log / sanitization_log / commit_hash / replay_ref`。

核心原则只有一句：

> **LLM 可以提案，LangGraph 可以执行，但只有 Host 可以提交（commit）。**

---

## 二、核心纠偏：真正冲突的不是能力本身，而是默认接法

原分析报告中的五项冲突，方向基本正确，但结论略显绝对。更准确的说法应当是：

> LangGraph 高级能力与当前 Host 治理协议的**默认接法**存在冲突，
> 而不是这些能力本体与治理约束天然不可兼容。

因此，正确问题不是“要不要禁用 LangGraph 能力”，而是：

- 哪些能力可以在治理化包装后开放
- 哪些能力只能对只读分支开放
- 哪些能力必须在提交边界之后才能启用
- 哪些能力仍应保持默认关闭

---

## 三、更优总体方案：Host Governance Overlay

建议将当前架构升级为：

```text
User Request
   ↓
Host Supervisor
   ├─ budget / deadline / dependency health
   ├─ governor rules
   └─ route compiler
   ↓
Governed LangGraph Runtime
   ├─ conditional edges on committed_route_token
   ├─ parallel branches on pre-issued branch_manifest
   ├─ audited subgraphs
   ├─ checkpoint / interrupt / resume
   └─ phase-locked streaming
   ↓
Evidence Plane
   ├─ llm_call_log
   ├─ airlock_log
   ├─ branch_commit_log
   ├─ graph_trace_flat
   └─ replay_index
```

这意味着：

- **LangGraph 不再持有主权**
- **Host 也不再亲自调度每一步执行细节**
- **治理协议从“代码结构限制”升级为“运行时协议限制”**

---

## 四、逐项冲突的更优解法

## 4.1 条件边冲突：从“LLM 决路由”改为“LLM 提案，Host 编译 route token”

### 当前问题

你当前担心的是：

- LangGraph `conditional_edges` 天然让“节点输出决定下一步”
- 这与 §0.1 “LLM 节点永远不裁决、不执行” 冲突

这个判断成立，但冲突点并不在 `conditional_edges` 本身，而在于 **routing function 直接消费了 LLM 原始结论**。

### 更优方案

将状态字段分成三类：

- `observed_*`：观测值，仅描述事实
- `proposed_*`：候选动作，仅表示提案
- `committed_*`：Host 已提交决策，只有 Host 可写

例如：

- `observed_risk_flags`
- `proposed_route_candidates`
- `committed_route_token`

LLM 只能输出：

- `risk_flags`
- `intent_draft`
- `needs_tool`
- `candidate_route`

Host Governor 再依据：

- 规则引擎
- 预算
- 依赖健康度
- AIRLOCK 状态
- RenderGuard 状态

统一编译成不可歧义的有限令牌：

- `GOV_OK_RENDER`
- `GOV_FAIL_FALLBACK`
- `GOV_AIRLOCK_REVIEW`
- `GOV_SECONDARY_REASONER`

最终 LangGraph 的条件边只读取 `committed_route_token`。

### 结果

这样就实现了：

- **LLM 可提议**
- **Host 才裁决**
- **LangGraph 只执行 Host 已提交的裁决**

因此，条件边可以恢复，但前提是 **条件边只允许基于 committed 状态跳转**。

---

## 4.2 并行分支冲突：从“串行审计”改为“预签发并行工单 + barrier 收敛”

### 当前问题

你当前将 AIRLOCK 的安全性建立在：

- 节点执行后
- 同步写审计
- 再决定是否允许外发

这在串行里是可行的，但一旦 fan-out，会出现：

- 分支上下文不一致
- 审计先后顺序失控
- `payload_digest` 竞争
- `sanitization_report_ref` 无法逐分支保证

### 更优方案

不要让并行分支“自己拼上下文后出站”，而是在 fan-out 之前增加一个 Host 节点：

`prepare_parallel_branches()`

它一次性为每个分支签发不可变工单（Branch Manifest）：

- `branch_id`
- `target_node`
- `input_digest`
- `sanitization_report_ref`
- `tool_visibility_hash`
- `deadline_at`
- `allowed_capabilities`
- `audit_ticket_id`

每个并行节点拿到的是自己的 `branch_envelope`，只允许在 envelope 定义范围内执行。

fan-in 时再加 `barrier_join()`：

- 校验所有分支是否存在 `audit_commit`
- 校验输出摘要是否与分支签发时一致
- 缺任一证据则 fail closed
- 若允许部分成功，必须记录 `join_policy_ref`

### 结果

这样 AIRLOCK 的逻辑从：

- **事后补写日志**

升级为：

- **事前签发出站许可**
- **事中分支隔离**
- **事后 barrier 校验**

这允许你安全开放如下并行场景：

- 并行只读检索
- 并行多模型评审
- 并行 rerank
- 并行候选生成

但不建议一开始就并行化任何**带副作用**的分支。

---

## 4.3 子图冲突：从“禁用子图”改为“审计透明子图”

### 当前问题

你现在认为 SubGraph 会导致：

- 父图视角变成黑盒
- `graph_trace` 不完整
- `llm_call_log` 丢失
- `replay_ref` 无法定位内部节点

这个问题在现有实现里确实成立，但根因是你把审计挂在 `_run_single_step` 这类父层循环上，而不是挂在每个实际执行节点上。

### 更优方案

所有子图必须通过统一工厂编译：

`compile_governed_subgraph(...)`

它自动为子图内部每个节点注入：

- `run_id`
- `parent_path`
- `subgraph_path`
- `node_id`
- `attempt`

并在执行前后统一写证据：

#### 执行前记录

- `input_digest`
- `prompt_profile_id`
- `model_profile_id`

#### 执行后记录

- `output_digest`
- `latency_ms`
- `sanitization_report_ref`

同时，每次模型调用必须拥有稳定 namespace，例如：

- `req_id/subgraph:s1/node:reasoner/attempt:2`

子图结束时输出 `subgraph_commit_summary`：

- 节点序列
- 关键状态摘要
- 最终 commit hash

Evidence Plane 则保存展平后的证据流：

- `graph_trace_flat`
- `llm_call_log_flat`
- `replay_index`

### 结果

这意味着：

- **允许子图存在**
- **但不允许不透明子图存在**

因此，子图应该恢复，但必须采用“审计透明子图”模式，而不是默认子图模式。

---

## 4.4 断点续跑冲突：从“状态恢复即流程恢复”改为“双时钟模型”

### 当前问题

你当前认为：

- LangGraph Checkpointer / Interrupt 是状态驱动
- Host 降级链路是时间驱动
- 两者时钟模型不一致
- 因此不能共存

这个判断只对了一半。

Checkpointer 的职责本来就不是“管理 SLA”，而是“保存可恢复现场”。
真正的问题是你把“是否允许恢复”也交给了同一层。

### 更优方案

引入 **双时钟模型**：

#### 1）Execution Clock（执行时钟）

由 LangGraph 提供：

- checkpoint
- interrupt
- resume

职责：
保存现场、恢复状态、支持人工介入。

#### 2）Policy Clock（治理时钟）

由 Host Supervisor 提供：

- `budget_ms_total`
- `budget_ms_remaining`
- `deadline_at_mono`
- `degrade_stage`
- `resume_deadline_at`

职责：
决定还能不能继续等、能不能恢复、该不该降级。

每次恢复前，先由 Host 检查：

- 是否已超过 `deadline_at_mono`
- 当前阶段是否允许再试
- 当前依赖是否健康
- 当前策略是否允许人工介入

如果已经超时，则不恢复 checkpoint，而是直接写入 synthetic event：

- `node_result = TIMEOUT_SYNTHETIC`
- `error_code = DEADLINE_EXCEEDED`
- `route_token = GOV_FALLBACK_SECONDARY`

### 结果

这样：

- LangGraph 负责“能不能恢复”
- Host 负责“允不允许恢复”

两者职责分离后，断点续跑与时间驱动降级不再冲突。

---

## 4.5 流式输出冲突：从“禁流式”改为“分相流式 + 快照锁定”

### 当前问题

你当前的担心是正确的：

- S1 还没结束，S2 就可能开始消费中间内容
- `intent_draft` 边界无法锁定
- `RenderGuard` 无法判断 S2 是否加料
- 因此只好完全串行

### 更优方案

不要用单通道流式，而要拆成三个流：

#### A. Presence Stream（在场流）

性质：

- 非权威
- 只给前端看
- 不允许作为下游节点输入

可包含：

- 正在思考/检索
- 节点阶段
- 临时 narrative chunk
- 进度型状态

#### B. Commit Stream（提交流）

性质：

- 权威
- 只在 S1 完成并经过 Governor 后产生

包含：

- `intent_commit`
- `s1_output_hash`
- `locked_fields`
- `render_input_snapshot_ref`

#### C. Render Stream（渲染流）

性质：

- 只能读取 Commit Stream 对应的快照
- 不允许回读 Presence Stream 的临时 token

最终由 RenderGuard 检查：

- `intent_commit` 未变化
- `locked_fields` 未变化
- 渲染输出只修改允许的派生字段

### 结果

因此，真正可行的做法不是彻底禁用 streaming，而是：

- 允许 S1 向 UI 提供低风险在场流
- 禁止 S2 消费未提交状态
- S2 只能基于 `render_input_snapshot_ref` 开始
- 只有 RenderGuard 通过后，最终输出才变成 authoritative

这样既保住低延迟体验，又不破坏 §4.2 的边界锁。

---

## 五、推荐的架构升级方式

推荐把当前“LangGraph 主动降级 + Host 全权 while-loop 调度”改造成：

### 5.1 治理在上，图在下

Host 负责：

- 决策提交
- 审计签发
- 边界锁定
- 超时与降级
- 输出授权

LangGraph 负责：

- 节点编排
- 状态传播
- 并行执行
- checkpoint / interrupt / resume
- 流式状态传递

### 5.2 将治理约束写成运行时协议，而非写死为结构禁令

原先的做法更像：

- “不许条件边”
- “不许子图”
- “不许流式”
- “不许并行”

更好的写法应改为：

- 默认禁用
- 满足 Governed Runtime Contract 时允许启用
- 启用范围、证据要求、失败闭锁策略必须明确

### 5.3 先恢复能力，再恢复复杂度

不建议一次性解封全部高级能力。建议按风险从低到高开放。

---

## 六、建议的落地优先级

### 第一阶段：恢复条件边，但仅基于 Host 提交令牌

先实现：

- `committed_route_token`
- `route compiler`
- `conditional_edges` 只认 committed 字段

这是收益最高、风险最低的一步。

---

### 第二阶段：恢复子图，但必须是审计透明子图

实现：

- `compile_governed_subgraph()`
- 展平 trace
- 稳定 namespace
- 子图 commit summary

这样可以显著改善图的可维护性，而不牺牲 §6 的证据链。

---

### 第三阶段：只开放“只读并行”

实现：

- `branch_manifest`
- `audit_ticket_id`
- `barrier_join()`

优先给以下场景开放：

- 多源检索
- 多模型比对
- 并行候选评估

暂不开放带副作用的并行分支。

---

### 第四阶段：把 timeout 从图逻辑里抽离

实现：

- `Policy Clock`
- `Execution Clock`
- synthetic timeout event
- Host Supervisor 先决判断

这样可恢复 checkpoint / interrupt，又不破坏降级链路。

---

### 第五阶段：引入分相流式

顺序建议为：

1. 先只开放 Presence Stream
2. 再补 Commit Stream
3. 最后再接 Render Stream 与 RenderGuard

不建议一步到位直接做跨阶段 token 级流式。

---

## 七、建议对 ModelPlane.md 的修订方向

建议不要把规范写成：

- LangGraph 高级能力与系统硬约束结构性冲突
- 因此默认永久禁用

更好的表述是：

> LangGraph 高级能力与当前 Host 治理协议的默认接法存在冲突。
> 在满足 Governed Runtime Contract 的前提下，可按能力类别、风险等级、证据完备度有条件启用。

建议新增一类条款：

### 7.1 Governed Runtime Contract（治理运行时契约）

至少包括：

- **Route Commitment Rule**
  条件边只能基于 `committed_route_token`

- **Parallel Branch Pre-Issuance Rule**
  并行分支必须先获得 `branch_manifest`

- **Transparent Subgraph Rule**
  子图内部节点必须对 Evidence Plane 透明

- **Dual-Clock Rule**
  状态恢复与超时降级必须分别由 Execution Clock / Policy Clock 管理

- **Phase-Locked Streaming Rule**
  Presence / Commit / Render 三类流必须隔离，且 Render 只能消费 committed snapshot

### 7.2 默认策略

建议明确：

- 默认禁用
- 白名单式启用
- 每项能力必须说明：
  - 适用范围
  - 证据要求
  - 失败闭锁策略
  - 回放要求
  - 边界锁定要求

---

## 八、最终结论

原报告的总体方向是正确的：
**治理必须优先于框架能力。**

但更精确的结论应当是：

> LangGraph 的高级能力并非天然不可用；
> 真正冲突的是其默认控制语义与当前 Host 治理协议未分层。
> 通过 route tokenization、audit pre-issuance、transparent subgraphs、dual-clock supervision、phase-locked streaming，可在不放松主权约束的前提下，有条件恢复大部分高级能力。

因此，系统不应停留在“主动降级使用 LangGraph”的长期状态，而应升级为：

> **Host 持有主权，LangGraph 承担执行，Evidence Plane 固化证据。**

---

## 九、建议的后续动作

建议下一步直接处理 `ModelPlane.md` 的条款层修订，将现有“结构性禁令”改写为“协议化准入”。

优先修订顺序建议为：

1. §0.1 主权约束 —— 增补 `proposal / commit` 二分
2. §0.2 AIRLOCK —— 增补 `branch_manifest / audit_ticket`
3. §4.2 输出边界 —— 增补 `phase-locked streaming`
4. §4.3 降级链路 —— 增补 `dual-clock supervision`
5. §6 审计链 —— 增补 `transparent subgraph` 与 `flat replay index`

---

## 十、附：一句话版判断

一句话概括：

> 不是"LangGraph 高级能力不能用"，而是"不能按默认方式直接用"；
> 必须先把治理语义协议化，再让 LangGraph 承担被治理的执行能力。

---

## 十一、架构结论修订：三句话版（2026-03-06 补充）

> 本节为对第八节结论的精确化修订，基于对"本质限制"与"工程风险"的分层判断。

### 11.1 四项负面评估的分层

原评估中的四项负面点，性质并不相同，需要区分对待：

#### 第一类：本质限制（结构性事实，无法消除，只能接受）

**① 不能完整释放 LangGraph 原生能力**

这是结构性事实，不是暂时缺陷。LangGraph 最有价值的部分，本来就是让图根据状态自主继续长、继续分叉、继续恢复、继续决策。但 Natsume 的主权约束恰恰要求：状态可以演化，决策必须上收。两者不可能同时最大化，这是不可调和的哲学冲突，不是实现问题。

**② 五阶段存在串行风险链（部分本质）**

系统将安全、审计、边界锁、提交权集中在 Host，天然会形成若干串行闸门。能优化的是"把不影响主权的部分前移并行"，但不可能把所有关键路径都并行化——否则主权本身会被稀释。串行闸门是主权集中的必然代价。

#### 第二类：工程风险（可以通过设计优化）

**③ 治理层复杂度不低，需要严格实现**

这是协议复杂度，不是普通代码复杂度。最危险的不是代码量大，而是"语义错位"：

- `route_token` 已提交，但 `audit_ticket` 还没落库
- `render_snapshot` 已锁定，但 `presence_stream` 还在被下游误消费

这类错位一旦发生，系统表面仍能运行，但主权约束已被悄悄打穿。这是可以通过严格的协议设计和集成测试来防范的工程问题。

**④ Evidence Plane 写入压力未被充分评估**

一旦以下能力全部上线：

- 子图透明化
- 分支 manifest
- 每节点前后摘要
- `llm_call_log`
- `replay_index`
- `presence / commit / render` 三类事件流

写入量会从"审计日志"膨胀为"事件源系统"。Evidence Plane 将不再只是留痕，而会变成吞吐瓶颈、延迟放大器、甚至故障传播点。这是可以通过分级写入、异步落库、分区索引等工程手段缓解的问题。

---

### 11.2 修订后的三句话架构结论

**第一句：**

> LangGraph 在该系统中的定位应固定为 **governed runtime**，而非 **autonomous orchestrator**。

**第二句：**

> Host 主权约束决定了图内自治只能存在于执行层，不能进入裁决层。

**第三句（最关键）：**

> 因此系统优化目标不是"释放 LangGraph 全能力"，而是"**以最小治理成本换取最高执行收益**"。

第三句直接改变落地策略：**不是所有可治理化能力都值得做。**

---

### 11.3 能力优先级三档分类

按"收益/主权风险比"重新分档：

#### 第一档：优先做（收益大、主权风险低）

| 能力 | 理由 |
|------|------|
| `committed_route_token` | 条件边恢复的最小代价，主权完全保留 |
| `transparent_subgraph` | 显著改善可维护性，证据链不损失 |
| `dual-clock supervision` | 解耦执行与治理，降低降级链路脆性 |

#### 第二档：可以做，但要严格限域

| 能力 | 限制条件 |
|------|---------|
| 只读并行分支 | 必须先有 `branch_manifest`，暂不含副作用分支 |
| Presence Stream | 只允许向 UI 单向输出，禁止作为下游节点输入 |

#### 第三档：不建议强求"接近原生体验"

| 能力 | 原因 |
|------|------|
| 图内自主降级 | 降级权属于 Host，不能下放给图 |
| 基于 LLM state 的自治路由 | 违反主权约束核心条款 |
| 跨阶段 token 级联动 streaming | 边界锁定成本极高，收益有限 |
| 带副作用的并行执行 | 审计闭锁难以保证，风险不可控 |

---

### 11.4 最终定位声明

> Natsume 采用 LangGraph，但不继承其默认自治语义。
> 系统仅复用其受控执行能力，所有裁决、提交、降级、边界锁与审计闭锁均保留在 Host 主权域内。
> LangGraph 在该系统中永远是 governed runtime，这不是妥协，而是正确的架构选择。
