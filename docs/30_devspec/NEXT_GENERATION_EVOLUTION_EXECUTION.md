受众：CODEX
标题：Natsume AI 下一代演进正式执行合同

# Natsume AI 下一代演进正式执行合同

版本：v3.1
日期：2026-07-20
状态：`NE-01_IN_PROGRESS / PROGRAM_INCOMPLETE`
修订执行基线：`origin/dev@f379d6dbe`；既有 NE-03 证据保留为已完成波次证据；当前连续 migration head 为 0118 Conversation Acceptance Effects，0115 durable HardState、0116 Workspace lifecycle 与 0117 Scheduler Clock authority 保留为连续历史地基；NE-01 继续收紧通用 Acceptance Effect 与 Internal protected result 边界
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
| Universal Runtime 七路线 | Gate 已覆盖 `chat/skill/plan/delegate/scheduler/internal/operator`，但七路当前全部为 `INCOMPLETE`。Chat/Skill 缺当前提交和迁移头绑定的真实回执；Plan/Delegate 仍有节点级治理缺口；Scheduler governed occurrence 已把真实 handler 移到 Acceptance 后，Internal 仍桥接 legacy executor；Operator 已迁移 `scheduler.task.trigger`、`sys_mode.switch` 与 `task_terminal.reconcile` 三条 vertical slice，其他 34 个写命令继续阻断。终态修复只在 Secure Ingress 后把签名命令写入 0119，TaskGraph 仅携带 ref/digest，并经 Review、Action Preview 与 Acceptance 后执行。旧 GR-06 与历史 2/37 receipt 只作历史证据，不具备整路线完成资格 |
| Scheduler | `PARTIAL / DURABLE_CLOCK_OCCURRENCE_FENCE`：post-Acceptance handler 与 immutable result authority 不变；0117 已删除 `SchedulerClock._retry_state`，并在同一 UoW 将 live DUE retry claim 原子转成 occurrence-bound claim。执行前 renew、执行中 heartbeat；过期 lease、第二 owner、stale fence 和旧 tick 时间都不能授权 dispatch/clear/defer。单元、合同与 durable-UoW 定向批次为 19 项、47.53 秒。definition/admin、mutable batch freezing、通用未知效果人工归因与 canary 未完成 |
| Acceptance Effect 恢复 | `PARTIAL / EXACT_POST_ACCEPTANCE_EFFECTS_AND_AUTHENTICATED_QUERY`：visible turn、Preference、Life projection 是三个独立 post-Acceptance Effect。Preference store gate 缺失/非 `bool` 时失败关闭且零写；配置 false 或 malformed env 保守解析为 `bool false`，只写零 event/profile 的 `DISABLED` receipt。启用路径由 0118 在同一 SQLite UoW 写 event、append-only profile child version 与 evaluation receipt，sealed-child trigger 与 reconcile full-set 校验共同拒绝追加/篡改；`NO_SIGNAL` 也有真实 receipt。推荐反馈只绑定 admitted-time 以前且 exact person/conversation/session 的 delivery，并冻结 canonical delivery digest。Surface response loss 只能用 `internal_reply` HMAC 只读 query；Gateway 从同一 SQLite outbox payload 精确校验 `effect_id`，`NOT_FOUND` 永远保持 `UNKNOWN` |
| Internal Agent | `PARTIAL / INTERNAL_SEALED_CHECKPOINT_CONTINUATION`：边界 v2 会编译为 V3，但执行仍桥接 legacy `AgentWorkOrder`。下一次 runtime activation 可对受治理 Internal 单节点主动扫描 sealed checkpoint，重开 seal-bound recovery envelope、历史 Lease→更高 fence completion Lease 与 Operation/Budget/Preview/Grant/Task lineage，重建唯一 WorkerProjection 后继续 Verification、Acceptance、Effect、Secure Ingress claim 与 clean/no-change 0116 promotion，且不重跑 Agent。检查点 mutation 由显式 `InternalAgentCheckpointUnitOfWorkPort` 独占；A（WorkerProjection/Operation）、B（BudgetUsage/Budget）与 C（重验A/B后的terminal bundle）分别原子持久化并以durable continuation衔接，不能写成整链单事务。响应丢失只读重开且mutation调用数保持1；终态 worker 周期处理精确 sealed abandon/cleanup。周期 survivor continuation、crash-left unsealed partial cleanup、failure/cancel/budget claim closeout、general `settle_worker` crash bundle 仍未完成；一般write、handle-level TOCTOU、OS/container isolation、legacy bridge与canary也仍缺 |
| Durable DAG | `PARTIAL_ATOMIC_DEFAULT / NE01_IN_PROGRESS`：新生产 Graph 默认声明 `ATOMIC_GRAPH_SUCCESS_V1`，0090/0091 分别原子收口失败/成功；Host默认启用，Scheduler/Internal/Operator显式启用。公开accepted replay要求Graph COMPLETED与graph Budget CONSUMED；历史legacy图只做兼容恢复。0102 Node Acceptance、应用Python单写封口与0092～0099既有边界保持不变。bounded fan-out、Cancellation、Operation/Effect reconciliation与0099 auto仍默认关闭；general/unknown-effect/multi-node recovery、目标部署inventory/remediation、现实resolver、部署key lifecycle与真实canary仍未完成 |
| Budget | Host/Scheduler/Internal 已接 graph/node 预留与 usage ledger，但入场前 Chat 模型、legacy Skill 内部调用、Delegate 子 Agent、Scheduler handler 和 Internal legacy executor 的完整真实计量仍缺，七路线对应控制保持 `PARTIAL`。8/16/32 线程与跨进程竞争只允许一个 `EXCEEDED` winner/Outbox，其余 sibling 以 `ALREADY_EXCEEDED` 引用 winner；每次 charge 的 CAS 尝试下限为 policy `concurrency + 1`。concurrency 计量、DEGRADE/REVIEW 与 program-wide 策略仍未完成 |
| Review | `WIRED_TASK_BOUND / CANARY_NOT_RUN`：0096 coordination、surface continuation、PRE optional Attempt、POST no-rerun、exact Task-authority hand-off 与独立 `review:decide` 已接通；Operator 部署/真实 canary 未完成，也不代表 cancellation reconciliation 完成 |
| Persona 演化 authority | `CENTRAL_FENCE_WIRED`：legacy runtime writer 已被中央拒绝；当前只保留无副作用候选，proposal/Review/版本激活将在 NE-06 建立 |
| PlanningSearch | 公共内核存在；Host 不生成竞争方案 |
| Pentest L3 | 强制 shadow-only；0105 通用 durable calibration 可显式注入并跨重启重放，但默认兼容 ledger 仍在内存，outcome 是 `UNVERIFIED_RUNTIME_RECORD`，不能影响真实动作或生成 capability-unlocking 后继 |
| Code/Research | Code 已有动态纯 shadow catalog、`PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` 双 worktree、0106 cleanup-gated ledger，以及 D19 预注册固定 pytest argv 的受信子进程验证；Research 的 D20a 现有公开 metadata 动态 catalog 与可信实验根/manifest 的 readonly-file loader。两者都仍没有 Research paired cohort、真实模型、独立 verifier、成本/延迟收益对照或生产 influence；D19 也没有 durable historical cohort、受控模型/工具、独立信任域 verifier、hermetic Python 或 OS/container sandbox |
| Composition | `PARTIAL_HOST_LIFECYCLE_AND_DURABLE_HARD_STATE / D8_DURABLE_EMBODIMENT_AUTHORITY`：D1～D8 边界继续成立。每个 `create_app()` 构造独立 `HostLifecycleContainer` 与 `HostHardStateOwner`；`HOST_SYS_MODE` 仅提供 bootstrap 候选值，生产 owner 在 migration、canonical SQLite 和 Host runtime lease/fence 绑定前拒绝写入。0115 以 runtime scope 分区 append-only mode event 和 canonical head；repository 在一个 UoW 中完成完整 chain/head 验证、expected revision/mode CAS、exact lease/fence 复验、event append 与 head advance，owner 只在 durable commit 后替换内存状态。重启和 Host fence 轮换恢复已提交 mode，exact replay 不重复追加，漂移、篡改、旧 fence、失效 lease 和部分写入失败关闭。`sys_mode.switch` 使用 app-local governed Operator composition 和 Host/Operator 共用的单一 Acceptance Effect 恢复 owner；关停时必须先排空 Operator ingress 与 Runtime/Effect，再 quiesce Workspace，ModeTransition 依赖不得提前销毁。Chat、Tribe、Lifecycle 与 Scheduler 读取同一 owner，runtime snapshot 无 env fallback，Obs Life Body 只读 durable projection；process HardState 三 facade、slot/lock 与伪写保持为 0。ToolBroker/MCP direct/callback authority 已退役且唯一生产构造仍为 app-local `host_brain/tools/broker.py`。其余 process-wide locator、heartbeat、Tribe/Endocrine agent、共享 status、Host source/Chat/Delegate 其他 authority、普通 Agent Tool authority、完整 Worker scope 与真实 executor 仍未完成；自动模式规则和 last-interaction 仍易失，production canary/NE-01 波次集成未运行，NE-01 不得封板 |
| Evolution abilities | 多为 partial/placeholder/missing |

Memory/Distillation 的 D6 口径进一步冻结为：`COMPRESS_BEFORE_SAVE` 只证明 pre-save hook 被观测，不能记作 compression outcome；`DistillationEngine` 在 batch、single 及 item-level 写出的 terminal audit event 也不能记作完整 `LongTermMemoryDistiller` cycle。对应不可测 total/success/failed/cycle 字段必须为 `null`，只允许另列 observed event counts。

Ledger-stats 的 D6 容量合同冻结为“最新有界窗口”：每表按公开时间列和 durable 行身份倒序读取 5,001 行，只物化最新 5,000 行；`truncated` 只声明更旧尾部未物化，不再使整表永久 503。截断后只回答 `start_ms` 严格晚于最老保留时间的窗口；同毫秒边界、直接全表读取和未命中的 request ID 均以 `PROJECTION_SOURCE_TRUNCATED` 失败关闭。没有新事实的轮询复用 source cursor/digest、只刷新 freshness，immutable journal 行数保持不变；来源不可用与超保留期查询也继续失败关闭。因此这不是完整历史聚合，也不得据此宣称全量覆盖。

迁移 0103 的冲突口径同样冻结：legacy 行映射出的确定性 attempt ID 若已被不同 canonical 证据占用，整笔 `BEGIN IMMEDIATE` 必须失败并保留 `llm_node_call_log`；不得用 `INSERT OR IGNORE` 跳过冲突后删除旧表。已提交迁移的重放由旧表已删除的空兼容壳保证幂等。

ModelAttempt 非流式生产路径只投影 provider 明确回报的 token 维度，缺失维度保持不可测，只有 prompt 与 completion 都明确测得时才推导 total。总览、模型分组与时序桶都必须公开 measured-call coverage 与 lower-bound；部分覆盖的累计值不是精确总数，完全缺测为 `null`，真实零仍有测量覆盖证据。流式路径当前没有生产调用者，adapter 也未保留最终 usage，所以状态冻结为 `UNMEASURED / NOT_ENABLED`；在引入 typed final response/usage 与终态回归测试之前不得启用或计入“预算闭环完成”。

任何 PR 或报告若把上述 `PARTIAL/BROKEN/SHADOW` 写成 `DONE/PASS`，该交付自动失败。

2026-07-18 代码复审将最近波次重新定级如下；这些状态覆盖对应 PR 正文中的宽泛完成描述：

| PR | 当前定级 | 不计完成的原因 |
|---|---|---|
| #80 | `CONTRACT_ONLY_AT_MERGE / SUPERSEDED_BY_84_REWORK` | 合并时 World/Intent 没有进入 Surface、Proactive 或 Task admission；当前生产切流由 #84 重做，不回溯把 #80 改写为闭环 |
| #81 | `SELF_ATTESTED_SHADOW / TRUSTED_EVIDENCE_MISSING` | dataset、verifier 和 effect isolation 可由同一 runner 自报 |
| #82 | `PARTIAL / NON_ATOMIC / EVIDENCE_UNVERIFIED` | activation 与 receipt 跨事务，URI 未查询真实 authority，runtime resolver 未切流 |
| #83 | `LEDGER_ONLY / NO_REAL_ACTIVATION` | APPLIED/ACTIVE/ROLLED_BACK 仅写事件名，没有切换真实 version owner |
| #84 | `DRAFT_REWORK_IMPLEMENTED / LOCAL_ACCEPTANCE_PASSED_WITH_KNOWN_BOUNDARIES` | Host lifespan、三消费者 Scene identity、durable Intent admission、Action Preview authority、Guardian 与 Host Catalog 五阶段已重做且 commit-bound 定向 Gate 已通过；长周期目标、Windows 优雅停止和部署 canary 等边界继续如实保留 |

GitHub Actions 当前因账户额度无法分配真实步骤，统一记为 `NOT_RUN_ACCOUNT_QUOTA`。零步骤失败不阻断当前波次，也不能写成 PASS；本地定向 Gate 必须绑定 commit SHA 保存到忽略目录 `artifacts/local-gates/<sha>/`。

任何生产代码或工程工具变更必须在同一变更集中更新 README、对应权威文档或机器状态；CI 的 change-contract 门禁直接检查 Git 变更集，不能再用“文档最近更新过”代替本次同步。架构语义变化还必须按 freshness policy 的 source→doc 映射更新对应事实、数据流、图和 Trace。

## 3. 执行 DAG

```text
交付协议与机器状态纠偏
  ↓
NE-03 World/Scene/Intent/Guardian 重做与生产切流
  ↓
NE-01 执行主链与显式 Composition 闭环
  ↓
NE-02 真实 PlanningSearch 与收益证明
  ↓
NE-04 Evolution Lab / Scenario / Counterfactual
  ├──────────────┐
  ↓              ↓
NE-05 Skill      NE-06 Feedback / Knowledge / Identity
  └──────┬───────┘
         ↓
NE-07 Full Program Acceptance
```

NE-03 先修复已打开的 #84，随后回到 NE-01 清除通用执行主链缺口；NE-05 与 NE-06 可在 NE-04 的实验/提案合同冻结后并行。不得为了“并行”同时修改同一 authority、migration 或 composition 文件。

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

- 当前结构债务切片：`acceptance_effect_handlers.py`、`asgi.py`、`scheduler_repository.py` 已分别降至1262/1376/2942，focused 模块不转移原 writer、lifespan/task owner或repository UoW。967行 Budget rejection Store只拥有受约束的 C-boundary mutation；444行只读 terminal resolver与93行纯 route helper不拥有第二 authority。独立 Gate 1–14=`PASS`、`failed_gates=[]`、`各 Gate issues=[]`；Gate 3 `new_mixed_over_800=[]`，没有扩allowlist。上一结构提交36文件265项只作历史。该静态 PASS 不完成 Universal Runtime、locator/旧 authority 清零、wave integration 或 canary；NE-01 仍 `PARTIAL / IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；
- 混合职责 authority 第二切片：ASGI 保留 `create_app`/lifespan/nonlocal owner/完整 shutdown，Life/Scheduler、启动安全 Gate 与 Runtime Context 接线迁入 app-local wiring；Obs humanoid 只迁出纯响应模型；Degradation 只迁出 immutable policy catalog；Node Grant 与 Planning compiler 只迁出纯 codec/error；Intent admission 保留唯一 verifier/三入口/RuntimeEvent append，Review continuation 迁入无 store owner 的私有 mixin；Guardian 保留 store/restore/reducer/`_append` 唯一 writer，命令和纯合同迁出。第三切片继续将 Host/Tool app-local 装配迁入无生命周期 owner 的 typed composition；其原始 commit-bound 七文件快照为 1492、1303、1894、1495、1281、1336、1247 行，合并后的当前文件库存以机器状态清单为准。fresh 全仓 mixed-file Gate 为 `issues=[]`，没有新增或放宽 allowlist。该静态 Gate 通过不改变 atomic/fan-out/cancellation/reconciliation 的生产开关，也不代表 Service Locator 或旧执行旁路已清零；
- Host lifecycle Composition 切片：`HostLifecycleContainer` 在每次 `create_app()` 构造，`runtime_lifespan` 只通过 typed ports 预热依赖并启停 monitor/resource manager。factory/start 全部成功后才发布；Activity factory 失败不发布 Mode；closing fence 阻断新的 `begin()`；ResourceManager startup sample 归入唯一 owned loop。Chat/Input、Tool、Scheduler、Guardian heartbeat 及 lifecycle snapshot 共用选中的 app-local provider；Obs 读路径缺 monitor 时显式 unavailable，不启动第二实例。Tribe lifespan 以 process-global exact owner bind/unbind fence 只允许一个 live Host，第二个 Host fail closed，不提供并发 multi-app Tribe 隔离。`http_hooks.py` locator 直接导入降为 0，全局 setter/slot/lock 删除。该切片原始 commit-bound 清单为 named 47/152→45/124、all-form 52→51、Container 56 fields/4 locks/51 refs；#89 Gate 11、ReAct 与 Visual 的 45/123/51、43/120/49、41/117/47 继续作为历史保留。HardState app-local 切片后的机械库存为 named 40/116、all-form 46、Container 52/3/48。这不覆盖尚未迁移的其余 process-wide locator、heartbeat cache/client、Tribe bus、shared resource status file、HardState mode 持久恢复与 Worker 对象图；
- business-layer SQLite Gate 10 切片：0112 schema readiness 从 runtime 私有 SQLite 检查下沉至 `RuntimeEventRepository`，`RuntimeEventStore` 暴露 readiness port；Service Catalog 的连接、SQL 与事务下沉至 `SQLiteServiceCatalogRolloutRepository`，`rollout_authority.py` 仅保留合同、Protocol 与 Guardian Observer，runner 依赖 Protocol。`ACTIVATION_STARTED` receipt 与 rollout head 在同一事务提交，注入失败时共同回滚。World Observation 原 `_write_lock` 只是 `RLock` 的静态误判，已更名 `_transition_lock`，不形成新 writer。本切片没有 migration；`host_startup.py` 与 ASGI 仍负责 concrete repository wiring，typed composition 未完成。定向证据按组为 Context `7 passed`、Catalog unit/contract `15 passed`、Host socket integration `6 passed`、Repository/Global 独立复跑 `16 passed`（与 15 重叠，禁止相加），Global SQLite boundary `PASS`。该切片与后续 Projection 生命周期切片完成后、最终 Gate 1 合同切片前的 full architecture static 为 `FAIL [1,11]`，相对 `origin/dev` 的 `FAIL [1,6,10]` 已关闭 Gate 10 与 Gate 6；Gate 11 是 ToolBroker production-service inventory 同时命中 `process_runtime_services.py` 与 `tools/broker.py`，不是 Gate 10 回归。当时 Gate 1 SCC、Gate 11 与 NE-01 仍未完成，波次 integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；
- projection daemon lifecycle Gate 6 切片：`ObsApplicationContainer.start()` 已改为具名 `start_projection_jobs()`，生产代码仅 `obs_fastapi_app.py` 的嵌套 ASGI `lifespan` 调用一次。真实所有权顺序是 ports bundle 内部原子 single-use claim→compose app-local daemon→start→yield；关闭执行 stop/join/in-flight fence→精确 leader lease release→close owned SQLite→clear state。stop timeout 与 rollback close failure 都保留原 container/ports 句柄供重试；同一 bundle 不能被两个 app 共享，`dataclasses.replace()` 沿用同一 claim，独立 ports/app 仍隔离。没有 module-global daemon，也没有新 migration。完整 Obs app 首跑为 `13 passed + 1 test fixture failed`（旧 monkeypatch 指向已删除 `start`），修正后该 case `1 passed`；最终生命周期关键批次 `12 passed`；daemon cadence/shutdown/serialization/start-single-flight `4 passed`。这些批次有重叠，禁止相加。Gate 6 与 Gate 10 均为 `PASS`；该切片完成后、最终 Gate 1 合同切片前的 full architecture static 为 `FAIL [1,11]`，`origin/dev` 基线仍为 `FAIL [1,6,10]`。当时 Gate 1 SCC 和同一 ToolBroker Gate 11 双命中仍未关闭，NE-01 仍 `IN_PROGRESS`。wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；后台 daemon 未捕获异常退出尚未通过 `/health` 直接呈现 `running/last_failure`，continuous-SLO supervision 未闭环；
- Gate 1 Service Catalog 分层切片：启动协调器已迁至 `service_catalog/startup_coordinator.py`，composition 旧路径仅作同对象重导出；稳定合同位于 `contracts/service_catalog.py`，旧类型路径也只同对象重导出；`rollout_authority.py` 只保留 Guardian observer，repository 仅依赖 contracts。application→composition 两条和 Catalog persistence→application 一条依赖均为 0，SCC 从 application/composition/persistence 缩为 application/persistence。该切片完成时剩余 persistence→application 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4）。证据为 contract/unit `61 passed`、真实 Catalog/Host socket integration `7 passed`、边界复跑 `7 passed`，集合可能重叠不得相加；
- Gate 1 PlanningSearch 持久化合同分层切片：新增纯 `contracts/planning_search/`，下沉 core、benchmark、calibration、code、research 八组合同与模型；旧 `planning_search` 同名模块仅同对象重导出，保持 `__all__`、类型 identity、`isinstance` 与序列化形状。code benchmark、calibration、research shadow 三个 SQLite repository 不再导入 application 路径，清除 7 条 persistence→application。边界 `5 passed`，D13/D18/D19/D20 回归 `60 passed, 1 skipped`（Windows 无 symlink 权限），`py_compile`/diff-check 通过。Gate 1 仍 `FAIL`，application/persistence SCC 只剩 Evolution/Lab/Skill/World 4 条；application→composition=0、Edge violation=0、reverse import=0，Gate 6/Gate 10 保持 `PASS`。这不是 PlanningSearch 功能或收益完成；无 migration，wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`，typed Composition 与 NE-01 继续 `IN_PROGRESS`；
- Gate 1 最终合同分层切片：新增纯 `contracts/evolution.py`、`contracts/evolution_lab.py`、`contracts/runtime_context.py`、`contracts/skill_lifecycle.py`，旧 Evolution/Lab/Runtime Context 路径保持同对象重导出，`skills/skill_lifecycle.py` 只保留应用服务并重导出合同；Evolution Governance、Evolution Lab、Skill Lifecycle、World Observation 四个 repository 只依赖中立 contracts，最后 4 条 persistence→application 清零。边界 `4 passed`，Context/World、Evolution Lab、Skill、Evolution Governance 定向 `32 passed`，`py_compile`/diff-check 通过。fresh Gate 1=`PASS`，issues/SCC/reverse imports 均为空、Edge finding/violation=0，Gate 6/10 保持 `PASS`；但 #87 组合后的 full static=`FAIL [11]`，Gate 11 仍是 ToolBroker production-service inventory 双命中。无 migration，wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；这只关闭 Gate 1，不完成 NE-01、PlanningSearch 收益或 NE-04～NE-06 生产闭环；
- Gate 11 ToolBroker composition 收口切片：删除 process `get_tool_broker` accessor、`tool_broker` container slot 与未被消费的 `HostLifecycleToolBrokerProvider`，生产 ToolBroker 只在 app-local `host_brain/tools/broker.py` 构造且 constructor count=`1`。fresh full architecture static=`PASS`、`failed_gates=[]`，Gate 1/6/10/11 均通过；该 #89 commit-bound 历史库存为 named 45 modules/123 names、all-form 51 consumer modules、Container 54 fields/4 locks/49 mutable refs。ReAct、Visual 与 HardState 的 43/120/49、41/117/47、40/116/46（Container 52/3/48）均为历史快照；删除两个无消费者的 Obs 全局缓存后，当前 Container 为 50 fields/3 locks/46 mutable refs。结构/生命周期/bridge/route-token/seed 为 `43 passed`，ASGI、Tool/MCP、HTTP、broker、MCP audit 与真实 SQLite 为 `98 passed`，补充 `1 passed` 与 43 项批次重叠，禁止相加；`py_compile`、policy JSON 与 whitespace 通过。该 PASS 只表示当前静态 Gate 通过，不证明真实 workspace-bound governed executor、完整 typed Composition 或 NE-01 完成；wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；
- HardState app-local authority 切片：新增 lifespan-owned `HostHardStateOwner`，仅由 composition 将 bootstrap-only `HOST_SYS_MODE` 显式传入，非法 bootstrap mode 以 maintenance 失败关闭；owner、runtime snapshot 以及 `chat_generation_payload`/`RoutingMatrix` helper 均不再把该环境变量当运行期真相或写回通道。模式切换以一个非重入锁原子替换 immutable state，Endocrine resolver 在冻结快照并释放锁后调用。Chat mode gate、Tribe、Lifecycle 与 Scheduler 共享同一 owner；owner 缺失/非法时 maintenance/degraded fail closed；Obs Life Body 仅消费 durable runtime projection。两个 helper 已改为接收显式 mode，但正向生产调用点仍未形成证据。process `get_hard_state`、`get_endocrine_state`、`set_sys_mode` 与对应 slot/lock 为 0，Limbic/Prefrontal 伪写为 0。定向总集 `210 passed`，D7b `7 passed`，operator/focus/runtime projection `29 passed`；三者独立记录，禁止汇总相加。Tribe 伪写 `45 passed`、legacy 显式 mode `23 passed` 等集合已包含于前述集合或另有重叠，同样禁止相加。Obs 宽组在 60 秒上限内未结束，既不计失败也不计通过；wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。本切片没有真实 executor、Service Locator 全量清零、mode durable journal/restart recovery、helper 正向生产 mode 传播或生产 canary，NE-01 仍为 `IN_PROGRESS`；
- HardState durable journal 切片：新增 0115 `host_hard_state_event` append-only journal 与 `host_hard_state_head` canonical projection，二者在同一 SQLite UoW 中提交；`HOST_SYS_MODES` 与 `HOST_MODE_TRANSITIONS` 已移入 neutral HardState contract，journal repository 不再反向导入 core application，fresh full architecture static=`PASS`、`failed_gates=[]`。migration trigger 与 repository 双层精确绑定 Host `runtime_scope_id + owner + lease + fencing token + expiry`。生产 ASGI 在 migration 和 embodiment runtime authority 就绪后绑定 journal，绑定前写入 fail closed；完整分页 replay 校验 revision/digest/from-to/head，restart 或 fence rotation 从 durable head 恢复，expected-head CAS 保证双 writer 只有一个胜者。成功切换仅以 journal 作为 canonical audit，exact replay 不重复追加进程内 history 或 legacy `error_log`。durable owner、ModeTransition、Operator domain adapter、真实 SQLite runtime binding、neutral contract 与 0115 contract 权威 consolidated 为单一核心批次 `84 passed in 66.37s`，覆盖 payload drift、historical receipt lookup、owner drift、no-fake `NO_CHANGE`、request-id 贯通、released/stale fence、tamper、event/head 两个 fault point rollback与1001 transitions；修复后的 lifecycle/ASGI/manifest/operator final peripheral affected batch 独立为 `67 passed in 35.78s`。外围67可能与核心84语义重叠，二者以及旧`83`/`82`历史均不得叠加。内部response-loss exact replay不等于Operator/HTTP E2E：`sys_mode.switch`仍被Preview/TaskGraph gate阻断，legacy helper无POST route。自动模式规则和last-interaction仍为进程内状态，production canary、NE-01 wave integration均未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；因此只关闭durable mode authority/restart缺口，不提升NE-01为完成；
- governed Operator `sys_mode.switch` 切片：以 canonical Secure Ingress request identity 生成 typed `OPERATOR` HostCommand 与 immutable TaskGraph，冻结 `operator_command_id=sys_mode.switch`，由 Review continuation 恢复后编译最小 `host.sys_mode_transition` Acceptance Effect。Host 与 Operator 共用一个 app-local `AcceptanceEffectRuntime` 投递/恢复 owner；effect handler 必须凭 typed Preview terminal proof 重开 exact event/boundary/authority，才能调用 `ModeTransitionService`。外层 OBS Outcome Journal v2 分离 domain result、continuation 与 terminal result：首次 Review 返回 `WAITING_REVIEW`，中央 Secure Ingress claim 保持未封口；若 Review 已持久化而 outer WAL append 失败，Host 重启保持 OBS admission 关闭。零条 Review 转 `UNCERTAIN`；唯一 `PENDING/APPROVED/CONSUMED/REJECTED/EXPIRED/CANCELLED` 必须重开 Secure Ingress、HostCommand、TaskNode、ReviewItem 与所需 terminal continuation 后恢复同一 wait，多条或漂移均启动失败关闭。`RECOVERY_PENDING`、其他 owner 持有 lease 或不确定失败必须 durable defer。`NO_CHANGE` 不产生 HardState event 且必须投影 `side_effects_executed=false`；HardState 调用后的响应丢失、`success=False` 或归因失败必须先重开 immutable event-id：已有 commit 规范化为 `REPLAYED`，读取不确定保持 `PENDING + RECONCILIATION_REQUIRED`/`UNKNOWN`，只有权威确认不存在才普通失败；历史成功即使后续追加超过 1000 条 mode event 也不重跑。关停顺序固定为 Operator ingress drain→Scheduler producer stop→Task Runtime/Effect owner close→Workspace quiesce→ModeTransition/Activity dependencies close→command/security stores close→最后清空 app refs，关键关闭失败保留依赖和发布引用以供重试。Operator 进度仅由 1/37 增至 2/37，最终受影响集中回归为 `265 passed in 179.82s`；首轮为 `264 passed + 1 stale fixture failed`，禁止与终轮相加。剩余 35 条写命令继续 admission 阻断，commit-bound acceptance、NE-01 wave integration 与 production canary仍待收口，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；
- 生产等价的 Chat/Delegate/Plan 集成在同一 SQLite 上构造 durable Host lease、World/Scene/Guardian composition、Production ActionPreview resolver 与真实 Acceptance/Outbox，只替换最底层模型/网络 transport。它修复了四个此前被假夹具遮蔽的断点：WorkOrder 重开只导出 canonical Runtime Context 字段；Scene 64hex digest 在 Preview 边界确定性投影为 `sha256:` 且 resolver 仍逐字段重开比对；同 scope/owner/lease/fence/revision 的 Guardian expiry 只允许单调延长，缩短或任一身份漂移拒绝；terminal boundary 深拷贝 `action_args`，避免 proof 注回 handler payload 形成循环别名。Chat route 精确验证并消费 model budget guard 后，公开 WorkerProjection 移除 guard、保留 typed measurement，敏感字段 Gate 没有放宽；
- 修复 `AdmittedTaskNode` 未冻结 `TaskNode.risk_level` 的真实断点；Scheduler/worker execution context 现在从 immutable admitted node 读取 1..5 风险等级，避免首次真实 worker 执行触发 `AttributeError`；
- Persona 中央写入围栏、迁移专用审计入口、无副作用候选化及 `T-PERSONA-FENCE-01`；
- Scheduler 真实 `WorkOrderV3` admission、Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay；
- Scheduler post-Acceptance handler 切流：worker 只产出冻结 operation plan，canonical WorkerProjection 作为 Acceptance 前 checkpoint；Graph Acceptance 与 `scheduler.operation.execute` Effect staging 同一 TaskStore UoW，shared app-local dispatcher 在 Acceptance 后重开 derived Preview 并调用真实 handler，实际结果继续写原有 immutable Scheduler result repository。0117 再把 Clock retry/backoff 从进程内 dict 切到 SQLite：`admit_due_occurrence` 在同一 `BEGIN IMMEDIATE` 中验证 DUE owner/fence、写 occurrence/definition head，并把 retry row 原子转为同 owner/fence 的 occurrence claim；执行 lifecycle 在 dispatch 前 renew、执行中按 lease/3 heartbeat，lease 过期不得自我复活，clear/defer 必须使用实际调用时间。定向证据为 19 项、47.53 秒。definition/admin direct authority、mutable batch freezing、一般 unknown-effect 人工归因、wave integration 与 canary 仍未完成，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；
- 通用 Acceptance Effect 恢复切片：visible turn、Preference 与 Life projection 已拆成独立 post-Acceptance Effect。Preference store gate 不存在或非 `bool` 时失败关闭且零写；配置 false 或 malformed env 保守解析为 `bool false`，disabled 路径只提交 `DISABLED` receipt，不写 event/current profile/child profile version。启用路径以 effect-bound operation ID 在 0118 单一 SQLite UoW 中提交 event、append-only profile child version 与 evaluation receipt；receipt 可见后 trigger 禁止追加 child，reconcile 还会比较 receipt 声明与实际 child 全集，绕过 trigger 的额外记录也失败。推荐反馈以请求 admitted timestamp 为上界并 exact 绑定 person/conversation/session，evidence 保存 canonical full-delivery digest。Life 只读重开 exact command receipt。Surface response loss 只能以真实 `internal_reply` HMAC/timestamp/nonce 查询 exact Gateway outbox；Gateway 在同一 SQLite snapshot 内从 canonical payload 重验 `effect_id`，Host 重派生 delivery/payload digest，`NOT_FOUND` 不得降为 `NO_SIDE_EFFECT`；
- Internal Agent sealed checkpoint 切片：保留真实 `WorkOrderV3` admission、Secure Ingress principal、Budget、child grant、Verification、Acceptance、Receipt 与 replay。下一次 runtime activation 会主动恢复 seal 后/WorkerProjection 前的受治理 Internal 单节点检查点，重开 frozen node input、verified grant、Preview、worker timing、Operation/Budget/Task/Lease lineage，以更高 fence completion Lease 重建唯一 WorkerProjection，并继续 Acceptance Effect、canonical result、claim 与 clean/no-change 0116 promotion；Agent 调用数保持 1，后续客户端调用只读 exact replay。显式 checkpoint UoW 已接入 Host/Internal/Scheduler/Operator 四个生产 coordinator，并独占 WorkerProjection INSERT；A/B/C 三个事务边界各自原子，C 的五个故障点全量回滚。response loss 只做一次mutation，随后由read-only authority重开。终态 sealed artifact 的 abandon/cleanup 由 app-local 周期 worker 处理。周期 survivor continuation、crash-left unsealed partial cleanup、failure/cancel/budget Secure Ingress claim closeout、general `settle_worker` crash bundle仍未完成；一般write、handle-level TOCTOU、OS/container isolation与legacy bridge也未完成，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；
- post-COMMIT Budget settlement 双重丢失切片：Operation 已 `COMMITTED` 后，BudgetUsage 写响应与首次 read-back 同时失败不再触发 `mark_worker_failure()` 或 Budget release，而是以 `RECOVERY_REQUIRED + budget_settlement_state=UNKNOWN` 原位挂起；该分支不附加 lifecycle/finalization mutation，并保留可能已原子提交的 usage/`EXCEEDED` 事实，process-control `BaseException` 直接传播。真实 SQLite 只承认 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING / CONFLICT` 四类；单节点 Lease 到期后更高 fence takeover 对前三类分别进入 `ACCEPTED/BUDGET_BLOCKED/按幂等结算结果收口`，`CONFLICT` 失败关闭，worker 调用保持 1。普通单节点与 Internal Agent 的 exact rejected 终态共用同一事务；generic route authority精确绑定HostCommand/TaskNode/WorkerProjection，仅接受生产支持路由并拒绝Internal降级；response loss、后续duplicate HostCommand与terminal reconciliation共用只读exact resolver，unknown/conflicting Outbox按durable corruption失败关闭且Runtime保留该原因。最终唯一去重 12 文件批次为 `71 passed in 99.50s`、0 fail/skip；多节点/批次当前只保证安全 hold，settlement recovery 仍未完成；
- 本批验证口径：Scheduler、0117 migration 与 coverage 合同 37 项；原子 fence/heartbeat 子集 19 项、47.53 秒，二者不相加。generic exact Life/Surface 25 项。受影响组合首跑 203 项成功、1 项因 direct integration 未注入新增 Conversation/Life readers 而失败；改为 production-equivalent composition 后该失败项单独复跑成功，不得重写为一次 204 项。该证据不是 NE-01 wave integration；真实模型、NE-07 服务栈、canary 和远程 CI 均未运行，远程状态为 `NOT_RUN_ACCOUNT_QUOTA`；
- Durable DAG B1 safe-retry 子集：Coordinator 消费 B0，原子提交 claim 并执行到期 promotion；worker resolver 的显式 `RetryableWorkerPreparationError` 只会在 operation reservation 前进入 `schedule_retry`。retry_count BudgetUsage、Budget、Attempt、Node、Lease 与 Outbox 在同一 `BEGIN IMMEDIATE` 内提交，超限则原子阻断 graph；真实 Coordinator 已证明 attempt 1 → RETRY_PENDING → 到期 attempt 2，且 reservation 后 worker 异常绝不 retry。提交后响应丢失只在 Attempt/Node/Lease/Graph/Budget/BudgetUsage/关联 Outbox 全部精确一致时 replay，不重复扣费；`CancelledError` 原样传播。当前生产 resolver 尚无现实异常源抛该 marker，因此这是已接 contract path，不是现实故障自动分类已完成；
- pre-reservation 普通/耗尽 failure 已通过 `fail_running_attempt_bundle` 在单事务内关闭 Attempt、Node、Graph、Lease 并生成四条 attempt/evidence-bound Outbox；已有 operation reservation 时拒绝，精确重放会拒绝 fence、version、evidence 或 Outbox 漂移。Budget finalize 仍在 bundle 外；
- Task mutation 共用的 `_insert_outbox` 与 exact replay 归属 `TaskOutboxDeliveryStoreMixin`；BudgetUsage contention、Operation checkpoint read 与 Lease takeover authority 分属 focused mixin。`TaskLeaseTakeoverAuthorityMixin` 只有读取校验并借用 caller transaction，确定性 acquisition identity 与纯 row binding 已继续拆到 93 行 `task_lease_replay_identity.py`；`DurableTaskStore` 仍是唯一连接/事务/UoW authority。当前静态 Gate 记录 `task_repository.py` 4585 行、takeover mixin 230 行，低于未修改的 4600/250 行门禁；
- operation reservation 后已启动 `DurableExecutionHeartbeat`：先续 Lease、再续 Reservation，流事件和最终结果均 checkpoint，stop/join 后才允许使用最新 reservation version COMMIT；续租同参响应丢失支持精确重放，失败返回 `RECOVERY_REQUIRED` 且绝不 COMMIT；
- Budget 高竞争已由 focused `TaskBudgetUsageStoreMixin` 收口：8/16/32 线程及跨进程竞争中只有首个超限 usage 能把 Reservation 推到 `EXCEEDED` 并拥有唯一 Outbox；刷新后到达的不同 sibling 只追加 rejected `ALREADY_EXCEEDED` usage，版本 before/after 不前进且引用 winner Outbox，不能成为 0090 terminal authority。0090 Budget decision 的写入、读取与 replay 均复用完整 terminal authority 校验；public reader 以显式只读事务冻结一个 SQLite snapshot，篡改 Reservation version、第二同版本 Outbox、sibling Graph/Attempt lineage或 winner 引用会 fail closed。`DurableBudgetRuntime._charge` 的尝试数为 `max(configured retries, policy concurrency + 1)`；定向测试以 concurrency=8/连续 8 次 CAS conflict证明第 9 次成功；
- post-COMMIT Operation continuation 只开放 `SINGLE_NODE_CANONICAL_COMMITTED_CHECKPOINT_PROVEN` 窄窗：Store 必须同时证明 `COMMITTED` Reservation、唯一 `OPERATION_COMMITTED` Outbox、原 Attempt、历史 Lease/fence、hash receipt、canonical WorkerProjection 与唯一 graph Budget。worker BudgetUsage 只允许 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING / CONFLICT`：前两类只读核对，`MISSING` 才可由新 owner/fence 按持久时间上界幂等结算，`CONFLICT` 一律 fail closed；worker 不重跑；
- pre-Operation Attempt takeover 是另一窄窗：Operation 必须尚不存在，Node/latest Attempt/旧 Lease/唯一 graph Budget/canonical PRECHARGE 必须来自同一 durable snapshot。`TaskLeaseTakeoverAuthorityMixin` 借用 TaskStore 的 `BEGIN IMMEDIATE`，同时重验 deterministic reservation id 与同 scope/fingerprint Operation absence，不取得第二连接/UoW/writer；`task_lease_replay_identity.py` 以 Attempt、owner、TTL、expected version、takeover mode、absent/existing reservation ID 计算确定性 Lease/Heartbeat identity。响应丢失重放还必须精确匹配未续租 ACTIVE Lease、初始 Heartbeat、历史最大 fence+1、未过期 TTL 和 canonical Outbox；任何参数或持久行漂移都失败关闭。普通 Attempt/Lease/Operation 额外要求恰好一个 ACTIVE graph Budget，takeover 才允许 ACTIVE/EXCEEDED。任一 live sibling 先返回 `GOVERNED_EXECUTION_IN_PROGRESS`，Operation 竞态转 `RECOVERY_REQUIRED`。Operation 已预留后的 worker 异常或 collision 不再写普通 Attempt/Node/Graph failure或释放Budget/Lease；Graph/Budget terminal写拒绝活跃Node/Attempt/Lease/Operation。最终工作树五组不重叠验证为184/69/23/68/52，合计396项、0 fail/skip；Gate 1～14 全部通过，repository/mixin分别4585/230行且无白名单扩张。串行兼容命令超时后按三组重跑全绿，新增AST测试自身的首参假设失败修正并补齐纯模块/早返回门禁后identity/structure为12项通过；这些过程证据保留但不叠加。该切片不关闭普通Graph+Budget终态分步崩溃窗、success/effect完整原子UoW、multi-node/batch settlement、unknown effect或七路线验收，状态为 `CANONICAL_PRE_OPERATION_ATTEMPT_TAKEOVER_PROVEN / POST_RESERVATION_RECOVERY_HOLD_PROVEN / GENERAL_MULTI_NODE_RECOVERY_INCOMPLETE`；
- `AcceptedGraphFinalizationRuntime` 统一正常执行、在线 continuation 与 outer DAG 的 Graph completion。Node `ACCEPTED` 已落盘、Graph 仍 `ACTIVE`、Effect 尚未落盘时，重启只从 exact Operation/Attempt/Lease/Outbox 与 canonical WorkerProjection 重建 Effect，并通过原 UoW 原子提交；篡改投影不得完成 Graph，重复重放不新增 Effect，worker 不重跑；
- 0102 immutable TaskNode result artifact 与 GR06 NODE Effect 已合入 canonical `commit_node_acceptance()`：production normal、COMMITTED checkpoint continuation 与 Review approve 在一个 Store UoW 中冻结 exact Effect set、artifact、`Node ACCEPTED` 和 exact Attempt Outbox；响应丢失逐项重验且禁止 Acceptance 后补 Effect。Effect delivery 必须命中并验证同 graph/revision/node/Attempt artifact，旧成功 Attempt 不得借权。compiler 独占 dependency result ref，READY/worker 前解析同 revision 前驱并冻结输入；缺失、跨 lineage 或篡改 fail closed。新 admission 拒绝空 `output_contract`，Task Runtime startup 扫描活动 legacy 空合同和缺 artifact。public Graph create/transaction insert 与 standalone Effect stage 已删除：生产 Graph hook 仅 `HostCommandRepository._admit_normalized()` 1 次且与 HostCommand 共用 admission UoW；Effect hook 仅 Graph Acceptance、Node Acceptance、Parallel Success 三个 canonical UoW 共 6 次；Graph/Node 与 Effect 的 `INSERT` SQL 分别只有一个私有 owner。测试 raw/orphan fixture 不写 `host_command_journal`，不能作为 canonical 证据。本轮无新 migration/trigger，故定级只能是 `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED / SQL_DIRECT_BYPASS_PRESENT`；
- `GovernedTaskCoordinator` 的 `bounded_fanout_enabled` 仍默认 `false`，生产 composition 未开启。`0090` schema 在 Budget/worker 前 fail-fast；进入 B5 成功路径还要求 `0091` schema ready。Store-only `ParallelSettlementProjection` 已接管 settlement 所需的 active re-entry、rejected Budget outcome 与 outcome snapshot read/validation，`ParallelSettlementRuntime` 共享原 Store/BudgetRuntime/RuntimeOutbox 并接管 failure/review/Budget outcome resolution 与 settlement orchestration；Coordinator 直接调用该服务。live 与 crash/re-entry 的 0090 outcomes 继续从整个 admitted Graph 的 durable Node、Attempt、Reservation、BudgetUsage 重建同一 payload/ref并 exact replay，DurableTaskStore 仍是唯一 UoW mutation authority，既有 ownership/CAS、selected Budget usage、digest/Outbox/Attempt/usage fail-closed 语义不变；
- B5A 已把 `result_node_id` 与可选 `completion_policy` 加入 canonical TaskGraph/AdmittedExecution、durable identity 和 planner semantic/shadow comparison。新生产 Graph 与新 PlanningGraph 默认声明 `ATOMIC_GRAPH_SUCCESS_V1` 并冻结 sink result；失败重规划在调用方未显式指定 policy 时继承前一 revision，避免把历史 legacy 图静默升级。历史无 policy/legacy 图只保留兼容重放；
- B5B 的 `atomic_graph_success_enabled` 已对 Host 默认开启，并在 Scheduler、Internal、Operator composition 显式开启；`bounded_fanout_enabled` 仍独立默认关闭。Application 在 repository admission 前校验 runtime capability、node-grant service 与 parent grant；Coordinator 直入只接受覆盖全部 TaskNode 的 exact grant map。atomic policy 即使单节点或串行执行也由 outer 成功封口；terminal-leaf 实际并行还需另开 bounded fan-out；
- 每个节点必须已有 `ACCEPTED` Node、latest `SUCCEEDED` Attempt、已提交 Reservation receipt 和 canonical worker projection。projection 会重算 digest并绑定 command/request、route/target、graph/node/attempt、receipt 与 node grant；只有显式结果节点编译整图 effect intents 和用户可见结果，Skill 也使用冻结 canonical projection；
- 全节点成功由 `complete_parallel_graph_success()` 在单一 Store UoW 中提交 `Graph COMPLETED`、共享 `Budget CONSUMED`、两条不同的 Graph/Budget Outbox、整图 durable effect intents 与迁移 0091 的 `Natsume.task_runtime.parallel_success.v1` canonical decision。任一 identity、projection、effect、Outbox 或版本漂移 fail closed；晚故障整体回滚。completed replay 只读 0091、不重编 effect；连续两次 Store 响应丢失会重读 terminal authority；
- 定向证据覆盖单节点、串行、多 terminal roots 完成顺序对抗、flag-off admission 前拒绝、缺 grant service 拒绝、projection 篡改、restart、一次/两次响应丢失与 completed replay；
- B5C 以迁移 0092 持久化 canonical cancellation intent、final decision 与 effect fence。`task_graph.cancel` 已接入 Operator Command Gateway、同源 BFF 与 Task Runtime UI，payload 精确绑定 `graph_id + graph_revision + reason_code`，权限固定为 `obs:write + task-runtime:cancel`。Gateway 生成的 server-owned authority 携带 durable audit/claim、request/principal/tenant、认证 body digest、action/scopes；同一个 `SecureIngressGraphCancellationAuthorityVerifier` 注入 Host/Internal/Scheduler，重读 committed 且尚无 terminal `claim_result_ref` 的 Secure Ingress authority 并核对 canonical body后才签发以 `audit_ref` 为 request identity 的取消 authority。Settled authority 不得作为进程内 bearer 重用，公开 duplicate 只重放原 outcome；Application 不接受调用方自报 request identity/source；
- Cancellation、Operation、Effect 与 terminal reconciliation 分别由默认 `false` 的四个 Operator 开关控制；开启 cancellation 时 Operation、Effect 与 Terminal 三类对账必须全部开启。`HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED` 默认 `true`，只允许处理 0099 `EXACT_REPAIRABLE` case。任一 Operator 入口启用都要求 Secure Ingress=`ENFORCE`、可执行 Operator Gateway 与 verification-only Ed25519 公钥环，否则 ASGI 在路由开放前 fail closed。下游异常后 Runtime Group 重读 exact revision 的 durable intent：匹配本次 request/audit/source/reason/graph/revision 才标记可能已有副作用，无 intent 则明确未执行，读取失败或畸形事实保守标记不确定；
- 0099 是 schema-only、append-only terminal reconciliation sidecar：migration 只安装 immutable case/proof/supersession，不扫描或改写业务行。Readiness 从受信 migration 资产重建 canonical schema，对 live `sqlite_master` 严格比较 3 table/4 index/10 trigger 的全部 SQL，并以 Python codec 复核 case/subject/proof。`recover_startup` 后才在单次启动最多 10,000 个 terminal Graph 的上限内 inventory。`FAILED + exact atomic failure bundle + 唯一 graph-scope ACTIVE/EXCEEDED Budget` 可由显式 system auto 修复，`BLOCKED failure` 与 legacy `CANCELLED + unsettled Budget` 只接受签名 Operator；child/missing/multi Budget、PENDING/CONSUMED、`COMPLETED`不一致或 Graph/Node/Review/Attempt/Lease/parallel after-state 漂移永久 quarantine。Parallel authority 对 0090 要求恰好一条 canonical decision，0091/0092 分别复用完整 success 与 trusted cancellation validator；exact Attempt failure bundle 还逐条校验 Attempt/Node/Graph/Lease Outbox `payload_ref`。Runtime 与 Store 写事务在新建/激活 Budget 前双重拒绝 terminal Graph；同 Graph/revision 任一 inventoried 未决 current case 都围栏普通 TaskGraph transition/cancellation request 和 Budget transition/consume/usage/retry，0099 proof UoW 是唯一显式 bypass。Proof 只在原 Store UoW 中 CAS exact Graph/Budget并写确定性 Outbox；旧 case 仅能链接到新的 current case，每个历史 case 的 supersession chain 最终仍须收敛到可信 proof，不创建 Attempt/Lease或重跑 worker。`TERMINAL_HEALTHY_CLOSURE` 只在存在 unresolved unsuperseded 历史 case 且 current classifier healthy 时生成，固定 `SIGNED_REPAIR_REQUIRED + NONE`、仅 `SIGNED_OPERATOR`、Graph/Budget/Outbox 零写；snapshot 漂移继续追加 replacement/supersession。默认关闭的 exact auto 在查询层只取 exact case并循环到空/显式 `TRUNCATED`；system proof 以 immutable case 创建时间生成稳定 identity，多进程收敛到同一 proof/replay，批处理前缀已提交后的失败必须明示 `side_effects_may_have_run=true`。主 Host 与独立 Obs 的三条查询入口均只构造同库 `mode=ro + query_only=ON` reader，禁止回退完整 Store。该能力仍为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`；外部 deployment inventory、完整 key lifecycle 与 canary 仍未知，general/multi-node/pre-budget recovery仍未完成；
- 当前切流修正：上一条中“默认关闭的 exact auto / 整项 WIRED_DEFAULT_OFF”是 0099 初次交付口径。现在 Host 默认执行 exact auto；隔离真实 SQLite restart canary 已证明 exact FAILED case 自动释放预算、signed-required CANCELLED case 保持围栏且阻断 readiness，完整 cancellation 依赖束也通过临时 verification-only 公钥启动 preflight。Operator 与 signed repair 仍默认关闭，HTTP/network canary、部署 inventory 和 key lifecycle 未运行；机器状态必须记录 `AUTO_EXACT_ON_LOCAL_SQLITE_CANARY_PASS / OPERATOR_OFF_HTTP_CANARY_NOT_RUN`，不能越级为生产闭环；
- phase 1 UoW 先将 Graph 推入 `BLOCKED`，把非终态 Node、active Attempt/Lease、Review/token、operation 和未授权 effect 写入取消或 reconciliation 状态，提交后才 cancel/join 本进程 child 与 heartbeat。执行重入会发现 intent 并继续 advance/finalize，但本地 task handle 不是 authority；
- phase 2 只有在 active ownership、operation/effect/review 不确定性全部收敛后才原子提交 Graph `CANCELLED`、Budget 释放/取消、Outbox 与 canonical decision。accepted NODE + succeeded Attempt 的 effect 保留既有 delivery authority；其余 NODE/未完成 GRAPH effect 被 fence。0091 success 与 0092 cancellation first-writer-wins，请求/封口响应丢失 exact replay；
- Effect fence 不把“投递失败”解释成“没有副作用”：取消前已经 `FAILED`，或取消时仍 `CLAIMED` 且随后 handler/transport 失败，均继续保持 `RECONCILIATION_REQUIRED`。只有 `COMMITTED` 且存在 durable receipt 才可自动成为 `COMMITTED_EVIDENCE`。当前表没有可信的 handler-never-invoked / `NO_SIDE_EFFECT` provenance，因此不能用 reason code 猜测；旧 `FAILED_EVIDENCE` 只作兼容读取，必须纳入历史修复或隔离；`PENDING` 由完整 codec tuple 再细分，只有 `attempt_count=0 + fencing_token=0 + status_version=0 + reason_code/last_error=NULL` 的 virgin epoch 可逻辑取消，retry epoch保守待对账，混合 tuple 按 durable corruption fail closed。当前 canonical authority 不会让 handler-run retry epoch进入 cancellation fence；该分类是 legacy/corruption hardening。历史 retry-PENDING + CANCELLED fence 同样需要显式 audit/repair/quarantine，不能在 FINALIZED replay 中改写；
- Operation 与 Effect 对账均已完成受限签名合同/服务、ProcessTaskRuntimeContainer 构造、ASGI Operator Runtime 注册、scoped OBS command、公钥解析与 verification-only TaskStore 注入，状态为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`。`task_effect.reconcile` 只接受 exact case/subject snapshot；`NO_SIDE_EFFECT` 解析为 `CANCELLED`，`SIDE_EFFECT_CONFIRMED` 必须带 durable receipt 并解析为 `COMMITTED_EVIDENCE`。response-loss 只允许 exact proof replay，未知写结果保持 uncertain；
- 0098 只追加 immutable case/proof sidecar 和 revisioned reconciliation Outbox，不更新或删除 0087/0092 Effect、fence、decision 行。PENDING proof 参与之后的新 decision evidence；FINALIZED correction 必须绑定当前 immutable legacy decision ref/payload digest，且只作为 getter/request/advance/finalize replay 的 trusted overlay，proof ID 不得进入或替换旧 decision evidence。迟到的 Effect commit/fail/reschedule 必须在原事务内看见 PENDING proof 并 fail closed；
- Operator 只读接口提供脱敏 candidates、cases 与 exact signing challenge。0098 production readiness 只阻断数据库中真实存在且未满足 proof/correction 的 repairable/finalized legacy inventory，structural anomaly 永远 fail closed；正常运行时不存在的虚拟 case 不得阻断启动。仓库扫描未发现 SQLite 资产，因此尚无生产存量 remediation 或 canary 证据；
- Outbox publish 保留 `RuntimeOutbox` asyncio lock 与 `RuntimeOutboxPublisher` 实例级 `threading.Lock` 作为进程内降噪；调用矩阵仍为 27 build/31 publish/58 calls/5 best-effort。0093 已提供跨进程 durable claim/CAS。0095 要求 Graph Outbox 绑定现存 revision，并冻结 `graph_revision` 为 INTEGER/nullable/no-default/non-PK。回填来源包括 decision/intent/attempt/唯一 revision、HostCommand evidence 对 CREATED 的绑定，以及 Migration Runner deterministic `natsume_sha256_hex` 以 revision-bound command ID 重算升级前 governed ID/key，对 ACTIVE 等非 attempt transition 的绑定；冲突或不可解析自定义 producer 整笔迁移失败。Outbox ID/key 保持升级前稳定，revision 是新增列维度。新 Graph payload ref 必须是 `taskstore://event/TASK_GRAPH/{graph_id}@{revision}/{version}`，旧 ref 仅限 durable codec/replay compatibility，新写 trigger 拒绝。predecessor/head 按 type+id+revision 隔离；所有 Graph producer 显式写 revision。新 Runtime Event 使用 `graph_id@revision`，旧 journal 经 source Outbox revision 兼容读取；`TASK_STORE_OUTBOX source_ref` unique partial index 保证一对一，append 已成功但 complete 未确认的 legacy 重投只有全字段精确重建时返回原事件。Operator/ReviewStore 按 canonical revision subject 过滤。publisher 继续逐条 claim→幂等 append→fenced complete，失败 release/backoff；后台 worker 自动续投/takeover，close 与前台 publish 线性化，`DRAINED` 仅在 total_pending=0。0093/0095 readiness 与 receipt gate 同时冻结 payload trigger、source index、delivery index 与 persisted bindings；
- Runtime Event `created_ts=0` 对可重建的 current revisioned 与 legacy unrevisioned candidate 都允许 durable 非负 timestamp 兼容，因为两条历史 append 路径都可能用时钟替代零值；除 timestamp 外的字段继续 exact。PlanningRecovery 显式调用 `read_scheduler_snapshot(..., include_graph_outboxes=true)` 并在同一读事务使用唯一 terminal Outbox 的真实 payload ref；默认 false，普通 scheduler/Operator 不扫描 Outbox。只有旧版或替代 Store 未提供可选扩展字段时可合成 revisioned ref；生产 DurableTaskStore 提供 inventory 后，缺行、畸形或歧义都 fail closed。0100 append-only supersession、`admit_replan()` predecessor validation + revision+1 admission 单事务、exact replay/rollback 和旧 revision 共享 mutation/Review fence 已完成；当前 safe subset 仅接受静止 `FAILED`，拒绝 `BLOCKED`、所有历史 Review continuation 与 Acceptance Effect，自动 failure-triggered replan/生产 canary仍未完成，状态为 `PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET`；
- Task Runtime 启动已按 migration 与 0093/0094/0095/0098/0099、Review 0096、grant uniqueness 0097 readiness → `task_store.recover_startup` → 0099 terminal inventory → 默认开启且仅消费 `EXACT_REPAIRABLE` 的 system auto → publisher → recovery outbox publish → outbox background worker → Host/Internal/Scheduler runtime activation → cancellation 与 Review continuation workers 排序；ASGI 将配置逻辑 owner 派生为每次 container construction 唯一的 execution owner。恢复异常在 route activation 前 fail closed。Container 拥有且向 Host/Internal/Scheduler/worker 注入同一 process-shared `LocalGraphExecutionRegistry`，仅用于本进程 quiescence，不是 durable authority。Coordinator 激活后任一 worker start 失败会尝试反向 stop/join worker 与 close Coordinator；全部回滚成功才 unbind，失败则保留 handle 供 `aclose` 重试，Runtime Group 也保留关闭失败的 child。shutdown 经有界 grace+cancel，若任一 worker 仍不静默则 fail closed 并保持 Store 开放供重试。0093 Outbox 已具在线过期 takeover；0094 cancellation worker 以独立 recovery-scoped owner 扫描、claim、heartbeat、defer 与 expired takeover pending intent，claim/heartbeat/defer/complete 在 `BEGIN IMMEDIATE` 后才采时；Runtime 未显式获得 `local_pending` 时必做 `cancel_and_join`，本地非静默或 operation/effect/review 未收敛时都只 durable defer；
- lifecycle 未完成统一返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`；activation rollback 或 `aclose` quiescence 失败后 bind/activate 锁死，只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放；只有 Coordinator 与 worker 均静默后才 drain Outbox/关 infrastructure；
- 0096 新增纯协调表与 composition-owned Review continuation worker。`CONSUMED/REJECTED/EXPIRED` 的 task-bound Review 与原 Review mutation 同事务入队；worker 以 owner/token/expiry/单调 fence 跨进程 claim/takeover，按 immutable HostCommand surface 回到原 Host/Internal/Scheduler runtime，并只在 reviewed Node 离开对应 waiting checkpoint 后完成协调。PRE `WAITING_APPROVAL` 进入统一 0090 outcome，公开 receipt/decision 使用合法的 `attempt_id=null`；POST 仍绑定原成功 Attempt 且不重跑 worker。续跑 credential 的期限覆盖 `budget time + child TTL` 且硬上限 24 小时；READY claim 与 worker 首次执行校验必须复核 live claim、exact Attempt/Lease/owner/fence。首次校验后由 Task Attempt/Lease 管理已开始的执行，Review heartbeat 丢失不伪称能撤销 blocking/to_thread 外部副作用，旧 owner也不得 complete/defer；
- 0097 为 `S_TASK_GRAPH_NODE_GRANT` 增加 request+intent partial unique index 与身份 trigger；SQLite ledger 在 `BEGIN IMMEDIATE` 后重查 deterministic batch identity，跨进程 loser 只读取 winner 的 exact batch。批量第二条写入失败整事务回滚，历史重复让迁移 fail closed；
- 0089 若在物理事务提交后丢失 migration receipt，只能在 checksum、列约束与索引形状全部吻合时修复 receipt；schema readiness 仍必须先于 Budget 写入；
- Executive 生产入口、legacy SkillDag 执行和进程内任务 registry 已删除，`task_progress/recent_turn` 改为 durable projection/read route。
- 旧 `/v1/agent/run` route-local Visual Agent loop 已同步退役：raw loop/client 与 Ledger writer 已物理删除，兼容 route 只返回固定 SSE 拒绝；视觉聊天仅 `text` 可进入模型链，`actions` 和未知格式在 runtime/model/Prompt/Memory/Ledger 前拒绝，legacy Action Planner 模型入口固定封口，纯 parser 标记 `NONE_PROPOSAL_ONLY`。这些负向路径都不产生 Task/Attempt/Effect/Receipt；正向视觉自动化只能由受治理 proposal 编译为新的 immutable TaskGraph revision，该安全收口不得记为能力完成。
- D9 统一将受认证 Host Input/Gateway 的 Secure Ingress tuple（principal、成对 claim/audit ref、body SHA-256、派生 surface）冻结到 Governor、direct HostCommand 与 approved-plan admission；Plan 首次 admission 和 direct admission 均先以 base request/session/principal 只读预检 parent Governor binding，child grant 派生再复验。真实 SQLite admission/replay 与 principal-drift 零写入已覆盖该链。该证据不覆盖 Delegate 内部 dispatcher authority，也不等于通用候选搜索或 MCTS 已进入 Host。
- D10 用 composition-owned `GovernedDelegateDispatcher` 替换 Host Delegate 对 legacy global dispatcher 的回落：只注册 `pentest_agent` 这个 effect-free Domain Pack/SecureRun handoff，冻结 command/request/session/principal/root WorkOrder/graph revision/node/attempt/lease/owner/fence/parent+node grant；调用前重验 node grant，Attempt/Lease/Fence 仍由外层 durable worker 保持。它不创建 runtime gateway、不写 legacy `skill_exec_log`。`intent_agent`、`translate_agent`、`vision_agent`、`repair_agent` 与未注册 target 在 direct/plan admission 前失败关闭。真实 SQLite direct + plan delegate/replay 已证明；这不是通用 delegate 平台，也不证明预算硬限、完整 Review canary 或候选搜索。
- D12 先修复 PlanningSearch 的错误影响边界：`PentestPlanningSearchAdapter` 已移除“校准阈值通过即可选择 L3”分支，始终执行 L1/L2 baseline，并仅把 L3 作为 shadow evidence 记录。内存 `PentestMCTSCalibrationLedger` 不能跨 run、不能提供反事实 outcome，也不再拥有任何 production influence；dynamic successor、durable calibration、promotion proposal/gate 仍是 NE-02 未完成工作。
- D13 建立领域无关的 0105 `CalibrationPrediction/Outcome/Repository`：prediction 和 outcome 分表 append-only，canonical JSON/digest、prediction identity、candidate bind、outcome identity、重启 replay、physical-schema semantic verification 与 SQL immutable trigger 均 fail closed。Pentest adapter/loop 可从显式 kernel context 注入 repository，把 virtual state、baseline/shadow path、trace、预算和纯虚拟 counters 写入；候选 target 会在 fingerprint 前先收口到授权 scope。注入库的 busy/schema/drift 失败只产生 `mcts_durable_calibration_unavailable` 可观察事件并跳过可选 shadow evidence，不阻断既有 L1/L2 baseline。现有 Pentest execution record 只投影为 `UNVERIFIED_RUNTIME_RECORD`，不是 Task/Run receipt，默认无注入路径仍是内存 compatibility ledger；Research/Code、dynamic successor、receipt-bound counterfactual 和 promotion gate 仍未完成。
- Workspace 底层资产已推进到 `PARTIAL_W2_NO_CHANGE_PROMOTION_WIRED / GENERAL_WRITE_INCOMPLETE`：D0/W0/W1 继续有效；认证 Internal Agent admission 会覆写 caller workspace 为 graph epoch 派生 ID，并在显式稳定 HMAC secret 下冻结 source HEAD、创建独立 namespace worktree、实例/PID lease、heartbeat、handoff/takeover 与 cleanup continuation。下一次 runtime activation 已能从受治理 Internal 单节点 sealed checkpoint 恢复 projection 并继续 clean/no-change accepted promotion；终态 worker 可精确处理 sealed abandon/cleanup。Artifact namespace 已有逐层 link/reparse/junction 拒绝与关键 I/O 前重验，但不是 handle-level 原子 TOCTOU 防线。它不支持一般写节点；周期 survivor continuation、crash-left unsealed partial cleanup、failure/cancel/budget claim closeout、general `settle_worker` crash bundle、OS/container/tenant隔离、逐操作fence/强杀、Domain/Skill/Codex runtime与canary仍缺；
- Composition D1～D8 的 app-local route、Obs lifecycle、Host 五合同 lineage、Memory/VTS/Life Body cold read、Direct Skill snapshot 和 durable embodiment 边界继续有效。D7b 的 Life Body 只聚合同一 app 四个 durable roots 与 canonical ledger 有界 lineage，失败写 typed `SOURCE_UNAVAILABLE`、不保留 content/private body，源未变化不追加 journal；21 个 producer 的声明 source-mode 为 `6/3/8/4-auth/0-unavailable`，声明不等于运行成功。D8 的 canonical scope/runtime lease、持久 Edge credential、ActionPreview 与 `IN_DOUBT` exact reconciliation 语义不变；真实设备仍无证据。Host lifecycle 由每个 `create_app()` 的 `HostLifecycleContainer` 拥有；factory/start 完成后才发布，Activity factory 失败不发布 Mode，closing fence 阻断 `begin()`，ResourceManager startup sample 使用唯一 owned loop。Chat/Input、Tool、Scheduler、Guardian heartbeat 和 snapshot 读取 app-local monitor/resource/mode 实例，Obs 只读不懒启。`HostHardStateOwner` 同属该 app：只接受 bootstrap mode，运行期不把环境变量当真相，Chat/Tribe/Lifecycle/Scheduler 共享同一 immutable owner；Obs Life Body 只读 durable projection。Tribe agents/bus 保持 process-global，lifespan owner fence 仅允许一个 live Host 并令第二个 fail closed，不构成并发 multi-app 隔离；
- ToolBroker/MCP direct/callback authority 已退役；process `get_tool_broker` accessor、`tool_broker` slot 与 dead `HostLifecycleToolBrokerProvider` 为 0，`host_brain/tools/broker.py` 是唯一 app-local 生产构造点且 constructor count=1。#89、ReAct、Visual 与 HardState 的 45/123/51、43/120/49、41/117/47、40/116/46（Container 52/3/48）均为 commit-bound 历史；本切片另删除 `query_projection_store`、`obs_metrics_collector` 及三个 accessor，当前 Container 为 50 fields/3 locks/46 mutable refs。process `get_hard_state`、`get_endocrine_state`、`set_sys_mode` 与对应 slot/lock 也为 0。full architecture static 当前为 `PASS`、`failed_gates=[]`；
- 底层 Host source、Chat/Delegate 其他 authority、普通 Agent Tool authority、真实 workspace-bound Tool/MCP/Visual executor、其余 process-wide locator、heartbeat cache/client、Tribe bus、shared status、HardState auto-rules/last-interaction、剩余 34 个 Operator write adapter 与完整 Worker 对象图未完成；完整 Query Plane、NE-01 和全局 Composition 不得封板。

Lane A 已完成；Lane B 当前到 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW`。B4/0090 与 B5A/B5B/0091 语义继续有效；B5C 已有 0092～0099 分范围 durable authority。Budget 高竞争唯一 winner、单节点 post-budget 检查点接管、Internal 启动期 sealed checkpoint continuation、exact rejected terminal bundle 与 0099 窄历史终态 reconciliation 已有证据，但不构成通用恢复。Operation、Effect 与 terminal reconciliation 的一般路线仍为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`。Lane D 已到 `PARTIAL_W2_NO_CHANGE_PROMOTION_WIRED`：clean/no-change promotion与终态sealed abandon/cleanup已有窄证据；周期 survivor continuation、crash-left unsealed partial cleanup、failure/cancel/budget claim closeout、general `settle_worker` crash bundle以及一般write/OS/container/Domain/Skill/canary仍缺。Composition 也未完成。因此 Lane B、NE-01 和完整架构都不得封板。

目标：先修发动机底座，保证所有生产入口真的可执行，再删除第二 authority。

#### Lane A：Scheduler / Internal contract repair

状态：`COMPLETED`（Scheduler 与 Internal 均有首次真实 admission/replay 证据）。

- 将 `AgentWorkOrder` 转为显式 `WorkOrderV3`；
- 每路必须有 `BudgetPolicy`、node-bound grant、Review runtime、Verification、Acceptance 与 Receipt；
- 增加首个真实 admission/integration，不允许只测 startup；
- Host Input/Gateway/Scheduler/Internal/Operator 的 route matrix 全部 fail closed。

#### Lane B：Durable DAG completion

状态：`PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / LANE_IN_PROGRESS / PRODUCTION_OFF`。0102/GR06 canonical Node Acceptance 的应用 Store UoW 已统一，public Graph create/transaction insert 与 standalone Effect stage 已删除，应用 Python 写 authority 为 `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED`；但 SQLite 仍允许 orphan/raw SQL 且无新 migration/trigger，因此同时保持 `SQL_DIRECT_BYPASS_PRESENT`。Cancellation、Operation、Effect 与 0099 terminal reconciliation 均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`。Budget 唯一 winner、单节点 post-budget 安全接管、ACCEPTED 后 Effect 原子补写，以及 0099 窄历史终态 authority 已证明；尚缺 general/batch/pre-budget recovery、现实 resolver、目标部署 inventory/remediation、部署 key lifecycle 与真实 canary，不能宣称 Lane B 或通用恢复完成。

本切片测试证据保持分批原貌：authority/structure `19 passed`；HostCommand admission `1 passed`；NLE、Operation 与 Heartbeat `77 passed`；Acceptance Effect Store `15 passed`；Parallel/Settlement/Atomic `45 passed`；Cancellation 首跑 `29 passed + 1 fixture failed`，修正 fixture 后该 case `1 passed`；Integration 首跑 `46 passed + 3 fixture failed`，修正后 3 个 cases 通过；Terminal classification `32 passed`。第三个 integration fixture 的 `node_risk_level` 缺失可在干净 `origin/dev` 复现，修正只补齐当前必填测试输入。全部 fixture 修复/重命名后，authority、两份结构合同、两类 recovery、代表性 cancellation、durable artifact baseline、dispatcher semantic order 与 HostCommand admission 的最终关键回归为 `26 passed in 35.33s`；它不覆盖首轮失败记录。不得把这些批次相加伪造成单一波次总数；NE-01 wave integration 仍为 `NOT_RUN_PER_WAVE_POLICY`。TaskStore 切片当时 full static 为 `FAIL [1,6,10]`；Gate 10、Gate 6、Gate 1 与随后收口的 Gate 11 均已通过，当前 fresh full architecture static=`PASS`、`failed_gates=[]`。这不改变 NE-01 仍为 `IN_PROGRESS`。

- 维持 0094 scanner/worker、Operation/Effect 签名对账与 0098 append-only/late-writer/replay 故障矩阵；对每个目标部署运行真实 legacy inventory gate、补齐 deployment key/credential lifecycle 并完成 canary，此前保持 OFF；
- 持续守护 0095 revisioned Graph Outbox：歧义历史回填必须整笔失败，Graph/non-Graph trigger/readiness、multi-revision marker 完整扫描、revision 内顺序/不同 revision 独立推进、Runtime Event 与 Operator exact revision 必须保持通过；
- 守护 0099 历史终态合同：单次启动盘点最多 10,000 个 terminal Graph；只允许唯一 graph-scope Budget 进入精确修复，完整校验 Graph/Node/Review/Attempt/Lease、0090 唯一 decision、0091/0092 canonical authority 及 Attempt failure Outbox payload refs；child/missing/multi/不一致事实 quarantine；未决 case 围栏 graph-wide 普通 Graph/Budget mutation，仅 0099 UoW 可 bypass；旧 case 只可形成最终收敛到可信 proof 的 supersession chain；保持 canonical 3 table/4 index/10 trigger SQL readiness、Python codec、多进程 system proof 稳定性与批处理 partial-side-effect 语义；
- 守护 0096 Review continuation 故障矩阵：Review mutation 与 enqueue 原子性、过期扫描、claim heartbeat/takeover/stale fence、surface 精确路由、PRE null Attempt、POST no-rerun、长等待 child-grant renewal 与 shutdown quiescence；不得把它混同于 0092 cancellation reconciliation；
- 守护统一的 0102/GR06 Node Acceptance UoW、Attempt-bound Outbox、effect delivery exact-artifact guard、schema/asset readiness、startup legacy inventory 与 successor exact-input 矩阵；public standalone pre-stage 与 low-level Graph create 已迁除，下一步必须设计数据库层 orphan/raw SQL fence，并以新 migration/trigger 和回滚证据消除 `SQL_DIRECT_BYPASS_PRESENT` 后才能提升物理单写定级；
- 保持 0093/0095 故障矩阵：并发 owner、takeover/stale fence、per-revision 顺序；deterministic CREATED/ACTIVE 回填、自定义 producer 整事务失败、升级 ID/key 稳定；revisioned payload/legacy replay-only ref；payload/source-index readiness；append-success/complete-loss exact replay 与零重复投影；created_ts=0 对 current/legacy candidate 的受限兼容；PlanningRecovery opt-in 同事务实际 terminal ref、旧版或替代 Store 缺失可选扩展时的兼容 fallback，以及默认热路径零 Outbox 扫描；multi-revision marker/Event/Review/Operator exact read；PENDING/DRAINED、inventory/ready 与热路径不重扫；
- 把现实 resolver 的可证明瞬态准备失败映射到显式 marker；禁止扩大到 reservation 后异常；
- 在现有 max-attempt/backoff/retry-budget 原子路径上补齐真实故障分类与运行观测；
- 持续守护已完成的 Budget 高竞争唯一 winner/`ALREADY_EXCEEDED` 合同；不得把它扩写成 concurrency 维度计量或 `DEGRADE/REVIEW` 已完成；
- 持续守护 0096 task-bound Review continuation、单节点 post-budget Operation continuation 与 ACCEPTED→Effect restart finalization，保证 worker/Effect 不重放；另行实现 general/batch/pre-budget recovery 和一般 recoverable Attempt 的统一在线 continuation；
- 并发、retry、cancel、lease takeover、crash recovery 均产生同一 lineage。
- 新生产 Graph 已切到 0090/0091 原子终态；继续完成跨进程、接管、续跑和故障矩阵后执行 production canary。`bounded_fanout_enabled` 在 canary 前保持关闭；atomic 可通过显式环境回滚为关闭，但这会使新 atomic admission 失败关闭，而不会回退为 legacy 成功语义。

#### Lane C：Legacy authority removal

- 在 `PersonaEngine.apply_growth_delta()` authority 边界中央 fail closed 所有非迁移调用，并对 stage、milestone、interaction、emergent weekly 及后续新增调用点建立静态/运行时门禁；NE-01 只阻断旧写入，不在此波偷偷建立第二套演化系统；
- 删除 Executive `_execute_explicit_skill_dag` 生产路径；
- 删除 `LegacyRuntimeTaskRegistry` 写入和全局 accessor；
- `task_progress/recent_turn` 改为 projection/read route，不再借 Executive 执行；
- 删除 cycle→忽略依赖串行的兼容行为。

#### Lane D：Composition 与 workspace

- D0/W0/W1 已完成既定围栏；W2 在 Internal Agent 单路线接入 graph-bound Git worktree、heartbeat/handoff/takeover 和 cleanup continuation，并保持缺 secret 默认 fail closed。protected-artifact 消费者现证明 clean/no-change accepted HEAD、0116 promotion、启动期 sealed checkpoint continuation与精确终态sealed abandon/cleanup，不证明一般 write promotion或一般恢复。PR #88 依次切断 `run_skill`/`dispatch_sub_agent` 与 Visual Agent route-local loop，但没有退役普通 ReAct/Agent Tool authority。仍需周期 survivor continuation、crash-left unsealed partial cleanup、failure/cancel/budget claim closeout、general `settle_worker` crash bundle、受控container/OS tenant、进程级取消、一般写节点、Domain/Skill全路线与真实canary，不能把 `DIRECTORY_FENCE + worktree` 写成完整隔离；
- mixed-file ceiling 与 allowlist 均未抬高。首批四个 authority 文件和第二批七个文件均按“生命周期 helper、纯 codec/contract、无独立 store 的 command mixin”边界完成拆分；fresh 全仓 Gate 已返回 `issues=[]`。这只关闭 mixed-responsibility 静态清单，`DurableDagSchedulingRuntime` 仍是临时 outer orchestrator，46 个可变 Service Locator 引用、一般恢复和生产 canary 仍未完成；full architecture static 当前为 `PASS`、`failed_gates=[]`，但不能据此宣称 NE-01 或完整架构闭环；
- 用 `HostApplicationContainer`、`ObsApplicationContainer`、`WorkerApplicationContainer` 等 typed scopes 替代模块全局 container；
- route/handler 只依赖显式 ports；
- D1 已完成 Canon/Tool/Bond 及其 OBS adapter 的 app-local ports；不得把该局部结果写成整个 OBS 或 ProcessServiceContainer 已去 locator；
- D4～D8 的 projection、五合同 source、Memory/VTS/Life Body cold-read、Obs-route locator 清零、Host 六个 GET 切流、Direct Skill authority-first 门禁、durable embodiment、terminal ActionPreview 与 Obs assembly 反向依赖清零继续守护。Host lifecycle 与 HardState 已 app-local，ToolBroker/MCP direct/callback authority 已清零；process `get_tool_broker`、`get_hard_state`、`get_endocrine_state`、`set_sys_mode` accessors 及其 container slots/locks 已退役，ToolBroker 唯一生产构造位于 app-local `host_brain/tools/broker.py`。两个无消费者的 Obs cache slot/accessor 也已删除，但当前仍有 46 个可变 service 引用及其余 process-wide locator、Tribe bus、shared resource status、Chat/Delegate 其他共享 authority 和不完整 Worker scope。这不证明物理设备效果，也不等于真实 Tool/MCP executor 已接线；局部切片不得写成 NE-01 完成；
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

当前已完成的基础切片仅限以下范围：D14 已把 `PlanningGoal`、`VirtualStateSnapshot`、动态 successor 合同、shadow workload 合同与 context-owned immutable compilation 接入公共内核。D16 已在 Code 域实现从冻结 opaque ref/digest 状态动态生成 `inspect → registered patch → unit/rollback` 的纯 catalog；selected inputs 必须重新对照 catalog，`virtual_model_ready=false` 时不暴露 L3 transition/reward，即 `MCTSMode.ALWAYS` 也不能绕过。context 在 initial/replan/recovery 中透传，且 `approval_ref` 必须绑定 parent governor grant。它没有打开 worktree、源代码、模型、工具、测试、provider、Task admission 或生产 influence；中间虚拟 state 的 Code replan lineage 也尚未设计。D17 在独立实验 adapter 中增加 private bare clone（`--no-local`、移除 remote、private hooks path）和同 commit 的 baseline/selected detached worktree；它要求 source 自有非链接/非共享 `.git`，其 local Git config 与 checkout content 是受信输入。Git 子进程清除继承 Git 配置、system/global config 和交互提示，关闭 `core.fsmonitor`，仅允许 `file` transport，并跳过标准 LFS smudge。arm 只能经 lower-case portable repository-relative 文件 API 读写，padding/case alias/`.git`/遍历/symlink 逃逸都会拒绝，opaque ref 也拒绝 host-path-shaped 值；ignored artifact 失败。D18 只在 D17 成功清理后，才生成路径无关的 `VERIFIED_CLEAN` 回执：private bare、双 worktree 与运行根必须已删除，受保护 source fingerprint 必须前后一致。0106 随后以独立、append-only SQLite ledger 持久化 workload registration、冻结 paired RunSpec、两臂 evidence/outcome、cleanup receipt 与 terminal experiment receipt；每段均 canonical JSON/digest 绑定、精确 replay、schema semantic verification，且 terminal receipt 必须同时拥有预注册、两臂和 clean receipt。D19 可把一个冻结 `python -m pytest -q <repo-relative-test>` 计划预注册进同一 workload：它在两条 detached worktree 中以 shell-free、workspace-root cwd、无 `PYTHONPATH`、关闭 pytest 自动插件加载的子进程执行，并在 arm evidence 中持久化无路径的命令 identity、exit/status、受限输出 digest/长度和耗时；只有该 adapter 真正拒绝的 paired `max_test_commands` / `max_wall_time_ms` 才是硬门禁。D20a 新增 ResearchTaskSnapshot 和动态 `query/quick-answer/primary/independent/cross-check/conflict/stop` catalog：它只看冻结的公开 source id/ref/content digest 与有限 authority/relevance/read-cost hints，且没有专用本机 locator、source-body、answer-key 或 verifier-decision 字段；constraints 拒绝这些命名 key，但任务文字不是内容级 DLP。只有 `virtual_transition_ready=true` 才提供 L3 virtual ports。与 planner 分离的 FrozenResearchCorpusLoader 只从可信实验根与 manifest 共同指定的 repository-relative regular files读取，并在稳定受信输入中前后检查 ADS colon/case alias/链接/reparse、文件状态、大小并在读完后核对 digest，公开 receipt 不含路径或正文。D20a 没有真实 Research WorkOrder/BrowserWorker 或网页抓取、模型调用、独立 verifier、paired receipt/cohort、成本/延迟对照或生产 influence，根与 manifest 也是受信实验输入而非 OS/tenant sandbox。D19 仍是同主机受信 workspace，不是 independent verifier、container/OS/tenant/filesystem/network sandbox；本地 source、pytest 测试及用户 site 依赖仍是受信输入，model/provider/tool/token/cost 未受管。它不接入 Host/TaskGraph/AgentExecutor，也没有真实模型、隔离 hidden oracle、历史 cohort 或 uplift。D15 已把 sealed governed `codex_runtime` 的 `ModelAttemptService` / 同源 `PromptRegistry` 端口接入 `AgentRuntimeExecutor`，但它只证明 read-only agent runtime 的受信模型端口可达。ReAct 的 `run_skill` 与 `dispatch_sub_agent` 已退出发现面并在低层执行前失败关闭；这不证明普通 `GovernedToolNode`、`AgentExecutor` 的文件/Web/浏览器或其他工具 authority 已退役。ReAct 模型调用仍未消费 `ModelBudgetGuard`，而工具提案编译为 `graph_revision + 1` 的正向链尚未实现；因此 D15～D20a 仍不是 Code 执行、Research 生产研究或 Code 规划切流。

#### Research 第一真实领域

- 使用真实只读网页/文件来源与 Research WorkOrder；
- 候选覆盖 query、primary-source、cross-check、conflict resolution、stop；
- verifier 计算 claim coverage、引用有效率、主来源比例、冲突消解、成本和真实延迟；
- 同预算比较当前 baseline 与 PlanningSearch，先 shadow，不影响用户答案。

当前进度：D20a 仅完成后续实验的纯规划和受信 readonly-input 机械边界；loader 对稳定输入做普通文件/摘要漂移检测，不是 hostile concurrent replacement 的原子 TOCTOU 防线，且尚未把 `ResearchTaskSnapshot` 与 manifest 的 corpus/source id/ref/digest/authority tier 精确握手，因此不证明独立来源。它不满足本节任一“真实”验收项，`research_production_planning_benefit=UNPROVEN`。D20b 必须先预注册同一 workload/profile/budget 的 baseline/search 双臂，再将 planning snapshot、manifest、每份 opened document 做 exact corpus/source id/ref/digest/tier handshake，独立运行、独立验证并保存不可篡改的 source/model/output/verifier/cost/latency 证据；无真实非缓存 provider receipt 时不得把 scripted test 计入收益。

#### Code 第二真实领域

- D16 的纯 catalog 只能作为前置 planner：其 `allowed_path_refs` / `forbidden_path_refs` 不是 live Workspace ACL，不能拿模拟 descriptor 代替 executor；
- D17 只证明 trusted-process private-bare 双 detached worktree 与 server-side lower-case repository-relative API 的实验机械链；它必须保持 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` / `network_isolation=NOT_ENFORCED`，不允许把本地 Git hardening 或窄 API 写成 container/tenant/OS/network 隔离、模型预算治理、hidden-oracle 安全或真实 uplift；
- D18 将该机械链的成功证据写入独立 0106 durable ledger：必须先以独立 append-only transaction preregister workload，再 preregister exact baseline/selected RunSpec；cleanup 后的 terminal-evidence transaction 才写两臂 verifier/diff evidence、`VERIFIED_CLEAN` cleanup receipt 和 terminal receipt。它只让实验事实跨重启可审计，不产生 winner、promotion、生产切流或 hard model/tool/test budget authority；
- D19 只允许 registration 已绑定的固定 `python -m pytest -q <repo-relative-test>` argv 在两条 detached worktree 内运行；它实际拒绝 paired test-command/wall budget 超限，要求 `PASS ↔ exit_code=0`，并持久化 exact run/arm/plan/budget-bound 的 path-free command receipt，terminal replay 再核验两 arm aggregate bound。该 child 不是独立安全域或 hermetic Python：没有 network/container/OS/tenant/filesystem/process-tree/CPU/memory 隔离，APPDATA/user-site 仍是受信输入，也没有模型、provider、工具、token 或成本预算；专用 experiment service 仅在两 arm PASS 与 `VERIFIED_CLEAN` 时写 terminal receipt，crash cleanup recovery 仍未实现；
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

当前生产切流实现：0111 `world_observation_journal` 以 workspace/subject head 原子分配 revision，写入必须同时通过 canonical source event 与真实 Host lease；显式 expiry transition 处理 TTL，Resolver 重开 captured head 并校验 digest chain 和物理列/JSON。Host ASGI lifespan 在取得 runtime scope/owner/fence 后构造唯一 app-local Runtime Context composition；生产 observation application 只采集服务端 principal、Bond/Emotion 投影和 composition-owned environment，不允许请求 body 选择 World 或 Scene。`SceneResolver` 从重开的 World、Task、Guardian、关系与时间推导结果，Surface、Proactive 和 Task admission 消费同一 `scene revision/digest`。生产 `IntentAuthorityPort` 重开 HostCommand、WorkOrder、TaskGraph、Scene、action 和 Grant，`DurableIntentAdmissionVerifier` 先持久化完整节点准备集再封口 graph-level receipt set；输出始终 restrict-only，`BLOCK/REVIEW` 失败关闭，Review continuation 绑定 Task Runtime 的 durable ReviewStore。

高风险 Skill、Delegate、Scheduler/Proactive 与 Acceptance Effect 使用 lifespan-owned Action Preview authority：receipt 只在重新打开 canonical Command/WorkOrder/TaskGraph、Attempt/Lease、parent/node Grant、ContextPack、Scene 与 Guardian 后签发，并在执行点再次重开；derived terminal 还递归重开 origin Attempt/Lease，受 typed descriptor 的 exact scope/args 投影限制。Scheduler Proactive 以 typed proof 和固定 request ID 贯穿真实 Edge push，遗留 EdgeCommandPusher 无生产 issuer 时保持隔离且不能通过注入关闭门禁。Acceptance surface 只放行其 effect 精确绑定的最小文本，`raw_extra`、媒体、二次回复及 voice/performance/avatar 变换不能借同一 receipt 执行；公开具身 submit/enqueue 无 exact proof 时在端口前失败，durable outbox claim 后仅私有 submitter 可执行。终端正向覆盖 Acceptance surface Gateway 与 Scheduler-derived Life Edge/Gateway/VTS/MateEngine；旧 WorkOrder/effect、reply-performance/Gateway voice/Avatar 直通和尚未进入 Governed Operator TaskGraph 的 37 个直写命令隔离失败。这是安全封口，缺正向 TaskGraph 路径仍留给 NE-01。Guardian 是 Host 生命周期内 health/heartbeat 和已切流高风险前门的 app-local durable owner；Host lifespan 不再构造、绑定或解绑进程共享 MCP broker。旧 degradation 仅保留显式兼容 adapter，不再是这些生产前门的默认 authority。0113 为 Host Service Catalog 建立 append-only operation/receipt/head 与 coordinator fence，生产启动默认以 ENFORCE 为目标，但 fresh DB 必须逐级真实启动 AUDIT→SHADOW→PREFLIGHT→CANARY→ENFORCE，并关闭中间 listener。Pending health 必须 exact 绑定 operation/snapshot/stage/candidate，`REGISTERED` 崩溃按同 operation ID 恢复，stop failure 保持待对账而不伪造 rollback；非法 event/status、terminal-first、stage/rank 或 runtime-scope/activation identity 漂移、rollback-ref replay drift、stage skip/regression 和失去 Host identity均由 Store 与 SQLite 双重拒绝。

NE-03 已按边界合入，当前波次为 `NE-01_IN_PROGRESS`，不得越级为整体闭环：首两批职责拆分与 risk-level 断点修复不改变 atomic/fan-out/cancellation 的默认关闭状态。Tool/MCP 切片的 commit-bound 历史是 Obs locator 51/163→47/152、删除 process-owned MCP accessor/slot 后 47/151、再退役 ASGI/http_hooks Tool seed/route locator 后 47/147，Container 当时为 59 fields/6 locks/52 refs；这些数字不是当前库存。Operator 当前将 `scheduler.task.trigger`、`sys_mode.switch` 与 `task_terminal.reconcile` 三条命令接入 governed path。终态修复由 Secure Ingress 验签后写入 0119，通用 TaskGraph 只带 ref/digest；风险 4 强制 durable Review，Acceptance dispatcher 复验 Action Preview 后，Accepted handler 才重开签名命令并调用 0099 authority。Outcome Journal v2 必须精确复验 checksum chain、claim/audit/command/scope/body/domain/continuation/terminal refs，缺失或篡改均 fail closed；`RECOVERY_PENDING` 不得提前封口，`NO_CHANGE` 不得伪报副作用，Review `CANCELLED` 必须有 durable cancellation authority。34/37 个旧命令仍在 handler 前阻断。HTTP `/tools/execute` 也已进入 TOOL Secure Ingress、不可变 TaskGraph、durable Review、node-bound grant、Budget/Attempt/Lease、exact binding、app-local governed execution port 与 Verification/Acceptance/Receipt；0114 扩展 Worker Projection 的合法 route。这个正向证据使用真实 SQLite 和显式 safe-local 测试 adapter，不代表生产 Tool 已启用：生产 seed 的 governed executor 数仍为 0、health 为 `FAIL_CLOSED_NO_GOVERNED_EXECUTOR`。

本波次新增 `scripts/ci/check_delivery_metadata.py` 作为本地交付门禁：只审计 `origin/dev..HEAD`，阻断 feature merge commit、非中文规范标题、PR 字面量 `\\n`、固定章节缺失/乱序，以及 commit subject、PR 标题或正文中的无边界整体完成声明。完成声明的否定词按同一 scope 的局部 clause 归属，不能用同一行另一阶段的“未完成”放行，例如 `NE-01 完成；NE-02 未完成` 必须失败；`Full architecture static Gate PASS` 只作为显式静态 Gate 范围内的测试结果，不提升整体架构状态。它不修改或替代 GitHub Actions；账户额度不可用时继续如实记录 `REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。每个 commit 的本地证据仍写入忽略目录 `artifacts/local-gates/<commit-sha>/`。

PR #88 第一提交只让 ReAct 的 `run_skill` 与 `dispatch_sub_agent` 退出发现面，并让注入的 legacy ToolSpec 在低层 executor 前以 `REACT_TOOL_GOVERNED_ADMISSION_REQUIRED` 失败关闭；第二提交另行退役 `/v1/agent/run` 的 route-local Visual Agent loop、raw client 与 legacy Action Planner 模型入口。普通 `GovernedToolNode → CapabilityBroker`、文件/Web/浏览器/Memory/Emotion 与其他 Agent Tool 路线不在这两个切片的退役结论内。#89、ReAct 与 Visual 的 named/all-form/Container 快照 45/123/51、43/120/49、41/117/47 均为历史；HardState app-local 切片后的 fresh inventory 为 named 40 modules/116 names、all-form 46 modules，Container 为 52 fields/3 locks/48 mutable refs。full architecture static 当前为 `PASS`、`failed_gates=[]`；这只证明静态 Gate。Operator 当前为 3/37 条正向路线；workspace-bound 真实 Tool/MCP/Visual executor、HardState auto-rules/last-interaction 持久化、一般 pending-operation reconciliation、其余 34 个 Operator 入口和全局 Service Locator 仍未完成。

本地证据保留 Tool/MCP 收口批次、第一提交 ReAct Skill/Delegate 及相关回归 213/33/27/89 项，以及第二提交 Visual Agent 回归 61/89/7 项；这些定向测试不等于波次集成或生产 canary。证据写入忽略目录 `artifacts/local-gates/<commit-sha>/`，远程 CI 继续记录 `NOT_RUN_ACCOUNT_QUOTA`；Windows 后台优雅停止、生产多实例 canary 与 NE-07 真实 Docker 全栈均未验收。

原始 `ToolBroker.execute()`/`execute_bound()` 均固定拒绝；Frozen Tool Worker 的正向 port 先由 `TaskStore` 在单个 SQLite 读事务内原子复验 Graph/Node/Attempt/Lease/Reservation 与 owner/fence/expiry，随后分阶段 fail-closed 复验 node grant 和 exact binding；所有检查通过后，`before_handler` 才在实际 handler 前写入 `PENDING` dispatch marker，三段并非一个原子区间。`MCPAdapter` 与 MCP Skill bridge 已分别停止 callback 登记、默认关闭并清除 stale `mcp::*`；process-owned MCP accessor/slot 已删除，未进入 ASGI 的兼容 `MCPToolBroker` 只能拒绝，旧执行 authority 使用量为 0；MCP registry 只读发现仍保留。process `get_tool_broker` accessor、`tool_broker` slot 与 `HostLifecycleToolBrokerProvider` 现也为 0，ToolBroker 唯一生产构造点是 app-local `host_brain/tools/broker.py`。普通 ReAct 的 `GovernedToolNode → CapabilityBroker` 是独立待审路线，不能由旧 ToolBroker/MCP direct/callback authority 清零外推为全部 Tool authority 已清零。Tool seed readiness 当前早于 governed runtime activation；生产 executor=0 使其暂时不可能误报 `READY`，但启用首个真实 executor 前必须把 activation 纳入同一 readiness 状态机，此 P2 尚未修复。

ReAct 切片只让 `run_skill` 与 `dispatch_sub_agent` 退出发现面，并让注入的 legacy ToolSpec 在低层 executor 前以 `REACT_TOOL_GOVERNED_ADMISSION_REQUIRED` 失败关闭；Visual 切片只退役 route-local loop、raw client、Ledger writer 与 legacy Action Planner 模型入口。文件/Web/浏览器/Memory/Emotion、旧 Graph ReAct、`AgentExecutor` 与其他 Agent Tool 路线不在这两个切片的退役结论内。#89、ReAct 与 Visual 的 45/123/51、43/120/49、41/117/47 是历史快照；HardState 切片后的 fresh inventory 为 40/116/46，Container 为 52 fields/3 locks/48 mutable refs。full architecture static 当前为 `PASS`、`failed_gates=[]`；这只证明静态 Gate，workspace-bound 真实 Tool/MCP/Visual executor、HardState auto-rules/last-interaction、一般 pending-operation reconciliation、Scheduler 其余管理面、其余 35 个 Operator 入口和全局 Service Locator 仍未完成。本地新增 ReAct Skill/Delegate 相关回归批次 213/33/27/89 项与 Visual 回归 61/89/7 项；HardState 定向集合单独记录为 210 项，D7b 为 7 项，operator/focus/runtime projection 为 29 项，三者禁止汇总相加；Tribe/legacy mode 等子集另有包含或重叠，也禁止相加。这些定向测试不等于波次集成或生产 canary。Obs 宽组在 60 秒上限内未结束，不计通过或失败；证据写入忽略目录 `artifacts/local-gates/<commit-sha>/`，远程 CI 继续记录 `NOT_RUN_ACCOUNT_QUOTA`；Windows 后台优雅停止、生产多实例 canary 与 NE-07 真实 Docker 全栈均未验收。


同一 rebase 后的 Host lifecycle 切片又统一 Chat/Input、Tool、Scheduler、Guardian heartbeat 与 snapshot 的 app-local monitor/resource/mode 实例，并以 process-global exact owner fence 令 Tribe 同一时刻只允许一个 live Host、第二个 fail closed；不提供并发 multi-app Tribe 隔离。Activity factory failure、closing/begin 与 ResourceManager single-loop 边界已有定向证据。它原始 commit-bound inventory 为 45 modules/124 names、51 all-form consumers、56 fields/4 locks/51 refs；#89 Gate 11、ReAct 与 Visual 的 45/123/51、43/120/49、41/117/47 继续作为历史，HardState app-local 切片后的 fresh 值为 40/116、46、52/3/48。Host lifecycle 定向回归为 143 项，结构/单时钟批次为 22 项，两批重叠 4 项、去重 161 项；HardState 定向集合为 210 项、D7b 为 7 项，operator/focus/runtime projection 为 29 项，三者禁止汇总相加；Tribe/legacy mode 等子集另有包含或重叠，也禁止相加。波次 integration 仍是 `NOT_RUN_PER_WAVE_POLICY`。这不改变 atomic/fan-out/cancellation 默认关闭，也未清零 Operator 34/37 旧入口、真实 Tool/MCP/Visual executor、其余 process-wide locator、heartbeat cache/client、Tribe bus、shared resource status file、HardState auto-rules/last-interaction 和 Worker 对象图。Windows 后台优雅停止、生产多实例 canary 与 NE-07 真实 Docker 全栈均未验收。

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
- `SkillActivationEvidenceV1` 将 Lab report、Review、SBOM、signature、sandbox、rollback 和 maturity 绑定到 append-only 0109 ledger；
- production IntentDecisionEngine 默认启用 restrict-only maturity policy，缺失或伪造 scorecard 直接最高风险；新能力最高 L1/L2；
- promotion/degradation 由 durable evidence 与 Review 决定；
- 退役旧 Capability Forge 平行 routing authority，只保留 proposal source。

当前进度：确定性 `SkillDraftV1`、0109 evidence/receipt ledger、fenced activate/degrade/rollback service 和 active-only content-addressed registry 已落地；外部 provider sandbox canary、真实 Review/实验报告生产接线和 bundle import 仍未完成。最终完成条件仍是：至少一份 Markdown 和一条 accepted trace 各生成一个 draft；隔离验证、拒绝、staged activation、降级和 rollback 都有 receipt；未经审批自动升级为 0。

### NE-06：Experience Feedback、Knowledge OS 与 Identity Migration

目标：完成“经验可回流、知识可治理、人格可迁移但不自动漂移”的长期闭环。

#### Feedback

- 统一成功/失败/用户拒绝/工具可靠性/表达偏好来源；
- 只生成 `FeedbackProposal`，绑定 trace、outcome、置信度与 rollback；
- 修复 Prefrontal → `on_interaction_cycle()` 参数签名断链，并让异常进入可观测 evidence；
- 用 proposal-only adapter 替换 NE-01 已封口的 stage、milestone、interaction、emergent weekly 及其他 `apply_growth_delta()` 直接 writers；
- 删除其余吞异常的局部反馈链；
- proposal 进入 Lab、Review，再更新版本化 policy/maturity/knowledge。

当前进度：`FeedbackProposalV1` 已补齐 canonical digest/to_dict；0110 append-only ledger 已持久化 STAGED、Lab/Review APPLIED 和 ROLLED_BACK 事件，Prefrontal 的旧 `on_interaction_cycle()` 参数漂移已改为有界 `interaction_stats`。stage、milestone、emergent weekly 仍只产生候选，尚未全部改成 proposal adapter。

#### Knowledge OS

- 明确 Life Memory 与 Project Knowledge namespace；
- 知识条目记录 provenance、version、validity、confidence、conflict 和 decision refs；
- 支持去重、过期、冲突、promotion、deprecation 与项目摘要；
- 重大架构选择生成 ADR/Decision Record，不把 Canon 当普通事实库。

当前进度：0110 已提供 life-memory/project-knowledge 双 namespace 的 proposal→Lab/Review→active/deprecate 事件和只读 active projection，并保留同 conflict group 的 competing refs；旧 Memory/Markdown KB/Canon/Pentest 资产尚未完成 shadow compare 与读切换。

#### Identity

- release 主链真实生成和持久化 IdentitySnapshot；
- 建立 migration plan/executor、persona/relationship regression、用户 Review；
- 重大变化默认阻断，激活后保留前一 release rollback ref；
- 记忆正文和私密 thought 不进入 snapshot。

当前进度：`IdentityMigrationPlanV1` 已支持显式 no-change release，0110 已持久化 PROPOSED→ACTIVE→ROLLED_BACK 事件并要求 regression report 与 Review；现有 IdentitySnapshot/Review owner 尚未接入生产 activation。

完成条件：T-PERSONA-PROPOSAL-01 通过；至少一条真实任务经验走完整 proposal→Lab→Review→versioned apply；一个项目知识冲突被识别并处置；一次 identity no-change 与一次重大差异阻断/回滚演练通过。

### NE-07：Full Program Acceptance

目标：只运行一次完整最终验收，并明确外部环境限制。

进入条件：NE-01～NE-06 均 `CODE_COMPLETE`，没有已知 P0/P1，旧 authority usage 为 0；所有 Target Architecture Gate 仍是强制条件，早期 wave 允许 shadow 不等于最终豁免。

验收必须使用独立 acceptance 数据根和真实生产进程/镜像；mock、fake store、端口占位和 scripted replay 只能做前置测试，不能形成最终 receipt。`FullStackAcceptanceReceiptV1` 绑定 commit、run、配置摘要、镜像 digest、container/PID、健康和真实读写回执、重启/故障/性能结果，并排除 secret、用户正文和私密数据。

验收范围：

1. 全量 unit/contract/integration/e2e；
2. migration fresh/upgrade/backup/restore；
3. 构建 Monitor UI，并由真实 Nginx Monitor Proxy 服务构建产物；
4. 使用参数化 acceptance volume 启动固定版本/digest 的 Docker Neo4j、Chroma、Redis，逐项完成真实写入、读取、重启持久性和 namespace 清理；
5. 使用生产脚本启动 Host、Obs、Nginx Monitor、完整 Edge Profile 和 Unified Gateway；本地 Gateway webhook 必须贯通 durable inbox → HostCommand/TaskGraph → Acceptance/Outbox → durable delivery → reply receipt；
6. chat/skill/delegate/plan/scheduler/internal/operator 全路线 ledger 对账；
7. Research/Code/Pentest planning benchmark 与 calibration；
8. Evolution Lab、Skill compile、feedback、knowledge、identity golden paths；
9. security、privacy、performance、chaos、crash recovery、rollback；
10. 至少一个已配置真实非 mock 模型 provider；ASR/TTS/VRM/VTS/硬件可用则实测，不可用逐项 `NOT_TESTED`。

Docker、Neo4j、Chroma、Redis、Host、Obs、Monitor、Edge 或 Gateway 任一强制本地服务无法真实启动/读写时，结果必须是 `FAIL`，不能用内存 fallback。只有本地强制栈全部通过、仅缺外部 provider 凭据、平台账号或硬件时，才允许 `ACCEPTANCE_PENDING_EXTERNAL`。

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
4. 提交标题只允许 `重构/修复/功能/测试/文档/性能/安全/构建：中文结果`；`origin/dev..HEAD` 不允许 merge commit，同步 dev 必须 rebase。
5. PR 正文必须通过 Markdown 文件和 `gh pr create/edit --body-file` 提交，禁止在命令行拼接字面量 `\\n`。
6. PR 固定包含“目标、生产接线、删除或停用的旧路径、本地验证、远程 CI 状态、已知边界、风险与回滚”七节，并通过 `tools/delivery/change_metadata_gate.py`。
7. 每个代码提交同步受影响文档和机器状态，提交后立即推送；Draft 可 `--force-with-lease`，Ready 后禁止重写历史。
8. 不保护无价值 WIP；保护范围只包括用户明确资产、secret、模型、有效运行数据和当前任务外的真实业务改动。
9. 运行证据写入被忽略的 `artifacts/`；只有最终、脱敏、可复核的 machine receipt 才进入文档证据目录。

### 当前原子终态纠偏（2026-07-22）

正文中“atomic/fan-out/cancellation 均默认关闭”的句子属于对应历史切片的 commit-bound 状态，不代表当前生产对象图。当前新生产 Graph 已默认声明并执行 `ATOMIC_GRAPH_SUCCESS_V1`；0090/0091 分别原子收口失败/成功。`bounded_fanout_enabled`、durable cancellation 和 reconciliation 仍默认关闭，wave integration 与真实 canary 尚未执行。

## 7. 停止与回滚

仅在以下情况停止当前 wave：

- 发现会破坏用户数据、身份、密钥或不可逆外部副作用；
- 需要用户选择会显著改变产品语义；
- 同一外部依赖阻塞连续出现且无安全替代；
- 发现目标合同本身矛盾，继续实现会产生第二 authority。

代码难、测试慢、旧文档冲突或实现量大都不是停止理由。每个迁移必须 expand-first、读写可切、数据可备份；禁止 destructive down migration。Core Release manifest 的 `migration_head` 始终以仓库中实际连续的最高 migration 为准；当前分支已推进到 `0119_terminal_operator_signed_command.sql`（0115～0118 保留为连续历史资产），后续 NE 波次继续使用 next-free 编号，不得把旧的 0104/0110/0114/0115/0117/0118 固定值写回合同或测试。

## 8. 后续 Goal 定义

文档验收后创建的 Goal 应使用以下目标，不附带微任务数量：

```text
按 NEXT_GENERATION_EVOLUTION_EXECUTION.md v3.1 完成 NE-03、NE-01、NE-02、NE-04～NE-07：先重做 #84 并完成 World/Scene/Intent/Guardian 生产切流，再封板通用执行、真实 PlanningSearch 和受治理演化闭环；最终构建 Monitor UI，启动真实 Docker Neo4j/Chroma/Redis、Host、Obs、Nginx Monitor、完整 Edge 与 Unified Gateway 做全量网络、数据、重启、故障和回滚验收。全过程使用中文提交/PR、每提交同步文档并推送，远程 CI 额度缺失只记 NOT_RUN_ACCOUNT_QUOTA。
```
