受众：MASTER
标题：Natsume AI 当前代码架构事实

# Natsume AI 当前代码架构事实

版本：v5.77
核对日期：2026-07-28
合并基线：`origin/dev@fcb8997fa`；当前事实以本文所在提交的工作树为准
状态：`CODE_FACTS / FULL_ARCHITECTURE_INCOMPLETE`

本文只回答“代码现在是什么”。目标、执行顺序和历史结论分别由 [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)、[NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 与 [历史程序目录](../../_history/programs/2026-07/README.md) 承担。若本文与代码冲突，以生产代码和生产 composition 为准。

> 2026-07-28 口径纠偏：当前活动波为 `NE-08 / DL-06_REMAINING_COMPATIBILITY_PROJECTION_AUDIT_AND_RUNTIME_USE_METERING`。
> 0143 Life kernel 已将 typed observation、appraisal、mixed self-affect、
> episode、mood、need 与 regulation fold 纳入既有 command/event/CAS/replay
> 链；ASGI 的 Input composition 现会构造 app-local `AffectFusionService`，把
> 文本、自述、声学或视觉的**用户观察**写为 workspace/person/session-scoped aggregate 的
> append-only command，再以有界 appraisal 生成七海自身的 `CARE/CONCERN`
> 等 self-affect，绝不镜像用户 VAD。只有带内部 provenance marker 的 fused
> context 能进入纯 normalize bridge，外部伪造的 affective context 会被忽略。
> DL-03 进一步把 `RelationshipEpisodeV1`、`RelationalAftereffectV1` 和
> `RelationshipStateV2` 纳入相同 Life event/CAS/replay authority：已接受的
> reply 才能开 provisional episode，下一次用户输入以 opaque digest 形成 aftereffect，
> relation aggregate 以 workspace + subject 连续而不把 session 当长期身份。边界、
> 未接住和修复状态会收紧 Surface 话术；沉默、投递失败和单次道歉不会自动修复。
> `HostBondRuntime` 的生产默认已关闭积分自动阶段升级。DL-06 Scheduler
> policy 已要求 canonical relationship 的 `ALLOWED` 主动权限、无未结 episode/repair，
> 且七海自身未选择给空间，才会从 bounded canonical self-affect 生成无回应压力的
> 主动 outbox；旧 Bond/legacy affect 和三条固定模板不再拥有该 route 的发送权。已接受
> foreground reply 会将 `CompanionProactiveTargetV1` 与 provisional episode 原子写入同一
> relationship command；只读 registry 仅返回 scope-valid、`ALLOWED`、无 repair/active
> episode 的 target，Scheduler 再按 frozen Scene scope 确定性选择一个 aggregate。旧
> `last_target` 不能授权发送。`proactive.gateway` 的完成 receipt 会同 canonical outbox 的
> id/key/payload/authorization digest 与私有 scope-bound target context 一起重验；只有全部一致
> 才生成 deterministic `relationship_proactive_delivered` command 并打开 `PROACTIVE_MESSAGE`
> episode。receipt 后进程崩溃时，Host lifespan 与 outbox tick 只读扫描 completed receipt 并
> exact-replay relation command，不会重复调用 Gateway transport；该回执只表示 Host/Gateway
> accepted，不能推导第三方平台送达或用户阅读。
> DL-06 已新增 `CompanionPermissionIngress` 与
> `POST /v1/digital-life/relationship/proactive-permission`：它只从 signed Secure Ingress 接收
> strict `CompanionProactivePermissionRequestV1`，要求 scope `companion:contact:write`、
> `principal_kind=user`、`principal_id=relationship_subject_id` 与 `tenant_id=workspace_id`；
> audit ref 才会作为 `USER_EXPLICIT` 的 canonical evidence，重复/重启只重开同一 Life command。
> 它没有自然语言解析、模型或 Bond 旁路，也尚无 Monitor 端用户控件。0147 新增的
> `CompanionProfileLifecycleService` 同样只通过 Life command 写入：它从 canonical relation
> aftereffect chain 重开五条独立、跨至少三天、`HELPFUL`、boundary-safe 且 `>=0.85` 的证据，
> 才能建立五互动 canary；第五条合格 canary 后效应会自动 activate，并只能 exact rollback 到候选创建时绑定的旧 profile。
> profile 仅含低风险表达偏好和 opaque refs；其 ID/digest/revision 会进入下一次冻结的文本、
> Voice 与 Avatar plan，不能修改 Soul、consent 或 self-affect。生产文本 classifier 的 `0.72`
> 不满足门槛，不能被当成自动成长成功。
> `HostEmotionRuntime`、Limbic、Endocrine 与 global BodySense 的生产写入已逐项切断，Motive 也已降为无模块级缓存的纯规则投影，production
> Bond queue/writer 已退役为 no-write adapter；Life projection 的 post-process 也不再发布旧 Tribe 对话结束事件或直接推 VTS/整段 TTS。production Host 也不会从旧 action log 预热 legacy emotion timeline：它返回显式 retired 空投影，且 Runtime snapshot 不会把 user profile 升格为 self-affect，canonical Limbic/Prefrontal 不会读取该投影；但 Surface 的其它兼容 state、Monitor 审核、canary 长期收益、真实模型和三模态物理服务仍未闭环。因此状态仍为
> `EMOTIONAL_DIGITAL_LIFE=PARTIAL`，不得宣称生产验收完成。

> **DL-02 ASR 情绪语义边界：** final ASR 仍可进入标准 Host Input，但 Edge 附带的关键词
> emotion、intensity、context 或 observation list 不会被映射为 ChatRequest self-report 字段；
> 不可信字段被清除，原文本仅走既有低置信 TEXT observer。只有显式可用、未降级、tag/强度/置信度
> 完整的声学 attestation 才会生成一条 5 秒 TTL typed ACOUSTIC observation。它经过 Fusion/Appraisal
> 后仍只是用户观察，不直接写七海 self-affect；本地定向测试通过，远端 Edge 未启动。

> 同一 canonical composition 还退役了 Limbic 的 EMOTION_INFERENCE_UPDATED legacy consumer：
> 即使收到完整的声学 event，它也不创建 TribeStateRegistry voice-emotion 项、更不追加旧 timeline。
> final ASR 只能经标准 Host Input 产生 scoped user observation；离线 compatibility 测试保留的
> 历史行为不属于 production authority。

> **DL-06 旧主动路径退役：** `proactive_talk` 已不再注册为 Personality Skill、Tool Catalog、executor 或
> Scheduler-add task type。Host scheduler composition 对升级前存在的 `proactive_talk_periodic` durable definition
> 使用 CAS disable；若 occurrence 已在升级前被接纳，dispatch 仍会验证 immutable Scene identity，但随后仅返回
> `LEGACY_PROACTIVE_TALK_RETIRED`，不触碰 Bond、EmotionTimeline、Limbic、旧模板或 delivery intent。production
> 主动消息只可由 canonical `life_tick → companion_proactive → Life outbox` 产生；本地退役证明不等于真实
> Gateway/Edge、长期体验或 DL-06 封板。

> **DL-06 Runtime snapshot reader 修订：** production `LifecycleRuntimeApplicationPorts` 已显式带入
> `LifeSnapshotReader`。已组成 Host 的 OBS runtime snapshot 只读取当前 relationship subject 的一条 canonical
> Life head，并分别暴露 `self_id=natsume_ai` 的 self-affect 与 bounded user-affect observation；用户的观察不能
> 覆盖七海自身的 mixed emotion。该 port 一旦存在，reader 错误、subject/self-id drift 都只会 degraded，绝不回读
> SocialBrain/Bond、Limbic、Prefrontal 或 retired EmotionTimeline。此切片本地集合为 `67 passed in 10.65s`，不是
> Monitor、真实 provider/Edge 或 DL-07 完成。

> **DL-06 runtime snapshot 兼容计量修订：** Host composition 现在把 canonical
> Life reader 标记为必需。它缺失时不再具备 legacy fallback 资格，而是返回 degraded canonical
> snapshot；每个 Host lifespan 的 ports instance 公开一个只含固定 projection ID 和计数的
> `compatibility_projection_usage`，分别记录实际 legacy assembler 读取与被抑制的回退。该
> meter 不持久化、无用户内容且不是状态 authority；显式离线/测试 composition 的 rollback
> window 仍可使用旧 assembler 并被计数。本地 `51 passed in 9.07s` 仅覆盖这条边界，不代表
> 其它 compatibility reader、Monitor/Review、真实 provider/Edge 或 DL-07 已完成。

> **DL-06 Prefrontal 背景演化关系输入修订：** Host canonical `TribeApplicationPorts` 下的
> `PrefrontalAgent.run_evolution_cycle()` 既不会调用 `SocialBrain`/读取 Bond snapshot，也不会再调用 legacy
> `EvolutionOrchestrator`。scheduler intent 目前只有七海 persona identity，缺少 canonical relationship 必需的
> workspace + subject scope，因此固定返回 `DEFERRED / CANONICAL_RELATIONSHIP_SCOPE_REQUIRED`。canonical 不订阅
> `EVOLUTION_CYCLE_TRIGGERED`、`BOND_STAGE_CHANGED`、`LONELINESS_SIGNAL`、`IMMUNE_BLOCK`；残留直连 stage/milestone
> growth 分别返回 `LEGACY_BOND_STAGE_GROWTH_RETIRED`、`LEGACY_UNSCOPED_MILESTONE_GROWTH_RETIRED`。仅显式 legacy
> compatibility composition 可保留旧读取用于隔离回滚测试。

> **DL-06 Prefrontal session feedback 修订：** canonical `run_growth_feedback_cycle()` 也不再把
> `INTERACTION_RECORDED` 的原始文本和 legacy `session → Bond user` identity 送进
> `PersonaGrowthTracker`。它只返回 `LEGACY_SESSION_FEEDBACK_GROWTH_RETIRED` 与
> `CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED` 的 no-candidate；已接受、scope-bound aftereffect 才是
> 将来低风险表达学习的可用证据。人格 drift 仍能收紧 Governor，却不能在 canonical 路线直写旧
> Limbic/Endocrine/PainReward/Interoception 或发出 `HUMOR_LANDED`/`TASK_SUCCESS` 旧情绪事件。
> 这不等于 Persona/Identity activation 或长期 canary 已完成。

> **DL-06 无范围涌现成长退役修订：** `emergent_growth_weekly` 已从自动调度中撤出。canonical
> `PrefrontalAgent` 在解析 model port 前检查 Life authority，并对无 exact workspace/relationship
> subject 与 accepted-aftereffect 的旧入口返回
> `DEFERRED / CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED`；它不会读取 episodic 文本、调用 LLM 或 stage
> Persona proposal。状态查询固定为 `RETIRED / LEGACY_EMERGENT_GROWTH_RETIRED`。legacy typed-model
> handler 只保留在隔离 rollback composition，不能作为在线人格成长完成证据。

> **DL-06 退役进度回复边界：** Gateway progress fastpath 已被 ReplyEnvelope 主链取代。回滚窗口内的
> `input_runtime.progress` 不接受 ingress `bond_summary` 或用户 emotion 来决定可见措辞，固定为
> `FRIEND / CALM`，且不存在 SocialBrain/Bond fallback。无生产 import 的旧
> `app/input_service_ack.py` 已删除；`14 passed in 1.92s` 仅是本地 helper 证据。

> 0148 将 `CompanionProfileControlRequestV1` 接到 app-local Host：
> `POST /v1/digital-life/relationship/companion-profile` 通过独立的
> `host.companion-profile` Secure Ingress surface 认证 `companion:profile:write`，并再次比对
> `user principal == relationship_subject_id`、`tenant == workspace_id`。它只接受完整 bounded
> preference object，不能承载聊天正文、Soul patch、emotion、consent 或任意 Life command；lifecycle
> 仅从 canonical 强 aftereffect history 选证据并启动 CANARY，Secure Ingress audit ref 与 profile
> version 同时进入 Life journal。该路由不会直接 activation；Monitor/Review 与 legacy writer cutover
> 仍未完成。

> `CompanionFeedbackIngress` 已接入同一个 app-local Host composition：
> `POST /v1/digital-life/relationship/response-feedback` 经独立 `host.companion-feedback` surface
> 验证 `companion:feedback:write`、exact user/relationship subject 和 tenant/workspace。其 strict body
> 只能引用一个仍为 active provisional episode 的 accepted receipt，并给出四种 bounded outcome；只有
> explicit `HELPFUL` 生成 `0.95` 的 canonical aftereffect。该证据最多推进已有 companion-profile
> CANARY；只有第五条合格 canary 后效应会由 lifecycle 自动 activation，外部请求不能创建或指定 activation profile，
> 也不能写 Soul、consent、self-affect；旧关键词 classifier 与 persona
> proposal 没有该权限。

> `CompanionProfileRollbackRequestV1` 以另一条 server-owned Secure Ingress surface 接入：
> `POST /v1/digital-life/relationship/companion-profile/rollback` 要求
> `companion:profile:rollback`、exact user/relationship subject、tenant/workspace 和当前 active
> profile identity。body 不能指定历史 target；lifecycle 只读取 current profile 的 frozen predecessor。
> `companion_profile_rolled_back` command 保存 audit-derived `source_control_ref`，所以 duplicate 或
> restart 会重开同一 receipt；stale active identity、service principal、cross-scope 与 target injection
> 全部失败关闭。它不改变 Soul、consent 或 self-affect，且仍不等于 Monitor/Review 或 legacy writer
> cutover 已完成。

DL-04 已新增 immutable `AffectiveTurnPlanV1`、`CompanionActV1` 与
`ExpressionPlanV1`，并在可信 internal AffectFusion hand-off 后由纯 planner 冻结。
低歧义路径的稳定句仍先写 `affective_turn_planned` /
`surface_sentence_delivery_intent` 到 canonical Life journal，再通过 SSE 可见。新增
`ModelAttemptService.stream_with_result()` 后，明确绑定该 capability 的 provider 只能返回
typed delta，且必须以唯一 `ProviderResponse` 终止；终态 Attempt 会带模型与 transport 证据，
只有 token、completion token 与 cost 齐全时才带可计量 usage。直连 native stream 会优先使用这个协议；只有在首个 delta 前得到
`MODEL_STREAM_RESULT_UNSUPPORTED` 才允许回退旧 `stream()`，并留下
`STREAM_ATTEMPT_AUDITED_NO_FINAL_USAGE`，绝不估算 token/cost。Vault-backed openai、openai_compatible 与 deepseek 已请求 stream_options.include_usage 并转发终局 usage/identity/latency；
这只有本地 protocol/adapter 测试，缺 token/completion/cost、终局帧或 identity 时 guarded attempt 仍失败关闭，未运行真实 provider。`0145_affective_turn_delivery.sql` 只从 immutable event 派生 plan/sync/sentence
digest projection。Surface model 结果不再拥有 self-affect 写权；外部 emotion hint 也不会成为
七海的情绪。DL-05 已将同一份 `ExpressionPlanV1` 编译成成对的
`ExpressionDeliveryIntentV1`：native 稳定句在 SSE 可见前先把 Voice `SPEAK` 与 Avatar
`TRANSITION` 写入 canonical Life command，完成/打断再写 `RELEASE`/`CANCEL`。
`0146_affective_expression_delivery.sql` 从相同 immutable event 派生隐私最小化 Host protocol
head，下一轮只在 exact person/workspace/subject/session scope 读取上次已暂存 Avatar expression；
VTS compatibility transition 不再读取 legacy `_prev_emotion_tag`。`HOST_PROTOCOL_STAGED` 只表示
Host 持久化了协议，不表示远端已播放或已改变 Avatar。受治理 Chat 已有
node/attempt-bound 的逐句可见性、取消与重启去重 authority，但只覆盖已入场的冻结 vertical slice；其余路径继续使用 buffered acceptance；
`0149_task_runtime_chat_delivery.sql` 再将**已入场的冻结 Chat TaskNode**的 plan、稳定句和
`STREAM_COMPLETED/INTERRUPTED/CANCELLED` control 写入独立 Task ledger；它在 write trigger 与
application 两侧绑定 graph/revision/node/attempt/lease owner/fence/node grant、request/self/relationship/
workspace、sync 和 self-affect revision。Surface 仅在 ledger receipt 成功后生成可见句，Frozen executor/
worker 会复验同一 identity。`0150_task_runtime_chat_delivery_recovery.sql` 会在替代 attempt 写入
`PLAN_FROZEN` 的同一事务中检出前序 delivery evidence；替代 attempt 或同 attempt 重入必须进入
`TASK_CHAT_DELIVERY_RECOVERY_REQUIRED`，不会重新调用模型。它不提前写 Life、Memory、Voice、Avatar 或 transport。
普通 Chat 切流、受认证连接恢复、真实 TTS/VTS、物理 receipt 和完整恢复验收仍未完成。

### DL-04 受治理 Chat 主体分离修订

已入场 frozen Chat 的 Secure Ingress principal 与陪伴对象不再混用。`RuntimeContext v1` 冻结的
`subject_id` / workspace 是 Surface、AffectiveTurnPlan、sentence receipt 和 Acceptance effect 的唯一
陪伴 scope；Gateway service principal 仅保留为 HostCommand、lease、grant 与通道授权 audit 主体。
`surface_main_chain_native_sentence_stream` 现从不可变 `ModelBudgetGuard` 写入 exact `model_node_id`，
使 canonical `model_attempt_log` 可由最终 route acceptance 重开核验。Task Acceptance 后，用户 turn、
assistant turn、Life `foreground_completed` 和 reply delivery projection 均使用 frozen subject，不能把
`service.edge.gateway` 写进关系历史。此链有本地 SQLite production-handler integration evidence；它不代表
generic Chat、connection resume、真实 provider 或远端 Edge 完成。

### DL-04 直连个人 Chat scope 修订

`POST /v1/chat` 是本机个人 UI surface，不是 Gateway 的无签名替代入口。`direct_chat_scope.py`
在 AffectFusion、retrieval prefetch 与 Runtime Context 前用 `HOST_DEFAULT_USER_ID` 绑定唯一
relationship subject/principal，并写入 `personal:<subject>` workspace；请求体的 `person_id`、
`workspace_id` 和 nested emotion context 中的 workspace 都会失去 scope authority。该标记为
`LOCAL_PERSONAL_UNATTESTED`，不含 Secure Ingress claim/audit/tenant，不能外推为多用户认证；
Gateway 和多用户关系始终由 `/v1/host/input` 的签名身份建立。

## 1. 结论

NE-01 当前代码波次为
`CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_NE07`。当前权威收据在
`dev@08a172da` 上以 migration 0142 query-only 重开同一个 accepted
canonical SQLite，七路线全部 `PASS` 且没有重跑 worker；既有代码还通过
24 个去重的真实 SQLite、Windows 进程和 Git
Workspace 恢复场景。代码已经具备 bounded fan-out、签名 ambiguity
reconciliation、原子 terminal settlement 和 Workspace promotion/abandon
所需路径；部署 feature receipt、真实网络/provider、外部 namespace 与 Docker
仍未执行，因此 Universal Runtime 的严格生产 Gate 继续为 `INCOMPLETE`。
NE-03～NE-06 本地代码波次已合入，当前实施焦点是 NE-07，不改变完整架构
`INCOMPLETE`。

NE-07 首个预检切片已将 Neo4j、Chroma、Redis 默认镜像固定到 SHA-256
digest，并参数化 project、container、network、port 与 data root；Neo4j
不再有仓库默认密码。默认端口上已有的用户 `natsume-ext` 容器明确排除在
验收之外，独立 project `natsume-ne07-555ead6d` 的真实 Compose 配置已验证。
提交 `14f015bd` 已在隔离 project 中完成三库真实写入、容器重启、持久
读取、更新/查询和 namespace 清理；receipt digest 为
`sha256:7af6e6a0…2f71`，独立残留复核为 0，用户默认栈未重启。Host/Obs/
Nginx Monitor/Edge/Gateway 尚未启动，故状态仍为
`IN_PROGRESS_EXTERNAL_SERVICES_PASS_HOST_STACK_PENDING`。

Host/Obs/Monitor 启停现可绑定显式 acceptance runtime root；该模式下
ForceRestart/stop 不再全局扫描其它 Host。运行环境快照也已排除秘密值，只
保留安全 `sec://` 引用。Monitor UI 生产构建和 15 项 read-model test 通过，
官方 Nginx 1.31.3 已安装。首次真实启动实际拉起 Obs/Nginx，但 Host 因
Workspace HMAC 只能从被快照过滤的明文变量取得而退出；旧脚本还把退出前
短暂健康误报为 `started`。当前 Workspace composition 支持从 ignored local
secret store 解析 `HOST_AGENT_WORKSPACE_HMAC_SECRET_REF`，并拒绝双来源、
非 `sec://` 与未解析引用；启动器在 sidecar 之后要求五次连续 Host 进程、
监听及 health/readiness payload identity，同时复核 Obs/Monitor 端点。
修复尚待干净提交实栈重跑，8 项 npm audit 告警也仍开放，因此这里只记
`FIRST_REAL_START_FAILED / FIX_CODE_COMPLETE / RERUN_PENDING`。

`b6b341be` 上的第二次真实启动已证明 Workspace ref 解析成功，新增稳定性
Gate 也阻止了假 `started`；它同时表明 Service Catalog 的五阶段 rollout
会暂时提供并主动停止中间 Host candidate。启动器过去会在第一个通用 health
响应后过早启动 sidecar。当前初始探针和最终连续探针都要求响应头匹配目标
catalog stage、`host.main` owner、operation digest 与 snapshot digest，
并删除 readiness、metrics 和仅监听端口兜底。第三次真实启动证明该 Gate
不会接受中间 candidate；三个 AUDIT rollout operation 均在 canonical SQLite
中收口为 `ROLLED_BACK`，没有 active head，coordinator lease 已释放。

第三次启动的实际失败原因是 acceptance
`HOST_AGENT_WORKSPACE_MANAGED_ROOT` 位于当前源码 worktree 内。Workspace
allocator 继续拒绝 managed root 与 source repository 同目录或互为父子目录，
没有为验收放宽生产隔离。`start_host_side.ps1` 现接受独立
`AgentWorkspaceRoot`，在启动 Docker/Host 前规范化并验证父/子/同目录重叠，
合法外部根才进入进程环境和 secret-safe runtime snapshot；启动摘要也返回该
路径。该代码定向合同为 `23 passed in 3.91s`。

`b56d4d78` 上的第四次启动已证明外部 Workspace root 生效；Service Catalog
仍回滚是因为验收进程把 provider 显式设为 disabled，Host health 因此正确把
Governor/LLM 两个 required dependency 标为失败。当前 public health 的
cold-start 映射只在以下机械条件同时成立时降为 `DEGRADED`：配置选择非
disabled provider、显式允许 cold start、实际 required failure 行数与 summary
一致，且每一行 reason 都是 `SERVICE_UNAVAILABLE_COLD_START`。原 service
row 和 required count 不会被改成可用；任何本地或混合 required failure 以及
Guardian durability failure 仍为 `FAILED`。受影响合同为
`41 passed in 25.17s`。

`f48afe1b` 上的第五次真实 Host 进程启动使 canonical Service Catalog 的
AUDIT→ENFORCE 五阶段全部 `STARTED`，active head 为 ENFORCE；Host、Obs、
Nginx Monitor 与真实 metrics/static 代理均为 200，三个 Docker 容器保持
healthy。Host status 为 `DEGRADED` 的唯一原因是缺 OPENAI 凭据，原 LLM row
仍 DOWN、required count=1、Guardian durability 为 READY。

但 Obs health 的深入核对发现 projection daemon 不是健康状态：
`life-evolution` 对缺失 canonical Life snapshot 以 cursor 0 读取，却把 wall
clock 写入 payload；首次 digest 已绑定 cursor 0 后，后续每轮 payload 变化，
被 Projection Store 单调门禁作为 cursor conflict 拒绝。当前 loader 对空源
固定 `generated_at_ms=0`，真实源仍绑定 `updated_at_ms`，同 cursor 可 exact
replay；`29 passed in 23.25s`。因此这次只关闭 Host ENFORCE 首启，不关闭
Obs 或本地整栈，须重启确认 daemon 恢复。

首启过程中还验证出 secret-safe runtime snapshot 过去会排除 Neo4j password
但保留 username，复用时构成无效的半套 direct credential。当前 writer 将两
项 direct auth 都排除，只持久化 `sec://` auth ref；定向合同
`18 passed in 3.82s`。新 snapshot 的真实重启、Operator 认证 read model 与
无网络拦截 Playwright 尚未执行。

第六次真实 `ForceRestart` 已证明 snapshot direct-auth 修复不再构成前置
阻断，但发现 Host crash takeover 仍有缺口：被强杀的旧进程不能释放
30 秒 `host_embodiment_runtime_lease`，replacement candidate 单次 acquisition
失败后退出，0113 ENFORCE operation 以
`SERVICE_CATALOG_START_CALLBACK_FAILED / ACTIVATION_FAILED` durable
收口；旧 Lease 随后只靠 TTL 自然过期。启动器未感知 launcher 已退出，继续
等待约 240 秒，且此前 Obs/Nginx 仍监听。

当前代码新增有界 takeover builder：只重试精确
`EMBODIMENT_RUNTIME_AUTHORITY_UNAVAILABLE`，默认 35 秒且配置上限 40 秒；
到期后 repository 必须发放更高 fence，仍有 heartbeat 的 live owner 会继续
阻断 replacement。其他配置、存储和 Edge rebind 错误保持立即失败。Host
启动器的 `ForceRestart` 先按显式 runtime root/端口停止旧 Obs/Nginx，再
启动 replacement；首次 catalog 等待同时监视 launcher PID，退出即返回
失败而非耗尽总超时。authority、配置、隔离和 PowerShell 编排定向集合为
`33 passed in 34.32s`。真实更高 fence 接管、Obs 修复和 Monitor 交互仍待
实栈重跑，故不能将这段代码事实提升为恢复 `PASS`。

当前系统已经建立较完整的持久化、授权、事件、模型、设备和执行地基，但还不是完整的下一代架构。Universal Runtime 的机器 Gate 已扩展到七条生产路线；Chat、当前启用 Skill、`CAPABILITY_SEARCH_V1` Plan 与只读 `pentest_agent` Delegate 为 `COMPLETE`，Scheduler/Internal/Operator 为 `INCOMPLETE`。

`HostCommandRecoveryInventoryMixin` 现在只把 actionable `BLOCKED` 图交给
Host startup/periodic continuation。SQL 在同一 snapshot 中要求至少存在
`READY`、Review、retry、执行中或取消请求节点；已由 Budget/Failure
settlement 收口且仅剩 FAILED/ACCEPTED/结构性 BLOCKED 节点的 admission
不会再次出现。真实 spawn 矩阵已覆盖 retry+failure、Budget 与 Review
拒绝：进程恢复均不 resolve/调用 worker、只产生一个外层 terminal，最终
重启清单为空。该过滤不改变 canonical Graph 状态或其它 reconciliation
owner 的读模型。

`DurableDagSchedulingRuntime` 当前对 committed checkpoint re-entry 采用与
普通 fan-out 相同的终态归并：非 `done` 进度继续向外流，节点 `done` 被缓冲
并转换成 `_ParallelNodeOutcome`，再由 `ParallelSettlementRuntime` 决定
恢复阻断、失败、Review 或继续；只有单节点、明确 `RECOVERY_REQUIRED` 或
最终原子 Graph 结果会成为调用者终态。这修复了账本已完成却因 Host 收到两个
`done` 而拒绝 startup continuation 的缺陷。

真实 Windows spawn/SQLite 现已证明 cancellation-bound COMMIT/RELEASE
handler crash exact replay，以及 `left ACCEPTED + right RESERVED` 并发崩溃
后 live defer、expired hold、签名 REACQUIRE、只执行 right/join 和 terminal
零 worker。该证据通过显式启用测试组合获得；生产 feature receipt 尚不存在，
因此 bounded fan-out 与 cancellation 仍不启用。

Operation reconciliation 当前新增了真实处理进程崩溃证据，而不是新的执行
authority。checkpoint `COMMIT + SIDE_EFFECT_CONFIRMED` 与无 cancellation
intent 的 raw-failure `RELEASE + NO_SIDE_EFFECT` 都先由既有目标
Operation/Outbox UoW 改变 durable state；若 Accepted handler 在返回 receipt
前硬退出，新进程从唯一 current 0131 case、append-only evidence 和已提交
proof 精确重放。COMMIT 只续接 Verification/Acceptance，RELEASE 只重开
失败终态，两者均不再次调用 worker；最终 Host survivor 为空。取消绑定组合、
自动副作用归因和部署 fan-out 仍不在此证明范围。

SchedulerClock 当前不再把一次 sweep 拆成“pending 执行”和“due 再查询”两
个可互相影响的阶段。`scheduler_clock_batch.py` 定义中立的版本化合同，
`scheduler_clock_batch_store.py` 在一个 SQLite snapshot 中读取两组行并
深冻结，`clock.py` 在第一个 worker 前重验 batch ref/capture/limit。验收
reader 会在不输出 raw payload 的前提下重开持久 admission typed contracts、
digest 和 control policy；Release evaluator 要求三条控制 surface 各有且
仅有一个有效政策。核心 45 项与最终 100 项集合均通过，clean SHA
`2a88f56e` 的同库正式收据也得到七路 PASS、三个控制 policy count 各为
1。这是一项已接线且提交绑定的局部事实，不是 Scheduler 或 NE-01 封板。

Scheduler、Internal、Operator 虽然都以 `route=delegate` 进入 canonical
worker resolver，但现在由
[`control_route_execution_policy.py`](../../../host_brain/contracts/agent_runtime/control_route_execution_policy.py)
明确建模为 control transport，而不是 child-agent Delegate。政策的
content address 覆盖 surface、control command、target、execution owner、
Review mode、Budget mode、verification mode 和 Effect mode；其 ref 同时
绑定 WorkOrder、TaskNode 和 Budget。Host、Scheduler、Internal 在 initial、
replay、Review continuation、recovery 入口重开政策，通用 Verifier、
Acceptance 与 Effect compiler 再独立重开。Operator 使用既有 Intent
admission Review，Scheduler trigger/Internal 可使用节点 Review，合同禁止
同时声明两套 Review。Review runtime 与 TaskStore 共享可信时钟。同一
Operation status version 的 unknown-side-effect recovery hold 也已收成唯一
current authority，重复登记只允许 exact replay。这关闭的是三路已启用
控制入口的执行政策漂移，不代表一般恢复、
整波 integration 或 NE-07 已完成。
纯 replay normalization 位于 contracts 层，Scheduler 不再反向 import app；
Host 的 Effect audit projection 和 control policy composition 也已拆为 focused
app 模块。两个被本切片推过 1500 行的 owner 已回落至 1480/1499 行，Gate
1～14 在未新增 allowlist 的前提下重新全部通过。

Operator 的现行生产库存是 `14 active governed / 0 blocked / 23 retired`，
不是旧的 `14/37 + 23 blocked`。active manifest、domain handler registry、
OpenAPI 和生成式 Monitor client 只公布 14 条已接通命令；23 个旧 ID 由
[`operator_command_retirement.py`](../../../host_brain/contracts/operator_command_retirement.py)
绑定替代 authority。BFF 对旧 ID 返回 410 且不触发 Secure Ingress，Monitor
也不再拥有可路由的 `commandId`。Avatar/Mate 归并 Edge，raw Tool/Hunt
回到 Governed Tool 或 Internal/Scheduler，Forge/Canon/Bio/Bond 等待
NE-05/06 proposal/version authority。后文所有 `14/37、23 blocked` 均为
历史切片口径，被本段取代；退役清理不等于 Operator 的真实网络 canary、
一般恢复或 NE-01 完成。

当前代码已把 Plan/Delegate 的 enabled-scope authority 收成显式合同。
Plan 的 search trace 与 execution policy 分离：前者解释选路，后者以
`capability-plan-policy://sha256/...` 冻结 graph/revision、原子完成、
Graph Budget digest、两条 Skill 和 final Chat 的 Registry policy、
Review 与 effect mode。`GovernedHostRuntime` 在 initial/replay/Review/
recovery 入口重开它，`acceptance_effects` 只允许 result node 编译 Effect。
Delegate 的 `governed-delegate-policy://sha256/...` 同时进入 WorkOrder
input/metadata，预算 validator、frozen executor 与 dispatcher 均重验；
当前唯一 target 必须 Review 后执行、内部用量 exact-zero、无 Effect port。
Operator/Scheduler 虽然复用 `route=delegate` 的 worker transport，但它们是
受认证控制面：仍由 app-local allowlist 固定 target，并使用有限非零预算；
它们不进入 child-agent target policy，也不能冒充 `pentest_agent`。该区分在
合同验证和 Budget authority 两层同时执行。这批代码的 Plan/Delegate
定向集合为 `89 passed`，Operator 控制面回归为 `21 passed`（集合有重叠，
不相加），真实 SQLite 四路线为
`1 passed`；
干净提交 `2a88f56e` 的同库 v3 收据已重开这些代码事实并把两条 enabled
scope 路线提升为 `COMPLETE`；不外推到其它 target 或通用搜索收益。

Skill 的生产执行面现在先经过一个穷举、失败关闭的 policy partition。生产
canonical Host Registry 共 93 个 Skill：28 个 inline read-only、13 个 pre-Acceptance
proposal-only、3 个 post-Acceptance Workspace，以及 49 个等待 typed
post-Acceptance Effect 的 catalog-only 能力。只有前三类 44 个进入
manifest、PlanningSearch、Scheduler 自动调度和 Host admission；最后一类
即使已存在于旧 durable graph，也会在 frozen executor 再次拒绝。每个
declarative built-in 都绑定 exact `skill_id/tool/action`，新增、缺项或描述
漂移不能自动获得执行权。

旧 Tool/Scheduler catalog 使用的独立 Personality Registry 还有 19 个
metadata：5 个明确 blocked，14 个没有经过本轮分类而保持
`UNCLASSIFIED`；它的 governed manifest 为 0。动态 MCP Skill 也明确
blocked。二者可供只读目录兼容，但不能进入 Host Skill admission 或自动
调度。

所有启用 Skill 都消费 canonical Task precharge；普通只读能力不取得模型
端口，Agent 模型能力使用 Task-bound measurement guard。proposal-only
能力只能返回候选/staging artifact；三条 risk-4 写 Agent 只通过 protected
Workspace stage 和 Acceptance 后 promotion 产生持久写入。包含用户可见
Domain lifecycle、外部投递、系统/文件 mutation，或会持久化浏览器
profile/session 的能力均被归入 blocked 集合。该代码控制已有本地定向证据，
且干净提交 `c68850f8` 的新 v3 同库收据已将当前 44 个启用 Skill 的 Budget
与 transactional Effect 控制提升为 `PASS`，所以启用范围的 Skill 路线为
`COMPLETE`；49 个 blocked 能力没有执行权，也不算已交付。

Skill 的生产执行 owner 已从 Executive 兼容层迁到 app-local
`governed_skill_execution.py`。代码仓中 `SkillMeta.handler`、
`bind_validated_skill_authority` 与 `dispatch_executor_payload` 的 Skill
调用者各只有这一个模块；它要求 exact governed context、verified node
grant、durable ledger 和显式 ports 后才接触 registry。旧 Executive executor
已删除，`SkillLoader` 与 Scheduler metadata 分支只能返回
`GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED`，不能消费 token、调用 handler 或
进入 Broker；canonical receipt 写失败会向上抛出。干净提交
`3d68bd1eca69ad1593454634c2999d07b4d4f2f5` 上的 `209 passed`、真实
GR05、static/doc 与元数据 Gate 已将该单项控制提升为 `PASS`；本轮新增的
预算/副作用 policy 又由 `c68850f8` 同库收据正式复验，路线当前启用范围
已升级为 `COMPLETE`。

Chat 与 Plan 合成的模型预算校准现在使用真实生产等价调用链。GR05 构造真实 prompt registry、route policy、`ModelAttemptService`、ledger audit 与 Surface ports，只控制 terminal provider transport；两个新请求各自生成 canonical `started → succeeded` Attempt，provider token/cost measurement 进入不可变 WorkerProjection，再以稳定 usage id 同时落入 Graph parent 与 Attempt-bound child Budget。exact replay 只重开既有结果，不再次调用 provider。只读验收会关联这四组事实并拒绝缓存、孤儿 Attempt、身份和测量漂移。

该校准修复了两个运行时错误。`surface.post_process` 构造 Life trace 时过滤内部 `governed_model_budget`，避免把执行 guard 当成可持久业务数据；`GovernedFrozenNodeWorker` 只在 executor 未提供 path 时写 fallback，保留 Surface COMMIT 与 durable projection 的同一来源身份。干净提交 `3d68bd1e` 的正式同库收据已验证该受控 terminal transport 治理链，Chat budget 和路线状态分别为 `PASS / COMPLETE`；真实外部 provider 质量尚未运行。

Universal Runtime 现在有一套独立的提交绑定判定器和七路正式 PASS 收据。Persistence reader 以 SQLite URI `mode=ro` 和 `query_only=ON` 在单一事务重开运行事实；Release builder 将事实约束为 `natsume.universal-runtime-route-acceptance.v3`，绑定干净 Git HEAD、仓库与数据库 migration head，并逐节点核对 Attempt/Lease/Operation、父子 Budget usage lineage、Verification/Acceptance、Artifact/WorkerProjection、Effect/Outbox/Receipt。输出只保留 bounded identifier、状态、版本和 digest；原始 Command/WorkOrder/Graph/Effect payload、用户正文和模型输出不能进入收据。

当前本地验收 composition 已覆盖七路：GR05 共同运行 Chat、Direct Skill、reviewed Delegate 与多节点 Plan；`tools/acceptance/universal_remaining_routes.py` 对同一数据库调用真实 Scheduler、Internal 和 Operator application boundaries。Scheduler Effect 在节点验收后执行，Internal result commit 由正式 handler 持久化，Operator 经过 durable Review continuation 后才切换 HardState，并由 outer outcome 完成 Secure Ingress claim。干净提交 `2a88f56e2dc7c68d7f37100c6c2d408bc2fdf6a0` 的串联运行严格重开 admission digest 和三个 control policy 后得到七路 PASS；Chat、Skill 和当前启用 Plan/Delegate 范围均为 `COMPLETE`；Scheduler/Internal/Operator 仍因其它控制部分完成而保持 `INCOMPLETE`。

Review 拒绝现在有独立持久终态。0135 的 append-only settlement 精确绑定 terminal Review 状态/版本/digest、Graph revision、Node、graph Budget、三条 Outbox，以及 POST Review 的 0134 Acceptance、`SUCCEEDED` Attempt 和可选已有多节点 `BLOCKED` decision。PRE Review 仅在零 Attempt 时原子取消 Node/Graph 并释放 Budget；POST Review 原子失败 Node/Graph 并释放 Budget。Scheduler 或 Review continuation 在提交响应丢失后只重开同一 settlement；不会创建 Attempt、签发 Grant 或重跑 Worker。Store reader 会重新验证 Review、aggregate、Acceptance、blocking decision 和三条 Outbox，任一漂移失败关闭。

Review continuation 的恢复协调现具有队列活性保护。0096 表仍只保存 delivery
coordination；候选跨表绑定损坏时，Store 在候选自己的事务中写稳定 poison
错误码、版本与退避时间，不触碰 Review/Task 状态。固定 500 行扫描不再让坏
前缀永久遮挡后续健康行。`review_continuation_poison.py` 只接收现有连接并生成
隐私有界投影，不拥有连接或第二套业务 authority；app-local composition 把
observer 注入 worker，并让现有 runtime-scoped Guardian 持久记录单一 SLO
incident 与 replacement-process resolution。显式 Operator DLQ/修复 UI 和
真实生产 canary 仍缺，因此 Review/NE-01 总状态不升级。

节点 Acceptance 也有独立持久事实。0134 的 append-only decision 精确绑定 Graph revision、Node、Attempt、规范 `AcceptanceJudgement`、evidence、Node Outbox，以及可选 Review/Graph block lineage。自动接受由一个 TaskStore 事务共同提交 decision、TaskResult artifact、可选 Effect intents 与 `ACCEPTED` Node；非 `ACCEPT` judgement 由一个事务共同提交 decision、`WAITING_ACCEPTANCE_REVIEW` Node、pending ReviewItem 与 Outbox，直接单节点图还共同进入 `BLOCKED`。Scheduler snapshot 投影该 decision，Continuation 只按 exact node version 重开，worker 与 judge 均不重跑。pending Review SQL writer 已收敛为一个共享 owner；拒绝后的终态由 0135 唯一收口。

节点 Verification 已有独立持久事实。0133 的 append-only decision 精确绑定 Graph revision、Node、Attempt、规范 `VerificationResult`、evidence 与 Node Outbox；Node transition 和 decision 共用一个 TaskStore UoW。Committed checkpoint 在 `VERIFIED` 后重入时从 decision 取回结果，不再读取从未落库的 `node.verification_result`。直接单节点拒绝由一个事务同时写 `VERIFICATION_FAILED` Node、`FAILED` Graph、`RELEASED` graph Budget 及各自 Outbox；响应丢失走 exact replay，伪造 Graph Outbox 会使整个事务回滚。多节点拒绝只先记录节点决定，再由 0090 唯一 settlement owner 关闭 Graph/Budget。generic `transition_node()` 仍作为低层兼容 API 存在，但生产 verifier/continuation 调用点已迁移；一般 failure/cancellation 和七路线 commit-bound evidence 尚未完成。

Plan 的当前生产窄路径不再把 `SkillMeta.require_confirm=true` 的能力静默排除。`capability_plan_search` 把它冻结为 `PlanningCapability.requires_review`，domain search 将 catalog provenance 带入候选，compiler 再将其重绑定为覆盖整图实际 TaskNode ID 的 node review policy。Scheduler 逐节点检查该 policy；只有目标节点等待 Review，其前驱和后继不会共享一个图级审批布尔值。Review continuation 仍由 0096 owner 协调并由 NodeBoundGrantService 生成 claim-fenced batch；本次续跑前已 accepted 的节点从 immutable WorkerProjection 恢复原 grant，当前新执行节点必须匹配续签 grant。真实 SQLite 三节点图证明只运行一次并原子完成。机器矩阵因此把 Plan 当前启用路径的 `node_bound_grant` 与 `review_resume` 提升为 `PASS`；0132 还为每个 Attempt 建立 child Budget Reservation，但一般搜索/恢复、全部取消与释放组合、事务副作用、commit-bound acceptance 和 NE-01 仍未完成。

当前普通 Delegate 只准入 `pentest_agent` 的 `PORT_FREE_READ_ONLY` adapter。它不持有模型、工具、嵌套派发或 Effect port；dispatcher 必须产生 digest-bound `ReadonlyDelegateUsageAttestationV1`，绑定 command、graph/revision、node、attempt、node grant、target、adapter 与 invocation，并明确证明内部 token/tool/retry/model-cost 为零。Task Runtime 仍按真实外层动作预扣一次 canonical Delegate dispatch；冻结执行器不再从 `intent_agent_trace/repair_agent_trace` 推测用量。缺失、非零、篡改或 binding 漂移均在 COMMIT 前失败关闭，真实 SQLite 的 WorkerProjection 与 BudgetUsage 保存同一 attestation ref。当前启用 target 的 `budget_enforcement=PASS`，不外推到一般 Delegate、Review/restart、program-wide Budget、commit-bound acceptance 或 NE-01。

当前启用的通用 worker 不再各自伪造验收结果。`governed_worker_evidence.py` 是 Chat/Skill/Delegate/Plan/Tool/Operator 的共享独立证据 owner：Verifier 与 Acceptance 通过同一只读接口但分别重开 durable WorkerProjection，复验完整执行身份、node grant、WorkOrder/context、receipt digest、COMMIT 与输出合同；任意未知语义条件返回 `REVIEW`。Skill 的 terminal COMMIT 现在完整镜像 canonical projection，不再丢失 graph/node/attempt/grant 字段。Plan 的 final Chat 从 canonical TaskResult artifacts 构造唯一输入证据集和 bundle attestation，已声明但未消费的依赖不再通过验收。当前 Delegate/Plan 的全部声明控制均为 `PASS`，两条 enabled scope 路线为 `COMPLETE`；NE-01 总状态仍为 `IN_PROGRESS`。

Chat 路线的一个真实正确性缺口已经关闭：`life.conversation_turn_recorded` 与 `life.foreground_completed` 不再仅凭 Life command receipt 提交 Task Effect。生产 ASGI 把同一 `LifeProjectionDispatcher` 注入 Acceptance handlers；handler 重建并核对 exact command 的 projection-intent 全集，只领取自己的 intent，并在每个 payload-bound durable receipt 完整后生成 content-addressed barrier receipt。`surface.post_process` 的 durable mode要求 visible assistant turn 与 LifeMemory admission 成功，否则 projection 不产生 receipt，后序 `surface.reply_delivery` 也不会运行。response loss、restart、failed projection、receipt tamper 和 GR05 实际顺序均有本地 SQLite 证据。该控制项为 `transactional_side_effects=PASS`，但 external delivery 仍为 eventual，`commit_bound_acceptance=MISSING`，所以 Chat 与 NE-01 没有完成。

Direct Agent Skill 写入不再停在 proposal-only。`agent_worker`、`agent_self_heal`、`agent_self_evolve` 的 Host admission 从 app-local SkillRegistry 冻结 risk 4 与 durable Review policy；frozen worker 必须重开唯一已消费的 task/node-bound Review。兼容 payload 中的 V3、Graph、Workspace、Grant、Budget、Review、Lease/fence authority 会被拒绝，`governed_skill_work_order.py` 从已验证 Task context 重建 WorkOrderV3。sealed broker dispatch 才能给 `codex_runtime` 注入同一 app-local ArtifactStore/WorkspaceRuntime。成功写结果先成为 protected stage 和 immutable WorkerProjection proposal，Graph Acceptance 再编译先于 visible Effects 的 `agent.workspace_artifact_promote`；失败写结果不得生成 Effect，但必须在 immutable projection 中绑定同一 stage descriptor 与公开结果 digest。route-specific terminal reconciler 重开 admission、Task/Lease/Operation/projection/Verification 后精确 abandon，通用 lifecycle cleaner 再跨 runtime 清理。真实 SQLite+Git 已证明 source `main` 不动、成功 cleanup、Verification failure cleanup 与再次重启零重放。下一段记录受限 Plan single-writer success/downstream-failure/downstream-cancellation；其它 terminal matrix、Domain Pack、其它 Skill、一般恢复与强租户隔离仍未完成，NE-01 仍是 `IN_PROGRESS`。

Plan 内 write node 已从上述缺口中关闭一个受限生产切片：
`CAPABILITY_SEARCH_V1` 允许至多一个 reviewed risk-4 Agent Skill 作为非结果
Workspace owner。worker 只生成密封 proposal；整图 Acceptance 才在
parallel-success UoW 中同时暂存 node-owned promotion 与 result-node
Effects。成功 handler 使用派生 node operation fingerprint 和 exact
Action Preview 晋升 accepted HEAD；下游 Verification failure 则要求
reconciler 与 repository 重开唯一 Acceptance/artifact/Operation/projection
并证明 effect count 为零后才 abandon/cleanup。真实 SQLite+Git 已覆盖两条
路径、源分支不变与第二次重启零重放。多个写 owner、其它
failure/cancellation/unknown-effect 组合及强隔离仍未完成。

当前连续 migration head 为 `0146_affective_expression_delivery.sql`。0132～0135 继续持有 Task Budget、Verification、Acceptance 与 Review denial authority；0136 独立保存 PlanningSearch cohort evidence/proposal；0137 独立保存 runtime-fenced 长期目标 event/head；0138～0140 独立保存 proposal-only trusted Lab、redaction 与 sanitized dataset；0141～0142 保存 Skill/version activation authority；0143～0145 保存七海的 affect、关系与文本 delivery projection；0146 保存 Voice/Avatar Host protocol projection，均不改变 Task execution authority。

无 cancellation intent 的 signed `RELEASE + NO_SIDE_EFFECT` 已从单节点整图终态扩展为通用节点终态。`TaskOperationFailureReconciliationStoreMixin` 的第一事务只释放 Operation、失败目标 Attempt/Node、过期原 Lease并写四条 Outbox；只有单节点兼容路径在同一事务关闭 Graph/Budget。多节点 Graph/Budget 保持 ACTIVE，重启后的 `DurableDagSchedulingRuntime` 先重建 failed outcome，再让现有 0090 writer 原子提交 Graph/Budget 失败终态。Acceptance v4 receipt 不摘要会变化的 Graph settlement 字段，因此 `PENDING→COMMITTED` 后仍精确重放；0090 decision 或 Graph/Budget Outbox 漂移会失败关闭。双根、并发额度1的真实 SQLite 测试证明 READY sibling 未执行；并发额度2的双失败测试证明非 selected 失败节点仍由同一 decision outcome 精确覆盖并可重开。该链不自动判定副作用，也不覆盖其它 failure/cancellation。

Scheduler 现在有一个统一的生产写边界。`/scheduler` 与 `/host-scheduler` 下的 create/update/delete/enable/disable 不再调用 route-local command writer，而是取得 app-local Scheduler 管理 runtime，冻结 method/path/body、definition head 和 Secure Ingress claim，生成 `HostCommand(surface=SCHEDULER)` 与不可变单节点 `TaskGraph`。节点经过 Attempt、Lease、node grant、Budget、Review、Verification、Acceptance 后，唯一 `operator.state_control` Acceptance Effect 才调用 canonical Scheduler repository。`scheduler_admin_operation_result` 先记录提交事实，外层 claim 随后封口；二者之间崩溃时，新实例以同一 request/operation receipt 精确恢复，不能重复变更 definition。list/get/health 保持只读，trigger 继续使用 governed occurrence。

Operator 平面的 `scheduler.task.enable/disable`、`sys_mode.auto_rules.update/event.submit` 与 `long_term_goal.activate/revoke` 沿同一 accepted effect 写各自 canonical owner；0129 mode-policy 与 0137 goal journal 的 append rollback、lease/fence、exact replay 和 tamper 边界保持。Operator 当前为 `16 active / 23 retired / 39 known / blocked=0`，Scheduler surface 不计入 Operator 数量。历史 Scheduler 批次为 `34 passed in 139.12s`；0137 的真实 governed 激活/撤销/重放另为 `1 passed in 115.13s`，两者不相加。该结论不包含部署凭据、真实 HTTP/network、完整 Host 进程重启、波次集成或 NE-07。

最新 `dev` 基线包含 #114 的 exact Internal 单节点后置审核取消恢复：Operator Secure Ingress 写入的 immutable `BLOCKED` intent 与独立 trusted `CANCELLED` final decision 必须同时重开；restart terminal worker 只在该 lineage 完整时关闭 nonacceptance claim，并在其后 tombstone/清理 Workspace。当前分支将 pre-checkpoint 子流限定为唯一 Operation 的两种互斥 Ed25519 已签名终态：`RELEASED/RELEASE/NO_SIDE_EFFECT/no receipt` 或 `COMMITTED/COMMIT/SIDE_EFFECT_CONFIRMED/durable receipt`，同时要求 WorkerProjection、Acceptance Effect 与 Agent result 均不存在；HostCommand claim 重放复用 Composition 中同一个带验签 TaskStore。无副作用 result 保持 v1 digest 兼容，confirmed-effect 使用 v2 绑定 receipt。该实现仍没有扩展为 raw worker failure、一般未知副作用、一般多节点或其它取消恢复。

当前分支又把签名 Operation reconciliation 接到真实 Operator 主链。`task_operation.reconcile` 的原始 attestation 在 admission 前写入 0126 immutable evidence ledger；WorkOrder/TaskGraph 不含签名，只保存 evidence ref/digest 与 reservation/version/action/actor。Review 批准且 Graph Acceptance 后，Accepted handler 重开完整 evidence、再次验签，以同一 Attempt、严格更高 fence、新 owner 和新 Budget execution segment 执行 `REACQUIRE`，然后调用原 route continuation。真实 SQLite 证明 worker 首次续跑一次并把 Operation 收敛为 `COMMITTED`，exact request replay 零 target re-entry；evidence drift、直接 UPDATE/DELETE 和错误签名失败关闭。该切片默认关闭，未做部署 key/HTTP canary。

同一路线现在增加了独立的 checkpoint `COMMIT` 处理器，而不是把它塞进 cancellation service。它只接受 evidence-bound canonical WorkerProjection receipt；读取层重开完整 admission 和投影，mutation 层又在同一 `BEGIN IMMEDIATE` 内重验 Graph/Node/Attempt、Operation scope/fingerprint、旧 Lease expiry 与投影行。0127 在 SQLite 边界禁止 WorkerProjection UPDATE/DELETE。确认成功只把 exact `RECOVERY_REQUIRED` Operation 推进为 `COMMITTED`、追加一个 `OPERATION_RECOVERY_COMMIT`，再调用原 route continuation；模拟首次响应丢失时可重开 terminal Operation 并继续幂等 Task terminal，worker 仍只运行一次。普通 `RELEASE` 仍要求 cancellation intent；任意 transport receipt、live Lease、取消中的 Graph 或投影漂移均拒绝。

> 当前库存纠偏：生产 locator consumer/import、全部 import-form consumer、模块、字段、锁和可变引用均为 0。`process_service_container.py`、`process_services.py`、`process_environment.py` 与旧 wiring 已物理删除；ASGI/CLI 各自构造 fresh ports 并显式传给 Host root，Host builder 无全局 fallback。`HostAuthorityRuntimeOwner` 继续 migration-first 持有 canonical ledger、Episodic/LifeMemory、独立 Model 与 Capability 图。ResourceManager status 已由 0123 Host-fenced Runtime Event journal 持久化，共享 JSON production reader/writer/fallback 为 0。Tribe shared state、Host registry、event bus 与 23-Agent catalog 也由每个 Host scope fresh 持有；bind 不发布进程级 active owner，lifespan/background execution 与 HTTP request 通过 execution-local `ContextVar` 借用 exact owner context。两个异步重叠 scope 已证明 legacy shared-state/cross-Agent getter 返回各自 Agent/registry；同 execution 跨 scope、active execution 解绑和 stale inherited context 均失败关闭。23 个 Agent backing singleton/setter 与 state/Host registry/event bus 的无上下文 fallback 已物理删除，缺 exact context 统一拒绝；完整 Host multi-app production canary 尚未执行。旧测试通过显式隔离 registry/context 兼容，fresh 生产进程不会加载 pytest bridge。外部 Chroma/Neo4j namespace、Token/MCP durable restart、其它外部资源恢复、事务化 Chat、完整 Worker/Obs Composition 与 NE-01 仍未完成。

Host 情感上下文有两层边界。生产 ASGI 的 `InputRuntimeApplicationPorts` 通过惰性 `LifeCommandService` provider 构造 `AffectFusionService`；`resolve_chat_emotion_context(..., affect_fusion_service=...)` 会直接将输入归一为 `UserAffectObservationV1`，用 `workspace_id + relationship_subject_id + session_id` 形成不含原始身份的 aggregate key，并先持久化 observation，再有界生成至多两个 `EventAppraisalV1` 候选。只有 appraisal command 才能推进 canonical `state.affective`。视觉、声学、文本/自述各自有 3/5/30 秒 TTL；journal 只接受脱敏摘要和 digest ref，原始音频、图像、文本和模型链字段会失败关闭。`normalize_chat_emotion_context()` 对内部 fused context 只读；外部伪造 marker 不会取得 canonical authority。Input 与 Tribe composition 都不会再构造或解析 `HostEmotionRuntime`；缺少 Life authority 时稳定拒绝 `CANONICAL_AFFECT_FUSION_NOT_INJECTED`。旧类仅保留给直接隔离测试，不再是任何 Host composition 的 compatibility 路径。回复形态 `ResponseFormDecider` 继续由 Input composition 独占，并发语音额度检查与记账在同一锁内完成。

最新 DL-06 切片使这个 fallback 不再进入 production Host object graph：
`ProcessApplicationPorts` 和 `build_production_host_application_ports()` 已删除
`emotion_runtime_factory` 与 runtime/profile publication。ASGI 同时把 lazy Life command provider
传给 Tribe composition；Tribe 与 Input 在有 authority 时都 resolve `AffectFusionService`，不会
解析或调用 `HostEmotionRuntime`；遗漏 authority 不再存在 local/offline runtime fallback，而是失败关闭。
Lifecycle 的 global profile consumer 现为 optional read-only compatibility slot，production 留空。
`TribeApplicationPorts` 还显式携带 affective-life authority：
canonical mode 下 Limbic 的 legacy `EmotionPersistence`、metabolism、timeline append、decay，以及
Bond、task success、humor、loneliness、immune block、interoception 和 interaction-recorded callback 统一 short-circuit 为 `RETIRED_CANONICAL_LIFE`，不得再构造或写入
第二份 self-affect；只有直接构造的隔离测试 port 才能覆盖历史行为，Host composition 不存在该 fallback。
Endocrine 的 legacy
cortisol/oxytocin delta buffer、`_last_state`、hormonal hint、callback 与 fan-out 在同一 canonical mode
也全部退役，且 Circadian/MirrorNeuron 只能走受控 compatibility method，不能直写私有字段。production
BodySense 同样不再被 Surface/startup warmup/read：`project_body_sense_from_life_snapshot()` 只读取当前
relationship subject 的 canonical Life snapshot，Limbic canonical mode 的 tick/loneliness/conversation
mutation 统一退役，且 retired tick 不发布 Bus update。`MotiveEngine` 同时移除模块级 `_engine`，factory
每次返回新建的纯规则对象；它不能存储跨 turn 的情感、关系或学习上下文。production
`HostMemoryRuntimeOwner` 现以 `RetiredEmotionTimelineProjection` 取代旧 action-log hydration；marker
不包含 `NEUTRAL` 情感事实，Lifecycle/proactive 会忽略它，canonical Limbic 与 Prefrontal 不会解析/缓存它。
这仍不等于全量 compatibility state 清零：其它 Surface/legacy reader 的使用审计、Monitor/Review 与真实验收仍待后续完成。

默认模式的私密活动 reader 已成为上述审计中第一个实际切换点：`DefaultModeAgent` 的一次 Life snapshot
通过 `project_inner_world_state()` 生成只读 `canonical_life_source=CANONICAL_LIFE_JOURNAL`、有界
`inner_self_affect_projection.v1` 与 `inner_relationship_projection.v1`。`inner_activity_agent` 看到该 marker
后直接投影 mixed self-affect/mood，而非调用 Limbic timeline；也不再从 SocialBrain/Bond 读取 summary。关系投影
不含 workspace、subject、trigger/evidence 或 intimacy consent，且不把 trust/attunement 变成 score/stage。
高连续性 default-mode tick 已移除 legacy `drive_emotion_metabolism()` 调用。这只关闭这个 reader，其他
compatibility read path 和真实服务验证仍为 `PARTIAL`。

普通 Chat 的生产 prompt reader 现也有一条显式 canonical 分支：`prompt_context_plane` 与
`interaction_facts_plane` 从内部 AffectiveFusion envelope 抽取 `RelationshipStateV2`，并严格重验
`self_id=natsume_ai`、workspace、relationship subject、session 以及 `affective_turn_context.v1 /
relationship_turn_context.v1 / relationship_state.v2`。通过后，旧 `RelationshipStateBlock` 只作为
prompt-compatible 的**非浪漫**投影：stage 固定 `FRIEND`、phase 固定 `stepsibling`，trust/safety/attunement
只能给出有界安全、开放、轻松或修复纹理；不再把 legacy Bond `SOULMATE`、score 或 consent 变成 lover、排他、
依赖或主动许可。claimed canonical context 任一字段漂移时返回
`CANONICAL_RELATIONSHIP_REJECTED` 安全朋友投影，禁止读取旧 Bond 作为 fallback。Surface packet 复用同一结果；
最终 `surface_affect_control` 也以 `source_authority` 拒绝旧 `relationship_distance`、closeness、恋人线索和
lover task 的二次升级，canonical 为 `familiar`、rupture/rejected 为 `guarded`。当前是
`LOCAL_TARGETED_179_PASSED_PLUS_FINAL_GATE_82_PASSED / EMOTIONAL_DIGITAL_LIFE_PARTIAL`，不是 DL-06 封板。Cortex
retrieval prefetch 也在 canonical hand-off 存在时跳过 legacy state arbiter，向 retrieval/Motive 只传
`FRIEND / stepsibling / familiar|guarded` 的无分数 projection；scope drift 固定 guarded（定向 `34 passed in 5.64s`）。

**Bond queue 修订：** production `ProcessApplicationPorts` 已删除 `BondDynamicsWriter` 与
`BondUpdateQueue` construction factory。production `HostBondRuntimeOwner` 只保留 stage/graph 的
read compatibility，并返回没有 worker、backlog 或直接 writer fallback 的 retired adapter；canonical
Cortex 也会在解析 queue 前拒绝旧 Bond mutation。`/bond/event` 已不再解析或调用 writer：Host
route ports 物理删除 `bond_writer_provider`，端点稳定返回 `410 LEGACY_BOND_WRITE_RETIRED`。Monitor
同步删除人工补录关系事件的表单、API 和 endpoint registry 项，历史 score/stage 仅在回滚窗口内只读；
它们不能影响亲密、主动许可或七海 self-affect。旧 timeline 现在是显式 retired 空投影，而不是 canonical
projection；该公开入口退役不证明其它 compatibility reader 已清零。

Host→Edge 的旧进程 singleton 不再存在，且首条正向受治理 issuer 已接入 production composition。Obs `edge.command.push` 只接受 `edge.somatic + emotion_state_command`，由 Secure Ingress、typed Governor、风险 4 Review 和 immutable TaskGraph 生成确定性的 `edge.embodiment_enqueue` Acceptance Effect；terminal Preview 重开 Graph/Attempt/Lease/Grant/Scene/Guardian 后，Accepted handler 才把 `set_expression` 写入 app-local durable outbox。状态因此是 `FIRST_GOVERNED_SLICE_WIRED / REAL_EDGE_CANARY_MISSING`：SQLite 入队、exact replay 与只读 receipt reconciliation 已测，但真实 Edge/Gateway 网络、设备和其余 command type 未验收。

| 能力 | 当前定级 | 代码事实 |
|---|---|---|
| Core Infrastructure | `AVAILABLE` | migration、journal、outbox、model attempt、capability broker、Life、Edge、Query 等主干已存在；当前连续 migration head 为 `0146_affective_expression_delivery.sql`。0133～0135 收口 Task Verification/Acceptance/Review denial；0136 增加 proposal-only PlanningSearch cohort evidence；0137 增加长期目标 event/head；0138～0140 增加 proposal-only trusted Lab 数据链；0141～0142 增加 Skill/version activation；0143～0146 依次增加 affect、关系、文本及 Voice/Avatar Host protocol projection。这些资产不代表外部收益或整体生产验收 |
| Universal Runtime 七路线 | `CHAT + SKILL + PLAN + DELEGATE COMPLETE / THREE INCOMPLETE / SEVEN_ROUTE_GATE_ACTIVE` | v2 Gate 精确覆盖 `chat/skill/plan/delegate/scheduler/internal/operator`。`7e9b090b3` 同库收据在 migration 0140 query-only 重开，使七路 commit binding 与三控制 policy 继续通过且未重跑 accepted worker。Scheduler/Internal/Operator 仍有部署/其它控制缺口；Operator inventory 为 `16 active / 23 retired / 39 known / blocked=0` |
| Scheduler | `PARTIAL / DURABLE_CLOCK_OCCURRENCE_FENCE` | Clock/Manual occurrence 进入 HostCommand/TaskGraph 后，worker 只生成不可变 operation plan；Graph Acceptance 与 `scheduler.operation.execute` 在 TaskStore canonical UoW 原子 staging，真实 handler 由共享 app-local dispatcher 在 Acceptance 后执行，结果仍写既有 immutable Scheduler result ledger。0117 令 live DUE claim 在 admission UoW 内原子转为 occurrence-bound claim；执行前/中 renew，实际 failure clock 决定 backoff，过期 lease、第二实例与 stale fence 不能 dispatch 或收口。definition/admin authority、mutable batch freezing、通用未知效果人工归因、commit-bound 集成和 canary 未完成 |
| Internal Agent | `PARTIAL / INTERNAL_WRITE_PROMOTION_AND_SIGNED_RAW_FAILURE_CLAIM_BEFORE_CLEANUP` | 外部入口只接受未准入 v2 描述；显式 V3 或顶层 graph identity 以 `INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN` 拒绝，随后编译为 V3。受治理 frozen worker 将同一 `WorkOrderV3` 直接交给 `AgentRuntimeExecutor`、prompt、review 和 Workspace，已没有 V3→`AgentWorkOrder` 回投。V3 顶层 graph identity 与遗留 metadata 冲突时失败关闭。`artifact_store_read_model.py` 提供无写 authority 的密封清单/promotion/recovery/tombstone读取，`workspace_write_promotion.py` 密封 bounded safe-path manifest 与 binary patch并确定性生成 accepted HEAD；源 branch 不移动。`internal_startup_checkpoint_continuation.py` 提供 exact scan，`periodic_durable_continuation.py` 只托管 timer/wake/stop；`internal_agent_sealed_checkpoint_recovery.py` 仍以完整 lineage 与更高 fence 重建 projection 而不重跑 worker。composition-owned `InternalAgentCheckpointUnitOfWorkPort` 独占公开checkpoint mutation并复用WorkerProjection唯一SQL writer；A/B/C三段分别原子，response-loss只读重开且不二次mutation。终态 worker 除精确 sealed abandon/cleanup 外，只对 finalized cancellation 且符合共享中立合同两类 exact signed terminal 的未封存单节点先关闭 claim、再 cleanup/discard；失败顺序不会删除证据。一般 unknown effect、其它 crash-left/取消 closeout 与 general `settle_worker` crash bundle 仍缺；handle-level TOCTOU、OS/container isolation、canary与七路线验收也未完成 |
| Agent Skill / 单写节点 Plan Workspace | `PARTIAL / DIRECT_AND_SINGLE_PLAN_WRITER_SUCCESS_FAILURE_CANCELLATION_LOCAL_PROVEN` | 三条登记 Skill 冻结 risk 4 和 durable Review；server-bound V3 拒绝 caller authority。private `codex_runtime` 取得 app-local Artifact/Workspace ports。Direct success/failure 与 Plan 单写节点 whole-graph success/downstream failure/downstream post-review cancellation 均由 `skill_artifact_terminal_reconciliation.py`、纯只读 `workspace_terminal_cancellation_authority.py` 和 shared lifecycle cleaner 在 fresh runtime 收口；Plan 只允许一个 reviewed 非结果 owner，graph Acceptance 前 Effect 为零。取消路径还要求 finalized intent/decision 与 accepted writer 三快照同版本。真实 SQLite+Git 已证明源分支不变与零重放。其它 terminal matrix、多 Workspace、Domain Pack、其它 Skill、完整进程/network canary 与七路线验收仍缺 |
| 原生多节点 Durable TaskGraph | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / SIGNED_NODE_FAILURE_0090_LOCAL_PROVEN / PRODUCTION_FANOUT_OFF` | 0102+GR06 已把 exact NODE Effect、immutable artifact、`ACCEPTED` 与 Attempt-bound Outbox 合为 canonical 应用 Store UoW。TaskStore 的 public graph/effect staging 写入 API 已删除；生产 Graph 只能从 HostCommand admission 同一 UoW 进入，Effect 只能从 canonical Acceptance UoW 进入。0124/0125 封闭新准入。signed multi-node `NO_SIDE_EFFECT` 失败只关闭目标节点，重启后由 0090 唯一终结 Graph/Budget；READY sibling 零执行且 receipt 稳定。其它运行期 mutation、其它 general/batch/pre-budget recovery、部署 inventory/key/canary 仍缺 |
| 通用 PlanningSearch | `PARTIAL_EXPLICIT_HOST_CAPABILITY_SEARCH / D20C_ACCEPTANCE_PENDING_EXTERNAL` | 公共 L1/L2/L3 内核、不可变 Goal/VirtualStateSnapshot、纯 GoalPlanningService 与 context-owned compiler 已存在；唯一 Host Plan 入口是 registry-derived capability mode，顶层或 `args` 的 legacy `planned_steps/steps` 在上下文构造和 admission 前拒绝。D16～D20c 是 Code/Research shadow 基础资产；D20c 只将满足严格条件的 canonical 模型结果封成外部 provider attestation，尚无真实外部 cohort、独立 verifier 或 promotion 结论 |
| Host 智能候选规划 | `PARTIAL_RULED_OBJECTIVE_CAPABILITY_SEARCH_V1` | Thalamus 仅对明确的 Research/调研与 Frontend/可用性计划生成 server-owned `route=plan + args.planning_mode=CAPABILITY_SEARCH_V1`；编译器保留 `task/tag/risk`，Governor 严格绑定后才进入 app-composed `SkillRegistry`。该路径需要至少两项 host scope、免确认、风险不高于上限且标签匹配的冻结能力；L1/L2/条件 L3 在虚拟态中选择两项不同能力的有界顺序路径，选中后才重建 node 输入并编译 `skill → skill → chat` immutable TaskGraph，节点输出统一为 `receipt_ref + narrative`。显式 `failure_replan` 仅在 TaskStore 证明 predecessor 为 `FAILED` 且 evidence 完整后创建 revision `+1`；自动/按失败自适应的 replan、真实 provider/cohort 和收益仍未完成 |
| Pentest L3 | `WIRED_SHADOW_ONLY_FIXED_30_PROTOCOL_LOCAL_PASS_DURABLE_OWNER_AND_REAL_RUN_PENDING` | Conditional MCTS 可运行；当前真实 gate 通过动作只形成 root，同一 policy 产生但缺 capability 的已登记动作进入 virtual-only successor catalog，纯 transition 解锁后才展开。固定 30 项纯合同要求 successor identity 由授权 environment/scope、capability manifest、facts、policy、virtual state 与 candidate IDs 派生，并绑定独立 Task/Authorization/Verification receipt、校准误差和三类零违规统计。跨域 Gate 还要求 Code/Research 至少一域收益合格，但只生成 Review proposal。默认兼容路径仍是内存 ledger，没有统一 durable cohort owner 或真实 30 次运行；adapter 强制选择 L1/L2 baseline，L3 production influence=false |
| Code/Research 收益 | `FIXED_PROTOCOLS_LOCAL_PASS / REAL_BENEFIT_UNPROVEN` | Research 固定 24 项、三主题与本地/Web、hidden verifier、bootstrap/质量/citation/成本/时延门槛；Code 固定 20 项、同 source/model/provider/profile/budget、fresh worktree、+10pp 或非劣 5pp 且成本 -15%、hidden/static/diff-risk 非退化。D19 PASS/FAIL 终态都保留。两域均无统一 cohort owner/migration、真实完整队列、独立部署 verifier 或 promotion 结论 |
| Budget | `BLOCK_RUNTIME_FAIL_CLOSED / ATTEMPT_CHILD_RESERVATION_AND_PARENT_CONTENTION_LOCAL_PROVEN / PROGRAM_PARTIAL` | 0132 为每个 Attempt 创建 child reservation；父级仍是唯一 aggregate ceiling。8/16/32 线程及跨进程只产生一个 EXCEEDED winner/Outbox，其他 sibling 引用 winner；parent/child usage、retry 与 terminal observation 同事务，重启和升级回填不恢复已消费容量。并发作为 active Lease + `concurrency=1` child reservation 的可释放容量执行，不作为累计 usage。`BudgetPolicy` 合同仍能表达 DEGRADE/REVIEW，但 runtime support matrix 当前只有 BLOCK；两者在任何 Store/Outbox mutation 前稳定拒绝，不能静默降级。全部 cancellation/release 组合、真实 DEGRADE/REVIEW state machine 与七路线 policy 未完整执行 |
| Governed Chat Surface 模型预算 | `PASS_FORMAL_MODELATTEMPT_WORKERPROJECTION_PARENT_CHILD_BUDGET_LINEAGE` | 已 admission 的 Host Chat 把冻结剩余预算传给 Surface 主模型调用；canonical ModelAttempt、provider usage、WorkerProjection 与父子账本由 `c68850f8` 正式收据重开并精确匹配，replay 不再调用 provider。受控 terminal provider 不证明外部模型质量；其它路线仍需各自预算闭环 |
| Governed Agent Runtime 模型端口 | `PARTIAL_D15_GOVERNED_AGENT_MODEL_PORT_BRIDGE / REACT_SKILL_DELEGATE_FAIL_CLOSED` | 仅在 CapabilityBroker 已重验 sealed governed authority 后，`codex_runtime` 从 composed `ModelAttemptService` 派生同一 `PromptRegistry` 并交给 `AgentRuntimeExecutor`；缺端口/authority 直接关闭。前一 ReAct 切片只把 `run_skill` / `dispatch_sub_agent` 从发现面移除，并令注入的旧 ToolSpec 在低层执行器前拒绝；它不证明普通 ReAct Tool authority、文件/Web/Browser 路线或完整 Agent executor 已退役。ReAct 模型调用仍未受 `ModelBudgetGuard` 约束，也没有把上述提案编译为新 TaskGraph revision，因此只是窄安全封口，不是 Code/Research 工具能力 |
| Visual Agent 直连 HTTP | `RETIRED_COMPATIBILITY_ONLY / PRODUCTION_OFF` | `/v1/agent/run`（不是 Delegate 的 `vision_agent`）只以兼容 SSE 返回 `AGENT_INIT → LOOP_DONE / REJECTED` 和固定拒绝码；raw Agent Loop/ClawdBot/Edge client 与 Ledger writer 已删除。视觉聊天仅 `text` 可进入模型链，其他值在 runtime/model/Prompt/Memory/Ledger 前拒绝；legacy Action Planner 模型入口封口，纯 parser 为 `NONE_PROPOSAL_ONLY`。正向恢复必须经过 Secure Ingress/Governor → HostCommand → `graph_revision + 1` → node Attempt/Lease/Grant/Budget/Verification/Acceptance/Receipt；安全退役不等于视觉自动化能力完成 |
| Edge 指令直推 authority | `FIRST_GOVERNED_SLICE_WIRED / REAL_EDGE_CANARY_MISSING` | 全局 pusher owner/accessor、Host startup 注册和 Tribe 直推为 0。Obs `edge.command.push` 仅允许 `edge.somatic + emotion_state_command`，进入 OPERATOR HostCommand/TaskGraph、risk-4 Review、Verification/Acceptance，再以 `edge.embodiment_enqueue` 和 terminal Preview 写入 `HostEmbodimentRuntime` durable outbox；重复请求精确重开，claim 失效后只读查 receipt、不重发。Edge route 仍只是一条；Operator 全平面当前为 `16 active / 23 retired / 39 known`，更多 Edge command type 与真实 Edge/Gateway canary 仍缺 |
| Review/Resume | `GOVERNED_OPERATOR_CONTROLS_LOCAL_SQLITE_PASS / CANARY_NOT_RUN` | task-bound Review 的批准、拒绝、过期与取消进入 continuation；composition worker 按原 Host/Internal/Scheduler/Operator target 自动重入。`task_review.approve/reject/resume` 现在自身也必须先走独立 governed TaskGraph，并只在外层 Acceptance Effect 后调用共享 app-local `TaskReviewCommandService`。approve 的普通 bearer 不落 WorkOrder/SQLite/响应，Operator 只获得持久 public token ref；普通 API 拒绝该 ref，专用 Operator resume 在 Store 重开后仍一次性消费。历史 approve 即使已消费仍精确重放原 APPROVED/v1，不再进入 writer。PRE 不制造伪 Attempt，POST 不重跑 worker；部署 credential、完整进程 canary 和一般 recoverable Attempt 仍未统一在线续跑 |
| 对话副作用 | `POST_ACCEPTANCE_EVENTUAL / AUTHENTICATED_SURFACE_QUERY` | Acceptance 原子写 effect intents；visible turn、Preference 与 Life projection 分别执行并 exact 对账。Preference store gate 缺失/非 `bool` 失败关闭且零写；配置 false/malformed env 保守映射为 disabled，只写零 event/profile 的 `DISABLED` receipt；0118 将 event、append-only profile child version、evaluation receipt 放在同一 UoW，并以 sealed-child + full-set reconcile 拒绝篡改。推荐反馈只绑定 admitted-time 以前的 exact conversation/session delivery 及 canonical digest。Surface query 从 Gateway 同一 SQLite payload 精确校验 `effect_id`；`NOT_FOUND` 保持 `UNKNOWN`。各 effect 仍是最终一致，不是跨域全局事务 |
| Embodiment / Edge receipt | `PARTIAL_D8_DURABLE_EMBODIMENT_AUTHORITY` | Host 0104 让新 outbox 行绑定 canonical DB 派生的 `runtime_scope_id`，同 scope 只有一个持 durable runtime lease/fence 的进程可执行，第二实例失败关闭；enqueue、claim、过期、收口与对账均在同一写事务复验该 authority。精确 Edge session/owner/lease/fence 由 Host 持久保存；替代 runtime 必须在开放路由前 exact reconnect、轮换 Edge fence 并验证新 lease/`fence+1`。Edge 0002 冻结 command owner，receipt query v2 受 owner/current session/lease/fence 和 command/key 精确约束，legacy ownerless 返回 `NOT_FOUND`；`IN_DOUBT` 仅可只读 CAS 对账、绝不重放。loopback TCP/HMAC/SQLite 查询与重启 exact replay 已测；真实设备仍 `NOT_TESTED` |
| Composition | `INCOMPLETE / PRODUCTION_LOCATOR_PHYSICALLY_RETIRED / TRIBE_AGENT_CATALOG_APP_LOCAL / RESOURCE_MANAGER_STATUS_DURABLE` | 每个 Host ASGI app 拥有独立 Lifecycle/HardState/Authority/Bond/Memory/Governance runtime owner；Lifecycle、Tribe shared runtime/Agent catalog、Input、Agent、Context、Startup、Multimodal 与 heartbeat 使用显式 provider；ResourceManager status 由 0123 journal 绑定 exact Host fence。生产 locator module/field/lock/ref=`0/0/0/0`，测试桥仅在 pytest 安装。外部 Chroma/Neo4j namespace、Token/MCP durable restart、其它外部资源恢复、并发 multi-Host compatibility context、legacy Executive generic delegate、真实 workspace-bound executor 和 Host/Worker/Obs authority 仍未闭环；canary/wave integration 未运行，NE-01 不得封板 |
| 当前结构 Gate | `PASS_GATES_1_TO_14 / PROGRAM_INCOMPLETE` | Research ledger 与 NE-05 repository 已按职责拆分；NE-06 又将 Semantic recall result 移入 neutral contracts，并把 Prefrontal 纯运行策略拆为 201 行 focused module，Agent owner 为 1,879 行。fresh Gate 1 issues/SCC=`[]`、Gate 3 PASS、`failed_gates=[]`，未扩大白名单。静态通过不替代 NE-07 运行验收，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` |
| Persona 演化 authority | `PARTIAL / LEGACY_UNSCOPED_GROWTH_RETIRED` | `PersonaEngine` 中央拒绝非迁移 apply/rollback；Bond stage、milestone、session feedback 与 `emergent_growth_weekly` 在 canonical Host 下均不得产生候选，后者在 LLM/episodic 分析前退役。0142 只有在真实 Lab + Review 通过后才切换 versioned Persona/表达 policy，并可 exact rollback；aftereffect 驱动的正向核心人格成长尚未实现 |
| 长期演化能力 | `CODE_COMPLETE_NE03_TO_NE06_LOCAL / PROGRAM_INCOMPLETE` | World/Scene/Intent/Guardian、Evolution Lab、Skill lifecycle 与 0142 Feedback/Knowledge/Identity authority 已进入生产对象图。Knowledge reader 默认仍 SHADOW；部署 canary、真实数据库/网络/模型/设备与完整恢复必须由 NE-07 验收 |

### 1.1 Tribe runtime scope 当前事实

每个 `create_app()` 在 composition root 构造一个 `TribeRuntimeScope`，同时由 Host port root 独立持有三十五项 wrapper/state/runtime/publication/authority owner；同 root 稳定复用、跨 root 不共享。Authority owner 固定 canonical ledger、Episodic/LifeMemory、Model 与 Capability exact epoch 图。生产 locator 已物理清零，ResourceManager status 也已纳入 Host-fenced journal；Tribe scope fresh 持有 state registry、Host registry、event bus 与固定 23-Agent catalog，并以 exact owner context 围住兼容 getter。production provider 使用 constructor，显式 Chat/Input port 与 cross-Agent getter 都解析同一 scope；两个重叠 scope 不共享 Agent/registry identity，无 Host fallback 已归零。外部 Chroma/Neo4j namespace、其它外部资源恢复、完整 Host multi-app canary 和易失 EventBus 仍未收口，所以 app-local Agent identity 与 ResourceManager status durability 不等于完整 recovery、generic Delegate、七路线 receipt 或 NE-01。

NE-02 D20b 已开始建立独立于通用 DomainBenchmark/ModelAttempt 的 Research shadow 合同：语料 snapshot/manifest/source 精确绑定、`cache_policy=NONE`、两臂独立 execution、结构化 citation verifier 和 `SCRIPTED_REPLAY`/`CANONICAL_ATTEMPT_UNATTESTED` 非收益证据；0107 将 registration、run spec、arm execution 和 terminal receipt 持久化为 append-only SQLite ledger。D20c 的 `AttestedExternalResearchAdapter` 只调用 composition 注入的 canonical `ModelAttemptService`，为每个 arm 发送一个前台请求；只有返回值本身严格证明为单次、非缓存成功 attempt，且 route/output identity、provider/总延迟、token 和成本齐全时，才会重开同一 request 的 canonical audit started/terminal pair，核对 terminal record 与 provider latency 后生成内嵌 `ExternalProviderRunAttestationV1` 的 Research receipt。它没有 production prompt/workload 或真实凭据调用，因此当前只能是 `ACCEPTANCE_PENDING_EXTERNAL`。NE-03 当前工作树已由 Host ASGI lifespan 构造唯一 Runtime Context composition：服务端 observation 与 0137 active goal 进入 0111 World，Scene/Guardian 进入 0112，Surface、Proactive 和 Task admission 冻结同一 Scene identity，节点 Intent 经真实 authority adapter 仲裁并持久化 receipt set。0137 是 runtime-fenced、append-only 的长期目标 owner；激活/撤销只由 Governed Operator Acceptance Effect 提交，World 使用显式 `SOURCE_REVOKED` expiry，Intent admission 重开 current goal identity。Action Preview 在执行点重开 canonical authority，0113 Host Catalog 从 fresh DB 逐级启动到默认目标 ENFORCE。Guardian 绑定 Host runtime lease/fence。全部 `test_ne03_*` 单元/集成文件的一次完整波次为 `271 passed, 2 warnings in 332.52s`。这些只能将 NE-03 定级为“生产接线、本地波次通过”：Bond 上游原始证据、Windows graceful stop 与生产多实例 canary 仍是明确边界。#81～#83 后续资产继续保持 `PARTIAL/LEDGER_ONLY`，不得从 append-only 或测试通过推导生产闭环。

World projection 当前已按 `(key, source_ref)` 保留不同来源：同一来源仍以较高置信度版本去重，值不同的来源才标记 conflict 并公开在只读 `competing_facts` 分组；值相同的来源只记录为 corroboration，读取不会续期 TTL。该修复收口了旧的 confidence-only 静默覆盖，但 ContextPack/Scene/Guardian 尚未完成生产切流。Evolution Lab 的 branch policy 已从 `host_brain.evolution_lab` 导出，避免 runtime import 在收集阶段失败；它仍是实验分支门禁，不是生产激活 authority。

恢复调度边界现在会对 durable TaskGraph snapshot 重新执行全图环检测；发现依赖环时返回 `PLAN_CYCLE` 并失败关闭，绝不忽略依赖或退化为串行执行。该修复收口执行安全边界，但不改变多节点生产 canary 尚未完成的事实。

因此：历史 Core/GR 回执可以证明对应资产存在，不能证明 `Full Architecture DONE`。

## 2. 运行拓扑

| 运行面 | 入口 | 职责 |
|---|---|---|
| Host Main | `host_brain/__main__.py` → `host_brain.app.asgi:app` | 对话、Governor、Task/Agent Runtime、Life、Memory、Pentest、业务 API |
| Observability | `host_brain/obs_main.py` → `host_brain.app.obs_fastapi_app:app` | 只读投影、运维命令 BFF、Monitor UI 后端 |
| Monitor UI | `host_brain/app/monitor_ui/` | Operator 视图与受治理命令入口 |
| Edge RPC | `edge_client/__main__.py` | ASR/TTS、Avatar、Desktop、设备 actuator |
| Unified Gateway | `edge_client/gateway/bootstrap.py` | 平台 webhook、Host 输入回流、回复投递 |
| Desktop Pet | `desktop_pet/main.cjs` | Electron + Live2D 本地呈现 |

共享跨进程合同位于 `natsume_contracts/`。该包被 Host 与 Edge 直接使用，是版本化 API 边界，不是历史兼容目录。

## 3. Host canonical 请求主链

```text
Chat / Host Input / Gateway
  → Secure Ingress
  → Governor decision + grant
  → HostCommand + WorkOrderV3
  → direct graph 或 approved-plan graph
  → HostCommandRepository + DurableTaskStore admission
  → GovernedTaskCoordinator
  → node-bound grant + worker
  → Verification
  → Acceptance
  → Acceptance Effect intents
  → durable dispatcher / receipt / read-only projection
```

认证 Host Input/Gateway 在 Governor 前将 Secure Ingress principal 冻结为 command principal；`GovernedHostRuntime` 对 direct `chat/skill/delegate` 与 approved-plan 统一冻结 `surface / principal_id / ingress_claim_ref / ingress_audit_ref / input_sha256`。Plan 在首次 repository admission 前以 base request/session/principal 只读预检 parent Governor binding，direct route 也先预检，child grant 派生再复验；因此 principal 漂移不会留下不可执行的初始 Plan 图。同时具有成对 ingress ref 且 Gateway source 才是 `GATEWAY`，其他认证输入为 `HOST_INPUT`，无 ingress 为 `CHAT`；半绑定 ref 或畸形 SHA-256 失败关闭。D10 为 `pentest_agent` 建立唯一启用的 inner delegate adapter：不可变 invocation 带 command、graph/revision/node、attempt、lease/owner/fence、parent/node grant，调用前重验 durable grant，handler 只产生无副作用 Domain Pack/SecureRun handoff，也不写 legacy `skill_exec_log`。其它 target 在 direct/plan admission 前拒绝；这不是通用子 Agent 平台，也没有把已批准步骤的拓扑排序变成候选规划搜索。

关键实现：

- `host_brain/agent_runtime/cortex_runtime.py`
- `host_brain/app/governed_task_adapter.py`
- `host_brain/app/governed_delegate_dispatch.py`
- `host_brain/app/governed_task_authority.py`
- `host_brain/orchestration/governed_command.py`
- `host_brain/secure_ingress/graph_cancellation_authority.py`
- `host_brain/composition/runtime_group.py`
- `host_brain/observability/command_ports.py`
- `host_brain/app/observability_runtime/domain_command_ports.py`
- `host_brain/app/monitor_ui/src/features/execution/taskRuntimeApi.js`
- `host_brain/task_runtime/coordinator.py`
- `host_brain/task_runtime/coordinator_records.py`
- `host_brain/task_runtime/coordinator_receipts.py`
- [`host_brain/task_runtime/node_execution_runtime.py`](../../../host_brain/task_runtime/node_execution_runtime.py)
- [`host_brain/task_runtime/node_execution_projection.py`](../../../host_brain/task_runtime/node_execution_projection.py)
- [`host_brain/task_runtime/node_execution_records.py`](../../../host_brain/task_runtime/node_execution_records.py)
- [`host_brain/task_runtime/parallel_success_runtime.py`](../../../host_brain/task_runtime/parallel_success_runtime.py)
- [`host_brain/task_runtime/parallel_success_records.py`](../../../host_brain/task_runtime/parallel_success_records.py)
- `host_brain/task_runtime/parallel_settlement_projection.py`
- `host_brain/task_runtime/parallel_settlement_runtime.py`
- `host_brain/task_runtime/graph_cancellation_records.py`
- `host_brain/task_runtime/graph_cancellation_runtime.py`
- `host_brain/task_runtime/graph_cancellation_projection.py`
- `host_brain/task_runtime/runtime_outbox.py`
- `host_brain/task_runtime/local_graph_execution_registry.py`
- `host_brain/task_runtime/acceptance_effects.py`
- `host_brain/task_runtime/acceptance_effect_dispatcher.py`
- `host_brain/persistence/task_repository.py`
- `host_brain/persistence/task_outbox_delivery_store.py`
- `host_brain/persistence/task_dag_state_store.py`
- `host_brain/persistence/task_ready_claim_store.py`
- `host_brain/persistence/task_acceptance_effect_staging.py`
- `host_brain/persistence/task_acceptance_effect_delivery.py`
- `host_brain/persistence/task_decision_integrity.py`
- `host_brain/persistence/task_graph_cancellation_request.py`
- `host_brain/persistence/task_graph_cancellation_finalization.py`
- `host_brain/persistence/task_parallel_success_store.py`
- `host_brain/persistence/task_parallel_settlement_store.py`
- `host_brain/persistence/task_parallel_state.py`
- `host_brain/persistence/task_record_codec.py`
- `host_brain/persistence/task_retry_mutation_store.py`
- `host_brain/persistence/task_retry_replay_store.py`
- `host_brain/persistence/task_atomic_failure_store.py`
- `host_brain/persistence/task_store_types.py`

### 3.1 已接线范围

`chat`、`skill`、`delegate`、`plan` 被 Cortex 识别为 canonical governed route。它们不能回退到 Executive：`host_brain/executive_runtime/runtime.py` 对这四种 route 直接拒绝。

#### 3.1.1 Operator `sys_mode.switch` vertical slice

`sys_mode.switch` 不再由 caller 直接调用 mode mutation helper。Secure OBS ingress 先冻结 server-owned request/principal/tenant/action/scopes/method/path/body authority，`GovernedOperatorRuntime` 再生成 typed `OPERATOR` HostCommand、单节点 immutable TaskGraph 与 node-bound Preview/Acceptance 描述。Review 未决时 HTTP/outer receipt 为 `WAITING_REVIEW`，中央 Secure Ingress claim 不写 terminal result；continuation 恢复后才允许最小 `host.sys_mode_transition` Effect 进入 Host/Operator 共用的 app-local `AcceptanceEffectRuntime`。

Effect 为 `RECOVERY_PENDING`、其他 owner 持有 lease 或结果不确定时，Runtime Group 只 durable defer continuation，不得抢先把 outer outcome 写成拒绝或成功。Review `CANCELLED` 必须重开 exact Task cancellation authority；缺证据继续等待恢复，不能静默悬挂或伪造终态。COMMITTED effect 的副作用归因来自 exact HardState event；`NO_CHANGE` 无 event 且固定 `side_effects_executed=false`。一旦调用 HardState 后丢失响应、返回 `success=False` 或后置归因失败，必须先查询 exact event：已存在规范化为 `REPLAYED`，读取不确定保持 `PENDING + RECONCILIATION_REQUIRED`/`UNKNOWN`，普通 `max_attempts` 不得把它写成 `FAILED`；只有权威确认不存在才走普通失败。历史与未知结果只用 permanent event-id lookup 对账，不受后续 history window 限制，也不会再次执行 ModeTransition。

当前 Operator inventory 为 `16 active / 23 retired / 39 known / blocked=0`：既有十四条命令保持各自 lineage，新接入的 `long_term_goal.activate/revoke` 每个公开请求同样生成独立 HostCommand、TaskGraph、Attempt/Lease/Grant/Budget/Review/Verification/Acceptance lineage，只有 accepted effect 可写 0137。Scheduler CRUD HTTP 已以独立 `SCHEDULER` surface 完成相同治理切流，不改变 Operator 计数；真实 HTTP/Edge network canary、部署验签 key lifecycle、完整 Host 进程重启与 production canary 仍未执行，因此 Operator 与 Universal Runtime 不能由本切片升级，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

#### 3.1.2 Scheduler post-Acceptance operation slice

Scheduler 的生产 worker 不再调用 `SchedulerWorkerRegistry` 中的领域 handler，也不把 handler 返回值当作 Acceptance 前的 worker projection。`SchedulerOperationPlanExecutor` 只根据冻结的 occurrence、definition、worker key、operation fingerprint 和 graph/node/Attempt lineage 生成一个 immutable `scheduler.operation.execute` 描述；对应 verifier/acceptance 会从 canonical `WorkerProjectionStore` 和 Scheduler repository 重新打开这些 authority。

Graph Verification/Acceptance 与 Effect staging 复用 TaskStore 既有 canonical UoW。提交后，共享 app-local `AcceptanceEffectDispatcher` 才为 exact Scheduler action 派生并重开 Preview，调用 `SchedulerAcceptedOperationHandler`；实际领域结果继续写既有 append-only `SchedulerExecutionResultRepository`，没有新增第二个 result store。native occurrence claim 绑定该实际 result receipt；Operator 外层 outcome 使用自己的 checksum-chained receipt，并以实际结果已可归因为封口前置条件，不能把 Graph 已 ACCEPTED 当成 handler 已执行。Review 前 handler 调用为 0、Effect commit 后为 1；duplicate 命中 immutable operation/result identity 后不再调用。过期 claim 与 handler 越界后的响应丢失只允许 read-only reconciliation，无法证明时保持 `RECOVERY_REQUIRED`，禁止盲重放。

既有 post-Acceptance 切流测试仍只证明其声明范围。0117 进一步删除 Clock 的进程内 retry authority：`admit_due_occurrence` 在同一 `BEGIN IMMEDIATE` 内校验 live DUE owner/fence，写 immutable occurrence、推进 definition head，并把 retry row 原子转为同 owner/fence 的 `OCCURRENCE` claim。Clock 只接受该 exact claim；独立 lifecycle task 在 dispatch 前 renew、执行中按 lease/3 heartbeat，过期 lease 不能自我续活，clear/defer 都需实际调用时间。慢执行跨 lease 时第二实例 dispatch=0，异常 backoff 从真实失败时刻计算。该单元、合同与 durable-UoW 批次为 19 项、47.53 秒。Scheduler definition/admin 的直接写 authority、mutable batch freezing、一般未知外部效果的人工归因、wave integration 和 production canary 仍缺；远程 CI 为 `NOT_RUN_ACCOUNT_QUOTA`。因此 Scheduler 与 NE-01 仍为 `PARTIAL/IN_PROGRESS`。

#### 3.1.3 通用 Acceptance Effect 与 Internal result slice

Conversation 的 post-Acceptance 副作用现分为 visible turn、Preference evaluation 与 Life conversation projection。visible turn 精确绑定 `MemoryEvent` content/timestamp/metadata/projection/digest。Preference 在调用 extractor 前检查 app-local privacy gate contract：缺失/非 `bool` 失败关闭且零写；配置 false 或 malformed env 保守解析为 `bool false`，只提交 `DISABLED` receipt 且 event/current profile/child profile version 全为 0。启用时以 graph/revision/node/Attempt/request 派生 operation ID，并在 0118 单一 SQLite UoW 中写 event、append-only profile child version 与 evaluation receipt。receipt 可见后 trigger 封住 child 集，reconcile 还会比较 receipt 与实际 child 全集，故绕过 trigger 的额外记录也失败；零事件写真实 `NO_SIGNAL`。推荐反馈以 admitted timestamp 为上界，只读取同 person 且 exact conversation/session 的 delivery，并保存 canonical full-delivery digest。Life 以 deterministic command ID 重开 exact command receipt。Surface 初次 HTTP/畸形/响应丢失仍进入 `UNKNOWN`；Gateway query 从同一 SQLite outbox snapshot 读取 canonical payload 并精确校验 `effect_id`，Host 重派生 delivery/payload digest 后才可归因；`NOT_FOUND` 永远不能证明未执行。

共享 `AcceptanceEffectRuntime` 的启动扫描、后台 claim/recovery 失败与随后恢复会写入 app-local RuntimeGuardian 的持久 SLO event；重复未恢复失败不会制造无界事件，恢复后才 resolve。异常正文既不进入 durable evidence，也不写入日志，只保留 opaque incident 和异常类型。这个观察边界没有替代 Gateway/外部系统的权威查询，也没有提供一般未知效果人工归因。

Internal executor 的 raw work order、prompt、metadata、result、changed-files、verification 仍先写入 protected exact-inventory stage，公共 WorkerProjection 只携带 stage ref/digest 与有界摘要。外部入口只接受未准入 v2 描述；显式 V3 或顶层图身份会拒绝。受治理 post-admission worker 原生消费其 durable `WorkOrderV3`；没有 exact execution authority 的 write WorkOrder 仍 proposal-only。Governed Internal write 会在活 Workspace fence 内把 frozen base 到结果树的 path manifest 与 binary patch 密封，Acceptance 逐字重算并生成 deterministic accepted HEAD，再完成 0116 promotion；源分支不移动，seal 后篡改失败，lifecycle INSERT 回滚后新 Runtime 可复用同一 commit。recovery envelope、首次 activation 与周期 survivor 继续重建唯一 projection并续接 Verification/Acceptance/Effect，Agent 不重跑。检查点 A/B/C 仍是三个各自原子边界。终态 cancellation/raw-failure 仍需 claim-before-cleanup。状态为 `PARTIAL / INTERNAL_WRITE_PROMOTION_AND_PERIODIC_CONTINUATION`。Direct Agent Skill 不继承 Internal terminal classifier，而是由独立 route-specific reconciler 处理 success/Verification-failure stage，再复用 shared lifecycle cleaner；其它 terminal matrix、一般 unknown effect、Domain Pack/其它 Skill、handle-level TOCTOU、OS/container isolation 与七路线验收仍缺，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

post-COMMIT settlement 的 canonical BudgetUsage 仍只按 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING / CONFLICT` 解释；双重响应丢失先进入不附加 lifecycle/finalization mutation 的 hold，不释放 Lease/Budget或覆盖已提交usage/`EXCEEDED`。Lease 到期后更高 fence owner 只读重开前两类或幂等补写 `MISSING`，Agent/worker 不重跑。共享 Budget 已 `EXCEEDED` 时，accepted 或 missing checkpoint 必须证明唯一 sibling winner；missing 只能成为 version-stable、共享 winner Outbox/dimensions 的 `ALREADY_EXCEEDED`。真实 SQLite 已覆盖三 owner、双 checkpoint、four-root、五节点菱形和八根节点，按 graph order 释放依赖并保持 worker 单次调用。`CONFLICT` 会在新 Lease 前写 0130 quarantine hold；unknown `RESERVED/RECOVERY_REQUIRED` 和取消交叉也会写 durable hold 后进入各自签名 reconciliation。process-control `BaseException` 直接传播。无限规模、其它 failure/cancellation、自动归因、Workspace 全终态和部署 fan-out仍是缺口。

### 3.2 Read route 与退役边界

`task_progress` 直接读取 `DurableTaskStatusProjection`，`recent_turn` 直接读取 durable conversation window；两者只返回暂态 projection，不创建 Task、Attempt 或第二套执行 authority，也不在 Cortex 写入 user/assistant visible turn、Memory、Life、Preference、Outbox 或 Delivery。未知 legacy route 在 Cortex 以 `RUNTIME_ROUTE_UNSUPPORTED` fail closed。若未来需要将查询结果纳入对话历史，必须先经 TaskGraph Acceptance，不能恢复 read-route 直写。

Executive 已从生产 Cortex application ports 删除。旧 `_execute_explicit_skill_dag`、`route_planning`、`conversation_execution`、application `composition`、application port wiring、legacy diagnostic direct-skill matrix、进程内任务 registry、factory、accessor 和相关生产写入均已删除；Executive 内对 Runtime Capability Forge 自动 gap synthesis/replan/route override 的调用也已移除。Cortex 内 contextual-session/style、clarification、LLM unavailable 与异步 post-process 的直答 compatibility branch，及 Surface 的 `schedule_async_post_process()` fire-and-forget 入口同样已物理删除：canonical route 必须先通过 Governed Runtime `supports()`，否则在可见对话写入前拒绝；通过后只能把冻结上下文交给 TaskGraph，提交后的投影经 durable `surface.post_process` outbox 处理。`executive_runtime/runtime.py` 与 `app/executive_runtime.py` 只保留 fail-closed 拒绝边界，也不再是生产路由器。纯 `SkillDag` 类型及 adapter 仅保留为零生产调用者的兼容合同，不能调度 worker。NE-05 又移除了独立 Forge 的 approval/runtime-result/runtime-route 公共 authority，Forge 只产生 proposal。`app/intelligent_runtime.py` 已物理删除，不能成为隐藏的规划或执行入口；`SkillComposer` 也不再暴露 `execute()`。这些负向退役不证明真实外部 canary 或完整程序验收。

历史 `SubAgentDispatcher` 同样已降为无状态拒绝门：它不再保存 handler map、构造模型 gateway 或写独立 `skill_exec_log`，direct dispatch 固定返回 `LEGACY_SUB_AGENT_DISPATCH_RETIRED` 且不读取请求 context。Executive compatibility ports、runtime wiring 与 legacy `_dispatch_to_sub_agent` 已删除相应 request factory/accessor，因此没有再把该拒绝门作为可注入 Delegate 端口。`intent_agent_handler` 只投影 governed handoff，legacy source 和传入 gateway 都不能触发模型调用。保留的 `SubAgentRequest`/registry view 仅用于兼容 codec 与迁移诊断；真正的 Delegate 必须重新进入 `HostCommand → immutable TaskGraph`，目前仅 `pentest_agent` 的只读 adapter 被准入。这只关闭旧直调 authority，不等于一般 Delegate、模型预算或七路线验收完成。

Scheduler 与 Internal Agent 均已有真实任务切片。Internal 会先剥离调用方伪造的 lineage/approval authority，把 ingress v2 编译为 canonical `WorkOrderV3`；只有 `delegate:agent_runtime` child grant 完整验证后，frozen worker 才将该 V3 直接交给 executor。worker 原始材料进入 protected stage，seal 锁定 exact inventory/bytes；write result 还锁定 bounded manifest 与 binary patch。首次 activation 与 lifecycle survivor 可从 sealed checkpoint 继续 accepted path；正向 write Acceptance 则生成 deterministic accepted HEAD并保存 patch artifact。exact Budget 拒绝、终态 sealed cleanup、signed cancellation/raw-failure 各有窄 authority。三条 Direct Agent Skill 也会剥离 caller authority 并经 consumed Review、protected artifact 和 promotion Effect 写入同一 owner；同一机制现允许一个 reviewed Plan 非结果节点延迟到整图 Acceptance 才 promotion。Direct failure 与 Plan downstream failure 分别在 exact terminal/zero-effect 证明后由 Skill reconciler abandon，再由 shared lifecycle cleaner 清理。其它 terminal matrix、多 Workspace、一般 unknown effect、general `settle_worker` crash bundle、Domain Pack/其它 Skill 与 OS/container 隔离仍是明确边界。

## 4. TaskGraph、预算、Review 与副作用

### 4.1 Durable TaskGraph

当前 coordinator 能持久化多节点图、阻断未知依赖和环、推进 `BLOCKED → READY`、创建 Attempt/Lease/Reservation，并在 Verification 与 Acceptance 后完成节点。`TaskGraph.result_node_id` 与 `completion_policy` 是兼容 v1 可选字段。新生产 builder 与初始 `PlanningGraphCompiler` 默认 `ATOMIC_GRAPH_SUCCESS_V1` 并显式绑定当前 revision sink；失败重规划在未显式指定时继承 previous revision，因此历史缺省/`LEGACY_NODE_TERMINAL_V1` 图不会被静默升级。admission、durable identity 和 planner semantic/shadow comparison 都校验二者，不能从完成顺序推断结果 authority。

Host 现在在完整 RuntimeGroup 与共享 Acceptance Effect owner 激活后执行 mandatory boot continuation，并随即启动 app-local survivor supervisor。`HostCommandRepository.list_nonterminal_admission_page(limit=100, cursor=...)` 在每个只读 SQLite 事务中联结 immutable command 与当前 Graph，并在 SQL 中限定 `CHAT/HOST_INPUT/TOOL/GATEWAY + CREATED/ACTIVE/BLOCKED`；Scheduler/Internal/Operator work 不进入 Host inventory。repository 以 journal 的 immutable `(created_at_ms, command_id)` 做 keyset pagination，cursor 绑定 surface digest，查询每页读取 `limit + 1` 判定是否还有下一页。continuation 独立验证严格递增键、cursor 与页尾一致、command/Graph 不重复以及整轮 10,000 上限；因此第 101 条进入下一页，而不是让全部 survivor 失败。每条记录已经在对应 snapshot 内重验 command/work-order/assignment/Graph digest 及当前 Node identity，再进入既有 `GovernedCommandApplication.execute_stream()`。独立 OS 进程写入 101 个真实 SQLite admission 后，新 Runtime 已跨两页全部重开。supervisor 只串行重复这一查询与 exact re-entry，不重新构造 ingress、Governor decision 或计划；active Lease 和已有执行 owner 都只能得到 `DEFERRED`，Lease 到期后可在同一进程的定时/wake 扫描重新评估，缺失/漂移的 durable tuple、scheduler snapshot、页序或 terminal receipt 仍失败关闭。该边界只证明 Host-owned 图的有界分页周期精确重入，不提供 unknown-effect 或一般 multi-node/batch recovery。

RuntimeGroup 现在为 `INTERNAL/SCHEDULER/OPERATOR` 持有第二个、surface 集合不重叠的 survivor。生产激活顺序固定为完整 RuntimeGroup/Effect owner → Host scan → control scan → Scheduler clock；控制 scanner 按 command surface 和 Operator target 选择 exact runtime，并只传递已持久化的 Command/WorkOrder/Assignment/Graph。三类 runtime 显式公开 `continue_startup_admission`，Operation reconciliation 只是完成证据校验后复用该执行核心，避免用伪 Operation 触发启动恢复。App composition 注入 continuation/supervisor factory，因此 `composition/runtime_group.py` 不反向导入 App 实现；`internal_startup_admission.py` 独立承担 Internal startup tuple 校验、执行和终态归并。mandatory scan 有独立并发锁；周期 supervisor 的停止会等待活动 continuation 完成，再关闭 runtime admission。101 条控制面记录跨两页路由、三条真实 SQLite admission、并发 boot 去重和无 cancellation shutdown 已通过。该接线没有取得 Internal sealed-checkpoint 的专用恢复 authority，也没有证明真实 worker 的完整跨进程终态、一般 unknown-effect/failure/cancellation 或 NE-01 wave integration。

Host 一般 survivor 现在另有三条真实 OS 进程恢复证据。第一个旧进程在 Attempt/Lease/PRECHARGE 已提交但 Operation 不存在时硬退出，新进程沿 production Host continuation 取得同 Attempt 的更高 fence 后执行 worker；第二个旧进程在 WorkerProjection、Operation COMMITTED 和 worker BudgetUsage 已持久时硬退出，新进程直接进入 checkpoint continuation，不解析或调用 worker；第三个是 `root → child` 图，root 的 ACCEPTED artifact 保持 exact，只有 child 取得新 fence。三个完成状态在再下一进程中都不再进入 nonterminal inventory。该证据使用真实 SQLite 和生产 Task Runtime/Host 类，但 worker/Verifier/Acceptance 为可控协议实现，因此不能外推为现实模型、Skill、provider 或完整生产 canary。

被拒绝、过期或取消的 task-bound Review 走另一条精确终结分支：`GovernedCommandApplication` 只把 claim 的 node identity 传给 coordinator，不生成 `node_grant_refs`；`DurableDagSchedulingRuntime` 只接受无 grant 的已准入 node，并在读取 durable node 后要求该 node 仍为等待 Review、其余节点均非活跃执行态、Review runtime 再次报告 `REVIEW_DENIED`。随后才允许原有终结路径释放/写入 receipt；任何身份、状态、grant 或 decision 漂移均为 `GOVERNED_DURABLE_STATE_CONFLICT`。这不是一般 Review 恢复或多节点执行授权。

B0 的 `task_runtime/dag_scheduler.py` 是无副作用调度决策：它稳定选择 READY、依赖满足、retry due/waiting 节点，严格 fan-in，并受 concurrency/occupied slots 限制。B1 的 store/coordinator 子集已让 Coordinator 从 durable snapshot 消费该决策，并由 TaskStore 在 `BEGIN IMMEDIATE` 中原子提交 Attempt、Node、Lease、初始 Heartbeat 和 Outbox；claim 响应丢失可在 authority/TTL/outbox 均未漂移时精确返回原 Lease。

迁移 0089 只在 Node 持久化 `retry_not_before_ms`；attempt 次数始终由 Attempt journal 的连续 `attempt_no` 派生，避免第二真相。`DurableNodeExecutionRuntime` 只在 worker resolution/preparation 抛出显式 `RetryableWorkerPreparationError` 且尚无 operation reservation 时调用 `schedule_retry`；retry_count BudgetUsage、Budget、Attempt、Node、Lease 与 Outbox 在同一 `BEGIN IMMEDIATE` 内提交。预算允许时节点进入 `RETRY_PENDING` 并在到期后创建 attempt 2；retry_count 超限时进入 pre-reservation terminal failure 收口。reservation 创建后的异常从不进入自动 retry。

上述 safe-retry 是已接通并由真实 Coordinator fault injection 验证的 contract path。若 `schedule_retry` 已提交但响应丢失，同一确定性调用会严格校验 Attempt、Node、Lease、Graph、Budget、BudgetUsage 与关联 Outbox 后返回原决定，不重复扣减 retry budget；任一状态漂移都会 fail closed。resolver 的 `CancelledError` 不会被普通失败分支吞掉。当前三个生产 resolver（Host registry、Scheduler、Internal）都不会抛 `RetryableWorkerPreparationError`，因此不能写成现实瞬态故障已经自动分类。

普通或重试耗尽的 direct、non-deferred pre-reservation resolver failure 由 `DurableNodeLifecycleRuntime` 编排并调用同一个 `DurableTaskStore.fail_running_attempt_bundle`：在同一个 `BEGIN IMMEDIATE` 中把 Attempt、Node、Graph、Lease、唯一 graph-scope Budget 分别落为 `FAILED`、`FAILED`、`FAILED/BLOCKED`、`RELEASED`、`RELEASED`，并生成四条 attempt/error-bound Outbox 与一条无 attempt lineage 的 `BUDGET_RESERVATION_RELEASED` Outbox。Budget 必须精确匹配 graph/revision、`ACTIVE/EXCEEDED` status 和 expected version；任一 Outbox 插入错误都会回滚全部五类状态，响应丢失 replay 也逐项复核 Budget/outbox/version/timestamp。Runtime 对普通响应异常只以同一 callable 和同一 arguments 精确回放一次，第二次失败转 durable conflict，且没有 sequential fallback；旧 fence、版本/证据/Outbox 漂移或该 attempt 已存在任意 operation reservation 时一律拒绝。并行 deferred child 仍只关闭自己的 Attempt/Node/Lease，outer settlement 才能终结共享 Graph/Budget。TaskStore 仍是唯一 mutation/UoW authority；其它终态路径并未因此成为统一预算收口。

所有 Task mutation 共用的 Outbox 落库与 response-loss exact replay 校验实现归属 `TaskOutboxDeliveryStoreMixin`；BudgetUsage contention 与 Operation checkpoint read 分别归属 focused mixin。它们都只借用主 Store 的同一事务/连接；`DurableTaskStore` 仍是唯一 composition、connection 与 UoW authority。主 repository 当前 4,358 行，仍低于 4,600 行上限。

operation reservation 建立后，`DurableNodeExecutionRuntime` 会创建 `DurableExecutionHeartbeat`。后台循环严格先续 Lease、再续 Reservation，Worker 的每个流事件和最终结果都经过 checkpoint；停止并 join 心跳后才允许使用最新 Reservation version 执行 COMMIT。续租失败、版本漂移、worker 自发取消、时间预算到期、已知 worker 异常或调用方取消都会先尝试以 exact Attempt/Lease/owner/fence/version 调用 `TaskWorkerInterruptionStoreMixin`，在一个 SQLite UoW 内把仍为 `RESERVED` 的 Operation 推进为 `RECOVERY_REQUIRED`，同时写入 Operation Outbox、0130 recovery hold 与 case Outbox。case 写入失败会使整个 marker 事务回滚；成功后 Runtime 通过 `get_current_operation_recovery_hold_for_reservation()` 只读重开同一 case。持久原因只接受有界机器码；调用方取消继续向上传播。marker/case 不会终结 Attempt、Node、Graph、Budget 或 Lease，也不会判断外部副作用。

当前恢复由互不混淆的严格窄窗与一个持久停机面组成，不是任意 `RECOVERY_REQUIRED` 都能续跑。post-COMMIT 窄窗读取 exact `COMMITTED` Reservation、唯一 Outbox、原 Attempt、历史 Lease/fence、canonical WorkerProjection 和 graph Budget，再按 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING` 重开或幂等补写；多节点只有 `DurableDagSchedulingRuntime` 在 live owner fence 清除后可续接同一 Attempt。当前已证明双 checkpoint、shared-EXCEEDED missing、four-root、五节点菱形和八根节点。pre-Operation 窄窗严格证明 Operation absence 后才以同 Attempt、更高 fence 首次运行 worker。signed `REACQUIRE/COMMIT` 继续各自要求精确证据；signed `RELEASE + NO_SIDE_EFFECT` 可终结单节点整图，或在多节点只终结目标节点后由 0090 关闭共享 Graph/Budget。其它 unknown `RESERVED/RECOVERY_REQUIRED`、取消交叉或 checkpoint `CONFLICT` 由 0130 冻结为 version-bound append-only case + Outbox：unknown/cancellation case 从 marker 事务起即存在，Budget settlement、Lease expiry 与 lifecycle fence 不改变其未决 Operation 身份；出现 cancellation intent 时追加 intent-bound successor case并使旧 unknown case退出 current。unknown/cancellation 仍需各自签名对账，conflict quarantine，不得重放 worker。准确定级是 `ATOMIC_INTERRUPTION_RECOVERY_HOLD_LOCAL_PROVEN / GENERAL_SHAPE_COMMITTED_CHECKPOINT_RECOVERY_LOCAL_PROVEN / SIGNED_MULTINODE_NODE_FAILURE_0090_LOCAL_PROVEN / GENERAL_RECOVERY_INCOMPLETE`。

`AcceptedGraphFinalizationRuntime` 被 normal Node、上述 continuation 与 outer DAG 共享。它关闭了另一个独立崩溃窗：Node 已 `ACCEPTED`、Graph 仍 `ACTIVE`、Effect 尚未落盘时，重启从 exact checkpoint 与 canonical WorkerProjection 重建 Effect，并调用既有 `transition_graph_and_stage_acceptance_effects` 原子提交 Graph+Effect。测试证明 worker 不重跑、Effect identity/digest/idempotency 不漂移；篡改 projection 时 Graph 保持未完成且 Effect 不产生。

0102 `task_runtime_node_artifact` 现在是 Node 结果权威，GR06 NODE Effect 也已并入 canonical `commit_node_acceptance()`。normal、COMMITTED checkpoint continuation 与 Review approve 在同一个 SQLite writer transaction 中冻结 exact NODE Effect 集（可为空）、写 immutable artifact、推进 Node `ACCEPTED` 并追加绑定 exact Attempt 的 Node Outbox；任何 effect set、artifact、CAS、judgement 或 Outbox 漂移整笔回滚，响应丢失只能逐项重验后精确 replay。Effect dispatcher 在 0102 存在时还会命中并重新验证同 graph/revision/node/Attempt 的 artifact，旧 `SUCCEEDED Attempt` 不能借用另一个 Attempt 的 Acceptance。compiler 生成的 dependency result refs 不能由 planner 伪造，READY/执行/恢复前只解析同 revision 的全部前驱 artifact，`FrozenNodeInput` 再校验 ref 集合与 payload digest。新 HostCommand/Task admission 拒绝空 `output_contract`；`ProcessTaskRuntimeContainer` 在 recovery 前校验 0102 asset/schema checksum，并把非终态 active legacy 空合同/缺 artifact，以及缺 exact artifact 的可投递 terminal legacy Effect 隔离为 reconciliation required。

应用 Store 的 public `transition_node(ACCEPTED)` 在 0102 存在时已要求有效 artifact，生产三条 acceptance 路径不再拥有第二 writer。本轮进一步删除 `DurableTaskStore.create_graph`、`insert_graph_in_transaction`、`stage_acceptance_effect_intent` 三个 public 入口。唯一生产 Graph hook 是 `_insert_graph_from_host_command_in_transaction()`：它仅由 `HostCommandRepository._admit_normalized()` 调用 1 次，并借用同一 admission transaction。唯一 Effect INSERT hook 是 `_stage_acceptance_effect_intent_in_transaction()`：它仅由 `transition_graph_and_stage_acceptance_effects`、`commit_node_acceptance`、`complete_parallel_graph_success` 三个 canonical UoW 使用，共 6 个生产调用点。Graph/Node INSERT SQL 只存在于前一 hook，Acceptance Effect INSERT SQL 只存在于后一 hook。

0124/0125 把这个结果提升为窄范围的 `APPLICATION_AND_SQL_ADMISSION_AND_EFFECT_STAGING_WRITE_AUTHORITIES_SEALED`：Graph/Node/HostCommand 新 INSERT 必须来自 exact graph/revision admission scope，Effect 新 INSERT 必须来自 exact effect/graph/revision/node/attempt/commit-scope/idempotency scope并满足规范初始状态。plain SQLite、wrong/unscoped Store、late Node、raw HostCommand、非规范 Effect 与 trigger drift 均失败关闭。`tests/task_runtime_authority_fixtures.py` 是显式 test-only 历史状态 fixture，不得被引用为 canonical admission 或生产单写证据。该物理声明不包括其它 UPDATE/DELETE mutation，也不能据此宣称一般 recovery、production fan-out 或 NE-01 已完成。

TaskStore 切片的定向证据不做重叠汇总，分组如下：authority/structure `19 passed`；HostCommand `1 passed`；NLE/operation/heartbeat `77 passed`；acceptance-effect `15 passed`；parallel/settlement/atomic `45 passed`；terminal classification `32 passed`。Cancellation 首跑为 `29 passed + 1 fixture failed`，修正 fixture 后该 case `1 passed`；integration 首跑为 `46 passed + 3 fixture failed`，修正后 3 个 cases 分别通过。第三个 fixture 的 `node_risk_level` 断点已在当时的 dev 基线复现后修正。波次 integration 为 `NOT_RUN_PER_WAVE_POLICY`；该切片完成时 full architecture static Gate 为 `FAIL [1,6,10]`。

TaskStore 切片之后，NE-01 又关闭了 full architecture static Gate 10。0112 Runtime Context readiness 不再由 `runtime/context_authority_schema.py` 打开私有 SQLite 连接，而由 `RuntimeEventRepository` 持有检查实现并通过 `RuntimeEventStore` readiness port 提供给 runtime。Service Catalog 的连接、SQL 与事务从 `service_catalog/rollout_authority.py` 下沉到 `persistence/SQLiteServiceCatalogRolloutRepository`；authority 只保留不可变合同、repository Protocol 与 Guardian Observer，rollout runner 依赖 Protocol。`ACTIVATION_STARTED` receipt 和 rollout head 由 repository 在同一事务写入，故障注入证明任一写入失败时共同回滚。

World Observation 的 `_write_lock` 实际仅是保护 transition 的 `RLock`，静态 Gate 曾把名称误判为业务 SQLite writer；它已更名 `_transition_lock`，没有增加或迁移数据库写 authority。该切片没有新增 migration。`host_startup.py` 与 ASGI 仍负责 concrete `SQLiteServiceCatalogRolloutRepository` wiring，因此这里只能定级为 `GATE_10_PASS / TYPED_COMPOSITION_INCOMPLETE`。定向证据为 Context `7 passed`、Catalog unit/contract `15 passed`、真实 Host socket integration `6 passed`、Repository/Global 独立复跑 `16 passed`（最后一组与 15 项重叠，禁止相加）以及 Global SQLite boundary `PASS`。该切片完成时 full static 为 `FAIL [1,6]`，其先前审计基线为 `FAIL [1,6,10]`。当时剩余 Gate 1 是 application/composition/persistence SCC，Gate 6 是 projection daemon app lifecycle owner；NE-01 仍 `IN_PROGRESS`，wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

随后的 Gate 6 切片把 `ObsApplicationContainer` 泛化 `start()` 改成 `start_projection_jobs()`，并以静态合同锁定生产唯一调用点为 `obs_fastapi_app.py` 嵌套 `lifespan`。lifespan 先对 ports bundle 做原子 single-use claim，再 compose app-local daemon、start、yield；finally 的真实关闭序列是 stop/join/in-flight fence、精确 leader lease release、close owned SQLite、clear state。stop timeout 或 rollback close failure 不覆盖原句柄，保留 container/ports 供重试。bundle 内 claim 令两个 app 不能共享同一 ports，`dataclasses.replace()` 继承同一 claim 也不能绕过；两个独立 ports/app 保持隔离。没有 module-global daemon，也没有新 migration。

完整 Obs app 首跑为 `13 passed + 1 test fixture failed`，旧 fixture monkeypatch 仍引用已删除的 `start`；修正后该 case `1 passed`。最终生命周期关键批次为 `12 passed`，daemon cadence/shutdown/serialization/start-single-flight 为 `4 passed`，最终 Gate 6 定向集合为 `19 passed`；这些集合有重叠，禁止相加。Gate 6 切片自身的历史失败回执保持不变；其先前基线为 `FAIL [1,6,10]`，后续 Gate 1 与 Gate 11 曾在各自提交绑定快照中关闭。

当前修正切片把 daemon 监督状态接入真实 Obs `/health`。app-local snapshot 输出 lifecycle/running/generation/wave、连续失败、opaque incident、错误类型和恢复标记；job、lease acquire/renew 的普通异常不会结束后台线程，成功波会清零连续失败，进程控制异常会进入 `FAILED` 并要求显式 close 后再启动。新合同/单测首批 `6 passed in 13.13s`；受影响回归首次 `67 passed, 1 failed in 169.29s`，旧 Host fixture 补 lifespan 后单 case `1 passed in 22.08s`，最终 `68 passed in 211.30s`，批次重叠不得相加。durable SLO journal、Guardian acknowledgement、production canary 和 wave integration 均未完成；NE-01 仍 `IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

Gate 1 的首个分层收口只处理 Service Catalog：`service_catalog/startup_coordinator.py` 成为启动协调器 canonical owner，原 composition 路径仅同对象重导出；稳定合同进入 `contracts/service_catalog.py`，旧类型路径同样仅同对象重导出；`rollout_authority.py` 只保留 Guardian observer，`SQLiteServiceCatalogRolloutRepository` 只依赖稳定 contracts。application→composition 两条和本域 persistence→application 一条依赖已清零，使 SCC 从 application/composition/persistence 缩为 application/persistence。该切片完成时 persistence→application 仍有 11 条：PlanningSearch 7、Evolution/Lab/Skill/World 4。定向证据为 contract/unit `61 passed`、真实 Catalog/Host socket integration `7 passed`、边界复跑 `7 passed`，后两组可能重叠不得相加。

第二个 Gate 1 分层切片新增纯 `host_brain/contracts/planning_search/`，把 core、benchmark、calibration、code、research 八组稳定合同与模型移出 application；旧 `host_brain/planning_search/` 同名模块仅同对象重导出，兼容 `__all__`、类型 identity、`isinstance` 与序列化形状。`code_benchmark_ledger_repository.py`、`planning_search_calibration_repository.py`、`research_shadow_ledger_repository.py` 不再导入 `host_brain.planning_search`，因此本域 7 条 persistence→application 已清零。边界合同 `5 passed`，D13/D18/D19/D20 回归 `60 passed, 1 skipped`，跳过来自 Windows 无 symlink 权限；`py_compile` 和 diff-check 通过。该切片的 commit-bound Gate 1 仍 `FAIL`，application/persistence SCC 尚有 Evolution/Lab/Skill/World 4 条反向依赖；application→composition=0、Edge violation=0、reverse import=0，Gate 6/Gate 10 继续 `PASS`。本切片无 migration，只改变持久化合同依赖方向，不证明 PlanningSearch 生产功能或收益，typed Composition、全局 Service Locator 清零与 NE-01 均未完成。

最终 Gate 1 切片又把 Evolution、Evolution Lab、Runtime Context 和 Skill Lifecycle 的持久化共享类型迁入纯 `host_brain/contracts/evolution.py`、`evolution_lab.py`、`runtime_context.py`、`skill_lifecycle.py`。旧 `evolution/contracts.py`、`evolution_lab/contracts.py`、`runtime/context_contracts.py` 保持同对象重导出，`skills/skill_lifecycle.py` 仅保留应用服务并重导出合同；Evolution Governance、Evolution Lab、Skill Lifecycle、World Observation 四个 repository 只导入中立 contracts。最后 4 条 persistence→application 因此归零。fresh Gate 1=`PASS`，`issues=[]`、SCC=`[]`、Edge finding/violation=0、reverse imports=`[]`，Gate 6/10 继续通过；在该切片的 commit-bound 快照中，#87 组合后的 full architecture static=`FAIL [11]`，因为 ToolBroker production-service inventory 同时命中 `process_runtime_services.py` 与 `tools/broker.py`。边界合同 `4 passed`，Context/World、Evolution Lab、Skill、Evolution Governance 定向 `32 passed`，py_compile/diff-check 通过。本切片无 migration；它只关闭 Gate 1，不等于 typed Composition、Universal Governed Runtime、PlanningSearch 收益或 NE-04～NE-06 生产功能完成。

后续 Gate 11 切片没有改写上述历史回执，而是单独退役 process `get_tool_broker` accessor、`tool_broker` slot 与 `HostLifecycleToolBrokerProvider`。这与此前已经删除的 `get_mcp_tool_broker`/`mcp_tool_broker` 是两组不同边界。现在唯一 `ToolBroker(...)` constructor 位于 `host_brain/tools/broker.py`，count=1，并只由每个 ASGI app 的 `GovernedHostToolRuntimeComposition → ToolRuntimeBoundary` 懒构造和持有；route/provider 只引用同一 app-local 实例，不再回读 process locator。该切片的提交绑定静态快照为 `PASS`，36 文件受影响集的 `265 passed in 210.19s` 也只作该提交证据；当前全量静态结论另见文首的 Gate 1～14 `PASS`。当前仍是 `NE-01_IN_PROGRESS`：生产 locator 与 Tribe 无上下文 fallback 已在后续切片物理退役，Tribe shared state/registry/bus/Agent catalog 也归 Host scope，ResourceManager shared status 则由 0123 durable journal 取代；但真实 workspace-bound governed executor、易失 bus、其它恢复边界与完整 Host/Worker typed composition未完成，wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

Coordinator 有两个独立能力：`atomic_graph_success_enabled` 决定进程是否能执行 frozen atomic policy，`bounded_fanout_enabled` 决定 scheduler 多节点批次是否实际并发。atomic 对 Host 默认开启，Scheduler/Internal/Operator composition 显式开启；bounded fan-out 仍默认关闭。Application 在 `repository.admit` 前检查 atomic capability、node-grant service 与 parent grant，任一缺失即拒绝且不写 durable admission。Coordinator 再要求 `node_grant_refs` 精确覆盖整个 admitted Graph。atomic 图即使是单节点或顺序执行也使用 deferred child + outer terminal authority；terminal-leaf 实际并行还必须另开 bounded fan-out。节点 `ACCEPTED` 在 Graph 与 graph Budget 原子收口前只可供内部 outcome 重建，不能公开为最终成功。

Coordinator 在任何 Budget activation 或 worker resolution 前调用 `assert_parallel_settlement_decision_ready()`；`0090` 缺表/缺列会以 schema-not-ready fail-fast。B5 成功开关实际生效时还会先调用 `assert_parallel_success_decision_ready()`，要求迁移 0091 完整可用。当前 338 行的 Store-only `ParallelSettlementProjection` 读取 scheduler snapshot、Node 与 Attempt，负责 active re-entry、rejected Budget outcome 和 outcome snapshot 的严格校验；当前 657 行的 `ParallelSettlementRuntime` 共享原 Store、BudgetRuntime 与 RuntimeOutbox，负责 outcome resolution、ownership/CAS conflict 判定和 settlement orchestration。对于 `ACTIVE` 多节点重入，若 durable snapshot 仍含 live Node、active Attempt 或 `ACTIVE` Lease，入口返回 `GOVERNED_EXECUTION_IN_PROGRESS`，不会 resolve 新 worker、改变 Attempt/Lease，也不会抢现有 owner 的 execution authority。结算窗口遇到 ownership fence，或 CAS 后重读发现 active Node/Attempt/Lease，也返回 `IN_PROGRESS`；纯 CAS 且没有 active ownership 事实仍转为 `GOVERNED_DURABLE_STATE_CONFLICT`。

并行 child 使用 deferred shared-terminal 模式：它仍独占自己的 Attempt、Node、Lease、Reservation，并可记录 Budget usage 或把预算标成 `EXCEEDED`，但不得终结共享 Graph，也不得把 Budget 置为 `RELEASED/CONSUMED`。因此普通的 node-local 非接受不会 fail-fast cancel sibling；outer 会等待所有已启动 sibling 到达 durable node-local terminal，再按固定优先级解释整批结果。所有 Coordinator 与 cancellation runtime 发布先经过每个 Coordinator 唯一的 `RuntimeOutbox` asyncio lock；同进程 Host/Internal/Scheduler 共享的 `RuntimeOutboxPublisher` 又以实例级 `threading.Lock` 串行化 claim/append/complete drain。跨线程合同用两个 worker 证明共享 publisher 的 peak active drain 为 1；跨进程排他、过期接管和 stale-owner 拒绝由 0093 durable claim/fence 承担，而不是该锁。

live 与 crash/re-entry 不再从瞬时 receipt 拼不同决策：ParallelSettlementProjection/Runtime 从 durable Node、Attempt、Reservation、BudgetUsage 重建完全相同的 canonical `Natsume.task_runtime.parallel_settlement.v2` payload/ref，最终仍由 DurableTaskStore 在唯一 UoW 中提交 Graph、可选 Budget、Outbox 和 decision row；response-loss 只 exact replay。为保证跨批次重入 digest 稳定，outcomes 从整个 admitted Graph 的 durable terminal Nodes 和每节点 latest stable Attempt 全量重建，不只当前 claimable batch；当前 batch 只决定优先级与 selected。`RETRY_PENDING` 是合法的非 selected durable outcome，也可由重入恢复。BUDGET selected `BUDGET_BLOCKED` 绑定 rejected usage；共享超额后的被动 blocked sibling 不要求本地 usage。每次读取 decision row 都验证 digest、精确 Graph/可选 Budget Outbox 与 Attempt lineage，BUDGET 再验证 usage；terminal replay 另校验 Graph target/version，并要求 selected outcome 的 Attempt 仍为该节点 current latest。结构拆分没有把 0091 success 或 0092 cancellation authority并入 0090 Runtime。

Budget usage 高竞争已从主 repository 拆入 732 行 `TaskBudgetUsageStoreMixin` 并关闭唯一性缺口。一个 winning rejected usage 执行唯一 `ACTIVE → EXCEEDED`、推进版本并拥有唯一 Outbox；其他并发 sibling只追加 `ALREADY_EXCEEDED`并引用 winner。0090 Budget decision 的写入、读取与 replay 复用同一 terminal authority 校验；public reader 以显式只读事务冻结单一 SQLite snapshot，逐一复核 Reservation 精确版本、sole winner Outbox、Attempt/Graph lineage和 passive sibling，避免写入正确但读侧接受漂移。`DurableBudgetRuntime._charge` 不再固定最多 4 次 CAS，而以 `max(configured retries, policy concurrency + 1)`覆盖 admitted writer wave；concurrency=8/连续 8 次 conflict 后第 9 次成功已有单测。8/16/32 线程、跨进程、interleave、restart/replay 与 drift/corruption tests已通过；这不代表 concurrency 维度已经运行时计量，也不补齐 `DEGRADE/REVIEW`。

合同/集成测试覆盖：live 首次构造与 crash/re-entry 重构得到同一 payload/ref、提交响应丢失 exact replay、较早批次 terminal Node 被纳入后续 decision、`RETRY_PENDING` sibling 作为非 selected outcome、Budget selected usage 与被动 blocked sibling 分离、newer selected Attempt replay rejection，以及 decision row/marker corruption fail-closed。

B5 terminal success 不复用 0090 的 failure decision。每个 Accepted 节点必须拥有 latest `SUCCEEDED` Attempt、已 `COMMITTED` 的 operation Reservation receipt，以及由 `WorkerProjectionStore` 冻结的 `Natsume.task_runtime.worker_projection.v1` 投影。读取后重新 canonicalize 并重算 digest，精确校验 command/request、graph/revision、node/attempt、route/target、receipt、node grant 与 `APPROVE`；仅凭一条看似合法的 digest 字符串不能通过。只有 `result_node_id` 对应投影会编译整图 effects 并成为公开 projection；其他 Accepted 叶只提供 outcome/evidence。Skill 同样不能以瞬时 executor 返回值争夺结果 authority。

`ParallelSuccessRuntime` 负责 0091 整图成功的 read/validation/orchestration，但 `DurableTaskStore.complete_parallel_graph_success()` 仍是 B5 唯一成功 mutation authority。Runtime 只持同一 Store、共享 RuntimeOutbox/BudgetRuntime、projection reader 与共享 NodeExecutionProjection；纯 `parallel_success_records.py` 只拥有 route-authority 投影。Store 在同一个 `BEGIN IMMEDIATE` 中将 Graph `ACTIVE → COMPLETED`、共享 Budget `ACTIVE → CONSUMED`，写入不同的 Graph/Budget Outbox、全部 `GRAPH` scope effect intents，以及迁移 0091 的 `Natsume.task_runtime.parallel_success.v1` decision row。decision 使用 `tasksuccess://sha256/...` 引用并绑定 completion policy、所有 Node status/version、Accepted outcome、显式结果 Attempt/receipt、projection digest、effect identity 和两条 Outbox；任何晚故障整体回滚。已完成图的 replay 只读取并复核 0091，不重新编译 effects；若第一次提交和第二次 exact replay 的响应都丢失，ParallelSuccessRuntime 重读 Graph terminal authority 后进入同一 completed replay 分支。

对抗验证包含单节点 atomic、串行 atomic、多 terminal roots 反向完成顺序、flag-off 与缺 node-grant service 在 admission 前拒绝、projection narrative/command/grant 篡改、restart after accepted、completed restart 不重编 effects、一次和连续两次 Store 响应丢失。它证明 shadow contract 自洽，不等于生产 canary。

0095 已封闭历史 marker 的 revision 身份边界。`task_runtime_outbox.graph_revision` 对 `TASK_GRAPH` 必须是指向现存 Graph 的正整数；0098 只为 `GRAPH_CANCELLATION_EFFECT_RECONCILIATION` 增加同样的正整数 revision，并要求 case ID、revisioned payload ref 与 immutable inventory 物理一致；其他 aggregate 必须为 `NULL`。列的物理形状固定为 `INTEGER / nullable / no default / non-PK`。迁移接受 0090/0091/0092 decision、0092 intent、Attempt lineage、graph ID 唯一 revision，以及 HostCommand 的两种 exact authority：immutable `command_id` evidence 绑定正常 multi-revision CREATED；Migration Runner 注册 deterministic `natsume_sha256_hex`，以 revision-bound command ID 重算升级前五段 governed digest，同时匹配 legacy `outbox_id + idempotency_key`，绑定 ACTIVE 等非 attempt transition。升级本身不改变 Outbox ID/key，`graph_revision` 只是新增精确维度，因此 response-loss replay 不断裂。多个来源冲突或自定义 producer 历史行仍无法唯一归属时，整笔迁移事务失败而不猜测。0090 `taskbatch` 的缺 decision row/错绑扫描现在按 `graph_id + graph_revision` 完整覆盖多 revision 历史，所有 decision getter 也复核 Graph Outbox 的 exact revision。

B5C 在其上新增迁移 0092 和两阶段 Graph cancellation。`task_graph.cancel` 已接入 Operator Command Gateway、同源 BFF 与 Task Runtime UI，payload 精确绑定 `graph_id + graph_revision + reason_code`，权限固定为 `obs:write + task-runtime:cancel`。Gateway 认证后的 server-owned authority 携带 durable audit/claim、request/principal/tenant、认证 body digest、action/scopes；同一 `SecureIngressGraphCancellationAuthorityVerifier` 注入 Host/Internal/Scheduler，重读 committed 且未写入 terminal `claim_result_ref` 的 authority 并核对 canonical body 后才以 `audit_ref` 签发 request identity，settled authority 不可复用为 bearer。Application 不接受调用方自报 request identity/source。Store phase 1 先持久化 intent，将 Graph 置为 `BLOCKED`，再取消/围栏 Node、Attempt、Lease、Review/token、operation 与未授权 effect；`GraphCancellationRuntime` 只有在该提交后才通过共享 registry cancel/join 本进程 child/heartbeat。异常确定性只有经 cancellation handler 翻译为 OBS 封闭端口异常后才可进入 NOT_EXECUTED，任意依赖异常保持 uncertain。execute re-entry 由 Coordinator 薄委托该 Runtime，从 durable intent 继续 advance/finalize；只读 `GraphCancellationProjection` 只持 Store，本地 task registry 不构成 authority。0096 的 Review continuation 是另一条独立协调链，不取得 0092 cancellation authority。

`GovernedRuntimeGroup` 先按 exact revision 读取 immutable HostCommand surface，再把命令路由回 Host、Internal 或 Scheduler owner。若下游抛错，它重读 exact revision 的 0092 intent；只有 request、audit identity、OPERATOR、reason、graph/revision 全匹配时才报告可能已有副作用，无 intent 则明确按未执行拒绝，读取失败或畸形事实保守标记不确定。Task Runtime UI 随后刷新 durable detail，不把命令返回直接显示成 `CANCELLED`。

final cancellation 只有在 active ownership 与 operation/effect/review 不确定性全部 reconciliation 后才以一个 UoW 提交 Graph `CANCELLED`、Budget 释放/取消、Outbox 和 canonical decision。operation reconciliation proof 在动态 evidence 构造和终态 getter 中都会重验结构、digest、key 与签名；同 revision 的 Review create/approve/resume 在事务内被 0092 intent fence。`ACCEPTED` Node + `SUCCEEDED` Attempt 已确认的 NODE effect 保留 delivery authority；其他 NODE effect 与未完成 Graph 的 GRAPH effect 被 fence。0091 success 与 0092 cancellation first-writer-wins；请求响应丢失只允许四字段 authority 完全一致的 exact replay，publisher 故障不会阻止 durable intent 后的本地 cancel/join。基础设施 shutdown/disconnect cancellation 不会自动写成业务取消。

Acceptance Effect 的终态不是对称证据。`COMMITTED` 必须带 durable receipt，可自动把 `RECONCILIATION_REQUIRED` 收口为 `COMMITTED_EVIDENCE`；一般 handler/transport `FAILED` 只说明没有取得成功 receipt，外部副作用仍可能已发生。因此取消请求观察到既有 `FAILED`，或取消后 in-flight handler 落为 `FAILED`，都继续等待显式对账，不能按 reason code 猜测。0098 将旧 `FAILED_EVIDENCE`、legacy retry-PENDING+CANCELLED fence 与 finalized 历史异常冻结成 immutable case inventory；PENDING proof 只能追加新 decision evidence，FINALIZED correction 只作为 trusted read overlay。`PENDING` 的完整 durable tuple仍区分 virgin 与 retry epoch：只有 attempt/fence/version 全 0 且 reason/error 均空才可逻辑取消，混合 tuple先由 codec 判为 corruption。Production claim 仅授予 `SUCCEEDED + ACCEPTED` NODE 或 `COMPLETED` GRAPH；前者在 cancellation 中保留 authority并被排除，后者已终态，所以 fenceable retry PENDING 只表示 legacy/imported/cross-table corruption。请求路径对它保守阻断；旧 decision/fence/evidence 始终不可变。

Operation 与 Effect 签名对账当前均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`：受限 service 由 ProcessTaskRuntimeContainer 构造，ASGI 注册进 Operator Runtime state，scoped OBS command 绑定认证 actor，TaskStore 只注入 Ed25519 公钥验证 authority；Operator feature flag 默认关闭。开启 cancellation 时 Operation、Effect、Terminal 三类 reconciliation 必须全部开启，启动还要求 Secure Ingress `ENFORCE` 和可执行 Gateway。0098 Effect proof exact 绑定 case/intent/effect/fence/Attempt/resolution/actor/Outbox/revision；部署密钥/凭据、完整 key lifecycle 和 canary 未完成前 cancellation 继续 `PRODUCTION_OFF`。

0099 已将历史 terminal Graph/Budget 半状态改为 append-only case/proof/supersession reconciliation，而不是未实现的笼统缺口。Host 默认执行 exact auto，但只消费从 canonical ledger 唯一重建的 `EXACT_REPAIRABLE`；signed 与 quarantine 保持 fence。Readiness 严格比较 3 table/4 index/10 trigger 的 canonical `sqlite_master` SQL，并由 Python 复核 case/subject/proof。未决 current case 在整个 Graph/revision 上围栏普通 Graph/cancellation/Budget transition/consume/usage/retry writer，只有 0099 proof UoW 可 bypass。System auto 用 immutable case 时间生成稳定 proof identity 并跨进程收敛；`TERMINAL_HEALTHY_CLOSURE` 只在历史未决 case 且 current classifier healthy 时生成，固定为 signed `NONE` 且零 Graph/Budget/Outbox 写入。它仍只是窄 reconciliation，不是 general recovery。

Dedicated `obs_fastapi_app` 的 Effect GET 不构造 `DurableTaskStore`，而是在 `HOST_NLE_OPERATOR_VIEWS_ENABLED=true` 时用 ledger canonical DB path 构造 `EffectReconciliationQueryRepository`。该 repository 仅继承 read/validation mixin，连接强制 SQLite `mode=ro + query_only=ON`，没有 mutation/UoW surface；false 时不触发 0098 readiness。Operator Query Gateway 对 candidates/cases/challenge 精确要求 `obs:task-runtime:read`，`obs:operator:read` 被拒绝；cases/proof 是最小投影，exact subject 通过 canonical-JSON base64url + SHA-256 `signing_material` 在 privacy serializer 后保持可签名。生产集成证据为 `tests/integration/test_ne01_obs_effect_reconciliation_read.py`。

0094 由 `ProcessTaskRuntimeContainer` 拥有 composition worker 与一个 process-shared `LocalGraphExecutionRegistry`。Host、Internal、Scheduler Coordinator 和 worker 的 `GraphCancellationRuntime` 共用该 registry 确认本进程 quiescence，但 durable authority 仍只属于 0092 intent/decision。0094 readiness/codec 锁定 exact frozen columns，额外 `status` 等业务字段一律拒绝；`intent_ref` 物理 identity 必须是 `TEXT NOT NULL PRIMARY KEY`，不允许 SQLite rowid PK NULL/复合 FK 豁免留下幽灵行。worker 以 recovery-scoped owner 扫描并 claim/heartbeat/defer/takeover；这四类变更在取得 `BEGIN IMMEDIATE` 写锁后才采样时钟。它按 exact Graph revision 重建 HostCommand admission 并重入现有 Runtime；若未显式传入 `local_pending`，Runtime 必须先对共享 registry 执行 `cancel_and_join`，本地 pending 非空时不得 advance/finalize，worker 只能 durable defer。仅在 0092 intent `FINALIZED` 且 decision 存在后才 complete/清理 0094 协调行。

当前定级仍是 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`：deterministic terminal exact auto 已默认开启；Cancellation 与 Operation/Effect/Terminal Operator 入口仍默认关闭，真实 canary 未运行。Budget contention 与 0099 窄历史终态机制已证明，但目标部署 inventory/remediation、general/batch/pre-budget recovery、部署密钥/凭据和完整 key lifecycle 未完成。

最新 NE-01 focused split 未扩 policy 白名单。`artifact_store_read_model.py` 只读 seal/promotion/recovery/tombstone，`internal_startup_checkpoint_continuation.py` 只扫描并续接，`internal_agent_sealed_checkpoint_recovery.py` 只重开精确 lineage，`operation_recovery_checkpoint_loader.py` 只验证/接管 canonical checkpoint，均不重跑worker。本轮又把中断分类拆为 273 行 `operation_interruption_runtime.py` 与 focused `task_worker_interruption_store.py`；`node_execution_runtime.py`、`task_repository.py` 当前分别为1495/4597行，低于未变的1500/4600上限。独立 Gate 1–14 已按当前工作树重跑为 `PASS`、`failed_gates=[]`，统一 static scan 也通过；该结构边界不代表 Runtime、签名 Operator 部署或 NE-01 完成，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

Schema readiness 在 Budget 写入前检查，retry schedule/promotion 仍同事务校验 ACTIVE graph/graph-level Budget，backoff 节点可原子取消。若 0089 的物理事务已提交但 migration receipt 丢失，runner 只有在冻结 checksum、列约束与完整索引形状全部成立时才补记 receipt，否则 fail closed。

`ProcessTaskRuntimeContainer` 的生产启动顺序现为 migration → TaskStore 0093/0094/0095/0098/0099 与 Review 0096 readiness → `task_store.recover_startup` → 0099 bounded inventory → 默认开启的 exact auto → publisher 构造 → recovery outbox publish → Outbox 后台 worker → surface runtime activation → cancellation 与 Review continuation workers；恢复抛错会在开放路由前终止构造。ASGI 通过 `execution_owner_id()` 把配置逻辑 owner 与当前 container 的 `recovery_id` 摘要绑定，得到每次构造/启动唯一且不泄漏原 recovery id 的 execution owner。Coordinator 已激活但 worker start 失败时，activation 会反向 stop/join worker 与 close Coordinator；仅全部成功才 unbind，失败保留 bound handle 供 `aclose` 重试。shutdown 给 worker 有界 grace，超时后 cancel 并有界 join；若协程抑制取消，容器有界 fail closed 且保持 Store 开放。Container 构造 Operation/Effect/terminal reconciliation service，并按显式公钥环向共享 TaskStore 注入 verification-only verifier；ASGI 仅在各自独立开关启用时注册 Operator service，cancellation 开启强制要求 Operation/Effect/Terminal gate 并通过 0098/0099 production readiness。Outbox、cancellation coordination 与 Review continuation 都支持过期接管；未部署 proof 的 Effect 仍会让 0094 durable defer。

任何 activation rollback 或 `aclose` quiescence 未完成都以 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED` 锁死后续 bind/activate，只保留 `aclose` 重试路径。Coordinator admission close、drain、`aclose` 任一失败都不关 Store；必须等 Coordinator 与 continuation worker 同时静默，才 drain Outbox 并关闭其余 infrastructure。

仍缺：

- B5C durable cancellation 仍是 production-off shadow；0092、两阶段 fence/final decision、共享生产 verifier、Operator Gateway/BFF/UI 与三 surface 路由已实现，但开关默认 OFF，部署凭据/真实 canary 未完成；
- 历史 `BLOCKED + failure Node` 或 `FAILED + ACTIVE/EXCEEDED Budget` 已进入 0099 exact/signed/quarantine 窄 reconciliation，但外部部署 inventory/remediation 仍未知；0096 已把 `WAITING_APPROVAL` 作为无 Attempt 的合法统一 outcome observation；
- 0095 已补齐 stable-ID Graph revision/payload、deterministic 历史回填、多 revision marker、source-fenced Runtime Event/Review/Operator exact read；仍需持续守护不可解析 producer 整事务失败、legacy payload/event replay、零重复投影、receipt-loss、trigger/index readiness 与多 revision 回归矩阵；
- 0094 scanner/worker 已跨进程 claim、heartbeat、defer、expired takeover 并重入既有 cancellation runtime；Operation/Effect 签名对账均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`，未部署 proof 的 Effect 仍会持续 defer；terminal exact-auto 已完成隔离真实 SQLite restart canary，signed-required case 仍被围栏，Operator HTTP canary 未运行；
- 0093 已以 durable claim/CAS 和可关闭后台 worker 收口跨进程 Outbox drain，仍需保持 expiry 自动 takeover、stale fence、同 aggregate 顺序、非空 backlog 不得误报 DRAINED 和 append/complete 响应丢失故障矩阵；
- 生产 resolver 尚未把现实瞬态准备故障映射到显式 marker；无前置 Review 的到期 retry 已将 promotion 借入 READY claim 的同一决策事务，且 Attempt Outbox 注入失败会整笔回滚；需要新 Review 的重试仍先进入 durable Review，不能绕过审批；
- Budget 高竞争唯一 winner/`ALREADY_EXCEEDED` 已收口，并发额度由 fenced Lease 与 Attempt child reservation 执行；`DEGRADE/REVIEW` 当前在首次持久化前显式拒绝，仍缺预算例外/降级 replacement Graph 的完整状态机；
- 周期 Lease/Reservation heartbeat 已接，0096 已完成 Review continuation；single-node canonical COMMITTED checkpoint、scheduler-owned 多节点 exact COMMITTED continuation、signed multi-node `NO_SIDE_EFFECT` node failure→0090 settlement 与 exact operation-free persisted Attempt 均有各自窄窗，但一般 recoverable Attempt、未知副作用及其它 general multi-node/batch recovery 仍缺。

### 4.2 Budget

`host_brain/task_runtime/budget_runtime.py` 和 TaskStore usage ledger 已为 Host 四路线、Scheduler 与 Internal Agent 建立 graph/node 预算预留及可用的 usage 结算边界；时间、dispatch、显式 provider token/cost、ModelAttempt retry 与工具步数只有在对应 worker 上报 canonical measurement 时才进入账本。Internal post-admission worker 已原生传递 V3；Chat、Internal/Agent Skill 多轮模型和显式声明模型预算的普通 governed Skill 已有 task-bound guard。当前唯一启用的只读 Delegate 还以 exact-zero attestation 关闭了自身内部用量缺口，但入场前 Chat 模型、未来有端口的一般 Delegate、Scheduler handler、legacy 非 governed 调用及所有外部工具仍未覆盖，因此 program-wide 七路线 Gate 的 `budget_enforcement` 继续保持 `PARTIAL`。

D11 只收紧一条已 admission 的 Host `chat` 垂线：`FrozenNodeInput` 的 active Budget remaining 被投影为不可扩张的 node-bound `ModelBudgetGuard`，再由 `FrozenChatRouteExecutor → Surface main-chain → ModelAttemptService` 消费。该调用禁用 cache 和 streaming，按冻结剩余值限制 deadline、provider attempt、总 token、输出 token 与 `model_cost_micros`；provider 必须返回 `token_count / completion_tokens / model_cost_micros` 三项 canonical 整数。量测缺失、超限、timeout 或 provider 失败都会终止当前 guarded call，不再 retry/fallback；Surface 不会把它降级成 `APPROVE`，并跳过额外的 Spark judge。成功 trace 才会被投影为该 chat worker 的 budget observation。

普通受治理 Skill 的旁路也已收紧。Registry 只有显式 `model_budget_required=true` 的 Skill 才会取得 `GovernedSkillModelBudgetRuntime`；执行层从当前 ACTIVE Budget 派生 exact graph/revision/node/attempt guard，不再向任意普通 Skill 注入裸 `ModelAttemptService`。typed runtime 逐次扣减 canonical Token/retry/cost/elapsed measurement，并产生 server attestation；frozen worker 重验 request/Budget/Graph/Node/Attempt 后才将其转换为 `WorkerBudgetUsage`。当前 `emergent_growth_weekly` 使用该路径；缺 runtime、usage、fresh identity 或额度会失败关闭。Agent Skill 继续使用既有 sealed guard。

这不是全局模型成本预支或通用预算账本完成：Thalamus 的普通意图与 Pentest 目标模型分类已被移出准入前路径，入口只会给出规则或默认 chat seed；但 Cortex 准入前模型、Delegate/子 Agent、legacy 非 governed 调用、通用外部工具和跨 route 累计成本仍不在这些 guard 内。没有 provider 报告 canonical `model_cost_micros` 时，guarded 路径选择 fail closed，而不是推算价格或伪造已结算成本。

同一 Reservation 的超限竞争现在有唯一 durable authority：8/16/32 线程及跨进程压力下，只有一个 rejected usage 是 `EXCEEDED` winner并拥有版本 transition/Outbox；其他 sibling 是引用该 Outbox 的 `ALREADY_EXCEEDED` observation，不推进版本，也不参与 terminal winner selection。0090 决策读/replay 会在同一 SQLite snapshot 中重新验证完整 authority，而不是只相信 selected usage ID。

限制：

- legacy 非 governed 路径中 provider 未返回的 token/cost 保持 `UNMEASURED`，不做伪精确估算；D11 Surface 与 declared governed Skill 调用则直接拒绝缺少 canonical token/cost 的结果；
- concurrency 字段没有运行时 charge；
- 超限后的 `DEGRADE`、`REVIEW` 尚未形成完整分支，当前核心路径主要阻断；
- Scheduler 与 Internal Agent 已进入该主链；其 program-wide Budget 策略仍受本节其余限制。

### 4.3 Review/Resume

`ReviewStore`、一次性 token、subject/version/checkpoint 绑定以及 PRE/POST review 状态均存在。Host、Internal、Scheduler coordinator 能进入 `WAITING_APPROVAL` 与 `WAITING_ACCEPTANCE_REVIEW`。迁移 0096 的纯协调表在 `CONSUMED/REJECTED/EXPIRED` task-bound Review mutation 的同一事务内入队；composition-owned worker 以 durable claim/heartbeat/defer/expired takeover 重新进入 immutable HostCommand 指定的原 surface，并在 Node 离开相应 waiting checkpoint 后才完成协调。Review status/token 仍只由 0055 ReviewStore 拥有，Task/Attempt 仍只由 TaskStore 拥有。

PRE Review 不创建伪 worker Attempt：0090 outcome 与公开 receipt 对 `WAITING_APPROVAL` 使用 `attempt_id=null`，其余状态仍要求真实 Attempt。POST continuation 复用原 `SUCCEEDED` Attempt，不重跑 worker。等待超过原 child-grant TTL 时，Application 只能凭 consumed Review 与当前 live continuation claim 派生短期 renewal；worker entry 再验证 owner、claim-token digest、expiry 与 fence，claim 漂移后旧 renewal 立即失效。Review route 与 Operator command 都经 process-owned `TaskReviewCommandService` 写同一 Store，wake 仅是事务提交后的延迟提示。

自动续跑只覆盖 canonical task-bound PRE/POST Review；非 Task subject 仍只返回 decision/token 结果，不会被 0096 worker误当作 TaskGraph 调度请求。Operator UI 必须按该范围展示，不能把局部 `automatic_resume=true` 解释成任意 Review 都会执行任务。

### 4.4 Conversation 与 Delivery

canonical route 在 graph Acceptance 时原子写入 effect intents，避免 admission 前先落 user turn。之后 visible turn、Preference、Life、assistant/Memory projection 与 delivery 由 durable dispatcher 分别提交和重试。visible turn/Preference/Life 各自以 exact durable record/receipt 处理返回丢失；Surface 只有 exact Gateway outbox identity 或 authenticated read-only query 的精确重开结果才可归因为提交，`NOT_FOUND` 保持 `UNKNOWN`。因此正确语义仍是“Acceptance 后按 effect 最终一致并保守处理未知外发”，不是“所有对话与投递在一个数据库事务中原子完成”。

Task Runtime Outbox 已由 0093 建立跨进程 durable claim/CAS，0095 则把 Graph aggregate 身份补成 exact revision。claim 持久化 owner、token、expiry 与单调递增 fencing token，过期后才允许 takeover；predecessor/head 查询按 `aggregate_type + aggregate_id + graph_revision` 隔离，同一 Graph revision 的旧 version 未完成时新 version 不得越过，不同 revision 可独立推进。`TASK_GRAPH` Outbox 必须绑定现存 `(graph_id, revision)`，非 Graph Outbox 不得携带 revision，insert/update trigger、readiness 和 codec 都 fail closed。所有生产 Graph producer 都显式写 revision；Runtime Event 使用 `graph_id@revision` 作为投影 aggregate key，Operator 按 exact revision 读取，不把多个 revision 混为一条事件流。`RuntimeOutboxPublisher` 逐条执行 claim、幂等 event append 与 fenced complete，append 失败则 fenced release 并落 backoff/error；composition 以当次 boot/construction 的 `recovery_id::outbox` 作为唯一 owner，并在 startup publish 后启动可关闭后台 worker。worker 根据 aggregate-head backlog 的最早 available/claim-expiry 时间和有界轮询自动续投；close 与前台 publish 临界区线性化，不允许 close 返回后继续触及 Store。显式 drain 只有 canonical total_pending=0 才返回 DRAINED，仍有 claim/backoff/blocked predecessor 时返回 DEFERRED。`list_pending_outbox` 保持完整 PENDING inventory，ready eligibility 只属于 claim/backlog API。Readiness 是每个 Store 实例的缓存 mode；正常热路径不重扫 schema/全表，只有显式诊断 force-revalidate 例外。Event/claim complete 已提交后的 backlog 读失败会脱敏降级，不会造假失败。0094 另行实现 pending cancellation scanner/coordination；它不复用 Outbox claim，也不取得 0092 业务 authority。

0095 的新 Graph `payload_ref` 是 `taskstore://event/TASK_GRAPH/{graph_id}@{revision}/{version}`；durable codec/replay 为历史行保留旧 ref 兼容，但新 insert/update trigger 一律拒绝 unrevisioned ref。0095 不改写 pre-0095 Runtime Event journal。新写入的 Graph Event 使用 `graph_id@revision`，exact-revision 查询同时通过 immutable `source_ref → task_runtime_outbox.graph_revision` 找回旧式 `aggregate_id=graph_id` 事件并按 cursor 去重排序。`uq_runtime_event_task_outbox_source` 是只覆盖 `source_kind=TASK_STORE_OUTBOX` 的 unique partial index，一条 Outbox 只能投影一条 journal；升级前 event append 已提交但 Outbox complete 未确认的重投，只有 existing event 与 current 或 checksum-reconstructable legacy event 全字段一致时才 exact replay，否则 fail closed。Operator 的 Review 查询也把 revision 传给 ReviewStore，由后者按 canonical、URL-escaped `task-node://<graph>/<revision>/...` subject prefix 精确过滤；Task/Review/Event 三路读模型因此不会跨 revision 混读。0095 readiness/receipt gate 同时冻结 payload trigger 与 source unique partial index。

`created_ts_ms=0` 不再被 `or now()` 改写；source timestamp 确为零时，current revisioned 与 legacy unrevisioned 两种可重建 candidate 都允许既有 journal 使用任意非负时间，因为两条历史 append 路径都可能用时钟替代零值，但除 timestamp 外的全部字段仍 exact。PlanningRecovery 的 evidence 也不再默认合成 Graph ref：`read_scheduler_snapshot` 的 `include_graph_outboxes` 默认 `false`，普通 scheduler/Operator 无额外扫描；failure recovery 显式设为 `true`，在同一只读事务读取 exact revision Outbox，并选取唯一 terminal status/version row 的真实 payload ref。仅旧版或替代 Store 未提供可选扩展字段时才合成 revisioned ref；生产 DurableTaskStore 提供 inventory 后，类型错误、缺行或歧义均返回 durable drift。0100 建立 append-only supersession，`HostCommandRepository.admit_replan()` 在同一写事务重验 same-root `FAILED` predecessor、terminal Outbox 和完整静止事实，随后原子 admission revision+1 并写 supersession。共享 Store/Review 围栏拒绝被替代 revision 的普通 mutation/continuation；response loss exact replay，fault injection 证明全回滚。当前仍只开放保守子集：`BLOCKED`、任意历史 Review continuation、任意 Acceptance Effect 全拒绝，自动 replan 和生产并发 canary未完成，状态为 `PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET`。

## 5. PlanningSearch 的真实边界

### 5.1 公共内核

`host_brain/planning_search/` 已提供：

- `SearchRequest`、`SearchCandidate`、`SearchBudget`、`SearchResult`；`VirtualStateSnapshot` 会对 capability/fact/constraint/source refs 计算不可伪造 digest；
- `PlanningGoal`、`SuccessorCatalogPort`、Cognitive Ranking、Virtual Transition、Reward、MCTS Condition ports；
- UCB 选择、rollout、backpropagation、预算和确定性 trace；
- `CalibrationPrediction` / `CalibrationOutcome` / `CalibrationRepository`：shadow-only、JSON-safe 的不可变 evidence 合同；
- `DomainBenchmarkWorkload` / baseline 与 selected arm / verifier receipt / isolation / budget 的 `DomainBenchmarkPort` 合同；该合同强制 `SHADOW_ONLY` 与 `production_influence_enabled=false`；
- 不包含工具执行、网络访问或状态写入端口。

公共算法本身成立，但“算法存在”不等于“生产规划能力已成立”。

### 5.1.1 D14 Goal Planning foundation

`host_brain/orchestration/goal_planning.py` 现在可以把同一领域的 immutable `PlanningGoal + VirtualStateSnapshot + PlanningDomainPorts` 送入唯一的 `PlanningSearchService`。它只返回带 state schema/digest 的纯 `SearchResult`；不编译、准入、持久化、调 worker 或执行工具。失败重规划也只冻结 failure/accepted artifact refs 并产生新的纯 request，Task Runtime 仍必须通过已有的 durable recovery → `PlanningGraphCompiler.replan()` → admission 创建 revision `+1`。

`PlanningGraphCompiler` 新增可选 `PlanningCompilationContext`：新 goal-driven catalog 只能产生 node-level descriptor，不能夹带 budget、approval/review、workspace、idempotency 或 graph metadata。compiler 把 governor-bound context 写入 immutable WorkOrder，并把 VirtualState digest 同时绑定到 selected-plan lineage、`planner_ref`、operation fingerprint、input pack、metadata 与 compilation digest。旧 `planned_steps` compatibility adapter 仍保留纯合同代码，但已没有 Host production adapter import 或调用点，不能因此被误称为 Host 自主候选规划或执行 authority。

这仍主要是通用接口与 immutable compilation foundation：Host 现在只有由 Thalamus server-owned rule 产生的明确 Research/调研与 Frontend/可用性计划会经 Governor 绑定进入 `GoalPlanningService`；仅建议、禁止工具、普通 Chat、通用目标、Code/Pentest 都不走这条执行入口。没有 Code/Research/Pentest 领域 production cutover、实际成对 outcome receipt，也没有 MCTS 对真实用户任务的收益或 promotion influence。

Pentest 的生产选择调用点现在同时冻结两类数据：真实 PreActionGate 已通过的 executable roots，以及同一次 current-state policy 产生、具有 ToolContract、仅因缺 capability 暂不可执行的 virtual successors。`PentestVirtualExpander` 的 depth 0 永远只返回 roots；后续 depth 才依据 `PentestVirtualState.capabilities` 动态过滤包含 roots 与 successors 的不可变 catalog。实际 args、executor、callback 不进入 descriptor，实际 selected action 始终来自 baseline root；catalog digest/count 会与 initial virtual-state digest、trace 一起进入 0105 shadow calibration。该结构使 capability-unlocking 路径可被评估，但没有把虚拟 capability 写回 AgentState，也没有授予 deferred action 执行权。

### 5.1.2 D16 Code dynamic shadow catalog

`host_brain/planning_search/code_domain_models.py` 把一个 Code 任务冻结为只含 opaque governed ref、digest、目标 artifact、失败测试 ref、预注册 patch 和验证 profile 的 `CodeTaskSnapshot`；`host_brain/planning_search/code_domain.py` 只保留动态 catalog、排序和编译绑定。`CodeSuccessorCatalog` 不读取 `planned_steps`：它从根状态生成 `inspect`，再从已检查状态生成预注册 `patch`，最后生成 `unit verify / rollback`。L2 是确定性虚拟证据排序；只有冻结 `virtual_model_ready=true` 时才向公共内核暴露 transition/reward，因而即使外部要求 `MCTSMode.ALWAYS`，未就绪快照也没有可运行的 L3 端口。编译绑定还会拒绝任意 `execute` root、真实 workspace 字段、未知 root action 或缺失的 `write_files/network` 禁令；唯一允许的 root 策略是 `inspect + generate_report + {strategy: temp_workspace}`。

选择路径随后必须由 `bind_selected_code_inputs()` 再次逐段对照 catalog，才能把唯一的 `approved_candidate_inputs` 写进新的 `PlanningCompilationContext`。它拒绝 raw/file URI、超出 frozen target/test/change bound 的 patch、legacy `intent_draft` 覆盖、goal/state/objective 漂移，以及没有 durable predecessor lineage 的中间虚拟状态。`GovernedPlanningExecutionService`、显式 replan 和 recovery 也会透传该 context，并在搜索前要求 `approval_ref == parent governor grant`。

这不是 Code 执行切流：不会打开 worktree、读取源码、调用模型/工具/测试/provider、admit TaskGraph 或写 workspace；`allowed_path_refs` / `forbidden_path_refs` 只是冻结证据，不是 live ACL。没有历史成对 A/B、独立 verifier receipt、0106 comparison ledger 或 promotion gate，所以仍不能声称 Code 收益或生产 influence。

### 5.1.3 D17–D19 trusted-process private-bare paired-worktree、durable ledger 与受控 pytest 验证

`host_brain/planning_search/code_benchmark_sandbox.py` 不复用 W2 的主 checkout source：它要求服务器注册一个在净化 Git 环境中也干净、且拥有非链接、非共享 `.git` 目录的普通 Git source；source 的 local Git config 与 checkout content 仍是受信输入。在受保护根目录之外用 `git clone --bare --no-local` 建立一次性 private bare repository，设置私有空 hooks path、移除 remote 后，从同一个 frozen commit 给 baseline 与 selected 分别建立 detached worktree。Git 子进程丢弃继承的 `GIT_*` 配置，禁用 system/global config 与交互提示、强制关闭 `core.fsmonitor`，仅允许 `file` transport，并跳过标准 LFS smudge；这只是降低 Git 继承面，不是网络或进程隔离。

workload isolation 是精确六字段 manifest：`sandbox_mode`、`source_ref`、`source_commit`、`path_policy_digest`、`executor_trust` 与 `network_isolation`；任何额外字段（包括潜在主机路径）或旧 `network_enabled` 都会拒绝。governed ref 同时拒绝 `file://` 和 host-path-shaped 值。两个 arm 的 `CodeBenchmarkWorkspace` 只接受唯一的小写 portable repository-relative `read_text/write_text/exists` 路径拼写：拒绝 padding、大小写 alias、Windows trailing-dot/space、reserved device、`.git`、绝对路径、`..` 与 symlink component。private root/artifact 同样拒绝 symlink 与 Windows reparse point；cleanup 对发现的 direct-child link 只 unlink/rmdir link 本身，绝不枚举或递归删除外部目标。tracked binary diff 与 untracked regular-file content 各自进入 receipt digest，ignored artifact 直接失败。runner 清理 private worktree/bare repo 后再次比对受保护 source 的 HEAD、refs、tracked/untracked/ignored status、worktree inventory 与 Git control metadata。

这份 API 是受信 adapter 的机械防错层，并非权限隔离：executor 与 verifier 都在当前 Python process，能传入的只能是仓库自有、受审计的实现；绝不能把 model、skill/plugin 或 hidden-oracle 代码交给它。D17 的口径固定为 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` 与 `network_isolation=NOT_ENFORCED`，不是 OS/container/tenant/filesystem/network sandbox。

D18 将成功路径收口为：先以 `CodeBenchmarkWorkloadRegistration` 冻结 workload、六字段 isolation 和**声明性** execution budget；再以 `CodeBenchmarkRunSpec` 冻结 baseline/selected 两条 candidate path。这两项 preregistration 是各自独立、append-only 的提交，不承诺与后续运行同一事务。D17 runner 只有在 `finally` 删除 private bare、两个 worktree 和 run root，且受保护 source fingerprint 前后一致时才返回路径无关的 `CodeBenchmarkCleanupReceipt(status=VERIFIED_CLEAN)`。随后 0106 的**terminal-evidence transaction** 才在一个 SQLite transaction 写入两个 arm evidence/outcome、cleanup 和 terminal receipt；所有行不可更新/删除，FK/role/source/policy/cleanup gate 在 SQL 中复核，JSON/digest/对象重放和物理 schema drift 也 fail closed。terminal receipt 只是 `EXPERIMENT_EVIDENCE_ONLY`，不能选 winner 或影响 production。

D19 只在 registration 显式绑定同一 `CodeBenchmarkVerificationPlan` 时生效：计划只接受冻结的 `python -m pytest -q <小写仓库相对测试路径>` argv；两 arm 的最坏测试次数与命令 timeout 总和必须先被 registration 的 `max_test_commands` / `max_wall_time_ms` 覆盖。`ControlledCodeBenchmarkVerifier` 在每个 detached worktree 根目录以 `shell=False` 启动受限子进程，去掉 `PYTHONPATH`、关闭 pytest 自动插件加载和 cache provider，并将执行 argv digest、exit/status、受限 stdout/stderr digest/长度与耗时封为 path-free verifier receipt；策略实际解析的当前 Python 解释器会先被核验，再持久化回冻结的 portable argv digest，因此 receipt 不携带主机路径。`PASS` 必须等于 exit code `0`；同一 arm 会在第一个 child 可能启动前预留冻结 command count，因此 timeout/policy error 也不能在该 pair 内重试并绕过 `max_test_commands`。每条 receipt 都精确绑定 run/arm/plan/budget，D18 terminal reader 重验两 arm 合计 test/wall 不超过预注册 paired limit，并要求 outcome、verifier 与 controlled receipt 的 PASS/FAIL 完全一致。verified cleanup 后，成功与失败都形成 terminal experiment observation；这避免固定 cohort 丢弃失败样本，但 `COMPLETED_CLEAN` 只表示证据完整和环境已清理，不表示测试通过。

它依然未接 Host、TaskGraph、AgentExecutor、WorkspaceGrant 或生产 route；D19 的 child 只是同一主机受信 workspace 的 subprocess，不是 hidden-oracle 或独立信任域。没有 process-tree kill、CPU/内存、container、OS 文件系统、tenant 或网络隔离；本地 source、pytest 测试及用户 site 依赖仍是受信输入，且为了 Windows user-site discovery 保留 `APPDATA`，没有设置 `PYTHONNOUSERSITE`，因此更不能误称 hermetic Python 环境。`max_model_calls`、provider/tool/token/model-cost 和通用 Durable Budget Ledger 没有接入，且没有 durable benchmark lease、crash cleanup recovery/retry worker、真实历史 cohort、统计比较或收益结论，因此仍不能宣称已跑真实模型修复或完成 Code production shadow。

固定 Code 收益层位于 `contracts/planning_search/code_benefit_cohort*`。它要求恰好 20 个唯一 workload/run/terminal receipt，同 source commit/path policy、相同 execution provider/model/profile/budget，且 baseline/selected 使用独立模型 execution、fresh worktree、hidden-test verifier 和 static verifier。evaluator 只接受 +10pp pass-rate，或 -5pp 非劣且至少 15% 成本下降，同时 hidden/static rate 和 mean diff-risk 均不退化。当前只有纯合同与本地定向证据，没有 cohort SQLite owner、真实 provider/model、20 项运行或 promotion，因此状态仍为 `UNPROVEN`。

### 5.1.4 D20a～D20c Research frozen-corpus、citation 与外部模型证据边界

`host_brain/planning_search/research_domain_models.py` 的 `ResearchTaskSnapshot` 只冻结规划可见的任务/corpus governed refs、`source_id`、source ref、content digest 与 authority/relevance/estimated-read-cost hints；它没有 dedicated 本机 locator、source-body、answer-key、verifier-decision、模型客户端或 executor 字段，constraints 会拒绝这些命名 key，但任务文字不是内容级 DLP。`research_domain.py` 根据这份不可变元数据动态生成 `query`、`quick-answer`、`primary-source`、`independent-source`、`cross-check`、`resolve-conflict`、`stop` 的合法虚拟 successor；L2 只按公开 authority/relevance/cost hints 排序。只有快照冻结 `virtual_transition_ready=true` 时，adapter 才给公共内核提供 virtual transition/reward 与 Conditional MCTS 条件；否则没有 L3 rollout ports。选中路径不会读来源、产出回答或进入编译/admission。

`research_benchmark_corpus.py` 把实验输入读取留在另一个受信边界：`FrozenResearchCorpusLoader` 只从一个可信实验根目录下、`FrozenResearchCorpusManifest` 列出的 canonical repository-relative 普通文件读取。它对 root、每个目录 component、目标文件及打开后的 descriptor 做 link/symlink/reparse/type/identity 检查，读前/读后比对大小、文件状态，并在读完后核对 SHA-256 digest；在稳定的受信输入状态中，绝对路径、`..` 遍历、ADS colon component、case alias、root escape、未列 source、link/reparse 或任何元数据/正文漂移都会失败关闭。其 `public_manifest_receipt()` 和 document receipt 只保留 source id/ref/digest、字节数与 authority tier，绝不输出根目录、relative path 或正文。

`research_benchmark_verifier.py` 实现 D20b 的机械闭合：构造时将 `ResearchTaskSnapshot`、`FrozenResearchCorpusManifest` 与 `ResearchCorpusBindingV1` 的 corpus/source id/ref/digest/tier 做精确比对；每次 `verify()` 都重新构造 `FrozenResearchCorpusLoader`，不会接收 executor 的 loader、document 或 source map。它从新读取的 bytes 校验每个 `ResearchCitationSpanV1` 的 source membership、UTF-8 byte boundary 与 SHA-256 span digest，并只输出无路径/无正文的 verifier receipt digest。语料漂移、manifest/snapshot drift、未列 source、半个 UTF-8 字符、重复 span 或错误 digest 都失败关闭。

0107 `SQLiteResearchShadowLedger` 也不再只依赖“两个 arm 行已经存在”的 SQL trigger：普通 repository path 在 append 时只接受完全相同的 arm replay；terminal insert/replay 会重开 canonical registration、RunSpec 和两个 arm row，交叉核验 registration digest、实际 `BASELINE/SELECTED` arm order、baseline/selected plan、model/verifier JSON 与 evidence digest。任一漂移失败关闭，完全相同的 terminal replay 才返回原 receipt ref。新 paired RunSpec 不再接收调用方给出的 order 或“已随机化”标记：owner 在 SQLite 写事务中以 cohort key digest 建稳定 identity，精确 replay 在调用 CSPRNG 前返回原规格，只有新 identity 才以 `secrets.randbelow(2)` 决定实际 order；raw key 不持久化。旧规格和既有 terminal receipt 保持只读重开，但不能产生新的外呼或 terminal receipt。0121 另以 immutable operation/event chain 为每个 `run_id + arm_role` 预留 deterministic execution ID：fenced claim 才能落 `EXTERNAL_CALL_STARTED`；重启时 canonical audit 为空才可显式授权重试，存在 audit 而没有 0107 evidence 必须落 `RECONCILIATION_REQUIRED`，已经持久 evidence 才能落 `EVIDENCE_PERSISTED`。`ResearchPairedExecutionRunner` 只读取这份 owner-randomized RunSpec，不接收调用方替换的 plan、budget 或执行顺序；它保留 raw prompt/model output 在内存，且在外呼前要求 `ResearchCitationOutputParserV1` 的固定 JSON envelope digest 精确匹配 profile，parser 丢弃 answer 正文、只向每臂 fresh verifier 提供 citation span。最终回执还会重新核验 profile prompt digest、工具调用数、token 与单臂/成对时延预算。已有 evidence 只重开不再外呼，started-but-no-evidence 必须先按审计状态恢复或冻结。这仍是 owner-side shadow evidence 防线；旧 schema 的 append-only 触发器没有因此变成生产 promotion authority。

`attested_external_research_adapter.py` 是 D20c 唯一的模型边界 adapter：它不能接收 caller 指定的 provider、model、base URL 或 cache 控制，并会先经 `AttemptAuditLookup` 重开 execution ID；任意已有 canonical audit row 或 lookup 异常都视为未知外部效果，拒绝再次调用并要求 reconciliation。只有空审计的 ID 才能向已 composition 的 `ModelAttemptService` 发起带 execution/arm identity 的前台请求。生产 `VaultBackedSingleAttemptAdapter` 在实际 transport 调用点解析 server-owned endpoint，生成脱敏 identity digest，并只把调用耗时、digest 和保守 external-HTTPS decision 交给 canonical audit；原始 endpoint、query、userinfo 与凭据均不持久化。返回结果必须为一个非缓存成功 attempt，且 final target 与 attempt 的 adapter/provider/model、输出摘要、rendered prompt digest、canonical tool/token/成本、provider latency、transport identity 和总 latency全部精确一致；随后它再重开同一 request 的 canonical started/terminal pair，要求 terminal row 与结果完全一致且 audit 也包含同一 provider latency/transport identity/external decision，才会构造 `ExternalProviderRunAttestationV1`。该对象作为完整值嵌入 `ResearchModelExecutionReceiptV1`，而非由字符串 URI 代替。多 attempt、cache、本地/私网/Codex CLI/未知 transport、route/output/audit drift 或任何缺测量均失败关闭。历史无 transport identity 的 V1 attestation 可只读重开但不再 benefit-eligible。请求字段本身不能覆盖 route-owned retry ceiling，因此这里仍是结构化代码边界，不是已验证的外部调用事实。

这不是生产 Research route、TaskGraph、Host、BrowserWorker 或网页抓取接线；没有 production prompt/workload、真实 provider credential execution、独立信任域 verifier、paired cohort、成本/延迟对照、收益统计、winner/promotion authority，也没有 OS/tenant/sandbox。成对 runner 的 in-process canonical-model double 只覆盖本地恢复机械链，绝不能作为 external provider attestation 或 cohort evidence。adapter 的 SQLite audit 重开只证明本地持久审计可对照，不是外部 transport 或 production composition 证据。loader 的根目录和 manifest 都是受信实验输入，其 before/open/after 检查不是 hostile concurrent replacement 的原子 TOCTOU 防线；byte-span validity 也不能据此推导事实正确性或来源独立性。因此 D20c 为 `ACCEPTANCE_PENDING_EXTERNAL`，Research benefit 仍为 `UNPROVEN`。

`contracts/planning_search/research_benefit_cohort.py` 现承载后续真实验收的中立协议和纯 evaluator。它把单臂质量判断从 citation receipt 分离：hidden verifier 拥有不同 execution/attempt，绑定 arm response digest、profile/rubric 和自身 provider attestation。本协议不接受调用方调整 cohort 数或阈值，固定 24 项、三类主题、本地/Web 覆盖、10,000 次 deterministic paired bootstrap、质量/citation/严重错误/总成本/p95 时延 Gate；模型和 verifier 成本/时延一起计入。结构或身份漂移抛错，实验性能失败则生成不可 eligible 的 reason-code report。即使 report 通过也仅能进入 proposal，不能直接影响生产。目前没有 SQLite cohort owner、真实 workload/provider 或报告持久化，因此这一模块是可执行验收算法，不是已完成验收。

### 5.2 Host Plan

Host production adapter 不再调用 `host_brain/orchestration/plan_intent_search.py`。该文件保留为纯 compatibility/contract adapter：它可以检查旧 `planned_steps/steps` 的合法拓扑，但无法由 Host 接受、构造上下文或 admission。当前 Host 只接受显式 `CAPABILITY_SEARCH_V1`，从 app-composed Registry 冻结竞争候选，选中后才得到 immutable graph；顶层或 `args` 内的旧执行列表直接拒绝。

```text
server-owned task/tag/risk → frozen Registry competition → L1/L2/conditional L3
  → selected-only inputs → immutable skill → skill → chat TaskGraph
```

这只成立于当前 Research/Frontend 的窄 catalog；它不代表通用目标分解、Code/Research/Pentest production cutover、真实 provider/cohort 或收益结论。

#### 5.2A Capability Planning risk authority

`host_brain/orchestration/capability_plan_search.py` 对 `CAPABILITY_SEARCH_V1` 先验证完整 Governor approved binding，再把 `planning_max_risk_level` 与 binding `risk_level` 取最小值作为冻结 catalog 上限。Governor decision ref 和 server-owned 两项执行步数同时进入 `CapabilityPlanningSnapshot.source_refs`/virtual-state digest；缺失或畸形风险字段在 L1/L2/L3 前失败关闭。该切片防止已绑定 intent 借更宽的风险字段扩展候选权限，但仍只覆盖 Research/Frontend 的 server-owned 路由规则。

### 5.3 Pentest

Pentest L1/L2 已接入公共 service，L3 使用纯虚拟 transition/reward，真实工具执行不在 rollout 内发生。但当前 L3 有四个限制：

1. 根节点前先用真实 `PreActionGate` 过滤候选；
2. virtual expander 只排列根节点已可执行的同一批候选；
3. 无法表达“A 获得 capability 后才解锁 B”的后继生成；
4. 0105 的 `SQLitePlanningCalibrationRepository` 已可跨重启保存 immutable prediction/outcome、拒绝 identity/outcome drift，并验证 table/index/FK/trigger 的物理语义；但只有显式注入 `PentestAgentLoop` / adapter 的实例会使用它，默认兼容 ledger 仍在内存。

shadow 对账的还是实际执行的 baseline 根动作，不是 L3 未执行替代路径的真实结果。候选 target 会在生成 fingerprint 前先收口到授权 scope；D13 写入 durable repository 的 outcome 仅是脱敏 `UNVERIFIED_RUNTIME_RECORD` 投影，并不伪装成 Task/Run receipt。库 busy/schema/drift 会产出可观察的 `mcts_durable_calibration_unavailable`，但因为它只是可选 shadow evidence，不能在真实 baseline 执行后抛出中断。因此 adapter 已强制 production influence 为 `DISABLED_UNTIL_DURABLE_PROMOTION`；无论内存或 durable evidence 的阈值如何，均不能改变真实动作。未来必须同时具备 receipt-bound counterfactual evidence、动态 successor 和已批准 promotion gate，才可另行引入受控切流。

### 5.4 Code / Research

`tests/fixtures/planning_search_shadow/` 与 `docs/45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json` 明确声明 `SIMULATION_SHADOW`、`production_benefit_claimed=false`。D14 新增的是配对 workload/arm/outcome 的 shadow contract；D16 补了 Code 动态 catalog 和编译单测；D17 用临时测试仓库实际执行 private-bare clone、baseline/selected 独立 detached worktree、受信文件 API、verifier ref 与 cleanup，并在 cleanup 后复核受保护源的 Git control metadata fingerprint，漂移即失败。D18 将成功的 clean pair 以 0106 workload/spec/arm/cleanup/terminal receipt 分段不可变保存，重启可精确重放。D19 让预注册的固定 pytest 子进程实际在两个 worktree 中运行并 durable round-trip 其 path-free 回执；它没有把同主机 child 或两项测试预算伪装成模型/工具预算或隔离 sandbox。D20a/D20b 补 Research 的公开 metadata catalog、可信实验根/manifest 的安全普通文件读取，以及 fresh-loader 精确 handshake/UTF-8 span verification；D20c 只把已由 canonical audit started/terminal pair 重开的严格完整 ModelAttempt 结果转换为嵌入式 provider attestation。它没有真实 credential/provider execution 或 production prompt/workload，仍不产生真实来源获取、独立信任域 verifier 或 paired outcome。仍缺真实模型/工具、隔离的 hidden oracle、历史 cohort/statistical comparison 或收益结论；Research 为 `ACCEPTANCE_PENDING_EXTERNAL`，benefit 仍为 `UNPROVEN`。

Research persistence 的物理结构已从 1788 行单文件拆为 1271 行 repository owner、512 行 canonical codec 与 108 行 typed projection/error。repository 仍是唯一 SQLite connection/transaction/UoW owner；codec/models 不写数据库。公开导入和 exact replay 兼容不变，Gate 3 因真实拆分通过而不是获得例外；全量静态仍因 Gate 1 的 application/persistence 与 app/composition SCC 为 `FAIL [1]`。

### 5.4.1 D15 governed Agent 模型端口桥

此前 Host 的 `agent_worker → codex_runtime` Skill 路径虽然把 `ModelAttemptService` 放入 governed execution context，却直接构造裸 `AgentRuntimeExecutor`，所以会在首次模型调用前以 `AGENT_MODEL_RUNTIME_NOT_COMPOSED` 失败。D15 仅在 CapabilityBroker 已验证 sealed task authority 后，才由私有 executor adapter 读取 composition 提供的 `ModelAttemptService`，并从同一服务对象派生 `PromptRegistry`；调用方不能在 payload 中注入服务，缺 authority、模式或端口统一以 `AGENT_RUNTIME_PORTS_NOT_COMPOSED` 关闭。

这仍不是 Code PlanningSearch 写入切流。独立 D16 benchmark 编译出的 `agent_worker` 只是 shadow descriptor，尚未把 Code cohort 的 selected plan 准入 Direct Skill TaskGraph；但普通受治理 Direct Skill 的 `agent_worker` 若经真实 Host admission、risk-4 Review 和节点 authority，已经可以取得 task-bound app-local WorkspaceRuntime 并在 Acceptance 后提升工件。两者不能混为收益证明。AgentExecutor 的 ReAct `ModelAttempt` 仍未接入 D11 `ModelBudgetGuard`，`run_skill` / `dispatch_sub_agent` 继续退出发现面，也没有通用工具提案 `graph_revision + 1` compiler。因此 D16 仍无真实 PlanningSearch 模型修复、cohort uplift、预算 receipt 或 production canary。

## 6. 状态与 authority

| 状态域 | 当前权威写入/存储 | 备注 |
|---|---|---|
| Host command / Task / Attempt | `HostCommandRepository`、`DurableTaskStore` | canonical 四路线权威 |
| Scheduler | Scheduler repositories + Durable Task Runtime + shared Acceptance Effect dispatcher + 0117 Clock retry authority | occurrence 与 Task lineage 持久化；DUE→OCCURRENCE owner/fence 原子转移，执行 lifecycle renew/heartbeat；worker 只提交 operation plan，真实 handler 在 Acceptance 后执行。definition/admin、mutable batch freezing 与 canary 尚未收口 |
| Internal Agent result | protected stage + projection recovery envelope + explicit checkpoint UoW + lifecycle survivor + terminal artifact reconciler + `workspace_write_promotion.py` + `internal.agent.result_commit` + 0116 lifecycle | post-admission worker 原生消费 immutable `WorkOrderV3`，V3/legacy metadata graph drift失败关闭；外部 v2 只在 ingress 兼容。首次 activation 与周期 survivor 可从受治理单节点 sealed checkpoint 重建唯一 projection 并继续 accepted path；A/B/C分别原子、WorkerProjection只有一个INSERT owner、response loss只读重开。write path 密封 manifest/patch，Acceptance 生成 deterministic accepted HEAD 并经 0116 晋升；真实 SQLite+Git 验证 exact replay、漂移拒绝、事务回滚后新 Runtime 接管及 source branch 不变。终态worker处理sealed abandon/cleanup，并对共享合同允许的两类 exact signed pre-checkpoint cancellation 执行 claim-before-cleanup。Direct Agent Skill promotion另列且不继承此恢复语义；一般 unknown effect、其它 unsealed crash-left/取消 closeout、general `settle_worker` crash bundle、Domain Pack/其它 Skill、handle-level TOCTOU、OS隔离与七路线验收也未完成 |
| Review | `ReviewStore` + 0096 `ReviewContinuationStore` | token/decision 为唯一业务真相；task-bound continuation 自动、可 takeover，PRE 无伪 Attempt，POST 不重跑 worker |
| Runtime Event / Outbox | canonical SQLite journal/outbox | 关键事件与投影可恢复 |
| Host mode HardState/Policy | app-local `HostHardStateOwner` + 0115 journal；`HostModePolicyOwner` + 0129 journal | 两个聚合各自 event/head 同事务、expected revision CAS 与 exact Host lease/fence；完整 digest/revision replay后恢复。mode、auto-rules、last-interaction 与 event outcome 均有 canonical owner；production canary 未跑 |
| Model invocation | `ModelAttemptService` | 统一 route/attempt/trace 主干 |
| Capability execution | `CapabilityBroker` + execution journal | 签名和授权主干存在 |
| Digital Life | Life command/event/reducer/projection | 单核资产已建立 |
| Memory / Canon | typed repositories + governed apply | projection/cache 不应成为 writer |
| Host embodiment dispatch | app-lifespan `HostEmbodimentRuntime` + `EmbodimentOutboxRepository` | 没有 module default；outbox 按 runtime scope 过滤，同 scope 由 durable Host lease/fence 选出唯一 writer，Edge reconnect credential 持久保存；lifespan 独立 heartbeat 续租，不依赖默认关闭的 governed Scheduler |
| Edge durable receipt | Edge SQLite receipt store + HMAC/nonce receipt query v2 | owner/current session/lease/fence 与 command/idempotency key 精确绑定；legacy ownerless 返回 `NOT_FOUND`；`IN_DOUBT` 只读查询后用 identity+digest CAS 收口，相同 digest 重查不重复落审计，`NOT_FOUND`/失败不重放 |
| Edge physical effect | Edge actuator + durable receipt | loopback TCP/HMAC/SQLite/restart replay 已测，但真实硬件/设备效果仍 `NOT_TESTED` |
| Operator read | `QueryProjectionStore` | Query Plane 以 projection 为主 |
| Task/recent read routes | Durable Task projection + conversation history | 只读，不创建平行任务状态 |

## 7. Composition 现状

state-helper 收口将 `UserPreferenceStore` 和 `ConversationExperienceStore` 的 ledger 变为强制构造期依赖，执行侧只读既有 belief/lesson；旧 Conversation Experience 演化写入仍须由 NE-06 proposal/activation owner 承接。该切片的 commit-bound inventory 为 `3/15/6、Container48/2/45`；现行生产 locator 已推进为 consumer/import/module/field/lock/ref 全 0，ResourceManager status 由 0123 journal 持久化。外部 Chroma/Neo4j namespace 与其它外部资源恢复仍未闭环；这不代表 Cortex、正式 evolution activation、Tribe、Chat Acceptance、Worker/Obs 或全局对象图已完成。

普通 Cortex 检索已从业务 helper 的 locator 回读迁入 Host scope：`AgentRuntimeApplicationPorts.run_retrieval()` 显式传入 conversation history、preference-pack builder 和 recall factory，后者在 composition root 内绑定 ledger、Life memory 与 Bond Neo4j。`chat_retrieval_helpers.py` 不再 import `process_services`；该读路径缺 authority 时只保留既有降级，不重新发现全局服务。此切片只减少读取依赖的平行 authority，不将 Chat 的可见写入、Acceptance、Delivery 或外部副作用表述为完成。

Chat 侧的当前窄收口是明确的：Host lifespan 把 `AgentRuntimeApplicationPorts + Life authority` 固化为 immutable `SurfaceRuntimePorts`，把 history/turn/topic/tactic 固化为 `ConversationContextReadPorts`，并把 Surface role resolver 绑定同一 app-local RouteCatalog；普通 Chat、Gateway 与 ASR 从 ASGI 接收 `InputRuntimeApplicationPorts`。Host root 独立持有 Graph/Routing、Memory publication、Authority、Bond、Memory/State 与 Governance runtime owner；缺端口均 fail closed。生产 locator 已物理清零，ResourceManager status 由 0123 journal 持久化；外部 Chroma/Neo4j namespace 与其它外部资源恢复仍未闭环，该证据不覆盖事务化 Chat、Tribe app-local 化、generic Delegate 或全路线 Runtime。

`ProcessTaskRuntimeContainer` 是较好的显式 typed composition：它拥有 migration、TaskStore、ReviewStore、CommandRepository、EventStore 与 lifecycle。

Host ASGI 必须先让该 container 完成 canonical migration/readiness，再构造 `QueryProjectionStore`、具身 runtime 与其余依赖这些 schema 的应用对象；fresh database 因此会先得到 canonical migration 的真实结果，不会被 projection 的次生 schema 错误抢先遮蔽。具身 runtime 的 durable authority 由 lifespan 自有 heartbeat 续租，不能依赖默认关闭的 governed Scheduler；shutdown 先 cancel/join heartbeat，再精确释放 runtime lease。任一步失败都发生在 lifespan yield 前，路由不会开放。

但全局对象图尚未收敛：

- `host_brain/composition/process_service_container.py` 在模块级创建 `_PROCESS_SERVICE_CONTAINER`；
- 两轮静态调用图清理已删除 `retrieval_orchestrator`、`retrieval_pipeline`、`context_compressor`、`episodic_memory_graph`、`consolidation_trigger`、`skill_composer` 6 个生产零消费者槽位及 4 个死 accessor；
- 第二轮同时删除 `process_services` 上 7 个无仓内消费者的宽泛重导出和两处 `retrieval_pipeline` 测试 reset；
- 生产 container/facade 已物理删除，当前机械库存为 consumer/import/module/field/lock/ref 全 0；pytest-only bridge 不属于生产对象图；
- `process_runtime_services.py` 已删除；`process_services.py` 仍暴露 canonical ledger、Episodic/LifeMemory 与 Model/Capability Runtime 等兼容 accessor；
- #89、ReAct、视觉、HardState、Lifecycle/Tribe、Input、模型端口、AgentRuntime、Context、内部旁车与此前 wrapper/state/runtime 的库存均只作 commit-bound 历史；当前生产 locator 已物理清零；
- fresh 全量静态检查保持 `PASS`，`failed_gates=[]`，Gate 1/6/10/11 均已通过；Gate 11 的唯一生产 `ToolBroker(...)` constructor 位于 `host_brain/tools/broker.py`，由 app-local Tool boundary 所有。视觉切片没有恢复 ToolBroker/MCP/Skill/Delegate/registry/closure 直执行 authority；
- `host_brain.app` 仍承担部分真实 service factory 和 wiring。

D1 已先关闭路由级最直接的多 app 串扰：`HostRoutePorts` 是每个 FastAPI app 捕获的 frozen provider bundle，Canon/Tool/Bond 均由 `create_*_router(ports)` 构造；原 module setter/global provider 和 Tool fallback 到 `process_services` 已删除。同一 bundle 还经 Security composition 传入 Canon/Tool/Bond 的 OBS command adapter。双/三 Host app 交错请求、构造和关闭均只命中自己的 fake service。这个结论仅覆盖三类路由；OBS 的 Capability Forge、Scheduler/Review runtime lookup、Life/Tribe 与其余 process locator 仍在。

D2 把独立 Obs 的 `create_obs_app()`、`ObsApplicationPorts` 和 `ObsApplicationContainer` 变成显式应用级 scope。D3 最初注册19个 producer callback（2 core + 17），并先迁移17个生产端点。D4 将 BioOps 8、Life 3、Operator live 4、summary/runtime/resource 4，共19个 GET 的 `read_process_projection` 链改为必需显式 store 参数；36个已识别生产投影端点（35 HTTP + 1 WebSocket）、已识别stats查询与sessions/prompt response cache接入当前app的QueryProjectionStore/View或app-owned cache。D5 又修复此前漏扫的5个 `/api/ui/*` GET，以及Security runtime/assets/incidents、Skill Catalog stats、Life Evolution snapshot、Semantic Recall stats、Governance snapshot共7个实际挂载GET；双Obs app/双SQLite测试证明这些路由不交叉读取。6个 reconciliation 端点另从 app.state 使用显式 repository authority。

每个 `ObsApplicationContainer` 自有的 app-local Projection daemon 保持 single-flight、关闭 join 与 retry handles；模块级 `_default_daemon/register_projection_job` 兼容链和导入期注册已删除。当前21个producer的source-mode为 `DURABLE_READ=6 / STATIC_READ=3 / DURABLE_READ_PARTIAL=8 / AUTHENTICATED_HOST_READ=4 / TYPED_UNAVAILABLE=0`，21/0与认证声明都不是运行成功率。四个认证job分别物化Capability Forge、Semantic Recall、Governance与Organ/Tribe；Semantic/Governance各自读取独立versioned endpoint/type/cache/evidence和故障域。Life Body现在是partial producer：它只有在同app runtime/overview/insights/trace roots都健康且未过期、canonical ledger有界查询可用时写健康快照，否则写typed `SOURCE_UNAVAILABLE`。8个partial及认证来源的运行态失败可能性使health保持`DEGRADED`。

Host为每个lifespan生成一个五合同共享的`source_instance_id`，mode/capability/semantic/governance/runtime各自维护锁内单调`source_revision`。Obs通过通用Query Projection SQLite的`compare_and_upsert`单事务CAS持久当前lineage，并从immutable journal读取完整历史epoch；同epoch的revision、observed、evidence不得回滚，同revision payload不得漂移，任意已退役epoch重放都会拒绝，不受固定窗口限制。合同仅递归校验精确名为`timestamp`或以`*_ts_ms`、`*_at_ms`结尾的嵌套时间不晚于Host observed；Obs再以本地接收时钟允许最多5秒skew、拒绝超过120秒的正证据，并将freshness夹到本地接收时间。Host composition、Host route和Obs receiver各执行公共序列化与合同复验；合同不是字段白名单，Organ `key_metrics`另使用逐agent显式字段及数值/布尔allowlist。Transport固定五条路径、禁代理/重定向、限制1 MiB，并以一个monotonic总deadline覆盖DNS解析、连接、响应头和分块正文。真实loopback已覆盖五来源传输，但不代表部署长期健康。

Host source closure只在ASGI lifespan内预热、捕获并在finally清空；它底层仍读取`get_ledger/get_capability_* / get_semantic_recaller`等process authority，所以不是多Host全对象图隔离。Tribe state reader 已改为从当前 app 的 `TribeRuntimeScope` 捕获，不再读取 `require_process_tribe_runtime`。Capability Forge durable proposal由ledger直接执行newest-per-proposal查询。缺token、Host或有效证据时相关文档保持`SOURCE_UNAVAILABLE`；Organ只是allowlist后的瞬态证据而非Life authority。D7b的`LifeBodySourceReader`不读取这些Host closure，而是聚合同一app已物化的四个公开root和`LifeBodyQueryRepository`有界lineage；当前 sys mode 也来自该 durable projection，而不是读取进程环境或 app-local内存 owner。两app两SQLite、Host ledger关闭后的冷读、root missing/invalid/stale及content/private字段不落盘均有回归。Ledger-stats按公开时间列与durable行身份倒序读取最近5001行并物化最新5000行；`truncated`只标记更旧尾部，不再使高流量表永久503。D6 Memory/VTS冷读与0103边界继续有效；压缩率、蒸馏失败率和Edge物理回执覆盖仍明确不可测。callback仍不得调用`process_services`。

D7 静态扫描现有 `obs_routes*.py` 时，`process_services` 与 `_get_ledger` 的直接词法引用均为0；这个数字只描述该文件集合。当前 Host/Agent/Context/Startup/Multimodal/heartbeat 端口显式传入依赖，生产 locator module/field/lock/ref 也已为 0；ResourceManager status 已由 0123 journal 供 Obs durable read。外部 Chroma/Neo4j namespace、其它外部资源恢复和其他兼容实例仍未闭环。无 Task-bound Preview 的 Gateway voice Effect 为0。

D7b还把Direct Skill worker的resource manager、episodic store和semantic recaller从执行期全局getter改为Host lifespan捕获的实例快照；两个Governed runtime交错执行、关闭其中一个以及全局poison替换都不能改变另一个端口绑定。Durable node grant与child intent先验证，随后按Skill语义预检所需后端；缺依赖返回稳定失败，不进入低层executor，也不生成accepted或成功Skill receipt。Life Body把prompt injection/high-risk以及Gateway/capability风险映射为病理、免疫状态和诊断动作；根事实未变时沿用source identity，仅刷新freshness。D7c又把`obs_application.py`和`obs_projection_jobs.py`移入app层，NLE composition→app违规从85降为0且没有新增allowlist。这个证明只覆盖Direct Skill和Obs assembly：Chat/Delegate的dispatcher、`PROCESS_LIFE_PROVIDER`、Host source底层authority和其余Worker对象图仍未迁移。

Host lifecycle 以 `HostLifecycleContainer` 统一同一 ASGI app 的 monitor/resource/mode/context owner。0115 将 mode authority 接到 canonical SQLite；0129 将 auto-rules、last-interaction 与 mode-event outcome 接到同一 Host runtime authority 下的独立 canonical journal；0137 以相同 runtime scope/lease/fence 持有长期目标。`sys_mode.switch`、rules/event、Scheduler Operator controls、长期目标激活/撤销、Scheduler HTTP admin、terminal、Edge、Operation/Effect reconciliation 与 Task/Review controls 都在各自外层 Acceptance 后写 canonical owner。当前 Operator 为 `16 active / 23 retired / 39 known / blocked=0`；易失 Endocrine 事件缓冲/last state、易失 Tribe bus、其它资源恢复、真实 executor/Edge canary 和完整 Worker scope 仍未完成，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

D8 把 Host 具身 authority 收口为 ASGI lifespan 的 `HostEmbodimentRuntime`，但“app-owned”不表示同库可并行：ASGI 必须先通过 Task Runtime migration gate，再构造依赖 schema 的 Life authority 和 Query Projection。0104 令 outbox 新行绑定 canonical DB 派生的不可变 `runtime_scope_id`，同 scope 的 durable runtime lease/fence 同时只授予一个进程，第二实例在取得 authority 时失败关闭，历史无 scope 行也不会被新实例猜测 claim。Host 在当前 runtime fence 下持久保存精确 Edge `session/owner/lease/fence`，正常替代进程以同身份重启时必须先轮换 live Edge fence；旧 Host fence 不能更新该 credential。NE-03 在这个持久队列前增加 terminal ActionPreview 重开：当前只有携带 exact proof 的 Life VTS/MateEngine 与 Gateway/Edge 出站可继续入队，旧 reply-performance、Gateway voice、chat cue 和其他没有派生证明的直接调用失败关闭，尚未完成正向迁移。合法新入队报告 `PENDING / DURABLY_QUEUED`，精确重复回放既有状态，只有既有 `APPLIED` receipt 才可令 `physical_effect_verified=true`，`voice_started/vts_started` 仍不能由 admission 推导。

Edge 0002 在 command admission 时冻结 owner；HMAC+nonce 的 read-only receipt query v2 在同一 authority 判断中校验 owner、current session、lease、fencing token 与 `command_id/idempotency_key`，legacy ownerless command 返回 `NOT_FOUND`。`FOUND` 才返回精确 durable receipt，`NOT_FOUND` 不泄漏 receipt 且不会执行命令。Host 对过期 `DISPATCHING` 先转 `IN_DOUBT`，随后只读查询；只有 identity+receipt-digest CAS 命中才落入已知结果，相同 digest 重查不重复推进状态或 observer，`NOT_FOUND`、传输或校验故障保持不确定，绝不重放。非 loopback HTTP 在建立 client 前拒绝，远端必须 HTTPS。真实 child Edge 的 loopback TCP/HMAC/SQLite、receipt query 与重启 exact replay 已有测试；minimal RPC profile 故意没有 MateEngine/设备，物理效果仍 `NOT_TESTED`。前一提交只退役 ReAct 嵌套 `run_skill` / `dispatch_sub_agent`：两者不再出现在发现面，注入旧 ToolSpec 也在 low-level executor 前失败；普通 `GovernedToolNode → CapabilityBroker`、其他 Tool 路线和完整 executor 不在该结论内。本提交再退役视觉代理直连 authority：`/v1/agent/run` 只保留 `AGENT_INIT → LOOP_DONE / REJECTED` 两事件兼容边界，不解析模型、Ledger、Edge session、HTTP client 或 automation executor；raw `agent_loop.py` 与 `edge_rpc_client.py` 已删除，legacy `generate_action_plan()` 固定以 `VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED` 拒绝，剩余 ActionPlan parser/schema 只生成无 authority proposal。生产 chat route 对 `response_format != text` 在模型、Memory 与 Ledger 前固定拒绝，历史视觉 text helper 当前无生产 caller。该路径不是 Delegate `vision_agent`，也没有通过 ToolBroker/MCP/Skill/Delegate/registry/closure 恢复执行；正向视觉自动化必须经 Secure Ingress/Governor 产生新的 immutable TaskGraph revision。

Operator 当前为 `16 active / 23 retired / 39 known / blocked=0`。Edge、Operation/Effect、Task/Review、状态控制、长期目标与 Scheduler HTTP admin 切片都只证明本地持久权威和精确重放，尚未拉起真实网络或部署 key。生产 locator 已物理清零，但真实 workspace-bound governed Tool/MCP/Visual executor和完整 Host/Worker/Obs 对象图仍未完成。因此 D8 不是完整 Query Plane、Composition 或 NE-01 完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

Edge session/owner 不是默认的进程字符串：Host 从 normalized canonical DB path 的稳定 hash 派生二者。同一 durable store 的替代进程先取得新的 Host runtime fence，再读取持久化的精确 Edge credential，以原 identity 重连并查询历史 receipt；不同 store 有不同 identity，不会彼此争用默认 lease；两个 app 指向同一 store 时只有 durable Host lease winner 能访问该 Edge authority，另一个失败关闭。

所以当前更准确的说法是“集中式 Service Locator”，不是“显式 Composition Root 已完成”。

## 8. 长期演化资产

| 目标能力 | 已有资产 | 当前限制 |
|---|---|---|
| World Model | 0111、`persistence/world_observation_journal.py`、`runtime/world_observation_application.py`、`runtime/world_resolver.py` | Host lifespan 中的服务端 observation application 以 lease/source authority 追加事实，Resolver 重开 captured head；输入仍依赖既有 Bond/Emotion 投影，也仍不是预测/规划模型 |
| Scene Policy | 0112、`runtime/scene_resolver.py`、`runtime/context_application.py` | Scene 由重开的 World + app-local Guardian 推导，Surface/Proactive/Task admission 冻结同一 identity；旧 `scene_policy.py` 定义仍保留但无这三处生产构造调用 |
| Intent Arbiter | `IntentFrameV1`、`runtime/production_intent_authority.py`、`runtime/intent_admission.py`、0137 | 生产 adapter 重开 graph/context/action/grant/policy/current goal identity，TaskGraph admission 持久化节点/graph receipt set；只收紧不授权，冻结后 goal 漂移失败关闭；本地波次已通过，部署 canary仍缺 |
| Action Preview | `runtime/action_preview_authority.py`、`runtime/action_preview_resolver.py`、执行/terminal front doors | 重开 Command/Task/Attempt/Lease/Grant/Context/Scene/Guardian 与 Acceptance claim；derived terminal 递归重开 origin Attempt/Lease，descriptor 只收紧；Acceptance Gateway 仅允许 exact minimal text，拒绝未绑定 `raw_extra`/媒体/二次变换；公开具身 submit/enqueue 也必须 exact proof。既有 sys-mode、长期目标、terminal、Edge、Operation/Effect reconciliation、`task.operator_control` 与 `operator.state_control` 都必须携带 typed terminal proof；旧任务/effect、reply-performance/Gateway voice/Avatar 直通失败关闭 |
| Obs Preview / Shadow | `governor/action_preview.py`、Obs route | Obs 端点仍是单独的 dry-run eligibility 视图，固定 `would_execute=false`；它不替代 Host lifespan 的 canonical authority，也没有统一 shadow runner |
| Capability Maturity | scorecard/provider/Governor adapter、production composition fail-closed wiring | production IntentDecisionEngine 默认启用 restrict-only policy；缺 durable promotion/degradation lifecycle |
| Capability Forge | `app/capability_forge.py` | 只产生 proposal/gap candidate；approval、rollback、runtime result 和 route override 公共 authority 为 0，不得绕过 Lab/Review/0141 lifecycle |
| Evolution Lab | 0108 V1、0122 structural V2；0138 trusted ledger；0139 trusted redaction repair；0140 redacted dataset；`contracts/evolution_lab_redaction.py`、`evolution_lab/{docker_executor,golden_datasets,redacted_trace,golden_worker,golden_verifier,golden_experiments,trusted_runner,production_composition}.py`、`persistence/evolution_lab_{redacted_dataset,trusted_query}_repository.py`；Obs/Monitor Operator projection | 0108/0122 只读且不可晋级；0139 消除 trusted/V1 redaction 同名冲突，0140 只保存 content-addressed sanitized dataset、Manifest 与 digest-only Receipt，raw trace 不落库；纯规则位于 contracts，SQLite reader 只位于 persistence。专用 executor 只接受 digest-pinned Docker，重新验证 engine/image/container/command/inspect、禁网、只读 rootfs/dataset/runtime、非 root、临时 bind output、资源限制和零 effect audit；不允许 host subprocess fallback。三类 Golden Experiment 共六个独立容器 arm、本机真实 Docker response-loss exact reopen、unknown-effect reconciliation、脱敏重启/篡改/回滚已通过；query-only repository、Obs producer/route 与 Monitor 页面只展示 proposal-only 证据。clean-commit suite 绑定 `b68a8b9dd` 与 receipt `020c8fc0…`，exact replay 后 18 张 Lab 表、收据哈希和容器计数不变；状态为 `LOCAL_ACCEPTANCE_PASS / PROPOSAL_ONLY / PROGRAM_INCOMPLETE` |
| Feedback | 0142、`evolution/task_feedback.py`、`evolution/version_activation_feedback.py` | 全部启用入口 proposal-only；APPLIED 查询真实 Lab/Review 并切换 versioned Persona/表达/反馈 policy。失败写 durable evidence，0110 只读 |
| Persona Growth | `persona_growth.py`、Bond stage、Evolution Orchestrator、emergent growth skill | legacy apply/rollback 被中央围栏和静态扫描封禁；候选进入 FeedbackProposal，Review 后 activation 通过显式 migration authority 修改 traits，exact rollback 恢复前一版本 |
| Knowledge OS | `KnowledgeEntryV2`、`DecisionRecordV1`、0142 active projection、`KnowledgeOSSemanticRecaller` | active winner、cursor、supersedes/deprecation/dedup/conflict/TTL 已实现；Host 支持 SHADOW/CANARY/ENFORCE，默认 SHADOW，NE-07 前不宣称部署切流完成 |
| Identity | `release/identity_snapshot.py`、`release/identity_activation.py`、`IdentityMigrationPlanV2`、0142 | regression、no-change、重大差异阻断、真实 Lab/Review binding、canonical snapshot activation 与 exact previous rollback 已本地证明；真实 release deployment 留到 NE-07 |
| Runtime Guardian / Catalog | 0112、0113、`runtime/runtime_guardian.py`、`service_catalog/host_startup.py`、`ServiceHealthHeartbeat` | Host lifespan 绑定 runtime lease/fence；每个 lifespan 自有 heartbeat task、可选 keepalive、Guardian observer、probe cache 与 HTTP transport，关闭不影响其他 Host scope；未注入 observer 的 compatibility probe 只写自己的兼容 cache，不能写全局 degradation reducer。独立 Obs health projection 与 probe registry 只读 durable ledger/projection，不读取 Host 内存 cache。fresh DB 逐级启动五阶段并保留 ENFORCE 候选。兼容顶层 helper 不是 Host production authority；显式 legacy adapter、Windows graceful stop 与部署多实例 canary 仍未收口 |
| Workspace Isolation | `workspace_paths.py`、`workspace_policy.py`、`workspace_allocator.py`、`workspace_write_promotion.py`、`workspace_runtime.py`、`artifact_store.py`、`governed_skill_work_order.py`、`plan_workspace_acceptance_effect.py`、`skill_artifact_terminal_reconciliation.py`、`agent_workspace_acceptance_effect.py`、0116 repositories | D0/W0/W1 继续生效。W2 为认证 Internal 与三条 risk-4 Agent Skill 派生 graph workspace、冻结 source 并创建独立 worktree；Lease、heartbeat、handoff/takeover、cleanup 已接线。Direct Skill success/failure 与 Plan 单写节点 whole-graph success/downstream failure/downstream post-review cancellation 均密封 bounded patch 并由 shared lifecycle cleaner 跨 runtime 收口，accepted HEAD 不移动源分支且再次重启零重放。其它 terminal matrix、多 Workspace、Domain Pack/其它 Skill、逐操作 fence/强杀及 OS/container/tenant/handle 隔离仍缺；状态为 `PARTIAL_DIRECT_AND_SINGLE_PLAN_WRITE_SUCCESS_FAILURE_CANCELLATION_LIFECYCLE / GENERAL_CROSS_ROUTE_INCOMPLETE` |

### NE-06 生产证据链状态

0110 `evolution_governance_ledger` 现在是只读历史资产，0142 trigger 会拒绝
任何新 V1 mutation。V2 repository 将版本、proposal、Knowledge、Decision、
Identity snapshot/regression/plan、decision、operation、active projection、
receipt、failure evidence 与 shadow compare 分表持久化；只有唯一 repository
UoW 能修改 active projection。

`EvolutionVersionActivationService` 会在 mutation 前重新打开 0138 trusted
Lab report/proposal、exact ReviewItem subject/status version/evidence、target
version/digest、regression 和 rollback target。apply→rollback→新版本 apply
使用独立 operation identity；响应丢失重开 exact receipt。Host lifespan 持有
唯一 `HostEvolutionRuntimeOwner`，Input、Tribe、Task acceptance、Scheduler
和 semantic recall 均消费同一 service/repository identity。

本地波次为 `199 passed in 73.74s`；分层修复后重叠集合为
`51 passed in 28.16s`。fresh Gate 1～14 全部通过，Persona writer scan 为零
违规。真实部署、Knowledge ENFORCE、Identity release pair、Docker 和外部服务
仍未运行，因此状态为
`CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07`。

## 9. 仓库边界

- 顶层 `data/`、`flows/`、`skills/`、`integrations/` 已删除。
- 可变运行数据进入 `runtime/`，测试/验收输出进入被忽略的 `artifacts/`。
- 内置 Skill 代码保留在 `host_brain/skills/`；外部 bundle 只能经过签名接收与隔离，不能靠目录扫描激活。
- 跨进程合同保留在 `natsume_contracts/`。

## 10. 当前最高优先级缺口

1. 在隔离 acceptance 数据根启动固定版本 Docker Neo4j/Chroma/Redis，并完成真实写、读、重启读取和 namespace 清理。
2. 构建 Monitor UI，启动 Host、Obs、Nginx Monitor、完整 Edge 与 Unified Gateway；真实 HTTP 链必须贯通 TaskGraph、Budget、Verification、Acceptance、Effect、Outbox 和 receipt。
3. 在同一运行窗口执行 Chat、Skill、Delegate、Plan、Scheduler、Internal、Operator，以及 Lab、Skill lifecycle、Feedback、Knowledge、Identity golden flows。
4. 注入 Host/Edge/数据库崩溃、Lease 过期、Budget 竞争、未知副作用、网络中断和 rollback failure，证明不重跑已提交 worker、不重复外发。
5. 在真实 Chroma/Neo4j 下从 Knowledge SHADOW 走 CANARY；只有 read compare、恢复和隐私检查通过后才允许 ENFORCE。
6. 使用真实 release snapshot pair 执行 Identity no-change、重大差异阻断、activation 与 exact rollback。
7. 在外部条件可用时执行 PlanningSearch 24/20/30 cohort、外部 Skill provider canary、模型、ASR/TTS 与 Avatar；缺失时只能标 `ACCEPTANCE_PENDING_EXTERNAL/NOT_TESTED`。
8. 完成跨 workspace、授权提升、Preview 绕过、供应链、secret/隐私扫描和冻结基线性能比较。
9. 生成 `FullStackAcceptanceReceiptV1` 并按强制 Gate 得出 `PASS / ACCEPTANCE_PENDING_EXTERNAL / FAIL`；NE-07 前不得宣称 Full Architecture DONE。

## NE-05 当前生产对象图

生产 Host 在 lifespan 内构造 `SQLiteSkillLifecycleV2Repository`，再以
`LifecycleBoundSkillRegistry` 包装可信内置 Registry。包装器启动时一次性重开
workspace 的 V2 active projection，并验证 adapter、protocol、handler 和
maturity ceiling；任何一项漂移都会阻止 Host 开放路由。运行期只返回带
`SkillVersionBinding` 的 metadata/handler，不扫描磁盘目录，也不动态 import
外部 bundle。

0141 把外部 canary 的 pending operation/outbox/reconciliation 与本地
activation authority 分开。canary 只有有效 fence 可结算；claim 过期后状态是
`RECONCILIATION_REQUIRED`，不能再次派发。activate/degrade/rollback 各自有
idempotency operation；activation evidence、Supply Chain terminal、maturity
decision、active projection 和 lifecycle receipt 共用一个 SQLite UoW。
rollback 查询目标版本自己的历史 evidence，不复制当前版本证据。

该 SQLite writer 首版达到 1,834 行并被架构 Gate 3 正确阻断；本波没有增加
`cohesive_large_files` 例外。连接和 transaction owner 保留在 1,492 行
repository，0138/Review/Supply Chain 证据与 active projection 的纯校验拆到
475 行、无连接无事务的 mixin。拆分后 Gate 1～14 全部通过，writer authority
仍只有一个。

Compiler 与 activation 没有反向依赖：Compiler 输出 unsigned
content-addressed bundle、CycloneDX SBOM 和签名申请，生产签名/Review/Lab
authority 再独立决定是否激活。旧 V1 mutation service/repository、hot loader
和 Supply Chain 公共 activate/reject 已删除。当前唯一 runtime adapter 是冻结的
`builtin-skill-alias.v1`；真实外部 adapter/provider canary 留给 NE-07，因此
本波结论是 `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07`。

这些缺口的唯一活动执行顺序见 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。

## 11. Agent 多轮模型预算当前接线

Internal 与受治理 Agent Skill 的 canonical Agent loop 现已消费冻结
TaskGraph Attempt 的同一剩余模型额度。`model_runtime/governed_budget.py`
负责把 ACTIVE Budget projection 转为 node-bound guard，并在每个模型
结果后只接受 fresh、完整、exact-once 的 canonical measurement。下一次
请求得到的是扣除 Token、provider retry、成本与墙钟时间后的严格剩余额度。

Skill 路径还会把 Budget 的 canonical JSON 纳入 sealed
`BoundSkillExecutionAuthority` identity，再由 Broker 恢复到私有 governed
context；只读 V2 Agent WorkOrder 由 server-side authority 重建成绑定当前
graph/revision/node 的 WorkOrderV3。Internal 路径则直接从
`FrozenNodeInput.budget` 构造同一 guard。调用方 payload、环境
`max_steps/timeout` 或嵌套 `governed_lineage` 都不能扩大额度。

本地定向证据为 91 项相关单元回归、3 项真实 SQLite Internal
预算/取消/晋升回归，以及 1 项真实 SQLite+Git Agent Workspace
晋升回归；这些集合分别记录，不相加为波次总数。真实模型 provider、
完整 NE-01 integration、七路线 commit-bound acceptance 和 NE-07 服务栈
均未运行，故 Budget 与 NE-01 仍为 `PARTIAL / IN_PROGRESS`。

### `RESERVED` 后硬退出的当前恢复链

`DurableTaskStore.read_scheduler_snapshot()` 现在在同一个 SQLite read
transaction 中读取 Graph、Attempt、active Lease 与仍为 `RESERVED` 的
Operation。Store 先通过 canonical `_operation_record()` 重验 digest、key
和 Ed25519 signature，再丢弃完整证明、scope、fingerprint、receipt 与
reconciler，只投影 replacement fence 所需的最小字段和
`signed_reacquisition_verified`。普通 live Lease仍由
`HostStartupGraphContinuation` 直接延后；仅当该 projection 与 replacement
Lease 的 Attempt/Lease/owner/fence 精确一致时，发现层才把 immutable
admission 交回 DAG Runtime。DAG Runtime 再用自身 owner 做最终校验，
发现层没有 worker authority。

签名换权还支持启动恢复已经把原始 Lease 持久化为 `EXPIRED` 的状态。
Store 只接受 Operation 绑定的唯一原始 Lease，要求其已到期、属于同一
Attempt/owner、仍是最高 fence 且不存在其他活跃 owner，然后才允许同一
Attempt 取得 `fence + 1`。真实 Windows spawn 链已证明无证明时 worker=0、
签名后 worker=1、终态重启 worker=0。受控 worker不等于真实 provider，
所以该恢复子集为本地通过，NE-01 仍为 `IN_PROGRESS`。

### Direct Skill post-review 取消的当前实现

`GovernedSkillArtifactTerminalReconciler` 现有第四条受支持终态是 Direct
Skill 自身的 post-execution Review cancellation：

```text
route = skill
Graph = CANCELLED
Node = CANCELLED
Attempt = SUCCEEDED
Operation = COMMITTED
Acceptance decision = REVIEW + review_id
Acceptance Effect count = 0
```

分类器随后重开 finalized cancellation intent/decision；共享只读
`finalized_cancellation_preserves_node_state()` 还会联结当前
`task_runtime_graph` 与 `task_runtime_node`，验证 final Graph version 和
四份 Node state 完全一致。`WorkspaceLifecycleRepositoryMixin` 在真正写入
ABANDON event 前再次调用该 proof，并要求 Review 当前为 `CANCELLED`、
Operation=`COMMITTED` 且带 receipt，再对 exact Attempt 查询 Effect count。
因此 terminal reconciler 的判断不能绕过 repository，raw SQL 只改状态也不能
取得删除 authority。

真实集成通过 ReviewStore、SQLite Secure Ingress、
`GovernedCommandApplication` 和 fresh `DurableWorkspaceRuntime` 构造完整链；
已有 Effect 的故障注入保持 Workspace `ACTIVE`。五文件去重集合为
`68 passed in 117.23s`，基于
`origin/dev@55ece25163667a4db5464a6d2451ab3e7b8f76c9`，无 migration。
该实现不覆盖其它 Direct/Plan terminal matrix 或一般 unknown-effect，NE-01
仍为 `IN_PROGRESS`。

### Direct Skill 已密封但未提交 checkpoint 的失败恢复

`GovernedSkillArtifactTerminalReconciler` 现有第五条受支持终态是 direct
Skill 的签名单节点失败：

```text
route = skill
Graph / Node / Attempt = FAILED
Lease = EXPIRED
Operation = RELEASED + RELEASE + NO_SIDE_EFFECT
graph Budget = RELEASED
WorkerProjection / Verification / Acceptance / Effect / cancellation = 0
```

分类器不直接相信这些 status。它调用
`DurableTaskStore.resolve_reconciled_operation_failure()` 重新验证 Ed25519
proof、owner/fence、Operation fingerprint、terminal settlement 与 Outbox；
只有返回 `RECONCILED_OPERATION_TERMINAL_FAILURE` 且 scope 为
`GRAPH_TERMINAL` 才产生 `TERMINAL_SIGNED_NO_EFFECT_FAILURE`。

Workspace repository 随后通过
`workspace_terminal_failure_authority.py` 在同一只读 transaction 中再次
联结 HostCommand、Graph、Node、Attempt、Lease、Operation 和唯一 graph
Budget，重算 proof/error identity 并精确核对六类 Outbox。该模块没有 mutation
或 worker authority。真实 SQLite/Git/fresh-Runtime 测试证明正常清理、三类
篡改阻断与零 replay；这仍只是 direct single-node raw-failure 子集，NE-01
保持 `IN_PROGRESS`。

### Plan 多节点签名失败与 current 0090 authority

`GovernedSkillArtifactTerminalReconciler` 现在接受两种互斥 signed failure：

- `route=skill`：单节点 `GRAPH_TERMINAL`，Graph/Budget 由 Operation failure
  UoW 一起关闭；
- `route=plan`：多节点 `NODE_ONLY`，只有在
  `graph_settlement_status=COMMITTED` 且存在规范
  `taskbatch://sha256/...` 时才允许清理。

`canonical_current_parallel_terminal_decision()` 会读取同一 Graph revision
的全部 0090 rows，逐条运行既有 codec 与 Outbox binding validator，拒绝
重复/future version，再返回唯一 `graph_version_after == current
status_version` 的 decision。Workspace proof 还把其 `node_states` 与当前
全图逐项比较，并要求目标 outcome 绑定 exact Attempt、`FAILED` 和签名
failure error ref；Budget ID/version 必须一致。

这使 Review 阶段的历史 `BLOCKED` decision 与最终 `FAILED` decision 可以
合法共存。生产 TaskStore 仍是唯一 mutation authority，Workspace reader
只做同事务快照的结构证明。本地真实 Plan/SQLite/Git 测试证明结果节点不
执行、Graph/Budget fresh-runtime 结算、Outbox 篡改阻断与零 replay。

### Plan writer post-COMMIT Budget rejection

`task_checkpoint_budget_terminal_read_store.py` 现在除 single-node canonical
terminal 外，还能只读重开 0090-owned multi-node terminal。它先确定 current
`BUDGET` decision，再验证 selected Node/Attempt、唯一
`TASK_STORE_BUDGET_EXCEEDED` usage、graph-level Reservation、node child usage
bridge、`COMMITTED` Operation、released Lease、三条 child terminal Outbox
和 0090 Graph/Budget Outbox。direct 与 parallel authority 同时出现会被判为
冲突。

`ParallelSettlementProjection` 不再把“当前进程 outcome tuple 未包含 winner”
当作 durable corruption。winner 已由另一个 owner 精确关闭时，projection
从 FAILED Node/Attempt、COMMITTED Operation、无 active Lease 和 canonical
usage 重建 replayed outcome；winner 仍在恢复时返回 deferred。Runtime 还会
等待其它 sibling fence 全部关闭，并丢弃 `ALREADY_EXCEEDED` sibling 的伪
selected status，只有唯一 winner 能成为 0090 selected outcome。

`GovernedSkillArtifactTerminalReconciler` 仅在 route-specific authority、
零 Verification/Acceptance/Artifact/Effect/cancellation 和完整 projection
descriptor 成立时返回 `TERMINAL_BUDGET_REJECTED`。因此 Workspace lifecycle
可以在 fresh Runtime 中清理未验收 stage；最终 repository CAS 还会调用
`workspace_terminal_budget_authority.py`，在同一写事务独立重开 exact
child/0090/Budget authority，不能以 Graph/Node 的裸状态或上层先验检查作为
删除依据。真实 SQLite/Git 及多 owner 回归的去重集合为
`53 passed in 138.76s`，Workspace authority 合同集另为
`24 passed in 67.50s`，无 migration；一般 Budget/Workspace recovery 与
NE-01 仍未完成。

## Workspace cleanup Guardian 与多节点 Budget 当前接线

Workspace cleanup 的 mutation owner 仍是
`ProcessAgentWorkspaceContainer.cleanup_worker`。生产 composition 只新增一个
app-local `WorkspaceCleanupGuardianObserver`，它把脱敏状态转换为既有
`RuntimeGuardian.record_slo_breach()/resolve_slo_breach()` 调用；没有新 Store、
全局 singleton 或第二 writer。Guardian 事件继续绑定 Host 的
`runtime_scope_id + owner + lease + fencing token`。cleanup worker health
同时暴露受枚举约束的 projection 状态；projection 失败会覆盖伪健康结果为
`DEGRADED`。

```text
cleanup repository/Git pass
  ├─ fail → one opaque incident → RuntimeGuardian SLO breach
  └─ success → reopen owned active breaches → durable resolution
```

Task Runtime 侧没有新增预算实现分支。一般两节点证据直接经过现有
`DurableTaskStore`、`BudgetRuntime` 与 node worker timeout：ready claim 在同一
canonical SQLite authority 下创建 child concurrency reservation；实际 elapsed
time 由 graph Budget 的剩余额度约束。timeout 只形成 rejected usage 与
operation recovery hold，不自行推断效果或 Graph 终态。

当前 48 项定向证据只关闭上述接线和两种预算基础行为。跨进程 supervisor 拉起、
一般批次恢复、DEGRADE/REVIEW、取消释放、unknown-effect 与 NE-01/NE-07
仍为未完成边界。

## Workspace cleanup 监督与 Host shutdown

`ProcessAgentWorkspaceContainer` 仍是 cleanup repository/allocator/runtime/worker
的唯一 app-local owner。`app_factory` 新增只读 provider，从当前 FastAPI state
取得 worker health，并在进入 health route 前移除 report payload、workspace
identity、物理路径和异常 detail。`/health` 将 configured unhealthy worker
计入 runtime component degradation；`/ready` 同时改为非 READY。

关停顺序在 Task Runtime drain/close 后进入 Workspace quiesce。该 close 本身
已有外部操作 deadline，Host 再对同一 handle 做一次 bounded retry；成功才继续
hard-state、Memory、Security 与 process reference withdrawal。重试耗尽则以
`dependencies_preserved=true` 退出，live 和 partial-startup 都保留唯一 retry
handle。它解决的是进程生命周期内的安全重试，不是进程已被强杀后的 supervisor
重启证明。

## 只读 Agent 的 runtime repository 边界

`WorkspacePolicy` 已不再为 `runtime_repo/current_runtime_repo` 返回 live
`REPO_ROOT`。它从受信 source 解析 exact `HEAD^{commit}`，通过安全 tar reader
将 tracked regular tree 展开到 runtime-owned 临时目录，并拒绝 link、路径逃逸
与异常 member。快照没有 `.git`，也不会携带 dirty/untracked WIP。

`AgentRuntimeExecutor` 将 `runtime_repo_snapshot + git-commit://<sha>` 写入结果
metadata，在 Verification/Acceptance/result seal 之后显式 cleanup。故意在
fixture executor 内修改 snapshot 只改变临时副本，source checkout 保持原样。
这是进程内共享 checkout 隔离；它不等于 OS/container/tenant 强隔离，Workspace
总体仍为 `PARTIAL`。

### Plan 签名失败与 Budget terminal 的组合 authority

`task_operation_failure_reconciliation_store.py` 现在区分两种多节点 signed
failure 结算：普通 0090 `FAILURE + Graph FAILED` 与预算型 0090
`BUDGET + Graph BLOCKED + Budget RELEASED` 都保持稳定的
`RECONCILED_OPERATION_NODE_FAILURE` kind。后者只有在签名失败 Attempt
同时是唯一 Budget overage winner 时才可重开。

后者不是对 `BLOCKED` 的宽泛放行。Store 会重开 decision 前的 graph/budget
version、selected `BUDGET_BLOCKED` outcome、签名 failure `error_ref`、唯一
`TASK_STORE_BUDGET_EXCEEDED` usage、同 Attempt 的 child reservation/usage
bridge 及 winner Outbox。Accepted handler 的 receipt material 有意不包含
随后提交的 0090 live Graph settlement，因此 PENDING→BLOCKED 不改变
v4/v5 receipt ref。`GovernedSkillArtifactTerminalReconciler` 只允许
`route=plan` 消费 BLOCKED 形状，单节点 Direct Skill 仍要求 Graph `FAILED`。

`workspace_terminal_failure_authority.py` 同样只读重开上述联结，且
graph-wide single-node terminal 明确拒绝 `BLOCKED`。因此新能力没有把
Workspace reader 变成 Budget writer，也没有把状态组合当作删除凭据。真实
SQLite/Git 六路径回归为 `6 passed in 39.93s`，相邻回归另为
`46 passed in 137.82s`；无 migration。一般 Budget/recovery 交叉矩阵与
NE-01 仍未完成。

### 已合并 atomic authority 的结构基线

当前结构 Gate 已按实际 atomic owner 重建：Review denial 由
`settle_review_denial()` 单事务提交 Node、Graph、Budget 和 Outbox，不再要求
scheduler 出现三次独立 lifecycle 写；Plan writer 的
`PARALLEL_CHECKPOINT_BUDGET_REJECTED`、共享 `LocalGraphExecutionRegistry`
注入和 34 个 DurableTaskStore mixin 的顺序均有明确结构合同。Retry writer
只借用 `TaskRetryScheduleFacadeMixin` 与纯 identity builder，仍独占
`_schedule_retry()` 事务；Operation checkpoint Store 保留单一 snapshot，
只把纯 Outbox evidence 校验下沉。没有新增连接、writer、阈值例外或 allowlist。

## 12. NE-02 固定 cohort authority 当前事实

NE-02 cohort migration 为 `0136_planning_search_cohort_ledger.sql`；
仓库当前 migration head 为 `0146_affective_expression_delivery.sql`。新增
`SQLitePlanningSearchCohortLedger` 是三个固定 cohort 的唯一本地 writer：
它持有 registration、member proof、report 与 promotion proposal；连接和
`BEGIN IMMEDIATE` 仍只在 repository owner 中。support 模块仅做无连接的
canonical、digest、registration/report 重开校验。

来源不是由调用者自证。Research resolver 从
`SQLiteResearchShadowLedger.get_terminal_receipt()` 重开 0107 receipt；
Code 从 `CodeHistoricalBenchmarkLedger.get_completed()` 重开 0106 receipt；
Pentest 从 `CalibrationRepository.get()` 重开 0105 prediction/outcome，并
要求注入的 `PentestExperimentReceiptAuthority` 对 Task、Authorization、
Verification refs 逐项返回 exact digest。不存在 permissive default。

本地集成实际写入 3 个 registration、74 个 member、3 个 report、3 个
promotion evidence 和 1 个 proposal；重启后使用拒绝所有调用的 resolver
仍可 exact replay 已有成员和 proposal。proposal 在合同与 SQL 两层固定为
Review-required、无 activation authority、无 production influence。

这些是本地机械证据，不是实际收益。当前没有执行 24 个真实 Research、
20 个真实 Code、30 次授权 Pentest cohort，也没有 Review/activation；
NE-02 因而是 `CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_EXTERNAL`，L3
仍为 shadow。
