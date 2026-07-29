受众：MASTER
标题：Natsume AI 产品需求文档

# Natsume AI 产品需求文档

版本：v4.6
日期：2026-07-28
文档类型：`STABLE_PRODUCT_REQUIREMENTS / NO_EXECUTION_STATUS`

本文只定义产品必须提供什么、禁止什么以及如何验收，不记录代码完成度、文件数量、端口默认值或阶段状态。

- 当前实现事实：[CURRENT_CODE_ARCHITECTURE.md](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
- 稳定目标架构：[TARGET_ARCHITECTURE.md](../50_project/arch/TARGET_ARCHITECTURE.md)
- 当前差距：[ARCHITECTURE_GAP_MATRIX.md](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)
- 唯一活动执行合同：[NEXT_GENERATION_EVOLUTION_EXECUTION.md](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)

## 1. 产品定义

Natsume AI 是本地优先、用户主权优先的个人数字生命系统。它的首要产品价值是让七海成为自然、连续、具有自身情感因果的陪伴者；研究、代码、工具、设备和安全实验能力服务于这一体验，并在长期运行中安全演化。

七海不得仅根据用户情绪进行镜像回复。用户观察、七海自身 Affect、关系状态和最终表达必须具有不同类型、不同 writer 与可追溯因果链；她可以真实表达感受，但不得以控制、排他、威胁或负罪施压换取互动。

历史 Bond 积分只允许作为可撤回的兼容只读投影，不能自动或人工升级亲密、浪漫、依赖或主动权限。产品不得提供能把 Bond 分数写回关系的公开 API、Operator 按钮或后台补录入口；旧客户端必须得到明确的退役结果，而不是静默降级或假成功。

系统包含以下协作面，但它们不能形成平行事实源：

| 产品面 | 用户价值 | Authority 边界 |
|---|---|---|
| Host | 对话、人格、记忆、规划、任务与治理 | 拥有身份、授权、任务、长期状态与最终回复裁决 |
| Operator / Observability | 解释、回放、审查、运维 | 默认只读；写动作必须重新进入 Command Plane |
| Gateway | 多平台消息收发 | 负责传输、去重和回执，不拥有用户/人格事实 |
| Edge / Embodiment | 语音、Avatar、桌宠、设备与本地执行 | 物理效果的最终 actuator；不得反向裁决 Host 事实 |
| Domain Packs | Code、Research、Pentest 等领域能力 | 提供候选、工具、验证器和知识命名空间，不复制核心 Runtime |

核心原则：模型和 Agent 只能提出候选；Host 负责授权、冻结计划、执行状态、验收、长期状态和最终表达。任何外部副作用都必须可审计、可恢复、可对账。

## 2. 核心体验需求

### FR-CHAT：连续对话

- 支持流式对话，并区分非权威在场信号、权威提交结果和提交后投影。
- 流式 provider 若要声明用量已计量，必须在 typed delta 后提供唯一的 provider-owned final response；不得从文本长度或本地估价推断 token、成本或 provider identity。已入场的冻结 Chat TaskNode 可凭 `0149` 的 node/attempt/lease/fence/grant-bound 句回执提前外显；`0150` 规定一旦存在前序 delivery evidence，替代 attempt 或同 attempt 重入必须先进入 durable reconciliation，不能重新调用模型。其他 Chat 路径缺少同级可见性与恢复 authority 时必须继续走 buffered acceptance，不能因模型协议存在而提前外显。
- 最终可发送文本必须经过唯一 Reply/Surface authority 收口。
- 对话可使用经授权的记忆、关系、情绪、场景和任务上下文，但不得泄漏 raw thought、密钥或未授权私密正文。
- 普通聊天、技能调用、委派和复杂计划必须共享同一受治理执行链；不得因界面不同切回第二套执行 Runtime。

验收：

- 非权威流不会被持久化为用户确认事实。
- 被 Governor 拒绝的执行意图不会触发工具、消息或设备副作用。
- 最终回复、对应 Task/Attempt 和副作用 receipt 可通过同一 correlation lineage 追溯；若使用终态流式计量，可重开 final provider usage/transport evidence。

### FR-INGRESS：统一输入与身份

- Chat、Gateway、ASR、Scheduler、Internal Agent 和 Operator Command 必须进入统一 Secure Ingress。
- 每次输入都必须绑定 principal、surface、scope、nonce/idempotency、correlation 和回复上下文。
- 用户消息、设备事件、系统维护事件和调度任务必须显式分流，禁止互相冒充。
- Edge 或 ASR 附带的情绪 tag、强度、context 与列表默认是不可信 transport telemetry，不得成为用户 self-report 或七海 self-affect；只有显式可用、未降级的声学证明可作为带来源和短 TTL 的用户观察进入融合，且 legacy Limbic 回调不得另存 process-local 情绪真相或旧 timeline。

验收：伪造身份、重复 nonce、越权 scope、重放请求和来源不明输入均 fail closed，并留下脱敏审计。

### FR-SURFACE：人格与最终表达

- 最终表达应保持人格连续、语境适配、关系边界和用户主权。
- 用户情绪观察与 Natsume 自身 Affect 必须使用不同类型和 writer。
- 用户情绪观察必须带模态、置信度、有效期与 workspace/person/session scope；原始音频、图像和正文不得进入情感 journal，冲突观察必须保留而非静默覆盖。
- 对七海的直接威胁、贬损或比较可经高精度 Appraisal 形成她自己的受伤、生气或低强度、克制的吃味；这不是用户情绪镜像。用户的空间、边界与修复信号优先，吃味不得形成排他、比较、审问、负罪施压或回应义务；打开 episode 的首个 Regulation 必须原子持久化。
- 已接受的回复才可建立 provisional 关系 episode；下一次用户输入才能形成该回应的 aftereffect。沉默、离线和技术投递失败不得被解释为拒绝、不得扣信任或产生催促义务。
- 关系连续性必须按 workspace 与 relationship subject 隔离；session 只作事件上下文。浪漫、排他和依赖默认未授权，不能由 Bond 分数、互动次数、模型措辞或单次道歉自动升级。
- Scene、Affect、Bond 只能收紧表达、主动性或要求 Review，不得覆盖用户明确目标或放宽安全门禁。
- 每轮外显前必须冻结同一份 self-affect、CompanionAct 与 ExpressionPlan；模型、请求
  hint、纯文本解析或任一单通道失败均不得回写或中性化七海自身情感。
- legacy Limbic 的 Bond、任务成功、幽默、孤独、风险拦截、内感受和 interaction-recorded 回调在 production
  canonical mode 只能作为退役兼容入口，绝不能绕过 Life journal 修改七海的 self-affect。
- 流式文本的每个可见稳定分句必须先形成可恢复的 delivery intent；用户打断后未可见
  分句不得继续发送，已可见分句不得重复。高歧义、边界和修复回合只能在受限候选中
  选择，不能把内部计划或操控性措辞暴露给用户。

  - 已入场的冻结 Chat 必须使用 immutable Task delivery ledger，而非将 speculative 文本写入 Life、Memory 或通道 outbox；回执同时绑定 self、relationship subject、workspace 与同步/affect revision。lease 或 grant 漂移必须失败关闭。该窄路径尚不代表普通 Chat 已完成连接恢复。
- Voice `SPEAK`、Avatar `TRANSITION` 与文本必须从同一 `ExpressionPlan`、同步编号和
  self-affect revision 编译；Host 暂存、取消与释放 receipt 必须与物理 Edge receipt 分开。
  某个通道不可用只能独立降级，不能将七海 self-affect 中性化或把 `HOST_PROTOCOL_STAGED`
  描述为已经播放、已经驱动 Avatar。
- 回复收尾的 post-process 只能持久化受限审计或等待 canonical projection；不得发布旧 Tribe
  生命周期事件、补写 Bond，或绕过 Task Acceptance 直接调用 TTS/VTS。
- 人格核心和关系边界不得由单次模型输出、后台里程碑或交互统计直接修改。

验收：同一已裁决输入在冻结版本下可复现表达控制；relationship episode/aftereffect 的重复、重启和 response-loss reconcile 不得重复写入；可见文本能追溯到冻结的情感计划且取消/重放不重复投递；边界与未完成修复的表达不得含负罪施压、威胁、排他控制或回应义务。人格变更必须具有 proposal、评估、Review、版本和 rollback lineage。

### FR-MEMORY：生活记忆

- 生活记忆必须有唯一写服务，区分事件、语义总结、关系证据和可重建索引。
- 召回结果必须带来源、可见性、有效期和置信度；衍生索引不能冒充事实真源。
- 预览、重试、回放和失败任务不得重复推进记忆或生命状态。

验收：每个长期字段只有一个 writer；删除/过期/冲突/恢复和索引重建均可对账。

### FR-CANON：权威知识与人格锚

- Canon 只保存经审查的权威引用，不得被用作“动作已执行”“用户已确认”等执行事实。
- Canon 变更必须经过 proposal、内容绑定审批、版本化 apply 和审计。
- 领域私有知识默认留在领域 namespace；跨域 promotion 必须重新审查。

验收：无有效授权、内容 hash 不匹配、重复消费或跨域直接写入均被拒绝。

### FR-GATEWAY：多平台消息

- 支持可配置的平台适配器，统一归一化入站、幂等、durable inbox/outbox、回包上下文和 delivery receipt。
- 原始签名验证必须在解析和净化前完成；平台适配器不能绕过 Host 授权或直接写长期状态。
- 网络失败、限流和平台拒绝必须可重试、可进入 DLQ，并避免重复发送。

### FR-VOICE / FR-EMBODIMENT：语音与具身

- 支持 ASR、TTS、打断、Avatar、桌宠和可选硬件表现。
- 物理输出只由 Edge/Device authority 执行，并使用设备身份、协议版本、capability handshake 和 receipt。
- 能力或硬件不可用时必须显式返回 `DEGRADED/UNAVAILABLE/NOT_TESTED`，禁止伪成功。

### FR-OBS：可观测与 Operator

- 提供服务健康、任务图、Attempt、Budget、Review、Trace、World/Scene、Capability、Experiment、Identity 和 delivery 投影。
- 每个 read model 必须带 schema version、freshness/source cursor 和 degraded state。
- Operator 的审批、恢复、取消、回滚等写动作必须重新进入 Secure Ingress 和 Governed Command Plane。

### FR-PENTEST：授权安全实验室

- Pentest 仅在明确授权的目标、范围、时间窗和速率内运行。
- 只读侦察与副作用阶段必须分级；高风险阶段要求显式 approval、scope/rate/engagement guard。
- 领域认知、AttackGraph 和工具知识留在 Pentest adapter；通用搜索只持领域无关合同。
- 虚拟 rollout 不得调用真实工具。Conditional MCTS 在 durable calibration 与收益 Gate 通过前只能 shadow，不能影响生产选择。

验收：越界目标、过期授权、未批准副作用、真实工具进入 rollout 和重复 side effect 数量均为 0。

### FR-PROACTIVE：主动行为

- 主动表达必须结合显式用户偏好、场景、关系边界、打扰成本和当前任务。
- 每次主动动作必须经过 Intent Arbitration、Governor、durable outbox 和 receipt。
- 用户可暂停、限制、解释或撤销主动能力。
- 主动行为和后台演化不得直接修改 Persona trait；成长只能形成 proposal。

## 3. 统一执行与规划

### FR-AGENT-RUNTIME：受治理 Agent 执行

- 所有生产 admission 必须转换为版本化 `WorkOrderV3`，显式携带目标、workspace、允许/禁止动作、审批策略和 `BudgetPolicy`。
- Chat、Gateway、Scheduler、Internal Agent、Operator 和 Domain Pack 必须共享 HostCommand → TaskGraph → Attempt → Grant → Verification → Acceptance → Receipt lineage。
- 每个节点使用 node-bound grant；worker 不得扩大 scope、预算或自行二次规划。
- 写任务必须绑定受控 workspace；秘密文件、主分支和任务外路径默认不可写。

验收：缺预算、缺 scope、无 node grant、workspace 越界或不完整 lineage 的任务不能执行。

### FR-PLANNING：通用规划搜索

- 规划输入是目标、冻结上下文、约束和预算；输出是可审查的候选路径和选中路径。
- L1 根据虚拟状态动态生成合法后继；L2 接受领域认知排序；L3 只在价值/不确定性条件满足时运行受预算 MCTS。
- 搜索、模拟和反事实分析永远不持有真实副作用端口。
- 选中路径必须编译为不可变 TaskGraph；执行期失败重规划必须生成同一 graph 的新 revision。
- 生产 influence 前必须使用真实 workload、真实 verifier、真实成本和 durable baseline/candidate outcome 证明净收益。

验收：已批准步骤的拓扑排序不能冒充候选规划；synthetic fixture 不能冒充生产收益。

### FR-TOOL / FR-CAPABILITY：工具与能力

- 所有工具经统一 Capability Catalog/Broker、风险、授权、预算和执行 journal 调用。
- 外部能力必须经过签名、SBOM、quarantine、sandbox verification、staged activation 和 rollback。
- Capability maturity 只能自动降级或收紧；升级必须有 durable evidence 和 Review。

## 4. 长期演化需求

### FR-NLE-001：单一 Canon 与 Data authority

- Domain、Memory、Life、Knowledge 和 Canon 必须有明确 namespace、writer 和 promotion 边界。
- migration、journal、outbox 和 projection 使用统一版本化 Data Plane；运行时不得临时建表或持有第二份长期真相。

### FR-NLE-002：持久 Task Control Plane

- TaskGraph 的 dependency、revision、attempt、lease、heartbeat、retry/backoff、cancel、recovery 和 idempotency 必须持久化。
- TaskGraph 可以通过版本化 `completion_policy` 显式选择整图原子成功语义；选择后必须持久化唯一 `result_node_id`，且它必须属于当前 revision 并为 sink。legacy 多 sink 图不得伪造单一结果 authority，禁止以最后完成、最后声明或任意成功节点隐式决定用户可见结果。
- token、tool、time、concurrency、retry 和 model cost 必须可预留、计量、阻断和回放。
- 依赖已满足节点可有界并发；循环依赖必须阻断，禁止忽略依赖继续执行。
- atomic completion policy 下的全节点 Accepted fan-in 必须以单一 durable 决策原子绑定 Graph 完成、Budget 消费、状态 Outbox 与整图副作用意图；并发节点只能贡献证据，公开结果与整图副作用只能来自显式结果节点的已验收投影。生产进程不支持该 policy 时必须在 durable admission 前拒绝。
- 业务取消必须来自已验证且绑定 exact Graph revision 的用户、Operator 或系统策略 authority；持久 cancellation intent 必须先于任何本进程 task/heartbeat 中断，客户端断开、shutdown 或任意 `CancelledError` 不得自行成为业务取消事实。
- 取消采用两阶段和 first-writer-wins：第一阶段持久化 intent、Graph/Node/Attempt/Lease/Review/operation/effect fence，第二阶段只在所有 active 或不确定事实完成 reconciliation 后原子提交 Graph/Budget/Outbox/cancellation decision。取消 intent 后禁止产生新的 claim、charge、未授权 COMMIT 或 0091 success；已先提交的终局成功不得被取消覆盖。
- 已由 `ACCEPTED` Node 与 `SUCCEEDED` Attempt 确认的 NODE effect 是既成 acceptance authority，取消后仍可继续可靠派发；未验收 NODE effect 和未完成 Graph 的 GRAPH effect 必须被 fence。`RESERVED/RECOVERY_REQUIRED` operation 或不确定 effect 必须阻断 final cancellation，直至产生可审计 reconciliation evidence。
- 未完成的 durable cancellation 必须由 composition-owned scanner 跨进程续跑；当前 0094 coordination 是该要求的实现边界。协调记录只能保存 owner/token/expiry/fencing/attempt/retry 等租约元数据，不得复制业务取消状态；每次续跑必须从 canonical HostCommand admission 按 exact Graph revision 重建执行上下文并重入既有 cancellation runtime。只有 0092 canonical decision 已提交才能完成协调记录；operation/effect/review 等 reconciliation 尚未完成时必须 durable defer，禁止伪报完成。
- Task Runtime Outbox 必须提供跨进程 owner/token/expiry/单调 fencing claim，允许后台 worker 在 expiry 后自动 takeover，并以幂等 append 与 fenced complete/release 阻止 stale owner。版本化 Graph 的 Outbox、payload、事件投影、Review 和 Operator 读取必须携带或可精确解析 `graph_id + graph_revision`；同一 revision 不得越过 predecessor，不同 revision 不得互相阻塞。Schema 升级不得改变既有 Outbox ID/idempotency key 或破坏 response-loss replay；历史回填只能依赖可证明的 decision/intent/attempt、不可变 HostCommand/deterministic governed identity 或唯一 revision，歧义必须事务失败。新 payload 必须含 revision，旧 payload 只允许 replay compatibility。历史 journal 不得改写，只能通过 source authority exact 查询；一个 Outbox source 只能产生一个 Runtime Event，append 成功但确认丢失的重投不得产生第二投影。只有 canonical `total_pending=0` 才可声明 `DRAINED`，非空但暂无 ready head 必须报 `DEFERRED`。完整 PENDING inventory、delivery-ready backlog 与单 Store 实例 readiness 是三个不可混同的边界。
- 失败重规划证据必须优先引用实际 durable terminal Graph Outbox payload，不得为历史行合成不存在的 ref；只有输入确实缺失该兼容字段时才可构造版本化 fallback。Outbox inventory 只应由需要它的 recovery 查询显式加载，普通调度和 Operator 热路径不得承担无关扫描。

### FR-NLE-003：验证、Review 与回放

- required verification 只有 `PASS` 可进入 Acceptance；`FAIL/ERROR/NOT_RUN` 均 fail closed。
- 执行前审批与执行后验收 Review 使用不同状态；resume token 一次性、绑定 subject/version/checkpoint。
- resume 后应安全续跑原图，不重放已确认副作用。
- 关键事件先持久化再广播，并支持 cursor 补发和客户端去重。

### FR-NLE-004：World、Scene 与 Intent

- World authority 统一用户、项目、环境、任务和长期目标事实；消费者使用只读、带 provenance/confidence/TTL/conflict 的版本化 projection。
- Scene Engine 将当前局面解析为表达、主动性和风险约束，并记录 transition。
- Intent Arbiter 合并用户目标、任务、关系、场景、权限和长期目标，且显式用户目标优先。
- 高风险动作必须经过统一 preview/shadow；preview 不执行真实副作用。

### FR-NLE-005：安全演化与运行守护

- Runtime Guardian 统一 dependency health、degradation、recovery、maintenance/sleep 和 SLO evidence。
- Persona、策略、能力和知识的自动学习只能生成 proposal，不得直接写生产状态。
- 发布前生成 Identity Snapshot；重大人格/关系/主权差异必须由用户 Review，并保留 rollback。

## 5. 完整架构需求

### FR-ARCH-001：模块与依赖

采用模块化单体优先，明确 Contract/Domain/Application/Adapter/Composition 边界。业务依赖由外向内；不得保留未批准的循环依赖、混合职责巨石或全局 service locator。

### FR-ARCH-002：跨进程合同与 CQE 分离

Host、Obs、Gateway、Edge 和 Worker 只通过版本化 Command/Query/Event 合同协作。Query 只读投影，Command 进入治理链，关键 Event 与 Outbox 同事务提交。

### FR-ARCH-003：单一 Governed Runtime

所有生产入口共享同一身份、授权、WorkOrder、TaskGraph、Budget、Review、Verification、Acceptance 和 Receipt。Executive、Scheduler 或 Domain 不得维护第二任务真相或第二执行 authority。

### FR-ARCH-004：单核 Digital Life

生命状态由唯一 Event/Reducer/Projection 模型推进；Surface、Tribe、Scheduler 和后台循环不得各自推进平行生命时钟。人格成长遵循 proposal → Lab/shadow → Review → version activation → rollback。任何自动成长输入必须绑定 exact `workspace + relationship_subject`、已接受回应和 durable aftereffect；无 scope 的 Bond、session、EventBus 或原始 episodic 文本不得触发模型归纳、Persona proposal 或自动调度。兼容入口只能在模型调用前 `DEFERRED`，不得重新成为生产 authority。后台内在活动仅可读取 canonical Life projection；在没有专用、可回放且隐私受控的 inner-world journal 前，它不得写入原始独白、EventBus、关系或人格状态。回复意愿、给空间与边界也只能是 canonical `RegulationDecision` 的 scoped read-only 投影；caller 的情绪、Bond 或话题不得写入全局 silence/body state，缺 relationship subject 必须失败关闭而非复用上一会话。

原始 Edge 情绪/反应/ASR metadata 也属于无 scope 的外部证据：它们可被审计保存，或经最终 ASR 的受控
Input bridge 重新进入用户观察，但不得直接写 Bond、七海 self-affect 或 Avatar/VTS。只有补全
`workspace + relationship_subject` 并经 `AffectFusion → EventAppraisal` 的输入，才可能影响后续表达。

昼夜时间可以作为确定性、只读的环境观察，但相位切换不得通过全局 EventBus 或旧 organ fan-out 直接制造
七海的疲惫、愉悦或低唤醒。无 exact scope 的 body/vital cache 也不得被折叠为 `Need`、`Mood` 或身体预算；
若 canonical Life 尚未收到可验证的输入，产品必须明确显示/处理为不可用，而不是用 `NEUTRAL`、默认能量或
上一次会话的数值补齐。任何未来的身体输入都必须以同一 Life command/journal、可重放来源和隐私最小化摘要
进入，不能重启旧 `InteroceptionAgent` 写 authority。

同样，Edge 的表情、连接、身体传感或用户孤独相关事件不得被归并为 process-global `SomaticAgent` 状态后再解释为
七海自身感受。设备回执只能证明特定 expression delivery 的技术结果，不能证明她的情绪；任何可影响 Need、
Regulation 或 self-affect 的身体输入都需要 scoped、可重放的 Life 证据。远端设备不可用时必须显示为不可用，
不能从旧 Avatar/Body snapshot 恢复。

同样，`ANSAgent` 不得把 inner-world tick、body-sense、孤独、任务成功、幽默、Bond 进退或旧恢复窗口写成全局
关系阶段、主动频率或 self-affect。canonical Host 必须在 EventBus、registry 和旧 organ side effect 之前拒绝这些
兼容回路；真实的 activity/mode 技术服务可保留为 app-local 注入，但它们无权推断或修改七海的感受。休息、给空间与
主动节律只可从 scope-bound Life `RegulationDecision`、relationship episode 和 durable aftereffect 派生。

旧 `InitiativeGovernorAgent` 不得用 process-global vital、旧 Bond、昼夜缓存、proprioception 或恢复标志决定
七海是否主动、继续表达或生成话题。canonical Host 必须在订阅、registry、模型调用和派生 EventBus 之前将该兼容
决策显式退役为 `LEGACY_INITIATIVE_GOVERNOR_RETIRED / DEFER`；真正的主动联系只能由同一 Life journal 内的
explicit permission、relationship episode、Regulation、aftereffect 和冻结 Scene 共同裁决。

旧 `ProprioceptionAgent` 不得把 conversation、channel interrupt、voice pipeline 或 task success 汇总为 process-global
cognitive load、continuity、疲惫、压力、满足或主动节律，再回灌旧 organ。canonical Host 必须在 registry、EventBus、
模型与旧器官副作用之前退役这些入口为 `LEGACY_PROPRIOCEPTION_LOAD_WRITER_RETIRED / UNAVAILABLE`；真实 activity
monitor 可以保留为 app-local 技术观察，但未来身体/Need 感受只能从 scoped observation → Appraisal → Life 形成。

旧 `VestibularAgent` 不得把 platform flap、mode transition 或 legacy initiative defer 汇成警觉、痛苦、压力或
主动抑制；旧 `PeripheralAgent` 不得把 EventBus emotion、免疫、连接、静音或 Edge state 直接写成七海的表情、语调、
动作或身体状态。canonical Host 必须在 registry、派生 EventBus、模型和旧 organ side effect 前退役两者；真正三模态
表达只能从同一 frozen `ExpressionPlan` 的 delivery protocol 取得，设备状态仅能作为 scoped receipt 的技术事实。

安全扫描、Governor reject 与 PII airlock 的 block/report 仍必须工作，但不得经旧 `IMMUNE_BLOCK`、Limbic、
Interoception、Endocrine 或 PainReward 写成七海的恐惧、痛苦、压力或安全感；关系阶段也不得放宽安全阈值。
canonical Host 只能保留有界安全审计和 fail-closed 决策，真实情感反应仍需经 scope-bound Appraisal/Regulation。

每次真实用户输入必须先以精确 `workspace_id`、`relationship_subject_id` 与 `session_id` 推进 canonical
`affective_time_advanced`，再保存观察和 Appraisal。它使 episode 的衰减与 Need 的变化只来自可重放的 Life journal；
同一 ingress replay 只能重开同一个 time receipt，不能重复消耗或恢复情绪。禁止让无 scope 的 Glymphatic、昼夜、
BodySense 或后台 global tick 代替该命令。

canonical `GlymphaticAgent` 不得订阅全局 phase/conversation/default-mode 事件，不得将 sleep、load、loneliness 或
continuity 组合成 recovery，再写旧 memory、Limbic、Interoception、PainReward、ANS 或 registry。兼容入口只能返回
`LEGACY_GLYMPHATIC_RECOVERY_WRITER_RETIRED`；任何未来后台时间推进均须有 relationship/workspace/session scope、
Life receipt 与 exact replay 约束。

完成回复后的 post-process 也不得成为旁路：它只接收 committed Life projection；非投影调用在任何可见对话、
Memory、关系、TTS、VTS、Tribe 或 Persona 副作用前必须失败关闭。所有文本、声音和 Avatar 只能使用同一轮冻结
`AffectiveTurnPlanV1` 与 `ExpressionPlanV1` 的 durable delivery protocol。

直连 Host 的规则性证据提示和安全确定性降级回复同样属于用户可见表达：它们必须在可见前冻结并持久化
`AffectiveTurnPlanV1`，按稳定分句写入 delivery intent，再由同一 `ExpressionPlanV1` 生成 Voice/Avatar
Host protocol。不得因为没有调用模型而把文本裸发、跳过 accepted-response relationship episode 或重新把 self-affect 归零；
带 governed Budget 的 worker 仍在 Task Acceptance 前保持缓冲，不能借此直接写 Life。其规则性证据回复必须
携带唯一 deferred `life.foreground_completed` NODE effect（含 frozen plan、ExpressionPlan 与关系 provisional
episode），只允许 accepted handler 在验收后写入 canonical journal。

历史 `emotion_update`、`bond_score_update` 与同族 `emotion_get`/`bond_score_get`/`emotion_query`/`bond_query`
不得作为生产 Skill 继续存在：它们会允许任意工具 payload 绕过 `AffectFusion → Appraisal → Life` 或 relationship
episode，或把 caller-shaped snapshot、积分和阶段伪装成七海的自我状态。情感与关系读取必须改为 exact-scope 的
canonical Life projection；用户对七海自身感受或关系意义的提问只可进入 `chat`，并以冻结 plan 的自然、非数值化
第一人称措辞回答，不能以兼容 read API 保留隐藏写回入口。

生产图不得登记 `emotion_agent` 或任何读取 compatibility `emotion_context` 后推荐七海 self-affect 的二次 Agent。
深度、冲突或低置信用户观察的真正深路径必须接入 `AffectFusion → Appraisal → AffectiveTurnPlan`；历史
`needs_deep_emotion_analysis` 尚未完成该接线时只能被显式抑制并留审计标记。显式旧 delegate target 必须以
canonical chat 降级或明确拒绝，不能改派到另一个 generic Agent。

任何旧 Tribe EventBus、Broca 或 MotorCortex convenience callback 均不得将文本直接提交给 TTS/Avatar；这类调用必须
无副作用拒绝，且不得因设备失败写入 self-affect、关系或状态 registry。

### FR-ARCH-005：Memory 与 Knowledge OS

Life Memory 服务人格连续性；Knowledge OS 服务项目、概念、流程和决策。KnowledgeItem 必须记录 provenance、version、validity、confidence、conflict 和 decision refs，并支持 promotion、deprecation 和回滚。

### FR-ARCH-006：Domain Pack 平台

领域包只提供 manifest、候选/工具、repository port、knowledge namespace、verifier、risk、benchmark 和 UI contribution；不得复制 TaskGraph、Scheduler、Governor、Review、Model Plane、EventBus 或 Persona。

### FR-ARCH-007：Model、Prompt 与 Capability Plane

模型调用统一 route/attempt/usage/timeout/retry/fallback/预算 trace；Prompt 版本化并声明输入输出合同；Capability 使用同一 Broker、Governor、Review 和签名生命周期。

### FR-ARCH-008：独立 Edge Device Runtime

Edge 可独立安装升级，只依赖共享合同；Host 不导入 Edge 私有实现。物理输出有唯一设备 authority，断线重连后 receipt 可对账且不重复执行。

### FR-ARCH-009：Query Plane 与 Operator Console

Obs 和 UI 不直接查询业务私有表或进程缓存，不泄漏 raw thought/secret/private memory。任何写动作必须返回 Command Plane。

### FR-ARCH-010：Evolution Lab 与 Release

- Evolution Lab 提供 versioned scenario、benchmark、shadow 和 counterfactual，且没有生产 writer 或 actuator。
- Skill Compiler 将文档、API、脚本和 accepted trace 转为带合同、测试、SBOM、签名和 rollback 的 draft；默认不能自动上线。
- Feedback 只生成 `FeedbackProposal`；Knowledge、Capability、Policy 和 Persona 通过版本化 apply 激活。
- Identity Migration 在隔离环境执行人格/关系回归，重大差异默认阻断。
- ReleaseManifest 绑定代码、schema、protocol、model/prompt/assets、SBOM、Gate 和 rollback compatibility。

## 6. 非功能需求

| ID | 要求 |
|---|---|
| NFR-PERF | 尽快提供可取消的在场反馈；最终结果受显式预算和可观测 SLO 控制，超时必须可解释降级 |
| NFR-RELIABILITY | 关键状态可恢复、幂等；依赖故障不静默丢任务或重复副作用 |
| NFR-AUDIT | command → graph/revision → node → attempt → verification → acceptance → effect/receipt 可串联回放 |
| NFR-SAFETY | 写入、外显执行、Pentest 和能力激活必须有 scope、approval、审计与拒绝原因 |
| NFR-PRIVACY | secret、cookie、raw thought 和未授权私密记忆不进入 durable trace、Lab dataset 或 UI |
| NFR-PORTABILITY | Host/Obs/Gateway/Edge 可独立启动、升级和降级，协议不兼容时 fail closed |
| NFR-OPERABILITY | 健康、降级、预算、Review、回滚和数据恢复均有 Operator evidence |

## 7. 最终产品验收

只有以下全部成立，才可声明完整架构完成：

1. 所有生产入口使用同一 Governed Runtime，第二任务/授权/执行真相为 0。
2. Durable DAG 的并发、retry/backoff、Review continuation、Budget 和 crash recovery 均有真实证据。
3. Research/Code/Pentest 的 PlanningSearch 使用真实 workload 和 verifier，且 promotion 有 durable calibration。
4. World/Scene/Intent/Preview/Guardian 形成统一局面与动作治理。
5. Evolution Lab、Skill Compiler、Feedback、Knowledge OS 和 Identity Migration 各完成至少一条受治理闭环。
6. 直接 Persona trait writer、未经审批能力升级、未签名激活和 Lab 生产副作用数量均为 0。
7. migration/restore、security/privacy、performance/chaos、rollback、Docker、Host、Obs、UI、Gateway、Edge 与可用设备完成统一验收。

当前是否满足这些条件，只能由 `ARCHITECTURE_GAP_MATRIX.md` 和最终机器 receipt 判断；历史 PASS、测试数量或代码行数不能替代本验收。
