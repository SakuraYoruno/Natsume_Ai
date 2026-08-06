受众：CODEX
标题：Natsume AI 数据流架构

# 数据流架构

## 1. Tribe 智能体协作

```mermaid
flowchart LR
    INPUT["感官、环境、消息与任务上下文"] --> FRONT["Cortex / Thalamus / Immune"]
    LIFE["Canonical Life Snapshot"] --> BUS["App-local Tribe Event Bus"]
    FRONT --> BUS
    BUS --> SPECIALISTS["情感、社会、记忆、执行、身体、行动与恢复 Agent"]
    SPECIALISTS --> CONTRIBUTIONS["观察、评估、handoff、候选动作与运行投影"]
    CONTRIBUTIONS --> BOUNDARY["Application Boundary"]
    BOUNDARY --> COMMAND["受约束的 Life Command"]
    BOUNDARY --> ACTION["Task / Delivery Intent"]
    COMMAND --> LIFE
```

生产 Host 为 23 个 Agent 构造一个 owner-bound runtime scope。事件与 `TribeStateRegistry` 只承担进程内协作；它们不能直接成为七海长期自我。需要改变 self affect、关系、成长或主动节律的贡献必须经过 application boundary，写为可重放的 canonical Life command。有外部副作用的动作则进入 Task 或 Delivery 路径。

## 2. 普通情感对话

```mermaid
sequenceDiagram
    participant U as 用户
    participant S as Surface
    participant T as Tribe
    participant F as Affect Fusion
    participant L as Digital Life
    participant M as Model
    participant E as Edge

    U->>S: 文本／语音观察
    S->>T: scoped context / intent
    T-->>S: route + bounded contributions
    S->>F: scoped observations
    F-->>S: hypotheses + confidence + conflicts
    S->>L: appraisal command
    L-->>S: self-affect revision + companion act
    S->>M: frozen AffectiveTurnPlan
    M-->>S: streaming tokens
    S->>E: stable sentence + ExpressionPlan
    E-->>S: TTS／Avatar receipts
    S->>L: accepted outcome / provisional episode
    U->>S: 下一轮反应
    S->>L: relational aftereffect
```

关键不变量：用户 observation 不直接写 self affect；纯文本回合不重置为中性；已交付分句不重复；技术失败不当作关系拒绝。

情感聚合键由 `workspace_id + relationship_subject_id + session_id` 共同派生，不能只按 person 或 workspace 复用。A15 隔离 canary 在四组交叉 scope 上创建四个 aggregate，并只对主 scope 推进 1001 次时间事件；其他 scope digest 保持不变。Fusion 会省略 raw audio/image/video，reducer 也拒绝携带 raw 字段的旁路 command；完整 affective command、event、snapshot 与 projection intent 扫描未命中合成隐私 sentinel。重启时使用新 Repository 从同一 SQLite 重放，最终 snapshot 与 digest 完全一致。

```mermaid
flowchart LR
    O["Scoped observations"] --> F["AffectFusion · omit raw modality"]
    F --> C["Validated LifeCommand"]
    C --> J["Canonical Life journal"]
    J --> S["Bounded snapshot · 4 episodes / 8 components / 16 refs"]
    S --> R["Repository restart + exact replay"]
    X["Raw-field bypass command"] -->|"reducer rejects"| B["No receipt / no journal mutation"]
```

自然中文先经进程内纯函数 observer 形成低置信、多标签 observation；`AffectFusionService` 再把“不顺”“好消息＋不踏实”“不要建议”和“不要限制现实关系”分别评价为 distress、mixed affect、listen boundary 与 relational autonomy。只有 Appraisal reducer 能改变七海自己的 component。该快路径不调用模型；模型只在冻结后的 `AffectiveTurnPlan` 内完成措辞。

普通低风险回合生成一个回复；冲突、边界和修复等深路径在同一次请求中生成两个候选。中文文本＋日语声线模式下，候选是两个完整的配对对象而不是两个裸中文字符串；本地评分只决定选择哪一个，不能重新生成情绪，也不能丢掉选中候选的日语发声字段。首轮候选数量错误会触发唯一一次语义修复；第二轮若只有一个完整且安全的中日配对对象，可以明确降级发送但不计双候选通过，避免私人陪伴因输出形状问题整轮沉默。其余连续协议或安全失败仍记录为语义重试耗尽，不伪装成 Token／成本预算超限。

无前序 Task artifact 的普通 Gateway 回合在冻结模型目标时选择 `CHAT_SURFACE_REALTIME`。当前 DeepSeek Tier B 由 provider adapter 把服务端 `thinking_mode=disabled` 映射为请求的 `thinking.type=disabled`，使 completion 预算用于中文正文和日语 TTS 配对 JSON；该字段不会进入 Gemini、OpenAI 等其他 provider。需要候选择优、规划或研究的路径继续使用各自模型场景与思考策略。

候选选择顺序固定为“逐候选关系／安全／Voice 完整性门禁 → 在合格集合内评分择优 → 交付”。某个高分候选带追问、否定复杂感受或关系防御时，只淘汰该候选；只有所有候选都不合格时才触发语义重试或终止，不能让一个坏候选拖掉另一个可发送候选。

低风险 Companion Profile 不旁路这条链路。五条跨日至少三天、helpfulness 下界不低于 0.85 且无边界违规的 canonical aftereffect 只能创建可回滚 canary；接下来的五次强后效应把同一候选自动激活。Planner 将 profile 变成文本 style anchor，ExpressionPlan 同步缩放 Voice rate 与 Avatar motion。Rollback 只能恢复 active profile 自带的 exact predecessor，不能由调用者指定任意历史版本。A15 隔离 SQLite canary 已验证 Host 协议上的 `文本策略改变 + Voice 1.0→1.15 + Avatar 0.5→0.8`，并在 rollback 后全部恢复；它没有执行模型生成和远端设备。

```mermaid
flowchart LR
    E["5 条跨日强 aftereffect"] --> C["Reversible profile canary"]
    C --> P["Planner style anchors"]
    C --> V["Voice rate"]
    C --> A["Avatar motion"]
    C --> I["5 次 canary interaction"]
    I --> X["Auto activate same profile"]
    X --> R["Exact predecessor rollback"]
```

## 3. 有副作用的任务

```mermaid
flowchart LR
    C["HostCommand"] --> G["TaskGraph"]
    G --> A["Node Attempt + Lease"]
    A --> B["Budget + Grant"]
    B --> W["Worker / Tool / Delegate"]
    W --> V["Verification"]
    V --> AC["Acceptance"]
    AC --> O["Outbox"]
    O --> R["Effect Receipt"]
```

节点失败、取消、重试和未知副作用在 Task Runtime 内恢复。只有 accepted effect 才进入外部交付。

Node Grant 的有效期不是 Worker Lease 的镜像：它至少覆盖冻结时间预算，并额外保留一个 survivor 扫描窗口。这样 Host 在模型调用前崩溃时，旧 Lease 到期后仍有一段受限授权窗口可取得更高 fencing token；恢复仍复用同一 HostCommand、TaskGraph、Node 和 Attempt，不重新观察情绪、不重新规划，也不生成第二张图。

A15 真实进程注入已经验证这条路径：旧 fencing token 1 Lease 到期后，survivor 以 token 2 接管同一 Attempt；接管时 Node Grant 尚余 57.211 秒。最终 TaskGraph `COMPLETED`、TaskNode `ACCEPTED`、Budget `CONSUMED`，6 个 Acceptance Effect 均只提交一次。该结果只关闭 Host pre-Operation 崩溃项，不替代 unknown effect、rollback 或外部平台物理送达验收。

图已终止但预算或节点未结算时，terminal reconciliation 只能自动处理能由 canonical durable evidence 唯一推出的修复；任何需要解释或权限扩张的组合都保持原状并阻断生产就绪。Canary 报告绑定实际 migration catalog head，禁止用历史 migration 名称兜底制造“当前 schema 已验证”的假象。

operation reservation 已持久化、但 receipt 尚未提交时发生崩溃，系统不能判断外部调用究竟“尚未执行”还是“已执行但响应丢失”。恢复器因此只允许 `RUNNING → RECOVERABLE`、`RESERVED → RECOVERY_REQUIRED`，并追加 `UNKNOWN_SIDE_EFFECT` hold；在签名 reconciliation 给出 `NO_SIDE_EFFECT` 或 `SIDE_EFFECT_CONFIRMED` 前，禁止新建 Attempt、重新调用 worker 或提交 Acceptance Effect。这个高风险恢复边界不进入没有外部副作用的普通实时对话主链。

Gateway 平台外发采用相同的效果边界，但保持更轻量：payload 校验和 adapter selection 都在 `dispatch_started` 前完成，因此明确未发送时可以用同一冻结 Outbox 有界重试；真实 adapter 调用开始后若响应丢失，记录直接进入 `GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN`／DLQ。此后 duplicate enqueue、进程重启和普通 replay 都只返回已有事实，不再次调用平台；只有独立 reconciliation 能决定下一步。Loopback OneBot HTTP 可以证明本地 socket、SQLite 和重启语义，不能证明 QQ／微信客户端实际收到消息。

Container 启动是另一条生命周期边界：Coordinator 已激活、Continuation 尚未就绪时若启动失败，逆序回滚必须先静默 worker 再关闭 Coordinator。任一关闭失败都会保留 bound runtime 与 Store 并进入 `LIFECYCLE_RETRY_REQUIRED`；这时既不能再次 activate，也不能关闭底层 SQLite。后续 retry 只有在全部 quiescence proof 通过后才能 drain Outbox 并逆序关闭基础设施。

```mermaid
flowchart LR
    A["Node Grant 生效"] --> B["冻结时间预算"]
    B --> C["旧 Lease 到期"]
    C --> D["恢复扫描窗口"]
    D --> E["更高 fencing token 续跑"]
```

只读 Delegate 也不共用一套模糊执行权限：Pentest handoff 不接模型或工具并提交精确零用量证明；翻译 Delegate 从冻结 Node Budget 生成不可扩大的 model guard，经唯一 `ModelAttemptService` 调用 `workbench_translate`，把真实 provider usage 结算回 Node Budget。翻译结果本身没有 delivery authority，只有图 Acceptance 后才可进入 Gateway 回复。

```mermaid
flowchart LR
    D["Frozen Delegate Node"] --> P{"Target policy"}
    P -- "Pentest · port-free" --> Z["Read-only handoff + zero usage proof"]
    P -- "Translate · model-backed" --> B["Task-bound model budget guard"]
    B --> M["ModelAttemptService / workbench_translate"]
    Z --> A["Node Verification / Acceptance"]
    M --> A
```

Gateway 入站聊天属于“实时生成 + 有副作用交付”的交界：消息经 Secure Ingress 后编译为紧凑单节点 TaskGraph，聊天生成仍只执行一次，Acceptance 再原子写入可见对话和 Gateway Outbox。直接网页／本地聊天继续走低延迟实时链，不承担这层外部投递账本。

直接聊天的上下文准备遵守“先计划、后读取”：`NO_RECALL` 直接生成跳过包，不进入显式记忆 helper 或语义嵌入；`MAY_RECALL／MUST_RECALL` 才检查近期显式记忆并进入对应召回预算。近期对话连续性仍由 Surface 的有界 history window 独立消费。旧显式记忆 helper 对无关文本本已有内容级短路，所以这个计划级收敛只证明调用边界，不把一次真实样本差值冒充确定的 SQLite 收益。

召回后的领域增强同样先做廉价必要条件。普通情绪陪伴直接跳过轻小说最近推荐上下文；“刚才第二本的详情”等可能指代推荐的消息才读取 durable preference pack，补齐 recommendation packet 后执行完整 light-novel predicate。深读与 evidence 回写复用同一 packet，避免一轮内重复读取。

`MAY_RECALL／MUST_RECALL` 进入真正检索时，从 Agent Runtime app-local scope 取得同一个 RecallFusionService；该服务使用 canonical EpisodicStore 已持有的 Chroma client。请求只创建本轮 query embedding 与局部候选，不再创建第二个 HTTP client 或第二份服务 owner。

OBS 的 Task 读模型不在交互事务中同步构造。Sidecar 每 15 秒读取 canonical Task 索引，优先补齐新图，再按最旧 freshness 每波刷新最多 16 个详情；每次派生写提交后让出 5 ms canonical writer window。这样 Monitor 最终一致地看到 TaskGraph／Budget／Verification／Acceptance，同时等待写入 worker projection 的 Host 不会被一整波派生事务连续饿死。worker projection 本身在线程持久化，但 Task Runtime 必须等待其完成，不能把未落库 COMMIT 交给 Verification。

实际全栈复验观察到详情批次上限为 16、index 加详情上限为 17；三轮 worker projection 到后预算落库为 187／94／49 ms，而旧路径峰值为 2,880 ms。该证据关闭了 OBS 连续抢写这一段阻塞，后续仍需分别处理模型语义修复和 artifact 冷态尾部。

Gateway INBOX 固定请求体与 Host 幂等键；即使第一次 HTTP 响应丢失，Secure Ingress 和 HostCommand repository 也只返回既有图的精确结果，因此可以有界重试。向 QQ 等平台发送的 OUTBOX 不具备同等可证明语义，dispatch 开始后的未知结果必须进入对账隔离，不能盲目重发。

INBOX 与 OUTBOX 由同一 app-local Delivery Runtime 管理，但不共用执行线程。`gateway-durable-inbox` 只领取并向 Host 交付冻结入站；`gateway-durable-outbox` 只执行平台发送。两个方向各自使用 `worker_id:inbox/outbox`、Lease 与 fencing token。平台调用阻塞或进入 uncertain DLQ 不会阻塞下一条用户消息被 Host 接管，停止信号和 readiness 仍由同一 Runtime 统一收口。

HostCommand 一旦与 Secure Ingress claim 原子绑定，Gateway 重试只验证 claim、command、principal 和 body digest 后确认 durable handoff，不重新运行情绪观察、反馈、路由或图编译。Task Runtime 独立负责把已接管图推进到 Verification／Acceptance／Outbox。

```mermaid
flowchart LR
    GW["Gateway webhook"] --> SI["Secure Ingress"]
    SI --> HC["HostCommand / one-node TaskGraph"]
    HC --> CHAT["Affective chat generation"]
    CHAT --> AC["Verification / Acceptance"]
    AC --> OUT["Gateway Outbox"]
    OUT --> SEND["Idempotent platform delivery"]
    SEND --> REC["Delivery receipt"]
```

## 4. 语音与 Avatar

Acceptance 只代表 Host 已决定表达，不代表声音已经播放或动作已经执行。Host 为每个 TTS／Avatar effect 写入 durable intent；Edge 接收后，排队中的语音保持 `IN_DOUBT`，物理播放完成或失败时再写 terminal receipt。Host 只按同一 effect identity 对账，不盲目重放未知外部副作用。

```mermaid
sequenceDiagram
    participant H as Host Acceptance
    participant O as Embodiment Outbox
    participant E as Edge Runtime
    participant D as TTS / VTS Device

    H->>O: append expression intent
    O->>E: signed effect + idempotency identity
    E-->>O: queued / IN_DOUBT
    E->>D: execute physical effect
    D-->>E: terminal result
    E-->>O: APPLIED or REJECTED receipt
    O-->>H: reconcile exact effect
```

```mermaid
flowchart LR
    P["ExpressionPlan revision R"] --> TXT["文本节奏"]
    P --> VO["voice profile"]
    P --> AV["avatar transition"]
    VO --> TTS["GPT-SoVITS V4"]
    AV --> VTS["VTS / VRM"]
    TTS --> SYNC["synchronization id"]
    VTS --> SYNC
    SYNC --> REC["delivery receipts"]
```

当前方案显示中文文本、生成日语语音。各通道独立失败与降级，不反向修改七海情绪。

## 5. 主动陪伴

```text
relationship state + unresolved episodes + time + user state + permission
→ proactive candidate
→ quiet-hour / refusal / frequency policy
→ accepted delivery intent
→ Gateway/Edge
→ next user response or technical outcome
→ durable aftereffect
```

明确拒绝立即生效；连续忽略只降低频率，不扣信任，也不升级追问。

## 6. 偏好成长

```text
independent aftereffects
→ low-risk preference proposal
→ confidence and boundary checks
→ 5-interaction canary
→ activate version
→ next-turn text/TTS/avatar behavior
→ exact rollback when degraded
```

核心人格、价值观、亲密身份和敏感推断不进入自动成长。

## 7. 观测流

Host lifespan 心跳分别读取 Host 自身和真实 Edge 子服务，只把明确探测到的事实写入 canonical `service_health_log`；Host-only 自检不会再写一组较新的 Edge `NOT_TESTED` 覆盖远端事实。RPC 端口可达只作为缺失子服务结果时的 fallback，不能把关闭的 barge-in 或故障的播放设备标为 `UP`。OBS 从 canonical SQLite ledgers 生成只读 projection，Monitor 通过 OBS/Host API 展示状态。Monitor 的 React state、toast 或缓存不是业务成功证据。

Gateway 页面同样区分服务进程、durable transport 与平台通道详情。Gateway 本身 `UP` 但平台详情尚未上报时，读模型保留 `UNKNOWN` 并显示未确认数量；只有具体平台 observation 才能改变对应状态。

```text
canonical ledgers
→ OBS public projection
├→ current document：普通 JSON，直接服务 Monitor 查询
└→ immutable recovery checkpoints：默认每 kind/key 每小时至多一个
   ├→ Host source instance 切换：强制记录；同实例 revision 按小时检查点
   ├→ 小对象保持旧 JSON；大对象使用有界 zlib/base64 envelope
   → size + source digest 校验
   → rebuild / history reader 透明解码
```

压缩和检查点间隔只作用于派生 projection history。检查点之间的最新公开文档与 source cursor 仍原子持久化，Monitor freshness 不会降成小时级；极端情况下从 journal rebuild 最多恢复到上一个检查点，随后由下一次 source refresh 追平。解压后的公开载荷必须与 journal `source_digest` 一致，声明大小超过上限、编码损坏、尾随数据或摘要漂移均以 `OPERATOR_PROJECTION_DATA_INVALID` 阻断；canonical 账本和当前 Monitor 读路径不经过该 envelope。

## 8. 数据保留

| 数据 | 默认策略 |
|---|---|
| 原始音频／图像 | 不落盘 |
| 用户正文 | 对话存储按用户配置；不进入 affect journal |
| Affect observation | 脱敏摘要、TTL、证据 digest |
| Self affect snapshot | 有界 head，可由 journal 重放 |
| Relation episode | 结构化意义与未解决线程 |
| Task／effect receipt | 幂等与恢复所需最小字段 |
| 模型／语料／密钥 | 仓库外保存 |

## 9. Gateway 回复与生命体情绪观测流

```mermaid
flowchart LR
    GW["Signed Gateway webhook"] --> IN["Durable inbox"]
    IN --> GRAPH["HostCommand / immutable TaskGraph"]
    GRAPH --> EXEC["Model / Skill / Delegate / Plan"]
    EXEC --> ACCEPT["Verification / Acceptance<br/>durable effect intents"]
    ACCEPT --> SYNC["Chat 同步前缀<br/>user turn → Surface delivery"]
    SYNC --> OUT["Gateway Reply Outbox COMMITTED<br/>INPUT_COMPLETED"]
    ACCEPT --> WAKE["立即唤醒后台恢复"]
    WAKE --> BG["Life → foreground → expression<br/>memory / preference"]
    OUT --> OBS["OBS closed-loop projection"]
    BG --> OBS
    OBS --> MON["Monitor"]
```

`WAKE → BG` 是持久恢复链，不是请求线程里的松散任务。同步的 Expression／Memory／Preference handler 由 Dispatcher 在线程池调用；Life conversation／foreground 虽保留异步 handler 合同，其 SQLite 主体同样显式下沉到 worker thread。这样上一轮后台状态仍按 durable effect 顺序收敛，但不会独占事件循环并阻止下一轮 Gateway admission。

这条链到 `OUTBOX_COMMITTED` 只证明 Host 已经完成可恢复提交。Gateway 实际 dispatch、未知外部副作用对账、平台 ACK 和用户可见性是后续独立阶段，缺少后续回执时不得推断为已送达。

上述短前缀只对编译器可信标记的普通 Chat 生效。用户轮次未提交时不得发送 Surface；后台 effect 仍在同一 SQLite 队列中按原语义顺序完成，进程退出后也由同一恢复 owner 接管。Plan、Skill、Delegate 控制动作和旧账本不会因为存在 Surface delivery 自动获得快速路径。

```text
canonical Life journal
→ digital_life_runtime_snapshot.hard_state.emotion_tag
→ life_body_source.derived.currentEmotion
→ OBS /obs/life-body
→ Monitor 人格上下文
```

旧 `emotion_context` 只用于历史兼容；新运行时不会为了适配 UI 再制造第二份情绪真相。

## 10. 拆分后的对话与具身数据流

```mermaid
flowchart LR
    OBS["用户观察"] --> FUSION["Affect Fusion"]
    FUSION --> AFFECT["Affective Domain Reducer"]
    AFFECT --> PLAN["Affective Turn Plan"]
    PLAN --> STREAM["Native Sentence Stream"]
    PLAN --> DET["Deterministic / Deferred Delivery"]
    STREAM --> ACCEPT["Task Acceptance 或轻量 Life commit"]
    DET --> ACCEPT
    ACCEPT --> CMD["Embodiment Commands"]
    CMD --> EDGE["Edge TTS / Avatar"]
    EDGE --> RECEIPT["Terminal Receipt"]
    RECEIPT --> AFTER["Relationship Aftereffect"]
```

`domain_reducer.py` 仍是 canonical 聚合入口，但 self-affect 与 relationship 的纯 reducer 已分开；`surface_main_chain_adapter.py` 仍是调用 facade，但不再内嵌整套流式生成和交付实现。数据流和 authority 没有因文件移动发生改变。

Surface 的本地门禁只读取本轮冻结计划与已有 facts，不写 self affect，也不新增模型调用。边界、防御、混合情绪抹平和无依据物理共处只会让当前候选进入一次既有语义修复；显式事实已经说明见面或同处时，“当面”类措辞不会被误拦截。最终 runner 使用独立规则读取持久 projection，避免生产门禁自己给自己签发体验通过。

计划层向 Surface 传递的不是固定情绪模板：`HOLD_COMPLEXITY` 必须携带本轮观察到的害怕／不安语义，`GIVE_SPACE` 携带直接减少主动联系的动作，`CONTAINED_JEALOUSY` 携带“感受属于七海、选择权属于用户”的边界。候选层只能润色和筛选，不能把这些动作改写成忙碌反驳、赶走负面情绪或自贬为负担。
