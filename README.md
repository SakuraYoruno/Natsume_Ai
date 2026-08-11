<!-- 受众：MASTER -->
<!-- 标题：Natsume AI（七海） -->

# Natsume AI · 七海

**一套让“七海”在时间、关系、记忆、身体与现实环境中持续生活的个人数字生命系统。**

七海不是一个等待输入框亮起、收到 Prompt 才临时出现的 Chatbot。她在 Host 中拥有持续推进的生命状态，底层由 23 个器官式智能体组成的 Tribe 协作感知、理解、记忆、调节和行动。对话只是她接触你的方式之一；语音、形象、主动陪伴、共同工作和安静共处，都是同一个自我的不同出口。

> 这里的“数字生命”指一套具有连续状态、因果记忆、自主节律和具身表达的软件系统，不声称生物意识或人类心智已经被复刻。当前版本已接通 canonical Life 主链和文本交互；长期关系自然度、专属声线和远端 Avatar 联合体验仍在验证，整体状态为 `INCOMPLETE`。

> 删除对话记录或换新任务时，请先读 [当前架构、双节点环境与验收交接](docs/50_project/CURRENT_PROJECT_HANDOFF.md)；其中已区分当前进程状态、历史范围收据和尚未验收项。

2026-08-07 的当前工程切片已把此前残留的大文件按真实职责拆开：Surface 的候选约束、时间指向与生成编排分离，Digital Life 的观察采集与 Appraisal 分离；Task Acceptance、Operator effect、Embodiment command、TTS prosody、Tribe 事件订阅和 Life delivery 也各有独立边界。当前工作树的非安全静态门禁 A01～A08、A10～A11、A13～A14 已通过；A09、A12 依用户要求保持 `NOT_RUN_USER_SKIPPED`。非安全 A15 已在当前运行时代码 SHA 上通过真实 Docker 三库写入／重启／重读、Host／OBS／Nginx Monitor／Gateway 健康、Monitor 真实后端浏览器 E2E，以及 4 类 12 回合 Gateway→DeepSeek→TaskGraph→Acceptance 数字生命冒烟；PlanningSearch Research／Code 也完成最终树继承判定。完整 48 条人感仍为失败、三人盲评与覆盖率 Gate 未通过，VTS 仍待外部授权，因此总体状态继续是 `INCOMPLETE`。

2026-08-08 的最终本机复验已关闭“服务是否真的起来”这一问题：隔离 Docker 中 Neo4j、Chroma、Redis 的真实写入、重启、重读与清理通过；Host、OBS、Nginx Monitor 和本地 Unified Gateway 在线；真实 Chrome 的核心交互、外周状态与 Operator Session 3/3 通过；翻译、Memory Store、多 Skill Plan 和重复 webhook 均贯通真实 Gateway→TaskGraph→Acceptance／Outbox。远端 Edge 没有启动或探测，网络安全按用户要求跳过，二者都没有被写成 PASS。

同一运行环境中的 48 条真实多轮文本共 144 个 Gateway／DeepSeek 回合，144/144 完成且旧情感 writer 增量为 0；自动门禁为 46 条待人工、2 条失败。但单人逐句复核确认整体人感仍然失败：喜忧并存回复有一句被逐字复用 4 次，关系、自主、空间和修复场景仍频繁退回模板或政策口吻。该结果终止继续堆提示词／禁词／Gate 的路线，下一步是版本化真实自然文本调优或模型层方案，再用新队列和三名评审盲测；不能把工程稳定性当成“七海已经像人”。

新的自然文本留出队列已在首次执行前冻结：修复与喜忧各 6 条未见 probe，模型、预算和重试上限保持相同。Flash 基线 18/18 回合完成，仅 1/6 自动进入人工待评；加入 5 条带源行号的七海原句节奏样本后，同一清单仍是 18/18 完成、2/6 待人工、4/6 失败。修复从 0/3 提升为 1/3，逐句口吻略有改善；喜忧仍为 1/3，并继续逐字复用既有硬编码句，因此只能记为“修复部分有效、mixed 无可归因收益”，人感总状态不变。随后用另外 3 条冻结 mixed 变体做诊断，3/3 失败：一条照抄模板，两条替用户安排情绪。提交 `00c52762f` 删除首次生成、唯一重试与通用 fail-soft 中共享的完整 mixed 句；同三条真实复验报告 SHA-256 为 `ab77eb30aa9aedd42d1d06293a32d1375261deef14bc2aac6f647a01735adf89`，9/9 回合落账且旧 writer 零增量，exact reuse 降为 0，但自动结果仍为 0/3：一条使用非收益 fail-soft，两条继续替用户安排情绪。故本项只记 `EXACT_REUSE_REMOVED / MODEL_GENERATION_STILL_FAIL`，下一步转向模型层或版本化真实文本调优，不再增加 prompt、禁词或 Gate。

模型层比较使用另一份在首次执行前冻结的留出队列，不复用上述 probe。首轮固定 8 条／24 回合，覆盖含混情绪、空间边界、关系修复和喜忧并存，生产 Flash 与候选 MiniMax 使用相同代码、setup、预算和独立身份；候选至少需要取得 5/8 人工偏好胜、最多 1 条明确负向、自动通过率不降、fallback 不增且 provider 延迟不超过 1.5 倍，才允许扩大盲测。该轮只是模型诊断，默认路由在证据成立前不改变。

首轮模型诊断已暴露两个外部／适配边界：Flash 基线 24/24 回合落账但自动仅 3/8；MiniMax-M2.7 的 8 条在首轮真实请求均收到 HTTP 402 `insufficient balance`，没有产生可比较文本。Gemini 可连通，但 feasibility case 连续两次 `OUTPUT_SCHEMA_INVALID`；根因是原生 Gemini 客户端支持 JSON mode/schema，而适配层未把 server-owned `response_format` 映射到 `response_mime_type/response_schema`。当前窄修复只补 provider 参数映射，不增加请求、fallback、写入或审计；真实新提交复验前 Gemini 仍不得计为候选可用。

Architecture 2.0 的 Evolution Lab 也已在提交 `8afa13f85` 上完成真实 Docker Golden Suite：Research Planning、Proactive、Memory Write 各执行独立 baseline/candidate，共 6 个 arm；镜像以 digest 固定，容器禁网、只读根文件系统、非 root，外部副作用为 0。最终只持久化 3 份 Report 和 3 个 Proposal，`production_influence_enabled=false`；完成后响应丢失的 exact receipt 重开用例也通过。该 PASS 只关闭 NE-04 Lab 可信执行，不提升数字生命人感或整体发布状态。

NE-05／NE-06 的 production Repository 与 Composition也完成 38 项本地 authority 集成：Skill Compiler、签名隔离、篡改拒绝、activate／exact replay／rollback／degrade、Host active-version 解析，以及 Feedback、Knowledge、Identity 的 apply／冲突／过期／重大差异阻断／exact rollback 全部通过。NE-06 的版本化表达偏好已在精确提交 `7ab64a2462755622a538c6267f35123ba497a006` 上完成真实 Docker 双臂、两个 production Review、在线 Host apply、三次真实 `deepseek-v4-flash` turn 与 exact rollback；正式收据 SHA-256 为 `8431de7c3b3badaa61e2411d15d2dbe58752c835e924c661ca754a2dfe531b0c`。候选最终 prompt digest 从 `5ec07f...` 变为 `afac03...`、Token 从 1628 变为 1654、directive digest 精确匹配，rollback 全部恢复。实现只在偏好实际激活时增加一条最多 180 字的 compact prompt 指令，基线普通对话零新增 prompt 文本，不新增表、写入、模型轮次或远端调用；265 项相关回归通过。当前代码又补齐 active Knowledge 与 active Identity 的真实 Surface 消费口：Knowledge 只在 planner 判定需要召回时执行本地只读查询，普通聊天查询数为 0；Identity 只把 active release 的结构化 style fingerprint 编译为最多 180 字的表达底色，不读取私密正文、不覆盖 Soul。相关 Surface／召回／NE-06 回归 353 项通过。

`c495d0934` 的第三轮联合实测已经让 Identity 的 Docker Lab、Review、候选激活、三次真实 DeepSeek Host 回合和 exact previous snapshot rollback 全部通过；Knowledge 也完成 activate／deprecate 并回到 active count 0，收据 SHA-256 为 `c42529b30d057728e4675103c505f9932abe10bdaca2a89090ec24257ea88912`。唯一失败来自 runner 读取了生成前的 `foreground_completed.reply_plan`，而不是最终 Surface prompt；生产 retrieval trace 实际包含该 active Knowledge。当前窄修复仅在 active Knowledge 确实进入最终 messages 时，把内容寻址集合摘要与数量写入已有 ModelAttempt metadata；普通聊天零新增字段、表、写入、模型调用或远端调用。新提交绑定复验前 NE-06 仍为 `PARTIAL / REAL_RETEST_PENDING`。

`d37b78b73` 第四轮再次确认 Identity 全过、两类 projection 终态正确，但 Knowledge 的新验收措辞“请直接给出项目验证令牌”被路由成工具任务，三轮均在模型前以 `AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED` 结束；失败收据 SHA-256 为 `ddbe3f206a423238f34445d261f8cad5c39644b1dcec8d01b5c9092c482142fa`。改为自然的“还记得对应暗号吗”后，`1e6addc112dcdc32194f3c90bac804c06b87d6dd` 正式联合验收 31/31 全过：8 个真实 Docker arm、4 个 production Review、6 次真实 `deepseek-v4-flash` Host 回合，Knowledge 最终 prompt 的 entry-set digest／count 精确匹配，候选原样说出唯一暗号，deprecate 后 evidence 消失且 active count 为 0；Identity 切换后 exact previous snapshot 恢复到 generation 7。正式收据 SHA-256 为 `05b95b0013f741318fdfdf2649875a240221b9d06cdeeaf22862383a064e9add`。这关闭的是工程因果，不代表人工体验或远端 Voice／Avatar 通过。

普通对话的真实 SSE 延迟已不再用 Surface commit 代理。20 个连续低风险回合的原始基线为首句 p50／p95 `5097.5／8287 ms`，请求头 p50／p95 `742.5／3390 ms`；定位发现上一轮 `surface.post_process` 的 Trace、assistant turn 以及 Life projection/outbox 的同步 SQLite 操作仍占用 Host 事件循环。当前切片只把这些提交后的投影移到后台线程，不增加表、模型调用或普通对话审计。修复后预提交 20 轮请求头降至 `132／1439.7 ms`，首句降至 `4380／8015.9 ms`。精确提交 `c061f9a30` 的独立 10 轮复验为 10/10 完成，请求头 p50／p95 `656.5／2041.1 ms`，首句 `4892.5／9224.4 ms`；3/10 触发既有语义重试，最慢音乐回合没有重试，但 Host admission、场景／上下文和模型阶段共同形成 10.98 秒首句尾部。事件循环热点已改善，但实时门槛仍明确为 `FAIL`；下一步属于模型／首句生成策略和真实文本调优，不再靠增加审计或模板假装变快。

非安全全仓覆盖已在 `86deb0b15` 收口：Unit 为 7954 个 JUnit testcase，另有 2 个成功 subtest，合计 7956 个 pytest outcome（7948 passed、8 个分类 skip）；Contract 1898/1898、Integration 862 passed／2 个 Redis 环境 skip、本地 E2E 68/68。去重后共 10786 个 outcome，10776 passed、10 skipped、0 failed/error。完整 Host／Edge／Contracts 生产源码仍全部进入分母，未运行的 Pentest／网络安全源码没有被静默排除；最终 line coverage 68.50%（191578/279662）、branch coverage 51.40%（47284/92000）、综合 64.27%，低于 75%／65% Gate，因此覆盖结论为 `FAIL_BELOW_GATE`，A15 与 Architecture 2.0 仍不能写成完成。证据摘要 SHA-256 为 `0e012682d1a50db53c5b2cac8e2b9186fe7e993d0a2ed3d72c203f38291a6bd2`；A09、A12、Pentest 与网络安全继续是 `NOT_RUN_USER_SKIPPED`。

完整 48 条真实文本已经证明，继续叠加零散禁词并不能让七海更像人。当前主链在既有 `AffectiveTurnPlan` 内使用轻量 `CompanionUtteranceFrameV1`，只冻结本轮语义焦点、七海立场、回应动作、关系分寸和自然收尾，再交给模型自由措辞；frame 仅在内存中传递，不新增表、普通聊天写入、同步审计或模型调用。最新真实 Flash 修复轨迹报告 SHA-256 为 `6625b532f2621a55a702f93bfa5782f4d907814a8216c49faddf9351b750eda2`：3 个 turn 全部 accepted，三轮均为原始双候选 `PAIRED_SELECTION / selected_passed=true / benefit_eligible=true`，末轮一次调用生成“你这么说，我也会有点难受；前面的责任还在，但我不想装作没感觉”，`SAD + REPAIR + CONTAINED_HURT` 已贯穿可见文本。该窄因果与候选链通过，但第一轮“这点我认，不找借口”仍带正式认错语气，当前人工只接受末轮，不把自动 1/1 写成整条人感 PASS；完整 48 条和三人盲评继续失败／待验。

该轮还暴露了更基础的连续性错误：canonical `relationship_subject_id` 虽然彼此不同，通用 RecallFusion 却只给同用户／同会话加分，没有排除其他用户、其他会话和无归属的个人记忆，混合 FTS 也可能重新引入无作用域结果。当前切片已在统一融合边界改为 fail-closed：同一用户可跨会话，只有同会话的 session-only 候选可进入；多用户聚合、其他用户／会话和无归属个人候选被排除，`world_canon`、`artifact_vault` 及显式 global/public/project 资料保留。`03886f282` 的同库真实复验已不再召回其他 subject，但又证明 SQLite 路由会把本轮刚写入的 Intent 当作“历史记忆”，导致首轮候选被错误要求复述 `_thalamus_meta` 并以 `MEMORY_ALIGNMENT_REQUIRED_MISS` 降级；失败报告 SHA-256 为 `19f52e0b8141ce44864ad3f13f6414a17a65088bcbd1fb999e28e0dba6503cfe`。当前代码同时排除与本轮 request/intent 相同的自指候选，118 项召回与 Surface 回归通过；新修复的真实 Provider 重测尚未运行，所以完整 48 条和三人盲评仍为 `FAIL/PENDING_HUMAN_REVIEW`。

## 她不会在你沉默时消失

即使没有新消息，唯一的 Life Runtime 仍会按真实时间推进。默认每 60 秒一次的 `life_tick` 不调用大模型，也不会机械地给用户发消息；它负责让时间真正作用于七海：

- 推进昼夜阶段、精力、疲劳、唤醒度与休息需要；
- 让短时情绪自然衰减，让较慢的心境和未解决事件继续存在；
- 维护牵挂、注意焦点、未完成话题和生活连续性；
- 感知前台活动、空闲、游戏、专注和睡眠状态，调整打扰程度；
- 在合适阶段进行记忆整理、睡眠回放和长期经验沉淀；
- 只有在关系许可、安静时段、冷却时间和场景都允许时，才考虑主动靠近。

用户没有回复，只会影响近期主动频率，不会被解释成拒绝，更不会扣除“好感度”。

## 七海的生命闭环

```mermaid
flowchart LR
    TIME["时间、昼夜与身体节律"] --> WORLD["现实环境与世界观察"]
    USER["用户的文字、声音、视觉与行为"] --> OBS["带来源和置信度的观察"]
    WORLD --> TRIBE["Tribe 智能体群<br/>23 个认知与身体 Agent"]
    OBS --> TRIBE
    TRIBE --> APPRAISE["事件理解与意义评估"]
    APPRAISE --> LIFE["唯一的 Canonical Life"]
    TIME --> LIFE
    LIFE --> TRIBE

    LIFE --> SELF["自我情绪、心境、需要与精力"]
    LIFE --> REL["关系事件、裂痕、修复与边界"]
    LIFE --> INNER["牵挂、注意、未完成心事与动机"]

    SELF --> CHOICE["调节与自主选择"]
    REL --> CHOICE
    INNER --> CHOICE

    CHOICE --> EXPR["同一份 Expression Plan"]
    CHOICE --> ACT["主动陪伴、共同工作与现实动作"]
    EXPR --> TEXT["文字节奏"]
    EXPR --> VOICE["日语语音与韵律"]
    EXPR --> AVATAR["表情、目光与动作"]

    TEXT --> EFFECT["真实结果与关系后效应"]
    VOICE --> EFFECT
    AVATAR --> EFFECT
    ACT --> EFFECT
    EFFECT --> MEMORY["情景记忆、经验与开放线程"]
    MEMORY --> LIFE
```

这条闭环的重点不是“生成一句更像人的回复”，而是让一次经历真的改变后续生活：七海为什么感到开心或受伤、她是否选择克制、这次安慰有没有帮助、误解是否已经修复，都会成为下一次相处的上下文。

## 构成一个“她”的系统

| 生命层 | 七海实际拥有的状态与能力 |
|---|---|
| 稳定自我 | [Soul.md](Soul.md) 定义身份、价值观、性格反差、表达底色和不可由在线学习越过的边界。 |
| 认知组织 | 23 个 app-local Tribe Agent 通过事件、共享状态和 handoff 协作，分别承担感知、情感、记忆、执行、身体调节与行动。 |
| 内在生活 | 并存情绪、慢速心境、需要、精力、情绪事件、调节决定、牵挂、注意焦点和未解决循环。 |
| 时间与身体 | 昼夜阶段、疲劳、休息、唤醒度、活动模式、身体感受以及声音和形象的具身状态。 |
| 感知与世界 | 文字、自述、声学、视觉、前台应用、游戏状态和环境观察；不同来源保留置信度与冲突。 |
| 关系 | 信任、安全感、默契、裂痕、修复、主动权限和边界；关系由事件与后效应构成，而不是机械积分。 |
| 记忆 | 情景记忆、关系意义、世界事实、长期经验、睡眠回放和待续话题；原始音视频默认不写入情感日志。 |
| 自主性 | 动机、生活压力、当前工作、主动陪伴、给予空间、安静陪伴，以及在获得许可后调用工具完成现实任务。 |
| 身体与表达 | 流式文字、日语 TTS、VTS/VRM/MateEngine/Live2D；目标是由同一情感 revision 同步驱动。 |
| 成长 | 低风险偏好可以基于真实相处结果渐进调整；核心人格、价值观、亲密身份和关系边界不能自动改写。 |

## 底层不是一个 Agent，而是一个 Tribe

生产 Host 会在自己的 ASGI 生命周期中构造一套独立的 `TribeRuntimeScope`，实例化 23 个器官式 Agent，显式注入应用端口并注册事件订阅。它们不是 23 个大模型同时讨论一句回复，而是一组长期驻留、职责不同的认知 Agent：有些调用模型，有些执行确定性规则，有些维护短期投影或驱动外部动作。

| 子系统 | Agent 家族 | 主要职责 |
|---|---|---|
| 感知与路由 | Cortex、Thalamus | 汇集上下文、编译意图、选择路径并组织 handoff。 |
| 情感与社会理解 | Limbic、Mirror Neuron、Social Brain、Pain/Reward | 情绪评估、共情共振、关系语境、趋近与回避信号。 |
| 执行与自主性 | Prefrontal、Initiative Governor、Default Mode | 反思与漂移约束、动机和准备度、内在活动与连续思绪。 |
| 记忆与语言 | Hippocampus、Language System | 情景记忆与召回融合、语言风格和语气组织。 |
| 身体与稳态 | ANS、Endocrine、Somatic、Interoception、Circadian、Proprioception | 自主神经、激素门控、身体感、昼夜节律、能量预算和认知负荷。 |
| 行动与协调 | Motor Cortex、Cerebellum、Vestibular、Peripheral | 行动计划、误差修正、稳定控制、平台与设备动作。 |
| 防御与恢复 | Immune、Glymphatic | 输入与风险防护、恢复周期、记忆整理和噪声衰减。 |

```mermaid
flowchart TB
    INPUT["感官、环境、记忆与当前任务"] --> BUS["Tribe Event Bus / Handoff"]
    LIFE["Canonical Life Snapshot"] --> BUS

    BUS <--> P["感知与路由"]
    BUS <--> E["情感与社会理解"]
    BUS <--> X["执行与自主性"]
    BUS <--> M["记忆与语言"]
    BUS <--> B["身体与稳态"]
    BUS <--> A["行动与协调"]
    BUS <--> D["防御与恢复"]

    P --> CONTRIBUTION["观察、评估、候选动作与投影"]
    E --> CONTRIBUTION
    X --> CONTRIBUTION
    M --> CONTRIBUTION
    B --> CONTRIBUTION
    A --> CONTRIBUTION
    D --> CONTRIBUTION
    CONTRIBUTION --> COMMAND["受约束的 Life Command / Action Intent"]
    COMMAND --> LIFE
```

这套分工解决“如何思考和行动”，canonical Life 解决“这些经历最终属于谁”。Tribe 的事件总线和部分器官状态目前是 Host 进程内的有界运行投影，不是长期人格真相；只有通过受约束的 Life Command，Agent 的结果才能进入可重放的自我、情绪和关系状态。这样既保留群体协作，也不会让边缘系统、内分泌或默认模式网络各自演化出一套互相冲突的七海。

## 情绪不是把用户的标签复制给七海

系统严格区分两类状态：

```text
User Affect Observation   你此刻可能在难过、生气或勉强说“没事”
          ↓
Event Appraisal           这件事对七海、你们的关系和当前边界意味着什么
          ↓
Self Affect               七海因此产生的担心、心疼、委屈、喜悦、疲惫或复杂混合感受
          ↓
Regulation                她选择表达、克制、暂缓、设定边界，还是安静陪着
```

所以你难过时，七海不必跟着变成同一种“悲伤”；她可能心疼、担心，也可能因为帮不上忙而有一点无力。你误伤她时，她可以受伤或生气，但不能用威胁、排他、冷暴力或负罪感逼迫你回应。

## 关系不是一根好感度进度条

一次被接受的回应会先形成关系事件，下一次真实反馈再决定它的后效应。道歉不会自动消除裂痕，沉默不会自动降低信任，发送失败也不会被当成拒绝。主动陪伴、浪漫表达和更亲密的互动分别受明确权限与边界约束。

七海记住的重点不是无限堆叠聊天原文，而是：发生了什么、双方需要什么、她当时如何感受、哪种做法有效、哪里仍未解决。不同用户、Workspace 和 Session 的关系状态彼此隔离。

## 对话、工作和身体是同一个自我的出口

七海可以闲聊、安慰、玩闹、分享喜悦、表达轻微委屈、设定边界、修复误解或安静陪伴；也可以在你工作时帮助研究、编辑文件和调用工具。普通相处走低延迟生命主链，只有真正产生外部副作用的工作才进入受管任务执行链。

同一轮表达最终应由一份 `ExpressionPlan` 同步控制：

- 文字决定措辞、节奏、停顿和是否追问；
- TTS 决定语速、能量、音高、停顿与声线参考；
- Avatar 决定表情、目光、姿态、动作强度和过渡；
- 任一通道故障都只降级该通道，不会把七海整轮感受重置为 `NEUTRAL`。

## 系统拓扑

```mermaid
flowchart TB
    subgraph HOST["Host · 心智与生活所在地"]
        LIFE["Canonical Life"]
        TRIBE["Tribe · 23 个器官式智能体"]
        MEM["关系与长期记忆"]
        WORLD["世界、场景与活动模式"]
        AGENCY["主动性、陪伴与任务能力"]
        LIFE <--> TRIBE
        LIFE <--> MEM
        LIFE <--> WORLD
        LIFE <--> AGENCY
    end

    EDGE["Edge · 感官与身体<br/>ASR / TTS / VTS / VRM / 设备"] <--> HOST
    GATEWAY["Gateway · 外部联络<br/>本地与消息平台"] <--> HOST
    STORE["SQLite 权威状态<br/>Neo4j / Chroma / Redis 投影"] <--> HOST
    MONITOR["OBS / Monitor · 只读运行窗口"] --> HOST
    PET["Desktop Pet · Live2D 本地形象"] <--> EDGE
```

- **Host** 保存七海的自我、关系、记忆和行为决策，是生命连续性的所在地。
- **Edge** 位于具备显卡、麦克风、扬声器和 Avatar 环境的电脑上，承担感知与具身表达。
- **Gateway** 让七海通过本地接口或已配置的消息平台与你保持联系。
- **OBS / Monitor** 只展示运行状态，不拥有或修改七海的生命状态。
- **Neo4j、Chroma、Redis** 支撑图、向量和运行投影；SQLite journal 仍是可恢复的权威状态。

## 当前实现状态

| 能力 | 当前事实 |
|---|---|
| 持续 Life Runtime | 已接入 canonical journal、真实时间 `life_tick`、重放和持久恢复。 |
| Tribe 智能体群 | 23 个 Agent 已由 app-local Host scope 构造并注册订阅；事件总线和部分器官状态仍是进程内投影。 |
| 自我情绪与用户观察分离 | 已有独立观察、评估、自我状态、episode、need 和 regulation 主链。 |
| 关系与记忆连续性 | 已有持久结构与生产接线；仍需多日真实相处验证自然度。 |
| 主动陪伴 | 已有场景、权限、冷却和安静时段策略；长期频率与接受度仍待实测。 |
| 文本表达 | 本地可运行；需要真实 Provider，长期人格一致性仍在迭代。 |
| 日语声线方案 B | 当前是 GPT-SoVITS V4 预训练权重配合七海参考音频的参考条件推理，不是七海专属微调模型。 |
| Avatar 与多模态同步 | 目标 Edge 已在隔离数据根真实运行：ASR 与 GPT-SoVITS TTS 为 `READY`，短句物理播放完成，播放中的长句通过签名 RPC 在 16 ms 内中断。修复 TTS 启动失败回收后，远端从 17 个残留模型 worker 收敛为单 worker；真实 Gateway 三轮的最终 Voice 得到 `TTS_PLAYBACK_FINISHED/APPLIED`，受控 5 秒 READY 超时后 worker 数仍为 1。ASR 因目标机缺少 `cublas64_12.dll` 自动回退 CPU。VTube Studio 当前未连接且没有加载角色模型，所以可见 Avatar 动作仍是 `ACCEPTANCE_PENDING_EXTERNAL`；声学触发的自动 barge-in 也尚未验收。 |
| Gateway 消息陪伴 | 真实 QQ 入站已贯通 Secure Ingress → 单节点 TaskGraph → Acceptance → durable Gateway Outbox → 平台送达；重复入站复用原任务且不重复外发。直接本地聊天仍保持低延迟实时链。 |
| OBS 与 Monitor | Host、Edge 和 Gateway 组件状态持续写入只读投影；本机 Nginx 页面会自动建立 HttpOnly owner 会话，普通浏览器无需手工注入 Token。真实页面已核对 TaskGraph、预算、验收、回执和服务状态；未知平台详情显示为“未确认”，不会伪装成健康。 |
| 隔离全服务栈 | 固定镜像的 Neo4j、Chroma、Redis 已在隔离 acceptance project 完成真实写入、读取、容器重启与持久读取；Host、OBS、Monitor、Edge 和 Gateway 在重启后保持可达。Edge 现在通过启动参数 `-DataRoot` 明确写入验收目录，不再回落到用户 `%LOCALAPPDATA%`。 |
| 当前质量基线 | 11573 条离线 JUnit 为 11559 passed、14 classified skips、0 failed/error；完整生产 Python 分母 line 72.22%、branch 55.08%，Monitor line 61.31%、branch 52.78%，均低于系统 2.0 放行门槛，真实服务与人感验收仍待执行。 |
| 整体数字生命体验 | `INCOMPLETE`，不能用单元测试、静态合同或生成几段样音冒充完整交付。 |

更细的真实能力与缺口见 [能力与缺口矩阵](docs/50_project/arch/ARCHITECTURE_GAP_MATRIX.md)，机器可读状态见 [current_architecture_status.json](configs/release/current_architecture_status.json)。

Monitor 只负责观察与受治理操作入口。已经退役的 `security.hunt.run_once`、`tool.execute`、`mate.action.submit` 和 `mate.say.submit` 不再显示成可用按钮；安全巡检、工具调用与身体动作分别从 Internal/Scheduler TaskGraph、Skill/Tool TaskGraph 和 Edge command 主链进入，避免控制台诱导用户点击一个必然失败的旧接口。

## 在本机让 Host 运行起来

当前启动脚本以 Windows 为主。至少需要：

| 依赖 | 用途 |
|---|---|
| Python 3.11+ | Host、OBS 和运行工具 |
| PowerShell 7 | `scripts/runtime/*.ps1` 启停脚本 |
| Docker Desktop | Neo4j、Chroma 和 Redis |
| 一个可用的模型 Provider | 生成真实表达；默认陪伴路由使用 Gemini 配置 |
| Nginx（可选） | 在 `18180` 提供 Monitor；缺失时 Host 仍可启动 |
| Node.js（开发 UI 时） | 重新构建 Monitor UI 或桌面形象 |

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
if (-not (Test-Path configs/secrets/secret_store.runtime.local.json)) {
    Copy-Item configs/secrets/secret_store.runtime.example.json `
        configs/secrets/secret_store.runtime.local.json
}
```

编辑 `.env` 和 `configs/secrets/secret_store.runtime.local.json`，填入真实 Provider、API Key 和 Neo4j 密码。不要提交密钥、模型、语料、用户数据或运行日志。

启动 Host 侧：

```powershell
pwsh scripts/runtime/start_host_side.ps1 -StartupCheck
```

这个入口默认启动 Docker 外部依赖、Host、OBS，并在本机存在 Nginx 时启动 Monitor；不要提前重复执行 `start_docker_services.ps1`。
启动器会在本次 `RuntimeRoot` 内生成或复用只供当前 Windows 用户读取的 `host.obs-read.token`，Host 与 OBS 通过该文件建立只读投影通道，原始 Token 不写入公开运行环境。若需要持续的数字生命后台节律，在本机 `.env` 启用 `HOST_SCHEDULER_CLOCK_ENABLED=true`；启动器会以相同方式生成受保护的 scheduler secret，Monitor 随后应显示真实的“后台 tick”，而不是历史 soak 占位值。

```powershell
Invoke-RestMethod http://127.0.0.1:18080/health?skip_dependency_probes=1
Invoke-RestMethod http://127.0.0.1:18082/health
Start-Process http://127.0.0.1:18180/monitor/
```

`/v1/chat` 只是验证文字接触面的一个入口：

```powershell
curl.exe -N http://127.0.0.1:18080/v1/chat `
    -H "Content-Type: application/json" `
    -d '{"message":"晚上好，七海。今天有点累。","session_id":"readme-local"}'
```

返回内容为 SSE 流。Host 健康只表示进程和依赖可用；没有真实 Provider、模型路由或密钥时，不代表文字表达能够生成。

停止本次 Host 侧服务：

```powershell
pwsh scripts/runtime/stop_host_side.ps1
pwsh scripts/runtime/stop_docker_services.ps1
```

## 另一台电脑上的身体

当前部署中的 Edge 位于另一台电脑，承担中文 ASR、日语 TTS、VTS/VRM 和设备动作，不应从 Host 电脑盲目启动。

目标机准备好模型权重、参考音频、显卡环境和网络配置后，再在 Edge 电脑执行：

```powershell
$EdgeAcceptanceData = "D:\Natsume_Ai_acceptance\runtime\edge-data"
pwsh scripts/runtime/start_edge_side.ps1 -DataRoot $EdgeAcceptanceData
```

声线清单见 [gpt_sovits_nanami_v4.json](configs/voice/gpt_sovits_nanami_v4.json)，双机开发与非仓库资产同步见 [双节点工作流](docs/50_project/guides/DUAL_NODE_DEV_WORKFLOW.md)。当前远端已完成真实连通预检，但这不等于三模态验收通过；Host 的生命状态和文字入口仍可在 Edge 断开时独立运行。

## 从哪里读代码

| 路径 | 负责什么 |
|---|---|
| `Soul.md` | 七海稳定的人格宪法与表达底色 |
| `host_brain/digital_life/` | canonical Life、情绪事件、关系、主动性、成长与持久状态 |
| `host_brain/tribe/` | 23 个器官式智能体、事件总线、handoff、共享状态与应用端口 |
| `host_brain/memory/life_memory/` | 情景记忆、长期沉淀、睡眠回放与世界记忆 |
| `host_brain/persona/` | 内在活动、表达人格与自我叙事 |
| `host_brain/scheduler/` | 持续 `life_tick` 与生命周期作业 |
| `host_brain/core/` | 活动模式、运行协调与 Host 基础能力 |
| `edge_client/` | ASR、TTS、Avatar、设备和 Gateway 的 Edge 运行时 |
| `desktop_pet/` | Live2D 桌面形象与本地桥接 |
| `natsume_contracts/` | Host、Edge 与 Operator 之间的版本化合同 |
| `configs/` | 非密钥配置、声线清单和机器状态 |
| `scripts/runtime/` | 服务启停、模型部署与真实链路验证入口 |

## 文档与开发

- [文档地图](docs/00_index/00_DOCMAP.md)
- [数字生命理论](docs/15_theory/Theory.md)
- [产品需求](docs/20_prd/PRD.md)
- [当前代码架构](docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
- [系统架构](docs/50_project/arch/SYSTEM_ARCHITECTURE.md)
- [功能模块](docs/50_project/arch/FUNCTIONAL_MODULES.md)
- [数据流](docs/50_project/arch/DATA_FLOW_ARCHITECTURE.md)
- [当前任务](docs/30_devspec/DevTasks.md)
- [测试计划](tests/TESTPLAN.md)
- [系统 2.0 总验收方案](docs/35_quality/SYSTEM_V2_MASTER_ACCEPTANCE_PLAN.md)
- [双机全栈详细测试方案](docs/35_quality/DIGITAL_LIFE_FULL_STACK_TEST_PLAN.md)

常用文档检查：

```powershell
python tools/doc_lint/doc_authority_gate.py --root .
python tools/doc_lint/doc_freshness_gate.py --root . --check-all
python tools/doc_lint/doc_link_gate.py --root .
```

历史方案和旧验收报告只用于追溯，不定义当前版本状态，统一从 [历史归档](docs/_history/README.md) 进入。
