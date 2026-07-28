受众：PROJECT
标题：Natsume AI 下一代目标架构

# Natsume AI 下一代目标架构

版本：v1.0
文档类型：`STABLE_TARGET / NO_EXECUTION_STATUS`

本文只定义最终应成立的架构边界，不记录某个任务是否完成。当前事实看 [CURRENT_CODE_ARCHITECTURE.md](CURRENT_CODE_ARCHITECTURE.md)，目标差距看 [ARCHITECTURE_GAP_MATRIX.md](ARCHITECTURE_GAP_MATRIX.md)，实施顺序看 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。

## 1. 总目标

Natsume AI 的下一代目标不是继续增加互不相连的“高级能力模块”，而是让系统同时具备三种稳定能力：

1. **可靠执行**：所有入口共享同一授权、规划、TaskGraph、预算、验证、验收和副作用提交链。
2. **可证明的规划**：规划器能从目标和虚拟状态生成多条候选路径，在真实 shadow 中证明收益后再受控切流。
3. **安全演化**：经验、技能、知识、策略和人格变化只能形成可审查提案，经过实验、成熟度、审批、版本激活和回滚后进入生产。

最终系统应从“具有数字生命表现层的 Agent 平台”演进为“可长期运行、可追溯成长、人格连续且用户拥有最终主权的个人数字生命操作系统”。

## 2. 不可放宽的原则

1. 每个长期状态只有一个 writer；projection、cache 和 UI 不拥有事实。
2. 每个执行动作只有一条 HostCommand → Graph → Attempt → Grant → Receipt lineage。
3. 搜索、模拟和反事实分析永远没有真实副作用端口。
4. worker 只执行冻结的 TaskGraph revision，不在执行中偷偷二次规划。
5. 失败重规划必须产生同一 `graph_id` 的新 revision，旧 revision 与失败证据不可变。
6. 自动学习只能生成 proposal，不能直接修改生产策略、知识规则、能力等级或人格。
7. Capability maturity 只能收紧权限；升级必须有证据和人工批准。
8. 人格、关系、主动性和用户主权规则的重大变化必须由用户确认并可回滚。
9. Host、Obs、Edge、Gateway 和 Worker 只通过版本化合同协作，不共享实现包或全局服务容器。
10. 运行数据进入 `runtime/`，验收产物进入 `artifacts/`；不恢复顶层 `data/skills/flows/integrations` 杂物目录。
11. 生产 manifest 只公布已可执行且受治理的能力；尚未接线的旧命令必须从
    active contract 退役并单独记录 replacement authority，禁止用长期
    `blocked` 条目伪装成已经存在的产品能力。

## 3. 最终生产拓扑

```text
User / Gateway / Scheduler / Internal Agent / Operator Command
  → Secure Ingress
  → Identity + Scope + Governor
  → HostCommand
  → Intent/Scene/World Context
  → Planning Search（仅规划）
  → Immutable TaskGraph Revision
  → Durable Task Runtime
     → Attempt / Lease / Retry / Idempotency
     → Budget / Review / Node-bound Grant
     → Worker / Capability Broker
     → Verification / Acceptance
  → Transactional Effect Intents
  → Memory / Life / Knowledge / Gateway / Edge Outbox
  → Immutable Receipts
  → Read-only Projections / Operator Console
```

演化旁路与生产主链严格分开：

```text
Production Trace / Outcome / Feedback / Documents
  → Evolution Lab（隔离、无副作用）
  → Experiment / Counterfactual / Skill Draft / Feedback Proposal
  → Review + Maturity + Identity Gate
  → Versioned Activation
  → Production Catalog
  → Rollback Point
```

## 4. Bounded Context

### 4.1 Composition Kernel

职责：按进程显式构造 Host、Obs、Worker、Scheduler 与 Edge 对象图，管理 startup、drain、shutdown 和资源所有权。

必须满足：

- 无模块级可变 service registry；
- application/domain 不导入 composition；
- FastAPI dependency 只从 `app.state` 或显式 provider 读取 typed ports；
- 测试可构造最小对象图，不依赖清理全局单例。

### 4.2 Governed Execution

职责：统一 HostCommand、WorkOrderV3、TaskGraph、Attempt、Lease、Retry、Budget、Review、Verification、Acceptance 与 Receipt。

必须覆盖：Chat、Host Input、Gateway、Scheduler、Internal Agent、Operator command、Domain Pack。

### 4.3 Planning and Decision

职责：将目标、上下文、约束和预算转为可比较候选路径，再编译为不可变图。

分层：

- L1：根据当前虚拟状态动态生成合法后继；
- L2：领域可注入的认知排序；
- L3：只在不确定性/收益阈值满足时执行受预算 MCTS；
- Compiler：把选中路径编译为 TaskGraph；
- Calibration：用真实 shadow outcome 对 baseline 与候选路径进行 durable 对账。

### 4.4 World, Scene and Intent

职责：维护“当前局面”，而不是复制聊天记忆。

World Model 统一表达用户、项目、环境、任务和长期目标事实；Scene Engine 将事实解析为表达/主动性约束；Intent Arbiter 合并显式用户目标、任务目标、关系、风险和权限。

任何推断都必须有来源、置信度、观察时间、有效期和冲突状态。Scene 与 Affect 只能收紧表达或要求 Review，不能取消用户已授权的任务。

### 4.5 Evolution Lab

职责：运行 benchmark、scenario、shadow、counterfactual 和 capability experiment。

Lab 只能读取脱敏快照和可复放 trace，输出 `ExperimentReport` 或 proposal；不得持有生产 TokenService、CapabilityBroker writer、Memory writer 或 Edge actuator。

真实 trace intake 必须在持久化前完成确定性字段投影。原始正文只参与
source digest，不进入 Lab 数据库；规则版本/摘要、输出 dataset digest、
Manifest 和 Redaction Receipt 原子提交。Operator Lab 使用独立只读连接重验
完整证据链，只展示 proposal、arm 差异、成本、风险、验证和 Review 引用，
不展示原始/sanitized dataset 正文，也不提供直接激活入口。

可信 Lab worker 必须运行在可重新检查的隔离容器中；镜像以 digest 固定，dataset 只读、output 临时、网络关闭、rootfs 只读，并删除 capability、限制 PID/内存。运行前由 durable operation ledger 签发唯一 fence；响应丢失重开原 receipt，未知结果进入 reconciliation。Docker/Verifier receipt 只使报告具备“可提交 Review”的资格，不授予 activation。

隔离输出采用 acceptance-root 下的单次 bind mount，以便容器退出后仍可由独立 host reader 重开；其 source path 不进入公开收据，只保留 digest，并在 run receipt durable commit 后删除。worker 本身只读挂载且源码 digest 纳入 sandbox profile。实现不得将 Docker 不可用降级为本机 subprocess。

### 4.6 Capability Lifecycle

职责：从文档、轨迹、脚本和 API 合同生成 Skill Draft；完成签名、SBOM、隔离验证、成熟度、staged activation 与 rollback。

已有 Skill Registry、Capability Broker、Signed Intake 和 scorecard 应成为该生命周期的基础，旧 Capability Forge 只能作为 proposal 输入，不继续作为平行 authority。

### 4.7 Experience and Knowledge

职责：从任务 outcome、用户反馈和实验报告中提炼可审查经验；管理项目知识、决策记录、冲突、过期和来源。

Life Memory 服务人格连续性；Knowledge OS 服务项目、概念、流程和决策。两者通过引用协作，不共享 writer。

任何交互统计、里程碑或模型总结都只能生成 `FeedbackProposal`；不得直接调用人格 trait writer。人格相关变更必须进入版本化 policy/identity activation，并保留 Review 与 rollback。

### 4.8 Identity and Release

职责：版本化人格核心、关系边界、表达策略、主动性边界和用户主权规则；在发布前生成快照、迁移计划、回归报告和回滚点。

Identity migration 不能由普通模型输出直接驱动。重大差异必须进入 Review，且默认阻断发布。

### 4.9 Runtime Guardian

职责：统一健康、依赖、降级、维护、睡眠、恢复和长期 SLO；将故障转为可解释的 `DegradationIncident`，而不是由各模块各自 fallback。

## 5. 核心合同

| 合同 | 必要字段/语义 |
|---|---|
| `WorldFact` | subject、predicate、value ref、source refs、confidence、observed/valid/expires、conflict state |
| `WorldSnapshot` | person/workspace/project、fact versions、source cursor、digest |
| `SceneDecision` | scene、confidence、evidence refs、expression/proactive constraints、TTL |
| `ArbitrationDecision` | explicit goal、candidate action、risk、authority binding、decision、reason codes |
| `SearchCandidate` | domain-neutral id、virtual preconditions/effects、risk/cost/value、payload ref |
| `SearchOutcome` | baseline、selected path、trace digest、actual verifier outcome、cost、calibration scope |
| `ExperimentSpec/Run` | dataset/version、policy variants、budget、seed、isolation、metrics、evidence |
| `SkillDraft/Bundle` | input/output schema、tools、risk、verifier、SBOM、signature、maturity、rollback |
| `FeedbackProposal` | source outcome、pattern、suggested change、confidence、review state、rollback ref |
| `KnowledgeItem` | namespace、provenance、version、validity、conflicts、promotion state |
| `IdentitySnapshot` | release ref、public identity digest、relationship/policy refs、diff、review/rollback |
| `DegradationIncident` | dependency、impact、entered/recovered time、policy、evidence、SLO effect |

所有 durable contract 必须有 schema version、稳定 ID、correlation/causation、visibility、evidence refs 和 canonical digest。

## 6. Authority Matrix

| 决策/状态 | 唯一 authority | 禁止事项 |
|---|---|---|
| 身份与入口 scope | Secure Ingress + Identity | route 自造 principal/scope |
| 执行许可 | Governor + node-bound grant | planner/worker 自己提权 |
| 计划选择 | PlanningSearch + approved compiler | worker 内二次规划 |
| 任务状态 | Durable Task Runtime | Executive/内存 registry 写第二份状态 |
| 预算 | Durable Budget Ledger | 本地字段估算后绕过账本 |
| 副作用 | Acceptance Effect Outbox | handler/fire-and-forget 直接写外部系统 |
| Life 状态 | Life Command/Event/Reducer | Surface、Tribe、Scheduler 各自推进 |
| World 状态 | World Model aggregate/projection | Memory/Scene 反写平行事实 |
| Knowledge | Knowledge OS proposal/apply | trace miner 直接改规则 |
| Capability 激活 | Signed Lifecycle + Review | 目录扫描或未签名 import |
| Persona/表达策略演化 | Feedback Proposal + Lab + Identity/Policy version activation | milestone、后台任务或模型输出直接写 trait |
| 人格迁移 | Identity Release Gate | 自动接受重大差异 |

## 7. 关键闭环

### 7.1 真实规划收益

```text
Goal + frozen context
  → domain expander 生成可竞争后继
  → baseline 与 PlanningSearch 同预算 shadow
  → 两者编译为隔离图
  → verifier/outcome/cost 对账
  → durable calibration
  → promotion proposal
  → 受控 influence
```

未执行的替代路径只能通过隔离实验或可验证模拟估计，不能伪装成真实 outcome。

### 7.2 Skill Compiler

```text
Document / Trace / Script / API Contract
  → extract typed steps and risks
  → SkillDraft
  → deterministic verifier + test vectors
  → signed bundle + SBOM
  → quarantine sandbox
  → Review
  → L1 staged activation
  → measured promotion / rollback
```

### 7.3 Experience Feedback

```text
Accepted/Failed outcome + user feedback
  → pattern extraction
  → FeedbackProposal
  → scenario/counterfactual evaluation
  → Review
  → versioned policy/knowledge update
```

### 7.4 Identity Migration

```text
Previous snapshot
  → migration plan
  → isolated apply
  → persona/relationship regression
  → diff + user review
  → release activation
  → rollback to previous release ref
```

## 8. 目标代码边界

目标不是机械新增一批顶层包，而是把现有资产迁到明确 owner：

| 现有资产 | 目标 owner |
|---|---|
| `composition/process_*` 全局 accessor | typed Host/Obs/Worker composition scopes |
| `runtime/world_model.py` | `digital_life/world_model/` bounded context |
| `persona/scene_policy.py` | `digital_life/scene/`，Persona 只消费表达约束 |
| `governor/action_*` | Governor arbitration application service |
| `planning_search/` | domain-neutral planning kernel + durable calibration ports |
| `app/capability_forge.py` | capability lifecycle proposal service |
| `skills/capability_scorecard.py` | capability lifecycle maturity projection |
| `evolution_lab/policy.py` | 完整 Evolution Lab application/domain |
| Memory/Retrieval/Canon | 独立 Memory 与 Knowledge namespaces，通过 proposal promotion |
| `release/identity_snapshot.py` | identity release/migration bounded context |

只有在实现第一条真实 vertical slice 时才创建目录；禁止提前生成空包和占位模块。

## 9. 最终验收 Gate

完整架构只有在以下全部成立时才能标记完成：

1. Chat、Gateway、Scheduler、Internal Agent、Operator command 全部使用 `WorkOrderV3` 和同一 durable runtime。
2. Executive SkillDag 与 `LegacyRuntimeTaskRegistry` 的生产写入为 0。
3. TaskGraph retry/backoff、并发、Review continuation 和 Budget 所有声明策略均由 runtime 消费。
4. 全局 service locator 和业务层 `get_*()` accessor 为 0。
5. Research 与 Code 至少一个领域以真实 workload、真实 verifier、真实成本证明 PlanningSearch 相对 baseline 的收益；另一个领域完成 production shadow。
6. Pentest L3 使用 durable calibration，能生成虚拟状态依赖的后继，且 promotion Gate 证明安全与收益。
7. World/Scene/Intent 成为生产 Context 与 Action preflight 的统一来源，不创建第二真相。
8. Evolution Lab、Skill Compiler、Feedback、Knowledge OS、Identity Migration 均有至少一条受治理生产闭环。
9. 未签名 bundle、未经审批 maturity 升级、直接 Persona trait writer、未经确认人格变化和 Lab 副作用数量均为 0。
10. Docker、Host、Obs、UI、Edge、migration/restore、security/performance/rollback 和可用硬件矩阵完成一次最终验收；不可用硬件明确记 `DEGRADED/NOT_TESTED`。

任何历史 PASS、测试数量或代码行数都不能替代这些 Gate。
