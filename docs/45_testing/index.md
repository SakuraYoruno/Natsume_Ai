受众：PROJECT
标题：测试与验收目录

# 测试与验收目录

版本：v2.14
维护日期：2026-07-26
状态：当前证据

## NE-01 Chat 本地事务投影屏障当前切片

- Graph Acceptance 仍在 canonical UoW 中生成有序 Conversation/Life/Delivery Effect；两个 Life handler 不再以 command receipt作为完成回执，而是要求 exact command 的完整 projection intent与 durable result receipt集合。
- foreground `surface.post_process` 使用真实 app-local AgentRuntime ports；assistant turn 或 LifeMemory admission失败会让 projection失败，不产生 barrier receipt，外部 reply Effect因此无法越过。
- 首次 response loss可重开 exact command 并执行自己的 intents；reclaimed reconciliation只读。缺失/失败/篡改/身份漂移保持 UNKNOWN，后台 worker完成 crash-left intent后才可继续。
- 当前受影响集合为 `106 passed in 117.58s`，0 fail，覆盖 exact intent claim、response loss、restart、failed projection、receipt tamper、ASGI injection与 GR05 真实 SQLite先本地后外发顺序。该数字替代此前 Conversation focused总数，不相加。
- 当前结论为 `CHAT_TRANSACTIONAL_SIDE_EFFECT_CONTROL_PASS / CHAT_ROUTE_INCOMPLETE`。外部 Gateway delivery仍为幂等 eventual，commit-bound acceptance、NE-01 wave integration、真实网络/模型和 NE-07未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Operation recovery case-bound reconciliation 当前切片

- 0131 为 0126 evidence 增加可空但不可拆分的 `recovery_case_ref/recovery_case_subject_sha256`，并以唯一 partial index 保证一个 recovery case 只有一份 evidence。存在唯一 current 0130 hold 时，不携带 exact case 身份、绑定过时版本或分类/动作矩阵不符均在 evidence stage 失败。
- UNKNOWN case 不允许 cancellation intent；CANCELLATION case 必须精确绑定 intent 且只接受 `COMMIT/RELEASE`。checkpoint Budget/authority conflict 无合法正向动作，继续 quarantine，零 takeover Lease。
- Governed Operator 路线将 case identity 冻结到 Secure Ingress、WorkOrder、TaskGraph、risk-4 Review 与 Acceptance Effect。Accepted handler 复用既有 Operation/Outbox UoW 后重新打开 exact hold；只有 `current=false` 才提交 v5 receipt。响应丢失与重启只重开同一 evidence/receipt，不重复 mutation。
- 最终受影响集合为 `129 passed in 146.40s`，覆盖真实 SQLite UNKNOWN reacquire、cancellation release、missing/tampered case、唯一 evidence、重启、trigger 不可变、checkpoint quarantine、compiler、Acceptance handler/dispatcher 与 migration/coverage contracts。部署 key/HTTP/network canary、一般自动归因、其它恢复类别、NE-01 波次与 NE-07 全栈未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Governed Internal Workspace 写入晋升当前切片

- 写 WorkOrder 没有 exact `workspace_execution_authority` 时仍在 Agent 执行前失败关闭；有 canonical Internal TaskGraph/Attempt/Lease/Workspace authority 时才允许进入隔离 worktree。
- Worker 完成后，Runtime 在活 Workspace fence 内捕获 frozen base 到结果树的 UTF-8 路径清单和 binary Git patch。两者作为可选但不可拆分的 protected-stage seal 成员，最多 1000 条路径、32 MiB；秘密路径、symlink/Git link、allow-list 越界和 seal 后漂移全部拒绝。
- Acceptance 重新计算 live patch，生成 parent 固定为 frozen base、作者时间固定的 deterministic commit；配置源分支不移动。0116 必须先写 `ACTIVE→ACCEPTED`，artifact 再晋升，最后写 `PROMOTED`；accepted artifact 留存 patch，终态 cleanup 才删除 worktree/临时 branch。
- 真实 SQLite + Git 三项 focused integration 为 `3 passed in 15.82s`：正向 promotion/exact replay/cleanup、post-seal tamper、commit 已生成但 lifecycle insert 回滚后新 Runtime 实例接管并复用同一 accepted HEAD。
- 受影响回归为 `78 passed, 3 skipped in 55.52s`，0 fail；3 个 skip 均为现有 Windows 环境无法创建测试 symlink，不是本切片失败。该证据只关闭 Governed Internal write，不证明 Domain/Skill/Codex、OS/container tenant、handle-level TOCTOU、NE-01 波次 integration 或 NE-07 全栈；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
- fresh Gate 3 首轮发现 `workspace_runtime.py` 增长到 1533 行。未扩白名单：不可变 receipt/reason/有界值合同下沉到 `workspace_runtime_contracts.py` 后，runtime owner 为 1493 行；Core Architecture Gate 1～14 全部 `PASS`。兼容回归首轮 69 项通过、4 项旧期望失败；其中两项改为明确验证“缺 exact authority 仍 proposal-only”，一项改为断言 WorkerProjection 物理不可变 trigger，一项同步新的 Workspace grant reason。四个失败选择器复跑 `4 passed in 18.61s`，不得将其与首轮 69 项相加伪造整批结果。

## NE-01 签名多节点 Operation 失败恢复当前切片

- 真实 SQLite 两根节点 Graph 使用 `ATOMIC_GRAPH_SUCCESS_V1`、并发上限 1；left worker 在 Operation 预留后失败，right 保持 `READY` 且从未取得执行机会。旧 Lease 到期后，受信 Ed25519 `RELEASE + NO_SIDE_EFFECT` 只在同一 UoW 关闭 exact Operation、Attempt、Node、Lease，并写四条对应 Outbox；Graph 与共享 Budget 在第一阶段必须继续保持 `ACTIVE`，不得由节点级 authority 抢占。
- 重建 Coordinator 后，既有 0090 Parallel Settlement Runtime 必须先读取 durable `FAILED` 节点，再原子提交 Graph `FAILED`、Budget `RELEASED`、append-only settlement decision 及 Graph/Budget Outbox；right worker 调用数仍为 0。第一阶段与第二阶段之间重启、重复扫描和提交响应丢失只能重开同一 durable 结果。
- Accepted handler 为节点级结算生成 v4 receipt，只绑定不可变节点失败事实；因此 0090 将 Graph/Budget 从 `ACTIVE` 推进到终态前后，receipt 字节身份保持不变。历史单节点 Graph 仍使用 byte-compatible v3 receipt 和六聚合终态，不得被新合同改写。
- committed reopen 必须验证全部历史 0090 decision、选中的最新 Graph 失败 decision、Attempt lineage 与 Graph/Budget Outbox。删除 0090 Budget Outbox、伪造 decision、版本漂移或重复非历史 decision 均失败关闭。
- 并发上限 2 的同一双根 Graph 还证明两名 worker 都在 Operation 预留后失败时，两个独立签名节点终态先完成；0090 可只选择一个失败节点作为 Graph 终结代表，但 decision outcomes 必须覆盖另一个失败节点，使其 receipt 在终结后仍可精确重开。
- 当前去重受影响批次为 `32 passed in 77.00s`，覆盖签名单/多节点失败、一般多节点 checkpoint 恢复、共享 Budget settlement、accepted receipt 与 0090 结构合同。该数字不与开发中的小批次相加；`compileall`、JSON、文档与静态 Gate 结果以本提交机器状态为准。
- 当前结论为 `SIGNED_MULTINODE_NODE_FAILURE_AND_0090_SETTLEMENT_LOCAL_PROVEN / GENERAL_RECOVERY_INCOMPLETE`。它不自动归因 unknown effect，不覆盖其它 failure/cancellation、Workspace 终态、部署 key/HTTP canary、NE-01 波次 integration 或 NE-07 Docker 全栈；远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 一般形状 checkpoint 恢复与 0130 hold 当前切片

- 真实 SQLite 五节点菱形图 `root → (left,right) → join → tail` 在中间并发 wave 的两个 canonical `COMMITTED` checkpoint 响应丢失后，由新 owner 按 immutable graph order 精确续接；只有两个前驱均 accepted 后才释放 join，随后释放 tail。八个并行根节点另行证明恢复器没有固定四节点上限；所有已测试 worker 调用次数均为 1。
- `0130_task_operation_recovery_hold.sql` 为 `UNKNOWN_SIDE_EFFECT`、`CANCELLATION_CROSSING`、`CHECKPOINT_BUDGET_CONFLICT` 和 `CHECKPOINT_AUTHORITY_CONFLICT` 建立 append-only case。写入与 `OPERATION_RECOVERY_HOLD_RECORDED` Outbox 同属一个 UoW，subject 精确绑定 Graph、Node、Attempt、Operation、graph Budget、取消与 active Lease 版本；exact replay 必须返回同一 case/outbox，UPDATE/DELETE 和绑定漂移失败关闭。
- unknown/cancellation case 只进入对应的签名 reconciliation 队列；checkpoint conflict 只进入 quarantine，并在 takeover Lease 之前停止。测试不得把 hold 的存在解释为 `NO_SIDE_EFFECT`，也不得把它作为 worker 重放 authority。
- 五个新核心 case 首轮为 `5 passed in 23.12s`；包含 online takeover、共享 Budget、一般图形与取消跨进程的最终受影响集成为 `37 passed in 145.68s`，0 fail。前者被后者覆盖，不相加。
- 当前结论是 `GENERAL_SHAPE_COMMITTED_CHECKPOINT_RECOVERY_LOCAL_PROVEN / DURABLE_UNKNOWN_CANCELLATION_CONFLICT_HOLD_LOCAL_PROVEN / GENERAL_RECOVERY_INCOMPLETE`。无限规模、其它 failure/cancellation 组合、Workspace 终态、部署 fan-out、NE-01 波次 integration、真实网络和 NE-07 Docker 全栈均未运行；远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Scheduler 管理命令切流当前切片

- `/scheduler` 与 `/host-scheduler` 的 create/update/delete/enable/disable 必须调用 app-local Scheduler 管理 runtime；路由直接调用 `publish_definition/set_enabled/delete_definition` 的测试替身会立即报错，证明两个前缀都没有生产 writer 旁路。
- 每个写请求必须经过 Scheduler Secure Ingress → typed Governor → `HostCommand(surface=SCHEDULER)` → immutable `TaskGraph` → Attempt/Lease/Grant/Budget/Review/Verification/Acceptance → terminal Preview → `operator.state_control` Effect。list/get/health 保持只读，trigger 继续走既有 governed occurrence。
- 真实 SQLite 集成覆盖 publish、update、disable、delete/retire、Review continuation 与 exact replay；Acceptance 后已提交的 `scheduler_admin_operation_result` 若在 HTTP claim completion 前发生崩溃，新 service 实例必须只重开同一 receipt 并补齐 claim，definition head 保持 1。
- 当前唯一受影响代码批次为 `34 passed in 139.12s`，取代开发过程中的 32 项和结构修复前的 127.26 秒结果，不与历史 Scheduler/Operator 批次相加。
- Scheduler durable UoW、accepted effect、derived Preview、production cutover 与 dispatcher 的扩展回归为 `57 passed in 54.81s`。它修复了旧 production fixture 未显式声明 `internal_agent_checkpoint_uow` 的漂移；该组与 34 项存在语义重叠，禁止相加。
- 首次 fresh Gate 3 如实发现 `acceptance_effects.py` 为 1511 行；未扩白名单，将 state-control route predicate 下沉到专用 compiler 后主 owner 为 1500 行。最终 Core Architecture Gate 1～14=`PASS`、`failed_gates=[]`。
- 该切片清零的是 Scheduler 管理 HTTP 的直接生产 authority，不改变 Operator `14/37` 库存，也不证明 mutable batch freezing、一般多节点/unknown-effect 恢复、完整 Host 进程重启、部署凭据、真实 HTTP/network canary、NE-01 波次 integration 或 NE-07 Docker 全栈。远程 CI=`NOT_RUN_ACCOUNT_QUOTA`，NE-01 保持 `IN_PROGRESS`。

## NE-01 Operator 状态控制历史切片

- `scheduler.task.enable/disable` 与 `sys_mode.auto_rules.update/event.submit` 经过独立 Secure Ingress → Governor → `HostCommand` → 不可变 `TaskGraph` → Attempt/Lease/Grant/Budget/Review/Verification/Acceptance → terminal Preview → `operator.state_control` Effect；Review 前 canonical Scheduler/mode-policy 写入为 0。
- 0129 为 Host mode-policy 建立 runtime scope/owner/lease/fence 绑定的 append-only event/head。同 operation 精确重放不追加事件；1001 条分页重启恢复、append 故障全回滚、released fence 和 tamper 均由真实 SQLite 验证。
- 休眠 Scene 的 `PROACTIVE_ACTION_BLOCKED` 对通用 Intent 为 `RESTRICT`，对 Proactive projection 仍为 `BLOCK`；签名 Operator 唤醒事件因此不能绕过 Review，也不会被错误永久阻断。
- 当前不重叠代码回归为 `175 passed`：mode-policy SQLite 3 项、Operator 集成 5 项、unit/contract 167 项，均为 0 fail。最终 0129 SQLite、migration、coverage、文档与交付合同为 `59 passed in 100.78s`，它与前述集合有重叠，不相加。
- 首轮 Core Architecture Gate 3 如实发现主 `governed_operator_runtime.py` 达 1538 行；未扩白名单，而是把 mode receipt reopen/current-mode 责任移入专用 state-control runtime。拆分后主文件 1344 行、专用文件 650 行，相关回归 7 项通过，fresh Gate 1～14=`PASS`、`failed_gates=[]`、Gate 3 未分类文件为 0。
- 最终统一静态扫描=`passed / failures=[]`；changed Python compile、三份 JSON 和 `git diff --check` 通过。
- Operator inventory 为 `14/37` governed、`23/37` blocked。旧 Obs sys-mode mutation helper 已删除。后续 Scheduler 管理 HTTP 切流已关闭当时记录的独立 CRUD direct-authority 缺口，但不改变本历史批次的测试边界；完整 Host 进程重启、部署凭据、真实 HTTP/network canary、NE-01 波次 integration 与 NE-07 Docker 全栈仍未完成。

## NE-01 Operator Task/Review 控制历史切片

- `task_graph.cancel`、`task_review.approve/reject/resume` 已从 Obs 直接 writer 改为独立的 Secure Ingress → Governor → `HostCommand` → 不可变 `TaskGraph` → 节点 Attempt/Lease/Grant/Budget/Review/Verification/Acceptance → terminal Preview → `task.operator_control` Effect。只有 post-Acceptance handler 可以调用原 Task/Review owner；Graph cancellation 默认仍为关闭。
- Review approve 不公开 `review_resume::` bearer，只返回不可供普通 API 使用的 `review-token://...` 引用。真实 SQLite 合同会关闭并重开 `ReviewStore`，随后证明该引用仍严格绑定 task/subject/version/checkpoint、只能消费一次，且 bearer 不进入 WorkOrder、数据库或公开响应。
- 主定向集合为 `78 passed in 19.77s`。Acceptance/Review 生命周期文件为 `80 passed in 41.69s`，独立 Graph cancellation routing 文件为 `12 passed in 0.29s`；这些集合有重叠，不相加为伪总数。此前两个超过 60 秒并被工具终止的组合命令不计 PASS，也不计失败。
- changed-module `compileall`、JSON 与 diff-check 通过；统一 static scan=`passed / failures=[]`，文档 authority/freshness、delivery metadata 和 coverage 为 `39 passed in 9.23s`。fresh Core Architecture Gate 1～14=`PASS`、`failed_gates=[]`，通过中立合同、schema probe 与 worker-authority guard 拆分消除了反向依赖和未分类巨石，没有扩大 allowlist。
- 该切片自身没有 migration；其 `10/37` governed、`27/37` blocked 是提交绑定历史，现行 inventory 见上一节。完整 Host 进程重启、部署凭据、真实 HTTP/network canary、NE-01 波次 integration 与 NE-07 Docker 全栈均未运行；远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 general Acceptance Effect 签名对账当前切片

- 新增第 6 条 governed Operator 写路线 `task_effect.reconcile`。`GENERAL_UNKNOWN_EFFECT` 的原始 Ed25519 请求在 Secure Ingress admission 前验证并写入 0128 append-only evidence sidecar；WorkOrder/TaskGraph/Review/外层 Effect 只携带 ref/digest 与最小 subject identity。
- 风险 4 Review、Action Preview、Verification 和外层 Acceptance 后，Accepted handler 重开并重新验签 evidence；canonical UoW 原子提交 signed proof、target Effect 与 Outbox。cancellation-bound Effect reconciliation 保持独立，不能由该路线静默升级。
- 当前主受影响集合为 `63 passed in 52.65s`，handler/结构集合为 `52 passed in 1.94s`；迁移与合同按不重叠文件分区为 `32 + 37 + 5` 全绿，0126 增量 readiness 修复另有 `8 + 2 targeted` 全绿。集合不累加为伪总数。`compileall`、本批 JSON、diff check、生成 Operator client、文档 Gate 和 static Gate 1～14 全部通过且未扩白名单。部署 key、HTTP/network canary、NE-01 wave integration 与 NE-07 Docker 全栈均为 `NOT_RUN`，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`，NE-01 保持 `IN_PROGRESS`。

## NE-01 有界多 checkpoint 共享 Budget 恢复当前切片

- 真实 SQLite 构造两个无依赖根节点、`ATOMIC_GRAPH_SUCCESS_V1` 和并发度 2；两个 worker 都先提交 canonical WorkerProjection 与 `COMMITTED` Operation，再分别提交 worker BudgetUsage，同时模拟写响应与首次 exact read 双重丢失。初始 owner 只能返回 `RECOVERY_REQUIRED`，不得写伪终态或重跑 worker。
- 旧 Lease 到期后同时启动两个使用独立 Store/owner 的 scheduler。TaskStore CAS 只允许有效 higher-fence takeover，恢复顺序固定为 immutable graph order `root-a → root-b`；竞争 loser 只能看到 terminal replay 或 `RECOVERY_REQUIRED`。
- token limit 100 覆盖 accepted+accepted，最终 Graph=`COMPLETED`、Budget=`CONSUMED`、两 Node=`ACCEPTED`；token limit 15 覆盖 root-a accepted、root-b exact rejected，最终 Graph=`BLOCKED`、Budget=`RELEASED`、Node=`ACCEPTED/FAILED`，复用既有 parallel settlement。
- 修复后的 checkpoint loader 不再错误要求每个 accepted usage 都与 graph Budget 的最终 `EXCEEDED` 状态相等。它只在同一 canonical scheduler snapshot 可证明 admitted sibling 的 `TASK_STORE_BUDGET_EXCEEDED` worker usage、对应 Attempt、相同 graph/revision/Budget、非空 exceeded dimensions 和连续 Reservation version 时允许 accepted checkpoint 继续；测试删除 sibling rejection 后确认零 takeover Lease、状态保持 `RECOVERY_REQUIRED`。
- 新增三阶段 missing 场景：一个 sibling 已成为唯一 `EXCEEDED` winner，另一个 `COMMITTED` checkpoint 的 worker BudgetUsage 在持久化前丢失；第二 owner 使用稳定 usage ID 与正 durable elapsed 补写为 `ALREADY_EXCEEDED` 后再次丢失写响应和首次读取，第三 owner 只读精确重开。该 sibling 必须保持 `version_before == version_after == winner.version_after`，并与 winner 共用 Outbox 和 exceeded dimensions；篡改 winner Outbox 或把 winner 改成未准入节点的快照均在创建新 Lease 前失败关闭。
- 新增四根节点 bounded 场景：四个 worker/Operation 均先 COMMITTED，Budget charge 经真实 CAS retry 按确定顺序形成两条 accepted、唯一 `TASK_STORE_BUDGET_EXCEEDED` winner 和一个 `ALREADY_EXCEEDED` sibling；恢复后 Graph=`BLOCKED`、Budget=`RELEASED`、active Lease=0，四个 worker 各调用一次，terminal replay 调用数不增加。
- 双场景首轮为 `2 passed in 7.16s`；包含既有三项同文件回归的最终文件为 `5 passed in 23.40s`。online takeover 完整文件为 `23 passed in 71.29s`，恢复/Budget 单元为 `28 passed in 0.94s`，结构与 coverage 为 `27 passed in 2.58s`；这些集合与后续宽回归可能重叠，禁止相加成伪全量数字。
- parallel/settlement 宽回归首轮为 `67 passed + 8 failed`，8 项均是已合并合同后的旧测试而非本次生产修复失败：4 项仍期待 known interruption 的 Operation=`RESERVED`、2 项 monkeypatch 未透传 `execution_segment`、1 项仍尝试 UPDATE 0127 已物理冻结的 WorkerProjection、1 项仍在原子 Graph/Budget settlement 之外注入已删除的独立 Budget release 窗口。修正为生产形状后 8 项 selector=`8 passed in 22.14s`。
- 宽回归最终完整文件为 `74 passed + 1 timing assertion failed in 221.13s`。该项错误地把 best-effort heartbeat-loss marker 强制为 `RECOVERY_REQUIRED`；实际合同允许在旧 Lease 已过期时保持 `RESERVED`，但必须公开 `UNKNOWN + marker failure` 且零 `OPERATION_COMMITTED`。改为逐分支核对 Reservation、projection 和唯一/零 recovery Outbox 后，同一 selector 三次独立运行均为 `1 passed`。因此当前 75 个 case 均已有最终代码上的通过证据，但没有伪造一条“完整命令 75 passed”输出。
- 另有 checkpoint/parallel structure `20 passed in 38.41s`、accepted-effect restart 与 Budget contention `4 passed in 11.51s`、文档/release/coverage `54 passed in 13.45s`。测试夹具补齐 production-required node Grant verifier；目标模块 `compileall`、统一 static scan、doc gate、JSON 解析与 `git diff --check` 均通过。静态 Gate `failures=[]`，未扩大 allowlist。
- authority 加固后的两个新增场景为 `2 passed in 8.81s`，包含既有五项回归的完整模块为 `7 passed in 25.59s`；它们与后续宽回归可能重叠，禁止相加。
- 当前提交的受影响代码集合（多 checkpoint、online takeover、Budget contention、checkpoint recovery、parallel winner 与 Node projection）为 `56 passed in 126.52s`；文档 authority/freshness、coverage、release、NLE boundary 与 import-order 合同为 `45 passed in 94.67s`，其中 11 条既存转义 DeprecationWarning 不构成失败。Core Architecture Gate 1～14=`PASS`、`failed_gates=[]`、Gate 3 `issues=[] / new_mixed_over_800=[]`；compileall、规则降级扫描、JSON 与 diff-check 均通过。56/45 与前述 focused 集合可能重叠，不相加。
- 这是 0129 时点的历史 four-root 边界：当时不解决 `CONFLICT`、`RESERVED`、超出 four-root 的图形/规模、取消交叉或未知 external effect。本文首节记录的后续 0130 切片已经增加五节点菱形、八根节点和 durable hold，但没有把历史测试数字反写为新证据；部署 fan-out canary、七路线波次验收仍未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 signed WorkerProjection checkpoint COMMIT 当前切片

- `task_operation.reconcile` 在没有 cancellation intent 时只允许 `COMMIT + worker-projection://.../<sha256>`；任意 transport receipt、普通 `RELEASE` 或非 canonical projection 在 admission/handler/Store 边界失败关闭。
- 服务读取层重开 immutable HostCommand、TaskGraph、Node、Attempt、Operation 与 WorkerProjection；focused Store 在 Operation CAS 的同一真实 SQLite UoW 中再次验证 Graph/Node/Attempt、scope/fingerprint、projection digest、无 cancellation 和旧 Lease expiry。0127 trigger 阻断 WorkerProjection UPDATE/DELETE；readiness 比较 exact trigger set 与 canonical SQL，同名弱化 trigger 也会拒绝。
- 真实 SQLite 集成覆盖：worker 已执行一次并提交 projection、heartbeat-loss 后 Operation 为 `RECOVERY_REQUIRED`、错误 Ed25519 签名拒绝、live Lease 阻断、Lease 到期后签名 COMMIT、首次响应丢失 exact reopen、Accepted handler 继续原图、同一 Attempt/Graph 完成、唯一 `OPERATION_RECOVERY_COMMIT`，以及 Task terminal replay 不重跑 worker。
- 当前新增/受影响 focused 集合为 `69 passed in 15.29s`；既有 signed REACQUIRE 回归独立为 `1 passed in 2.65s`，online takeover 中 response-loss/reopen/heartbeat/interruption/reservation 五项为 `5 passed in 13.36s`。集合可能共享底层 fixture，不把数字相加成全量结论。
- 运行时、真实 SQLite、迁移、Composition、文档 authority/freshness、交付元数据和 release coverage 的最终合并 Gate 为 `114 passed in 25.32s`；它包含上述 69 项，不与任何批次相加。
- Bond、Persona、规则降级、NLE boundary 与 Edge contracts 五类统一静态扫描均为零违规；fresh Core Architecture Gate 1～14=`PASS`、`failed_gates=[]`。`task_repository.py` 为 4583 行，仍低于既有 4600 上限，没有增加或扩大 allowlist。
- 该历史 checkpoint 切片当时的 migration head 为 `0127_worker_projection_immutability.sql`；现行仓库 head 已推进到 0131。该证据使用本地临时 key、单节点和进程内 route continuation；0130 的多节点/hold 与 0131 的 case-bound reconciliation 证据由本文当前切片单独记录，不能反向扩展本历史切片。部署 key/HTTP/network canary、七路线波次与 NE-07 Docker 全栈仍未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 受治理签名 Operation 重获与原图续跑当前切片

- migration 0126 新增 `task_runtime_operation_reconciliation_evidence`。写入时用 verification-only Ed25519 key ring 验签并绑定当前 `RECOVERY_REQUIRED` reservation/version/action；evidence ref/digest 确定性派生，同 request exact replay 只读，payload drift 拒绝。真实 SQLite trigger 阻断 UPDATE/DELETE。
- `task_operation.reconcile` 通过 Secure Ingress、typed Governor、risk-4 Review、Action Preview、HostCommand/TaskGraph、Verification、Acceptance Effect 后执行。通用图中没有 `attestation_signature`，Accepted handler 从 evidence ledger 重开并再次验签。
- `REACQUIRE` 只保留同一 Attempt，签发严格更高 fence、新 owner 与新的 Budget execution segment；随后调用原 route continuation。真实 SQLite 端到端测试证明首次请求为 `WAITING_REVIEW`，批准后原 worker 只续跑一次并将 Operation 收敛为 `COMMITTED`；相同外层请求重放不再次进入 target runtime。
- handler、compiler、Store 结构、签名重获及 governed Operator 关键组合为 `64 passed in 12.77s`；单条完整生产 composition 路径独立为 `1 passed in 7.77s`，二者可能重叠，不相加。
- 最终回归按完整文件拆开执行：Operation/Governor `39 passed`，Action Preview `25 passed`，Scheduler/SysMode/Edge Operator 分别 `3/2/1 passed`，online takeover `23 passed`，Composition/结构 `104 passed`，migration contract/compatibility 分别 `23/31 passed`，文档 authority/freshness/metadata `27 passed`。职责拆分后的 58+2 项与前述集合重叠，不相加。两次合并 pytest 命令达到 60 秒工具上限而被终止，未计为失败或 PASS；同一完整文件已在上述拆分批次重跑。
- `governed_operator_runtime.py` 已从 1559 行拆为 1432 行主编排与 170 行纯 frozen-execution 模块；未扩 policy allowlist。生成 Operator client `--check`、统一 doc Gate、统一 static scan、JSON/compile/diff-check 通过，完整架构静态 Gate 1～14 为 `PASS`、`failed_gates=[]`。
- 测试 key 是临时 Ed25519 材料；功能默认关闭，未验证部署 key lifecycle、真实 HTTP/network、一般 unknown-effect 自动归因、general multi-node/batch recovery、NE-01 wave integration 或 NE-07 Docker 全栈。总体保持 `IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 执行中断持久分类与签名取消续接当前切片

- worker self-cancel、单节点 time guard、已识别 worker failure、heartbeat loss 与 caller cancellation 在 Operation 预留后先尝试 exact Attempt/Lease/owner/fence/version marker；`RESERVED → RECOVERY_REQUIRED` 与唯一 Outbox 在同一个真实 SQLite UoW 中提交，exact response-loss replay 不二次写，版本、owner、原因码或 Outbox 漂移均失败关闭。
- marker 只保存有界大写原因码，不保存异常正文；caller cancellation 原样向上传播。marker、运行时 helper 与 Store mixin 均没有 Attempt/Node/Graph/Budget/Lease terminal authority，也不能 `COMMIT/RELEASE` Operation。
- 跨进程集成从该 marker 开始，用临时 Ed25519 key 生成 `RELEASE + NO_SIDE_EFFECT` proof，重建 Store 后运行 `GraphCancellationContinuationWorker`；Graph/Node/Attempt 为 `CANCELLED`，Operation 为 `RELEASED`，第二次重启零工作，`OPERATION_RECOVERY_REQUIRED`、`OPERATION_RECOVERY_RELEASE`、`TASK_GRAPH_CANCELLED` Outbox 各一条，worker 没有重跑。
- 当前 runtime/store/structure/cancellation 定向集合为 `133 passed in 91.94s`。随后把文档 authority/freshness、机器 coverage、metadata、Obs 合同与受影响 Runtime 合并复核为 `147 passed in 140.38s`；它包含或重叠前述测试，不相加。目标模块 `compileall`、两份 JSON、doc-only delivery gate、统一 static scan 与 `git diff --check` 均通过；fresh architecture Gate 1～14 为 `PASS`、`failed_gates=[]`，`task_repository.py` 4597 行、`node_execution_runtime.py` 1495 行，未扩大 policy。
- 该历史切片自身没有 migration；当时仓库 head 为 0126，现行 head 已推进到 0129。测试 key 是进程内临时验签材料，不是部署凭据；Operation/Effect Operator 仍默认关闭，HTTP/key lifecycle/canary 未运行。general Effect 的签名人工归因后来已形成独立本地切片，但自动归因、其它 cancellation、general multi-node/batch、Workspace 一般写、七路线验收、波次 integration 与 NE-07 Docker 全栈仍未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Host/Internal 周期 survivor 当前切片

- `PeriodicDurableContinuation` 只监督 cadence、wake 合并和关停，不持有 TaskGraph、Lease、worker 或 Effect authority。单元测试覆盖 timer/wake 串行、普通失败留痕后重试，以及关停等待活动 durable scan 而不取消事务。
- Host 在完整 RuntimeGroup 与共享 Acceptance Effect owner 就绪后先执行 mandatory boot pass，再周期重用 `HostStartupGraphContinuation`。真实 SQLite 测试持久化一个 live Lease，首次扫描保持 `DEFERRED`；可信 clock 越过 expiry 后，在线 wake 无需重启 Host 或客户端 replay 即重入同一个 immutable admission。
- Internal 首次 sealed-checkpoint scan 后启动同一生命周期 supervisor；`HOLD` checkpoint 在下一次周期可重新评估，但仍必须通过既有 seal、projection recovery envelope、historical Lease、higher fence、Operation/Budget/Grant/Preview/Task lineage，Agent 不会由 supervisor 直接调用。
- 当前 file-disjoint 运行时验证为：新增 supervisor/Host/Internal/SQLite 集合 `13 passed in 4.58s`；Host/ASGI/RuntimeGroup/Internal terminal 与四条真实 Internal SQLite+Git checkpoint/review/cancellation 恢复合并集合 `85 passed in 27.57s`。两组可合计为 98 个不同 case，但历史批次和后续文档 Gate 不得重复相加。兼容集合首次命令使用了不存在的旧 recovery 文件名，pytest 在 collection 前以“no tests ran”退出；改为 `test_aw02_internal_agent_runtime_cutover.py` 的四个精确 selector 后 85 项全绿。
- 覆盖 manifest、文档 authority/freshness、交付元数据与本切片核心回归的最终组合 Gate 为 `52 passed`；它包含前述 13 项新增集合，不能与 98 相加。第一次命令引用了不存在的旧文件名，pytest 在 collection 前以“no tests ran”退出；修正为 `test_delivery_metadata_gate.py` 后组合 Gate 全绿，不能把首次命令伪记为测试失败或 PASS。
- 目标模块 `compileall`、两份机器状态 JSON、`git diff --check` 均通过；fresh architecture static Gate 1～14 为 `PASS`、`failed_gates=[]`，未扩大白名单。
- 该周期 survivor 切片没有 migration，当时不证明 raw worker failure、unknown-effect attribution、其它 cancellation、一般 multi-node/batch settlement 或一般 write；本轮独立 Workspace 证据已补上 Internal write promotion，但 Domain/Skill/Codex write、NE-01 波次 integration 与 NE-07 Docker 全栈仍缺。程序状态保持 `IN_PROGRESS`，远程 CI 为 `NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Internal pre-checkpoint 签名终态取消收口当前切片

- 中立合同校验器是 Store、terminal reconciler 与 HostCommand replay 的唯一终态矩阵定义。它只接受 `RELEASED + RELEASE + NO_SIDE_EFFECT + no receipt`，或 `COMMITTED + COMMIT + SIDE_EFFECT_CONFIRMED + durable receipt`；混合、缺失、未知或篡改组合失败关闭。
- deterministic result 绑定 command/claim、graph revision、node/attempt/lease、Operation reservation、reconciliation proof digest、cancellation intent 与 final decision。无副作用路径继续产生 byte-compatible v1 digest；confirmed-effect 路径使用 v2 并额外绑定 action、outcome 与 downstream receipt，任一字段漂移都会改变身份或令 replay 拒绝。
- canonical TaskStore 还要求单节点 Internal `delegate:agent_runtime`、Graph/Node/Attempt/Lease 全部 `CANCELLED`、唯一已验签 Operation、WorkerProjection/Acceptance Effect/Agent result 均为 0。confirmed downstream effect 只证明 Operation 终态，不会伪造 Agent result 或 Acceptance。
- terminal reconciler 的破坏性顺序固定为 Secure Ingress claim durable commit → Workspace cleanup receipt → stage discard；claim 写失败时 Workspace 与 stage 都保留。真实 SQLite + Git 集成以临时 Ed25519 key 和真实 worktree 证明 COMMIT receipt 跨对象重建重开、claim-before-cleanup、source repository 不变及 proof/receipt 篡改拒绝。
- 本切片聚焦的 unit/contract/integration 去重集合为 `83 passed in 78.94s`；共享中立校验器及其 exact status matrix 补测后的核心三文件复跑为 `40 passed in 9.19s`，后者包含于 83 项，不能相加。
- 最终受影响 Gate 把上述运行时集合与 Universal Runtime coverage、文档 authority/freshness 和交付元数据合并为 `122 passed in 89.73s`；该 122 项包含 83/40，禁止再次相加。fresh 静态架构 Gate 1～14 为 `PASS`、`failed_gates=[]`。
- 该切片没有 migration，也不证明 raw worker failure、一般 unknown-effect attribution、其它取消、多节点恢复、NE-01 wave integration 或 NE-07 Docker 全栈。NE-01 保持 `IN_PROGRESS`，远程 CI 为 `NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Host 情感与回复形态 app-local authority 已合并切片

- 每个 Host root 通过 `emotion_runtime_factory` 惰性持有一个 `HostEmotionRuntime`；Input、Tribe 与 Lifecycle 解析同一实例，两个重叠 Host root 的连续向量、Timeline 与用户画像互不共享。
- 不带 runtime 的 `normalize_chat_emotion_context()` 是 replay/render 纯路径；只有 `resolve_chat_emotion_context(..., runtime=...)` 可以推进连续向量、前一标签、Timeline 与画像。缺失或错误 runtime 以 `HOST_EMOTION_RUNTIME_NOT_INJECTED` 失败关闭。
- `ResponseFormDecider` 由每个 Input composition 独占，旧 `_decider/get_reply_form_decider/get_response_form_decider` 不存在；并发语音额度在一个锁内完成检查和记账。
- 原 #119 分支在 #118 后的互不相加历史批次为：情感/Composition/Prefrontal `81 passed in 16.61s`，ASGI/Input/NE-03 `119 passed in 89.26s`，文档/机器状态 `51 passed in 23.26s`。以已合并 #120 的 `dev@1183e433b` 建立替代分支后，合并受影响集合为 `190 passed in 98.58s`，文档/机器状态与交付元数据初跑为 `39 passed in 11.27s`、证据刷新后为 `39 passed in 12.81s`；`compileall` 通过，完整架构静态 Gate 1～14 为 `PASS`、`failed_gates=[]`。历史与替代批次不得相加；这些结果只证明本地定向边界，不是波次 integration、真实多 Host server canary、情感 durability、NE-06 或 NE-07 全栈证据。
- 无 migration；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`，NE-01 保持 `IN_PROGRESS`。

## NE-01 Obs 投影 app-local 监督已合并切片

- `ObsApplicationContainer.health_snapshot()` 把它精确持有的 `ProjectionJobDaemon` 监督快照接入真实 Obs `/health`；健康输出包含 lifecycle/running/generation/wave、连续失败、opaque incident、错误类型和恢复标记，不包含异常正文。
- 普通 projection job、leader lease acquire/renew 故障保持后台线程存活并进入 `DEGRADED`；后续成功波清零连续失败。`SystemExit` 等进程控制异常令生命周期进入 `FAILED`，显式 `close/stop` 前拒绝重启。
- 新合同与单测首批为 `6 passed in 13.13s`。首次受影响回归为 `67 passed, 1 failed in 169.29s`，唯一失败是旧 Host route 隔离 fixture 未进入 ASGI lifespan；修正 fixture 后单 case `1 passed in 22.08s`，最终受影响集合 `68 passed in 211.30s`。各批次存在重叠，禁止相加。
- 文档 authority/freshness/交付元数据与 coverage 合同 `51 passed in 14.19s`；`compileall`、机器状态 JSON 解析、`git diff --check`、统一静态扫描均通过，fresh architecture Gate 1～14 为 `PASS`、`failed_gates=[]`。
- 本切片没有 migration；durable SLO journal、Guardian acknowledgement、部署多实例 canary、NE-01 wave integration 与 NE-07 Docker/真实附属服务均未运行。程序状态保持 `IN_PROGRESS`，远程 CI 为 `NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Edge 指令首条正向 governed 切片

- 旧 `EdgeCommandPusher` 进程 owner、startup 注册与 Tribe/内部直推仍保持为 0；Obs `edge.command.push` 现在只允许 `target_agent=edge.somatic`、`command_type=emotion_state_command` 与 `emotion_tag/intensity` 白名单，其他 command 在 HostCommand admission 前失败关闭。
- 正向链为 `Secure Ingress → typed Governor → OPERATOR HostCommand → immutable TaskGraph → risk-4 Review → Attempt/Lease/Grant/Budget → Verification/Acceptance → edge.embodiment_enqueue → terminal Preview → HostEmbodimentRuntime durable outbox`。首次响应只证明 durable enqueue；物理 dispatch 与回执独立推进。
- 真实 SQLite 集成覆盖 Review 前 outbox 为 0、Acceptance 后唯一 `PENDING` 行、一次物理 dispatch、请求 exact replay 不重复入队/发送、unsupported command 不创建 HostCommand，以及 claim 失效后的 `IN_DOUBT → read-only receipt query → APPLIED` 且 submit 次数不增加。编译器测试另覆盖确定性 command/idempotency/authority identity 与 worker 伪造 runtime scope 拒绝。
- Acceptance handler、Operator runtime 和 Edge 绑定 helper 已拆成聚焦模块；`acceptance_effect_handlers.py=966`，sys-mode handler=368，general Effect Store=1426+716，general Effect Runtime=793+88+34，`governed_operator_runtime.py < 1500`。fresh architecture static Gate 1～14 全部通过且未扩大白名单。
- 最终去重的 Operator/Acceptance/Preview/Edge 受影响集合为 `78 passed in 79.67s`；文档 authority、freshness 和交付元数据为 `27 passed in 10.53s`。目标模块 `py_compile`、机器状态 JSON 解析、`git diff --check` 与 fresh static Gate 均通过，`failed_gates=[]`。
- 集成中的物理端口是记录型 async adapter，不是真实 Edge/Gateway 进程。波次 integration、production canary、真实网络/设备与 NE-07 Docker 全栈仍为 `NOT_RUN`；计入后续 general Effect route 后，其余 31 个 Operator 写命令继续阻断，NE-01 保持 `IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

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
- 多节点现在额外证明双 checkpoint 两种终态、共享 `EXCEEDED` 下 winner-bound missing 补写与三 owner重开，以及 four-root bounded mixed settlement；`CONFLICT`、`RESERVED`、任意图形/规模、取消交叉、unknown effect、NE-01 波次集成与生产 canary 仍未完成。

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

## NE-01 Agent 模型预算定向证据

2026-07-26 的当前切片覆盖以下局部合同：

- 91 项 Agent、ModelAttempt、Budget measurement、Skill Broker 与 frozen
  route 单元回归；
- 3 项真实 SQLite Internal budget rejection、post-review cancellation
  与 accepted Workspace 晋升回归；
- 1 项真实 SQLite+Git governed Agent Skill Workspace 晋升回归。

这些集合分别保存且不相加。它们证明多轮 Agent 模型请求不再重复获得完整
Task 额度，并证明 Skill sealed authority 不丢失 Budget；不证明真实模型
provider、NE-01 wave integration、七路线 commit-bound acceptance 或
NE-07 Docker/前后端全栈。远程 CI 保持
`NOT_RUN_ACCOUNT_QUOTA`。
