受众：MASTER
标题：Natsume AI 文档地图与权威顺序

# Natsume AI 文档地图与权威顺序

版本：v9.10
维护日期：2026-07-27
状态：`CURRENT_AUTHORITY_MAP`

## 1. 权威顺序

1. 生产代码、production composition、migration 和机器状态。
2. [当前代码事实](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)。
3. [稳定目标架构](../50_project/arch/TARGET_ARCHITECTURE.md)。
4. [当前架构差距](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)。
5. [唯一活动执行合同](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。
6. PRD、DevSpec、Trace 和 TestPlan。
7. 历史计划、固定 receipt 和审计快照。

README、理论、系统总览、模块地图和数据流负责解释系统，但不能覆盖上述状态 authority。历史 `DONE/PASS` 只对固定提交、环境和声明范围成立。

当前 Plan/Delegate 文档基线是不可变 execution policy，而不是 handler
存在性：Plan policy 绑定 Graph/Budget/Registry/Review/effect owner 并在
initial/replay/continuation/recovery 重开；Delegate policy 绑定
target/adapter/durable Review/exact-zero usage/effect mode，并拒绝内层
Effect/Outbox/delivery claim。工作树定向证据为 `93 passed`、真实 SQLite
四路线 `1 passed`；clean-SHA v3 收据尚待生成，因此两路继续
`INCOMPLETE`，不以本地绿测越级。

当前 Skill 文档基线为 canonical Host Registry 的 93 项完备 policy
partition：28 个 read-only、13 个
proposal-only、3 个 post-Acceptance Workspace、49 个 blocked pending
typed Effect。只有 44 个启用项进入运行 manifest；blocked 能力保持目录
可见但不算完成。代码定向证据与提交绑定七路收据必须分开记录；当前
`c68850f8` v3 收据已使 44 个启用项的 Skill 路线达到 `COMPLETE`，但没有
授权或完成 49 个 blocked 能力。
独立 Personality Registry 的 19 项 metadata 与动态 MCP Skill 没有
governed manifest 入口。

当前文档基线为 migration head `0135_task_review_denial_settlement.sql`：0130/0131 继续负责未决 Operation hold 与 exact signed reconciliation；0132 负责 Attempt-bound child Budget；0133 把 VerificationResult 与 Node transition 持久绑定；0134 把 AcceptanceJudgement、自动接纳 artifact/effect 或 Review waiting bundle 与 Node transition 原子绑定；0135 再把 terminal Review、Node、Graph、graph Budget 和三条 Outbox 收进一个不可变 settlement。多节点 Graph 的普通成功/失败仍由 0090/0091 既有外层 authority 唯一终结。一般 cancellation/release、一般恢复、七路线 commit-bound acceptance 和 NE-01 仍未收口。

当前 Chat 文档基线为 exact Life projection barrier：assistant turn 与 LifeMemory admission 的 durable receipts是 external reply的必要前置条件；首次调用可完成 exact pending intent，reclaimed reconciliation只读。该项只关闭 Chat 的 `transactional_side_effects` 控制，不改变七路线 `INCOMPLETE`、external eventual delivery、wave integration或 NE-07状态。

## 2. 文档信息架构

```mermaid
flowchart TD
    README["README\n项目入口、启动与导航"]
    THEORY["Theory\n为什么这样设计"]
    SYSTEM["System Architecture\n系统怎样组织"]
    MODULES["Functional Modules\n谁负责什么"]
    FLOW["Data Flow + Diagrams\n状态和副作用怎样流动"]
    CURRENT["Current\n代码现在是什么"]
    TARGET["Target\n最终应是什么"]
    GAP["Gap\n还差什么"]
    EXEC["Execution\n按什么顺序做"]
    PRODUCT["PRD / DevSpec / Trace / TestPlan"]
    HISTORY["History\n固定范围证据"]

    README --> THEORY
    README --> SYSTEM
    SYSTEM --> MODULES
    SYSTEM --> FLOW
    CURRENT --> GAP
    TARGET --> GAP
    GAP --> EXEC
    THEORY --> PRODUCT
    SYSTEM --> PRODUCT
    EXEC --> PRODUCT
    HISTORY -."scope-bound evidence".-> CURRENT
```

## 3. 按问题阅读

| 问题 | 文档 |
|---|---|
| 这是什么项目、如何运行？ | [README](../../README.md) |
| 为什么模型不能直接执行或修改人格？ | [核心理论](../15_theory/Theory.md) |
| Host、Obs、Gateway、Edge 如何协作？ | [系统架构](../50_project/arch/SYSTEM_ARCHITECTURE.md) |
| 某个功能由哪个包负责？ | [功能模块](../50_project/arch/FUNCTIONAL_MODULES.md) |
| 请求、Memory、Gateway、Edge、receipt 怎么流？ | [关键数据流](../50_project/arch/DATA_FLOW_ARCHITECTURE.md) |
| 想快速看图？ | [架构图册](../50_project/arch/diagrams/00_INDEX.md) |
| 前端如何查询和发 Operator 命令？ | [Monitor UI 架构](../50_project/arch/MONITOR_UI_ARCHITECTURE.md) |
| 服务如何部署、Docker 启动什么？ | [部署架构](../50_project/arch/DEPLOYMENT_ARCHITECTURE.md) |
| 哪些能力真的可用？ | [当前代码事实](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md) |
| 为什么不能封板？ | [架构差距矩阵](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md) |
| 下一阶段做什么？ | [正式执行合同](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) |

## 4. 当前状态摘要

> 最新 locator 清点（2026-07-25）：生产具名 consumer/import、全部 import-form consumer、locator 模块、Container 字段/锁/引用均为 0。`process_service_container.py`、`process_services.py`、`process_environment.py` 与旧 wiring 已物理删除；ASGI/CLI 构造 fresh `ProcessApplicationPorts` 并显式传入 Host root。旧测试只通过 `tests/support/process_service_compat.py` 的 pytest-only bridge 运行，fresh 生产进程不会加载它。`HostAuthorityRuntimeOwner` 继续持有同一 epoch 的 canonical ledger、Episodic/LifeMemory、独立 Model 与 Capability 图。外部 Chroma/Neo4j namespace、Token/MCP durable restart、资源崩溃恢复、事务化 Chat 和完整 Worker/Obs Composition仍未闭环；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

- Full Architecture：`INCOMPLETE`。
- Workspace：Governed Internal write 已从“Worker 只能留下 dirty worktree”推进为 `sealed manifest/binary patch → deterministic accepted HEAD → 0116 ACTIVE/ACCEPTED/PROMOTED → terminal cleanup`；真实 SQLite+Git 覆盖源分支不动、重放、篡改拒绝与 Runtime 重建。Domain/Skill/Codex、OS/container tenant 和 handle-level TOCTOU 仍缺，因此只记 `PARTIAL_W2_INTERNAL_WRITE_PROMOTION`。
- 当前静态结构：最新完整 Gate 1–14=`PASS`、`failed_gates=[]`；历史 `FAIL [1,3]` 已由中立合同分层和 Research shadow ledger 职责拆分关闭，不能继续当成现状，也不能把静态通过外推为运行时完成。当前 checkpoint reconciliation 新增 focused Store，不拥有独立连接/UoW，`task_repository.py` 仍低于既有 4600 行上限且没有扩大白名单。Full Architecture=`INCOMPLETE`、NE-01=`PARTIAL / IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
- Host AgentRuntime：`PARTIAL_APP_LOCAL_PORT_CUTOVER`。Life authority 就绪后，Host lifespan scope 向 Input→Cortex、Life projection 与 conversation Acceptance 发布同一 immutable port bundle，并在 shutdown 以 exact identity 撤销；Context、RouteCatalog、Bond、session stores、memory/state/governance/authority runtime、VTS、TTS/meta、Multimodal、heartbeat 与 startup 都显式供应。生产 locator 与 Tribe 无上下文 fallback 已物理清零，Tribe shared runtime 与 23-Agent catalog 由 Host scope fresh 持有；外部 Chroma/Neo4j namespace、资源崩溃恢复与易失 Tribe bus 仍未隔离。事务化 Chat commit、legacy Executive generic Delegate、Plan 多节点执行和其余路线不因本切片而视为 `WIRED`。
- Chat retrieval：`PARTIAL_HOST_SCOPED_READ_DEPENDENCIES`。普通 Cortex 检索只接受同一 AgentRuntime scope 提供的会话历史、偏好构造器与 recall factory；helper 不再回读 `process_services`。这不构成 Chat Acceptance、外部 delivery 或全局 locator 删除证据。
- Scheduler：`PARTIAL / DURABLE_CLOCK_OCCURRENCE_FENCE`；Internal Agent：`PARTIAL / INTERNAL_WRITE_PROMOTION_AND_SIGNED_RAW_FAILURE_CLAIM_BEFORE_CLEANUP`。受治理 Internal 单节点可在首次 activation 和其后的 app-local survivor 周期中，从 seal 后/WorkerProjection 前的密封检查点重开完整 lineage，重建唯一 projection 并继续 Verification、Acceptance、Effect 与 claim，Agent 不重跑。具备 exact Workspace authority 的 write 还会密封 frozen-base path manifest 与 binary patch，Acceptance 重算 deterministic accepted HEAD 并经 0116 晋升，源分支不移动；clean/no-change 路径保持兼容。Host 也在 mandatory boot scan 后周期重扫同一 exact durable admission；live Lease 只延后，到期后无需进程重启即可再次评估。检查点 mutation 已由 composition-owned `InternalAgentCheckpointUnitOfWorkPort` 封口，WorkerProjection 只有一个物理 INSERT owner；A（projection/Operation）、B（Budget charge）、C（rejected terminal bundle）是三个 durable atomic UoW，不是整链单事务。response loss 只读重开，禁止二次 mutation。未 sealed 的取消仅在 exact single-node lineage 下消费两种互斥签名终态：`RELEASED/RELEASE/NO_SIDE_EFFECT/no receipt`，或 `COMMITTED/COMMIT/SIDE_EFFECT_CONFIRMED/durable receipt`；v1 result digest 保持兼容，v2 绑定 receipt，且必须先提交 claim 再清理真实 Git Workspace/stage。一般 unknown effect、其它 cancel/budget claim closeout与 general `settle_worker` crash bundle 仍缺；Domain/Skill/Codex write、handle-level TOCTOU、OS/container isolation与legacy bridge也未完成，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
- Operation 已 `COMMITTED` 后的 Budget settlement 只在 exact checkpoint/版本链可证时续接；共享 `EXCEEDED` 下 missing 只能绑定唯一 winner 补写 `ALREADY_EXCEEDED`。真实 SQLite 已覆盖双 checkpoint、four-root、五节点菱形和八个并行根节点，依赖按 immutable graph order 释放且 worker 不重跑。`CONFLICT`、unknown `RESERVED/RECOVERY_REQUIRED` 和 cancellation crossing 会在新 Lease 前写 0130 append-only hold + Outbox：conflict quarantine，后两类进入各自签名 reconciliation。签名 `RELEASE + NO_SIDE_EFFECT` 现在可只关闭任意多节点图中的目标 Operation/Attempt/Node/Lease；Graph/Budget 保持 ACTIVE，重启后由唯一 0090 decision 原子终结，READY sibling 零执行，Acceptance receipt 在 `PENDING→COMMITTED` 间保持稳定。无限规模、其它 failure/cancellation、自动归因和部署 fan-out仍缺。
- Governed Operator：`14_OF_37_VERTICAL_SLICES / NE01_IN_PROGRESS`。仓库 migration head 已推进到 `0135_task_review_denial_settlement.sql`。0126～0132 保持各自签名 evidence、WorkerProjection、Effect、mode-policy、recovery 与 Budget 边界；0133 固化 durable Verification，0134 固化 durable Acceptance/Review 创建边界，0135 原子收口 Review 拒绝终态。Scheduler 管理 HTTP 作为独立 `SCHEDULER` surface，不虚增 Operator 库存。其余 `23/37` 命令、部署 key/HTTP/network canary、完整七路线、真实模型与 Docker 全栈仍未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
- Durable DAG：`PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。B5A/B5B 以显式 `result_node_id`、0091 和 canonical projection 冻结整图成功；B5C 已推进到 0098。Cancellation、Operation 签名对账与 Effect 签名对账均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`，开启 cancellation 时要求两类对账入口、Secure Ingress `ENFORCE`、可用 Gateway 和 verification-only Ed25519 公钥。0098 只追加 immutable case/proof sidecar：PENDING proof 可作为新 cancellation decision evidence；FINALIZED correction 只在 trusted read 验证 immutable legacy decision ref/payload digest，绝不改写旧 decision、fence 或 evidence。proof 的 case/intent/effect/fence/Attempt/resolution/actor/Outbox 与 `graph_revision` 均被签名和物理 trigger 绑定。生产门禁只阻断实际持久化且未修复/未隔离的 legacy inventory；仓库无 SQLite 资产不能证明外部生产数据已清理。Budget 高竞争、部署密钥/凭据、完整 key lifecycle 与真实 canary 仍缺，atomic、bounded 与 cancellation 均保持 OFF。
- PlanningSearch：内核可用；Code D16 有 dynamic pure-shadow catalog/compile，D17 有 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` 的双 detached worktree 本地实验机械链，D18 有独立 0106 preregistration → paired evidence → `VERIFIED_CLEAN` → terminal receipt 的 append-only durable ledger。D19 仅为已预注册的冻结 `python -m pytest -q <repo-relative-test>` 在同一主机受信 worktree 子进程中强制 argv、测试次数和总验证时限，并把无路径命令回执嵌入 arm evidence；它不是模型/插件/网络/文件系统隔离、通用硬预算或 production influence。跨领域真实收益仍 `UNPROVEN`。
- Capability Planning authority：Research/Frontend 的 `CAPABILITY_SEARCH_V1` 候选上限必须同时满足 intent 请求和 Governor approved binding，effective risk 为二者最小值；冻结目录强制选择两项不同 Skill，再以 `skill → skill → chat` 编译图，grant ref 与步数进入 virtual-state lineage；缺失/畸形 binding risk 失败关闭。该本地定向证据不提升 NE-02 或收益状态。
- Persona 演化：`CENTRAL_FENCE_WIRED / PROPOSAL_PIPELINE_MISSING`。
- 长期演化程序：作为整体尚未实现。
- Agent 模型预算：Chat、Internal 与 governed Agent Skill 已共享
  graph/node/attempt-bound guard；Skill sealed authority 会保留 canonical
  Budget JSON，Agent 多轮请求逐次扣减真实 measurement。该项只关闭局部
  超支旁路，Budget/NE-01 仍为 `PARTIAL / IN_PROGRESS`。

机器状态：[current_architecture_status.json](../../configs/release/current_architecture_status.json)。摘要变化时必须先更新机器状态、Current 和 Gap，不能只改本地图。

- 受治理 Chat 与 Tribe scope：`SurfaceRuntimePorts` 在 Host lifespan 内由 AgentRuntime/Life authority 一次组装并经 Internal/Tool Runtime 注入 frozen Chat；ASGI 将同一 `InputRuntimeApplicationPorts` 交给普通 Chat、Gateway 与 ASR。Authority、Bond、Memory/State 与 Governance runtime 缺失时稳定失败关闭，不再请求期解析 locator。生产 locator module/field/lock/ref=`0/0/0/0`；Input/Chat Agent ports 解析 exact Host-owned Tribe catalog。Tribe active binding 已改为 execution-local `ContextVar`：lifespan/background execution 与 HTTP request 显式激活 exact scope，两个重叠 scope 的 Agent/registry identity 已隔离，stale inherited context 失败关闭。23 个 Agent backing singleton/setter 与三类 shared runtime fallback 已归零，无 exact context 统一失败关闭；易失 bus、完整 Host multi-app canary 与外部 Chroma/Neo4j namespace 仍未收口，事务化 Chat acceptance、generic Delegate、七路线与 NE-01 仍未完成。

- Chat 检索依赖收口：`48 passed in 5.55s` 的 port/helper/lifecycle 定向集合与 `17 passed in 1.17s` 的相邻 Surface/read-route 单元集合均通过，二者可能重叠、不得相加。HTTP/波次集成和 NE-07 真实服务栈仍未运行。

## 5. 稳定合同

- [产品需求](../20_prd/PRD.md)：产品 WHAT、用户主权和验收边界。
- [工程合同](../30_devspec/DevSpec.md)：工程 MUST/MUST NOT。
- [执行任务](../30_devspec/DevTasks.md)：活动执行波的任务映射。
- [需求追溯](../60_trace/requirements_trace.md)：FR → code → test → execution wave。
- [测试计划](../../tests/TESTPLAN.md)：Gate 和测试层级。

## 6. 目录地图

| 目录 | 定位 |
|---|---|
| `10_whitepaper` | 产品宪法、用户主权和人格原则 |
| `15_theory` | 唯一核心理论总纲 |
| `20_prd` | 稳定产品需求，只写 WHAT |
| `30_devspec` | 稳定工程合同与唯一活动执行文档 |
| `35_quality` | 可复用质量规范与范围受限的旧审计 |
| `40_agents` | Codex 协作约束 |
| `45_testing` | 当前专项机器证据和实验报告 |
| `50_project` | 当前架构、部署和工程指南 |
| `50_release` | 固定提交的发布与回滚证据 |
| `60_trace` | 当前需求追溯、风险和故障记录 |
| `_history` | 历史快照，不作为 current authority |

## 7. 维护规则

1. 需求、理论、当前事实、目标、差距、执行状态和历史证据必须分文件维护。
2. 生产架构变化时按 [文档同步工作流](../50_project/guides/DOCUMENT_SYNC_WORKFLOW.md) 更新对应文档。
3. 新架构状态必须同步机器 JSON、Current、Gap、Execution 和 Trace。
4. 旧方案、完成报告和文件清单快照不得留在当前架构目录冒充现状。
5. 临时调查、命令日志和运行输出进入被忽略的 `artifacts/`，可变运行数据进入 `runtime/`。
6. 删除或移动文档时同步本地图、目录 index、链接和文档门禁策略。
