受众：CODEX
标题：Natsume AI 稳定工程合同

# Natsume AI 稳定工程合同

版本：v5.1
日期：2026-07-19
文档类型：`STABLE_ENGINEERING_CONTRACT / NO_EXECUTION_STATUS`

本文把 PRD 转为长期稳定的工程约束，不记录实现完成度或阶段状态。

- 产品需求：[PRD.md](../20_prd/PRD.md)
- 当前事实：[CURRENT_CODE_ARCHITECTURE.md](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
- 目标架构：[TARGET_ARCHITECTURE.md](../50_project/arch/TARGET_ARCHITECTURE.md)
- 当前差距：[ARCHITECTURE_GAP_MATRIX.md](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)
- 唯一活动执行：[NEXT_GENERATION_EVOLUTION_EXECUTION.md](NEXT_GENERATION_EVOLUTION_EXECUTION.md)

若本文与生产代码冲突，代码决定“现在是什么”，本文决定“实现必须收敛到什么合同”；不得用本文文字把未接线能力标成完成。

## 1. 进程与依赖合同

### 1.1 运行面

Host、Obs、Gateway、Edge、Worker 与 UI 必须可独立构造、启动、drain 和关闭。跨运行面只共享 `natsume_contracts` 等版本化合同，不共享具体 repository、service container 或设备实现。

### 1.2 分层

```text
Contracts / Kernel
        ↑
Domain
        ↑
Application
        ↑
Adapters / Interfaces
        ↑
Composition
```

- Domain 不导入 HTTP、composition、provider SDK 或其他领域私有 Store。
- Application 只依赖 typed ports。
- Adapter 实现网络、数据库、模型、设备和 UI 边界。
- Composition 是唯一允许绑定具体实现的位置。
- Application、runtime 与 service-catalog authority 不得自行打开私有 SQLite 连接或内嵌 SQL/事务。0112 readiness 必须通过 `RuntimeEventStore` typed port 进入 `RuntimeEventRepository`；Service Catalog rollout 的 SQLite/SQL/UoW 必须归属 persistence repository，authority 与 runner 只能依赖 Protocol。`ACTIVATION_STARTED` receipt 与 rollout head 必须在一个 repository 事务中同成同败。`host_startup.py`/ASGI 允许在迁移期作 concrete wiring，但在 app-local typed composition 取代前不得把 Gate 10 通过写成 Composition 完成。
- Service Catalog 的启动协调必须归属 application/service-catalog 边界：canonical owner 是 `service_catalog/startup_coordinator.py`，旧 `composition/service_catalog_startup.py` 只能同对象重导出；稳定 DTO、校验与 repository Protocol 必须来自 `contracts/service_catalog.py`，旧类型路径也只能同对象重导出。`rollout_authority.py` 只保留 Guardian observer，persistence repository 只能依赖稳定 contracts。该规则已令 application→composition 与 Catalog persistence→application 的已知依赖为 0；在 Service Catalog 切片快照中 Gate 1 仍有 application/persistence SCC 和 11 条 persistence→application 依赖，后续 PlanningSearch 分层清除其中 7 条。任一子域清零都不得宣称 Composition 或 NE-01 完成。
- PlanningSearch 被 persistence 消费的 core/benchmark/calibration/code/research 合同与模型必须归属纯 `contracts/planning_search` 包；旧 `planning_search` 类型路径只能同对象重导出，并保持 `__all__`、类型 identity、`isinstance` 与序列化形状。SQLite repository 禁止反向导入 PlanningSearch application 模块。该边界清零不代表 Host 已生成竞争候选、PlanningSearch 已生产切流或跨域收益已证明；只有 Gate 1 全部反向依赖归零且功能/收益 Gate 独立通过后，才能提升对应状态。
- Evolution、Evolution Lab、Runtime Context 与 Skill Lifecycle 被 persistence 消费的 DTO、枚举、codec/validator 必须归属纯 `contracts/evolution.py`、`contracts/evolution_lab.py`、`contracts/runtime_context.py`、`contracts/skill_lifecycle.py`；旧路径只能作同对象重导出，Skill Lifecycle application 模块只保留服务。Evolution Governance、Evolution Lab、Skill Lifecycle、World Observation repository 不得反向依赖 application。fresh 静态评估已证明 persistence→application=0、SCC=[]、Gate 1/6/10/11 通过；该结论只验收分层、类型 identity 与 production-service inventory，不得用来宣称 NE-01、NE-04～NE-06 或完整架构生产闭环完成。
- 禁止模块级可变 service registry、业务层 `get_*()` locator 和依赖 test reset 的单例。
- 每个 Host `create_app()` 必须构造自己的 `HostLifecycleContainer`。`ActivityMonitor`、`SystemResourceManager` 与 `ModeTransitionService` 必须属于同一 ASGI app/epoch：candidate 先被 container 拥有，只有 factory 与 start 全部成功后才向请求路径发布；Activity factory 失败不得发布 Mode，任何部分启动失败都必须回收。关闭必须先设置 closing fence 并撤销发布，closing 期间 `begin()` 必须失败，随后按 ResourceManager→ActivityMonitor exact-once 停止。ResourceManager 的 startup sample 必须进入它唯一 owned loop thread，不得创建不可追踪线程。Chat/Input、Tool、Scheduler、Guardian heartbeat 与 lifecycle snapshot 必须通过该 app-local provider 读取同一实例；Obs/runtime snapshot 只读已发布 monitor，禁止因查询而懒启任何 Host service。Tribe agents/bus 仍为 process-global：Host lifespan 只能以 exact owner bind/unbind fence 绑定 app-local Tribe application ports，同一进程只允许一个 live Host，第二个 Host 必须 fail closed；不得把该围栏表述为并发 multi-app Tribe 隔离。`http_hooks.py` 不得直接导入 `process_services`，不得恢复全局 `set_activity_monitor` / `set_resource_manager` 或对应的 process container slot/lock。Host lifecycle 原始 commit-bound 审计为 named47/152→45/124、all-form52→51、Container56 fields/4 locks/51 refs；Gate 11 修复前组合库存为 Container55 fields/4 locks/50 refs；#89、ReAct 与 Visual 的 45/123/51、43/120/49、41/117/47 均是提交绑定历史。HardState app-local 切片后的 fresh 审计必须报告 named40 modules/116 names、all-form46 consumer modules 与 Container52 fields/3 locks/48 refs，且 40 不得冒充全仓模块总数。heartbeat cache/client、Tribe bus、共享 resource status file、自动模式规则与 last-interaction 必须分别声明自己的 owner 和恢复语义，不能借 mode head 的持久化隐去易失边界。
- Host HardState 的运行期唯一 owner 必须是 lifespan-owned `HostHardStateOwner`。`HOST_SYS_MODES` 与 `HOST_MODE_TRANSITIONS` 必须由 neutral HardState contract 唯一冻结，persistence 不得反向导入 core application；静态 Gate 必须保持该反向依赖为0且SCC为空。composition 只能在 bootstrap 显式读取一次 `HOST_SYS_MODE` 并传入候选初始 mode；owner、Chat payload、routing matrix 与 runtime snapshot 在运行期不得再读或写该环境变量。生产 owner 在 migration、canonical SQLite 和 active Host runtime lease/fence 绑定前必须拒绝写入。mode journal 必须以 runtime scope 分区，使用 append-only event、连续 revision、previous/event digest、稳定 event/idempotency identity，并将 event 与 canonical head 放在同一 SQLite UoW；每次 transition 必须 CAS expected revision/mode 并精确绑定 runtime scope/owner/lease/fencing token/expiry。owner 只能在 durable commit 成功后发布 immutable next-state；成功 transition 只能以该 journal 作为 canonical audit，响应丢失只能 exact replay，且不得因 replay 再次追加进程内 history、legacy `error_log` 或第二审计。payload drift、stale head、过期/释放 lease、旧 fence、schema/digest/head 篡改和部分写入必须失败关闭或共同回滚。重启必须完整分页验证 journal chain 和 head 后恢复，禁止用 bootstrap mode 覆盖已提交状态。`set_sys_mode()` 的内存边界仍必须避免持锁递归 getter，Endocrine resolver 必须在冻结快照并释放锁后调用。Chat、Tribe、Lifecycle 与 Scheduler 必须注入同一 owner；Obs Life Body 只能消费 durable runtime projection。process `get_hard_state`、`get_endocrine_state`、`set_sys_mode` 及 Container slot/lock 必须保持为 0；Limbic/Prefrontal 不得把 immutable HardState 当作 mutable sink。mode journal 通过不等于自动规则、last-interaction、生产 canary、整个 lifecycle 或 NE-01 完成。
- `ToolBroker` 必须只由 app-local `ToolRuntimeBoundary` 构造和持有；production-service inventory 只能有 `host_brain/tools/broker.py` 这 1 个构造点。process 层 `get_tool_broker` accessor、`ProcessServiceContainer.tool_broker` slot 和 `HostLifecycleApplicationPorts.tool_broker` provider 必须保持为 0，禁止通过 `process_runtime_services`、`process_services` 或 Host lifecycle provider 恢复全局发布。每个 `GovernedHostToolRuntimeComposition` 可以在自己的对象图中懒构造一个边界实例，但不能把 metadata broker 变成可绕过 TaskGraph authority 的执行 locator。
- 独立 Obs ASGI 必须由 `create_obs_app(ports|ports_factory)` 构造；ledger query、projection store、metrics、Life reader、projection jobs 和资源关闭器归属单个 `ObsApplicationContainer`。导入模块不得配置 Host application ports，也不得 install/clear Host 的 Life provider。Container 不得暴露泛化 `start()` authority，只能由 `obs_fastapi_app.py` 的嵌套 ASGI `lifespan` 唯一调用具名 `start_projection_jobs()`：ports bundle 原子 claim→compose app-local daemon→start→yield，finally 再执行 stop/join/in-flight fence→精确 leader lease release→close owned SQLite→clear state。
- 每个 Obs app 必须拥有独立 daemon、thread、stop event、leader lease 与 resource lifecycle。Daemon start/run 必须 single-flight，stop 必须阻止新 wave 并等待 background/manual in-flight；超时不得关闭 lease/SQLite或清空 app-local container/ports/readers，必须保留原 retry handles 并让 shutdown 失败，rollback close failure 同理。每个 `ObsApplicationPorts` bundle 必须以 bundle 内部、线程安全、原子 single-use claim 绑定一个 lifespan；两个 app 共享同一 bundle 必须 fail closed，`dataclasses.replace()` 必须继承同一个 claim，不能用 derived bundle 绕过。需要重建时，`ports_factory` 必须返回全新的独立 bundle；两个独立 ports/app 仍应互不影响。关闭一方不得影响另一 app，sessions response cache/single-flight 也必须是 app-owned。模块级 `_default_daemon`、导入期 `register_projection_job` 和进程 locator fallback 已删除，禁止恢复；app-local `ProjectionJobDaemon` 是保留的正确生命周期边界。
- Projection lifecycle static Gate 通过只证明明确的构造、唯一启动点与可重试关闭所有权，不证明 continuous-SLO supervision。后台 daemon 若因未捕获的 lease renew 等异常退出，`/health` 必须最终能呈现 `daemon.running/last_failure`；在该健康接线完成前不得宣称 Projection 或 NE-01 全闭环。
- Producer registration、声明 source-mode 与运行时 availability 必须分开表达。当前默认注册 21 个 callback（2 core + 19），声明为 `DURABLE_READ=6 / STATIC_READ=3 / DURABLE_READ_PARTIAL=8 / AUTHENTICATED_HOST_READ=4 / TYPED_UNAVAILABLE=0`；21/0 与四条认证 job 都不是运行成功/失败率。Capability Forge、Semantic Recall、Governance 与 Organ/Tribe 分别由独立认证 job 物化，sys-mode 由 partial operator job 消费同一 app-owned Host source；缺 token、Host、正证据、完整合同、freshness、ledger source、Gateway 健康对象或 target 必须显式降级，不得以空结果冒充当前事实。Life Body 虽不再声明整 job unavailable，但任一必需 root 缺失、无效、过期或 canonical ledger 不可读时仍必须物化 typed `SOURCE_UNAVAILABLE`。任一 operator target 写失败必须令整 job 失败。8 个 partial 与运行时可能失败的认证源令 `missing_jobs=[]` 时 `/health` 仍为 `DEGRADED`。
- Security evidence 必须满足最小 schema 和身份时间合同；默认最大年龄为 10 分钟，可由 `OBS_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS` 收紧或放宽。`QueryProjectionStore` 的 operation clock 只用于 lease fence、journal 和 `updated_ts_ms`，evidence freshness 只写 `freshness_ts_ms`，两者禁止复用。
- mode、capability-forge、semantic-recall、governance、runtime 五条 Host 合同必须在每个 Host lifespan 共享一个不可空 `source_instance_id`，并按合同维护正整数、单调递增的 `source_revision`。Obs 必须通过通用 `QueryProjectionStore.compare_and_upsert` 的 SQLite CAS 按合同路径持久化当前 lineage，并从 immutable journal 判断完整历史 epoch：同 epoch 的 revision、`observed_at_ms`、`evidence_ts_ms` 不得回滚，同 revision 不得改变 payload，任一退役 epoch 永久不可重放，不得用固定长度窗口截断防重放历史。该 lineage 是 Obs 持久读模型，不把 Host 进程内对象提升为 durable authority。
- 合同内只对精确名为 `timestamp` 或以 `*_ts_ms`、`*_at_ms` 结尾的嵌套字段递归执行 Host-observed 时间上界校验，不得笼统宣称拒绝所有“未来字段”。Obs 以本地接收时间复验顶层 observed/evidence：最多允许 5 秒 clock skew，正 evidence 最大年龄 120 秒，写入 `freshness_ts_ms` 时必须取 evidence 与本地接收时间的较小值。
- Host composition、Host route 边界与 Obs 接收端必须各执行公共序列化并重新解析版本合同；合同本身不是字段白名单。Organ `key_metrics` 必须 deny-by-default，只放行逐 agent 显式字段及数值/布尔标量，禁止把任意 registry `state` 标量带出。Host→Obs transport 只允许五条 allowlist 路径和专用 token，禁代理、禁重定向、限制 1 MiB；一个 monotonic total deadline 必须覆盖 DNS 解析、连接、响应头和分块正文。远端必须 HTTPS，明文 HTTP 仅限 loopback/localhost。
- D4 的36个已识别生产投影端点及其 projection/stats 查询继续从当前 app 的 `QueryProjectionStore` 或 `ProjectionLedgerView` 读取；D5 的5个 `/api/ui/*` GET 与 Security 3、Skill 1、Life 1、Semantic 1、Governance 1 共7个实际挂载 GET 也必须显式注入当前 app Store。D7 进一步规定：`obs_routes*.py` 不得出现 `process_services` 或 `_get_ledger` 的 locator 词法引用；`capability_stats_projection`、`domain_state_projection`、`projection_ledger_view`、`remaining_query_projection` 不得重新引入全局兼容 reader/factory/source fallback，只保留显式 store/ledger/source port。Host 的 world、canon-sync、startup 与 memory GET 必须从 Host lifespan 持有的 projection store 读取；mode 与 Life action-preview trusted context/approval-token validation 必须通过 app-owned route ports 注入，端口缺失时 fail closed。没有挂载消费者的 ledger compatibility handler 必须删除，不得为通过 locator gate 发明未消费端口。Host app factory 必须显式接收 typed `ObsRoutePorts`，Process/Tribe 全局 ports 不得捕获某个 app closure。Action preview 只允许 DRY_RUN，不得因接入 trusted context 而获得执行 authority。
- Capability Forge、Semantic Recall 公开运行配置、Governance 只读快照与 Organ 必须由四个 authenticated job 分别物化；Semantic/Governance 必须读取各自独立 endpoint/contract/cache/failure domain，不得以 Capability v1 的兼容内嵌 child 作为生产来源。兼容 child provider 异常只能将该 child 写为有类型的 `SOURCE_UNAVAILABLE`，不能拖垮有效 Forge source。D6 的 Memory/VTS 冷读与迁移0103约束继续有效；缺 canonical 压缩 token、蒸馏失败终态或 Edge 物理回执时必须返回不可测。D7b Life Body 只能读取同一 app 的四个公开 durable roots 与 `LifeBodyQueryRepository` 的有界 lineage，禁止导入 route、Host composition 或 process locator；输出必须移除 content/private 字段，保留 prompt injection、high-risk、Gateway 与 capability 风险语义，源事实不变时只刷新 freshness、不得推进 cursor 或追加 immutable journal。Direct Skill 的 resource manager、episodic store 与 semantic recaller 必须由 Host lifespan 捕获为实例并冻结到 `GovernedSkillExecutionPorts`，执行期不得重新解析 `process_services`；Durable authority 必须先于依赖可用性检查，必需后端缺失必须稳定失败且不得生成 accepted/成功 Skill receipt。Obs application/job assembly 归属 app 层，composition 不得反向导入 app，NLE 边界不得用白名单掩盖。具身执行必须同样遵守 durable app scope：每个 Host ASGI lifespan 只能构造自己的 `HostEmbodimentRuntime`，禁止 module default；所有新 outbox 行必须绑定 canonical DB 派生的不可变 `runtime_scope_id`，历史无 scope 行失败关闭。同一 scope 同时只允许一个持有效 durable runtime lease/fence 的进程，第二实例必须在开放路由前失败，旧进程失租后不得继续入队、claim 或更新 Edge credential。Host 必须在该 runtime authority 下持久保存精确 Edge `session/owner/lease/fence`，供同身份替代进程重启重连；不得在正常关闭时把可重连 credential 猜测删除。所有终端出站还必须持有与最终 action/scope/args 精确绑定的 ActionPreview；当前仅带 canonical proof 的 Life VTS/MateEngine 与 Gateway/Edge 调用可继续入队。旧 reply-performance、Gateway voice、chat cue 和其他缺少 Acceptance→Embodiment 派生证明的直接调用必须失败关闭，待 NE-01 迁入 Governed TaskGraph；安全阻断不得写成正向迁移。合法新命令只报告 `PENDING / DURABLY_QUEUED`，精确重复回放既有 durable 状态，只有既有 `APPLIED` receipt 才可证明 Edge 已收口，绝不得由新 admission 宣称物理成功。

- Edge receipt query v2 必须在一个只读 authority 判断中同时验证 HMAC+nonce、owner、current session、lease、fence 与精确 command/idempotency key；legacy ownerless command 必须返回 `NOT_FOUND`，不得从当前 lease 猜测历史 owner。`IN_DOUBT` 只有 identity 与 receipt digest CAS 命中的 `FOUND` receipt 才能收口；相同 `IN_DOUBT` digest 的重复查询不得刷新完成时间或重复写 observer/audit，`NOT_FOUND`、传输或校验失败必须保持不确定，禁止重放或重提物理命令。替代 Host runtime 若继承 live Edge credential，必须在路由开放前以 exact reconnect 原子轮换 Edge fence，并验证返回 lease 已改变且 fencing token 恰为旧值加一；任何不匹配回包必须失败关闭。每个 Host outbox enqueue、claim、过期、收口和 `IN_DOUBT` 对账必须在其自己的 SQLite 写事务内复验 exact Host runtime lease/fence，禁止仅靠事务外预检查。Host→Edge 的明文 HTTP 仅允许 loopback/localhost，任何非 loopback 地址必须使用 HTTPS并在建 client 前失败关闭。Host durable queued ack 与 Edge SQLite receipt 都不等于设备效果；loopback TCP/HMAC/SQLite receipt/restart 验证不替代真实设备验收。Host lifecycle 已由 app-local owner 与单活 Tribe fence 收口，ToolBroker/MCP direct/callback authority 也已退役；但 Host source provider 底层 authority、Chat/Delegate 与 Acceptance Effect 仍有 process-global 边界，当前仍有49个可变 service 引用、workspace-bound 真实 Tool/MCP executor、剩余 legacy direct embodiment adapter、完整 Host/Worker composition、已部署 Edge/物理效果和全局 Service Locator 删除都未闭环，任何局部切片都不得解释为 NE-01 或完整架构完成。

- Host 的 app-owned embodiment runtime 必须以 normalized canonical DB path 的稳定 SHA-256 派生 `runtime_scope_id` 与 Edge session/owner identity：同一 durable store 重启后必须以已持久化的精确 lease/fence 重连同一外部 authority，以便查询其旧 receipt；不同 store 不得因为默认 `host-brain-primary` 而争用同一 lease；两个 app 指向同一 store 时只有获得 durable Host runtime lease 的实例可运行，另一个必须失败关闭，不能借不同 session 或进程内缓存逃逸。

- Memory/Distillation 统计必须区分“事件被观测”与“结果已测量”：`COMPRESS_BEFORE_SAVE` 只计入 `observed_pre_save_hooks`，不能记作 `total_compressions`，压缩 total/success/failed 均返回 `null`。`error_log` 的 `DISTILL_DONE/DISTILL_SKIPPED` 由 `DistillationEngine` 的 batch 与 single 路径共同写入，batch 内还可能包含 item-level 终态，因此只能报告 observed terminal audit events，不能充当完整 `LongTermMemoryDistiller` cycle 或失败率 authority。
- Ledger-stats 物化必须按表的公开时间列和持久 `id/rowid` 倒序稳定读取最近 5,001 行，只保存最新 5,000 行并以 `truncated` 声明未物化的旧尾部；该标志不得让已被证明完整覆盖的新窗口永久 503。截断表只允许回答 `start_ms` 严格晚于最老保留记录时间的窗口；相等时间可能仍有被 durable identity 裁掉的同毫秒记录，因此边界窗口、直接全表读取和未命中的 `request_id` 必须以 `PROJECTION_SOURCE_TRUNCATED` 失败关闭。来源不可用或查询超出保留期也必须失败关闭。轮询未发现新公开事实时必须复用原 `source_cursor/source_digest`，仅推进 freshness，不得向 immutable projection journal 追加重复行；内容变化时 cursor 才能单调推进。

## 2. Host、Chat 与 Ingress 合同

### 2.1 Secure Ingress

每个入口先归一化为版本化 ingress envelope，至少包含：

| 字段 | 语义 |
|---|---|
| `principal` / `surface` | 发起者与入口面 |
| `scope` | 允许的对象、动作、通道和时限 |
| `correlation_id` / `causation_id` | 跨进程因果链 |
| `idempotency_key` / `nonce` | 防重与重放保护 |
| `reply_context` | 回包平台、会话和目标 |
| `visibility` | 数据可见性和脱敏边界 |

Chat、Host Input、Gateway、Scheduler、Internal Agent 和 Operator Command 均不得绕过该入口直接调用 worker、repository 或 actuator。

### 2.2 Chat 流协议

Chat 使用三阶段语义：

```text
PRESENCE  非权威在场/进度信号，可取消，不可当作事实
COMMIT    经 Governor、执行与回复 authority 收口的可发送结果
POST      COMMIT 后的只读投影或 durable effect 进度
```

协议要求：

- `PRESENCE` 不写长期事实，不承诺工具已完成。
- `COMMIT` 必须绑定 command/graph/acceptance 或明确的只读 fastpath evidence。
- 最终文本只由 Surface/Reply authority 生成；worker、tool 和 sub-agent 只能返回结构化候选或事实。
- canonical `chat/skill/delegate/plan` 不得降级到 Executive 第二执行链。

### 2.3 输入与事件分流

- 用户消息进入 Host Input/Chat 语义链。
- Edge playback、resource、device 等事件进入独立 Event ingress。
- Scheduler/Internal Agent 使用服务身份和最小 scope。
- 查询类 `recent_turn/task_progress` 应走 projection/read service，不借执行 Runtime 维护第二任务状态。

## 3. Governed Execution 合同

### 3.1 唯一 lineage

所有生产执行遵循：

```text
Ingress
  → Governor Decision
  → HostCommand
  → WorkOrderV3 + BudgetPolicy
  → immutable TaskGraph revision
  → TaskNode / Attempt / Lease / Reservation
  → node-bound AuthorizationGrant
  → Worker
  → Verification
  → Acceptance / Review
  → Effect Intent / Receipt
```

同一 action 不得存在第二个有效 grant、第二份 Task 状态或无法关联的副作用 receipt。

### 3.2 WorkOrderV3

生产 admission 只接受显式、版本化 WorkOrder。最低字段：

- identity：work/root/parent/correlation；
- objective、domain、run mode；
- workspace owner/root/isolation；
- allowed/forbidden actions；
- approval/review policy；
- `BudgetPolicy`；
- input/context/artifact refs；
- verifier 与 acceptance contract。

`AgentWorkOrder` 等旧结构只能作为边界输入，在 admission 前无损转换；缺失预算、identity 或权限时 fail closed。

Host exact replay 必须按 `command.route_type` 校验 authority：`chat/delegate/skill` 先统一验证 WorkOrder、HostCommand、planning lineage、grant、review waiver、target 与 capability binding；只有 `skill` 叠加 direct-skill 专属 metadata 校验。不得把 direct-skill validator 无条件套到 `chat/delegate`，也不得因 durable replay 改走较弱分支。该纯校验边界由 `app/governed_task_authority.py` 拥有，Host adapter 只负责生命周期、合同编译与 Runtime 调用。

### 3.3 TaskGraph 与 revision

- TaskGraph 必须持久化 node、dependency、input/output ref、retry policy、idempotency 和 verifier。
- `completion_policy` 是 TaskGraph v1 的持久可选扩展；缺省保持 legacy node-terminal 语义。选择 `ATOMIC_GRAPH_SUCCESS_V1` 时必须同时冻结 `result_node_id`，该节点必须存在于当前图且为 sink。legacy 多 sink 图不得声明单一 result；admission、durable replay、planner semantic comparison 与 worker projection 必须校验同一 identity，不允许用 completion order 推断结果 authority。
- 未满足依赖的节点保持 `BLOCKED`；循环和未知依赖显式阻断。
- Planner 只能提交冻结 revision；worker 不得改变节点集合或依赖。
- 失败重规划沿用 `graph_id` 并创建 `revision + 1`，保留旧 revision、失败 Attempt 和证据。

### 3.4 Attempt、Lease、Retry 与并发

- 每次执行创建唯一 Attempt；retry 创建新 Attempt，不覆盖旧结果。
- Lease、heartbeat 和 fencing token 防止双 worker；takeover 必须留下恢复证据。
- retry/backoff 必须实际消费 `retry_policy` 和 retry budget。
- 所有依赖已满足节点可有界 fan-out；fan-in 在依赖全部 Accepted 后推进。
- atomic success 与 bounded fan-out 使用独立进程能力开关：atomic success 对新生产图默认开启，bounded fan-out 仍默认关闭。新生产 builder 必须声明 `ATOMIC_GRAPH_SUCCESS_V1` 与显式 `result_node_id`；失败重规划只继承前一 revision 的 policy。atomic policy 在 repository admission 前验证 runtime capability、acceptance-effects writer、durable projection reader、node-grant service和 parent grant，不满足即 fail closed，禁止降级为 legacy 成功。terminal-leaf 实际并发还必须另行启用 bounded fan-out，不能从 policy 或图形状推断开关。
- cancel、timeout、crash、lease expiry 和 process restart 不得重复已确认副作用。
- TaskStore 的 `task_store_types.py` 只拥有稳定值对象，`task_record_codec.py` 只拥有确定性 JSON/SQLite Row 校验与转换；二者不得打开连接、执行 SQL、读取时钟或开始事务。`task_decision_integrity.py` 只拥有 decision/outbox lineage 的只读校验，必须借用调用方现有事务连接且只能执行 `SELECT`；不得另开连接、另建快照、提交事务或取得 mutation authority。
- Task mutation 的中央 Outbox 写入与 response-loss exact replay 校验只能由 `TaskOutboxDeliveryStoreMixin._insert_outbox()` 和 `_assert_mutation_outbox_replay()` 实现。两个 helper 必须接受调用方当前 `conn`，验证 aggregate/event/attempt/exact Graph revision 与 canonical Outbox identity，不得自行打开连接、开始/提交事务、读取新时钟或创建第二发布锁。Mixin 可以借用主 Store 的 delivery/readiness 能力，但不得定义独立 Store owner；`DurableTaskStore` 必须保持既有 MRO、对外方法 identity 与唯一 composition/connection/UoW authority。移动代码不得改变 mutation、CAS、legacy/current replay 或 0095 payload/revision 语义。
- Acceptance Effect 持久化按 atomic staging 与 fenced delivery 拆为无状态 mixin；mixin 不得定义 `__init__`、连接、时钟或事务 owner，只能复用 `DurableTaskStore` 的 `_connect()` / `_transaction()`。public `stage_acceptance_effect_intent()` 已删除，`_stage_acceptance_effect_intent_in_transaction(conn, ...)` 只能由 `transition_graph_and_stage_acceptance_effects()`、`commit_node_acceptance()` 与 `complete_parallel_graph_success()` 三个 canonical UoW 调用；当前静态库存必须保持 6 个调用且首参均为调用方现有 `conn`。Effect `INSERT` SQL 只能位于该私有 helper，Parallel Success、Node/Graph acceptance 不得因模块拆分跨事务或恢复 standalone pre-stage authority。
- 0091 Parallel Success 持久化 mixin 只借用主 Store authority；`complete_parallel_graph_success()` 必须只有一个 owner transaction，并以同一 `conn` 贯通 cancellation fence、Node/Attempt/projection lineage、Graph/Budget/Outbox CAS、Acceptance Effect staging、0091 insert/replay 与 decision binding。Cancellation 侧在自身事务内对 0091 的直接查询不得改成另开连接的 public getter。
- 0090 Parallel Settlement 持久化 mixin 同样只能借用一个 owner transaction；cancellation fence、完整 Node snapshot、Attempt/BudgetUsage lineage、Graph/Budget CAS、两类 Outbox 与 canonical decision insert/replay 必须使用同一 `conn`。latest reader 的 `taskbatch` marker 扫描、exact Outbox binding 和 lineage 校验必须复用同一个读连接。0090/0091/0092 共享的 Node snapshot helper 必须保持无 SQL、无 Store owner 的纯函数。
- 0090 runtime orchestration 与 read/validation 必须拆成窄边界，但不能产生第二 authority。`ParallelSettlementProjection` 只能持有同一个 TaskStore，并只读 scheduler snapshot、Node 与 Attempt，以验证 active re-entry、rejected Budget outcome 和完整 outcome snapshot；不得持有 Budget runtime、Outbox、锁、task、Coordinator 或 mutation port。`ParallelSettlementRuntime` 只能持有同一个 TaskStore、共享 `DurableBudgetRuntime`、共享 `RuntimeOutbox` 与该 Projection，负责 failure/review/Budget outcome resolution 和 canonical settlement orchestration；不得反向持有 Coordinator、绑定 callback、新建锁，或取得 0091 success/0092 cancellation authority。Graph、可选 Budget、两类 Outbox 与 0090 decision 的唯一 UoW mutation authority 仍是 DurableTaskStore。
- Graph/Node/Attempt transition、节点 Budget 终结/拒绝和执行失败收口必须由单一 `DurableNodeLifecycleRuntime` 编排。该 Runtime 只能持有同一 TaskStore、共享 `DurableBudgetRuntime`、共享 `RuntimeOutbox` 和 Coordinator 已验证的 owner id；不得取得 Coordinator callback、worker/verifier/acceptance/review authority、TTL 或新锁。九个迁移方法的规范化 AST 必须与迁移前冻结基线一致。pre-reservation failure 只能调用 Store 的原子 bundle，普通响应异常后只允许以同一 callable 和同一 arguments 精确回放一次，第二次失败必须转 durable conflict，禁止 sequential fallback；`CancelledError`/`BaseException` 不得被普通异常分支吞掉。`defer_shared_terminal=true` 时只能 refresh 共享 Budget，不得由节点 Runtime 终结共享 Graph/Budget。TaskStore 仍是唯一 mutation/UoW authority。
- 单节点执行必须由一个 `DurableNodeExecutionRuntime` 承担，并复用 Coordinator 已构造的同一 TaskStore、`RuntimeOutbox`、`DurableBudgetRuntime`、`DurableNodeLifecycleRuntime`、worker resolver、Verifier、Acceptance、Review、只读 Projection、projection reader、owner、两个 TTL 与共享 `AcceptedGraphFinalizationRuntime`。它可调用 `DurableOperationRecoveryRuntime` 与 `CommittedCheckpointContinuation`，但只允许从 exact single-node canonical COMMITTED checkpoint 续跑；不得反向持有 Coordinator callback、新建锁或取得 parallel settlement/cancellation authority。general/pre-operation recovery仍必须 fail closed，multi-node/batch只允许安全hold。
- 0091 整图成功编排必须由独立 `ParallelSuccessRuntime` 承担，并且 slots 只允许同一个 TaskStore、共享 `RuntimeOutbox`、共享 `DurableBudgetRuntime`、`projection_reader` 与共享 `NodeExecutionProjection`；不得反向持有 Coordinator callback，不得新建锁，也不得取得 0090 settlement、0092 cancellation 或 persistence authority。迁出的 `complete` 与纯 `_projection_route_authority` 必须分别保持迁移前冻结基线的规范化/精确 AST，Coordinator 不得保留旧 wrapper。调用矩阵固定为 Store `get_graph` 2 次、`complete_parallel_graph_success` 2 次、`read_scheduler_snapshot`/`get_latest_parallel_success_decision`/`get_node` 各 1 次，Outbox 4 build/2 publish/0 best-effort，Budget refresh、projection reader load 与 NodeExecutionProjection try_operation 各 1 次。`parallel_success_records.py` 只能拥有 route-authority 纯投影，不得持有 Store 或 runtime authority。
- outer DAG 调度必须由 `DurableDagSchedulingRuntime` 承担，并复用同一 Store、共享 Outbox/Budget/Lifecycle/NodeExecution/Projection/0090/0091/Cancellation/Review、共享 `AcceptedGraphFinalizationRuntime`、flags、reader 与 progress buffer；不得反向持有 Coordinator callback、创建新锁或取得第二 persistence authority。当前调用矩阵冻结为 Store 9、Budget 8、Lifecycle 17、AcceptedFinalization 1、NodeProjection 13、NodeExecution 7、Settlement 4、Success 1、Review 4、Cancellation 1、Outbox 1 build/6 publish。1,343 行 Runtime 仍是临时 outer orchestrator；不得据此声称 mixed-responsibility、历史 reconciliation 或一般 recoverable Attempt continuation 已完成。结构证据为 `tests/unit/test_dag_scheduling_runtime_structure.py` 与 `tests/unit/test_node_lifecycle_runtime_structure.py`。
- `AcceptedGraphFinalizationRuntime` 必须是 normal Node、online continuation 与 outer all-ACCEPTED re-entry 共用的唯一 legacy Graph completion boundary。effects 启用时，瞬时 worker payload 只可用于本次正常路径；重启路径必须经 Store `get_committed_operation_checkpoint()` 证明 exact `COMMITTED` Reservation、唯一 COMMITTED Outbox、Attempt、历史 Lease/fence 和 hash receipt，再验证 canonical WorkerProjection 全 lineage，最后复用 `transition_graph_and_stage_acceptance_effects()` 原子提交 Graph+Effect。缺失、重复或篡改证据不得完成 Graph；重复重放不得新增 Effect或重跑 worker。
- 在线 Operation 接管只能定级 `SINGLE_NODE_CANONICAL_COMMITTED_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE`：除 canonical COMMITTED checkpoint 外，worker BudgetUsage 必须唯一归类为 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING / CONFLICT`。前两类逐字段精确且只读重开；`MISSING` 要求稳定 usage ID 与所有 worker-kind row 均缺失，并只可在更高 fence 下使用正 durable elapsed 幂等补写；`CONFLICT` 永远阻断。settlement 写响应与首次读回同时丢失时，原 owner只返回不附加 lifecycle/finalization mutation 的 recovery hold，不得释放 Lease/Budget或覆盖可能已提交的 usage/`EXCEEDED`；旧 Lease 到期后，单节点接管保留原 Attempt、不重跑 worker，并继续到 Verification/Acceptance 或精确 `BUDGET_BLOCKED`。DAG outer 在存在active Operation checkpoint时必须优先调用continuation，不能先用aggregate `EXCEEDED`封板；`EXACT_REJECTED`也必须取得higher-fence Lease并在收口时释放新Lease。旧 Lease 未过期、零/非法 durable time、pre-operation、multi-node/batch 或不确定副作用都不得完成 Graph；多节点/批次当前只有安全 hold，没有自动恢复。
- `NodeExecutionProjection` 只能持有同一 TaskStore 与只读 `projection_reader`，terminal replay、parallel decision selection、Attempt snapshot 和 operation lookup 不得写 Store、持有 Outbox/Budget/worker/review 或建立锁/callback；其五个方法必须保持迁移前规范化 AST。`node_execution_records.py` 只能包含 `build_review_context` 与 node-local 纯 helper，不得持有 Store 或 runtime authority。TaskStore 继续是唯一 mutation/UoW authority。
- `_execute_stream_impl` 外层作用域不得绑定名为 `terminal_event` 的局部变量；该名字保留给 canonical receipt renderer。Graph、parallel batch 与单 Node 的终态事件必须使用不同局部名，避免 Review/Recovery 等先于 re-entry 的分支因 Python 作用域遮蔽而失去 renderer。
- 0092 cancellation mutation 按 request 与 advance/finalize 拆为无状态 mixin；三个写方法各自必须恰好一个 owner transaction，所有直接 SQL 和 Graph/Budget/Outbox/CAS/evidence helper 都复用该 `conn`。request/finalize 对 0091 的 first-writer 查询必须位于同一事务快照；公共 evidence builder 继续调用主 Store `_operation_record()` 完整验证 reconciliation proof，不得在 mixin 内复制或降级。
- 0092 Acceptance Effect fence 必须把失败状态视为不确定事实：一般 handler/transport `FAILED` 不得自动写成 `FAILED_EVIDENCE`，取消请求观察到既有 `FAILED` 时必须创建 `RECONCILIATION_REQUIRED`，`CLAIMED` 在取消后转 `FAILED` 也不得解除 fence。只有带 durable receipt 的 `COMMITTED` 可自动成为 `COMMITTED_EVIDENCE`。当前 schema 没有可信 `NO_SIDE_EFFECT` attestation 时一律保守阻塞；旧 `FAILED_EVIDENCE` 可兼容读取，但属于历史修复/隔离范围，不得作为新生产写路径。`PENDING` 还必须先由 canonical codec 验证完整 tuple：仅 `attempt_count/fencing_token/status_version` 全 0 且 `reason_code/last_error` 均空的 virgin epoch 可写 `CANCELLED`，retry epoch写 `RECONCILIATION_REQUIRED`，混合 tuple 按 durable corruption 拒绝。由于 production claim 要求 `SUCCEEDED + ACCEPTED` NODE（随后被 cancellation 排除）或 terminal `COMPLETED` GRAPH，可进入 fence 的 retry `PENDING` 在当前 canonical API 不可达；该分支只用于 legacy/corruption hardening，不得伪称正常 writer race。历史 retry-PENDING + CANCELLED fence 必须显式审计、修复或隔离；已有 `FINALIZED` decision 的 replay 不得自动改写 immutable evidence。
- Operation 与 Effect 签名对账的受限 service、production composition、OBS Operator command 和 verification-only Ed25519 公钥注入已同时存在，均只能定级 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`。Effect proof 必须签名绑定 immutable case、intent、effect、fence、Attempt、resolution、认证 actor、attestation、Outbox 与 positive `graph_revision`；`NO_SIDE_EFFECT` 只能解析为 `CANCELLED`，`SIDE_EFFECT_CONFIRMED` 必须带 durable receipt并解析为 `COMMITTED_EVIDENCE`。历史库存 remediation、历史半状态、general recovery、部署密钥/凭据、完整 key lifecycle 与真实 canary 完成前，durable cancellation 必须保持 `PRODUCTION_OFF`。
- Retry mutation 与 response-loss replay 必须分离职责但共享调用方当前 `conn`；任何 operation reservation 都在 Node/版本 mutation 前关闭自动 retry，existing BudgetUsage 是唯一 replay 入口。BudgetUsage、Budget/Attempt/Node/Lease/Outbox、retry deadline 与 legacy/node-only Graph disposition 必须在同一事务内精确校验。Atomic failure 只原子关闭 Attempt/Node/Lease、可选 Graph 和 3/4 条 Outbox，明确不得取得 Budget mutation authority；Budget finalize 仍在 bundle 外。
- DAG state 与 READY claim 拆分模块只能借用主 Store authority。public `create_graph()` 与 `insert_graph_in_transaction()` 已删除；`_insert_graph_from_host_command_in_transaction(conn, ...)` 只允许 `HostCommandRepository._admit_normalized()` 在既有 `BEGIN IMMEDIATE` admission UoW 中调用一次，Graph、Node、Graph Outbox 与 `host_command_journal` 同成同败。Graph/Node `INSERT` SQL 只能定义在该私有 helper。`insert_outbox_in_transaction()` 与 Graph 私有 helper 都必须要求调用方提供已开启事务的 `conn`，不得自行 begin/commit。scheduler snapshot 必须在同一个只读事务内取得 Graph、Node、Attempt、Lease、Budget 与 usage。READY claim 必须在一个 writer transaction 中复核 cancellation、Graph/Budget/dependency/capacity，提交 Attempt、Node、Lease、初始 Heartbeat 与三条 Outbox；response-loss replay 必须复用该 `conn`，不得再占并发槽、取得新时钟或写入第二份事实。Retry promotion 与 claim 当前仍是两个各自原子的事务，不得把结构拆分描述成已合并。
- 0102 是 TaskNode 输出的 durable authority。canonical Node Acceptance 必须由单个 Store UoW 同时冻结 exact NODE Effect 集（允许为空）、写 immutable artifact、推进 `Node ACCEPTED` 并追加绑定 exact Attempt 的 Node Outbox；任一 contract、digest、Attempt、receipt、effect set、CAS 或 Outbox 漂移都必须整笔回滚。丢响应只允许重验全部 durable lineage 后精确重放，不得在 Acceptance 后补插缺失 NODE Effect。后继只解析 compiler 生成的同 graph/revision 前驱 ref，并在 worker 首次调用前把验证后的 artifact 冻结为输入。
- NODE/GRAPH Effect delivery 在 0102 存在时必须要求同 graph/revision/node/Attempt 的有效 artifact，并重算其 contract/payload digest；一个旧的 `SUCCEEDED Attempt` 不得借用另一个 Attempt 的 Acceptance。新 HostCommand/Task admission 必须拒绝任一空或非 canonical `output_contract`；0102 readiness 还必须校验 migration asset checksum 与完整 table/column/FK/index/trigger snapshot，并在路由开放前把非终态活动 legacy 空合同、`ACCEPTED` 但缺 artifact 的节点，以及缺 exact artifact 的可投递 terminal legacy Effect 定级为 reconciliation required。READY mode 按 Store 缓存，只有显式 `force_revalidate` 重扫。
- 应用 Store 的 public `transition_node(ACCEPTED)` 在 0102 存在时必须先验证 artifact，normal/checkpoint/Review 均只调用统一 `commit_node_acceptance()`。Graph standalone create、public transaction insert 与 standalone Effect pre-stage 三个 Python 公共入口均已删除，定级为 `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED`；测试只能在 `tests/task_runtime_authority_fixtures.py` 通过私有 transaction hook 构造显式 raw/orphan 状态，该状态不写 `host_command_journal`，不得当作 canonical admission。SQLite 仍允许 orphan Graph 和直接 raw SQL，本轮没有新增 migration 或跨 Graph/Command/Effect/artifact/Node/Outbox trigger，因此必须同时标记 `SQL_DIRECT_BYPASS_PRESENT`；不得宣称数据库物理单写、NE-01 完成或通用恢复完成。
- Coordinator 的 record validation、不可变 execution receipt/terminal rendering、node execution records 与 cancellation payload/state 纯投影必须位于纯叶子模块；这些模块不得持有 TaskStore、runtime port、锁、task、Coordinator 实例或绑定回调，也不得反向导入 `coordinator.py`。`GovernedExecutionReceipt` 的旧 Coordinator/package 导入必须与新定义保持 object identity。`NodeExecutionProjection` 与 `GraphCancellationProjection` 是独立的只读 stateful leaf，只能持有各自冻结的只读依赖并调用只读方法白名单；`GraphCancellationRuntime` 只能持有同一个 TaskStore、共享 `RuntimeOutbox`、共享 `LocalGraphExecutionRegistry`、lease TTL 与该 Projection，不得反向持有 Coordinator、绑定方法、callback 或新增锁。Coordinator 的公开取消方法只作薄委托，执行重入直接委托该 Runtime continuation。
- `RuntimeOutbox` 是每个 Coordinator 唯一的进程内 Outbox 构造/发布锁域，并与 `GraphCancellationRuntime`、`ParallelSettlementRuntime`、`DurableNodeLifecycleRuntime`、`DurableNodeExecutionRuntime`、`ParallelSuccessRuntime` 共享同一实例：所有 message 的 deterministic `outbox_id/idempotency_key` 必须继续只绑定 command/type/id/version/event 五段升级前材料，0095 不得把 revision 加入该 digest，以免破坏已提交响应丢失后的 exact replay；Graph 的新精确维度必须放在独立 `graph_revision` 列和 revisioned `payload_ref`。seed/final evidence 不得改变 outbox id；同步/异步 publisher 共用同一锁，best-effort 只能降级普通异常，不能吞 `CancelledError`。跨 Coordinator 与五个提取 Runtime 的联合 AST 矩阵必须保持 27 个 build、31 个 publish、58 个调用和 5 个 best-effort publish；其中节点生命周期 Runtime 必须精确持有 6 build/3 publish/1 best-effort，节点执行 Runtime 必须精确持有 6 build/14 publish/4 best-effort，Parallel Success Runtime 必须精确持有 4 build/2 publish/0 best-effort。搬移 owner 不得删减调用或建立第二发布锁。`ProcessTaskRuntimeContainer` 必须唯一构造并向 Host、Internal、Scheduler Coordinator 与 0094 worker 注入同一 process-shared `LocalGraphExecutionRegistry`。Registry 只持 graph+revision→Task 集合与一把锁；`execute_stream` 的完整迭代必须位于 `track()` 作用域，cancel/wait/gather 必须在锁外，正常、异常、外部取消和 async-generator `aclose` 都必须清空注册。Registry 不是 durable 取消 authority；只有 durable intent 已提交或 exact replay 且 binding 校验通过后，才允许先尝试 publish、再在 `finally` 本地 cancel/join、最后 continue。
- Task Runtime Outbox 跨进程投递必须以 0093、0095 和 `TaskOutboxDeliveryStoreMixin` 为唯一 authority。Claim 必须持久化 owner、不可猜 token、expiry 和单调递增 fencing token；过期 claim 可 takeover，但新 owner/token/fence 必须使旧 worker 的 complete/release fail closed。predecessor/head identity 必须是 `aggregate_type + aggregate_id + graph_revision`；同一 revision 下未发布 predecessor 必须阻止后续 version，不同 revision 必须能独立推进，revision 内顺序键为 aggregate version、created timestamp 与 outbox id。`RuntimeOutboxPublisher` 必须逐条执行 `claim → idempotent append_task_outbox → fenced complete`；append 失败必须以同一 claim 进行 fenced release，并持久化 retry deadline/backoff 与 last_error。Composition 必须用 boot/construction-scoped `recovery_id::outbox` 作为 publisher owner，不得复用逻辑 owner 充当跨重启权威；启动发布后必须运行可关闭的后台续投线程，按 aggregate-head backlog 的最早 available/claim-expiry 唤醒，并保留有界轮询兜底。Publisher close 必须与前台 publish 线性化并等待已进入的发布临界区，禁止 close 返回后继续访问下游 Store；event 已 append/complete 后的 backlog 只读失败只能返回脱敏 `DEGRADED`，不得把已提交投递伪报为失败。显式 drain 只有 canonical `total_pending=0` 才可声明 `DRAINED`，否则必须返回 `DEFERRED`；`list_pending_outbox` 必须保留完整 PENDING inventory，只有 claim/backlog API 可以表达 delivery-ready 语义。0093/0095 readiness 必须保存在单个 Store 实例内：`verify_schema=True` 的构造或显式 `assert_outbox_delivery_ready()` 负责验证，热路径只消费缓存的 READY mode；只有显式诊断 `force_revalidate=True` 可重扫 PRAGMA/schema/全表。0094 pending-cancellation coordination 使用独立 Store/worker 和 0092 业务 authority，不得复用 Outbox claim。
- 0095 `task_runtime_outbox.graph_revision` 对 `TASK_GRAPH` 必须是引用现存 Graph 的正整数；0098 只允许 `GRAPH_CANCELLATION_EFFECT_RECONCILIATION` 作为第二种 revisioned aggregate；其他 aggregate 必须为 `NULL`。`PRAGMA table_info` 必须精确为 `INTEGER / notnull=0 / default=NULL / pk=0`。Migration Runner 必须注册 deterministic `natsume_sha256_hex`。迁移回填允许 0090/0091/0092 decision、0092 intent、Attempt lineage、该 graph ID 唯一 durable revision、HostCommand immutable `command_id` evidence 对 CREATED 的绑定，或以 revision-bound HostCommand command ID 重算升级前五段 governed digest 并同时匹配 legacy `outbox_id + idempotency_key`，从而精确恢复 ACTIVE 等非 attempt transition。多个 authority 冲突或自定义 producer 历史行仍无法唯一归属时，整个 `BEGIN IMMEDIATE` 事务必须失败，禁止按时间、aggregate version 或当前 revision 猜测。新 `TASK_GRAPH` payload ref 必须是 `taskstore://event/TASK_GRAPH/{graph_id}@{revision}/{version}`；旧 ref 只能由 durable codec/replay compatibility 读取，新 insert/update trigger 必须拒绝。readiness 与 receipt-loss postcondition 必须验证 physical column shape、column check、revision/payload trigger、revision delivery index、`TASK_STORE_OUTBOX` source_ref 的 unique partial index 与全表身份。所有生产 Graph Outbox producer 必须显式绑定 revision 和新 payload ref；所有 decision getter 必须复核 exact Graph Outbox revision，完整 `taskbatch` marker 扫描不得跳过 multi-revision 图。新 Runtime Event 的 Graph aggregate key 必须为 `graph_id@revision` 并在 payload 保留 revision；pre-0095 journal 必须保持不可变，exact-revision 查询只能通过 `source_ref` 连接 canonical Outbox revision 兼容读取并按 cursor 去重。每个 TaskStore Outbox 的 `source_ref` 只能对应一个 Runtime Event；升级前 event append 已提交但 Outbox complete 未确认的重投，只能在现存事件与可重建 legacy/current event 全字段精确一致时返回原 journal，不得追加第二投影。Operator read model 必须向 ReviewStore 传 exact revision，ReviewStore 只接受 `task_graph_id + positive graph_revision` 组合，并按 canonical URL-escaped `task-node://<graph>/<revision>/...` subject prefix 精确过滤。
- 0098 对 0095 的唯一窄扩展是：`GRAPH_CANCELLATION_EFFECT_RECONCILIATION` Outbox 也必须携带 positive `graph_revision`，其 revisioned payload ref、aggregate case ID 与 0098 immutable case inventory 必须由 insert/update trigger 物理一致性约束；除 `TASK_GRAPH` 和该 aggregate 外，其他 Outbox 仍必须保持 `graph_revision=NULL`。签名 binding、物理 Outbox 列与 case revision 三者必须一致。
- Dedicated Obs 对 Effect reconciliation 的读取必须来自 ledger canonical DB，而不是构造另一 `DurableTaskStore` 或复制 projection。`EffectReconciliationQueryRepository` 只能继承 read/validation mixin，以 SQLite URI `mode=ro` 且 `PRAGMA query_only=ON` 建立 operation-scoped connection，不得暴露 mutation、transaction 或 UoW。只有 `HOST_NLE_OPERATOR_VIEWS_ENABLED=true` 才构造并执行 0098 readiness；false 必须完全不触碰 0098。candidates/cases/challenge 三条 GET 必须精确要求 `obs:task-runtime:read`，通用 `obs:operator:read` 不得继承权限；cases/proof 输出最小化，exact subject 必须以 canonical-JSON base64url + SHA-256 专用 `signing_material` 交付，不得依赖 privacy serializer 会移除的原始字段名。
- 0096 Review continuation 只能保存协调元数据，ReviewStore 与 TaskStore 继续分别拥有 decision/token/checkpoint 与 Node/Attempt/Lease 真相。Task-bound create 必须在同一事务验证 canonical subject、outer graph/node、Node waiting/version、PRE 无 Attempt或 POST exact SUCCEEDED Attempt；终态 mutation 与 enqueue 原子提交。secret-free authority 必须是固定字段集，原 claim token不得跨 surface。READY claim 在创建 Attempt/Lease 的同一 `BEGIN IMMEDIATE` 复核 live claim；worker 首次调用前复核 exact Attempt、Lease、owner 与 fence。credential 可覆盖 `budget time + child TTL` 但不得超过 24 小时；它不能绕开 live hand-off。首次 worker 校验后由 Task lease 管理已开始的 blocking 调用，Review heartbeat 丢失不得被表述为可撤销外部副作用，stale owner也不得 complete/defer。claim、expiry 与 reconciliation 必须逐候选隔离 poison 行；Internal/Scheduler denial 使用 exact Review decision ref，多 Review DAG 不得错绑当前 consumed claim。历史 POST pre-acceptance result 仅允许在 exact HostCommand、Graph/Node/Review/version 与旧 receipt 全匹配的单事务 CAS 中替换。
- 0097 必须为 `S_TASK_GRAPH_NODE_GRANT` 冻结 nonempty request/intent identity 与 partial unique `(request_id, intent_id)`。Ledger batch append 必须先取得 `BEGIN IMMEDIATE` writer authority，再重查 deterministic batch intents；并发 loser 只能读取 winner 的 exact batch并返回 replay。任意节点写入失败必须回滚全批；历史重复必须阻断迁移，禁止静默去重。Review HTTP 决策只能使用 server-owned `review.operator / review:decide` policy，不能复用 `internal:agent:execute`；actor/requester 必须绑定认证 principal，Task-bound Review create 不得暴露给 HTTP。
- Runtime Event 的 `created_ts_ms=0` 是合法 source timestamp，不得被 truthiness fallback 改成当前时钟。由于 current revisioned 与 legacy unrevisioned 两条历史 append 路径都可能以时钟替代零值，`source_ts=0` 时两种可重建 candidate 都允许其历史 journal timestamp 为任意非负值；event ID/schema/type/source/aggregate/payload/evidence/dedupe/redaction 等除 timestamp 外的字段必须继续 exact，source timestamp 非零时 timestamp 也必须 exact。
- `read_scheduler_snapshot(..., include_graph_outboxes=False)` 必须默认不扫描 Outbox，普通 scheduling 与 Operator 热路径不得因 0095 增加该开销。PlanningRecovery 必须显式传 `include_graph_outboxes=True`，并在原同一只读事务中取得当前 exact revision inventory；生成 failure evidence 时必须选出恰好一条与 Graph id/revision、terminal status event 和 status version 匹配的 durable Graph Outbox，直接使用其真实 `payload_ref`，不得把 pre-0095 legacy ref 合成为数据库中不存在的新 ref。只有旧版或替代 Store 实现未提供可选 `graph_outboxes` 扩展字段时才允许合成 revisioned fallback；生产 DurableTaskStore 在显式请求后始终提供该字段，字段存在但类型错误、缺行或多行歧义时必须报 durable drift。

### 3.4.1 Durable cancellation

- Application 取消入口必须只接受 server-owned structured authority，并由 `GraphCancellationAuthorityVerifierPort` 验证 request、principal/source/reason 与 exact `graph_id + revision`；调用方不得提交或覆盖 `request_id`、`request_identity`、`source`。Host、Internal、Scheduler 三个生产运行时必须共享同一个 Secure Ingress verifier；没有 verifier、绑定漂移或无法解析既有 durable admission 时 fail closed。
- 受控 Operator 入口固定为 `task_graph.cancel`，payload 只含 `graph_id + positive graph_revision + canonical reason_code`，权限为 `obs:write + task-runtime:cancel`。Operator Command Gateway 必须认证 canonical envelope 并持久化 audit/claim；verifier 必须重新读取 committed Secure Ingress authority，逐项复核 audit/claim、request/principal/tenant、认证 body digest、action、scopes、path/method 与 allow/complete 状态，再以 `audit_ref` 签发 canonical `request_identity`。`claim_result_ref` 必须仍为空；terminal outcome 一旦写入，旧 authority 不得被进程内复用为新取消 bearer，公开 duplicate 只能重放原 outcome。同源 BFF 与 UI 只能选择 exact revision，不得直写 TaskStore 或乐观显示 Graph 已取消。
- `HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED`、Operation、Effect 与 `HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED` 四个 Operator 开关默认 `false`；开启 cancellation 时三类 reconciliation 必须全部开启。`HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED` 默认 `true`，但只能处理从 canonical durable evidence 唯一重建的 `EXACT_REPAIRABLE`，不得处理 signed/quarantine case。任一 Operator 入口启用都要求 Secure Ingress=`ENFORCE`、可执行 Operator Gateway 与有效 `HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON`；Host 只能加载 verification-only Ed25519 公钥，不得加载私钥或签名器。真实 Operator canary 通过前仍不得把 cancellation 定级为生产验收通过。
- Migration 0099 必须保持 schema-only，不得在迁移内扫描、inventory、backfill 或改写 Graph/Budget；它只安装 immutable case/proof/supersession authority。Readiness 必须从受信 0099 migration 资产在隔离 SQLite 中生成基准，对 live `sqlite_master` 严格比较 3 张 table、4 个 index 与 10 个 trigger 的 canonical SQL；同名 no-op trigger、错列 index 或 table constraint 漂移都必须 fail closed。SQLite 约束不得代替 Python codec，case/subject/proof 仍需以 canonical JSON、digest、列绑定和 authority matrix 复核。Startup inventory 在显式 10,000 terminal Graph 上限内一次覆盖 current anomaly，并严格区分唯一 graph-scope Budget 与 child/missing/multi Budget；后者以及 PENDING/CONSUMED、Graph/Node/Review/Attempt/Lease/parallel-after-state 漂移一律 `QUARANTINED_STRUCTURAL`。并行终态 authority 对 0090 必须恰好一条 canonical decision，0091 必须复用完整 decision/outbox/Attempt lineage validator，0092 必须复用 trusted final-decision 与 Effect-overlay validator；Attempt failure bundle 还必须精确验证 Attempt/Node/Graph/Lease 四条 Outbox 的 canonical `payload_ref`。`EXACT_REPAIRABLE` 可接受 system durable evidence 或签名 Operator，`SIGNED_REPAIR_REQUIRED` 只接受签名 Operator，quarantine 不得修复。Runtime 重入看到 terminal Graph 时必须在新建/激活 Budget 前零写拒绝；同 Graph/revision 的已 inventoried 未决 current case 必须为 graph-wide 围栏，拒绝所有普通 TaskGraph transition/cancellation request 及 Budget transition/consume/usage/retry writer。只有 0099 proof UoW 可显式 bypass 该围栏。Proof 只能在同一 DurableTaskStore UoW 中 CAS exact Graph/Budget、写确定性 Outbox并追加 immutable proof；旧 case 只可由同事务冻结的新 current case supersede，替代链最终仍须有 proof，不得创建 Attempt/Lease、重跑 worker或修改0090/0092 authority。Cancellation production gate必须阻断 current anomaly、缺失 trusted proof/supersession closure 的 case与当前 quarantine，但不得阻断单独启用的 remediation Operator。
- 0099 的 `candidates/cases/challenge` 在独立 Obs 与主 Host 都必须使用同 canonical SQLite 的 `mode=ro + query_only=ON` reader并要求 `obs:task-runtime:read`；不得 fallback 到完整 `DurableTaskStore`。Challenge actor只能取 OperatorQueryGateway 已认证 principal，key binding存在时必须拒绝请求 key 漂移；ref/key/time、case/subject、action/targets和确定性 Graph/Budget Outbox必须进入 Store 实际验签的同一 canonical bytes，返回的 `command_request` 必须可由真实 Ed25519 签名后直接通过 service/Store，UI摘要不得冒充签名 payload。Auto 必须在 Store 查询层只取 `EXACT_REPAIRABLE`，循环到空或明确报告 `TRUNCATED`，不得被 signed case 饥饿。`SYSTEM_DURABLE_EVIDENCE` 必须使用 immutable case `created_ts_ms` 生成时间稳定的请求/proof identity，而不是进程启动时钟；并发进程必须收敛到同一 proof/replay。批处理中后续 candidate 或 completion read 失败时，如前缀已提交，必须返回 `side_effects_may_have_run=true` 和已提交数，不得报成零副作用。`TERMINAL_HEALTHY_CLOSURE` 只能在同 Graph/revision 存在 unresolved unsuperseded 历史 case 且 current 完整 classifier 返回 healthy 时创建；它固定为 `SIGNED_REPAIR_REQUIRED + NONE`，只允许 `SIGNED_OPERATOR`，Graph/Budget/Outbox 零写，snapshot drift 必须继续以 replacement + supersession 追加。
- Operator 路由必须先从 HostCommandRepository 精确解析 immutable surface，再回到 Host/Internal/Scheduler owner。下游异常后必须读取 exact revision 的 durable cancellation intent 判定副作用确定性：只有 intent 的 request、audit identity、source、reason、graph/revision 与本次命令全部一致时才可标记 `side_effects_may_have_run=true`；确定无 intent 时必须返回明确未执行，读取失败、畸形或无法证明时保守标记不确定。命令路由只能信任注册 port 显式抛出的 OBS 封闭执行异常；任意依赖异常的 duck-typed `reason_code/side_effects_may_have_run` 不构成 authority，必须保守按可能已执行处理。`CancelledError` 必须原样传播。
- 取消是两阶段 Graph authority。`request_graph_cancellation` 在一个 Store UoW 中写入 canonical intent，将 Graph 推入 `BLOCKED`，并为非终态 Node、active Attempt/Lease、Review/resume token、operation 与未授权 effect 建立取消或 reconciliation 状态；只有 durable intent 返回后，`GraphCancellationRuntime` 才能通过共享 registry cancel/join 本进程 child 与 heartbeat，Outbox publisher 异常不得阻止该本地停工。已有 intent 仅允许 `request_id/request_identity/source/reason_code` 四字段完全相同的 replay。完整 reconciliation evidence 由 canonical decision 摘要绑定，operation proof 在封口与 getter 中完整验签；终态 Outbox 只保留 decision、intent、request identity 三个稳定 manifest authority ref，不展开可增长的证据清单。
- 本地 task registry 只用于加速 join，不是 authority。重启或另一进程只能依据 0092 intent/decision、Graph/Node/Attempt/Lease/operation/effect/review durable facts推进；不能因本进程没有 task handle 就假定取消已经完成。
- 0094 `task_runtime_graph_cancellation_continuation` 只能保存跨进程协调元数据，不得复制 cancellation status 或 decision；0092 intent/decision 始终是唯一业务 authority。Schema readiness/codec 必须强制 exact frozen column set，任何额外 `status` 或其他业务字段都 fail closed；readiness 必须精确冻结 `PRAGMA table_info` 的 name/type/notnull/default/pk，并要求 `foreign_key_list` 只有一个 `id=0`、`seq=0/1/2` 的三列复合外键，禁止拆成三条单列 FK 或增加额外 FK。`intent_ref` 必须是 exact `TEXT NOT NULL PRIMARY KEY`，所有必填 identity/counter/timestamp 列都必须保持 `NOT NULL`，不允许 SQLite rowid PK NULL/复合 FK NULL 豁免产生幽灵协调行。`GraphCancellationContinuationWorker` 必须由 `ProcessTaskRuntimeContainer` 拥有，在 runtime activation 后启动，并使用 boot/construction-scoped `recovery_id::graph-cancellation` owner。Store claim 必须绑定 owner、不可猜 token、expiry、单调 fencing token、attempt、available deadline 与 expected version；heartbeat/defer/complete 必须以 exact owner/token/fence/version CAS，过期后才允许新 owner takeover，stale worker fail closed。
- claim/heartbeat/defer/complete 必须在 `BEGIN IMMEDIATE` 成功获得写锁后再采样 trusted clock 并计算 expiry/available deadline；禁止用进事务前的旧时间决定 lease 或 CAS authority。
- continuation worker 每次 claim 必须从 `HostCommandRepository.get_by_graph(graph_id, graph_revision)` 重建 canonical command、work order、assignment、graph 与 `AdmittedExecution(replayed=true)`，并核对 exact Graph binding 后调用既有 `GraphCancellationRuntime.continue_graph_cancellation()`；禁止复制一套取消编排。未显式传入 `local_pending` 时，Runtime 必须先对 process-shared registry 执行 `cancel_and_join`；非空 pending 必须阻断 advance/finalize，worker 只能 defer。仅当 0092 intent 已 `FINALIZED` 且 canonical decision 存在时才能 complete/删除协调行。若 runtime 返回后 operation/effect/review 等 durable facts仍未收敛，必须以稳定且不泄密的错误码 durable defer；heartbeat 丢失 authority 时不得再发 stale complete/defer。
- activation 必须原子暴露：Coordinator 已激活但 continuation worker `start` 失败时，必须尝试反向 `worker.stop → worker.join → coordinator.close/aclose`；仅在全部回滚成功后解绑 Coordinator，任一关闭失败都必须保留 bound handle 供 `aclose` 重试。`GovernedRuntimeGroup` 也只移除已成功关闭的 child，失败 child 必须留存供下次 close。任何 activation rollback 或 `aclose` quiescence 未完成必须使用统一 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，并锁死 bind/activate，直到 `aclose` 重试真正收口。shutdown 必须经过有界 grace、cancel 和有界 join；若协程拒绝响应 cancellation，容器必须有界报错且保持 TaskStore/HostCommandRepository 开放供下次重试。Coordinator admission close、drain、`aclose` 三步任一失败都不得关 Store；只有 Coordinator 和 worker 均已 quiescent 后才能 drain Outbox 并关闭 infrastructure。
- `advance_graph_cancellation` 只能在 ownership 已稳定后推进 `CANCEL_REQUESTED`；`RESERVED/RECOVERY_REQUIRED` operation、待对账 effect、active Attempt/Lease 或 live Review/token 均阻断 finalization。0098 必须是 append-only sidecar，不得更新或删除既有 0087/0092 Effect、fence、decision/evidence。PENDING proof 可进入之后新建的 cancellation decision evidence；`LEGACY_FINALIZED_CORRECTION` 只能绑定当前 immutable legacy decision ref/payload digest，并作为 trusted-read overlay，绝不得把 correction proof 写回旧 decision evidence。`finalize_graph_cancellation` 必须在单一 UoW 中提交 Graph `CANCELLED`、Budget `RELEASED/CANCELLED`、Outbox、evidence 和 canonical decision。
- `ReviewStore` 的 create/approve/resume 必须在自身 `BEGIN IMMEDIATE` 内检查 exact `task-node://graph/revision/node` 是否已有 0092 intent；Graph/Node URI 段统一使用 canonical percent-encoding，TaskStore 撤销、live 查询和 evidence 查询必须复用同一编码口径。同 revision 存在 intent 时 fail closed，Review 先提交则由随后取消事务撤销。不得用 Graph ID 前缀误伤相邻 revision。
- NODE effect 若绑定 `ACCEPTED` Node 与 `SUCCEEDED` Attempt，视为已提交的 acceptance authority，取消后继续派发/重试；其他 NODE effect 与未完成 Graph 的 GRAPH effect 必须被 fence。final Graph 取消不等待已验收 effect 的外部 delivery 完成。
- 0091 success 和 0092 cancellation 采用 first-writer-wins：已存在 terminal success 时拒绝取消，已存在 cancellation intent 时禁止新成功/claim/charge/未授权 COMMIT。request 与 finalize 的响应丢失只能 exact replay；advance 必须从当前 durable facts 幂等收敛，任意 identity、version、snapshot、Outbox 或 evidence 漂移均 fail closed。
- 调用方断开、进程 shutdown 等基础设施 `CancelledError` 原样传播；worker 自发取消转为 `RECOVERY_REQUIRED`，bounded fan-out supervisor 必须立即停批并 cancel/join 未完成 siblings，Graph 保持 `ACTIVE` 等待 durable recovery。两者都不能绕过 verified authority 建立业务取消。
- 上述 Operator authority/入口、Operation 签名对账与 Effect 签名对账只能定级为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`，不能将 B5C 标为完成。0098 production gate 只检查实际持久化的 legacy inventory。Budget 高竞争唯一 winner 已证明，但 general/batch/pre-budget recovery、历史半状态、部署密钥/凭据、完整 key lifecycle 与真实 canary 全部通过前，production cancellation 必须保持 OFF。

### 3.5 Budget

Budget ledger 支持 token、tool call、time、concurrency、retry 和 model cost 的 reserve/charge/release/reconcile。

- 同一 Budget 的超限竞争必须只有一个 rejected winner 能执行 `ACTIVE → EXCEEDED` 并拥有唯一 Outbox；刷新后到达的不同 sibling 必须追加 `ALREADY_EXCEEDED` rejected usage，保持 before/after 为当前版本并引用 winner Outbox，不得再次 transition Budget、产生第二 Outbox或成为 terminal authority。0090 Budget decision 在写入、读取与 replay 时必须以单一读快照重验 terminal Reservation 的精确版本、唯一 winner/Outbox、winner 与 sibling 的 Attempt/Graph lineage、相同 terminal version和 sibling 的 winner 引用。验收必须覆盖 8/16/32 线程、跨进程、restart/replay/interleave 与 drift rejection。
- `DurableBudgetRuntime._charge` 的 CAS attempt budget 必须至少为 `policy.limits.concurrency + 1`，同时保留显式 configured retry floor；不得恢复固定最多 4 次。concurrency=8 时连续前 8 次 `TASK_STORE_CAS_CONFLICT` 后第 9 次成功是稳定单测合同。该规则只是 admitted writer wave 的重试容量，不等于 concurrency 维度已计量或 program-wide Budget 已完成。
- provider 未返回 usage 时记录 `UNMEASURED`，不得伪造精确值。
- ModelAttempt 用量投影的 prompt、completion、total 三维必须保持 `Optional`；只允许在 prompt 与 completion 都明确测得时推导 total。总览、模型分组与时序桶必须同时携带 measured-call coverage 和 lower-bound 标志：部分覆盖的和只能解释为下界，完全缺测为 `null`，真实零则以 measured-call 证据与缺测区分。
- ModelAttempt 的流式 adapter 必须以 typed final response/usage 把 provider 最终计量交回终态 Attempt；当前 `ModelAttemptService.stream()` 没有生产调用者且该合同未接，因此只能标记 `UNMEASURED / NOT_ENABLED`，不得用无 usage 的字符串分块宣称预算计量完成。
- 超限策略 `BLOCK/DEGRADE/REVIEW` 必须有确定性分支和 receipt。
- model retry、tool retry 与 Task retry 都进入同一 root budget。
- Planner、worker 和 provider 不能自行扩大预算。

### 3.6 Review 与 continuation

- PRE_EXECUTION 与 POST_EXECUTION/ACCEPTANCE Review 使用不同状态。
- Resume token 一次性，绑定 subject、version、checkpoint、principal、expiry 和 allowed action。
- 合法 resume 重新调度原图的可继续节点，不重放已确认 effect。
- 拒绝、过期、主体不匹配、版本漂移和重复消费均 fail closed。

### 3.7 Verification 与 Acceptance

- required verification 只有 `PASS` 可进入 Acceptance。
- `FAIL/ERROR/NOT_RUN` 必须阻断或进入显式 Review。
- Acceptance 绑定 verifier result、artifact digest、graph/node/attempt、grant 和预算结算。
- handler 返回成功不能代替 Acceptance。

### 3.8 副作用与一致性

- 外部副作用只能由 Acceptance 同事务写入 durable effect intents 后执行。
- 若 Node `ACCEPTED` 已落盘而 Graph 仍 `ACTIVE`、Effect 尚未落盘，重启必须从 exact COMMITTED Operation checkpoint 与 canonical WorkerProjection 重新编译同一 Effect，并通过 Graph+Effect 原子 UoW 收口；投影/Attempt/Lease/Outbox/grant 任一漂移都 fail closed，worker 不得重跑。
- 多节点全成功的外层 UoW 必须一次提交 `Graph COMPLETED`、共享 `Budget CONSUMED`、Graph/Budget 两条不同 Outbox、整图 effect intents 与 canonical success decision；任何晚故障必须整体回滚，响应丢失只允许 identity 完全一致的 exact replay。
- 整图 effect intents 和最终公开 projection 必须从显式结果节点已经冻结并通过 canonical digest、command/request、route/target、attempt/receipt 与 node-grant lineage 校验的 durable worker projection 编译；其他 Accepted 节点只提供 outcome/evidence，不得争夺结果 authority。Skill 节点同样不得绕过该冻结投影读取瞬时 executor 返回值。Graph 已完成后的 replay 只读 canonical success decision，不重新编译 effect intents。
- dispatcher 对每个 effect 独立 claim、retry、dedupe 和出 receipt。
- 对话、Memory、Life、Gateway 和 Edge 可以是 Acceptance 后最终一致，但必须公开该语义；不得宣称跨系统单事务。
- fire-and-forget task 不得承担不可恢复的 canonical 写入。

## 4. Surface 与 Digital Life 合同

### 4.1 Surface

Surface 输入只包含已裁决事实、授权记忆引用、Scene/Emotion/Relationship 控制和 worker 结构化结果。Surface 可以调整表达，不得：

- 新增事实或升级承诺强度；
- 把 Canon/风格/情绪/候选当作已执行事实；
- 泄漏 raw thought、secret 或未授权私密正文；
- 反写任务、权限、World 或 Memory authority。

### 4.2 Life 状态

Life 由 Command/Event/Reducer/Projection 推进；Surface、Tribe、Scheduler、Inner World 和后台循环只提交贡献或命令，不各自推进平行生命时钟。

用户观察、Natsume Affect、Relationship、Drive、Scene 与 Identity 使用不同 typed state 和明确 writer。重放同一事件流必须得到兼容版本下确定性投影。

### 4.3 Persona 演化

交互统计、里程碑、反馈和模型总结只能生成 proposal：

```text
evidence
  → FeedbackProposal
  → Lab / shadow / regression
  → Review
  → versioned policy or identity activation
  → rollback point
```

禁止后台任务或模型输出直接调用 trait writer。

## 5. Memory、World 与 Knowledge 合同

### 5.1 Memory

- Life Memory 是生活记忆唯一写服务。
- Episodic/Semantic/Vector/Graph/Cache 有明确 source/projection 关系。
- Recall 返回 evidence refs、visibility、confidence、freshness 和 conflict；只读 Context 构造不能写 Memory。
- distillation、forgetting、merge 和 promotion 使用 version/CAS 和审计。

### 5.2 World、Scene 与 Intent

`WorldFact` 至少包含 subject、predicate、value ref、source refs、confidence、observed/valid/expires 和 conflict state。`WorldSnapshot` 绑定 person/workspace/project、fact versions 和 digest。

- World authority 接受受治理 Command；消费者只读版本化 projection。
- Scene transition 写 journal，并输出 expression/proactive/risk constraints 与 TTL。
- Intent Arbiter 合并显式用户目标、任务、关系、场景、风险和权限。
- Scene/Affect 只能收紧，不能取消用户已授权目标或放宽 Governor。

### 5.3 Knowledge OS

Life Memory 与 Project Knowledge 分 namespace。`KnowledgeItem` 具有 provenance、version、validity、confidence、conflict、decision refs 和 promotion state；支持 dedupe、staleness、deprecation 与 rollback。

重大架构和产品选择生成 Decision Record；Canon 不作为普通项目事实数据库。

## 6. Planning and Decision 合同

### 6.1 公共边界

公共 PlanningSearch 只依赖以下 ports：

- `SuccessorCatalogPort`：按虚拟状态动态生成后继；
- `CognitiveRankingPort`：领域认知评分；
- `VirtualTransitionPort`：纯虚拟状态变换；
- `RewardPort`：领域 outcome/reward；
- `MCTSConditionPort`：决定是否进入 L3；
- `CalibrationRepository`：持久 baseline/candidate/prediction/actual outcome/cost。

公共层没有工具执行、网络、TokenService、Memory writer 或 actuator。

### 6.2 三层搜索

- L1：从当前虚拟状态生成合法候选，不只排列根节点候选。
- L2：结合领域模型、经验和约束排序；领域提示词留在 adapter。
- L3：在阈值满足时执行有预算、确定性的 selection/rollout/backpropagation。
- SearchResult 必须编译为 node-bound immutable TaskGraph payload。

### 6.3 Shadow 与 promotion

- synthetic fixture 只用于算法回归。
- production shadow 使用真实 workload、model、source/repo、verifier、成本和延迟。
- 未执行的替代路径不能用 baseline outcome 冒充；需要隔离实验或可验证模拟。
- 达到预注册样本、误差、安全与净收益阈值后，只生成 influence proposal；实际切流仍经 Review。

## 7. Capability、Domain 与 Pentest 合同

### 7.1 Capability lifecycle

```text
source material / accepted trace
  → SkillDraft
  → contracts + verifier + test vectors
  → SBOM + signature
  → quarantine sandbox
  → Review
  → staged activation
  → evidence-based promotion / rollback
```

未经签名、SBOM、隔离验证或审批的 bundle 不可 import/activate。成熟度策略只收紧；自动升级为 0。

### 7.2 Domain Pack

Pack 可提供 manifest、candidate/tool、repository port、knowledge namespace、verifier、risk、benchmark 和 UI contribution。Pack 不拥有 TaskGraph、Scheduler、Governor、Review、Model Gateway、EventBus、Persona 或跨域 Store。

### 7.3 Pentest

- 所有 Run 绑定真实 principal、engagement manifest、target/scope、时间窗和速率。
- side-effect 阶段必须有有效 approval 和执行前 PreActionGate。
- AttackGraph、CVE、工具知识和认知提示留在 Pentest adapter。
- 虚拟 rollout 禁止真实工具；L3 在 durable calibration Gate 前保持 shadow。
- Pentest 知识进入领域 Store；跨域 promotion 走 Canon/Knowledge proposal。

## 8. Gateway、Edge 与设备合同

### 8.1 Gateway

- raw-body 签名/认证在正文解析前完成。
- inbox/outbox/attempt/dedupe/DLQ 使用 durable store；adapter 不持第二 retry 真相。
- 本地 adapter 成功与远端 provider receipt 分开记录。
- reply 使用独立身份和密钥，不回退通用 Edge 凭据。

### 8.2 Edge

- Edge 只依赖版本化共享合同，可独立安装和升级。
- capability handshake 声明协议、设备身份、支持动作和降级状态。
- 物理效果只有一个 actuator owner；重连不会重放已完成命令。
- loopback bridge 默认认证；远程暴露必须显式配置、最小 scope 和审计。

## 9. Query、Observability 与 Operator 合同

- Query Plane 只读 projection，不直接查询业务私有表或全局内存对象。
- Read model 包含 schema version、source cursor、freshness、visibility 和 degraded state。
- UI 写操作构造 Operator Command，重新进入 Secure Ingress。
- Trace 能串联 command、graph/revision、node、attempt、model/tool attempt、verification、acceptance、effect 和 receipt。
- raw thought、secret、cookie 与未授权私密记忆不进入 UI 或 durable trace。

## 10. Model 与 Prompt 合同

模型调用统一为：

```text
ModelRequest
  → RouteDecision
  → ProviderAttempt(s)
  → StructuredResult / Failure
```

- route、timeout、retry、fallback、circuit breaker、usage、cost 与 reason code 可回放。
- 每个 provider attempt 绑定 root Budget 与 trace。
- Prompt 模板版本化，声明 owner、input slots、output schema、visibility 和 rollback。
- 业务模块不得绕过 Model Plane 调私有 provider helper。

## 11. Evolution Lab、Feedback 与 Identity 合同

### 11.1 Evolution Lab

`ExperimentSpec` 绑定 dataset/version、seed、policy variants、budget、isolation、metrics 和 evidence。Lab 只能读取脱敏快照/trace，不能持有生产 writer、Capability side-effect port 或 Edge actuator。

Scenario、benchmark、shadow 和 counterfactual 只输出 `ExperimentReport`/proposal，不直接切生产。

### 11.2 Feedback

成功、失败、用户拒绝、工具可靠性和表达偏好统一转为 `FeedbackProposal`。Proposal 绑定 trace/outcome/confidence/suggested change/rollback，经过 Lab、Review 后才更新版本化 policy、maturity、knowledge 或 identity。

异常不得被吞掉；失败 feedback path 必须进入可观测 evidence。

### 11.3 Identity

每次 release 生成 `IdentitySnapshot`，只包含公开身份、人格核心、关系/表达/主动性/主权 policy 的版本与 digest，不包含记忆正文和私密 thought。

Identity Migration 在隔离环境执行，产出 diff、persona/relationship regression、Review candidate 和 rollback plan。重大差异默认阻断并要求用户确认。

## 12. Runtime Guardian 与 Workspace 合同

### 12.1 Runtime Guardian

统一 dependency health、heartbeat、degradation、recovery、maintenance/sleep 和 SLO evidence。进入/退出降级必须产生 versioned incident；局部 fallback 不能静默覆盖全局状态。

### 12.2 Workspace

- 每个 Task/Experiment 绑定 owner、root、allowed paths、isolation mode 和 cleanup receipt。
- 使用解析后的路径边界，禁止字符串前缀判断逃逸。
- Code/Skill 实验使用隔离 worktree/container；主工作区和 secret 默认只读。
- subprocess 不是安全 sandbox；需要执行不可信材料时必须使用可验证隔离。
- 0101 `agent_workspace_authority` 仍是唯一持久权威：authority 必须不可变绑定 root WorkOrder、graph ID/revision、owner、逻辑 managed-root ref、source、isolation mode 与 canonical allow-list；数据库不得保存或选择物理根路径。
- 租约必须保存 owner、token digest、expiry、单调 fencing token 和 status version。W2 的租约身份还绑定 boot owner、runtime instance 与 PID；同 workspace 执行 scope 必须独占，响应丢失只能同实例精确重放，其他实例在活租约期间必须冲突，过期接管推进 fence。WorkOrder 只可携带服务端派生的逻辑 `workspace_id`，raw lease token 只能由服务端 provider 注入。
- W2 的 allocator/provider 资产为认证 Internal Agent admission 保留：首次分配冻结配置 Git source 的 exact commit，拒绝预存 namespace branch并创建独立 worktree；合法 replay 可验证该冻结 commit 的 descendant。当前 W2 没有正向 Agent Tool consumer。PR #88 第一提交只退役 ReAct 的 `run_skill` / `dispatch_sub_agent` 发现与低层直执行，第二提交另行退役 Visual Agent 直连 HTTP loop；二者都不证明通用 `AgentExecutor`、文件/Web/Browser 或其他 Tool authority 已退役，也不得把 W2 底层资产表述为正在执行 model/tool。未来恢复必须由 planner 创建新的 TaskGraph revision Tool/Visual 节点，并只向该节点注入 exact node Grant 与 `WorkspaceRuntime`。`HOST_AGENT_WORKSPACE_HMAC_SECRET` 未配置时 admission 必须 fail closed，不能回落到 runtime repo 或 caller path。
- heartbeat、顺序 node/attempt handoff、可信 `git worktree remove` cleanup、cleanup receipt、`CLEANUP_PENDING` 启动/后台续作和 ASGI 启停 quiescence 已接线。启动同步 continuation 必须限为 1 条，剩余 durable backlog 由 app-scoped worker 逐行续作并响应 stop probe。Provisioning/allocator/cleanup adapter 的 Filesystem/Git callback 不得运行在 SQLite transaction 中；必须按 workspace→source 的固定顺序取得跨进程锁，在一个 lease-bounded monotonic deadline 内执行，并以前后短事务重验 exact owner/token/fence/status/expiry。heartbeat 引起的 status version 前进合法，后验 authority 漂移或超时不得返回成功。启动/关闭回滚未静止时必须保留 Workspace 与 Task Store handle并返回 retry-required，禁止伪报关闭。
- 当前只做到 `PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED / NO_POSITIVE_AGENT_TOOL_CONSUMER`。Worktree、lease、handoff/takeover 与 cleanup continuation 资产仍有效，但正向恢复前必须实现每个 Tool 操作的 durable fence、失租抢占、accepted HEAD authority、成功 promotion、失败/取消 abandon 与重启恢复；不能沿用旧的“动作间 heartbeat 检查”作为执行保障。还没有 Domain Pack/Codex runtime 接线、cleanup health route、OS/container/tenant 或 handle/reparse-point TOCTOU 隔离。Git worktree 不能被描述为不可信代码 sandbox。
- `/v1/agent/run` 不得继续拥有 route-local Visual Agent 执行 authority。raw Agent Loop/ClawdBot/Edge client 与 `log_agent_loop` writer 必须物理退役，兼容面固定返回 `AGENT_INIT → LOOP_DONE / REJECTED`、`VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED` 与 `side_effects_executed=false`，且不得解析模型、Ledger 或调用执行端口。真实 `/v1/chat` adapter 只允许 `response_format=text` 进入 Bio Ops/Immune/模型链，其他值必须在这些预检及 runtime/model/Prompt/Memory/Ledger 前拒绝；legacy Action Planner 模型入口固定封口，纯 parser 只能产生 `NONE_PROPOSAL_ONLY`。正向视觉自动化只能由受治理 proposal 生成新的 immutable TaskGraph revision，并由独立节点持有 Attempt/Lease/Grant/Budget/Verification/Acceptance/Receipt；兼容拒绝不能作为能力完成证据。

## 13. Schema、Migration 与恢复合同

- durable schema 只有中央、forward-only migration catalog 可修改；service startup 不 inline DDL。
- migration 绑定 id/name/checksum，支持 empty/old/current DB、backup/restore 和 crash recovery。
- 破坏性变更采用 expand → backfill → dual-read/controlled-write → cutover → verify → retire。
- 每个写模型使用 version/CAS、idempotency 和 transactional outbox；projection 可从 authority 重建。

## 14. 验证与质量 Gate

每个 vertical slice 至少验证：

1. 正常路径和 fail-closed 负路径；
2. identity/scope/budget/review/version 绑定；
3. restart、retry、duplicate、cancel 和 rollback；
4. 无第二 writer、第二执行 authority 或不可恢复 fire-and-forget；
5. receipt、trace、privacy 与 operator projection。

算法或模拟测试只能证明其覆盖的合同。生产切流还需要真实 composition、真实 admission 和至少一条真实 golden path。

## 15. 兼容与退役合同

- 兼容 facade 必须声明 owner、usage telemetry、remove-by condition 和 fail-closed 边界。
- 新任务切到新 runtime 后，旧任务由旧 executor drain；不得跨 executor 恢复。
- usage 为 0 且稳定窗口通过后删除旧 writer、registry、route、migration adapter 和测试。
- 不允许长期 dual-write、长期 shadow authority 或“改名后的全局 locator”。

## 16. 状态与执行分离

| 问题 | 唯一来源 |
|---|---|
| 产品必须提供什么 | `../20_prd/PRD.md` |
| 工程必须遵守什么 | 本文 |
| 代码现在是什么 | `../50_project/arch/CURRENT_CODE_ARCHITECTURE.md` |
| 最终架构是什么 | `../50_project/arch/TARGET_ARCHITECTURE.md` |
| 还差什么 | `../50_project/arch/ARCHITECTURE_GAP_MATRIX.md` |
| 当前按什么顺序实施 | `NEXT_GENERATION_EVOLUTION_EXECUTION.md` |
| 历史为何这样做 | `../_history/` 与固定 receipt |

DevSpec 不再保存 `DONE/CODE_COMPLETE`、旧阶段编号或任务进度。任何完成结论必须来自当前代码审计和绑定 commit/environment 的机器证据。
