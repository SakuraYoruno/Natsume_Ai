受众：CODEX
标题：当前架构程序注册表

# 当前架构程序注册表

版本：v4.8
维护日期：2026-07-30
状态：`ONE_ACTIVE_PROGRAM`

## 唯一活动程序

- 目标：`../50_project/arch/TARGET_ARCHITECTURE.md`
- 当前事实：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- 差距：`../50_project/arch/ARCHITECTURE_GAP_MATRIX.md`
- 执行合同：`NEXT_GENERATION_EVOLUTION_EXECUTION.md`
- 机器状态：`../../configs/release/current_architecture_status.json`

当前执行波为 `NE-08 / DL-00～DL-07`。NE 是面向产品的端到端 Architecture
Wave，DL 是该波内部的连续交付面，不拆回逐个 TASK Seal。

当前活动实现波为 `NE-08_IN_PROGRESS / DL-06_SIGNED_GATEWAY_CONVERSATION_SCOPE_BINDING`：以实时
情感陪伴数字生命为唯一产品主线。DL-00 已冻结体验基线；DL-01 已完成 canonical
Affective Life Kernel、0143 迁移和 1002 个真实 SQLite journal event 的重放/重启
确定性验证；DL-02 已把 workspace-scoped AffectFusion/Appraisal 接入 Host Input，
使用户 observation 先落 journal、再可选形成七海自身 CARE/CONCERN 等 self-affect，
而非镜像用户标签。DL-03 已把 accepted reply 的 provisional relation episode、
下一次输入的 aftereffect、boundary/repair/proactive permission 和 Surface 的可信关系
约束接入同一 canonical Life journal；生产 Bond 默认关闭积分自动亲密晋级。最新 DL-01 public cutover
进一步删除 Host route composition 的 `bond_writer_provider`：`POST /bond/event` 只返回
`410 LEGACY_BOND_WRITE_RETIRED`，不解析或调用 `BondDynamicsWriter`。Monitor 同步删除人工补录
关系事件的表单、API 与 registry 项，历史 Bond 页面只读展示回滚窗口投影，不能重新成为关系或
情感 authority。此为本地公开写入口收口，不构成真人、远端 Edge 或全服务栈验收。Edge ingress 的情绪事件也已从
fire-and-forget Bond/VTS 旁路退役：它只记录观测，final ASR 才能进入正常 Host Input，Voice/Avatar 只能由已接受
的 `AffectiveTurnPlanV1` 驱动；通用 Agent post-process 的 Surface/Cortex Bond fallback、Tribe 对话结束事件与
直接 VTS/整段 TTS 也已退役为 trace-only，表达只能等 Task Acceptance 的协议 effect。
DL-04 已将
可信 hand-off 冻结为 `AffectiveTurnPlanV1`，普通低歧义文本可优先使用
`ModelAttemptService.stream_with_result()` 的 typed delta + provider final result（完整字段时才计量）逐句输出；Vault-backed openai、openai_compatible 与 deepseek 已请求 stream_options.include_usage 并转发终局 usage/transport/latency，但没有真实凭据或外部 provider 运行；每个稳定句先写 canonical Life delivery
intent，只有在首个 delta 前明确不支持终态合同才回退兼容 stream 并保留无 usage 标记。候选模型输出和纯文本 fallback 均不能改写 self-affect，高歧义/边界/修复仅
允许恰好两候 buffered selection。DL-05 已将同一冻结 `ExpressionPlanV1` 编译为 Voice
`SPEAK` 与 Avatar `TRANSITION` 的成对 Host protocol，并在结束/打断时追加
`RELEASE`/`CANCEL`；0146 只投影 Host staging，远端 Edge 尚未启动，因此不能声称播放或
Avatar 已执行。DL-06 的首个 Scheduler 切片仅在 canonical `proactive_permission=ALLOWED`、
无 repair/未结 episode 且 self regulation 未要求克制时，才以 bounded canonical
self-affect 生成无回应压力的主动消息；旧 Bond、legacy `affect` 与固定模板均无发送权。
已接受 foreground reply 现在会把 scope-bound `CompanionProactiveTargetV1` 与 provisional
episode 原子写入同一 relationship command；Scheduler 从 durable registry 按冻结 Scene
scope 确定性选择单一 aggregate，旧 `last_target` 无发送权。`proactive.gateway` 的 immutable
Host/Gateway receipt 现重验 outbox、授权摘要、scope 与 target 后才写入 deterministic
`relationship_proactive_delivered`；receipt 后崩溃由 Host 启动或 outbox tick 只读重开，绝不
重发外部消息。它不代表第三方平台送达或用户已阅读。
DL-06 现另有签名的 structured permission ingress：严格 JSON 只可写 `ALLOWED/DENIED`，
并将 `user principal == relationship subject`、`tenant == workspace` 与 `companion:contact:write`
一并重验，不能由自然语言、Bond、情绪或模型替代；claim completion 与 Life receipt 支持
exact duplicate/restart replay。0147 已将低风险表达 profile 接入 canonical Life command：候选必须
重开五条独立、跨三天、`HELPFUL`、boundary-safe 且 `helpfulness>=0.85` 的 aftereffect；五次后续
真实互动才可 activate，并只能 exact rollback 到当前 profile 预先绑定的旧版本。CANARY/ACTIVE profile
会真实进入下一轮 text/Voice/Avatar 的冻结 plan，但不能改变 Soul、consent 或 self-affect；当前
保守文本 classifier 为 `0.72`，不会误作学习成功。0148 已将受认证的结构化 profile control
接入 app-local Host：`POST /v1/digital-life/relationship/companion-profile` 必须同时满足
`companion:profile:write`、`user principal == relationship subject` 与 `tenant == workspace`；它只能
从 canonical strong aftereffect history 启动 candidate canary，不能从自然语言推断、直接指定 activation
或改写 Soul/consent/self-affect，Secure Ingress audit ref 与 candidate 一同持久化。Monitor 审核控件、
 canary 真实证据、旧 writer 清零仍未完成，不得称为主动陪伴闭环。`0149` 已让已入场的冻结 Chat 用 attempt/lease/fence/grant-bound immutable Task ledger 在 SSE 前暂存 plan 与稳定句，取消/中断也有控制回执；`0150` 要求已有前序 delivery evidence 的替代 attempt 或同 attempt 重入先 durable reconciliation，禁止盲目重跑模型；它不写 Life/Memory/设备 effect。普通 Chat 切流、受认证连接恢复、跨进程完整 recovery 与真实外设仍由后续 DL-04～DL-07 交付；模型终态 result 合同不替代这些 Task Runtime authority。此前 NE-01～NE-07 的 Universal Runtime、搜索、
Lab、Skill、Knowledge 和服务栈工作仅保留为基础资产；它们不能证明七海具有独立
情感因果、长期关系修复或文本/语音/Avatar 同轮表达。后文关于 NE-01～NE-07
的段落是历史范围说明，不再决定当前执行顺序。

**DL-06 签名 Gateway 关系 scope 修订：** `/v1/host/input` 在 Secure Ingress 允许后、任何
Fusion/Appraisal 前，使用可信 `principal_id + tenant_id` 与签名 `from_id + conversation_id` 编译
`SecureGatewayConversationScopeV1`。`from_id` 决定 opaque relationship subject，conversation 只决定
opaque session，workspace 固定为 `tenant:<tenant>`；缺少 sender 会 durable reject，不能将 group/conversation
当作人或回落到 `default`。这份 scope 同时进入 `ChatRequest`、thalamus、aftereffect、偏好 proposal 和
Runtime Context，并在 Runtime Context 重验 ingress authority；服务 principal 仍只用于 HostCommand、grant 与
delivery 授权。当前只完成本地 SQLite/合同边界，真实多用户平台、provider、远端 Edge 与 DL-07 未验收。

受治理 Gateway Chat 的 ExpressionPlan 不再因 transport source 被无条件标为 TTS suppression。该旧
flag 只留给未受治理兼容 fallback，以免它与 `_deliver_gateway_reply` 重复入队；真正的 Voice/Avatar
仍必须通过 Task Acceptance 后的 Host-owned durable outbox，不能把 suppression 的移除解释为远端播放。

DL-00 现补上了提示词层的唯一关系 authority：`Soul.md` 原文及其 digest 继续作为不可在线改写的
人格源材料，但 `SoulCompiler` 不再把旧剧情、固定用户名、Bond/阶段或亲密例句送入 live prompt。
Surface、Cortex 和 node generation 共同调用 runtime projection，并强制附带“关系只能由
`RelationshipStateV2 + AffectiveTurnPlanV1` 决定”的边界。72 条体验场景也已从仅检查数量的 fixture
升级为测量合同：必须覆盖误解、自我边界、未接受修复、混合感受、空间、ASR/TTS/Avatar 与打断；在真实
provider 轨迹和至少三名独立评审盲测出现前，状态只能是 `MEASUREMENT_PENDING`，不能写作体验 PASS。

DL-02 还收口了 ASR 到 Host Input 的语义边界：Edge 的关键词推断、外带 emotion tag、强度、context
和伪造观察列表都不能升格为用户 self-report；它们只可让原文本走既有低置信 TEXT observer。仅有明确
可用且未降级的声学 attestation 才会形成一条 5 秒 TTL 的 typed ACOUSTIC observation，且不回传
raw audio、原始 context 或直接 Chat emotion 字段。真实 Host ASGI 的 final ASR bridge 也已定向验证同一
payload 语义；它不代表远端 Edge 已启动或声学模型已验收。

在 canonical Life composition 中，Limbic 的 EMOTION_INFERENCE_UPDATED 订阅也不再写
TribeStateRegistry 的运行期 voice-emotion 项或旧 emotion timeline；真正 final ASR 只能按上面的
标准 Host Input 路径入场。离线兼容组合仍可保留原行为，但无 production authority。

DL-02 当前又收口了三个直接影响陪伴体验的 canonical 因果：明确请求空间时，
`user_boundary_or_space` appraisal 必须冻结 `GIVE_SPACE`，不能退化为通用确认；明确
指向七海的受伤信号形成 `natsume_may_have_hurt_user → CARE / OPENNESS / CAUTION → REPAIR`，
而不是把用户的 `SAD` 复制成她的情绪；同一可信观察中的喜悦与痛感共存形成
`user_mixed_affect → HOLD_COMPLEXITY`，允许“开心与舍不得同时存在”。选定 Appraisal 会在同一
Fusion 路径生成 `RegulationDecisionV1`：空间为 `GIVE_SPACE`、修复为 `SEEK_REPAIR`、冲突为
`CONTAIN`、其余为 `EXPRESS`；Planner 只接受 reason ref 精确指向本请求 appraisal 的 regulation，
避免旧状态控制新回合。这些是有限、可审计的本地规则，journal 仍只保存 opaque evidence ref 和 typed
event，不保存原话。该切片有 unit 与
SQLite restart 证据，但未替代真人盲评、真实 provider、远端 Edge 或 DL-07 服务栈验收。

DL-03 的 repair aftereffect 现在也会回流到当前 affect scope：只有 durable
`REPAIR_ACCEPTED` receipt 才生成 `relationship_repair_accepted → RELIEF / CARE`，并以
`relationship-aftereffect://` opaque ref 写入 Appraisal/Regulation；它不把一次接受放大成永久关系
承诺。`proactive_permission=DENIED` 只保留在 Scheduler 的主动投递门禁，Surface foreground reply
不会据此对当前主动输入冷处理。

同一条 canonical 链还区分“用户要求停下”和“用户明确贬损七海”：前者（包括 `闭嘴`、`别来烦我`）必须
形成 durable `BOUNDARY`、`GIVE_SPACE` 和 `DENIED` 主动许可；后者仅在明确指向七海且无停止语义时才形成
`HURT / CAUTION → CONTAIN → SET_BOUNDARY`。它是可解释的当轮 self-affect，不是要求道歉、关系降级或
永久人格变化。

DL-04 当前新增的是一条**已入场 frozen Chat 的本地 acceptance 证据链**，不是普通 Chat 封板：
Gateway service principal 保留为 command/lease/grant/delivery authority，canonical Runtime Context 的
`subject_id` 与 workspace 才进入七海的 AffectiveTurnPlan、conversation、Life、Memory 与 reply projection。
原生句流会把 immutable model budget guard 的 exact node id 写入 canonical ModelAttempt audit；最终
acceptance 对 node drift fail-closed。受控 SQLite integration 已验证两句流、acceptance effect 与 Gateway
delivery 的端到端窄链；真实 provider、远端 Edge、authenticated connection resume 与 generic Chat cutover
仍未完成，状态必须保持 `PARTIAL / REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

本次补充的 ASGI 回归已验证：`POST /v1/chat` 的本机个人、低歧义 text route 在显式选择具备
provider-final-usage 合同的受控 OpenAI-compatible transport 后，会走 `HostCommand → TaskGraph →
Task-bound sentence ledger → SSE SENTENCE → Acceptance`。首句前失败持久化 `STREAM_INTERRUPTED` 后以
stable `REJECT` 收口，不进入 unknown-effect 重放；它不把 fixture transport 提升为真实 provider
或 generic/multi-user Chat 完成证据。

当前 DL-05 继续将这条窄链落入 Host expression protocol：两条已外显的 task-bound sentence 在
Acceptance 后被重新读取为四条 immutable `HOST_PROTOCOL_STAGED` Voice/Avatar intent，并以两条
`HOST_RELEASE_STAGED` control 收口；不是按合并 narrative 再次切分。frozen executor 会比较逐句
Voice text、channel/action、plan/sync/self-affect revision、workspace/subject/session 和 receipt，
任一缺失或漂移均失败关闭。本地 expression unit/restart/stream 与 real SQLite universal Runtime
定向集合为 `38 passed in 38.50s`；
未连接远端 Edge，因此不能升级为真实 TTS/VTS 或实体 Avatar 的完成状态。

DL-05 的 accepted-expression 子切片现将上述 protocol 接到 Host durable embodiment outbox：
Acceptance compiler 仅接受带同一 frozen plan、accepted narrative 和 exact protocol projection 的
`life.foreground_completed`；它先重开 `APPLIED` Life receipt 与 terminal Action Preview，再生成
idempotent Voice/Avatar enqueue command。Host outbox admission、reconciliation 与物理 Edge receipt
是三个不同阶段，当前只完成前者；远端 Edge 不启动、不连接，也不能由 `PENDING/APPLIED` outbox
状态推导出播放或转场。

`POST /v1/chat` 的本机个人入口现于 AffectFusion、检索预取和 Runtime Context 之前绑定
`natsume.direct-chat-scope.v1`：由 `HOST_DEFAULT_USER_ID` 派生唯一 subject/principal 和
`personal:<subject>` workspace，调用方的 `person_id`、`workspace_id` 及 nested emotion-context
workspace 均不能选择关系或 Life aggregate。该 provenance 明确是
`LOCAL_PERSONAL_UNATTESTED`，没有 claim/audit/tenant，不能作为 Secure Ingress 或多用户认证结论；
Gateway/多用户仍只允许经 `/v1/host/input` 的签名身份进入。

本切片同时新增 receipt-scoped `CompanionResponseFeedbackRequestV1`：
`POST /v1/digital-life/relationship/response-feedback` 只允许 `HELPFUL/NEUTRAL/MISUNDERSTOOD/BOUNDARY`，
必须持有 `companion:feedback:write`、exact user/relationship subject 与 tenant/workspace 绑定，并且
引用一条仍处于 canonical provisional episode 的已接受 response receipt。只有明确的 `HELPFUL` 映射为
`0.95` strong aftereffect；它只可推进既有 profile CANARY，无法自动创建或 activation profile。现有
自然语言 classifier 的 `0.72` 和旧 ResponseFeedback proposal 保持低权重/proposal-only，均不具备该
成长 authority。

当前子波改为 `DL-06_SIGNED_PROFILE_ROLLBACK_CONTROL_SLICE`：
`POST /v1/digital-life/relationship/companion-profile/rollback` 只接受
`CompanionProfileRollbackRequestV1` 的 current active profile identity，必须另持
`companion:profile:rollback` 并重验 exact user/relationship subject、tenant/workspace。
body 没有历史 target；canonical lifecycle 只能恢复 current profile 的 frozen predecessor，且将
audit-derived `source_control_ref` 写进 immutable rollback event，使 duplicate、claim-result 中断和
restart 仅重开一个 Life receipt。该入口不改变 Soul、consent 或 self-affect；Monitor/Review、长期
canary、legacy writer cutover 与真实服务验收仍未完成。

当前子波此前进入 `DL-06_HOST_EMOTION_RUNTIME_CUTOVER_SLICE`：生产
`build_production_host_application_ports()` 已不再构造或发布 mutable
`HostEmotionRuntime`，也不再发布 process-wide `user_emotion_profile`。ASGI 为 Tribe
注入与 Input 相同的惰性 `LifeCommandService` authority；二者在真实 request 时均经
`AffectFusionService` 写入 scoped observation/appraisal，再读取 canonical self-affect。
`HostEmotionRuntime` 不再可由 Input 或 Tribe composition 构造；遗漏 Life authority 的请求固定
`CANONICAL_AFFECT_FUSION_NOT_INJECTED`，不能以 offline/test composition 名义恢复第二个 self-affect
writer。该类只保留给直接隔离测试，不能作为 Host fallback。

当前 `DL-06_LIMBIC_LEGACY_AFFECT_WRITE_RETIREMENT_SLICE` 已在同一 immutable
`TribeApplicationPorts` 上标记 canonical Life authority。production Limbic 遇到 legacy
`EmotionPersistence` restore/persist、EmotionVector metabolism、`emotion_timeline` append、scheduler
decay、Bond progress/regress、task success、humor landed、loneliness、immune block、interoception 或 interaction-recorded callback 时，统一返回 `RETIRED_CANONICAL_LIFE`，不再创建、更新或追加
第二份 self-affect；这个 gate 只在明确 offline/test compatibility composition 未选 canonical Life 时放开。
它没有替代新的 Life command，也没有退休 BodySense、Endocrine、Motive 或 volatile Bond queue，故 DL-06
仍为进行中。

当前 `DL-06_ENDOCRINE_LEGACY_MUTATION_RETIREMENT_SLICE` 继续复用该 production authority：
Endocrine 只能返回输入 hard-state 的无状态 compatibility projection，不能消费或累加旧
cortisol/oxytocin delta、更新 `_last_state`、生成旧 hormonal hint 或向 Limbic/Interoception/PainReward
fan-out。`record_stress_event`、Bond/loneliness/task/immune callback 与显式 delta 入口在 canonical
mode 均返回 `RETIRED_CANONICAL_LIFE`；Circadian/MirrorNeuron 已停止直接写 Endocrine 私有字段。
这只退役 Endocrine mutation authority。随后 `DL-06_BODYSENSE_GLOBAL_MUTATION_RETIREMENT_SLICE`
已在同一 canonical authority 下让 production Surface/startup 不再 warmup 或读取 global
`BodySenseModel`：每次读取都必须提供 relationship subject，并从对应 Life snapshot 做纯读 projection。
legacy conversation/tick/loneliness mutation 统一返回 `RETIRED_CANONICAL_LIFE`，retired tick 不再发
`BODY_SENSE_UPDATED`，因此不能重新驱动 silence 或 legacy metabolism。随后 Motive 已去除模块级
`_engine`：兼容工厂每次创建无状态规则对象，所有动机只能由本次传入的快照纯投影，不能作为跨用户或跨
turn 的情感 authority。production Host 随后删除 `ProcessApplicationPorts` 对 real
`BondDynamicsWriter`/`BondUpdateQueue` 的 construction factory；Host runtime、Surface hook 与
Cortex canonical mode 都只返回 explicit retired adapter，`/bond/event` legacy write 为 `410`，没有 worker、
backlog 或失败后直写 fallback。production Host 现在也不再从旧 action log 预热 mutable
`EmotionTimeline`：retired empty projection 不含伪造的 `NEUTRAL`，Lifecycle/proactive 会忽略它，canonical
Limbic/Prefrontal 不会读取或缓存它，user profile 也不能覆盖七海 self-affect。其它 compatibility projection
的生产读取审计、Monitor/Review 与真实服务验收仍待处理；这一事实不是 DL-06 或真实陪伴验收完成。

本轮只完成 composition 级 fail-closed：Input/Tribe 缺 Life authority 的情感解析稳定拒绝，最终定向
composition/输入路由集合 `148 passed in 39.51s`（直接 composition 子集 57 项）。它不等于旧类物理删除、Monitor/Review 使用量归零或真实服务验收。

当前子波新增 `DL-06_DEFAULT_MODE_CANONICAL_INNER_ACTIVITY_READER_CUTOVER`：`DefaultModeAgent` 读取一次
canonical Life snapshot 后，`project_inner_world_state()` 只交出有限 `self_affect`/`relationship_state`
投影和 `CANONICAL_LIFE_JOURNAL` 来源标记；`inner_activity_agent` 据此构造私密活动上下文，不再解析 retired
EmotionTimeline 或 SocialBrain/Bond snapshot。关系纹理保持与旧 Bond summary 分离，trust/attunement 不得映射成
score/stage、亲密或主动许可；projection 也不带 identity、trigger/evidence 或 intimacy consent。默认模式高连续性
不再调用 legacy Limbic metabolism 补写 self-affect。该读路径与相邻 DL-06/default-mode 集合为 `92 passed in
44.20s`；它只完成一个 compatibility reader，Monitor/Review、其余 reader、跨进程真实恢复、真实 provider/Edge
和 DL-07 服务栈仍待后续。

当前继续执行 `DL-06_CANONICAL_RELATIONSHIP_PROMPT_PROJECTION_CUTOVER`：普通 Chat 的 Prompt Context
Plane、Interaction Facts Plane 与 Surface packet 仅消费经内部 AffectiveFusion provenance、`natsume_ai`、
workspace、relationship subject、session 和 `v1/v1/v2` schema 共同校验的 `RelationshipStateV2`。该状态只能
投影成非浪漫 `FRIEND / stepsibling` 文本纹理；旧 Bond `SOULMATE`、分数、trust/attunement 或 consent 无法
触发 lover、排他、依赖或主动权限。repair/rupture 仍会收紧为修复状态。claimed canonical context 失效时
fail-closed 为 `CANONICAL_RELATIONSHIP_REJECTED`，不回退 legacy Bond。相邻受影响集为 `179 passed in 63.85s`；
这只是本地 prompt reader 切流，DL-06、真实 provider/Edge 和 DL-07 均未完成。

同一切片继续收口 `DL-06_SURFACE_AFFECT_CONTROL_CANONICAL_AUTHORITY`：最终表达 Gate 识别
`CANONICAL_RELATIONSHIP_LIFE` 与 `CANONICAL_RELATIONSHIP_REJECTED`，不让旧显式 `relationship_distance`、高
closeness、恋人 clue 或 caller-selected lover task 抬升 Surface 距离；有效 canonical 维持 `familiar`，裂痕或
被拒绝 hand-off 维持 `guarded`。该 Gate 定向集合为 `82 passed in 21.18s`，仍非完整服务验收。

继续执行 `DL-06_CORTEX_RETRIEVAL_CANONICAL_RELATIONSHIP_CUTOVER`：Cortex prefetch 在检测到 internal Life
hand-off 后，严格以 workspace/person/session 抽取 bounded projection，并跳过 `resolve_state_arbiter_bond_fact()`。
检索和 Motive 只接收 `FRIEND / stepsibling / familiar|guarded` 与有限 trust/safety/attunement texture；不接收
legacy score、恋人 stage 或 proactive permission。claimed canonical 无效时同样 guarded 且不回退 arbiter。
该定向集合为 `34 passed in 5.64s`，尚未清零其它 reader 或完成真实服务验收。

当前追加 `DL-06_LEGACY_PROACTIVE_SCHEDULER_RETIREMENT_SLICE`：旧 `proactive_talk` 已从 Personality
registry、Tool Catalog、executor 及 Scheduler-add type 移除。Host scheduler composition 对升级前遗留的
`proactive_talk_periodic` definition 执行 CAS disable；若一个旧 occurrence 已完成 governed admission，仍先
验证 immutable Scene identity，但只会得到 `LEGACY_PROACTIVE_TALK_RETIRED`，不能读取 Bond、EmotionTimeline、
Limbic 或模板，也不能创建 outbox/delivery intent。唯一发送路径仍是 canonical
`life_tick → companion_proactive → Life outbox`。此切片只完成本地旧 authority 退役，剩余 compatibility
reader、Monitor/Review、真实 provider/Gateway/Edge 与 DL-07 仍未验收。

当前追加 `DL-06_RUNTIME_SNAPSHOT_CANONICAL_READER_CUTOVER`：production
`LifecycleRuntimeApplicationPorts` 现在显式接收 `LifeSnapshotReader`。OBS snapshot 对同一个
relationship subject 读取一条 canonical Life head，再纯投影 `self_id=natsume_ai` 的 mixed self-affect、
bounded user-affect observation、关系纹理、body-sense、motive 与 render posture；任何用户 `emotion_tag`
都不能覆盖 self-affect。该 port 一旦已组成，reader 缺失、scope/self-id drift 或读取失败均为 degraded，绝不
回退 SocialBrain/Bond、Limbic、Prefrontal 或 retired EmotionTimeline。当前 `67 passed in 10.65s` 只覆盖本地
切流与邻接回归；Monitor/Review、真实 provider/Gateway/Edge、跨进程恢复与 DL-07 仍待验收。

`DL06_RETIRED_PROGRESS_REPLY_AUTHORITY_CUTOVER`：进度 fastpath 已由主链退役，仍保留的
`input_runtime.progress` helper 不再解析 payload 的 Bond stage 或 user emotion，也不导入 SocialBrain；它只能
将技术状态渲染为 `FRIEND / CALM`。无生产 import 的旧 `app/input_service_ack.py` 已删除。`14 passed in
1.92s` 为定向本地合同，不能当作 Gateway 送达、可见对话或 DL-06 完成。

NE-02 已补齐 D20c 的生产 transport identity 边界：真实 provider adapter
生成脱敏 endpoint digest、调用时延和外部 HTTPS 决策，canonical audit 与
Research attestation 必须精确一致；本地、私网、CLI、未知 transport 及历史
无 identity receipt 均不可计收益。真实凭据、24 项 Research cohort、Code
20 项和 Pentest 30 次验收尚未执行，因此 NE-02 保持
`CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_EXTERNAL`。

NE-03 当前实现 0137 runtime-fenced 长期目标 authority：激活/撤销只能经
Secure Ingress、Governor、TaskGraph、Review、Action Preview 和 Acceptance
Effect，随后 event/head 在同一 SQLite UoW 提交。World 只投影 active goal，
撤销以 `SOURCE_REVOKED` expiry 清除；Context/Intent 冻结 exact refs 与 typed
constraints，并在 admission 重开 current identity。现行 Operator inventory
为 `16 active / 23 retired / 39 known / blocked=0`。本地定向链和全部
`test_ne03_*` 单元/集成波次均已通过；波次结果为
`271 passed, 2 warnings in 332.52s`。部署多实例 canary、Windows graceful
stop 和 NE-07 全栈仍未运行，因此本波保持 `IN_PROGRESS`。

Research 24 项验收协议的纯合同、确定性 evaluator 与 0136 durable owner 已落地：固定 cohort
规模/主题/来源覆盖，hidden verifier 独立 execution 与 response binding，
10,000 次 paired bootstrap，以及质量、citation、严重错误、含 verifier 成本
和 p95 时延的不可放宽门槛均有定向测试。owner 会先从 0107 terminal ledger
重开每个 pair，再持久化成员 proof 和完整报告。下一步是运行真实双臂；当前不得把
`proposal_eligible` 的可计算性写成已获得真实收益。

Pentest L3 已把固定根全集改为两层 catalog：当前 ToolContract/PreActionGate
通过的动作只作为 executable roots；同一 server policy 生成但缺 capability
的已登记动作只作为 virtual successors，待纯 transition 的新 capability
满足后才可展开。successor catalog digest/count 已进入 durable shadow
calibration，生产选择仍强制使用 L1/L2 baseline。固定 30 次协议和跨域
proposal Gate 的纯合同现已落地：每次运行绑定授权环境、facts/capability/
policy 派生 successor、独立 Task/Authorization/Verification receipt 和
校准指标；Pentest 合格且 Research/Code 至少一域证明收益后也只生成强制
Review、无 activation authority 的 proposal。0136 已把 0105 calibration
与三类外部 receipt authority 绑定为统一 cohort 成员。下一步仍是 30 次真实
授权实验、真实非 Pentest 收益与 Review；当前不得升级
L3 production influence。

Code 20 项收益协议已冻结：同 source/model/provider/profile/budget、独立 fresh
worktree 与 execution；通过率提升至少 10 个百分点，或非劣 5 个百分点且成本
降低至少 15%，同时 hidden/static/diff-risk 不得退化。D19 失败 arm 现会在
cleanup 后形成 terminal observation，不再因测试失败被 cohort 丢弃。下一步
仍是真实外部模型执行、20 项 evidence 和 proposal Gate。

本地 NE-02 波次集合最终为 `169 passed, 1 skipped in 104.80s`，跳过项仅为
Windows symlink 权限缺失且不计 PASS。该波 migration 为 0136；仓库当前
continuous head 为 0137。首次集合中的
4 项失败已透明保留并修复。当前本地代码状态为
`CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_EXTERNAL`，不等于真实收益达标，
也不允许 L3 production promotion。
收口发布合同首次因 manifest 仍硬编码 0135 得到 `58 passed / 1 failed`；
更新为 0136 后完整复跑为 `59 passed in 8.75s`。统一 static/doc Gate
和 fresh 架构 Gate 1～14 均通过，失败清单为空。

## 历史编号

- TASK-0000～TASK-0622、M48～M57、AW-00～08、GR-00～06 已冻结。
- 原文位于 `../_history/programs/2026-07/` 或 Git 历史。
- 历史编号仍可用于搜索，不得决定当前完成度、优先级或 Goal。

## 记录规则

1. 小工作片只写 PR checklist 和定向测试，不扩张本文件。
2. Architecture Wave 状态写在唯一执行合同和机器状态中。
3. 一个波只有 production cutover、旧 authority 删除和 wave integration 同时成立才可完成。
4. 提交信息与 PR 全部使用中文。
