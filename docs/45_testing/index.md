受众：PROJECT
标题：测试与验收目录

# 测试与验收目录

版本：v2.53
维护日期：2026-07-28
状态：当前证据

## DL-06 主动陪伴、可逆偏好与关系后效应切片

- `test_dl06_legacy_proactive_scheduler_retirement.py`、`test_ne03_proactive_context_cutover.py`、
  `test_scheduling_center.py`、`test_ne01_skill_execution_policy.py` 与 ASGI composition 定向用例验证：
  `proactive_talk` 已从 production registry/catalog/executor/Scheduler-add 输入移除，Host 启动以 CAS
  禁用旧 `proactive_talk_periodic` head；已接纳 occurrence 重开 exact Scene 后只能返回
  `LEGACY_PROACTIVE_TALK_RETIRED`，不读 Bond/EmotionTimeline/Limbic/模板且不产生 delivery intent。
  该定向集合为 `28 passed in 3.00s`；它是本地退役合同，不是真实 Gateway、Edge、用户送达或 DL-06 完成证据。
- `test_dl06_companion_proactive.py` 验证 Scheduler 的主动决定只读取 canonical
  `relationship_v2`、`state.affective`、clock 与 admitted timezone：explicit
  `ALLOWED` 才能发送；`UNKNOWN/DENIED`、repair、未结 episode 与 `GIVE_SPACE`
  regulation 均只会 hold。测试故意提供极端 legacy `state.affect`，证明该字段
  不再拥有主动消息发送权。
- `test_aw04_life_tick_policy.py` 验证同一 frozen snapshot 的 deterministic outbox、
  content-digest trace、scene/grant fail-closed 和 explicit permission。旧早/晚/默认
  三句模板不再是当前 Scheduler 输出合同。
- `test_dl02_affect_fusion.py` 验证 production fusion composition 不会解析
  `HostEmotionRuntime` provider；Input/Tribe composition 在缺少 Life authority 时固定
  `CANONICAL_AFFECT_FUSION_NOT_INJECTED`，不会构造任何 compatibility runtime。
- `test_ne01_host_lifecycle_composition.py` 与 `test_dl02_affect_fusion.py` 现额外验证
  production Host root 不再构造或发布 mutable `HostEmotionRuntime` / global user profile，
  ASGI Tribe 具备 Life authority 时会和 Input 一样走 canonical fusion，且不会解析旧 runtime。
  本切片的最终 composition/输入路由受影响集合为 `148 passed in 39.51s`（其中直接 composition
  子集为 57 项）；
  这不是 Limbic、Endocrine、BodySense、Motive、Bond queue 清零或真实服务栈证据。
- `test_limbic_agent.py` 现验证 production canonical Life authority 会让 Limbic 的 legacy
  persistence、metabolism、timeline append、decay 以及 Bond progress/regress callback 返回
  `RETIRED_CANONICAL_LIFE`，且不会实例化 `EmotionPersistence` 或追加旧 timeline；未知 authority
  值失败关闭，Tribe canonical fusion wiring 也被直接断言。相关 Limbic、fusion 与 Host composition
  集合为 `77 passed in 31.20s`，与前项集合有重叠，不能相加；它不证明 BodySense、Endocrine、Motive、
  Bond queue、真实 Edge/provider 或 DL-07 服务栈完成。
- `test_endocrine_agent.py` 现验证 canonical Life mode 不会消费 legacy cortisol/oxytocin buffer、
  更新 `_last_state`、产生 hormonal hint 或触发旧 endocrine fan-out；stress、Bond、loneliness、task、
  immune 与 generic delta 输入均显式退役。Circadian/MirrorNeuron 的邻接测试还验证它们改为调用受控
  compatibility port，而非写 Endocrine 私有字段。该受影响集合为 `72 passed in 2.11s`；它不证明
  BodySense、Motive、Bond queue、真实 Edge/provider 或 DL-07 服务栈完成。
- `test_limbic_agent.py` 现额外验证 canonical mode 下的 BodySense 只能从 exact
  relationship subject 的 Life snapshot 纯投影，不会解析 global `BodySenseModel`、写 Tribe state、
  调整 silence penalty 或把 legacy BODY_SENSE_UPDATED 再推进 self-affect；缺 subject 明确
  `UNAVAILABLE`。`test_body_sense_skill_tribe_events.py` 验证 retired tick 不会发布伪 update。
  本次最终单一受影响集合为 `187 passed in 28.59s`；它是本地代码证据，
  不证明 Motive、Bond queue、真实 Edge/provider 或 DL-07 服务栈完成。
- `test_dl00_motive_willingness.py` 现额外验证 `get_motive_engine()` 每次返回不同的
  无状态规则对象，同一冻结输入仍得到相同动机投影；配合 Motive/Limbic 定向集合为
  `37 passed in 1.55s`。这只证明模块级缓存已退役，不证明 volatile Bond queue、关系后效应、
  真实 Edge/provider 或 DL-07 服务栈完成。
- `test_ne01_host_bond_runtime.py`、`test_ne01_host_lifecycle_composition.py`、
  `test_ne01_agent_runtime_composition.py`、`test_cortex_agent.py`、`test_bond_async_queue.py`、
  `test_surface_emotion_bond_flow.py` 与 `test_bond_routes_api.py` 现验证 production Host 不再
  构造真实 Bond writer/worker queue，canonical Cortex 不解析 queue，Surface hook 不回退写分数，
  legacy `/bond/event` 返回 `410`。该定向集合为 `84 passed in 12.82s`；这是本地退役边界证据，
  不证明其它 compatibility reader、真实模型、远端 Edge/provider 或 DL-07 服务栈完成。
- `test_dl06_legacy_emotion_timeline_cutover.py`、`test_ne01_host_memory_runtime.py`、
  `test_ne01_host_lifecycle_composition.py`、`test_limbic_agent.py` 与 `test_prefrontal_agent.py`
  验证 production Host 不会打开旧 action log 预热 `EmotionTimeline`，而是返回显式 retired 空投影；
  canonical Limbic/Prefrontal 不会 resolve/cache 该投影，Runtime snapshot 也不会把 user profile 的
  top emotion 或 volatility 升格为七海 self-affect。最终受影响集合为 `169 passed in 12.85s`；它不证明
  其它兼容读取、Monitor、真实 provider/Edge 或 DL-07 服务栈。
- `test_dl06_default_mode_canonical_inner_activity.py`、
  `test_inner_activity_agent_limbic_integration.py`、
  `test_inner_activity_agent_social_brain_integration.py` 与
  `test_aw08_life_memory_legacy_retirement.py` 共同验证：默认模式从同一 canonical Life snapshot 获取有限
  self-affect/relationship projection 后，私密活动不再触碰 legacy EmotionTimeline/SocialBrain/Bond；projection
  不泄露 trigger/evidence、workspace/subject 或 intimacy consent，且 trust/attunement 不会被压成 Bond score。
  `test_tribe_deepening_batch47.py` 同时确认高连续性 tick 不会再调用 legacy Limbic metabolism。连同
  `test_default_mode_agent.py`、相邻 DL-06、Limbic/Motive 与 inner-activity 文件的最终单一集合为
  `92 passed in 44.20s`（4 条既有弃用警告）；这是本地 reader-cutover 证据，不证明其他 compatibility reader、
  Monitor/Review、真实 provider/Edge 或 DL-07 服务栈。
- 上述 Host/Limbic/Endocrine、fusion 与旧 organ 邻接文件的最终合并定向回归为
  `149 passed in 33.81s`。它是覆盖重叠后的单一集合，不能与前述批次相加，也不构成波次集成或真实服务栈验收。
- `test_dl06_companion_target_registry.py` 使用真实 SQLite Life journal 验证 accepted
  reply 与 `CompanionProactiveTargetV1` 同一 relation command 写入；只有显式 `ALLOWED`
  且 provisional episode 被后效应关闭后，read-only registry 才返回 scope-valid target。
  同一文件还验证 Scheduler 只从与 frozen Scene 相符的 registry target 中稳定选择一个。
- `test_dl06_proactive_delivery_reconciliation.py` 使用真实 SQLite Life journal 验证：只有
  immutable `LifeDeliveryReceipt` 与原 outbox 的 id、key、payload/authorization digest 和
  scope-bound target 全部一致时，才会打开一个 `PROACTIVE_MESSAGE` episode；下一次真实用户输入
  才关闭该 episode 并写 aftereffect。它还注入 receipt 已落库但关系对账回调崩溃的窗口，验证
  恢复扫描只重开 deterministic relationship command、不会第二次调用 transport；篡改私有 target
  context 会失败关闭。
- `test_dl06_companion_permission_ingress.py` 使用真实 SQLite migration、Secure Ingress
  principal/credential store、Host route 和 canonical Life journal 验证：只有具有
  `companion:contact:write` 的 `user` principal，且 principal/tenant 精确绑定
  `relationship_subject_id/workspace_id` 时，strict JSON `ALLOWED/DENIED` 才能写入关系状态。
  同一签名请求经过 HTTP duplicate 与进程重启后只重开同一 Life command；跨用户 scope、
  service principal、重复 JSON key 与 `UNKNOWN` 均失败关闭，且不产生关系事件。
- 本次新增定向集合为 delivery reconciliation/policy/registry/relationship/outbox `28 passed in
  26.13s`，邻接 recovery、Surface/Acceptance、scheduler、fusion 回归 `91 passed in 49.91s`。
  两组有重叠，不能相加；历史大合同文件在 60 秒窗口未完成，仍未记为通过。
- 本次 permission ingress 切片额外运行 `test_dl06_companion_permission_ingress.py`、DL-06
  relationship/proactive 回归及 Secure Ingress service 回归，合计 `47 passed in 27.69s`；这是本地
  定向代码证据，不包含真实 Gateway、Monitor、provider、Edge 或 DL-07 服务栈。
- 本次 `test_dl06_companion_profile_lifecycle.py` 在真实 SQLite migration `0147` 上覆盖：五条独立、
  跨三天、强且守住边界的 canonical aftereffect 才能启动 candidate；第五次合格后续 canary interaction 会
  自动 activate；冻结 profile 确实影响下一轮 text anchor、TTS rate 和 Avatar motion，rollback 精确恢复
  baseline；journal replay/restart digest 一致且 projection 禁止直接改写。该文件 `3 passed in 11.76s`；
  与 relationship/proactive 邻接集合合计 `11 passed in 17.99s`，两组不得相加。
- 证据 fixture 明确替换了 relation classifier 才产生 `0.91` 的强后效应；生产保守文本 classifier 的
  `0.72` 仍被拒绝，不能把该 fixture 当作自动偏好学习已经上线。receipt 只表示 Host/Gateway transport
  accepted 或 Host expression staged，不证明第三方平台送达、用户阅读或真实 Gateway/Edge 运行；直接偏好
  反馈与 Monitor 审核控件、旧 writer 清零、真实模型和 DL-07 服务栈仍未运行，不能由这些定向测试推导为
  主动陪伴完成。
- `test_dl06_companion_profile_ingress.py` 以真实 SQLite migration `0148`、Secure Ingress
  principal/credential/claim store 与 Host route 覆盖 signed profile control：只有
  `companion:profile:write`、exact `user`/relationship subject、tenant/workspace 绑定和已有五条
  canonical strong aftereffect 时才能创建 CANARY；duplicate 与重启只重开同一 Life receipt，service
  principal、跨 scope body、弱历史、重复 key 和 Soul patch 全部失败关闭。该文件 `4 passed in 13.87s`。
  随后 profile lifecycle、permission、proactive、relationship 的受影响集合 `20 passed in 46.58s`，
  Secure Ingress 邻接回归 `7 passed in 4.40s`；集合分开报告，未运行真实 Gateway、模型、Monitor、Edge
  或 DL-07 服务栈。
- `test_dl06_companion_feedback_ingress.py` 以真实 SQLite、Host route 和 Secure Ingress 验证：一条
  signed `HELPFUL` 必须同时绑定 `companion:feedback:write`、exact user/subject、tenant/workspace 与
  仍 active 的 response receipt，才可写入 `0.95` canonical aftereffect；它推进既有 profile CANARY，
  第五条合格 canary interaction 会由 canonical lifecycle 自动 activation。HTTP duplicate、进程重启和
  claim-result 恢复不会重复写 aftereffect、canary interaction 或 activation；service principal、跨 scope
  body、失活 receipt 和额外 `rating` 字段全部失败关闭。当前 feedback/profile lifecycle 的直接集合为
  `5 passed in 22.54s`；与 profile/permission/proactive/relationship/Secure Ingress 邻接集合为
  `29 passed in 60.87s`。这仍不是真实用户盲评、Monitor UI、Gateway、provider、Edge 或 DL-07 证据。
- `test_dl06_companion_profile_ingress.py` 现额外覆盖 signed exact rollback：真实 SQLite 的 active
  CANARY→第五条后效应自动 activation 之后，只有 `companion:profile:rollback`、精确 user/subject 与
  tenant/workspace 绑定的 current active identity 才能经过 Host route 恢复 predecessor。HTTP duplicate、
  进程重建与同一 audit ref 只重开一个 `companion_profile_rolled_back` event；stale identity、service
  principal、cross-scope body 与 caller-supplied target 字段都失败关闭。此为本地合同/路由证据，仍不代表
  Monitor 操作界面、长期用户体验、旧 writer cutover、真实 Gateway/Edge/provider 或 DL-07。本次完整
  受影响集合为 `35 passed in 79.78s`；其中没有 Docker、远端 Edge 或外部模型调用。
- `test_dl06_canonical_relationship_prompt_projection.py` 新增普通 Chat 关系 projection 回归：一个内部
  `RelationshipStateV2` 必须同时匹配 `natsume_ai`、workspace、relationship subject、session 与三层
  schema；即使 legacy Bond 声称 `SOULMATE`，Prompt Context、Interaction Facts 与 Surface packet 仍只输出
  `FRIEND / stepsibling / familiar`，不会从 trust、attunement、consent 或旧分数升级恋人、排他、依赖或主动许可。
  repair/rupture 保持为显式修复状态；subject/workspace/session drift 或损坏 canonical hand-off 必须以
  `CANONICAL_RELATIONSHIP_REJECTED` fail-closed，绝不回退旧 Bond。最终普通 Chat、canonical fusion、
  AffectiveTurn、表达协议、Surface/adapter 与受影响历史回归集合为 `179 passed in 63.85s`，8 条既有
  Python deprecation warnings；未运行 Docker、真实 provider、远端 Edge、Monitor 或 DL-07。
- `test_surface_affect_control.py` 补充 final presentation Gate：canonical relationship authority 到达 Surface 后，
  即使兼容输入携带 `SOULMATE`、`relationship_distance=lover`、`closeness=0.99`、恋人 clue 或 lover task，也只能
  输出 `familiar`；rejected canonical 则保持 `guarded`。与普通 Chat projection、Surface packet/runtime 回归合计
  `82 passed in 21.18s`；未启动 Docker、真实 provider、远端 Edge 或物理 Voice/Avatar。
- `test_dl06_retrieval_canonical_relationship_cutover.py` 覆盖 Cortex 检索预取：valid internal Life context 只能
  生成无 score 的 `FRIEND / stepsibling / familiar` 摘要；workspace drift 生成 guarded rejected 摘要；两者均不调用
  legacy state-arbiter Bond fetch，Motive 也只能收到该 bounded projection。Cortex workspace 传递、governed route、
  Surface 关系 Gate 一并为 `34 passed in 5.64s`。

## NE-07 隔离外部服务栈

- 默认端口上已有用户长期 `natsume-ext` Neo4j、Chroma、Redis；本轮明确不
  停止、不写入、不计入验收。
- acceptance project `natsume-ne07-555ead6d` 使用独立 container names、
  network、27474/27687/28000/26379 端口和 `runtime/acceptance/...` 数据根。
- 三个默认镜像已固定 digest，Neo4j 密码只允许运行期注入并在启动结果中脱敏；
  三个服务均声明容器 healthcheck。
- 真实 Docker `compose config --quiet`、PowerShell parser 与静态合同
  `9 passed in 0.88s` 通过。
- 外部服务 persistence runner 已进入代码，强制校验隔离 identity、端口和
  Compose project 标签；其真实执行结果必须另存 ignored acceptance receipt，
  不能由 runner 存在性替代。
- 提交 `14f015bd` 的跑次 `ne07-ext-20260728-01` 已真实完成 Neo4j、
  Chroma、Redis 写入、容器重启、持久读取、更新/查询和清理，耗时
  38,984 ms；receipt digest 为 `sha256:7af6e6a0…2f71`。独立复核的三类
  探针残留均为 0，明文 secret 未进入 receipt/日志，用户默认栈未重启。
- Host、Obs、Nginx Monitor、Edge、Gateway 和后续故障注入仍未运行，不能
  把三库子 Gate 外推为完整全栈通过。

## NE-07 Host / Obs / Monitor 启动前验证

- Monitor UI：`npm ci`、Operator client check、lint、typecheck、15 项
  read-model test 和 production build 全部通过。
- Host、Obs、Monitor 六个启停脚本支持显式 acceptance runtime root；该模式
  不扫描其它 Host，运行环境快照排除 secret 值并保留安全 `sec://` 引用。
- PowerShell parser 通过；定向合同为 `13 passed in 3.33s`。
- 官方 Nginx 1.31.3 已安装并记录 executable SHA-256。
- `npm audit` 的 6 high 构建链和 2 moderate React Router 告警保持开放；
  Host 首次真实启动因 Workspace HMAC 缺少安全引用入口而退出，且暴露旧脚本
  会把短暂健康误报为 `started`。该跑次为 `FAIL`，不是 preflight PASS。
- Workspace 现支持只落盘 `sec://` 引用并在 Host 进程内解析；启动器在
  sidecar 启动后连续五次核对 Host PID、监听与 health/readiness payload，
  同时要求真实 Obs、Monitor 端点继续可用，失败会定向清理 acceptance 根。
- `b6b341be` 的第二次真实启动确认 Workspace 引用已生效且稳定性 Gate 正确
  拒绝假成功；进一步发现 Service Catalog 中间 rollout candidate 被旧首探针
  误认成最终 Host。首探针现必须验证最终 stage、`host.main` owner 及
  operation/snapshot digest，禁止用 readiness、metrics 或监听端口兜底。
- 第三次真实启动已拒绝中间 candidate，但因 acceptance Workspace managed
  root 位于源码 worktree 内部而被既有隔离门禁拒绝。三个 AUDIT operation
  均留下 `ROLLED_BACK` receipt，head 为空、lease 已释放，没有删库恢复。
- 启动器新增 `-AgentWorkspaceRoot`：Docker/Host 启动前拒绝 managed root 与
  source 的同目录、父子目录重叠，合法外部根才进入 runtime snapshot。更新后
  定向单元为 `23 passed in 3.91s`，Workspace ASGI 前序定向集成为
  `5 passed in 5.45s`。
- `b56d4d78` 的第四次启动证明外部 Workspace 根已生效，但
  `HOST_LLM_PROVIDER=disabled` 令 Governor/LLM required health 为
  `FAILED`，AUDIT receipt 因 `SERVICE_CATALOG_POST_HEALTH_FAILED` 回滚。
- health 现只允许“选择真实 provider + 显式允许 cold start + required
  failures 全部为缺凭据”映射为 `DEGRADED`；disabled provider、混合
  required failure 和 durable Guardian failure 仍失败关闭。旧 health contract
  同步到 app-local provider fixture，最终受影响集为
  `41 passed in 25.17s`。
- `f48afe1b` 的第五次真实 Host 进程启动已通过五阶段 durable rollout；
  Host/Nginx Monitor、真实代理路由和三个 Docker 容器均独立核对成功。
  Host 精确保留一个缺外部模型凭据的 cold-start failure，不能记模型 PASS。
  Obs HTTP 虽为 200，但 health 暴露 `life-evolution` projection daemon
  连续 cursor conflict，因此不能记 Obs PASS。
- 空 Life snapshot 现以 source time 0 生成确定性 payload，不再用 wall clock
  改写同 cursor digest；domain/canonical/live-source 回归为
  `29 passed in 23.25s`，仍须真实 daemon 重启恢复验证。
- 直接复用旧安全 snapshot 的预检暴露 Neo4j 用户名/密码被过滤成半套
  credential；writer 现同时排除 direct username/password，只保留 `sec://`
  ref。启动脚本合同为 `18 passed in 3.82s`，真实 snapshot restart 与无拦截
  认证 Playwright 尚待下一跑次。
- 第六次 `ForceRestart` 不再受半套 Neo4j credential 阻断，但旧 Host 被
  强杀后留下 30 秒 embodiment authority Lease；replacement 单次 acquire
  失败并退出，ENFORCE operation durable `ACTIVATION_FAILED`，旧 Lease
  随后自然过期。启动器未观察 launcher 退出而空等约 240 秒，旧 Obs/Nginx
  也未在前置阶段清理；本跑次结论为 `FAIL`。
- takeover builder 现在只对精确 authority-unavailable 做默认 35 秒有界
  重试，必须在 predecessor 到期后取得更高 fence，live heartbeat 不可抢占；
  `ForceRestart` 先定向停止同 runtime root 的 Monitor/Obs，首次 catalog
  等待监视 launcher PID。authority、配置、隔离和启动脚本集合为
  `33 passed in 34.32s`，真实 crash restart 与 Obs daemon 恢复仍待重跑。

## NE-05 Skill 生命周期本地代码波次

- migration `0141_skill_lifecycle_authority.sql` 通过 fresh 0001→0141
  真实 SQLite smoke；V2 authority 覆盖 canary operation/outbox、
  activation evidence、maturity decision、idempotency operation、active
  projection 和 terminal receipt。
- `test_ne05_skill_lifecycle_v2.py` 真实重开 0138 Lab
  report/run/sandbox、proposal、ReviewItem 和 Supply Chain 证据，覆盖原子
  activate、exact replay、degrade、使用目标自身 evidence 的 rollback、
  forged Review 零写、过期 canary reconciliation 以及 Host active-version
  resolver。
- Compiler、Supply Chain、安全接收、V2 lifecycle、Host composition、
  maturity、Forge proposal-only 和持久化分层的最终受影响集合为
  `181 passed, 1 skipped in 51.82s`。唯一 skip 是 Windows symlink 创建权限，
  不计为通过。
- 架构 Gate 3 首跑拒绝了 1,834 行未分类 V2 repository；没有增加白名单。
  证据/投影校验被拆到不持有连接或事务的 475 行 mixin，SQLite transaction
  owner 降为 1,492 行。拆分后的重叠定向回归为
  `12 passed in 13.79s`，fresh Gate 1～14 最终
  `PASS / failed_gates=[]`。
- 全仓 collect-only 收集到 11,419 项；唯一 error 是 `origin/dev` 已有的
  `test_long_memory_quality_regressions.py` 导入缺失。该基线错误未被计入
  NE-05 通过，也未在本波扩范围修改。
- 外部 bundle/provider canary、真实签名基础设施和 NE-07 全栈没有运行；
  `REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。当前结论为
  `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07`。
- 0141 使既有七路线 0140 coverage receipt 按设计失效；干净实现提交落定后，
  已以 SQLite backup 复制 accepted snapshot、只追加 0141，并在
  `6f78e25c24a2bc148aa8a0a12016558a6e492a7a` 上 query-only 重开，没有重跑
  accepted worker。七路线全部 `PASS`，receipt digest 为
  `sha256:f42f5fef85fd754daf8e523de6479f09ec94373118f9401caa9a53a46c980986`。

## NE-01 代码波次封口

- 当前权威收据在 `08a172da662df11d34fb42ea6e25a651f84da141`
  上，以 SQLite backup 复制既有 accepted 七路线快照，从 0141 仅追加
  migration 0142 后 query-only 重开。七路线仍全部 `PASS`，receipt digest 为
  `sha256:161b860fd67d47945c44ff403304742c5619bc6a590cd76dd12b119e1e29f15f`，
  accepted worker 没有重跑。
- 恢复波次去重后为 24 个用例，结果 `24 passed in 165.99s`。范围包括
  `test_ne01_general_multinode_recovery.py`、四类跨进程签名恢复、fan-out、
  parallel terminal、cancellation continuation 和真实 Git Workspace。
- 当前收据证据保存在忽略目录
  `artifacts/local-gates/08a172da662df11d34fb42ea6e25a651f84da141/ne06-coverage/`；
  恢复波次仍保留在其原 commit-bound 目录，不提交 SQLite、日志或用户数据。
  状态为
  `CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_NE07`；远程 CI 为
  `NOT_RUN_ACCOUNT_QUOTA`，真实全栈尚未运行。

## NE-01 并发终态跨进程恢复矩阵

- `test_ne01_parallel_terminal_process_restart.py` 使用 Windows `spawn` 与
  真实文件型 SQLite，在 retryable+hard failure、Budget overrun、以及
  post-Acceptance Review 三种场景中让进程 A 于 0090 outer settlement
  提交前硬退出。
- 进程 B 通过真实 Host startup continuation 重开，resolver/worker 调用均为
  0，每条只观察到一个 `done`；Review 在拒绝后由进程 C 提交 0135 denial，
  终态进程再启动 inventory=0。
- 首轮 `2 passed, 1 failed` 暴露 Budget 已释放但 `BLOCKED` 图仍被重复发现；
  修复 recovery inventory 的 actionable-node 过滤后，新增矩阵、SQLite
  startup integration 与 continuation unit 的最终集合为
  `14 passed in 46.30s`。
- 旧并发 failure/Budget/Review/retry 结算集合随后为
  `12 passed in 49.41s`。首次宽回归发现一条 7 月 16 日旧夹具没有注入
  现行强制的 durable ReviewRuntime，使节点停在 `VERIFIED`；补齐真实
  ReviewStore/Runtime 后通过。这是测试权威修复，不放宽生产 Review 门禁。
- 该集合证明本地进程恢复与清单分类，不证明生产 fan-out promotion、七路线
  canary、真实 provider 或 NE-07。

## NE-01 取消终态与混合 fan-out 跨进程恢复

- `test_ne01_signed_cancellation_process_restart.py` 分别覆盖 cancellation
  `COMMIT + SIDE_EFFECT_CONFIRMED` 和
  `RELEASE + NO_SIDE_EFFECT`。每条均由进程 A 建立 REQUESTED intent/current
  hold，进程 B 在目标 UoW 提交后、handler 返回前硬退出，进程 C exact
  replay 并运行真实 cancellation continuation，进程 D 证明零 pending。
- `test_ne01_fanout_reserved_process_restart.py` 在真实文件型 SQLite 中并行
  启动两个根节点：`left` 已 `ACCEPTED` 后，`right` 保持
  `RESERVED/RUNNING` 并令进程硬退出。新 Host 在 live Lease 下只 defer；
  Lease 过期后写 current hold；签名 `REACQUIRE` 后只调用 `right` 与
  `join`，`left` 的 Artifact/Attempt 不变，终态重启 worker=0。
- 回归同时证明 checkpoint continuation 的节点 `done` 不再直接泄漏给
  Host；它先进入现有 parallel settlement，最终仅公开一个 Graph `done`。
  新增两文件共 `3 passed in 32.25s`；不含新增 case 的相邻 cancellation、
  signed unknown-effect、Host restart、general multi-node 与 signed failure
  集合为 `16 passed in 83.15s`。
- 这些测试使用临时 Ed25519 key 和受控 projected worker。部署 key lifecycle、
  生产 fan-out/cancellation 开关、真实 provider、波次集成与 NE-07 均未运行。

## NE-01 签名终态处理进程崩溃恢复

- `test_ne01_signed_terminal_process_restart.py` 使用 Windows `spawn`、真实
  文件型 SQLite 和独立 Ed25519 签名进程，覆盖 checkpoint
  `COMMIT + SIDE_EFFECT_CONFIRMED` 与 raw failure
  `RELEASE + NO_SIDE_EFFECT`。
- 两条链都在目标 mutation 已提交、Accepted handler 尚未返回时硬退出；
  新进程只能重开同一 evidence、Operation proof 和 handler receipt。
  checkpoint 链继续 Verification/Acceptance，failure 链重开 terminal；
  两者 worker 调用均为零，随后 Host 终态重启也为零 replay。
- 既有 checkpoint/failure 集成测试现均重开唯一 current 0131 recovery
  case，并把 exact case ref 与 subject digest 同时绑定进 evidence 和
  Acceptance Effect payload。新增跨进程 `2 passed in 18.33s`，既有签名
  回归 `5 passed in 18.06s`；取消绑定组合、真实 provider、部署 key/HTTP
  和波次集成仍未验收。

## NE-01 Scheduler 时钟批次与控制政策验收切片

- `test_ne01_scheduler_clock_batch.py` 验证 capture time、limit、顺序、深层
  JSON 行和 digest 的 exact reopen；alias mutation、重复 identity、越界与
  payload tamper 均失败关闭。
- `test_aw02_scheduler_clock.py` 证明一个 pending worker 修改原 due mapping
  后，同一 sweep 仍使用执行前冻结的 definition；Clock 不再执行第二次
  `list_due()`。
- `test_aw02_scheduler_repository.py` 使用真实 migration SQLite，证明 pending
  与 due 在同一事务读取、重开 repository 得到相同 batch ref，嵌套字段篡改
  被拒绝。
- Universal acceptance reader 严格重开 admission 的 Command、WorkOrder、
  TaskGraph 和三个 digest。控制路线只有在完整 policy 重新验证后才投影
  privacy-safe evidence；非控制路线夹带 policy 也会失败。
- Scheduler/acceptance 核心批次为 `45 passed in 53.57s`；加入结构、
  control-policy、coverage 与文档回归后的最终受影响集合为
  `100 passed in 64.61s`。架构 Gate 1～14 和
  `static_scan,doc_gate` 均为 PASS。随后 clean SHA `2a88f56e` 的 GR05
  为 `1 passed in 26.28s`，三控制路线 CLI 为 PASS；同库正式收据七路
  reason code 为空，Internal/Operator/Scheduler 的 control policy count
  各为 1。该收据不得与历史 37/19/69 项批次相加，也不证明一般恢复、波次
  integration 或 NE-07。

## NE-01 Operator 旧命令物理退役切片

- `test_obs_domain_command_ports.py` 和
  `test_ne03_action_preview_front_doors.py` 要求 active manifest/registry
  恰好只有 14 条 governed command，blocked set 为 0，23 个 retired ID
  与 active 集合不相交且没有 domain port。
- `test_aw07_operator_auth_transport.py` 要求认证 BFF 对退役 ID 返回
  `410 OPERATOR_COMMAND_RETIRED + replacement_authority + replacement_wave`，
  并证明 Secure Ingress 调用数为 0。
- 生成式 Operator OpenAPI/client 只包含 active 14 条；Monitor endpoint
  registry 的 retired entry 不得拥有 `commandId`，`endpointPost` 必须在
  fetch 前失败。对应 9 个 Node 测试、client freshness、lint、typecheck
  均通过。
- Python 最终受影响集合为 `41 passed in 25.87s`，包含 BFF 410 零
  Secure Ingress 证明。该数字不与前一 69 项控制政策回归相加。
- 这项证据只证明旧 Operator 广告面和 dormant handler 已退役，不证明
  replacement authority 已全部实现，也不证明一般恢复、HTTP/Edge canary、
  NE-01 wave integration 或 NE-07；远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Scheduler/Internal/Operator 控制政策与恢复唯一性切片

- `test_gr05_direct_host_route_contracts.py` 覆盖
  `ControlRouteExecutionPolicyV1` content address、WorkOrder/TaskNode/
  Budget binding、缺失/篡改拒绝，以及 Intent Review 与 TaskNode Review
  不能同时声明。
- `test_aw02_internal_agent_runtime_cutover.py` 与
  `test_ne01_governed_operator_scheduler_trigger.py` 覆盖真实 SQLite 的
  initial/replay/Review/recovery 重开，完整 WorkOrder replay drift 失败关闭。
- `test_governed_operator_replay_attribution.py`、
  `test_gr06_acceptance_effect_compiler.py` 和通用 worker evidence 测试要求
  independent verifier/acceptance/Effect compiler 不信任 route 字符串，
  必须重新验证 frozen policy。
- Operator 使用已有 durable Intent admission Review；Scheduler 的人工
  trigger 与 reviewed Internal 保留 TaskNode Review。测试必须证明批准一次
  后不会再进入第二个 `WAITING_APPROVAL`。
- `test_ne01_governed_operation_reconciliation.py` 使用真实 SQLite 证明同一
  `RECOVERY_REQUIRED` Operation version 在 Budget/Lease 推进后仍只返回原
  recovery case；签名请求不能面对两个同时 current 的 authority。
- 最终结构重构后合批按不重叠文件分为：policy/Effect/replay unit
  `29 passed in 1.41s`；Host/Scheduler/Internal `32 passed in 61.30s`；
  Operator `8 passed in 170.27s`。合计 69 项、零失败；三组并行执行，墙钟不以三组
  时长相加。
- 结构收口把 replay normalization 下沉 contracts，并拆出 control policy
  composition 与 Effect audit projection；Scheduler→app 反向 import 清零，
  两个 owner 为 1480/1499 行。完整架构静态 Gate 1～14
  `PASS / failed_gates=[]`，没有修改 mixed/cohesive allowlist。
- 本节只证明当前启用 control transport 的执行政策和 recovery-case
  唯一性。Operator 旧命令已由上一节物理退役；一般恢复、NE-01 波次 integration 与
  NE-07 实栈仍未完成；远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Plan/Delegate 执行政策与审批恢复当前切片

- `test_ne02_capability_plan_search.py` 覆盖 Plan policy 冻结/重开、Graph
  Budget digest、Registry risk/Review/execution policy、policy tamper、缺失
  Skill 和非 result node Effect 拒绝。
- `test_gr04_planning_execution.py` 约束 policy binder 只能扩展 WorkOrder
  metadata，不能改写 TaskGraph、domain、预算、输入或 planning lineage。
- `test_gr05_direct_host_route_contracts.py`、`test_aw02_governed_host_runtime.py`
  与 `test_ne01_governed_delegate_dispatch.py` 覆盖 Delegate content address、
  pre-execution Review、exact-zero budget mode、未登记 target、policy drift 和
  effect/outbox/delivery claim 拒绝。
- `test_gr05_frozen_route_executors.py` 在 COMMIT 前重验 admitted policy 与
  dispatcher audit 的 adapter/mode/ref 一致性，不能只相信 URI 前缀。
- Operator/Scheduler 的 `route=delegate` 是已认证控制传输，不是 child-agent
  Delegate：target allowlist 仍须精确命中，但它不携带 pentest target policy，
  使用有限非零预算。`test_aw02_governed_host_runtime.py` 与真实
  `test_ne01_governed_operator_sys_mode.py` 共同覆盖该边界，回归集合为
  `21 passed in 41.62s`；Scheduler 的同边界另由下述定向集合覆盖。
- 当前 Plan/Delegate 定向单元/合同集合为 `89 passed`；它与上述
  Operator 集合存在重叠，数字分别报告、不相加。
- `test_gr05_universal_host_runtime.py` 使用真实文件型 SQLite，Delegate 首次
  返回 `WAITING_APPROVAL` 且零 Attempt；Review approve/resume 后由
  `ReviewContinuationWorker` claim exact checkpoint，最终 `ACCEPTED` 且
  continuation 消失。Chat/Skill/Delegate/Plan 集成为
  `1 passed in 26.40s`。
- 干净实现 SHA `2a88f56e` 已重跑同库七路线 v3 收据：
  `ne01_scheduler_clock_control_route_acceptance_20260727.json` 的七路均为 `PASS`，
  Plan 三节点与 Delegate Review continuation 均有提交绑定证据。当前启用
  Plan/Delegate 范围为 `COMPLETE`；真实 provider、Docker 与完整服务栈未运行，
  远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Skill 预算与副作用 policy 当前切片

- `test_ne01_skill_execution_policy.py` 要求 canonical Host Registry 精确包含 93 个
  Skill，并固定为 28 个 inline read-only、13 个 proposal-only、3 个
  post-Acceptance Workspace 和 49 个 blocked；运行 manifest 必须恰为
  44 项。
- 独立 Personality Registry 必须为 19 项且 governed manifest=0（5 blocked、
  14 unclassified）；动态 MCP Skill 固定 blocked，不能借默认 metadata
  获得执行权。
- built-in descriptor 缺失或 `skill_id/tool/action` 漂移失败关闭；自定义
  declarative Skill 必须同时显式声明 execution 与 budget policy。
- blocked 条目不得进入 manifest、PlanningSearch、Scheduler 自动调度或
  Host admission；旧 durable graph 到达 frozen executor 时仍须拒绝。
- user-visible Domain lifecycle、持久 browser profile/session、文件/系统
  mutation、Memory/Scheduler write 都不能伪装成 inline read-only。
- 启用条目统一 Task precharge；普通 Skill 无裸模型端口，Agent model 使用
  measured guard；proposal-only 只产出候选/stage，三条 risk-4 Agent 写入
  使用既有 post-Acceptance Workspace promotion。
- 最终互不重叠的本地批次为：Skill/Registry/Scheduler 相关 unit
  `107 passed in 3.70s`，Planning policy unit `15 passed in 0.71s`，
  Host admission/drain unit `18 passed in 0.56s`，release/coverage 合同
  `49 passed in 1.75s`；三条真实 SQLite/Git 集成为
  `3 passed in 35.92s`。批次分别报告，不与历史数字相加。
- 首次过宽命令误包含外部 E2E/VTS smoke，主动终止；随后 unit 合批因测试
  `echo_skill` 未显式分类而在 admission 前拒绝，测试仍等待 started 导致
  184 秒超时。为测试条目补显式只读 policy 后，该 Host 文件 18 项在
  0.56 秒完成；超时不计 PASS，也没有掩盖失败。
- JSON 解析、changed-module compileall 与 `git diff --check` 通过；文档受众、
  文档 authority、20 条 freshness rule 均零失败，统一
  `static_scan,doc_gate` 为 `PASS / failures=[]`（110.3 秒）。
- 正式七路收据仍
  绑定旧 SHA，因此当前只记
  干净提交 `c68850f859048be955c15366e33ada3a917d9b12` 随后以同一
  真实 SQLite 重跑七路线，正式收据为
  [ne01_skill_policy_route_acceptance_20260727.json](test_reports/ne01_skill_policy_route_acceptance_20260727.json)，
  receipt digest=`sha256:eee1b416d6de71a92df07d57a5c8cf84f4788ed3d9d74b4365fa99d571e6f13e`。
  该收据把 Skill 当前启用范围的 Budget 与 transactional Effect 控制提升为
  `PASS`，路线状态为 `COMPLETE`；49 个 blocked 能力不计完成。远程
  CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Skill 唯一执行 authority 当前切片

- 生产 AST inventory 要求 `SkillMeta.handler`、
  `bind_validated_skill_authority`、`dispatch_executor_payload` 各只有
  `host_brain/app/governed_skill_execution.py` 一个调用者；旧 Executive
  executor 文件必须不存在。
- `SkillLoader.execute()` 在读取 registry、验证 token、调用 handler/Broker
  或产生外部 I/O 前返回
  `GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED`；Scheduler 的 Codex/Personality
  metadata 入口同样失败关闭。
- 缺 node authority 在 registry 前拒绝；canonical Skill receipt 落库失败
  必须传播，不能伪造成功。
- 干净提交 `3d68bd1eca69ad1593454634c2999d07b4d4f2f5` 的最终受影响
  代码与合同集合得到 `209 passed in 22.60s`；真实
  `test_gr05_universal_host_runtime.py` 为 `1 passed in 27.03s`，
  证明 Chat/Skill/Delegate/Plan 主链未因 authority 迁移退化。扩大 D7B
  集合的一个 `RECOVERY_REQUIRED`/`FAILED` 断言在干净
  `origin/dev@a984edb71` 同样失败，不计本切片回归或 PASS；旧 HTTP smoke
  受当前 admission Review 和未启动外部 Chroma 阻断，也不计生产证据。
- static/doc Gate 和中文交付元数据 Gate 均在同一 SHA 上零失败通过，本地
  证据位于忽略目录 `artifacts/local-gates/3d68bd1eca69ad1593454634c2999d07b4d4f2f5/`。
  因此 `canonical_execution_authority=PASS`。Skill 的预算全覆盖、领域
  副作用 post-Acceptance 化、波次集成和 NE-07 仍未完成；
  `REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Chat 真实模型预算持久链当前切片

- GR05 不再 monkeypatch Surface 调用或构造假的 `surface_model_attempt`；Direct Chat 与 Plan final Chat 经过真实 prompt registry、route policy、`ModelAttemptService`、ledger audit 和 Surface runtime，只在 terminal provider adapter 返回受控的 18 tokens / 27 micros 测量。
- 两次新请求各产生 `started → succeeded` canonical ModelAttempt；WorkerProjection 的 route/provider/model/attempt/measurement 必须与父级 `task_runtime_budget_usage`、child `task_runtime_node_budget_usage` 精确相等。Direct Chat 与 Plan replay 不产生第三次 provider 调用。
- 七路线 reader 新增隐私有界的 `model_attempts` 与 `worker_model_budgets`；validator 会拒绝缓存结果、生命周期缺失、孤儿/重放 Attempt、provider identity 漂移、测量漂移或父子账本漂移。
- 真实执行发现并修复内部预算 guard 泄漏进 Life acceptance payload，以及 Task Runtime 覆盖 Surface audit path 两个生产缺陷；相应回归覆盖 payload 白名单和 COMMIT/Projection path 一致性。
- 当前受影响代码集合为 `44 passed in 31.44s`；干净 SHA GR05 为 `1 passed in 26.37s`；release/coverage/doc/metadata 集合为 `94 passed in 14.31s`，三者存在覆盖且分别报告、不相加。正式同库收据已验证 Chat/Plan 模型 lineage，完整架构静态 Gate 1～14 为 `PASS / failed_gates=[]`，统一 `static_scan,doc_gate` 为 `PASS / failures=[]`。Chat 为 `BUDGET_PASS / ROUTE_COMPLETE`；真实外部 provider、波次 integration 与 NE-07 尚未运行，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 七路线提交绑定验收账本 v3

- `UniversalRouteAcceptanceReader` 只读打开真实文件型 SQLite，在一个事务中读取 Command、Graph、Node、Attempt、Lease、Operation、父子 Budget、Verification、Acceptance、Artifact、WorkerProjection、Effect、Outbox、recovery、reconciliation 与 cancellation 投影；内存数据库、缺表/缺列和迁移不一致均失败关闭。
- `UniversalRouteAcceptanceBuilder` 要求干净 Git HEAD、仓库/数据库 migration head 一致、每个 request 只证明一条路线，并输出经过字段白名单和总 receipt digest 保护的隐私边界收据。
- GR05 已加入 Direct Skill，真实 Host Runtime 的单项集成 `1 passed`，共同证明 Chat、Skill、Delegate、Plan。`test_ne01_universal_remaining_route_acceptance.py` 另以同一真实 SQLite 运行 Scheduler、Internal 和带 Review/outer outcome 的 Operator，单项集成 `1 passed`。
- 最新串联运行绑定干净提交 `2a88f56e2dc7c68d7f37100c6c2d408bc2fdf6a0`；GR05 单项为 `1 passed in 26.28s`，剩余路线 CLI 为 `PASS`。v3 validator 对同一文件型 SQLite 严格重开 admission digest 与控制 policy，得到七路全部 `PASS`、reason code 全空，migration head 为 0135。当前正式收据为 [ne01_scheduler_clock_control_route_acceptance_20260727.json](test_reports/ne01_scheduler_clock_control_route_acceptance_20260727.json)，receipt digest=`sha256:505c300bc88882258bc47d13c75a542fb4a9526fc4e16cf2b7e9875d8d9cdd1a`。
- 机器矩阵中七路 `commit_bound_acceptance` 已全部为 `PASS`；Chat、当前启用 Skill、`CAPABILITY_SEARCH_V1` Plan 和只读 `pentest_agent` Delegate 达到 `COMPLETE`。Scheduler/Internal/Operator 的其它 `PARTIAL` 控制仍使 NE-01 保持 `IN_PROGRESS`。受控 terminal provider 不具备真实 provider 质量、PlanningSearch 收益或 NE-07 资格，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Review 拒绝原子终态当前切片

- `0135_task_review_denial_settlement.sql` 新增 append-only settlement，绑定 exact terminal Review 状态/版本/digest、Graph revision、Node、graph Budget、三条 Outbox，以及可选 Acceptance/Attempt 和已有多节点 `BLOCKED` decision；fresh 0001→0135 与真实前缀 0134→0135 必须通过。
- `tests/integration/test_ne01_review_denial_terminal_uow.py` 使用真实 SQLite 验证 POST Review 的 Node/Graph/Budget/三 Outbox/settlement 同成同败、响应丢失 exact replay、Review/settlement immutable 和 identity drift 拒绝。
- `test_gr03a_governed_durable_dag.py` 覆盖 PRE Review 零 Attempt 取消、POST 并行 Graph 拒绝、多节点 blocking decision lineage，以及 continuation 在终态提交后重启只返回同一 `REVIEW_DENIED` receipt、Worker 调用保持 0。
- `tests/unit/test_task_review_denial_store_structure.py` 约束 canonical Store mixin、focused 文件尺寸、单事务 writer 和 Scheduler 不再组合三个公共写接口；`test_aw03_migration_compat.py` 覆盖真实 0134→0135。
- 最终受影响集合为 `58 passed in 39.48s`，覆盖 Store/Runtime/Review continuation、0134→0135 真实升级、发布合同和真实 SQLite DAG；完整架构 Gate 1～14 与统一 `static_scan,doc_gate` 均通过，`failed_gates/failures=[]`。本切片只关闭 Review denial terminal bundle；一般 cancellation/recovery、七路线 commit-bound acceptance、NE-01 wave integration 和 NE-07 Docker 仍未完成，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 持久 Acceptance 与 Review 原子边界当前切片

- `0134_task_node_acceptance_decision.sql` 新增 append-only decision，绑定 Graph revision、Node、Attempt、AcceptanceJudgement digest、evidence、Node Outbox，以及可选 Review/Graph block lineage；fresh 0001→0134 与真实前缀 0133→0134 必须通过。
- `tests/integration/test_ne01_durable_acceptance_review.py` 使用真实 SQLite 验证 ACCEPT 的 decision/artifact/Effect/Node 原子提交，以及 REVIEW 的 decision/Node/Review/直接 Graph block 原子提交；覆盖 restart exact reopen、decision tamper 和 decision INSERT 后故障整体回滚。
- `tests/integration/test_ne01_durable_node_artifacts.py` 与 `test_gr03a_governed_durable_dag.py` 继续验证 accepted response-loss replay、scheduler snapshot 的 `node_acceptances`、Review continuation 与 worker exactly-once。
- `tests/unit/test_task_node_acceptance_store_structure.py` 约束 focused Store、唯一 shared pending Review INSERT writer和 canonical composition；`test_aw03_migration_compat.py` 覆盖真实 0133→0134。
- 当前受影响范围按六个互不重叠批次执行：结构 25、Coordinator/Review 34、migration/release 20、Acceptance/Artifact 20、并发 Review/restart 6、Verification terminal 2，合计 `107 passed`；各批 pytest 时间合计 `87.59s`，不是伪装成一次单命令运行。文档/发布合同另为 `45 passed in 10.42s`，与上述 release 子集重叠，不相加。changed-module `compileall`、三份 JSON、`git diff --check`、完整架构 Gate 1～14，以及统一 `static_scan,doc_gate` 均通过，后两者的 `failed_gates/failures` 均为空。Review denial terminal bundle 已由后续 0135 切片关闭；一般 cancellation/recovery、七路线 commit-bound acceptance、NE-01 wave integration 或 NE-07 Docker仍未完成，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 持久 Verification 与直接失败终态前序切片

- `0133_task_node_verification_decision.sql` 新增 append-only decision，绑定 Graph revision、Node、Attempt、VerificationResult digest、evidence 和 Node Outbox；fresh 0001→0133 与真实前缀 0132→0133 均必须通过。
- `tests/integration/test_ne01_durable_verification_terminal.py` 使用真实 SQLite 验证 PASS decision 跨进程重开、scheduler snapshot、exact replay、不可变 trigger，以及 FAIL 时 Node/Graph/graph Budget 原子收口；伪造 Graph Outbox 后三者保持原状态且 decision 不存在。
- `tests/unit/test_task_node_verification_store_structure.py` 约束三个 focused persistence owner 的尺寸、canonical Store composition 和每条写路径恰好一个事务；Runtime 结构测试要求 verifier 与 checkpoint continuation 只调用新 authority。
- Store、Runtime、composition、结构、真实升级、发布与文档合同的唯一去重批次为 `156 passed in 91.22s`。真实 SQLite DAG/Review/直接验证拒绝/重启五项为 `5 passed in 17.09s`，checkpoint-budget 九项为 `9 passed in 0.42s`，两组独立报告且不与 156 相加。changed-module `compileall`、JSON、`git diff --check`、统一 `static_scan,doc_gate` 和完整架构 Gate 1～14 均通过；Gate 3 通过抽离无 I/O schema inventory 将 `task_repository.py` 从 4642 降至 4553 行，未扩大白名单。
- 本切片只关闭验证事实丢失和直接失败半提交；多节点仍复用 0090 settlement，通用低层 transition 仅保留兼容。一般 cancellation/recovery、七路线 commit-bound acceptance、NE-01 wave integration 和 NE-07 Docker 不在本切片执行，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Attempt-bound 节点预算当前切片

- `0132_task_node_budget_reservation.sql` 新增 immutable child reservation 与 append-only child usage ledger；READY claim 在一个 `BEGIN IMMEDIATE` 中共同写 Attempt、Lease、Heartbeat、Outbox 和 child reservation。Graph Budget 仍是唯一 aggregate ceiling。
- `tests/integration/test_ne01_node_budget_reservation.py` 覆盖双根 distinct child、父子 charge、跨实例重开、exact response-loss replay、identity tamper，以及从 0131 升级后按历史 usage 回填且不恢复已消费容量。
- `tests/integration/test_ne01_budget_contention_store.py` 继续证明 8/16/32-thread 与跨进程共享父级只产生一个 EXCEEDED winner；child binding 不改变 aggregate contention authority。两文件去重后为 `12 passed in 44.78s`。
- 节点预算、Coordinator、checkpoint hold、parallel winner、执行/READY claim 结构的扩展单元集合为 `50 passed in 2.84s`，覆盖并取代较小的 14 项运行；retry、due promotion、Review boundary 与 exact reopen 的独立集合为 `8 passed in 26.80s`。各组可能覆盖相邻代码，分别报告，不相加。
- fresh migration 已从 0001 连续执行到 0132，changed-module `compileall` 通过；release/coverage/文档合同为 `68 passed in 14.20s`，统一 `static_scan,doc_gate` 的 `failures=[]`。当前环境未安装 Ruff，记为 `NOT_RUN_TOOL_UNAVAILABLE`。该切片没有覆盖全部 cancellation/release、七路线 Budget policy、波次 integration、真实 provider 或 NE-07 Docker，因此状态仅为 `ATTEMPT_BOUND_NODE_BUDGET_LOCAL_PROVEN / NE01_IN_PROGRESS`，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Plan 节点审批与续跑当前切片

- `tests/unit/test_ne02_capability_plan_search.py` 证明 confirm-required registry capability 可参与竞争，compiler 生成覆盖实际图节点的政策；缺项与字符串布尔失败关闭。
- `tests/integration/test_ne01_plan_node_review_runtime.py` 使用真实 SQLite、PlanningSearch、Graph compiler、HostCommand admission、Task Runtime、ReviewStore、0096 Continuation Worker、NodeBoundGrantService 和 durable WorkerProjection，证明三节点图只有目标 Skill 等待审批，消费后续跑且三个 worker 各执行一次。
- 最终回归集合为 `89 passed in 42.96s`；真实 SQLite/生产入口集成为 `10 passed in 59.58s`，两组并行执行且不相加。后者同时覆盖既有 PRE/POST Review、自动 continuation、accepted predecessor 重启、retry Review 及 Chat/Delegate/Plan 共用 Host 主链。
- 首次 Core Gate 3 因 `dag_scheduling_runtime.py` 超过 1500 行失败；历史 grant 恢复已拆到 `parallel_success_grant_reentry.py`，未增加/扩大白名单，最终 Gate 1～14 `PASS`。发布/文档合同另为 `77 passed in 11.04s`；统一 `static_scan,doc_gate` 无失败。
- 本切片无 migration、无真实 provider、无 Docker；Plan 当前 enabled route 的 `node_bound_grant/review_resume=PASS`，Budget、一般搜索/恢复、commit-bound acceptance、NE-01 与 full architecture 均未完成。远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 只读 Delegate 预算证明当前切片

- `host_brain/contracts/agent_runtime/delegate_usage_attestation.py` 冻结只读 Delegate 的 exact-zero usage attestation；绑定 command、graph/revision、node、attempt、node grant、target、adapter 与 invocation，digest/字段/非零漂移均失败关闭。
- `tests/unit/test_ne01_governed_delegate_dispatch.py` 验证生产唯一目标 `pentest_agent` 的 port-free 注册、无模型/工具/网络/进程 runtime import、完整绑定、篡改与非零拒绝。
- `tests/unit/test_gr05_frozen_route_executors.py` 与 `tests/unit/test_gr05_worker_budget_usage.py` 验证执行器只消费新 attestation，忽略 legacy intent/repair Agent trace，并在缺失、attempt drift 或非零时阻断 COMMIT。
- `tests/integration/test_gr05_universal_host_runtime.py` 在真实 SQLite 中验证外层 canonical Delegate dispatch 预扣一次工具调用，adapter 内部 token/tool/retry/model-cost 为零，并由 WorkerProjection 与 durable BudgetUsage 共同保存 attestation ref。
- 定向集合为 `56 passed in 19.61s`，0 fail。没有 migration、真实 provider、波次 integration 或 NE-07 Docker 全栈；当前启用 Delegate 预算控制为 `PASS`，Delegate 路线与 NE-01 仍为 `INCOMPLETE / IN_PROGRESS`，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。
- 文档/发布合同为 `78 passed in 10.90s`；audience、authority、20 条 freshness 规则、Core Architecture Gate 1～14、统一 static scan、changed-module compileall、机器状态 JSON 与 `git diff --check` 均通过。Ruff 在当前环境未安装，记为 `NOT_RUN_TOOL_UNAVAILABLE`。

## NE-01 普通受治理 Skill 模型预算当前切片

- `tests/unit/test_ne01_governed_skill_model_budget.py` 覆盖 typed guard、连续调用剩余额度、缺 usage、attestation drift、普通 Skill 裸端口隔离、`emergent_growth_weekly` governed fail-closed 与外层 WorkerBudgetUsage 结算。
- ModelAttempt、Emergent Growth、Skill Broker 和 frozen route 的去重定向集合为 `60 passed in 31.84s`。
- Skill registry/loader 兼容集合为 `78 passed, 4 failed`；4 项在纯 `origin/dev@632810516` 上以相同 `DEGRADATION_STATE_UNAVAILABLE` 失败，确认为既存基线，不冒充本切片 PASS，也不在本切片修改。
- changed module `compileall`、机器状态解析、`git diff --check`、文档 audience/authority/freshness Gate 与统一静态扫描均通过；发布/文档合同另为 `65 passed in 10.85s`，不与代码集合相加。Ruff 在当前环境不可用，记为 `NOT_RUN_TOOL_UNAVAILABLE`。
- 本切片没有 migration，不运行真实 provider、波次 integration 或 NE-07 Docker 全栈；Program 状态保持 `IN_PROGRESS`，远程 CI 为 `NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 独立 Worker 证据与 Plan 产物流当前切片

- 通用 Verifier 在 worker 结束后通过独立 reader 重开 WorkerProjection，复验 immutable authority、receipt digest、完整 COMMIT 与 `TaskNode.output_contract`；Acceptance 使用第二次独立读取并复验 verification identity。
- 固定结构策略才允许自动 `PASS`；无法由 durable evidence 判断的自然语言标准稳定返回 `REVIEW`。projection 缺失、digest/COMMIT/grant/identity 漂移和输出合同错误均失败关闭。
- Plan final Chat 只消费两个已验收前驱 TaskResult artifacts，并把 artifact ref、worker receipt、payload digest 与 bundle digest 写入 trace attestation；普通 Chat 不携带该证明，也不会被 Plan artifact 污染。
- 当前定向单测 `50 passed`；Tool/Operator 真实 SQLite 集成 `9 passed`；Universal Host Chat/Delegate/两 Skill 节点/最终 Chat/replay/principal-drift 集成 `1 passed`。三个集合分别报告，不相加。波次 integration、真实模型、commit-bound acceptance 与 NE-07 未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

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
- `0130_task_operation_recovery_hold.sql` 为 `UNKNOWN_SIDE_EFFECT`、`CANCELLATION_CROSSING`、`CHECKPOINT_BUDGET_CONFLICT` 和 `CHECKPOINT_AUTHORITY_CONFLICT` 建立 append-only case。known interruption 的 `RECOVERY_REQUIRED` marker、Operation Outbox、case 与 `OPERATION_RECOVERY_HOLD_RECORDED` Outbox 现在同属一个 UoW；故障 trigger 证明 case INSERT 失败时 marker/两个 Outbox 全部为 0。subject 精确绑定 Graph、Node、Attempt、Operation、graph Budget、取消与 active Lease 版本；exact replay 必须返回同一 case/outbox，UPDATE/DELETE 和绑定漂移失败关闭。
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
- 该历史 checkpoint 切片当时的 migration head 为 `0127_worker_projection_immutability.sql`；现行仓库 head 已推进到 0136。该证据使用本地临时 key、单节点和进程内 route continuation；0130～0135 的 Task 证据与 0136 的 cohort evidence 分别记录，不能反向扩展本历史切片。部署 key/HTTP/network canary、七路线波次与 NE-07 Docker 全栈仍未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 受治理签名 Operation 重获与原图续跑当前切片

- migration 0126 新增 `task_runtime_operation_reconciliation_evidence`。写入时用 verification-only Ed25519 key ring 验签并绑定当前 `RECOVERY_REQUIRED` reservation/version/action；evidence ref/digest 确定性派生，同 request exact replay 只读，payload drift 拒绝。真实 SQLite trigger 阻断 UPDATE/DELETE。
- `task_operation.reconcile` 通过 Secure Ingress、typed Governor、risk-4 Review、Action Preview、HostCommand/TaskGraph、Verification、Acceptance Effect 后执行。通用图中没有 `attestation_signature`，Accepted handler 从 evidence ledger 重开并再次验签。
- `REACQUIRE` 只保留同一 Attempt，签发严格更高 fence、新 owner 与新的 Budget execution segment；随后调用原 route continuation。真实 SQLite 端到端测试证明首次请求为 `WAITING_REVIEW`，批准后原 worker 只续跑一次并将 Operation 收敛为 `COMMITTED`；相同外层请求重放不再次进入 target runtime。
- handler、compiler、Store 结构、签名重获及 governed Operator 关键组合为 `64 passed in 12.77s`；单条完整生产 composition 路径独立为 `1 passed in 7.77s`，二者可能重叠，不相加。
- 最终回归按完整文件拆开执行：Operation/Governor `39 passed`，Action Preview `25 passed`，Scheduler/SysMode/Edge Operator 分别 `3/2/1 passed`，online takeover `23 passed`，Composition/结构 `104 passed`，migration contract/compatibility 分别 `23/31 passed`，文档 authority/freshness/metadata `27 passed`。职责拆分后的 58+2 项与前述集合重叠，不相加。两次合并 pytest 命令达到 60 秒工具上限而被终止，未计为失败或 PASS；同一完整文件已在上述拆分批次重跑。
- `governed_operator_runtime.py` 已从 1559 行拆为 1432 行主编排与 170 行纯 frozen-execution 模块；未扩 policy allowlist。生成 Operator client `--check`、统一 doc Gate、统一 static scan、JSON/compile/diff-check 通过，完整架构静态 Gate 1～14 为 `PASS`、`failed_gates=[]`。
- 测试 key 是临时 Ed25519 材料；功能默认关闭，未验证部署 key lifecycle、真实 HTTP/network、一般 unknown-effect 自动归因、general multi-node/batch recovery、NE-01 wave integration 或 NE-07 Docker 全栈。总体保持 `IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 执行中断持久分类与签名取消续接当前切片

- worker self-cancel、单节点 time guard、已识别 worker failure、heartbeat loss 与 caller cancellation 在 Operation 预留后先尝试 exact Attempt/Lease/owner/fence/version marker；`RESERVED → RECOVERY_REQUIRED`、Operation Outbox、0130 recovery case 与 case Outbox 在同一个真实 SQLite UoW 中提交，exact response-loss replay 不二次写，版本、owner、原因码、case 或 Outbox 漂移均失败关闭。
- marker 只保存有界大写原因码，不保存异常正文；caller cancellation 原样向上传播。Runtime 必须只读重开 marker 同事务产生的唯一 case。worker failure 后的 BudgetUsage 会推进 Budget version，但 current case仍按 exact Operation version保留；Lease/Attempt/Node/Graph 的后续围栏同理。Graph cancellation 会新增 intent-bound successor case，旧 unknown case退出 current；两条 append-only history都保留。marker、运行时 helper 与 Store mixin 均没有 Attempt/Node/Graph/Budget/Lease terminal authority，也不能 `COMMIT/RELEASE` Operation。
- 本提交去重后的受影响集合为 `114 passed in 184.27s`，覆盖 Coordinator/结构门禁、0052/0054 兼容 Operation checkpoint、Graph cancellation Store、跨进程 cancellation continuation、online takeover、0130 INSERT 故障整体回滚，以及 worker failure 后 Budget version前进而 case仍 current。首轮同一集合为 `112 passed / 2 failed`：两项均为合并后测试 fixture 未跟随 `_Verifier` 初始化和 0134 原子 Review 端口；改为真实 `ReviewStore/TaskNodeReviewRuntime` 与 `commit_node_acceptance_review` 后单项 `2 passed`，再全量重跑得到上述 114 项。过程数字不相加。
- changed-module `compileall`、两份 release JSON、core policy JSON 与 `git diff --check` 通过；fresh architecture Gate 1～14 为 `PASS`、`failed_gates=[]`，统一 `static_scan,doc_gate` 为 `PASS`、`failures=[]`，未扩大 policy allowlist。
- 跨进程集成从该 marker 开始，用临时 Ed25519 key 生成 `RELEASE + NO_SIDE_EFFECT` proof，重建 Store 后运行 `GraphCancellationContinuationWorker`；Graph/Node/Attempt 为 `CANCELLED`，Operation 为 `RELEASED`，第二次重启零工作，`OPERATION_RECOVERY_REQUIRED`、`OPERATION_RECOVERY_RELEASE`、`TASK_GRAPH_CANCELLED` Outbox 各一条，worker 没有重跑。
- 当前 runtime/store/structure/cancellation 定向集合为 `133 passed in 91.94s`。随后把文档 authority/freshness、机器 coverage、metadata、Obs 合同与受影响 Runtime 合并复核为 `147 passed in 140.38s`；它包含或重叠前述测试，不相加。目标模块 `compileall`、两份 JSON、doc-only delivery gate、统一 static scan 与 `git diff --check` 均通过；fresh architecture Gate 1～14 为 `PASS`、`failed_gates=[]`，`task_repository.py` 4597 行、`node_execution_runtime.py` 1495 行，未扩大 policy。
- 该历史切片自身没有 migration；当时仓库 head 为 0126，现行 head 已推进到 0129。测试 key 是进程内临时验签材料，不是部署凭据；Operation/Effect Operator 仍默认关闭，HTTP/key lifecycle/canary 未运行。general Effect 的签名人工归因后来已形成独立本地切片，但自动归因、其它 cancellation、general multi-node/batch、Workspace 一般写、七路线验收、波次 integration 与 NE-07 Docker 全栈仍未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Host/Internal 周期 survivor 当前切片

- `PeriodicDurableContinuation` 只监督 cadence、wake 合并和关停，不持有 TaskGraph、Lease、worker 或 Effect authority。单元测试覆盖 timer/wake 串行、普通失败留痕后重试，以及关停等待活动 durable scan 而不取消事务。
- Host 在完整 RuntimeGroup 与共享 Acceptance Effect owner 就绪后先执行 mandatory boot pass，再周期重用 `HostStartupGraphContinuation`。真实 SQLite 测试持久化一个 live Lease，首次扫描保持 `DEFERRED`；可信 clock 越过 expiry 后，在线 wake 无需重启 Host 或客户端 replay 即重入同一个 immutable admission。
- 后续分页修正已取代“第 101 条 overflow 时整轮失败”的旧边界：repository 以 immutable admission `(created_at_ms, command_id)` 和 surface-bound cursor 做 keyset pagination，continuation 复验严格递增页序、页尾 cursor、command/Graph 唯一性以及每页/整轮上限。单元测试覆盖多页全量恢复和乱序页拒绝；真实 SQLite 集成由独立 OS 进程写入 101 个 admission，再由新 Runtime 跨两页全部重开，并覆盖 cursor authority 篡改拒绝。当前核心定向集合为 `10 passed in 9.87s`；其中单条跨进程 101 admission 用例独立复跑为 `1 passed in 6.16s`，二者重叠不可相加。受影响 Host/ASGI/Task Runtime composition/repository/文档状态集合最终为 `78 passed in 14.65s`。首轮该集合有 `66 passed / 12 failed`，12 项均来自 composition 的 `_TaskStore` 测试替身漏实现现行 0134/0135 readiness 方法；补齐两个真实生产合同及顺序断言后，该文件 `17 passed`、最终 78 项全绿，不能把首轮 fixture 漂移记成产品回归或 PASS。
- 控制面 continuation 复用相同分页校验器但绑定独立 `INTERNAL/SCHEDULER/OPERATOR` surface digest。`activate_internal_runtime()` 的顺序必须为 Task Runtime/RuntimeGroup 激活 → Host mandatory scan → RuntimeGroup control mandatory scan → Scheduler clock。测试以 101 条控制 admission 验证两页路由到唯一 `internal_agent/scheduler/operator`，以并发调用证明 mandatory scan 只执行一次，以 mandatory 和 periodic 两种阻塞中的 exact re-entry 证明 shutdown 等待自然完成且没有 task cancellation，并用真实 SQLite 各写入一条 Internal/Scheduler/Operator admission 后重开对应 continuation port；该新增定向集合为 `5 passed in 1.92s`。首次静态扫描准确发现 Composition 直接导入两个 App 实现，修正为 App composition 注入 factory 后通过；随后架构 Gate 3 又准确发现 `internal_task_runtime.py` 增长到 1507 行，抽出 `internal_startup_admission.py` 后降至 1457 行且未扩白名单。职责拆分后的 Internal/ASGI/RuntimeGroup 定向回归为 `56 passed in 58.09s`，最终 static scan 与完整架构 Gate 1～14 均通过。它不等于真实 production worker 跨进程完成，也不提升一般恢复或 NE-01 状态。
- `test_ne01_host_worker_process_restart.py` 使用真实 Windows spawn、file-backed SQLite 和硬退出码 91/92/93，禁止以抛异常后继续使用同一进程替代。三条 case 分别覆盖：Operation 前崩溃后同 Attempt 高 fence 单次 worker 完成；Operation COMMITTED 后崩溃由新进程零 worker replay 完成 Verification/Acceptance；已 ACCEPTED root + child Operation 前崩溃时保持 root artifact 不变、只恢复 child。每条都再启动第三进程证明 inventory 与 worker 调用为 0，并核对 Attempt、Lease lineage、Operation、Verification、Acceptance、Artifact、Node 和 Graph 的真实 SQLite 记录。首跑双 case 为 1 passed/1 failed，失败是把 decision payload 的嵌套字段当顶层；修正后 2 passed。加入多节点后为 2 passed/1 failed，失败是测试误认为 Lease 直接含 node_id；改以 attempt_id 关联后最终 `3 passed in 22.41s`。与现有同进程 takeover 两条及 Host startup pagination 合并的最终相邻回归为 `8 passed in 42.89s`，包含前述三条所以不得相加。受控 projected worker 不是外部生产 provider，并发 fan-out 与 RESERVED unknown-effect 不在该证据内。
- Internal 首次 sealed-checkpoint scan 后启动同一生命周期 supervisor；`HOLD` checkpoint 在下一次周期可重新评估，但仍必须通过既有 seal、projection recovery envelope、historical Lease、higher fence、Operation/Budget/Grant/Preview/Task lineage，Agent 不会由 supervisor 直接调用。
- 当前 file-disjoint 运行时验证为：新增 supervisor/Host/Internal/SQLite 集合 `13 passed in 4.58s`；Host/ASGI/RuntimeGroup/Internal terminal 与四条真实 Internal SQLite+Git checkpoint/review/cancellation 恢复合并集合 `85 passed in 27.57s`。两组可合计为 98 个不同 case，但历史批次和后续文档 Gate 不得重复相加。兼容集合首次命令使用了不存在的旧 recovery 文件名，pytest 在 collection 前以“no tests ran”退出；改为 `test_aw02_internal_agent_runtime_cutover.py` 的四个精确 selector 后 85 项全绿。
- 覆盖 manifest、文档 authority/freshness、交付元数据与本切片核心回归的最终组合 Gate 为 `52 passed`；它包含前述 13 项新增集合，不能与 98 相加。第一次命令引用了不存在的旧文件名，pytest 在 collection 前以“no tests ran”退出；修正为 `test_delivery_metadata_gate.py` 后组合 Gate 全绿，不能把首次命令伪记为测试失败或 PASS。
- 目标模块 `compileall`、两份机器状态 JSON、`git diff --check` 均通过；fresh architecture static Gate 1～14 为 `PASS`、`failed_gates=[]`，未扩大白名单。分页修正后的最终文档 authority/freshness、交付元数据与 Universal Runtime coverage 集合为 `39 passed in 9.93s`，独立 `doc_gate` 也为 `PASS`、failure 为空。
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

## NE-01 cleanup Guardian 与多节点 Budget 定向证据

本切片的去重定向命令覆盖：

- 真实 migration SQLite、`RuntimeEventStore`、runtime scope/owner/lease/fence
  下的 cleanup failure → Guardian breach → replacement-process recovery；
- 同一连续失败第二次轮询不新增 Guardian event，成功 exact replay 不重复
  resolution；
- 私密路径、凭据、异常正文和 exception type 不进入 durable Guardian journal；
- Guardian observer 故障时 worker、`/health`、`/ready` 保持降级；
- ASGI 唯一 runtime context Guardian 注入 Workspace composition；
- 一般两节点 Graph 的实际 remaining-time timeout、rejected BudgetUsage、
  child Budget `EXCEEDED` 与 Operation `RECOVERY_REQUIRED`；
- durable graph concurrency claim 为一时，首节点持有 child reservation，
  第二节点仍为 `READY`；
- ASGI 聚焦文件边界与抽离后的 Agent immune scheduler handler。

结果为 `48 passed in 21.13s`，0 fail/skip；基线为
`origin/dev@f2dae580df4ff7308deb966f41b605de7b3a37f0`，无 migration。
这不是 NE-01 波次 integration，也没有启动 Docker/Host/Obs/Monitor/Edge/
Gateway；一般 Budget 与 supervisor 仍未验收。PR #176 首个 HEAD 的 9 个
失败 job 已逐个验证为零步骤、无 runner，另有 1 个 skipped，记录为
`NOT_RUN_ACCOUNT_QUOTA`，不计为远程通过。

## NE-01 Workspace cleanup 健康与关停重试

- `/health` 必须显示 app-local cleanup worker 的 HEALTHY/DEGRADED/STOPPED/
  RETRY_REQUIRED 状态；响应只保留 error type、时间戳、计数，不得出现 raw
  detail、workspace ID 或物理路径；
- `/ready` 在 configured worker unhealthy 时必须 `ready=false`，但 W2 未配置
  且没有 shutdown error 时为 `NOT_CONFIGURED`，不能误报故障；
- shutdown 第一次 Workspace close 失败、第二次成功时继续 hard-state 与
  process-reference withdrawal，最终不保留错误；
- 两次失败时，不论 live runtime 或 partial startup，均在 hard-state close
  前终止，保留 container/Task handle 并返回 `dependencies_preserved=true`；
- 真实 cleanup continuation worker 的 failure health、stop probe、pending
  inventory 续跑与 ASGI composition 回归保持通过。

去重定向集为 `25 passed`；文档与机器状态合同为 `39 passed`，统一
`static_scan,doc_gate` 和架构 Gate 1～14 均通过且未扩大白名单。无
migration。该 Gate 未执行 process kill、Host 真机重启或 NE-07 服务栈；
PR #175 的 9 个失败 job 已逐项确认均为零步骤且没有 runner，另有 1 项按
设计跳过，远程 CI 因而记为 `NOT_RUN_ACCOUNT_QUOTA`，不是代码 PASS。

## NE-01 runtime_repo 只读快照验证

- unit fixture 创建真实 Git repository，冻结已提交 `HEAD` 后再制造 dirty tracked
  与 untracked 私有文件；snapshot 必须只包含 commit 中的内容、没有 `.git`，
  并绑定 `git-commit://<sha>`；
- archive 中绝对路径、父目录逃逸、symlink/hardlink 或非 regular member 由
  policy 失败关闭；snapshot physical path 必须仍位于 runtime-owned runs root；
- executor fixture 故意写改 snapshot，source checkout 内容必须不变；result
  必须记录 `workspace_source=runtime_repo_snapshot`，返回前 snapshot 已清理；
- `runtime_repo/current_runtime_repo` 对 write-capable WorkOrder 的既有 W0
  拒绝保持不变。

本 Gate 是本地文件系统与真实 Git 定向证据，不是 OS/container readonly mount
或租户隔离证据；policy/executor 定向为 `20 passed`，连同 WorkOrder、Skill
wrapper 与 Immune 早期准入回归的去重受影响集为 `44 passed in 1.97s`。无
migration；文档/机器状态合同为 `39 passed`，统一 static/doc Gate
无 failure，架构 Gate 1～14 全部通过且未扩大白名单。NE-01 保持
`IN_PROGRESS`，远程 CI 为
`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Plan writer 提交后预算拒绝证据

- 新场景先完成真实 Review continuation、Git Workspace 写入与 stage seal，
  再持久化 WorkerProjection/Operation，最后用实际 token usage 触发
  graph Budget rejection；断言 terminal 为 `BUDGET_BLOCKED`。
- fresh application 重开同一 Graph 时，writer、已完成 read sibling 和 result
  sibling 均不得再次调用；current 0090 必须是 `BUDGET`，Graph=`BLOCKED`、
  Budget=`RELEASED`。
- TaskStore reader 必须返回
  `PARALLEL_CHECKPOINT_BUDGET_REJECTED`，并逐项验证唯一 winner、父/子
  BudgetUsage、FAILED Node/Attempt、COMMITTED Operation、released Lease、
  child Outbox 与 0090 Outbox。
- 复制数据库并删除 current 0090 Budget Outbox 后，terminal reconciler 必须
  返回 durable-data invalid，Workspace 保持 `ACTIVE` 且 worktree 存在；
  同一篡改副本绕过 reconciler 直调 Workspace runtime 时，仓储最终 CAS
  也必须在 artifact abandoner 前拒绝。未篡改副本才允许
  `ABANDONED/CLEANED`，第二次重启 inventory 为零。
- 既有 2 节点、4 节点、winner 先/后于 sibling 以及两个 owner 并发接管用例
  一并复跑，证明 settlement 会等待全部 child fence，并能从 exact durable
  terminal 重建不在本地 outcome tuple 中的 winner。

代码去重受影响集为 `53 passed in 138.76s`，Workspace lifecycle authority
合同集另为 `24 passed in 67.50s`，两组不相加。无 migration，基线为
`origin/dev@c7984d9e0bb95a5b0176360c39b2e405e247f63e`。本结果不是
NE-01 wave integration，也没有启动 Docker 或真实外部 provider；
`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Plan 签名失败与时间预算拒绝交叉证据

- 新参数使用真实 reviewed Plan Workspace writer，在 stage seal 后、
  WorkerProjection/Operation checkpoint 前中断，并用确定性 monotonic clock
  让实际 `time_ms` 超过 5,000ms graph Budget。
- 签名 `RELEASE / NO_SIDE_EFFECT` 后，节点必须为 Attempt/Node=`FAILED`、
  Lease=`EXPIRED`、Operation=`RELEASED`；Graph 暂时 `ACTIVE`、Budget
  `EXCEEDED`，禁止提前清理 Workspace。
- fresh Runtime 必须追加 current 0090 `BUDGET` decision，得到
  `BUDGET_BLOCKED`、Graph=`BLOCKED`、Budget=`RELEASED`，且 writer/result
  调用数不增加。
- TaskStore 只在失败 `error_ref` 与唯一 Budget winner 的 `usage_id` 同时
  出现在 selected outcome，并能重开父/子 BudgetUsage、child reservation
  和 Outbox 时，才允许既有
  `RECONCILED_OPERATION_NODE_FAILURE` 以 Graph `BLOCKED` 形状重开；签名
  reconciliation response loss 必须 exact replay，且 Accepted handler 的
  v4/v5 receipt ref 在 0090 PENDING→COMMITTED 前后保持相同。
- Workspace authority 在独立 SQLite read/CAS 中重复验证同一证明。复制
  数据库并删除 current 0090 Budget Outbox 后，TaskStore reader 抛出
  durable-data invalid，Workspace helper 返回 false，artifact abandoner
  不得运行；正常数据库才允许 `ABANDONED/CLEANED`。

`tests/integration/test_ne01_plan_node_review_runtime.py` 六种真实 Plan
Workspace 终态为 `6 passed in 39.93s`；相邻 Accepted handler、签名失败、
Workspace 与多节点预算回归为 `46 passed in 137.82s`，两组独立报告，不
汇总为波次证据。无 migration，基线为
`origin/dev@921a56b77a05b192e1967efe97b06142a782166c`。该结果不等于
一般 Budget/recovery matrix、NE-01 wave integration 或 NE-07 全栈；
`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Plan 写节点多节点签名失败证据

- 使用真实 capability PlanningSearch 编译三节点 immutable TaskGraph；
- 前置只读节点通过，唯一写节点经真实 Review continuation 后在真实 Git
  worktree 写入并密封 stage，随后于 WorkerProjection 前抛出中断；
- Runtime 返回 `RECOVERY_REQUIRED`，临时 Ed25519
  `RELEASE/NO_SIDE_EFFECT` 仅生成 `NODE_ONLY/PENDING`，此时 Graph/Budget
  必须仍为 `ACTIVE`；
- fresh Host owner 触发 0090 failure settlement，Graph=`FAILED`、
  Budget=`RELEASED`，结果节点调用次数为 0；
- 历史 `REVIEW` 与最终 `FAILURE` decision 同时保留；current reader 必须
  重开全部 canonical decision/Outbox，并选择唯一当前 Graph version；
- Workspace proof 删除最终 Budget Outbox 后必须拒绝，原数据库则完成
  `ABANDONED → CLEANED`、source HEAD/文件不变、第二次重启零 replay。

最终去重受影响集为 `58 passed in 128.70s`，覆盖 Plan 四种 terminal
outcome、Direct Skill 签名篡改、Workspace 合同、Operation 多节点结算和
terminal reconciler 单元回归。无 migration，基线为
`origin/dev@49615ae6e4d4364c26b3eb651012fa50bf959d0e`。这不是 NE-01
波次 integration，也未运行 Docker/网络/前后端全栈；远程 CI 为
`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Direct Skill 签名失败 Workspace 证据

- 真实 Agent writer 在真实 Git worktree 写入并密封 stage，随后不提交
  WorkerProjection 或 Operation COMMIT，以模拟响应丢失前的真实中断边界；
- 真实 SQLite Operation 先进入 `RECOVERY_REQUIRED`，时钟越过原 Lease
  到期点后，由临时 Ed25519 key 签署 exact `RELEASE/NO_SIDE_EFFECT`；
- TaskStore 必须在一个 UoW 中得到 Graph/Node/Attempt=`FAILED`、
  Lease=`EXPIRED`、Operation/Budget=`RELEASED` 及六类可重开的 Outbox；
- fresh Runtime 重新验签后才允许 `ABANDONED → CLEANED`，源 Git HEAD 和
  文件不变，第二个 fresh Runtime 的 terminal inventory 为零；
- 删除 `OPERATION_RECOVERY_RELEASE` Outbox、注入 Acceptance Effect 或把
  proof signature 替换为同格式无效签名时，terminal worker 必须
  `BLOCKED`，Workspace 保持 `ACTIVE`；
- Workspace contract 另证明只伪造 `FAILED/FAILED/FAILED/EXPIRED` 状态、
  没有完整证明时不能取得 abandon authority。

代码相关的两个非重叠定向批次为 `44 passed in 90.46s` 与
`11 passed in 75.31s`；前者覆盖 Workspace 合同、四条真实签名/篡改集成和
取消 proof，后者覆盖既有 Direct Skill success/failure、签名 Operation
failure 与 Plan Review 回归。数字不与先前批次相加为波次结论。本切片无
migration，基线为
`origin/dev@8030a0cc69e2d88c4761e03ea9cf435630555878`；NE-01 波次
integration、真实网络与 NE-07 服务栈未运行，远程 CI 为
`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Plan 写节点 Workspace 原子终态证据

当前切片新增两条真实 SQLite + Git 集成路径：

- `graph-success`：三节点 Plan 中一个经 Review 的风险 4 Agent 写节点生成
  密封 proposal；该节点 Acceptance 后 Effect 仍为零，整图 Acceptance 才
  原子暂存 promotion；真实 handler 完成 Action Preview、accepted HEAD
  晋升和 fresh Runtime cleanup；
- `downstream-failure`：同一写节点已经 ACCEPTED，但下游 result-node
  Verification 失败使 Graph 进入 FAILED；确认零 Acceptance Effect 后，
  fresh Runtime 按 exact decision/artifact/Operation/projection 将 Workspace
  ABANDONED/CLEANED，失败文件不进入源分支。

两条路径都验证 source `main` HEAD/文件未改变、第二次重启 inventory 和
replay 为零。单元/合同还覆盖：最多一个写 owner、risk/Review/effect-mode
漂移拒绝、speculative Effect 为零、node-owned graph Effect、多个节点
Effect 的 parallel-success UoW、缺 Runtime Context/Scene Preview 拒绝，
以及只有完整零副作用证据才允许 FAILED Graph 中的 ACCEPTED node abandon。

本批无 migration。受影响集合一次运行取得 `99 passed in 127.35s`；最终
“任何已暂存 Effect 都不得 abandon”的 fail-closed 收紧后，真实 Plan
success/failure 集成定向复跑 `2 passed in 17.83s`。文档/机器状态/发布合同为
`56 passed in 9.35s`。统一 `static_scan,doc_gate` 为
`passed / failures=[]`，完整
架构 Gate 1～14 为 `PASS / failed_gates=[]`，changed-module `compileall`、
两份 JSON 与 `git diff --check` 通过。其它 Plan
failure/cancellation/unknown-effect 组合、
多 Workspace 并发写、强隔离、NE-01 wave integration 和 NE-07 真实全栈
仍未执行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 `RESERVED` 未知副作用跨进程恢复证据

2026-07-27 新增 Windows `spawn` 真实进程链：

- 进程 A 在 SQLite 已提交 Operation `RESERVED`、worker 尚未调用时
  `os._exit(94)`；
- 原 Lease 存活与到期后的两个 fresh Host 均证明 worker 调用数为 0；
- 到期启动恢复提交 `RECOVERY_REQUIRED`、`EXPIRED` Lease、可恢复
  Attempt 与唯一 current recovery hold；
- 独立进程验证 case-bound Ed25519 `NO_SIDE_EFFECT`，在同一 Attempt
  上取得 `fence + 1`；
- fresh Host 只执行一次 worker并提交 Verification、Acceptance 和
  Graph completion；终态再重启的 inventory 与 worker 调用均为空；
- HostCommand、WorkOrder、Assignment、TaskGraph JSON 中不存在完整签名，
  append-only evidence 中的签名与请求精确相同；scheduler snapshot 只含
  Store 验签后的最小 execution-fence 投影，不含签名、scope、fingerprint、
  receipt 或 reconciler。

启动发现层的单测、既有签名换权和新进程链为
`10 passed in 16.20s`；相邻 Host restart、governed Operator、
online takeover 与 startup 集成为 `33 passed in 137.00s`，两组重叠不
相加。初始运行先暴露了“startup 已将旧 Lease 写成 EXPIRED 后无法换权”的
真实生产缺口，修复后才取得最终结果。该 worker 是受控生产协议 adapter；
真实 provider、完整波次与 Docker 服务栈未运行，远程 CI 为
`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Direct Agent Skill Workspace 终态证据

当前切片的定向集合为 `57 passed`，由以下互补证据组成：

- 真实 SQLite + 真实 Git worktree：成功 promotion 后在 fresh Runtime 中
  清理，源 HEAD 不变，再重启零重放；
- 真实 SQLite + 真实 Git worktree：Agent 写入失败后密封 artifact，
  immutable rejection projection 与 canonical Verification failure 精确
  绑定，fresh Runtime 执行 ABANDON→CLEAN，失败文件不进入源分支；
- 活跃 direct Skill stage 只返回 `HOLD`，Internal reconciler 不再重复解释
  或报告伪损坏；
- frozen Skill wrapper 的成功分支只生成 promotion intent，失败分支只生成
  bounded terminal descriptor 且不包含 `_protected_stage`；
- terminal worker、route ownership、模型预算和 governed WorkOrder 相邻
  单元回归通过。

本前序集合无 mock Workspace、无内存 TaskStore，也没有用端口探针代替
文件状态验证。它本身不包括 Plan；下一节单独补充 single-writer Plan
success/downstream-failure/downstream-cancellation，但所有其它 review/budget/raw-failure/
cancellation terminal matrix、OS/container isolation、NE-01 wave
integration 或 NE-07 Docker/前后端全栈仍未覆盖。远程 CI 继续记录
`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Plan 单写节点 Workspace 原子终态证据

- capability Plan 至多允许一个 reviewed、risk-4、非结果
  `POST_ACCEPTANCE_WORKSPACE` Agent Skill；第二个写 owner、写结果节点或
  policy drift 在 Effect 前失败关闭。
- 写 worker 只生成 sealed proposal。只有整图成功时，node-owned promotion
  才与 result Effects 在一个 parallel-success UoW 中暂存。
- 下游验证失败时，Graph=`FAILED` 且 write node=`ACCEPTED`；只有精确
  Acceptance/artifact/Operation/projection 和零 node Effect 才可 abandon。
- 下游结果等待 Acceptance Review 后由真实 Secure Ingress authority 取消时，
  Graph=`CANCELLED` 而 write node 仍为 `ACCEPTED`。immutable intent 保持
  `BLOCKED` target，可信 final decision 单独绑定 `CANCELLED`；intent-after、
  decision-before、decision-after 的 node status/version 必须完全一致。
- 真实 SQLite 与临时 Git worktree 三路径为 `3 passed in 21.34s`，覆盖
  graph-success、downstream-failure、downstream-cancellation；三者都验证
  source HEAD/文件不变、fresh Runtime 收口和第二次重启零 replay。Workspace
  仓储合同另为 `23 passed in 58.29s`，两组不相加。
- 抽取只读取消 proof 模块后，Plan 三路径与 Workspace 仓储合同的去重受影响
  集为 `26 passed in 79.73s`；再加入 Direct Skill 相邻回归的本批最终受影响
  集为 `28 passed in 92.34s`，二者重叠不得相加。
- 只读取消 proof 的独立防篡改单测为 `6 passed in 0.33s`：精确 authority
  通过；intent/decision payload digest、decision ref、intent status version
  漂移及 decision 缺失均失败关闭。最终把该组与三路径 Plan、Workspace 仓储
  合同合并复跑为 `32 passed in 82.89s`；它包含前述 3、23、6 项，不再相加，
  且不包含另行记录的 Direct Skill 相邻回归。

本批无 migration，不宣称其它 failure/cancellation/unknown-effect 组合、
多 Workspace、强租户隔离、NE-01 波次集成或 NE-07 全栈完成。远程 CI 仍为
`NOT_RUN_ACCOUNT_QUOTA`。当前 coverage、authority、freshness 与 metadata
文档合同为 `39 passed in 10.01s`。

## NE-01 Direct Skill 验收评审取消终态证据

- 真实 Direct Agent Skill 成功执行并提交 `COMMITTED` Operation 后，测试用
  canonical Verification UoW 落下 PASS，再用真实 ReviewStore 和 Acceptance
  Review UoW 形成 Node=`WAITING_ACCEPTANCE_REVIEW`、Graph=`BLOCKED`。
- 取消由 SQLite Secure Ingress claim 和 production
  `request_graph_cancellation()` 发起；断言 Graph/Node/Review=`CANCELLED`、
  Attempt=`SUCCEEDED`、Budget=`RELEASED`、final intent/decision 完整。
- fresh Runtime 只在 post-review decision、取消三快照、当前 Graph/Node
  版本和零 Acceptance Effect 全部一致时执行 `ABANDONED → CLEANED`；源 Git
  HEAD/文件不变，第二次重启零 replay。
- 故障注入在同一已取消节点加入一个持久 Acceptance Effect；terminal worker
  必须返回 `BLOCKED`，Workspace 保持 `ACTIVE`，不得 tombstone 或 cleanup。
- proof 单元测试覆盖 intent/decision digest、ref、status version、Graph
  version、Node version/status、缺 decision、Review 未取消/版本漂移、
  Operation 未提交/无 receipt 和已有 Effect；raw 状态矩阵没有 finalized
  cancellation authority 时，Workspace repository 必须拒绝。

五个去重受影响文件一次运行结果为 `68 passed in 117.23s`。该集合已经包含
terminal reconciler、取消 proof、Workspace repository、Direct Skill 与 Plan
回归，不与先前 55/4/3 等开发中复测相加。本批无 migration，基线为
`origin/dev@55ece25163667a4db5464a6d2451ab3e7b8f76c9`；NE-01 波次集成、
真实网络和 NE-07 服务栈未运行。当前文档 authority/freshness、交付元数据与
coverage 合同为 `39 passed in 12.64s`；远程 CI 为
`NOT_RUN_ACCOUNT_QUOTA`。

## NE-01 Review continuation 坏前缀活性证据

- 文件型 SQLite 中构造 500 条跨表 subject-version 漂移协调行和 1 条健康行；
  首轮验证 500 行各自在独立事务持久退避，第二轮验证健康行被唯一领取。
- 健康投影固定为
  `natsume.review_continuation.failure_snapshot.v1`，最多采样 128 行形成
  opaque digest，公开结果不含 Review ID、用户内容或原始错误。
- 真实 runtime-scoped Guardian 验证首次 failure、摘要变化不重复追加、
  replacement process 重开以及唯一 recovery resolution。
- RuntimeGroup/Host composition 验证同一个 app-local observer 被注入 worker，
  无全局 singleton 或第二 journal；focused structure Gate 保持 Store
  `<1500` 行。
- 最终受影响集合为 `88 passed in 116.19s`；文档、交付元数据与 coverage
  合同为 `39 passed in 7.58s`，fresh 架构静态 Gate 1～14 全部通过；无 migration。该证据不包含独立
  terminal DLQ/Operator 修复 UI、跨进程即时 wake、production canary、
  NE-01 波次集成或 NE-07；远程 CI 仍为
  `NOT_RUN_ACCOUNT_QUOTA`。

### NE-01 Budget 动作 support matrix

- 新增 `test_ne01_budget_policy_runtime_boundary.py`，分别从
  `ensure_active()` 与低层 precharge 证明 `DEGRADE/REVIEW` 在任何 Store 或
  Outbox 调用前以稳定 reason 失败关闭；合同 round-trip 仍保留 REVIEW。
- 初始组合 `test_ne01_budget_policy_runtime_boundary.py`、
  `test_ne01_budget_runtime_contention_retry.py` 与
  `test_aw02_governed_task_coordinator.py` 最终复跑为 `30 passed in 0.94s`。
- 文档、交付元数据与 coverage 合同为 `79 passed in 36.98s`；changed-module
  `compileall`、JSON、`git diff --check`、统一 `static_scan,doc_gate` 和完整
  架构 Gate 1～14 均通过。
- 更宽的 62 项结构探针结果为 `60 passed, 2 failed`；两个失败文件与
  `origin/dev` 逐字相同，分别是 DAG lifecycle 调用矩阵和 terminal replay
  AST hash 的旧期望。本提交不把它们记成 Budget 回归，也不把 60 项冒充整批
  PASS；结构基线按独立提交审计修复。
- 该结果只证明 unsupported action 不再被静默按 BLOCK 执行，并确认现有
  concurrency 是 Lease/child reservation 容量；真正预算例外、降级图、
  `graph_revision + 1`、Review/restart/rollback 仍未实现，NE-01 不升级。

### NE-01 已合并结构 authority 基线纠偏

- 初始全体 30 个 unit structure 文件暴露 `109 passed / 11 failed`。11 项都
  先与 `origin/dev` 源和引入 commit 对照：9 项是 atomic Review denial、
  Plan writer effect/checkpoint、registry 注入及 Store MRO 已变但期望未同步；
  2 项是 Retry writer 827 行和 Operation checkpoint owner 267 行真实越界。
- 结构期望改为冻结当前 exact authority 或相对 owner 顺序；Review denial
  三次 lifecycle 写只有在单一 settlement UoW 与独立 route-specific contract
  存在时才从矩阵删除。parallel checkpoint Budget terminal hash 绑定已验证
  `PARALLEL_CHECKPOINT_BUDGET_REJECTED`，不是盲改。
- 真实越界通过纯模块拆分处理：Retry writer 751 行、identity 86 行、facade
  65 行；Operation checkpoint owner 206 行、evidence validator 82 行。
  新模块均无 connection/transaction，阈值与 allowlist 没有增加。
- 最终全体结构集合 `120 passed in 11.09s`；Retry/Operation checkpoint 相关
  功能与合同 `110 passed in 74.16s`；原子 Review/checkpoint 集合
  `39 passed in 6.56s`，文档/交付合同 `79 passed in 42.49s`，架构 Gate
  1～14 与统一 static/doc Gate 通过。各组有重叠风险，不累加为伪总数；NE-01
  wave integration、production canary、真实 provider 和 NE-07 仍未运行。

### NE-02 D20c 外部 transport identity

- 生产 Vault-backed provider adapter 以 server-owned base-url resolver 计算
  脱敏 transport identity、外部 HTTPS 决策和实际调用时延；原始 URL 与凭据
  不进入 ModelAttempt audit。
- canonical terminal audit 保留 provider latency、transport digest、
  `transport_external` 以及 tool/token/cost measurement；D20c result 与审计
  重开值不一致时失败关闭。
- localhost、`.local`、单标签/内部域名、非全局 IP、CLI/未知 transport 和历史无 identity
  receipt 不得 `benefit_eligible`。
- 受影响生产 adapter、ModelAttempt、Research contract/codec、D20c adapter、
  paired runner、历史 V1 降级重开与真实 SQLite audit 回归为
  `60 passed in 26.89s`；无
  migration。统一 `static_scan,doc_gate` 为 `PASS / failures=[]`
  （108.4 秒）；当前环境未安装 Ruff，记为
  `NOT_RUN_TOOL_UNAVAILABLE`。本组使用受控 transport，不是外部
  provider、24 项 cohort 或收益 PASS，状态仍为
  `ACCEPTANCE_PENDING_EXTERNAL`。

### NE-02 Research 固定 cohort 统计协议

- `tests/unit/test_d20_research_benefit_cohort.py` 定向结果为 `9 passed in
  0.62s`。
- 正向用例固定 24 个唯一 paired workload、三个主题、本地/Web 来源，
  验证输入倒序不改变 report，并要求均值提升、paired bootstrap 下界、
  citation、含 hidden verifier 的成本与 p95 时延全部通过。
- 负向用例覆盖 23 项 cohort、单主题、缺 Web、terminal/response digest
  漂移、verifier 与 arm 共用 execution、未 attested verifier、非 owner
  randomized 顺序、质量/严重错误/成本/时延门槛失败。
- 本组只验证纯合同与确定性统计器；append-only cohort repository、真实
  provider、24 项运行与 promotion 均为 `NOT_RUN`，不得提升收益状态。

### NE-02 Pentest capability-derived successor

- `test_gr01_pentest_planning_search_adapter.py`、
  `test_gr01_pentest_virtual_planning_world.py`、
  `test_pentest_agent_loop_contracts.py` 与
  `test_pentest_strategic_planning.py` 合计 `38 passed in 7.86s`。
- L1 必须把缺少当前 capability 的 policy 动作放入 successor-only catalog，
  而不是 executable roots；纯 transition 提供 capability 前不得展开。
- successor catalog 的 digest/count 必须进入 durable shadow prediction；
  descriptor 不含 args、executor 或 callback，真实选择仍是 L1/L2 baseline。
- 本组未运行 30 次授权实验，runtime outcome 仍为
  `UNVERIFIED_RUNTIME_RECORD`；安全、收益和 promotion 均不得标记通过。

### NE-02 Code 固定 20 项收益协议

- `test_ne02_code_benefit_cohort.py` 与 D18/D19 受影响合同合计
  `15 passed in 4.96s`。
- 正向用例分别覆盖 pass-rate 提升 10 个百分点，以及 pass-rate 非劣
  5 个百分点且含 verifier 成本降低至少 15%；evidence 倒序不改变报告。
- 每项必须绑定同 source commit/path policy、相同 provider/model/profile/
  budget、owner-randomized 两臂、独立 fresh worktree 和独立模型/hidden/static
  execution；hidden/static rate 与 mean diff-risk 不得退化。
- D19 cleanup-gated terminal 现在同时保留 PASS 与 FAIL 观察，三层状态必须
  精确一致；失败不能在统计前被丢弃。真实 D19 集成在本波 integration
  统一运行，本切片未重复执行。
- 本组没有 cohort repository/migration、外部模型或真实 20 项执行，Code
  benefit 保持 `UNPROVEN`。

### NE-02 Pentest 固定 30 次校准与跨域 proposal Gate

- `tests/unit/test_ne02_pentest_calibration_cohort.py` 定向结果为
  `7 passed in 0.40s`。
- 正向用例固定 30 个唯一 workload/run，倒序 evidence 不改变报告；每项绑定
  授权环境/scope、capability manifest、facts、policy、算法/adapter/budget、
  Task/Authorization/Verification receipt、独立 verifier 与动态 successor
  candidate IDs。
- 负向用例覆盖 29 项队列、预注册 identity 漂移、固定根 catalog 摘要、
  未授权/未验证终态、adapter/budget/verifier 漂移、校准误差以及三类安全
  违规；报告不得省略由指标必然产生的 reason code。
- 跨域 Gate 只有在 Pentest calibration 合格且 Code/Research 至少一域
  benefit report 合格时生成 proposal；content-addressed report ref 漂移直接
  拒绝。即使正向通过，`review_required=true`、
  `activation_authority=false`、`production_influence_enabled=false`。
- 本协议切片当时没有 migration/durable cohort owner；后续 0136 已补 owner。
  真实 30 次实验、真实非 Pentest 收益或 Review/激活仍未执行，Pentest L3
  保持 shadow。

### NE-02 统一 cohort 持久 authority

- `0136_planning_search_cohort_ledger.sql` 建立 Research/Code/Pentest 共用的
  append-only registration、member evidence、report、promotion evidence
  与 proposal 表，并以不可变 trigger 拒绝 UPDATE/DELETE。
- `SQLitePlanningSearchCohortAuthorityResolver` 分别重开 0107 Research
  terminal、0106 Code cleanup-gated terminal、0105 Pentest calibration；
  Pentest 还要求独立 Task/Authorization/Verification receipt authority。
- `tests/integration/test_ne02_planning_search_cohort_ledger.py` 使用一个真实文件型
  SQLite 跑完 24+20+30 共 74 个成员，证明 source rejection 不落库、完整报告、
  Review-only promotion、response-loss exact replay、重启、checksum drift 与
  append-only 篡改失败关闭。
- NE-02 最终波次集合覆盖 26 个 contract/unit/integration 文件，结果为
  `169 passed, 1 skipped in 104.80s`；skip 是 Windows symlink 权限不足，
  不计 PASS。首轮为 `165 passed / 4 failed / 1 skipped`，修正后完整重跑通过。
- 收口发布合同首轮为 `58 passed / 1 failed`；唯一失败是 Core Release
  manifest 仍固定 0135。同步为 0136 后完整复跑
  `59 passed in 8.75s`。统一 `static_scan,doc_gate` 为
  `PASS / failures=[] in 109.4s`；fresh 架构 Gate 1～14 为
  `PASS / failed_gates=[] in 25.6s`。
- 这不是 24/20/30 个真实实验。真实 provider、授权 lab、隐藏 verifier、
  Review/activation 尚未执行，状态为
  `CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_EXTERNAL`，
  `REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

### NE-03 0137 长期目标 authority

- `0137_long_term_goal_authority.sql` 建立 runtime-fenced、append-only 的
  workspace/subject-scoped goal event/head；accepted operation exact replay
  不再次进入 writer。
- World 只投影 active goal 的 typed ref/constraint，撤销或替换追加
  `SOURCE_REVOKED` expiry；Intent admission 重开 current goal identity，
  冻结后漂移失败关闭。
- 定向证据分别为 repository/World `9 passed in 7.55s`、
  Production Intent/Context `59 passed in 114.77s`、
  Obs/Security/Release `62 passed in 29.05s`，以及完整 Governed Operator
  activate→revoke→replay `1 passed in 115.13s`；这些批次不相加。
- migration 兼容组如实保留一次历史 0135 head 断言失败，限定 through-135
  catalog 后修复项与 0137 repository 复跑 `3 passed in 7.50s`。
- 全部 `test_ne03_*` 单元/集成文件一次完整运行得到
  `271 passed, 2 warnings in 332.52s`；warning 为 websockets deprecated。
- 修复历史夹具后，migration/Core Release 最终整组为
  `48 passed in 81.34s`；fresh 架构 Gate 1～14 为
  `PASS / failed_gates=[]`，且未扩大 allowlist；统一
  `static_scan,doc_gate` 最终重跑为 `PASS / failures=[] in 114.5s`。
- Universal Runtime coverage 首轮因 manifest 仍绑定 0135 而按设计
  fail-closed，结果为 `23 passed / 9 failed`。随后以 SQLite backup 复制
  既有七路线数据库，只追加应用 0136/0137，并在干净提交
  `708aa845ef68b9f7df931b2aba8c204cf6e9946a` 上 query-only 重开，未重跑
  accepted worker；该 SHA 在后续 PR 合并后不再是 `dev` 祖先，所以收据仅作
  历史校准。0140 收据再次 backup 同一 accepted snapshot 并只追加
  0138～0140；NE-05 推进 migration 后又 backup 该 snapshot，只追加 0141，
  并在干净实现提交
  `6f78e25c24a2bc148aa8a0a12016558a6e492a7a` 上 query-only 重开；七路线
  仍均为 `PASS`，receipt digest 为
  `sha256:f42f5fef85fd754daf8e523de6479f09ec94373118f9401caa9a53a46c980986`。
- 部署多实例 canary、Windows graceful stop 与 NE-07 真实服务栈尚未运行，
  `REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

### NE-06 Feedback、Knowledge 与 Identity V2

- migration `0142_evolution_activation_authority.sql` 从 fresh 0001 连续应用
  成功；0110 V1 mutation trigger、V2 append-only 表和 active projection UDF
  guard 均已验证。
- 波次去重命令覆盖 V2 activation、V1 read-only、Input/Prefrontal/Scheduler、
  Persona 围栏、Identity release、migration/Core Release 与 NE-05 回归，
  得到 `199 passed in 73.74s`。
- 首次架构 Gate 如实发现 Knowledge reader 的 application→app 反向依赖和
  Prefrontal 1,985 行超限；SemanticHit/Result 迁入中立 contract、纯运行策略
  拆分后，重叠定向集合为 `51 passed in 28.16s`，完整 Gate 1～14 为
  `PASS / failed_gates=[]`，没有修改阈值或扩大白名单。
- Persona growth static scan 返回零违规；`compileall`、JSON 与
  `git diff --check` 通过。
- 0142 实现提交落定后，accepted 七路线 snapshot 经 SQLite backup 复制并
  只追加 0142，在 `08a172da662df11d34fb42ea6e25a651f84da141` 上
  query-only 重开；七路线全部 `PASS`，receipt digest 为
  `sha256:161b860fd67d47945c44ff403304742c5619bc6a590cd76dd12b119e1e29f15f`，
  accepted worker 没有重跑。
- 收据落定后的最终重叠回归为 `53 passed in 45.10s`；架构 Gate 1～14、
  统一 `static_scan,doc_gate`、`compileall`、JSON 与 diff check 全部通过。
- 证据证明 proposal→真实 Lab/Review→version apply→exact rollback、
  Knowledge conflict/TTL/DecisionRecord/shadow cutover，以及 Identity
  no-change/重大差异阻断/active owner/exact rollback。
- 真实 Docker/Host/Obs/Monitor/Edge/Gateway、Knowledge ENFORCE、真实 release
  pair、外部模型与硬件尚未执行，结论为
  `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07`；
  `REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
