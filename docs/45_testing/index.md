受众：PROJECT
标题：测试与验收目录

# 测试与验收目录

版本：v2.1
维护日期：2026-07-20
状态：当前证据

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

## NE-01 pre-Operation Attempt takeover 切片

- 当前提交的受影响集按五个互不重叠的真实命令保存：Runtime/结构/在线接管 `184 passed in 289.38s`，TaskStore+Heartbeat `69 passed in 23.43s`，Acceptance/atomic `23 passed in 101.70s`，Cancellation/parallel `68 passed in 228.20s`，checkpoint/Budget/Intent `52 passed in 123.58s`；合计 396 项、0 fail/skip。架构 Gate 1～14 全部 `PASS`，`task_repository.py` 为 4585 行、低于未变的 4600 上限，takeover mixin 为 230 行，未扩大白名单。一次把后三组串行执行的命令在 364 秒上限被截断且没有测试失败结果，拆分后全部通过；新增 AST 门禁首跑 1 个 test-only 失败，最终补齐纯模块与精确早返回门禁后 identity/structure `12 passed in 1.32s`。旧 191+1、198/49/45 与 post-COMMIT 71/39 均保留为历史，不参与当前 396。
- 真实 SQLite 覆盖 Operation 预留前硬停机、同 deterministic Attempt 的 higher-fence takeover、reservation response loss、并发单 winner、ACTIVE/EXCEEDED shared Budget 与 canonical PRECHARGE 矩阵、accepted/failed/review sibling、Operation-present/operation-free 混合 live owner、Graph/Budget terminal fence，以及 post-reservation worker error/collision 的 recovery hold。
- live owner 固定返回 `GOVERNED_EXECUTION_IN_PROGRESS` 且零 worker/零 mutation；Operation absence 在 `BEGIN IMMEDIATE` 内同时按 reservation id 与 scope/fingerprint 重验，竞态出现只转 `RECOVERY_REQUIRED`。Operation 已预留后的异常不再写普通 Attempt/Node/Graph failure或释放Budget/Lease。
- 状态仅为 `CANONICAL_PRE_OPERATION_ATTEMPT_TAKEOVER_PROVEN / POST_RESERVATION_RECOVERY_HOLD_PROVEN / GENERAL_MULTI_NODE_RECOVERY_INCOMPLETE`。普通 Graph+Budget 终态和 success/effect 的完整原子 UoW、unknown effect、multi-node/batch settlement、wave integration、真实模型与 NE-07 全栈仍未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

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
