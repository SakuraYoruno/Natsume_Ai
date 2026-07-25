受众：PROJECT
标题：测试与验收目录

# 测试与验收目录

版本：v2.4
维护日期：2026-07-25
状态：当前证据

## NE-01 Edge 指令首条正向 governed 切片

- 旧 `EdgeCommandPusher` 进程 owner、startup 注册与 Tribe/内部直推仍保持为 0；Obs `edge.command.push` 现在只允许 `target_agent=edge.somatic`、`command_type=emotion_state_command` 与 `emotion_tag/intensity` 白名单，其他 command 在 HostCommand admission 前失败关闭。
- 正向链为 `Secure Ingress → typed Governor → OPERATOR HostCommand → immutable TaskGraph → risk-4 Review → Attempt/Lease/Grant/Budget → Verification/Acceptance → edge.embodiment_enqueue → terminal Preview → HostEmbodimentRuntime durable outbox`。首次响应只证明 durable enqueue；物理 dispatch 与回执独立推进。
- 真实 SQLite 集成覆盖 Review 前 outbox 为 0、Acceptance 后唯一 `PENDING` 行、一次物理 dispatch、请求 exact replay 不重复入队/发送、unsupported command 不创建 HostCommand，以及 claim 失效后的 `IN_DOUBT → read-only receipt query → APPLIED` 且 submit 次数不增加。编译器测试另覆盖确定性 command/idempotency/authority identity 与 worker 伪造 runtime scope 拒绝。
- Acceptance handler、Operator runtime 和 Edge 绑定 helper 已拆成聚焦模块；`acceptance_effect_handlers.py < 1300`、`governed_operator_runtime.py < 1500`，fresh architecture static Gate 1～14 全部通过且未扩大白名单。
- 最终去重的 Operator/Acceptance/Preview/Edge 受影响集合为 `78 passed in 79.67s`；文档 authority、freshness 和交付元数据为 `27 passed in 10.53s`。目标模块 `py_compile`、机器状态 JSON 解析、`git diff --check` 与 fresh static Gate 均通过，`failed_gates=[]`。
- 集成中的物理端口是记录型 async adapter，不是真实 Edge/Gateway 进程。波次 integration、production canary、真实网络/设备与 NE-07 Docker 全栈仍为 `NOT_RUN`；其余 33 个 Operator 写命令继续阻断，NE-01 保持 `IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Runtime 合同分层当前切片

- `TaskRuntimeFeatureCanaryReceipt` 与 `HostLifecycleApplicationPorts` 分别迁入中立 `contracts/task_runtime_feature_canary.py`、`contracts/host_lifecycle.py`；旧 Runtime/App 路径保持同一对象导出。
- 合同测试同时锁定类型 identity、Persistence/Composition source import 方向以及既有序列化和生命周期行为。
- feature-canary、lifecycle、ASGI、HardState 受影响集合为 `74 passed in 21.79s`；fresh full architecture static Gate 1～14=`PASS`、`failed_gates=[]`，Gate 1 issues/SCC 为空，Gate 3 继续通过且未扩白名单。
- 波次 integration、production canary、真实模型与 NE-07 Docker 全栈均未运行；本切片只验收静态分层，NE-01 继续 `IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Research shadow ledger 职责拆分当前切片

- 对外 `SQLiteResearchShadowLedger` import、账本 UoW 与 `secrets.randbelow` 注入点保持不变；canonical JSON codec 和 typed projection/error 已分别迁入独立模块。
- 行数从单文件 1788 行收敛为 repository owner 1271 行、codec 512 行、models 108 行；没有新增或扩大 `core_architecture_policy.json` 白名单。
- Research ledger、paired execution 与 canonical persistence contract（含 split authority guards）的同一组回归为 `18 passed in 22.38s`。fresh full architecture static 从 `FAIL [1,3]` 收敛为 `FAIL [1]`，Gate 3 `PASS`，当前切片新增静态失败为 0。
- 文档 authority/freshness、交付元数据、release/coverage 合同为 `62 passed`；`compileall`、JSON 解析和 `git diff --check` 通过。
- 这是职责拆分证据，不是 Research provider、cohort、收益、promotion、NE-01 wave integration 或 NE-07 全栈验收；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Tribe 无上下文 fallback 退役当前切片

- 每个 production `TribeRuntimeScope` fresh 持有 `TribeStateRegistry`、`HostTribeRegistry`、`InMemoryTribeEventBus` 与固定的 23-Agent `TribeAgentCatalog`；production provider 是 Agent constructor，不再是 process singleton getter。
- Host bind 只建立 scope ownership；lifespan/background execution 与每个 HTTP request 通过 execution-local `ContextVar` 激活 exact-owner `TribeRuntimeContext`。旧 shared-state、cross-Agent getter 与 Chat/Input `TribeRuntimeAgentPorts` 只能返回该 execution 所属 scope 的 exact 对象/Agent。23 个 Agent backing singleton/setter、`_GLOBAL_REGISTRY`、`_HOST_TRIBE_REGISTRY` 与 `_EVENT_BUS` 已物理删除；无 exact context 统一以 `TRIBE_RUNTIME_CONTEXT_NOT_BOUND` 失败关闭。两个异步重叠 production scope 返回不同 Agent/registry，active execution 阻断解绑，解绑后的继承 context 以 stale reason 失败关闭。
- `DigitalLifeSupervisor` 不再写入 Limbic/DefaultMode 模块单例；它为一次验证运行显式构造、绑定、激活并最终释放自己的 Tribe scope。旧裸构造行为测试使用显式隔离 registry 或 `tribe_runtime_context` fixture，不能靠 pytest 复活生产 fallback。
- 排除已在 clean `5997daa2c` 基线复现的 7 个 Internal Runtime 既有失败后，覆盖 Agent/Tribe 的宽集合为 `659 passed, 4 skipped, 4 existing warnings in 53.83s`；包含 fallback、生命周期、Obs Organ 投影与 Digital Life harness 的最终集中复核为 `69 passed in 7.25s`。这些集合重叠、不得相加。基线 7 项继续作为独立待修缺口，不能冒充本切片回归。
- Tribe 级 execution isolation 与 fallback 物理退役已有本地机械证据，但完整 Host multi-app（其他 authority、外部 namespace、真实 server lifecycle）仍未做 production canary，EventBus 仍易失。因此本切片不构成 durable Tribe journal、全 Host 多实例封板、NE-01 波次集成或 NE-07 全栈验收。`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`，Ruff 在当前环境未安装，记为 `NOT_RUN_TOOL_UNAVAILABLE`。

## NE-01 生产 Service Locator 物理退役当前切片

- 生产 `process_service_container.py`、`process_services.py`、`process_environment.py` 与旧 `process_service_wiring.py` 已物理删除；`ProcessApplicationPorts` 只剩 frozen contract，ASGI/CLI fresh 构造后显式传入 Host root，Host builder 不再有 require/global fallback。
- 旧测试兼容逻辑只位于 `tests/support/process_service_compat.py`，由根 `conftest.py` 在 pytest 进程内安装；fresh 生产 ASGI 导入验证三个 retired module 名均不在 `sys.modules`。
- 清洁退出的分组结果：结构/启动/路由 `27 passed`；Host lifecycle/Authority `44 passed`；Obs projection `16 passed`；Obs fresh-process `16 passed`；模型/Capability/Memory 测试桥 `105 passed`；受影响集成分别 `1 passed`、`5 passed`。最终精选结构/owner 复核为 `59 passed, 12 existing warnings in 29.06s`，它覆盖前述部分集合，不累计。一次合并批次虽打印 `103 passed in 118.69s`，但外层命令最终 timeout=124，明确不计为 clean PASS。
- 文档/交付/仓库卫生组 `35 passed in 8.05s`，coverage/policy/repository 组 `40 passed in 45.17s`；两组测试文件互不重叠。`compileall`、三份 JSON、`git diff --check` 与 fresh 生产 ASGI 模块隔离检查通过。
- 该历史 locator 切片完成时完整架构静态 Gate 为 `FAIL [1]`；当前分支已通过 neutral contract 切片独立复跑为 Gate 1～14 `PASS`。Gate 2 的 Capability 与 app-local Model constructor 均精确位于 `HostAuthorityRuntimeOwner`。波次集成、canary、真实模型与 NE-07 Docker 未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Host Authority Runtime 上一切片

- 该提交当时的生产 locator inventory 为 named0/0/all-form0、Container8/2/5；现已被上面的物理退役切片取代。生产 Host 继续由 `HostAuthorityRuntimeOwner` 持有 canonical ledger、Episodic/LifeMemory、Model Runtime 与 Capability Runtime。
- Authority 切片的早期宽受影响集合为 `207 passed / 12 existing warning emissions`；最终选定集合为 `131 passed / 8 existing warnings`，相邻 FastAPI/Obs 为 `42 passed`，owner/model 的 `9 passed` 与主集合重叠，均不得相加。旧 HTTP skill smoke 因 fixture 已失效且真实 Chroma/Edge 不可用而失败，不作为本切片 PASS。
- `compileall`、三份 JSON、`git diff --check` 和文档 Gate 已通过，文档合同为 `45 passed`。统一 static scan 精确保留 `host_application.py → HostLifecycleApplicationPorts / emotion_context` 两条既有 NLE boundary；完整架构 Gate 为基线 `FAIL [1]`，Gate 11 通过，本切片新增失败为 0。
- 本切片不执行波次 integration、生产 canary、真实模型或 NE-07 Docker/前后端实栈；远程 CI 仍为 `NOT_RUN_ACCOUNT_QUOTA`，NE-01 保持 `IN_PROGRESS`。

## NE-01 上一检查点 UoW 切片（历史证据）

- owner 行数：Acceptance 1798→1262、ASGI 1527→1389、Scheduler 3537→2942；focused 模块保持原 writer、lifespan/task 和 repository UoW authority。
- 独立 Gate 1–14：`PASS`、`failed_gates=[]`、`各 Gate issues=[]`；Gate 3 `new_mixed_over_800=[]`，无 allowlist 扩张。`task_internal_agent_budget_rejection_store.py:967` 只拥有受约束的原子 mutation；444行 terminal resolver 为只读，93行 route helper 为纯函数。
- fresh 主证据：最终唯一去重的 10 文件受影响批次 `130 passed in 296.69s`、0 fail/skip。首轮同一批次为 `128 passed + 2 failed`：既有结构阈值捕获一个恰好 550 行的模块，exact reopen fixture/adapter 仍读取旧的 nested usage 形状；两项修正后整批复跑通过，未扩大 Gate/白名单。旧 87/35/3/62/46/4 与上一结构切片 265 项均为历史或重叠证据，不累计。
- 独立交付复核：`compileall` 与三份 JSON 解析通过；文档、交付元数据、coverage 与 freshness 合同最终 `39 passed in 10.59s`、0 fail/skip。该批次不与 71 项代码证据或历史 130 项批次相加。
- 状态边界：静态 PASS 不等于 NE-01 或完整架构完成；NE-01=`PARTIAL / IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 post-COMMIT Budget settlement 切片

- 最终唯一去重受影响批次为 12 文件 `71 passed in 99.50s`、0 fail/skip；前序 25、5、34、40、42、26 与 28 项 focused run 只作重叠历史，不累计。该批次包含 generic/Internal C-boundary、生产支持 route authority、防Internal降级、response-loss只读重开、unknown/conflicting Outbox fail-closed、后续 duplicate replay、terminal reconciliation、Runtime durable-corruption传播与 rollback 回归。
- 真实 SQLite 覆盖 Operation 已 `COMMITTED` 后 BudgetUsage 的 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING / CONFLICT` 四类。write-response + first-read 双重丢失首次只返回 `RECOVERY_REQUIRED`，不附加 Attempt/Node/Graph failure 或 Lease/Budget release，并保留可能已提交的 usage/`EXCEEDED`；单节点 Lease 到期后由更高 fence owner 精确重开或幂等补写前三类，最终为 `ACCEPTED` 或 `BUDGET_BLOCKED`，`CONFLICT` 失败关闭，worker 调用数保持 1。
- `BaseException` 进程控制路径直接传播且不写假终态；普通 reconciliation 异常在 committed checkpoint continuation 中固定进入无 mutation hold。
- 多节点/批次仅验证安全 hold，settlement recovery、NE-01 波次集成与生产 canary 仍未完成。

## NE-01 Chat / Host Input Tribe 端口切片

- 覆盖普通 Chat、Gateway、ASR、startup 与 lifespan：`TribeRuntimeAgentPorts` 由 Host composition root 传给 `InputRuntimeApplicationPorts`，`chat_handler` 与输入归一化不再直接读取 Thalamus/ANS singleton getter；缺少 Input/Tribe 端口时 fail closed。
- 当前定向命令覆盖 Chat streaming、Host Input、Gateway dispatch、startup warmup、lifespan shutdown 与 typed composition，结果为 `131 passed in 7.25s`。#103+#104 的 `196 passed in 21.08s` 为重叠历史，禁止相加。
- 边界：该历史端口测试本身不证明 Tribe Agent app-local；后续 catalog 切片已单独证明顺序 Host identity 隔离，但仍未证明并发多 Host、Chat Acceptance receipt、generic Delegate、NE-01 wave integration 或 NE-07 Docker/前后端实栈；远程 CI 继续为 `NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 pre-Operation Attempt takeover 切片

- 当前提交的受影响集按五个互不重叠的真实命令保存：Runtime/结构/在线接管 `184 passed in 289.38s`，TaskStore+Heartbeat `69 passed in 23.43s`，Acceptance/atomic `23 passed in 101.70s`，Cancellation/parallel `68 passed in 228.20s`，checkpoint/Budget/Intent `52 passed in 123.58s`；合计 396 项、0 fail/skip。架构 Gate 1～14 全部 `PASS`，`task_repository.py` 为 4585 行、低于未变的 4600 上限，takeover mixin 为 230 行，未扩大白名单。一次把后三组串行执行的命令在 364 秒上限被截断且没有测试失败结果，拆分后全部通过；新增 AST 门禁首跑 1 个 test-only 失败，最终补齐纯模块与精确早返回门禁后 identity/structure `12 passed in 1.32s`。旧 191+1、198/49/45 与 post-COMMIT 71/39 均保留为历史，不参与当前 396。
- 真实 SQLite 覆盖 Operation 预留前硬停机、同 deterministic Attempt 的 higher-fence takeover、reservation response loss、并发单 winner、ACTIVE/EXCEEDED shared Budget 与 canonical PRECHARGE 矩阵、accepted/failed/review sibling、Operation-present/operation-free 混合 live owner、Graph/Budget terminal fence，以及 post-reservation worker error/collision 的 recovery hold。
- live owner 固定返回 `GOVERNED_EXECUTION_IN_PROGRESS` 且零 worker/零 mutation；Operation absence 在 `BEGIN IMMEDIATE` 内同时按 reservation id 与 scope/fingerprint 重验，竞态出现只转 `RECOVERY_REQUIRED`。Operation 已预留后的异常不再写普通 Attempt/Node/Graph failure或释放Budget/Lease。
- 状态仅为 `CANONICAL_PRE_OPERATION_ATTEMPT_TAKEOVER_PROVEN / POST_RESERVATION_RECOVERY_HOLD_PROVEN / GENERAL_MULTI_NODE_RECOVERY_INCOMPLETE`。普通 Graph+Budget 终态和 success/effect 的完整原子 UoW、unknown effect、multi-node/batch settlement、wave integration、真实模型与 NE-07 全栈仍未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 TaskGraph SQL 准入 authority

- migration 0124 为 `task_runtime_graph`、`task_runtime_node` 与 `host_command_journal` 的未来 INSERT 安装三条 fail-closed trigger；连接必须注册准入函数并进入 exact `graph_id + graph_revision` scope，且 scope 仅由 `HostCommandRepository._admit_normalized()` 持有。
- 真实 SQLite 覆盖 plain connection 缺 UDF、unscoped/wrong scope、legacy source、late Node、raw HostCommand、trigger drift、中途迁移回滚、恢复后 exact retry、既有行不改写与 canonical replay。authority/HostCommand 定向集合为 `27 passed`。
- migration 0125 为 `task_runtime_acceptance_effect_intent` 的未来 INSERT 增加 exact 七字段 scope 和规范初始状态 trigger；exact replay 不进入写 scope，plain SQLite、unscoped/wrong scope、非规范初始状态与 trigger drift 均失败关闭。包含 Graph/Effect authority、迁移兼容、artifact/recovery/dispatcher 的最终受影响去重批次为 `99 passed in 245.03s`。
- 该证据只支持 `APPLICATION_AND_SQL_ADMISSION_AND_EFFECT_STAGING_WRITE_AUTHORITIES_SEALED` 的窄边界；其它 UPDATE/DELETE mutation、波次集成、production canary 与 NE-01 总体仍未完成，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## Governed Runtime

- `test_reports/gr02a_planning_search_shadow_benchmark_v1.json`
- `test_reports/gr06_universal_governed_runtime_live_acceptance_20260715.json`
- `test_reports/index.md`

## Pentest

- `pentest_agent_capability_map.md`
- `pentest_vulnerable_labs.md`

## NE-03 Runtime Context

#84 的本地证据覆盖必须与最终 commit SHA 绑定，且定向验证至少包括：

- 真实 SQLite 0111 World observation 并发 revision、显式 expiry、restart reopen 和 source/lease tamper；
- 真实 SQLite 0112 Scene/Guardian 1001+ cursor replay、digest/envelope 校验、双实例 fencing 与 append response-loss reconciliation；
- Host ASGI 连续 lifespan 构造/解绑，防止进程共享 broker 保留旧 Guardian；
- Surface、Proactive 和 Task admission 消费同一 Scene revision/digest，caller 伪造与上下文漂移均失败关闭；
- Intent 全节点 preparation/complete receipt set、`BLOCK/REVIEW`、durable Review continuation、duplicate/restart/exact replay；
- Action Preview 重开 Command/Task/Attempt/Lease/Grant/Context/Scene/Acceptance Effect；覆盖 caller 自证、future/expiry、旧任务隔离、derived descriptor 扩权、1000+ origin 定位、response-loss、37 个 Operator 直写、Acceptance surface Gateway、Scheduler-derived Life Edge/Gateway/VTS/MateEngine 的终端正向与篡改，以及旧 reply-performance/Gateway voice/Avatar 直通失败关闭；
- 0113 Service Catalog 以真实 socket/Uvicorn 逐级执行 AUDIT→SHADOW→PREFLIGHT→CANARY→ENFORCE；覆盖 operation receipt 状态机、target 停点/恢复、ENFORCE 重启、append 不确定性、stage skip/regression 和中间 listener 释放；
- 架构扫描证明三处生产 `build_scene_decision()` 调用与默认 module-global degradation reader 不再存在。

上述定向测试通过也只能证明 NE-03 边界，不能推导 NE-01/02/04‑07 完成。GitHub Actions 当前因账户额度未执行真实步骤，必须记录 `REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`，不得写 PASS。NE-07 的 Docker Neo4j/Chroma/Redis、Host、Obs、Nginx Monitor、Edge 和 Unified Gateway 真实全栈尚未执行。

当前证据快照由 `artifacts/local-gates/<final-head-sha>/` 绑定 #84 最终 HEAD，本地状态为 `LOCAL_ACCEPTANCE_PASSED_WITH_KNOWN_BOUNDARIES`：

- NE-03 wave：`239 passed`；既有受影响路径：`101 passed`；raw transport 私有化后回归：`36 passed`；Service Catalog 关闭加固回归：`6 passed`；
- 独立审计在静态边界扫描后复跑 Guardian/Catalog：`42 passed`，ActionPreview/Edge：`47 passed`；
- production CLI Host 在隔离 runtime 环境与 fresh SQLite 上真实完成 AUDIT→SHADOW→PREFLIGHT→CANARY→ENFORCE，形成 5 个 operation / 15 个 receipt；`/health` 返回 HTTP 200、`status=OK`、`service_identity=host.main/http`，Guardian durability=`READY`、fencing token=5；
- Windows Ctrl+Break 后 Host 进程和监听端口清零且没有伪造正常 lease release；旧 lease 到期后，同一 SQLite、端口与安全快照由 coordinator fence=2 接管，重新保持 ENFORCE/health OK，Guardian fence=6，ledger 累计 6 个 operation / 18 个 receipt。该结果证明 crash-fenced recovery，但明确不是 graceful-stop PASS；优雅停止脚本留到 NE-07 实栈复验；
- 部署多实例 canary 和 NE-07 真实附属服务全栈尚未执行；远程 CI 保持 `NOT_RUN_ACCOUNT_QUOTA`。

临时开发报告、运行反馈和逐次实测日志不再保存在 `docs/`。长期验收优先提交可重复生成的结构化证据。
