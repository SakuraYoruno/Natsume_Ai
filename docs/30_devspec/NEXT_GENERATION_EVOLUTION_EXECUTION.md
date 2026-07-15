受众：CODEX
标题：Natsume AI 下一代演进正式执行合同

# Natsume AI 下一代演进正式执行合同

版本：v1.2
日期：2026-07-15
状态：`NE-01_IN_PROGRESS / PROGRAM_INCOMPLETE`
代码基线：`origin/dev@a6d7ae383`，叠加仓库清理 `f0cd1dfd`
目标架构：[TARGET_ARCHITECTURE.md](../50_project/arch/TARGET_ARCHITECTURE.md)
当前事实：[CURRENT_CODE_ARCHITECTURE.md](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
差距矩阵：[ARCHITECTURE_GAP_MATRIX.md](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)

本文是唯一活动执行合同。旧 M48、M49～M57、AW、GR 和 TASK-xxxx 文件均已归档，只能作为需求和证据来源，不能决定当前顺序或完成状态。

## 1. 唯一目标

在不重建第二套 Runtime、Life Core、Memory 或 Capability 平台的前提下，完成以下闭环：

1. 修复当前 governed execution 的真实断点，并删除 legacy authority。
2. 删除全局 service locator，形成可测试、可隔离的显式进程 composition。
3. 把公共 PlanningSearch 从“算法和拓扑排序”升级为能生成真实候选、可证明收益的通用规划能力。
4. 建立 World/Scene/Intent/Guardian 的统一局面与动作治理。
5. 建立无副作用 Evolution Lab、Scenario 与 Counterfactual。
6. 建立 Skill Compiler、成熟度、签名供应链、staged activation 与 rollback。
7. 建立 Experience Feedback、Knowledge OS 与 Identity Migration 的受审演化闭环。
8. 用一次最终全量与真实服务验收重新证明 Full Architecture。

## 2. 当前起点不得再误写

| 项目 | 执行起点 |
|---|---|
| Host `chat/skill/delegate/plan` | 已接 canonical runtime，作为可复用地基 |
| Scheduler | `WIRED`：真实 occurrence 已贯通 `WorkOrderV3 + Budget + child grant + Review + Verification + Acceptance + Receipt` |
| Internal Agent | `WIRED`：边界 v2 已立即编译为 V3，完整 Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay 已通过真实集成 |
| Durable DAG | `PARTIAL_B1_STORE`：Coordinator 消费 B0、原子 READY claim、到期 promotion 与预算并发门禁；store 具备 retry deadline/attempt 2，但生产失败尚未调用 `schedule_retry`，真正并行、retry budget、heartbeat/takeover 和 continuation 未实现 |
| Budget | Host/Scheduler/Internal 已接；program-wide 与全部策略语义未完成 |
| Review | durable token 已有；自动 continuation 未完成 |
| Persona 演化 authority | `CENTRAL_FENCE_WIRED`：legacy runtime writer 已被中央拒绝；当前只保留无副作用候选，proposal/Review/版本激活将在 NE-06 建立 |
| PlanningSearch | 公共内核存在；Host 不生成竞争方案 |
| Pentest L3 | shadow、非 durable 校准、不能生成 capability-unlocking 后继 |
| Code/Research | 只有 synthetic benchmark，不是生产收益 |
| Composition | typed Task container 可复用；全局 locator 未删除 |
| Evolution abilities | 多为 partial/placeholder/missing |

任何 PR 或报告若把上述 `PARTIAL/BROKEN/SHADOW` 写成 `DONE/PASS`，该交付自动失败。

任何生产代码或工程工具变更必须在同一变更集中更新 README、对应权威文档或机器状态；CI 的 change-contract 门禁直接检查 Git 变更集，不能再用“文档最近更新过”代替本次同步。架构语义变化还必须按 freshness policy 的 source→doc 映射更新对应事实、数据流、图和 Trace。

## 3. 执行 DAG

```text
NE-00 文档与机器事实重置
  ↓
NE-01 执行主链与显式 Composition 闭环
  ├──────────────┐
  ↓              ↓
NE-02 真实规划   NE-03 World/Scene/Intent/Guardian
  └──────┬───────┘
         ↓
NE-04 Evolution Lab / Scenario / Counterfactual
  ├──────────────┐
  ↓              ↓
NE-05 Skill      NE-06 Feedback / Knowledge / Identity
  └──────┬───────┘
         ↓
NE-07 Full Program Acceptance
```

NE-02 与 NE-03 可在 NE-01 合同冻结后并行；NE-05 与 NE-06 可在 NE-04 的实验/提案合同冻结后并行。不得为了“并行”同时修改同一 authority、migration 或 composition 文件。

## 4. Architecture Waves

### NE-00：事实重置与文档权威链

状态：`COMPLETED`

目标：删除“目标、事实、状态、历史混在一起”的文档结构。

交付：

- 当前代码事实、稳定目标、差距矩阵、唯一执行合同四层分离；
- `current_architecture_status.json` 成为机器可读当前状态；
- 旧 M48/AW/GR 文档进入 `docs/_history/`，历史 receipt 原样保留；
- indexes、PRD、DevSpec、Trace 只指向新权威链；
- 文档门禁阻止历史计划再次成为 current authority。

完成证据：权威文档链接、JSON、受众 lint、authority gate、全量 freshness、相关 contract 与全仓静态扫描均通过；旧计划和失真专项文档已归档或删除。

### NE-01：执行主链与显式 Composition 闭环

状态：`IN_PROGRESS`

当前已完成切片：

- Persona 中央写入围栏、迁移专用审计入口、无副作用候选化及 `T-PERSONA-FENCE-01`；
- Scheduler 真实 `WorkOrderV3` admission、Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay；
- Internal Agent 真实 `WorkOrderV3` admission、Secure Ingress principal、Budget、`delegate:agent_runtime` child grant、PRE/POST Review、Verification、Acceptance、Receipt 与 replay；
- Durable DAG B1 store/coordinator 子集：Coordinator 消费 B0，原子提交 Attempt/Node/Lease/初始 Heartbeat/Outbox，并执行到期 promotion；store 级 retry deadline、attempt 序列、并发门禁、backoff cancel 与 claim exact replay 已持久化。生产 Coordinator 尚未从真实失败进入 `schedule_retry`，所以不能把手工 store 测试写成生产自动重试；0089 若在物理事务提交后丢失 migration receipt，只能在 checksum、列约束与索引形状全部吻合时修复 receipt；
- Executive 生产入口、legacy SkillDag 执行和进程内任务 registry 已删除，`task_progress/recent_turn` 改为 durable projection/read route。
- Workspace D0 统一 path resolver 已接入 Agent 文件 I/O/list、截图保存、Verification cwd/argv 和 git cwd，封闭绝对路径、遍历、同前缀目录及已解析 link 逃逸。
- Composition 第一刀删除 5 个生产零消费者全局槽位、4 个死 accessor 及重导出/reset 残留；排除 6 个 lock 与 `application_ports` 端口束后的可变服务引用由 59 降为 54，但全局 container 仍存在。

Lane A 已完成；Lane B 只有 B1 store/coordinator 子集，生产 retry entry、retry budget、heartbeat/takeover 与真正并行仍未完成。Lane D 的 durable workspace authority/隔离 worktree/显式 composition 也未完成，因此 NE-01 不得封板。长时间人工 Review 超过 child-grant TTL 的统一 renewal/derivation 仍随 Review continuation 一并解决。

目标：先修发动机底座，保证所有生产入口真的可执行，再删除第二 authority。

#### Lane A：Scheduler / Internal contract repair

状态：`COMPLETED`（Scheduler 与 Internal 均有首次真实 admission/replay 证据）。

- 将 `AgentWorkOrder` 转为显式 `WorkOrderV3`；
- 每路必须有 `BudgetPolicy`、node-bound grant、Review runtime、Verification、Acceptance 与 Receipt；
- 增加首个真实 admission/integration，不允许只测 startup；
- Host Input/Gateway/Scheduler/Internal/Operator 的 route matrix 全部 fail closed。

#### Lane B：Durable DAG completion

状态：`PARTIAL_B1_STORE / LANE_IN_PROGRESS`。当前证明 durable decision、atomic claim、store 级 retry deadline 与到期 continuation；尚未证明生产失败会进入 retry，也不代表并行 fan-out 或运行中 takeover 已完成。

- 调度所有依赖已满足节点，支持有界 fan-out/fan-in；
- 真正消费 `retry_policy.max_attempts`、backoff 和 retry budget；
- 实现 resume 后的自动 continuation，保证 checkpoint 后不重放已确认副作用；
- 并发、retry、cancel、lease takeover、crash recovery 均产生同一 lineage。

#### Lane C：Legacy authority removal

- 在 `PersonaEngine.apply_growth_delta()` authority 边界中央 fail closed 所有非迁移调用，并对 stage、milestone、interaction、emergent weekly 及后续新增调用点建立静态/运行时门禁；NE-01 只阻断旧写入，不在此波偷偷建立第二套演化系统；
- 删除 Executive `_execute_explicit_skill_dag` 生产路径；
- 删除 `LegacyRuntimeTaskRegistry` 写入和全局 accessor；
- `task_progress/recent_turn` 改为 projection/read route，不再借 Executive 执行；
- 删除 cycle→忽略依赖串行的兼容行为。

#### Lane D：Composition 与 workspace

- D0 已完成 scoped path-safety vertical slice；它不是 per-task workspace ownership 或 OS/tenant/worktree 隔离；
- 用 `HostApplicationContainer`、`ObsApplicationContainer`、`WorkerApplicationContainer` 等 typed scopes 替代模块全局 container；
- route/handler 只依赖显式 ports；
- 消除业务层 `get_*()` service locator 和 test reset 单例；
- 每个 Task/Experiment 绑定 workspace owner、root、isolation mode 与 cleanup receipt；
- Code shadow 使用隔离 worktree，禁止污染主工作区。

完成条件：Scheduler/Internal 首次真实任务成功；全 route 使用 `WorkOrderV3`；legacy registry 写入为 0；T-PERSONA-FENCE-01 通过；global service locator/accessor 为 0；DAG retry/parallel/resume 有 crash recovery 证据。

### NE-02：真实通用规划与收益证明

目标：让 PlanningSearch 生成并选择竞争方案，而不是包装上游既定步骤。

#### 公共合同

- `SuccessorCatalogPort`：根据虚拟状态动态生成后继；
- `VirtualStateSnapshot`：schema、digest、capabilities、facts、constraints；
- `CalibrationRepository`：durable prediction、baseline、selected path、actual outcome、cost；
- `DomainBenchmarkPort`：真实 workload、baseline、verifier、budget、isolation；
- Search result 标准编译为 node-bound immutable TaskGraph payload。

#### Research 第一真实领域

- 使用真实只读网页/文件来源与 Research WorkOrder；
- 候选覆盖 query、primary-source、cross-check、conflict resolution、stop；
- verifier 计算 claim coverage、引用有效率、主来源比例、冲突消解、成本和真实延迟；
- 同预算比较当前 baseline 与 PlanningSearch，先 shadow，不影响用户答案。

#### Code 第二真实领域

- 使用临时 worktree 和固定真实任务集；
- 候选覆盖 inspect、patch、unit、integration、rollback；
- verifier 使用真实测试、静态检查和 diff risk；
- 只在隔离 workspace 执行，不改主工作区。

#### Pentest 校准修正

- expander 根据虚拟 capability/fact 动态产生后继；
- 真实 PreActionGate 只在执行当前节点前复验；
- 校准跨 run 持久化；
- 不得用 baseline 根动作 outcome 冒充 L3 未执行路径 outcome；
- 达到预注册样本量、误差、安全和 uplift 门槛后，才允许 influence proposal。

完成条件：Research 真实 shadow 证明预设净收益；Code 至少完成 production shadow；Pentest L3 可在本波保持 shadow，或在 durable Gate 通过后受控切流。若保持 shadow，必须把 Target Gate #6 记录为 NE-07 未决条件；Synthetic benchmark 只能作为算法回归。

### NE-03：World、Scene、Intent 与 Runtime Guardian

目标：形成“当前局面 → 场景约束 → 意图仲裁 → 动作预演 → 执行”的统一治理链。

交付：

- WorldFact/WorldSnapshot 有唯一 resolver、provenance、confidence、TTL、conflict 和 workspace scope；
- Project/User/Environment 状态进入 Context Pack，但不直接改长期记忆；
- Scene transition 写入 journal，并统一影响 Surface、Proactive 和 Task context；
- Intent Arbiter 合并显式用户目标、任务、关系、场景、风险、权限与长期目标；
- 高风险 action 必须经过统一 preview/shadow；
- Runtime Guardian 统一 dependency health、degradation、recovery、maintenance/sleep 与 SLO evidence。

完成条件：至少覆盖架构重构、调试、研究、闲聊、深夜、用户烦躁、游戏低打扰和发布维护八类场景；所有约束保持“只收紧、不覆盖用户目标”；故障进入/恢复可回放。

### NE-04：Evolution Lab、Scenario 与 Counterfactual

目标：让所有演化先在隔离实验室获得证据。

交付：

- versioned `ExperimentSpec`、dataset、seed、budget、policy variants；
- scenario runner、synthetic user/project、real-trace redacted replay；
- shadow executor 无生产 writer、TokenService、CapabilityBroker side-effect port 或 Edge actuator；
- counterfactual 比较原路径、替代路径、证据、成本、风险和可验证 outcome；
- ExperimentReport 只能生成 proposal，不能直接切生产；
- Operator Lab 提供只读结果、diff、证据和 Review 链接。

完成条件：Research planning、主动行为、记忆写入三类实验可重复；同 seed/dataset digest 结果稳定；Lab 外部副作用为 0。

### NE-05：Skill Compiler、成熟度与签名供应链

目标：把材料和成功轨迹转为可验证能力，但默认不能自动上线。

交付：

- Markdown、API contract、script、accepted trace → typed SkillDraft；
- 自动生成 input/output contract、风险、工具依赖、verifier、test vectors、rollback；
- bundle 生成 SBOM 和签名，进入 quarantine sandbox；
- staged activation/rollback 成为真实生命周期，不靠目录扫描/import；
- maturity policy 默认开启且 fail closed；新能力最高 L1/L2；
- promotion/degradation 由 durable evidence 与 Review 决定；
- 退役旧 Capability Forge 平行 routing authority，只保留 proposal source。

完成条件：至少一份 Markdown 和一条 accepted trace 各生成一个 draft；隔离验证、拒绝、staged activation、降级和 rollback 都有 receipt；未经审批自动升级为 0。

### NE-06：Experience Feedback、Knowledge OS 与 Identity Migration

目标：完成“经验可回流、知识可治理、人格可迁移但不自动漂移”的长期闭环。

#### Feedback

- 统一成功/失败/用户拒绝/工具可靠性/表达偏好来源；
- 只生成 `FeedbackProposal`，绑定 trace、outcome、置信度与 rollback；
- 修复 Prefrontal → `on_interaction_cycle()` 参数签名断链，并让异常进入可观测 evidence；
- 用 proposal-only adapter 替换 NE-01 已封口的 stage、milestone、interaction、emergent weekly 及其他 `apply_growth_delta()` 直接 writers；
- 删除其余吞异常的局部反馈链；
- proposal 进入 Lab、Review，再更新版本化 policy/maturity/knowledge。

#### Knowledge OS

- 明确 Life Memory 与 Project Knowledge namespace；
- 知识条目记录 provenance、version、validity、confidence、conflict 和 decision refs；
- 支持去重、过期、冲突、promotion、deprecation 与项目摘要；
- 重大架构选择生成 ADR/Decision Record，不把 Canon 当普通事实库。

#### Identity

- release 主链真实生成和持久化 IdentitySnapshot；
- 建立 migration plan/executor、persona/relationship regression、用户 Review；
- 重大变化默认阻断，激活后保留前一 release rollback ref；
- 记忆正文和私密 thought 不进入 snapshot。

完成条件：T-PERSONA-PROPOSAL-01 通过；至少一条真实任务经验走完整 proposal→Lab→Review→versioned apply；一个项目知识冲突被识别并处置；一次 identity no-change 与一次重大差异阻断/回滚演练通过。

### NE-07：Full Program Acceptance

目标：只运行一次完整最终验收，并明确外部环境限制。

进入条件：NE-01～NE-06 均 `CODE_COMPLETE`，没有已知 P0/P1，旧 authority usage 为 0；所有 Target Architecture Gate 仍是强制条件，早期 wave 允许 shadow 不等于最终豁免。

验收范围：

1. 全量 unit/contract/integration/e2e；
2. migration fresh/upgrade/backup/restore；
3. Docker 依赖、Host、Obs、Monitor UI、Gateway、Edge；
4. chat/skill/delegate/plan/scheduler/internal/operator 全路线 ledger 对账；
5. Research/Code/Pentest planning benchmark 与 calibration；
6. Evolution Lab、Skill compile、feedback、knowledge、identity golden paths；
7. security、privacy、performance、chaos、crash recovery、rollback；
8. ASR/TTS/VRM/VTS/硬件矩阵：可用则实测，不可用则精确记为 `DEGRADED/NOT_TESTED`。

其中 Pentest L3 必须满足 Target Gate #6：使用 durable calibration、能按虚拟状态生成后继，并由 promotion Gate 证明安全与收益；否则 NE-07 只能 `FAIL` 或 `ACCEPTANCE_PENDING_EXTERNAL`，不得 `PASS`。

完成状态只能是：

```text
PASS
ACCEPTANCE_PENDING_EXTERNAL
FAIL
```

不得用“测试很多”“diff 很大”“历史 22/22”替代最终 Gate。

## 5. 测试与效率合同

遵守以下节奏，避免再次把数小时花在每个小工作片上：

| 层级 | 何时运行 | 范围 |
|---|---|---|
| 小工作片 | 每次实现 | 相关 unit/contract/static；目标分钟级 |
| 波内汇合 | 一个 lane 完成 | 只跑该 lane 受影响的 integration |
| Wave Integration | 整个 NE 波代码接线完成 | 每波一次，包含一条真实 golden/smoke |
| Program Acceptance | NE-07 | 一次最终全量、Docker、前后端、Edge、真实 provider/hardware |

失败后只重跑失败节点和受影响 wave；禁止因一个测试修复重复跑全仓。全量测试在 NE-07 前仅用于仓库级高风险破坏，并必须记录原因。

## 6. 工作与提交合同

1. 每个 NE 波至少交付一条 production cutover、两个可量化架构改善和旧路径删除。
2. 只增加 adapter、shadow fixture、空目录、报告或测试时，状态保持 `IN_PROGRESS`。
3. 不按微型 TASK 编号逐个提交；按可审 vertical slice 中文提交。
4. 提交信息、PR 标题和正文统一使用中文。
5. 每个 PR 写清：真实生产路径、删除的旧 authority、定向测试、一次 wave integration、风险与回滚。
6. 不保护无价值 WIP；保护范围只包括用户明确资产、secret、模型、有效运行数据和当前任务外的真实业务改动。
7. 运行证据写入被忽略的 `artifacts/`；只有最终、脱敏、可复核的 machine receipt 才进入文档证据目录。

## 7. 停止与回滚

仅在以下情况停止当前 wave：

- 发现会破坏用户数据、身份、密钥或不可逆外部副作用；
- 需要用户选择会显著改变产品语义；
- 同一外部依赖阻塞连续出现且无安全替代；
- 发现目标合同本身矛盾，继续实现会产生第二 authority。

代码难、测试慢、旧文档冲突或实现量大都不是停止理由。每个迁移必须 expand-first、读写可切、数据可备份；禁止 destructive down migration。

## 8. 后续 Goal 定义

文档验收后创建的 Goal 应使用以下目标，不附带微任务数量：

```text
按 NEXT_GENERATION_EVOLUTION_EXECUTION.md 完成 NE-01～NE-07：先修复 Scheduler/Internal 与 Durable DAG/Review/Budget/legacy authority，删除全局 service locator；再完成真实跨领域 PlanningSearch、World/Scene/Intent、Evolution Lab、Skill Compiler、Experience/Knowledge/Identity 受治理闭环；最后统一启动 Docker、Host、Obs、前端、Gateway 与 Edge 进行一次完整验收。全过程按小工作片定向单测、每波一次集成、最终一次全量实测执行。
```
