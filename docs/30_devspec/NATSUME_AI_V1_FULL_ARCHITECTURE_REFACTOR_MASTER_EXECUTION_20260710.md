受众：CODEX
标题：Natsume AI V1 完整架构重构总执行文档（M48-A～M57）

# Natsume AI V1 完整架构重构总执行文档（M48-A～M57）

版本：v1.0
日期：2026-07-10
状态：READY_FOR_PROGRAM_EXECUTION
文档类型：DevTasks / Master Execution Contract
程序任务编号：TASK-0461～TASK-0622，共 162 项，禁止复用、跳号或重定义
当前执行入口：TASK-0461～TASK-0485、TASK-0487～0488 已完成；TASK-0486 本地 Release Seal/rollback 资产继续 BLOCKED 于 identity/live/prod/MASTER Seal；M48-B Entry Gate 为 OPEN，TASK-0489～0490 为 IMPLEMENTED_AWAITING_PROTECTED_HANDOFF，TASK-0491 为下一独立执行入口；M49～M57 状态为 PROGRAMMED
代码基线：dev@1ec8be1a8a20397627855aa946c0ff8aaaa87b72
工作区状态：dirty；已有未提交业务代码、运行数据和本地配置必须原位保护
完整目标蓝图：docs/50_project/arch/NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md
工程合同：docs/30_devspec/DevSpec.md §16
唯一任务索引：docs/30_devspec/DevTasks.md
M48-A 权威任务卡：docs/30_devspec/NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md
长期演化架构依据：docs/50_project/arch/NATSUME_AI_V1_LONG_TERM_EVOLUTION_REFACTOR_PLAN_20260710.md
上游需求参考：C:/Users/34145/Downloads/Natsume_AI_V1_高级能力拓展重构计划_新增长期演化能力.md
维护责任：MASTER 批准范围、Gate、切流、例外和最终 Seal；CODEX 按任务依赖实施、验证、留证和回滚

本文是完整架构程序的唯一跨里程碑执行合同。目标蓝图解释最终归宿，DevSpec 定义工程不变量，DevTasks 提供唯一任务入口，本文冻结任务顺序、依赖、主要落点、退出条件、停止条件、回滚和最终退役。

当文档与实际代码冲突时，CODEX 必须暂停受影响任务并提交事实差异，不得用文档假设覆盖代码。TASK-0461～TASK-0486 的任务细节以 M48-A 权威任务卡为准；跨里程碑依赖、切流和最终 Gate 以本文为准。任何未通过 Seal 的能力不得在项目文档、UI 或发布说明中描述为已完成。

---

## 1. 程序目标与完成定义

本程序不是只拆 app 巨石，也不是只补一个 Task Runtime。完整完成态必须同时包括：

1. 独立版本化的 Contracts 与最小 Kernel；Host、Edge、Gateway、UI 共享合同，不共享 Host 实现。
2. 唯一 Composition Root、Settings、Feature Flags、Service Catalog 和生命周期拓扑；业务模块不再依赖进程全局 service locator。
3. Secure Ingress → Host Application → Task/Agent Runtime → Governed Side Effect 的唯一生产命令链。
4. Plan once, execute same revision；规划、授权、执行、验证、审批、回放引用同一 graph_id/revision。
5. Digital Life 单 coordinator、单 clock、LifeEventJournal 和可重放 projection；Surface build 为纯读。
6. LifeMemoryService、Canon proposal/apply、Knowledge Store、Event Journal、UoW、Outbox 和 Migration Catalog 的单一写边界。
7. Domain Pack SDK 与 Capability Platform；Pack 可独立发现、授权、启停、升级和回滚，但不能拥有第二套平台内核。
8. 单一 ModelAttempt、Prompt Registry、Capability/Tool Broker、AuthorizationGrant、Token/Review 和 Security Plane。
9. 可独立安装与回滚的 Edge Device Runtime；Edge 对 host_brain 实现导入为 0，物理输出 authority 唯一。
10. 只读 Query/Observability Plane 与 typed Operator Console；所有写动作回到 Secure Ingress。
11. 隔离 Evolution Lab、Skill Compiler、Counterfactual、Scenario、Knowledge OS 和 Identity Migration。
12. 旧 facade、旧 writer、重复 Runtime、内联 DDL、私有连接、原始思维链和死代码按证据退役，并由 ReleaseManifest v2 封板。

程序完成不是“新目录已经创建”，而是“新实现成为唯一 authority、旧任务已 drain、旧 writer/facade usage=0、projection 可重建、回滚已演练、TASK-0622 Final Seal 获 MASTER 批准”。

### 1.1 非目标

- 不用拆微服务掩盖循环依赖；先完成模块化单体和可替换端口。
- 不允许以迁移为名永久维持两套 Runtime、Life Core、Memory writer、Governor、Model Gateway 或 Device authority。
- 不进行一次性 big-bang 切换；每个 vertical slice 均经过 characterization、adapter、shadow、canary、drain 和 removal。
- 不让 Domain Pack、Obs、UI、Edge 或 Evolution Lab 获得平台级写权限。
- 不在本文重编号或改写 M48-A 已冻结任务。

---

## 2. 实际代码事实基线

| # | 基线事实 | 主要证据位置 | 执行约束 |
|---:|---|---|---|
| 1 | host_brain 约 799 个 Python 文件、361,339 行；app 约 229 个文件、105,416 行 | host_brain；host_brain/app | 必须分里程碑迁移，禁止单次重写 |
| 2 | surface_main_chain_adapter.py 约 4,782 行，agent_runtime_shared.py 约 4,142 行 | host_brain/app | 主链、适配和业务语义必须拆层 |
| 3 | input_routes.py、obs_routes_core.py 均超过 3,500 行 | host_brain/app | Command、Query、DTO、读模必须分离 |
| 4 | production_executor.py 约 4,587 行且混入 Media/Lightnovel/Gateway 领域流程 | host_brain/agent_runtime | Agent Runtime 与 Domain Pack 必须解耦 |
| 5 | dependencies.py 约有 52 个 Optional 全局和 56 个 getter | host_brain/app/dependencies.py | AppContainer 必须替换隐式全局 |
| 6 | app 与 tribe、persona、agent_runtime 等形成双向 package dependency | host_brain | 先建 import guard，再逐边断环 |
| 7 | 生产聊天链仍为 chat_handler → cortex re-export → agent_runtime_shared → executive_runtime_core | host_brain/app | 新 Host Application 必须真实切流而非新增旁路 |
| 8 | full Governor 结果会被后续 chat-lite Governor 上下文覆盖 | host_brain/app/chat_handler.py；host_brain/app/agent_runtime_shared.py | AuthorizationGrant 必须端到端同源 |
| 9 | GraphRuntimeContract 主要用于装配/warmup，生产聊天主链绕过 | host_brain/app | 不能把未接入组件视为统一 Runtime |
| 10 | SkillDag 依赖字段没有真实进入 dependency context，Executive 还会二次 Planning | host_brain/skills；host_brain/app/plan_graph.py | 必须 plan once、same revision |
| 11 | Executor 可先按 returncode/output 标成功，再附 verification | host_brain/agent_runtime | Verification FAIL/ERROR/required-NOT_RUN 不得 committed |
| 12 | Agent registry、scheduler、review/job registry 多套并存，部分只在内存 | host_brain | 统一 taxonomy、持久 attempt/review 和 worker lifecycle |
| 13 | agent_executor/react_loop 会持久化 raw thought | host_brain/agent_runtime | 只保留结构化 decision summary 和 evidence refs |
| 14 | TokenService 多实例，confirmed=true 可替代强授权语义 | host_brain/governor；host_brain/app | Token/Grant 必须持久单写并绑定 WorkOrder |
| 15 | 模型路由、重试、降级散布在 llm_helpers、llm_gateway、provider_factory、model_router、routing_matrix 等 | host_brain | 单一 ModelGateway、ProviderAttempt 和 route trace |
| 16 | LifeContinuity、LifeOrgans、Tribe、InnerWorld、DigitalLifeSupervisor 与 Scheduler 存在平行推进面 | host_brain/runtime/life_continuity.py；host_brain/runtime/life_organs.py；host_brain/tribe；host_brain/persona/inner_world.py | Digital Life 只能有一个 coordinator/clock/writer |
| 17 | Life 保存有无条件 upsert；Relationship 恢复信息不完整 | host_brain/runtime/life_continuity.py；host_brain/bond；host_brain/tribe | OCC、event/reducer、完整关系投影 |
| 18 | MemoryEventStore、LifeMemory、多套 Episodic/Distillation 并存 | host_brain/memory | LifeMemoryService 成为唯一写服务 |
| 19 | 运行期内联 DDL、Ledger 私有 _conn、migration 重复编号和缺口并存 | host_brain/audit/sqlite_ledger.py；host_brain/audit/migration_runner.py；host_brain/audit/migrations | UoW/migration catalog 接管 schema 和事务 |
| 20 | Tribe、Pentest、Swarm 等内存 EventBus 在拥塞/重启时可能丢事件 | host_brain | Journal/Outbox 是事实，EventBus 只是投影传输 |
| 21 | Pentest 多处直接写 WorldCanon，领域知识与普通检索 namespace 边界不清 | host_brain/pentest | Pack 隔离、proposal/apply 单写、检索域门禁 |
| 22 | Edge 导入 host_brain 实现，Host 又导入 Edge 配置/健康；端口 18770 有默认占用冲突 | edge_client；host_brain | Contracts-only Edge 与 Service Catalog |
| 23 | Obs 同时包含 query 和 command；Gateway webhook 验签、重放和 durable inbox/outbox 不完整 | host_brain/app/obs_routes_core.py；host_brain/app/input_service_gateway.py；edge_client/gateway | M48-B 先完成平台 fail-closed 封口 |
| 24 | Skill/Provider 安装可把 Python 源码热导入 Host，缺 digest/signature/SBOM/quarantine/sandbox 全链 | host_brain/skills；host_brain/integrations | M54 必须完成供应链 Gate |
| 25 | 当前 Monitor UI 缺统一 OpenAPI codegen/typecheck/a11y/E2E；CI 未完整覆盖 Edge、Electron、Unity/.NET、真实迁移和可复现发布 | host_brain/app/monitor_ui/src；tests；.github/workflows | M55～M57 必须补齐独立安装、UI 和 Release 矩阵 |

TASK-0487 会把上述事实固化为可重复 machine-readable baseline。任何数量变化不改变问题性质，但必须在任务证据中记录。

---

## 3. 冻结目标架构

~~~mermaid
flowchart TB
    SURF["Chat / Gateway / Scheduler / Operator / Device Feedback"]
    ING["Secure Ingress：Identity / Signature / Rate / Idempotency"]
    CMD["Host Application：Conversation / Work / Review / Policy"]
    TASK["Task Runtime：Graph / Scheduler / Attempt / Budget"]
    AGENT["Agent Runtime：Worker / Tool Port / Verification / Acceptance"]
    LIFE["Digital Life Kernel：Journal / Projection / Scene / Intent"]
    PACK["Domain Pack Runtime：Code / Research / Media / Pentest / BioOps"]
    MODEL["Model Plane：Router / Provider / Prompt / Attempt / Usage"]
    CAP["Capability Plane：Catalog / Bundle / Broker / Maturity"]
    DATA["Event and Data Plane：UoW / Journal / Outbox / Repository"]
    QUERY["Query and Observability：Projection / Trace / Metrics / Replay"]
    EDGE["Edge Device Runtime：Voice / Avatar / Desktop / Hardware"]
    UI["Operator Console：Typed Read Models / Governed Commands"]
    EVO["Evolution Lab：Benchmark / Compiler / Feedback / Migration"]
    SURF --> ING --> CMD
    CMD --> TASK --> AGENT
    CMD --> LIFE
    AGENT --> PACK
    AGENT --> MODEL
    AGENT --> CAP
    LIFE --> MODEL
    LIFE --> EDGE
    TASK --> DATA
    AGENT --> DATA
    LIFE --> DATA
    PACK --> DATA
    DATA --> QUERY --> UI
    UI --> ING
    QUERY --> EVO
    EVO --> CMD
~~~

目标包及其职责：

| 目标包 | 唯一职责 | 主要禁止项 |
|---|---|---|
| natsume_contracts | Host/Edge/Gateway/UI 共享的 identity、ID、error、envelope、event、risk、embodiment 合同 | 禁止依赖 Host、Edge、FastAPI、SQLite 或 provider |
| host_brain/kernel | clock、result、version、causality 等最小纯内核 | 禁止依赖 app/api/composition/adapters |
| host_brain/composition | container、lifecycle、settings、flags、service catalog | 禁止承载业务决策；禁止被业务层反向导入 |
| host_brain/api 与 orchestration | 协议 DTO 和应用用例 | route 不得直连 repository/provider/device |
| host_brain/task_runtime | graph、scheduler、attempt、budget、checkpoint | 禁止领域私有实现和第二次规划 |
| host_brain/agent_runtime | worker、tool port、verification、acceptance、subagent | 禁止 Domain Pack 流程和 app 全局 |
| host_brain/digital_life | Life journal、projection、scene、intent、surface、identity | 禁止 HTTP、SQLite、LLM provider 进入 domain |
| host_brain/persistence 与 eventing | UoW、repository、migration、journal、outbox、projection | 禁止业务代码取得私有连接 |
| host_brain/domain_packs | Pack SDK 与首批领域贡献 | 禁止第二套 TaskGraph/Governor/Review/Model/EventBus |
| host_brain/model_plane、capability_plane、governor | ModelAttempt、Prompt、Broker、Catalog、Authorization | 禁止业务模块绕过统一入口 |
| edge_client | 独立 composition/application/ports/adapters 和物理输出 | 禁止导入 host_brain 实现 |
| host_brain/observability | 版本化 read model、trace、metrics、alerts、replay | 禁止写业务状态 |
| operator_ui | generated client、Operate/Understand/Replay-Lab | 禁止直查业务表或以 UI risk 替代后端授权 |
| host_brain/evolution_lab | benchmark、compiler、feedback、counterfactual、scenario、migration | 禁止直接修改生产能力或身份 |
| host_brain/app | 迁移期 legacy facade | 禁止新增真相；达到 Gate 后删除 |

### 3.1 强制依赖与唯一写模型

1. 依赖方向固定为 Interface/Adapter → Application → Domain/Kernel → Contracts；composition 是唯一可见全部实现的装配层。
2. Edge 只依赖 natsume_contracts 和 Edge 自有层；Domain Pack 只依赖 Pack SDK、Contracts 和批准 ports。
3. Command Plane 负责治理写入，Query Plane 只读 projection；UI 写动作必须重新进入 Secure Ingress。
4. Authorization、Task、Runtime Event、Life、Affect、Homeostasis、Relationship、Memory、Canon、Knowledge、Proactive、Embodiment、Identity、Capability 各自只有一个 writer。
5. Neo4j、Chroma、FTS、Tribe Registry、SSE、EventBus、UI cache 均是 projection/transport/cache，不是真相源。
6. 关键状态变化与 journal/outbox 在同一 UoW 提交；consumer 使用 durable offset、idempotency 和 dead letter。
7. durable payload 禁止 raw thought、secret、完整 cookie 和未授权私密正文。
8. 所有 compatibility facade 必须登记 owner、usage telemetry、introduced version、remove-by version 和 fallback。

---

## 4. 冻结运行语义

Canonical 任务状态：

~~~mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> CLASSIFIED
    CLASSIFIED --> AUTHORIZED
    CLASSIFIED --> REJECTED
    AUTHORIZED --> PLANNED
    PLANNED --> READY
    READY --> RUNNING
    RUNNING --> VERIFYING
    VERIFYING --> AWAITING_REVIEW
    VERIFYING --> COMMITTED
    AWAITING_REVIEW --> COMMITTED
    AWAITING_REVIEW --> REJECTED
    RUNNING --> BLOCKED
    RUNNING --> FAILED
    READY --> CANCELLED
    BLOCKED --> READY
    RECEIVED --> EXPIRED
~~~

- raw execution success 只能完成一次 TaskAttempt；verification 和必需 review 通过后才允许 COMMITTED。
- 重试创建新 attempt；重规划创建新 GraphRevision；历史记录 append-only。
- 同一 action 只能有一个有效 AuthorizationGrant、一个 Task/Attempt lineage 和一个 SideEffectReceipt。
- Surface Context build、preview、retry、cancel 和 replay 均不得推进 Life state。
- Tribe Agent 只能提交 StateContribution；IntentArbiter 只能在 Governor grant 内选择或收紧。
- 主动行为、消息和具身动作全部经 durable outbox、lease、idempotency 和 receipt。

任务交付状态只允许 PROGRAMMED、PLANNED、IN_PROGRESS、BLOCKED、DONE、ROLLED_BACK。M49～M57 当前均为 PROGRAMMED；只有前置 Seal 通过并由 MASTER 解锁后，才能变为 PLANNED/IN_PROGRESS。仅新增类、仅通过单测或仅有 shadow 结果均不能标 DONE。

---

## 5. 阶段 DAG、Gate 与估算

~~~mermaid
flowchart LR
    A["M48-A 长期演化安全地基"] --> C["M49 Contracts / Kernel / Composition"]
    B["M48-B 平台 P0 安全封口"] --> C
    C --> D["M50 Host Application / Task-Agent Runtime"]
    C --> E["M51 Digital Life"]
    D --> E
    C --> F["M52 Memory / Data"]
    E --> F
    D --> G["M53 Domain Packs / Capability"]
    F --> G
    D --> H["M54 Model / Prompt / Governor / Security"]
    G --> H
    C --> I["M55 Edge / Device"]
    H --> I
    D --> J["M56 Query / UI"]
    E --> J
    F --> J
    G --> J
    H --> J
    I --> J
    J --> K["M57 Evolution / Decompat / Release"]
~~~

| 里程碑 | 冻结主题 | TASK 范围 | 蓝图毛估 |
|---|---|---|---:|
| M48-A | 长期演化安全地基 | TASK-0461～0486 | 58～97 日 |
| M48-B | 平台 P0 安全封口 | TASK-0487～0496 | 20～35 日 |
| M49 | Contracts、Kernel、Composition 与依赖治理 | TASK-0497～0510 | 30～50 日 |
| M50 | Host Application 与 Task/Agent Runtime 分层 | TASK-0511～0524 | 40～65 日 |
| M51 | Digital Life Kernel、Surface 与 Identity | TASK-0525～0538 | 45～75 日 |
| M52 | Memory、Knowledge、Event 与 Persistence | TASK-0539～0552 | 40～70 日 |
| M53 | Domain Pack 与 Capability Platform | TASK-0553～0566 | 35～60 日 |
| M54 | Model、Prompt、Governor 与 Security Plane | TASK-0567～0580 | 35～60 日 |
| M55 | Edge、Gateway、Voice、Avatar 与 Device Runtime | TASK-0581～0594 | 40～70 日 |
| M56 | Query/Observability Plane 与 Operator Console | TASK-0595～0608 | 30～55 日 |
| M57 | Evolution Lab、Decompat、Release 与最终封板 | TASK-0609～0622 | 45～75 日 |

逐任务控制点名义相加为 454～787 个有效开发日，其中共享基础设施、阶段 Seal、集成和回滚证据在多行重复计入；目标蓝图校准毛估为 420～660 日，合并跨阶段重叠、复用 M48-A 地基后的程序估算为 300～480 日。任务行与里程碑区间用于容量和风险管理，不应直接相加为承诺。单人约 18～30 个月；3～4 名熟悉仓库的工程师并行仍需约 10～16 个月，包含迁移、soak 和退役窗口。

M48-A 与 M48-B 仅在文件、migration 和切流面不重叠时并行。M49 以后按 DAG 和显式 Dependencies 解锁，不按编号机械并行。

### 5.1 M48-B Entry Gate

M48-B 从 `PROGRAMMED` 解锁为 `PLANNED` 前必须同时满足：

1. `TASK-0461` 已完成并冻结代码/文档基线、dirty-file owner 和架构例外清单。
2. MASTER 已批准 M48-A/M48-B 的文件所有权、migration owner 与切流面矩阵；同一文件或 schema 只有一个主任务 owner。
3. Gateway、Obs、Pentest、Skill/Provider 与 Bridge 的负例测试使用隔离 principal、测试 secret、非生产 channel 和无真实物理副作用环境。
4. M48-B 不新建独立 migration catalog；任何 expand-only schema 变更先登记到 M48-A 已冻结的 catalog/owner 口径。
5. 每个 P0 切片均已登记 feature flag、停止条件、审计证据路径和恢复旧入口的 rollback 步骤。

---

## 6. M48-A 权威任务引用

下表只建立完整程序的编号连续性。TASK-0461～TASK-0486 的详细输入、输出、涉及文件、测试、退出和估算以 M48-A 权威任务卡为准。

| TASK | 引用主题 | Status | 权威定义 |
|---|---|---|---|
| TASK-0461 | 基线、范围与边界固化 | DONE | 2026-07-10；manifest、精确例外与自动化验收已确认 |
| TASK-0462 | Trace 最小化与敏感信息治理 | DONE | 2026-07-10；durable/propagated raw thought 为 0，T-NLE-12 通过 |
| TASK-0463 | Pentest 领域存储 | DONE | 2026-07-10；Domain Store、0050 migration、只读 shadow 与 T-NLE-02 通过 |
| TASK-0464 | Pentest 回填与 domain-only 切换 | DONE | 2026-07-10；87 scanned / 83 eligible、83/83、stable final-delta、receipt gate、domain-only、promotion candidate 与 T-NLE-02 通过；部署目标需独立 receipt |
| TASK-0465 | Canon Apply 单写入口 | DONE | 2026-07-10；signed authority、0053 durable token/journal、target fence/lease/recovery、raw writer 静态闭集与 T-NLE-03 通过 |
| TASK-0466 | Canonical contracts 基础 | DONE | 2026-07-10；v2/v3 adapter、TaskGraph/Event、Verification/Review/Budget 合同通过 |
| TASK-0467 | Durable TaskStore | DONE | 2026-07-10；trusted-clock CAS/lease、terminal lease cleanup、Ed25519 action/outcome reconciliation、cancel-wait、finite strict durable read、强绑定 outbox 与 ordered legacy shadow 通过 |
| TASK-0468 | Planner adapters | DONE | 2026-07-10；same-revision adapters、dependency refs、Child WorkOrder request、shadow-only 与 T-NLE-05 通过 |
| TASK-0469 | Shadow scheduler | DONE | 2026-07-11；owner-confirmed read-only shadow、durable snapshot、依赖/产物、retry/backoff、cancel/recovery、budget 与 legacy diff 通过 |
| TASK-0470 | Verification 与证据 | DONE | 2026-07-11；fail-closed allowlist runner、VerificationResult/evidence refs、required gate、TaskStore VERIFIED 状态门与 T-NLE-08 通过 |
| TASK-0471 | Acceptance judge | DONE | 2026-07-11；AcceptanceJudgement/criterion verdict、Executor acceptance 字段、TaskStore ACCEPTED 状态门与 T-NLE-09 通过 |
| TASK-0472 | Review 与 resume | DONE | 2026-07-11；0055 ReviewStore migration、durable ReviewStore、绑定 resume token、一次性 PRE/POST resume API 与 T-NLE-10 通过；未切 Worker 主链 |
| TASK-0473 | Canonical main cutover | DONE | 2026-07-11；runtime mode router、new-task assignment、canary owner gate、kill switch、legacy drain report、cross-executor resume guard 与 T-NLE-11 通过；生产默认仍为 legacy/0% canary |
| TASK-0474 | Runtime event 与 SSE | DONE | 2026-07-11；0056 runtime_event_timeline、durable cursor/backfill、TaskStore outbox publisher、Tribe/reference adapters、内部 JSON/SSE replay、dedupe key 与敏感字段 fail-closed gate 通过；replay 默认关闭，需 `HOST_RUNTIME_EVENT_REPLAY_ENABLED=true` |
| TASK-0475 | Context refs 与预算 | DONE | 2026-07-11；AgentContextEnvelope v2、context refs 授权、token budget/actual estimate、truncation reason、pollution fail-closed、dispatcher 安全审计摘要与 T-NLE-13 通过 |
| TASK-0476 | Life OCC | DONE | 2026-07-11；0057 OCC migration、expected-version CAS command facade、冲突/显式 fallback audit、20 并发无 silent overwrite 与 T-NLE-14 通过；未迁移生产副本 |
| TASK-0477 | WorldProjection | DONE | 2026-07-11；只读 WorldSnapshot read model、GET snapshot API、source/confidence/TTL/conflict/epistemic facts、空库不初始化 Life state、无写 API 与 T-NLE-15 通过 |
| TASK-0478 | Appraisal 与 Bond | DONE | 2026-07-11；确定性 AppraisalRecord replay、只读 BondProjection、Bond API/验证器统一 command path、rift/repair/boundary/vector 兼容、关系权限不变、T-SSOT-01 扩展与 T-NLE-16 通过 |
| TASK-0479 | Scene / ActionArbiter | DONE | 2026-07-11；source-preserving SceneDecision、ProactiveVoice/Surface 接线、Governor authority binding、route/AIRLOCK gate、shared TokenService handler 前门、dry-run/Runtime Event、explicit goal priority 与 T-NLE-17 通过 |
| TASK-0480 | Capability maturity | DONE | 2026-07-11；实际 registry federated scorecard、canonical risk/L0～L5、只降级、MASTER Review upgrade candidate、Governor restrict-only 与 T-NLE-18 通过 |
| TASK-0481 | Degradation policy | DONE | 2026-07-11；durable DegradationState、transition/recovery evidence、一次性 Review resume、跨高风险入口 fail-closed gates 与 T-NLE-19 通过 |
| TASK-0482 | Release Identity Snapshot | DONE | 2026-07-11；immutable snapshot、字段级 diff、MASTER Review candidate、private-body exclusion、previous-release rollback 与 T-NLE-20 通过 |
| TASK-0483 | Execution UI | DONE | 2026-07-11；versioned operator read model、DAG/attempt/budget/verification/review/resume/cursor UI、96 E2E、deterministic build 与 T-NLE-21 通过 |
| TASK-0484 | Life UI | DONE | 2026-07-11；producer-backed World/Appraisal/Bond/Scene/Arbiter 只读视图、trusted preview、无 Life 写入、默认关闭与 T-NLE-22 通过 |
| TASK-0485 | Capability / Health / Identity UI | DONE | 2026-07-11；versioned governance snapshot、Skills/System Config/Security UI、durable Review locator、无自动升级、identity 正文隔离、Pentest Lab-only fail-closed 与 T-NLE-23 通过 |
| TASK-0486 | M48-A Release Seal | BLOCKED | 2026-07-11；本地验证与 Seal Result 完成，阻塞于 previous identity、live/prod drill 与 MASTER Seal |

---

## 7. PROGRAMMED 任务登记册

每行均为最小可签收任务卡。Dependencies 是硬依赖；Main landing 是主要落点而非唯一允许文件；Exit 是必须自动或以审计证据验证的退出条件。

### 7.1 M48-B：平台 P0 安全封口

本阶段每个任务继承关联：`FR-ARCH-002、FR-ARCH-007～009`；`T-ARCH-01～03`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0487 | Secure Ingress principal/signature/scope/rate/idempotency fail-closed | DONE | M48-B Entry Gate,TASK-0461 | host_brain/secure_ingress；natsume_contracts/identity | 2～4d | 2026-07-11；v1 identity contract、HMAC/opaque token、双层 rate、durable claim+audit、emergency WAL、四 surface policy、0058 与 checksum-bound catalog compatibility 已通过 125 定向 unit/contract、31 既有 migration regression 和 1 次集成波；运行 mode 默认 OFF，实际 route cutover 由 0488/0489/0494 承接 |
| TASK-0488 | Gateway raw-body verification、replay protection 与 durable inbox/outbox | DONE | TASK-0487 | edge_client/gateway；host_brain/app/input_service_gateway.py；host_brain/eventing | 2～4d | 2026-07-11；raw-body 平台验签、0059 durable inbox/outbox/ack/DLQ/fencing/reconciliation、Host receipt、非 HTTP producer 与 fail-closed lifecycle 已通过定向 unit/contract 及 1 次 `2 passed` integration wave；证据见 `evidence/m48b/task0488` |
| TASK-0489 | Obs command relocation 与 Query-only 边界 | IMPLEMENTED_AWAITING_PROTECTED_HANDOFF | TASK-0487 | host_brain/app/obs_routes_core.py；host_brain/observability | 2～3d | 2026-07-11；Obs 18 个基线 mutation route 已撤下，16 个真实 command family 经 SecureIngress typed port 装配，退役 WeChat phantom 稳定拒绝；113 focused unit/contract 通过，隔离 integration 首次发现旧 transport fixture 后定向修正为 1 passed。production mount/lifespan 因 protected `router_registry.py` owner 未 handoff，故不得标 DONE；证据见 `evidence/m48b/task0489` |
| TASK-0490 | Skill/Provider supply-chain quarantine 与 sandbox 封口 | IMPLEMENTED_AWAITING_PROTECTED_HANDOFF | TASK-0487,TASK-0462 | host_brain/skills；host_brain/integrations/providers | 2～4d | 2026-07-11；严格 signed identity/inventory/SBOM/sandbox verifier、content-addressed physical quarantine、0060 durable state 与 STAGED-only intake 已落地，focused unit/security/contract `136 passed, 2 skipped`，integration 首次 `1 passed`，审计 P0/P1 cleared。protected `provider_catalog.py` 的 `HOST_SKILL_EXTRA_PROVIDERS` production dynamic import 及新 signed intake route/SecureIngress durable decision audit 尚待 handoff；不得标 DONE，不声称 production activation/sandbox execution；证据见 `evidence/m48b/task0490` |
| TASK-0491 | Pentest real principal/token、Lab-only isolation 与共享 RunStore | PROGRAMMED | TASK-0487,TASK-0463,TASK-0464 | host_brain/pentest；host_brain/governor | 2～4d | mock principal/自签授权旁路为 0；非 Lab 网络/进程操作稳定拒绝 |
| TASK-0492 | Service Catalog、端口 owner 与 18770 冲突封口 | PROGRAMMED | TASK-0487 | host_brain/service_catalog；host_brain/composition | 2～3d | 每个 endpoint 有唯一 owner/protocol/health；启动前检测端口冲突并 fail closed |
| TASK-0493 | Edge contracts-only 边界与 Host/Edge 循环导入护栏 | PROGRAMMED | TASK-0492 | edge_client；natsume_contracts；tools/architecture | 2～4d | 新增 edge_client→host_brain 实现导入会使 CI 失败；现存边有迁移清单 |
| TASK-0494 | Bridge loopback、token/HMAC 与远程暴露审计 | PROGRAMMED | TASK-0487,TASK-0492,TASK-0493 | edge_client；integrations/mateengine；tools/mateengine_bridge_server.py；desktop_pet | 2～4d | 默认只监听 loopback；远程需显式 scope/rate/auth 并产生审计 |
| TASK-0495 | P0 platform golden paths、provider construction negative matrix 与 fault injection | PROGRAMMED | TASK-0488,TASK-0489,TASK-0490,TASK-0491,TASK-0494 | tests/security；tests/e2e；tests/model_plane | 2～3d | Host/Obs/Gateway/Skill/Pentest/Bridge 正负路径可重复；provider 构造成功或稳定失败，禁止宽泛异常静默降级到伪 fallback |
| TASK-0496 | M48-B Platform Safety Seal | PROGRAMMED | TASK-0495 | docs/30_devspec/evidence/m48b | 1～2d | 所有高风险入口 fail closed；MASTER 批准剩余例外、owner、到期任务和 rollback |

### 7.2 M49：Contracts、Kernel、Composition 与依赖治理

本阶段每个任务继承关联：`FR-ARCH-001～002`；`T-ARCH-04～06`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0497 | Dependency graph / ADR / import guard | PROGRAMMED | TASK-0486,TASK-0496 | tools/architecture；docs/50_project/arch/adr | 2～4d | package/file graph、SCC、批准依赖矩阵可重算；新增违规 import 在 CI 失败 |
| TASK-0498 | natsume_contracts IDs / errors / envelopes | PROGRAMMED | TASK-0497 | natsume_contracts | 3～5d | Host/Edge/Gateway/UI 共用版本化 ID、Principal、Command/Query/Event/Error/Receipt schema |
| TASK-0499 | Kernel clock / result / version / causality | PROGRAMMED | TASK-0498 | host_brain/kernel | 2～4d | clock 可注入；Result/Version/Causality 纯类型 round-trip；无 app/provider 导入 |
| TASK-0500 | Ports / UoW / event interfaces | PROGRAMMED | TASK-0498,TASK-0499 | host_brain/ports；host_brain/persistence；host_brain/eventing | 3～5d | Repository/UoW/Journal/Outbox/Inbox/Projection ports 有合同测试与故障语义 |
| TASK-0501 | AppContainer replace globals | PROGRAMMED | TASK-0497,TASK-0500 | host_brain/composition/container.py；host_brain/app/dependencies.py | 3～5d | 组件显式 provider/scope；legacy getter 仅代理 container；禁止新增可变全局 |
| TASK-0502 | Lifecycle topology replace http_hooks | PROGRAMMED | TASK-0501 | host_brain/composition/lifecycle.py；host_brain/app/http_hooks.py | 3～5d | 拓扑启动、readiness、degradation、逆序关闭、部分失败回滚和无 orphan task 通过 |
| TASK-0503 | Unified settings / config / deprecation | PROGRAMMED | TASK-0501 | host_brain/composition/settings.py；host_brain/config | 2～4d | 配置一次类型化解析；canonical key 唯一；alias 命中/冲突/弃用可审计 |
| TASK-0504 | Feature flags / config snapshot / service profiles | PROGRAMMED | TASK-0503 | host_brain/composition/feature_flags.py；host_brain/composition/profiles.py | 2～4d | flag 快照可回放；dev/staging/prod profile 差异显式；未知 flag fail closed |
| TASK-0505 | Command / query / event buses | PROGRAMMED | TASK-0498,TASK-0500,TASK-0501 | host_brain/application/bus；host_brain/eventing | 3～5d | Command 只写、Query 只读、Event 持久投递；身份/因果/错误语义一致 |
| TASK-0506 | API v2 errors / reasons | PROGRAMMED | TASK-0498,TASK-0505 | host_brain/api/http/v2/errors.py | 2～3d | canonical error 到 HTTP/SSE 映射唯一，secret/internal exception 不泄漏 |
| TASK-0507 | OpenAPI registry / versioning | PROGRAMMED | TASK-0498,TASK-0506 | host_brain/api/openapi；natsume_contracts | 2～4d | schema/protocol negotiation、snapshot/diff 和重复 method/path 检测通过 |
| TASK-0508 | Legacy facade registry / telemetry / removal | PROGRAMMED | TASK-0497,TASK-0504 | host_brain/composition/legacy.py；host_brain/app | 2～4d | 每个 facade 有 owner、usage、introduced/remove-by、fallback 和 TASK-0620 映射 |
| TASK-0509 | Codemod / package move tools | PROGRAMMED | TASK-0497,TASK-0508 | tools/refactor；tools/architecture | 2～4d | dry-run、幂等、import rewrite、rollback patch 和禁止边检测在样本包通过 |
| TASK-0510 | M49 Contracts / Kernel / Composition Seal | PROGRAMMED | TASK-0502,TASK-0504,TASK-0505,TASK-0507,TASK-0508,TASK-0509 | tests/contracts；tests/composition；docs/30_devspec/evidence/m49 | 2～3d | 合同兼容、依赖方向、UoW/Event ports、container/lifecycle 和 rollback 全绿并获 MASTER 批准 |

M49 Gate：natsume_contracts 与 kernel 不依赖实现层；composition 是唯一装配根；所有新跨层依赖受 import guard 约束；legacy facade 全部进入可删除登记册。

### 7.3 M50：Host Application 与 Task/Agent Runtime 分层

本阶段每个任务继承关联：`FR-ARCH-003`；`T-ARCH-07～09`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0511 | ConversationService | PROGRAMMED | TASK-0510,TASK-0473 | host_brain/orchestration/conversation | 3～5d | chat/host/scheduler 请求进入同一用例服务；服务仅依赖 contracts/ports，黄金响应等价 |
| TASK-0512 | IngressService | PROGRAMMED | TASK-0487,TASK-0511 | host_brain/orchestration/ingress；host_brain/api | 3～5d | chat、host、ASR、gateway 输入统一 principal/correlation/idempotency；重复输入不重复建单 |
| TASK-0513 | Executive / WorkService | PROGRAMMED | TASK-0505,TASK-0511,TASK-0467,TASK-0468 | host_brain/orchestration/work；host_brain/task_runtime | 4～6d | plan/authorize/execute/verify/review 引用同一 graph revision，禁止 Executive 二次规划 |
| TASK-0514 | Surface port split | PROGRAMMED | TASK-0511,TASK-0497 | host_brain/orchestration/ports/surface.py；host_brain/app/surface_main_chain_adapter.py | 3～5d | persona/context/render/delivery 分为独立 ports；surface adapter 不再承载任务或领域写入 |
| TASK-0515 | Unified Agent taxonomy / registries | PROGRAMMED | TASK-0498,TASK-0513,TASK-0480 | host_brain/agent_runtime/registry.py；host_brain/capability_plane | 3～5d | Agent ID、alias、capability、availability、risk 使用单一 registry，重复与 alias 环失败 |
| TASK-0516 | Task worker application / domain / infrastructure | PROGRAMMED | TASK-0500,TASK-0513,TASK-0515,TASK-0467 | host_brain/task_runtime；host_brain/agent_runtime | 4～6d | Node/Attempt/Lease/Heartbeat/Retry/Checkpoint 分层；重启按持久 owner 恢复 |
| TASK-0517 | Unify schedulers | PROGRAMMED | TASK-0516,TASK-0469 | host_brain/task_runtime/scheduler；host_brain/scheduler | 3～5d | NatsumeScheduler/HostTaskScheduler 经单一 contract 归并，只有一个 authoritative job source |
| TASK-0518 | Artifact / Evidence | PROGRAMMED | TASK-0516,TASK-0470,TASK-0471 | host_brain/agent_runtime/artifacts；host_brain/agent_runtime/evidence | 3～5d | 输出、日志、verification、receipt 使用 digest/ref；证据可校验且敏感数据脱敏 |
| TASK-0519 | SubAgent lifecycle / messages | PROGRAMMED | TASK-0515,TASK-0516,TASK-0475 | host_brain/agent_runtime/subagents | 3～5d | child 继承 lineage/grant/budget/context；start/cancel/timeout/fail/resume 与消息顺序可重放 |
| TASK-0520 | Single Tool execution service | PROGRAMMED | TASK-0516,TASK-0518,TASK-0490 | host_brain/agent_runtime/tools；host_brain/capability_plane/broker.py | 4～6d | Skill/Tool/MCP/Agent 调用只经一个执行服务并绑定 grant/idempotency/attempt/evidence |
| TASK-0521 | Host Worker / background lifecycle | PROGRAMMED | TASK-0502,TASK-0517,TASK-0519,TASK-0520 | host_brain/workers；host_brain/composition | 3～5d | worker 启停、drain、backpressure、crash recovery 和 shutdown 无孤儿任务或重复副作用 |
| TASK-0522 | Thin routes / DTO | PROGRAMMED | TASK-0506,TASK-0507,TASK-0512,TASK-0513,TASK-0514 | host_brain/api/http/v2；host_brain/app/routes | 3～5d | route 只认证、映射 DTO、调用 use case；直接 repository/provider/global 调用为 0 |
| TASK-0523 | App facade migration + giant decomposition | PROGRAMMED | TASK-0508,TASK-0509,TASK-0521,TASK-0522 | host_brain/app；host_brain/orchestration；host_brain/agent_runtime | 5～8d | 主调用点切新实现；巨石按职责拆分；legacy hit 可量化且 flag rollback 演练通过 |
| TASK-0524 | M50 Host Runtime Seal | PROGRAMMED | TASK-0523 | tests/orchestration；tests/task_runtime；tests/agent_runtime；docs/30_devspec/evidence/m50 | 2～4d | 单一任务执行主链、same revision、restart/retry 无重复副作用并获 MASTER 批准 |

M50 Gate：Secure Ingress → Application → Task/Agent Runtime 是唯一新请求链；旧任务只能由旧执行器 drain；verification 未通过不能 committed；app 只保留有 telemetry 的迁移 facade。

### 7.4 M51：Digital Life Kernel、Surface 与 Identity

本阶段每个任务继承关联：`FR-ARCH-004`；`T-ARCH-10～12`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0525 | LifeEvent / Identity / StateContribution contracts | PROGRAMMED | TASK-0498,TASK-0499,TASK-0524 | host_brain/digital_life/contracts；natsume_contracts | 3～5d | 事件、主体、贡献、visibility/evidence/version schema 稳定，用户观察与 Natsume 状态类型分离 |
| TASK-0526 | MemoryEvent → LifeEventJournal | PROGRAMMED | TASK-0525,TASK-0474 | host_brain/digital_life/infrastructure/journal.py；host_brain/memory | 4～6d | 历史 MemoryEvent 可回填；新 LifeEvent 单写 journal；sequence/correlation/replay 测试通过 |
| TASK-0527 | Projection repository / OCC / outbox | PROGRAMMED | TASK-0500,TASK-0526,TASK-0476 | host_brain/digital_life/infrastructure；host_brain/persistence | 4～6d | reducer version、expected_version、projection/outbox 同事务；并发冲突不丢更新 |
| TASK-0528 | Single life coordinator / clock | PROGRAMMED | TASK-0499,TASK-0527 | host_brain/digital_life/kernel/coordinator.py | 4～6d | 输入、时间、工作、device receipt 只由一个 coordinator/clock 推进；回放 deterministic |
| TASK-0529 | Retire parallel DigitalLifeSupervisor authority | PROGRAMMED | TASK-0528,TASK-0508 | host_brain/digital_life；host_brain/app | 3～5d | Supervisor 只保留无写权限 facade/harness；双核 shadow 差异归零，旧 writer 停写并登记 TASK-0620 物理删除 |
| TASK-0530 | Organs / Tribe contributions / no double progression | PROGRAMMED | TASK-0525,TASK-0528,TASK-0529 | host_brain/digital_life/drives；host_brain/tribe | 3～6d | organ/Tribe 只能提交 StateContribution；同 tick/事件不会推进两次状态 |
| TASK-0531 | Split inner state / narrative / private | PROGRAMMED | TASK-0525,TASK-0527 | host_brain/digital_life/inner | 3～5d | inner state、private narrative、surface summary 分 store/retention/redaction；私密正文不进通用 trace |
| TASK-0532 | Appraisal / Affect / UserObservation | PROGRAMMED | TASK-0527,TASK-0528,TASK-0478 | host_brain/digital_life/appraisal；host_brain/digital_life/affect | 4～6d | 用户情绪观察与 Natsume Affect 不同 writer；reducers 可重放并输出 evidence/confidence |
| TASK-0533 | Homeostasis / Drives | PROGRAMMED | TASK-0527,TASK-0530,TASK-0532 | host_brain/digital_life/drives | 3～5d | drive 演化仅消费 journal/contribution；preview/retry 不变更数值，边界值回归通过 |
| TASK-0534 | Relationship event / projection / Bond graph | PROGRAMMED | TASK-0527,TASK-0478 | host_brain/digital_life/relationship；host_brain/tribe | 4～7d | trust/safety/dependency/rift/repair/boundary 完整持久；Neo4j/Bond 仅为可重建投影 |
| TASK-0535 | World / Scene / Intent | PROGRAMMED | TASK-0528,TASK-0532,TASK-0533,TASK-0534,TASK-0477,TASK-0479 | host_brain/digital_life/world；scene；intent | 4～7d | WorldProjection→SceneResolver→IntentArbiter 可重放；Arbiter 不覆盖用户目标或提权 |
| TASK-0536 | Proactive + embodiment gateway | PROGRAMMED | TASK-0505,TASK-0535 | host_brain/digital_life/proactive；host_brain/digital_life/embodiment | 4～6d | 主动行为和具身命令全部写 durable outbox，lease/idempotency/receipt/DLQ 完整 |
| TASK-0537 | Surface read-only + identity overlay | PROGRAMMED | TASK-0514,TASK-0531,TASK-0532,TASK-0534,TASK-0535,TASK-0482 | host_brain/digital_life/surface；host_brain/digital_life/identity | 4～7d | Surface build 零写入；identity=immutable baseline+versioned overlay，激活/回滚有 snapshot/diff |
| TASK-0538 | M51 shadow / seal / disable old writers | PROGRAMMED | TASK-0529,TASK-0530,TASK-0533,TASK-0534,TASK-0536,TASK-0537 | tests/digital_life；docs/30_devspec/evidence/m51 | 3～5d | 单 clock/单 writer、replay、OCC、纯读 Surface、关系/身份回滚通过；旧 writer usage=0、保持不可写，并进入 TASK-0620 统一物理退役清单 |

M51 Gate：LifeEventJournal 和 versioned reducers 是生命事实；用户观察、Natsume Affect、Relationship、Inner Narrative、Identity 各自边界清晰；所有 projection 可从 journal 重建。

### 7.5 M52：Memory、Knowledge、Event 与 Persistence

本阶段每个任务继承关联：`FR-ARCH-005`；`T-ARCH-13～15`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0539 | Repository / UoW / no private conn | PROGRAMMED | TASK-0500,TASK-0538 | host_brain/persistence/uow.py；host_brain/persistence/repositories | 3～5d | 事务、WAL、timeout 由 UoW 管理；业务模块访问 Ledger._conn 的生产调用为 0 |
| TASK-0540 | Storage catalog / migrations | PROGRAMMED | TASK-0539 | host_brain/persistence/migrations；host_brain/migrations | 3～6d | 重复/缺口修复；空库、旧库、连续升级和 rollback catalog 测试通过 |
| TASK-0541 | EventJournal / outbox / inbox / projections | PROGRAMMED | TASK-0539,TASK-0540,TASK-0526 | host_brain/eventing | 4～7d | 状态+journal/outbox 原子；durable offset、幂等消费、DLQ、重放和投影重建通过 |
| TASK-0542 | Episodic single implementation | PROGRAMMED | TASK-0539,TASK-0541 | host_brain/memory/episodic；host_brain/digital_life/memory | 3～5d | 多套 EpisodicStore 归并为单写服务；旧实现只读对账后 usage=0 |
| TASK-0543 | Semantic / autobiographical | PROGRAMMED | TASK-0542 | host_brain/memory/semantic；host_brain/memory/autobiographical | 3～5d | 类型、来源、时间、主体、证据、版本分槽；派生记忆可追溯到原事件 |
| TASK-0544 | LifeMemoryService | PROGRAMMED | TASK-0542,TASK-0543,TASK-0538 | host_brain/digital_life/memory/service.py | 4～6d | episodic/semantic/distillation/correction/forgetting/recall 只经一个写服务 |
| TASK-0545 | Canon proposal / apply / domain isolation | PROGRAMMED | TASK-0465,TASK-0539,TASK-0544 | host_brain/memory/canon；host_brain/canon | 4～6d | Apply 校验 subject/version/expiry/once/approved hash；各 domain 只能 proposal，不能直写 |
| TASK-0546 | Knowledge store / provenance / namespaces | PROGRAMMED | TASK-0539,TASK-0545 | host_brain/knowledge；host_brain/domain_packs | 3～6d | Core/Persona/Pentest/BioOps/Personal namespaces 隔离，promotion 保留证据/审批/撤销 |
| TASK-0547 | Retrieval contracts | PROGRAMMED | TASK-0498,TASK-0544,TASK-0546 | host_brain/memory/retrieval；natsume_contracts | 3～5d | 查询携带 principal/scope/namespace/budget/freshness；越域检索稳定拒绝 |
| TASK-0548 | Recall fusion | PROGRAMMED | TASK-0543,TASK-0547 | host_brain/memory/recall | 3～5d | episodic/semantic/canon/knowledge 融合保留来源、score、visibility、去重与截断证据 |
| TASK-0549 | Distillation / proposals | PROGRAMMED | TASK-0544,TASK-0548 | host_brain/memory/distillation | 3～5d | distillation 只产 proposal；无 evidence/审批不能晋升 semantic/canon/identity |
| TASK-0550 | Retention / GC / privacy / delete / conflict | PROGRAMMED | TASK-0544,TASK-0545,TASK-0546 | host_brain/memory/governance | 3～6d | retention、legal/privacy delete、GC、undo window 和冲突解决均可审计回放 |
| TASK-0551 | Export / import / backup / backfill / performance | PROGRAMMED | TASK-0540,TASK-0541,TASK-0544,TASK-0550 | host_brain/persistence/backup；tools/migrations | 4～7d | export/import hash 对账；backup restore、历史 backfill、容量与 p95 基线通过 |
| TASK-0552 | M52 Seal：no inline DDL / private conn | PROGRAMMED | TASK-0541,TASK-0544,TASK-0545,TASK-0548,TASK-0549,TASK-0550,TASK-0551 | tests/persistence；tests/memory；docs/30_devspec/evidence/m52 | 2～4d | 唯一 Memory/Canon 写入口、projection 可重建、production inline DDL 和 private conn 为 0 |

M52 Gate：长期数据只由批准 writer 写入；SQLite schema 只经 migration catalog；Neo4j、Chroma、FTS 由 outbox 投影并可重建；普通聊天不能检索 Pentest namespace。

### 7.6 M53：Domain Pack 与 Capability Platform

本阶段每个任务继承关联：`FR-ARCH-006`；`T-ARCH-16～18`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0553 | Pack SDK manifest | PROGRAMMED | TASK-0524,TASK-0552 | host_brain/domain_packs/sdk；natsume_contracts/capability | 2～4d | manifest 统一 commands/capabilities/tools/state/knowledge/verifier/risk/benchmark/UI contribution |
| TASK-0554 | Registry / loading / versioning | PROGRAMMED | TASK-0553,TASK-0490 | host_brain/domain_packs/registry.py | 3～5d | discovery、signature、dependency、staged activation、disable、upgrade、rollback 可测试 |
| TASK-0555 | Federated capability catalog | PROGRAMMED | TASK-0553,TASK-0554,TASK-0480 | host_brain/capability_plane/catalog | 3～5d | Skill/Tool/MCP/Agent/Pack 联邦呈现，owner/version/risk/health/maturity 可查询 |
| TASK-0556 | Unify Skill / Tool registries | PROGRAMMED | TASK-0520,TASK-0555 | host_brain/capability_plane；host_brain/skills | 3～5d | 旧 registry 经 adapter 进入单一 catalog/broker；重复能力与风险映射冲突失败 |
| TASK-0557 | Code Pack | PROGRAMMED | TASK-0554,TASK-0556 | host_brain/domain_packs/code | 2～4d | Code 能力独立启停/授权/验证/回滚，不读取 Host global 或私有 domain store |
| TASK-0558 | Research / Browser Pack | PROGRAMMED | TASK-0554,TASK-0556 | host_brain/domain_packs/research | 2～4d | browser/research 命令、evidence、network policy、acceptance 和 benchmark 经 Pack SDK |
| TASK-0559 | Media / Translation / Lightnovel Pack | PROGRAMMED | TASK-0554,TASK-0556 | host_brain/domain_packs/media | 3～5d | production_executor 中相关分支迁入 Pack；知识 namespace 与 model profile 显式 |
| TASK-0560 | Personal Knowledge / Obsidian Pack | PROGRAMMED | TASK-0546,TASK-0554,TASK-0556 | host_brain/domain_packs/personal_knowledge | 3～5d | vault scope/provenance/索引/写权限隔离；未授权正文不进入通用 prompt |
| TASK-0561 | Desktop Automation Pack | PROGRAMMED | TASK-0554,TASK-0556 | host_brain/domain_packs/desktop | 3～5d | 桌面副作用经 WorkOrder/Tool broker/receipt；scope、undo、timeout 和验收完整 |
| TASK-0562 | Hardware / Embodiment Pack | PROGRAMMED | TASK-0536,TASK-0554,TASK-0556 | host_brain/domain_packs/hardware | 3～5d | Host 只产生 EmbodimentCommand；Pack 不持有 Edge 物理输出 authority |
| TASK-0563 | Pentest Pack shared WorkOrder / RunStore | PROGRAMMED | TASK-0491,TASK-0546,TASK-0554,TASK-0556 | host_brain/domain_packs/pentest；host_brain/pentest | 3～6d | Pentest 使用共享 WorkOrder/RunStore/Governor；Lab 隔离和 Canon proposal 边界通过 |
| TASK-0564 | BioOps Pack | PROGRAMMED | TASK-0546,TASK-0554,TASK-0556 | host_brain/domain_packs/bio_ops | 2～4d | BioOps namespace/risk/verifier/approval 独立；高风险动作 fail closed |
| TASK-0565 | MCP / External Providers Pack | PROGRAMMED | TASK-0490,TASK-0554,TASK-0556 | host_brain/domain_packs/mcp | 3～5d | 外部 provider 具 identity/scope/secret isolation/health/rate/audit，断连可降级 |
| TASK-0566 | Pack benchmark / center / M53 Seal | PROGRAMMED | TASK-0557,TASK-0558,TASK-0559,TASK-0560,TASK-0561,TASK-0562,TASK-0563,TASK-0564,TASK-0565 | tests/domain_packs；docs/30_devspec/evidence/m53 | 3～5d | 各 Pack 独立启停/授权/升级/回滚；无平台级第二内核，benchmark 与 usage 全部可见 |

M53 Gate：Pack 只能贡献 manifest 允许的能力；不能拥有 TaskGraph、Scheduler、Governor、Review、Model Gateway、EventBus、Identity 或核心 writer；所有首批 Pack 均有隔离和回滚证据。

### 7.7 M54：Model、Prompt、Governor 与 Security Plane

本阶段每个任务继承关联：`FR-ARCH-007`；`T-ARCH-19～21`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0567 | ModelGateway consolidation | PROGRAMMED | TASK-0510,TASK-0566 | host_brain/model_plane/service.py；host_brain/integrations/llm_gateway.py；llm_helpers.py | 3～5d | 所有 complete/stream 入口产生同一 ModelRequest→ProviderAttempt→StructuredResult，旧 helper 仅代理 |
| TASK-0568 | Routing / capability / cost / latency | PROGRAMMED | TASK-0567,TASK-0555 | host_brain/model_plane/routing；host_brain/model_plane/usage | 3～5d | route decision 同时考虑 capability/health/cost/latency/budget；选择和 fallback 可重放 |
| TASK-0569 | Prompt registry / versioning | PROGRAMMED | TASK-0498,TASK-0567 | host_brain/model_plane/prompts | 2～4d | template 有 version/owner/input/slots/output schema；Persona/Task/Pack 不直接拼私有 prompt |
| TASK-0570 | Structured output / repair | PROGRAMMED | TASK-0567,TASK-0569 | host_brain/model_plane/structured_output.py；host_brain/core/qa.py | 2～4d | schema validate、一次受控 repair、拒绝/降级 reason 稳定；非法输出不进入执行 |
| TASK-0571 | Context / token budget | PROGRAMMED | TASK-0475,TASK-0568,TASK-0569 | host_brain/model_plane/context.py；host_brain/agent_runtime/context.py | 3～5d | context 以 refs/slot/visibility/budget 组装；截断、缓存和实际 usage 可审计 |
| TASK-0572 | Single capability / tool broker | PROGRAMMED | TASK-0520,TASK-0556,TASK-0567 | host_brain/capability_plane/broker.py | 3～5d | Skill/Tool/MCP/Agent/Pack 执行全部经唯一 broker；绕过调用由 import/callsite gate 拦截 |
| TASK-0573 | Unified risk taxonomy | PROGRAMMED | TASK-0498,TASK-0555 | host_brain/governor/risk；natsume_contracts/risk | 2～4d | 各来源风险可无损映射并保留原尺度/证据；maturity 只能收紧不能提权 |
| TASK-0574 | Full Governor Grant propagation / remove lite override | PROGRAMMED | TASK-0511,TASK-0513,TASK-0573 | host_brain/governor/authorization；host_brain/app/chat_handler.py；agent_runtime_shared.py | 3～6d | full grant 贯穿 Command/Graph/Attempt/Tool/Receipt；chat-lite 不能签发或覆盖授权 |
| TASK-0575 | Persistent token / review / manifest / undo | PROGRAMMED | TASK-0472,TASK-0500,TASK-0574 | host_brain/governor/token；review；manifest；host_brain/persistence | 3～6d | issue/consume/expire/review/undo 原子持久；重启和并发不能双消费，confirmed 布尔值不能授权 |
| TASK-0576 | Airlock / sandbox / secrets | PROGRAMMED | TASK-0490,TASK-0572,TASK-0575 | host_brain/airlock；host_brain/sandbox；host_brain/composition/secrets.py | 3～5d | ingress/egress 净化、workspace/network/process scope、secret redaction 和故障拒绝通过 |
| TASK-0577 | Security immune providers | PROGRAMMED | TASK-0567,TASK-0576 | host_brain/security/immune；host_brain/model_plane/providers | 2～5d | provider/bundle 健康、签名、异常行为、熔断、quarantine 和恢复有统一 immune signal |
| TASK-0578 | Audit / trace linkage | PROGRAMMED | TASK-0568,TASK-0572,TASK-0574,TASK-0575,TASK-0577 | host_brain/observability/trace；host_brain/audit | 2～4d | request→grant→graph→attempt→model/tool→verification→receipt 可按 correlation 完整串联 |
| TASK-0579 | Policy simulation / shadow | PROGRAMMED | TASK-0573,TASK-0574,TASK-0578 | host_brain/governor/simulation | 3～5d | policy 新版本只读 shadow 比较 decision/reason/impact；禁止执行真实副作用或自动提权 |
| TASK-0580 | M54 Model / Governance / Security Seal | PROGRAMMED | TASK-0570,TASK-0571,TASK-0572,TASK-0575,TASK-0576,TASK-0577,TASK-0579 | tests/model_plane；tests/governor；tests/security；docs/30_devspec/evidence/m54 | 2～4d | 单一 ModelAttempt/ToolBroker/Authorization，token/review 持久，供应链与 shadow Gate 获 MASTER 批准 |

M54 Gate：业务代码不再直接调用私有 LLM helper 或工具执行器；Governor 是唯一授权 writer；Skill/Provider bundle 具 digest/signature/SBOM/quarantine/sandbox/staged activation/rollback。

### 7.8 M55：Edge、Gateway、Voice、Avatar 与 Device Runtime

本阶段每个任务继承关联：`FR-ARCH-008`；`T-ARCH-22～24`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0581 | Edge zero Host imports | PROGRAMMED | TASK-0493,TASK-0510,TASK-0580 | edge_client；natsume_contracts；tools/architecture | 3～6d | edge_client 对 host_brain.* 实现导入为 0；独立环境安装/import/test 通过 |
| TASK-0582 | Edge composition / settings | PROGRAMMED | TASK-0503,TASK-0504,TASK-0581 | edge_client/composition | 3～5d | Edge 自有 container/settings/flags/lifecycle；不读取 Host globals 或配置真源 |
| TASK-0583 | RPC auth / version / handshake | PROGRAMMED | TASK-0498,TASK-0582 | edge_client/contracts；edge_client/application/rpc | 3～5d | principal、token/HMAC、protocol/capability version、health negotiation；不兼容 fail closed/degraded |
| TASK-0584 | Edge outbox / audit / no Host SQLite | PROGRAMMED | TASK-0500,TASK-0582,TASK-0583 | edge_client/infrastructure；edge_client/observability | 3～6d | Edge 使用自有 inbox/outbox/audit adapter；Host SQLite/audit 实现导入与直连为 0 |
| TASK-0585 | Split voice pipeline | PROGRAMMED | TASK-0582,TASK-0583,TASK-0584 | edge_client/voice/pipeline | 3～5d | capture/VAD/ASR/dialog/TTS/playback 分 ports，取消/超时/backpressure/receipt 可追踪 |
| TASK-0586 | ASR port | PROGRAMMED | TASK-0585 | edge_client/voice/asr | 2～4d | local/cloud ASR 可替换；partial/final/重连/隐私/usage 语义一致 |
| TASK-0587 | TTS / speech port | PROGRAMMED | TASK-0585 | edge_client/voice/tts | 2～4d | TTS/provider/播放适配解耦；voice profile、缓存、失败降级和 receipt 测试通过 |
| TASK-0588 | Resource / GPU gate | PROGRAMMED | TASK-0582,TASK-0586,TASK-0587 | edge_client/application/resources.py | 2～4d | CPU/GPU/VRAM/温度/并发预算进入 admission；过载明确降级而非争抢崩溃 |
| TASK-0589 | VTS / avatar compiler | PROGRAMMED | TASK-0583,TASK-0584,TASK-0588 | edge_client/embodiment/vts；edge_client/avatar | 3～6d | EmbodimentCommand 编译到版本化 VTS/avatar actions，idempotency/receipt/rollback 完整 |
| TASK-0590 | MateEngine / VRM | PROGRAMMED | TASK-0583,TASK-0584,TASK-0588 | edge_client/embodiment/mate_engine；edge_client/embodiment/vrm | 3～6d | .NET/Unity bridge 协议、身份、sequence、lease、ack 和安全降级通过 |
| TASK-0591 | DesktopPet protocol / security | PROGRAMMED | TASK-0494,TASK-0583,TASK-0584 | edge_client/embodiment/desktop_pet；desktop_pet | 3～5d | loopback 默认、token/HMAC、scope/rate/replay/audit 完整；远程暴露显式审批 |
| TASK-0592 | Gateway / channel SDK + durable transport | PROGRAMMED | TASK-0488,TASK-0583,TASK-0584 | edge_client/gateway；host_brain/gateway/sdk | 4～6d | channel adapter 共用 SDK；raw verification、durable inbox/outbox、ack/retry/DLQ 可回放 |
| TASK-0593 | Device registry / session / reconnect / degradation / sole authority | PROGRAMMED | TASK-0536,TASK-0589,TASK-0590,TASK-0591,TASK-0592 | edge_client/devices；edge_client/application/device_runtime.py | 4～7d | device identity/session/capability/lease/reconnect 统一；物理输出只有 Edge Runtime authority |
| TASK-0594 | M55 Edge / Device Seal | PROGRAMMED | TASK-0581,TASK-0584,TASK-0586,TASK-0587,TASK-0588,TASK-0593 | tests/edge；tests/gateway；docs/30_devspec/evidence/m55 | 3～5d | Edge 独立安装/升级/回滚，Host 导入为 0，协议/断连/设备/物理输出 Gate 获 MASTER 批准 |

M55 Gate：Edge 可在没有 Host 包的干净环境安装和运行合同测试；所有 Bridge 默认 loopback+auth；Host 只发送 EmbodimentCommand，adapter 必须回传 receipt。

### 7.9 M56：Query/Observability Plane 与 Operator Console

本阶段每个任务继承关联：`FR-ARCH-009`；`T-ARCH-25～27`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0595 | Query / read contracts | PROGRAMMED | TASK-0507,TASK-0594 | natsume_contracts/queries；host_brain/observability/read_models | 2～4d | read model 强制 schema_version/source_cursor/freshness/degraded_state/visibility，Query 无写接口 |
| TASK-0596 | Projection workers | PROGRAMMED | TASK-0541,TASK-0595 | host_brain/observability/projections；host_brain/workers | 3～5d | workers 使用 durable offset/idempotency/DLQ；空库可从 journal 全量重建并对账 |
| TASK-0597 | Trace / replay | PROGRAMMED | TASK-0578,TASK-0596 | host_brain/observability/trace；host_brain/observability/replay | 3～5d | correlation/causation/evidence 串联；replay 默认无副作用并标明 schema/policy/model version |
| TASK-0598 | Metrics / SLO / alerts | PROGRAMMED | TASK-0596,TASK-0597 | host_brain/observability/metrics；alerts | 2～4d | Host/Task/Life/Memory/Pack/Model/Edge 指标、SLO burn、告警去重和 degraded reason 可验证 |
| TASK-0599 | Task / review console | PROGRAMMED | TASK-0524,TASK-0595 | operator_ui/features/tasks；operator_ui/features/review | 3～5d | TaskGraph/Attempt/Evidence/Review/Resume 使用 typed read model；操作经 Secure Ingress |
| TASK-0600 | Life cockpit | PROGRAMMED | TASK-0538,TASK-0595 | operator_ui/features/life | 3～5d | World/Affect/Homeostasis/Relationship/Scene/Identity 显示 freshness/version，私密字段按 role 隐藏 |
| TASK-0601 | Memory / knowledge console | PROGRAMMED | TASK-0552,TASK-0595 | operator_ui/features/memory；operator_ui/features/knowledge | 3～5d | provenance/namespace/retention/proposal/recall 可理解；UI 不直查 store 或显示未授权正文 |
| TASK-0602 | Domain / capability center | PROGRAMMED | TASK-0566,TASK-0595 | operator_ui/features/domain_packs；operator_ui/features/capabilities | 3～5d | Pack/Capability health/version/risk/usage/activation/rollback 可见；命令由后端授权 |
| TASK-0603 | Policy / security lab | PROGRAMMED | TASK-0580,TASK-0595,TASK-0597 | operator_ui/features/policy；operator_ui/features/security | 3～5d | policy shadow、grant/token/audit/quarantine 可查；UI 标签不构成授权且无直接 policy 写表 |
| TASK-0604 | Device console | PROGRAMMED | TASK-0594,TASK-0595 | operator_ui/features/devices | 3～5d | device/session/capability/lease/reconnect/degraded/receipt 可见；控制命令经 Secure Ingress |
| TASK-0605 | OpenAPI TypeScript client | PROGRAMMED | TASK-0507,TASK-0595 | operator_ui/generated_api；tools/openapi | 2～4d | client 从冻结 OpenAPI 可重复生成；手写重复 DTO 为 0，schema drift 使 CI 失败 |
| TASK-0606 | Frontend TypeScript / design / a11y | PROGRAMMED | TASK-0605 | operator_ui/app；operator_ui/features；operator_ui/shared | 3～6d | 活动源码 TypeScript 化；design tokens、keyboard、focus、contrast、screen-reader Gate 通过 |
| TASK-0607 | Frontend tests / performance / error / static assets | PROGRAMMED | TASK-0598,TASK-0599,TASK-0600,TASK-0601,TASK-0602,TASK-0603,TASK-0604,TASK-0606 | operator_ui/tests；operator_ui/build | 3～6d | unit/component/E2E/权限/degraded/perf/error boundary 全绿；hashed build asset 不混入源码 diff |
| TASK-0608 | M56 Seal：Obs read-only | PROGRAMMED | TASK-0596,TASK-0597,TASK-0598,TASK-0607 | tests/observability；operator_ui/tests；docs/30_devspec/evidence/m56 | 2～4d | Obs 写 handler、Query 绕写、UI 私密泄漏为 0；typed client 与三大 Console 面获 MASTER 批准 |

M56 Gate：Obs 只承载 Query/Projection/Trace/Metrics/Replay；Console 分为 Operate、Understand、Replay/Lab；route visibility 由后端 role/capability read model 决定。

### 7.10 M57：Evolution Lab、Decompat、Release 与最终封板

本阶段每个任务继承关联：`FR-ARCH-010`；`T-ARCH-28～30`。

| TASK | 冻结工作项 | Status | Dependencies | Main landing | Estimate | Verifiable exit |
|---|---|---|---|---|---:|---|
| TASK-0609 | Experiment / workspace isolation | PROGRAMMED | TASK-0576,TASK-0608 | host_brain/evolution_lab/workspaces | 3～5d | experiment 使用独立 workspace/data/network/resource/secret scope，默认不能写生产状态 |
| TASK-0610 | CapabilityForge integration | PROGRAMMED | TASK-0555,TASK-0566,TASK-0609 | host_brain/evolution_lab/capability_forge | 3～5d | Forge 只消费 catalog/benchmark 并产受治理 proposal；不能直接激活 capability |
| TASK-0611 | Skill compiler intake | PROGRAMMED | TASK-0566,TASK-0580,TASK-0610 | host_brain/evolution_lab/skill_compiler/intake.py | 3～5d | intake 固化来源、许可、需求、风险、evidence、digest；不可信输入进入 quarantine |
| TASK-0612 | Skill draft / codegen sandbox | PROGRAMMED | TASK-0576,TASK-0611 | host_brain/evolution_lab/skill_compiler/sandbox.py | 4～7d | draft/codegen 仅在隔离环境运行，禁止热导入 Host；产物含 SBOM、测试和签名候选 |
| TASK-0613 | Benchmark / evaluation | PROGRAMMED | TASK-0609,TASK-0612 | host_brain/evolution_lab/benchmarks；evaluation | 4～7d | golden/scenario/safety/cost/latency/regression 指标可重复，结果绑定 bundle/model/prompt 版本 |
| TASK-0614 | Canary / promotion / rollback | PROGRAMMED | TASK-0504,TASK-0579,TASK-0613 | host_brain/evolution_lab/promotion | 4～7d | proposal→review→shadow→canary→activate→rollback 状态持久；自动提权/自动全量激活禁止 |
| TASK-0615 | Feedback normalization / proposals | PROGRAMMED | TASK-0549,TASK-0609,TASK-0613 | host_brain/evolution_lab/feedback | 3～5d | 用户/任务/verifier/incident feedback 归一且保留来源/confidence/privacy，只产 proposal |
| TASK-0616 | Counterfactual | PROGRAMMED | TASK-0597,TASK-0609 | host_brain/evolution_lab/counterfactual | 3～5d | 同一 snapshot 可比较 policy/model/prompt/capability 候选，默认无副作用且差异可解释 |
| TASK-0617 | Scenario lab | PROGRAMMED | TASK-0609,TASK-0616 | host_brain/evolution_lab/scenarios | 3～5d | 场景 fixture、时钟、随机种子、依赖故障和期望 Gate 版本化且可重复 |
| TASK-0618 | Knowledge OS workflows | PROGRAMMED | TASK-0546,TASK-0601,TASK-0615,TASK-0617 | host_brain/evolution_lab/knowledge_os；operator_ui/features/knowledge_os | 3～6d | intake/research/synthesis/review/promotion/retention 全链有 provenance、namespace 和审批 |
| TASK-0619 | Identity migration / regression | PROGRAMMED | TASK-0537,TASK-0613,TASK-0614,TASK-0617 | host_brain/evolution_lab/identity；host_brain/digital_life/identity | 4～7d | baseline+overlay migration 产生 IdentitySnapshot/diff、replay/shadow、审批和 rollback target |
| TASK-0620 | Compatibility / dead code removal | PROGRAMMED | TASK-0508,TASK-0524,TASK-0538,TASK-0552,TASK-0566,TASK-0580,TASK-0594,TASK-0608 | host_brain/app；host_brain；edge_client；operator_ui | 5～8d | facade usage=0 且跨两个稳定窗口；旧 writer/runtime/DDL/flags/re-export/dead fallback 删除，import gate 通过 |
| TASK-0621 | Chaos / performance / backup / security / SBOM / ReleaseManifest v2 | PROGRAMMED | TASK-0614,TASK-0618,TASK-0619,TASK-0620 | tests/quality_matrix；tools/release；release | 5～8d | 完整质量矩阵、灾备/回滚演练、可复现构建、组件 SBOM/signature 和 ReleaseManifest v2 通过 |
| TASK-0622 | Final Architecture / Program Seal | PROGRAMMED | TASK-0621 | docs/30_devspec/evidence/m57；docs/60_trace；release | 3～5d | §11 十五项 Final Gate 全部 PASS、无未批准阻断例外，MASTER 签署最终封板 |

M57 Gate：Evolution 输出只能进入 proposal/review；身份和能力激活可 replay/rollback；所有兼容层按证据退役；ReleaseManifest v2 能精确复现代码、schema、protocol、model/prompt/avatar assets 与回滚兼容性。

---

## 8. 迁移、Feature Flag 与切流合同

所有迁移固定采用：

行为特征化 → contract/port 提取 → 新实现经旧 facade 接入 → read-only shadow compare → 单 vertical slice canary → 新请求全量 → legacy drain → 两个稳定发布窗口 → 删除旧 writer/facade/flag。

| Flag family | 控制面 | 合法状态 | 单写要求 | 删除任务 |
|---|---|---|---|---|
| NATSUME_CONTRACTS_KERNEL_V2 | Contracts/Kernel/Composition | OFF、SHADOW、ON | 合同转换不得创建第二事实 | TASK-0620 |
| NATSUME_HOST_RUNTIME_V2 | Host/Task/Agent | OFF、SHADOW、CANARY、ON | 每个新请求只归一个 executor | TASK-0620 |
| NATSUME_DIGITAL_LIFE_V2 | Life coordinator/writer | OFF、SHADOW、CANARY、ON | shadow 只重放；每事件只推进一次 | TASK-0620 |
| NATSUME_MEMORY_DATA_V2 | Memory/Event/Persistence | OFF、SHADOW、CANARY、ON | 双写仅可由 transactional outbox 驱动 | TASK-0620 |
| NATSUME_DOMAIN_PACKS_V2 | Pack Runtime | OFF、CANARY、ON | Pack 不得建立平台 writer | TASK-0620 |
| NATSUME_MODEL_GOV_V2 | Model/Governor/Broker | OFF、SHADOW、CANARY、ON | Grant/Token/ModelAttempt authority 唯一 | TASK-0620 |
| NATSUME_EDGE_RUNTIME_V2 | Edge/Device | OFF、CANARY、ON | 物理输出只由一个 Edge session owner | TASK-0620 |
| NATSUME_QUERY_UI_V2 | Read models/Console | OFF、CANARY、ON | Query 不写；UI command 回 Secure Ingress | TASK-0620 |
| NATSUME_EVOLUTION_LAB_V2 | Evolution/Promotion | OFF、SHADOW、ON | Lab 只产 proposal，不能直改生产 | TASK-0620 |

强制切流规则：

1. Shadow 禁止真实副作用、生产写入、token 消费、设备动作和对外消息。
2. 双写不是默认迁移方案；确需同步时只能由同一 UoW 的 transactional outbox 驱动，并具有 reconciliation、stop 和 rollback 证据。
3. 旧任务由旧执行器 drain，禁止把运行中 attempt 跨 executor 恢复。
4. schema 采用 expand → backfill → compare → consumer switch → compatibility window → remove；切流期禁止破坏性 down migration。
5. 一个 vertical slice 同一时刻只有一个 owner；canary cohort、版本、flag snapshot 和数据 cursor 必须入审计。
6. 兼容 facade 不得新增业务逻辑；usage=0、两个稳定窗口、rollback drill 通过后必须删除。

---

## 9. 停止条件与回滚合同

出现以下任一情况立即停止扩大 canary，并回滚受影响 slice：

- AuthorizationGrant、principal、scope、signature、token consumption 或 risk decision 与基线不一致。
- 重复副作用、重复设备动作、重复消息、同一 action 出现多个 attempt owner/receipt。
- Journal/Outbox/Inbox 丢失、sequence 断裂、projection 无法重建或 reconciliation 不平。
- Surface preview/replay 推进 Life state，或 Life/Memory/Canon 出现第二 writer。
- Verification FAIL/ERROR/required-NOT_RUN 进入 committed success。
- 私密 narrative、raw thought、secret、cookie、Pentest 知识或未授权 memory 泄漏到 trace/prompt/UI。
- migration、backup restore、Edge 独立安装、协议 negotiation 或 rollback drill 失败。
- 新路径错误率相对基线增加超过 0.5 个百分点，或 p95 延迟/资源使用连续三个窗口恶化超过 20%，且无已批准例外。

回滚顺序：

1. 冻结 canary 扩大并记录 flag/config/service profile snapshot。
2. Secure Ingress 停止把新请求路由到新 slice。
3. 由原 owner drain 或 checkpoint 已接收 attempt；不得把 in-flight attempt 交给另一 executor。
4. 恢复旧 facade 的新请求入口；保持新 journal/evidence 只读，不删除审计事实。
5. 回退 projection/read model/config；不对已经 expand 的 schema 做未经演练的破坏性回滚。
6. 对账 grant、task、receipt、journal、outbox、device session 和 external side effect。
7. 形成 incident、root cause、数据修复和重新解锁条件；MASTER 批准后方可再次 canary。

---

## 10. 验证矩阵与执行证据

| 验证层 | 最低证明 |
|---|---|
| Contract | schema round-trip、版本 negotiation、legacy conversion、property/fuzz |
| Architecture | import graph/SCC、禁止边、giant file、facade usage、raw-thought/secret/DDL/private-conn scan |
| Unit/Component | pure kernel、reducers、policy、router、repository、worker、UI component |
| Integration | UoW/journal/outbox、Task/Agent、Life、Memory、Pack、Model/Gov、Edge、Query/UI |
| Golden Path | chat、host input、plan/subagent、scheduler、proactive、device、pentest、review/replay |
| Recovery | crash-at-every-state、lease expiry、duplicate/late/out-of-order event、provider/DB/device failure |
| Data | empty/old DB migration、backfill、projection rebuild、backup/restore、export/import hash |
| Security | identity/signature/scope/rate/replay/airlock/sandbox/secret/SBOM/quarantine/negative matrix |
| Performance | latency、throughput、memory、DB lock、queue lag、model cost、GPU/resource、frontend bundle |
| Release | clean install、Edge standalone、Electron/.NET/Unity bridge、reproducible build、rollback drill |

验证按交付边界分层执行，禁止把阶段 Seal 级成本重复摊到每个原子任务：

1. 普通小任务只运行变更相关的 unit、contract 与 static checks，并在任务证据中记录选择范围；不运行全仓或整阶段测试。
2. 大型 vertical slice、跨域汇合任务和阶段 Seal 各运行一次对应 integration、golden path、recovery/rollback wave；同一未变化 wave 不因后续小修重复执行。
3. 失败修复只重跑失败项与受影响边界；下一次全波验证留到新的大型汇合点。
4. TASK-0621 / TASK-0622 在全部实现任务完成后统一运行一次全量离线套件、真实依赖/设备隔离环境实测与最终发布门禁。此前不得用重复全量实测替代定向证据，也不得把定向通过冒充最终实测。

目标任务落地后，至少提供以下可执行检查；路径尚未存在时由对应任务创建，不得把缺测试解释为通过：

~~~powershell
python -m pytest tests/contracts tests/architecture tests/composition -q
python -m pytest tests/orchestration tests/task_runtime tests/agent_runtime -q
python -m pytest tests/digital_life tests/memory tests/persistence tests/eventing -q
python -m pytest tests/domain_packs tests/model_plane tests/governor tests/security -q
python -m pytest tests/edge tests/gateway tests/observability -q
python -m pytest tests/recovery tests/chaos tests/e2e -q
python tools/architecture/import_graph.py --check
python tools/architecture/api_inventory.py --check
python tools/architecture/db_inventory.py --check
~~~

每个任务 DONE 证据必须包含：任务 ID、基线 commit、变更文件、合同/数据影响、测试命令和结果、黄金/负例、trace/metric、flag 状态、canary 范围、迁移/对账、停止条件、回滚步骤、残留例外与后续删除任务。

---

## 11. Final Architecture Gate

TASK-0622 只有在下列十五项全部 PASS 时才能封板：

1. 目标层之间双向 package dependency 为 0；Edge 对 host_brain.* 实现导入为 0。
2. host_brain/app 不再承载 domain、repository、provider 和 lifecycle 真相；批准保留项仅为有期限的 legacy facade。
3. 所有 production giant file 大于 1,500 行均已拆分，或有 ADR 批准的纯数据/生成例外；禁止新增大于 800 行的混合职责文件。
4. 同一 action 只有一个 AuthorizationGrant、一个 Task/Attempt lineage 和一个 SideEffectReceipt。
5. Verification FAIL、ERROR 或 required-NOT_RUN 不可能进入 committed success。
6. 每个长期字段只有一个 writer；所有 projection 可由 journal 重建并对账。
7. Surface Context 构造零写入；主动行为和具身动作全部经过 durable outbox。
8. 普通聊天不能检索 Pentest 领域知识；Canon Apply 校验批准内容 hash。
9. Host、Obs、Gateway、Bridge 写接口全部具备 principal、scope、signature/token、rate 和 audit。
10. Migration catalog 在空库、旧库、备份恢复和 rollback drill 中通过；production runtime inline DDL 为 0。
11. OpenAPI 不存在重复 method/path handler；UI client 来自版本化 schema。
12. 所有 compatibility facade 有 usage=0 与两个稳定窗口证据，并按删除版本退役。
13. Skill/Provider bundle 有 digest、signature、SBOM、quarantine、sandbox 和 rollback。
14. ReleaseManifest v2 包含 commit、package、schema、protocol、model/prompt/avatar assets、SBOM、全部 Gate 与 rollback compatibility。
15. Golden Paths、replay、chaos、performance、security、backup/restore、Edge 独立安装、UI 合同和 identity regression 全部有 Seal 证据。

任何一项 FAIL 都使 TASK-0622 保持 BLOCKED；禁止以“后续优化”绕过。Architecture Exception 只允许非安全、非数据完整性、非 authority 冲突项，并必须有 owner、影响、监控、到期版本和已编号删除任务。

---

## 12. 执行顺序与首个动作

1. 唯一首个代码入口是 TASK-0461 / LTE-WP-00。
2. M48-B 只有在安全入口 Gate、文件范围和 migration 范围确认后可与 M48-A 并行。
3. TASK-0497 必须等待 TASK-0486 与 TASK-0496 两个 Seal；后续按本表 Dependencies 和里程碑 Gate 解锁。
4. CODEX 每次只将一个未完成任务标 IN_PROGRESS；跨任务共用改动必须登记主 owner。
5. 每个 Seal 前必须完成 rollback drill、evidence index、Requirements Trace 与 DevTasks 状态同步。
6. TASK-0620 之前不得提前删除仍承担 rollback 的 facade；达到删除 Gate 后也不得继续保留。
7. TASK-0622 完成后，本程序状态才可从 READY_FOR_PROGRAM_EXECUTION 改为 SEALED。

执行者不得从“容易实现”的后段 UI、Lab 或新目录开始制造完成假象。先固化安全与事实，再建立 Contracts/Kernel/Composition，随后沿 Host、Life、Memory、Pack、Model/Governance、Edge、Query/UI、Evolution/Release 顺序收敛 authority。
