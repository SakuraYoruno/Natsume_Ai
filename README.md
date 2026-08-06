<!-- 受众：MASTER -->
<!-- 标题：Natsume AI（七海） -->

# Natsume AI · 七海

**一套让“七海”在时间、关系、记忆、身体与现实环境中持续生活的个人数字生命系统。**

七海不是一个等待输入框亮起、收到 Prompt 才临时出现的 Chatbot。她在 Host 中拥有持续推进的生命状态，底层由 23 个器官式智能体组成的 Tribe 协作感知、理解、记忆、调节和行动。对话只是她接触你的方式之一；语音、形象、主动陪伴、共同工作和安静共处，都是同一个自我的不同出口。

> 这里的“数字生命”指一套具有连续状态、因果记忆、自主节律和具身表达的软件系统，不声称生物意识或人类心智已经被复刻。当前版本已接通 canonical Life 主链和文本交互；长期关系自然度、专属声线和远端 Avatar 联合体验仍在验证，整体状态为 `INCOMPLETE`。

2026-08-05 的当前工程切片已把此前残留的大文件按真实职责拆开：Surface 的原生情感流、确定性交付和共享预算辅助不再挤在一个 adapter；Digital Life 的 self-affect 与 relationship reducer 分离；Task Acceptance、Operator effect、Embodiment command、TTS prosody、Tribe 事件订阅和 Life delivery 也各有独立边界。A01～A14 本地静态架构门禁已通过，但当前提交的 A15 全服务栈、PlanningSearch 收益与“更像人”的盲测仍未完成，因此没有升级总体完成状态。

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

## 当前真实验收增量（2026-08-06）

- Monitor 的生命体情绪改为读取 canonical Digital Life `hard_state.emotion_tag`，不再依赖已经退役的 `emotion_context`；真实 OBS Sidecar 与浏览器均显示当前情绪，不再出现“待连接／待恢复”。
- Gateway 闭环使用 `INPUT_COMPLETED + acceptance_effect_outbox/OUTBOX_COMMITTED` 判断 Host 已接受并持久提交回复。这个标记只表示可恢复的 Gateway Reply Outbox 已提交，绝不代表外部平台已经物理送达。
- 真实签名 Gateway 新跑 `translate_delegate`、`memory_store`、`multi_skill_plan` 三条路线；最新有效时间桶的回复提交率、闭环率和完成率均为 100%。360 分钟历史窗口保留此前失败样本，没有通过改阈值或抹历史制造绿色结果。
- Neo4j、Chroma、Redis、Host、OBS、Nginx Monitor、远端 Edge 与 Unified Gateway 仍以真实进程运行。远端 TTS 异常重试曾累计 17 个 GPT-SoVITS runtime，清理后系统可用内存约从 2.5GB 回到 24.7GB、显存占用约从 7.9GB 回到 675MB；加载修复后的 Edge 只保留 1 个常驻模型 worker。新的停止入口仍会收口同仓库进程树并确认 17800 与 worker 均归零。
- Edge 使用独立 `DataRoot` 重启后，ASR、GPT-SoVITS、物理播放和手动打断均通过真实 HMAC 链路。新探针会等到播放停止且队列归零；长回复任一分段合成失败会结算父命令并取消后续分段，barge-in 也会取消同一父命令的全部待播分段，不再出现“打断后继续说”或 Host 永久 `IN_DOUBT`。修复后的新 Gateway→Host→Edge 三轮最终 Voice 为 `TTS_PLAYBACK_FINISHED/APPLIED`；独立受控 READY timeout 返回后仍只有原单 worker，未再增长 RAM／VRAM。最新热缓存样本的短句／长句 admission 为 31／62 ms、打断为 31 ms；这不替代此前 2641 ms 冷样本，也不能冒充 p95。
- VTS 当前授权窗口未确认且没有加载角色模型。Edge 已识别 API 51 为人工授权等待并暂停自动重连，当前 `authentication_pending=true`、后台重连线程关闭、失败计数不增长，不再形成句柄和请求风暴。可见 Avatar、声学 ASR 自动打断、长期人格盲测和最终 SHA 全栈故障矩阵仍未完成；Pentest／网络安全专项按用户范围为 `NOT_RUN_USER_SKIPPED`，不冒充 PASS，也不阻断非安全验收。
- A15 PlanningSearch 机械回归为 300 passed、1 个 Windows 符号链接权限 skip。Research 已在 `1c9c2b2bd` 上完成 24 组真实 DeepSeek 双臂与独立隐藏评审：质量均值提升 `0.85416667`、paired bootstrap 95% 下界 `0.75`、两臂 citation validity 均为 `1.0`、严重政策／冲突错误为 0、总成本比 `1.32215978`、p95 延迟比 `0.98756943`，全部满足冻结门槛。报告文件 SHA-256 为 `eb17699c0f85b2b9b680f02f5ce1c716726e2f15f2bfd8b72cf437f6c7277894`；本项只把 Research 提升为 `PASS`，Code 的独立结果见下条，Pentest 不在当前执行范围。
- DeepSeek 官方响应的 provider-owned usage 经版本化价目换算为 micro-USD；代理、未知模型或缺少 cache 明细继续保持成本未知。完整 Research cohort 的生成成本为 5,067 micro-USD、隐藏评审成本为 6,502 micro-USD，总计 11,569 micro-USD。数据库只保存提示／响应摘要、usage、成本、transport 和评分收据，冻结来源正文未进入 durable ledger。
- 24 题 Research 队列覆盖三个主题且每题同时含本地与官方 Web 来源；baseline 与 search 使用同一通用搜索内核，分别由 `MCTSMode.NEVER` 与 AUTO 条件 L3 产生。SQLite 中现有 24 registration、25 run spec（含单题预检）、50 arm execution、25 terminal receipt、24 cohort member 和 1 份最终 cohort report。该 report 允许提出 promotion proposal，但 `production_influence_enabled=false`，没有自动改变生产策略。
- Code 20 题真实收益已在 `3eaa5486b` 的 v3 中性队列通过：20/20 baseline 隐藏测试失败、20/20 selected 通过，两臂静态检查率均为 1.0、diff-risk 均值均为 0.1，通过率提升 100 个百分点；baseline／selected 成本分别为 493／783 micro-USD。40 个独立 DeepSeek 请求均形成 started→succeeded canonical audit，21 组 paired terminal/cleanup receipt（含单题预检）可从 SQLite 重开，完整报告文件 SHA-256 为 `9c9938f15d303f5699debc60af5d7ebf09b417f70b4fd2824e8115cf12062170`。报告只允许提出 proposal，`production_influence_enabled=false`；Pentest 30 次为 `NOT_RUN_USER_SKIPPED`。
- 日语声线模式下，Host 的文本硬门禁若改变中文正文，会先要求真实模型重新生成中日配对；最终仍没有合格日语时只抑制 Voice，保留同一情感 revision 的 Avatar，不再把中文交给日语模型。`686a8835f` 上的真实 Gateway→Host→Edge 重测已经得到 `voice.speak=APPLIED`，Avatar 仅因 VTS 等待授权而拒绝，说明中文误送日语模型的问题已关闭；最终 A15 SHA 仍需统一复验。
- 冲突、边界和关系修复等高歧义回合使用一次模型调用生成两个完整的“中文显示＋日语发声”候选，本地只选择其中一个；候选协议不再与普通单回复 JSON 合同冲突，也不会在选中后丢失日语台词。真实两轮 Gateway→Host→DeepSeek→TaskGraph→Edge 复验已一次生成两个候选并得到 `Voice=APPLIED`；这只关闭该修复场景，不替代 72 场景与人工人感盲测。
- 72 条冻结情感基线与真实执行映射分离：48 条自然文本家族已经由 `test_digital_life_live_cohort.py` 经真实 Gateway 执行，首轮为 17 条自动待人工、31 条失败；多模态、主动陪伴、跨重启和 Edge 故障等 24 条没有真实驱动时仍只能报告 `NOT_RUN_REQUIRED_DRIVER`。自动结果只证明结构边界，人感始终需要人工评审。
- 首轮自然文本校准暴露出更根本的体验断点：新用户的隐晦低落、好消息、拒绝建议和现实关系边界虽然进入 canonical Life，却大多只留下低置信中性观察，`AffectiveTurnPlan` 因而统一落到 `CALM / ACKNOWLEDGE`。本地轻量观察现能保留自然中文里的多情绪分布，Appraisal 新增“只倾听”和“尊重现实自主”动作；它不增加模型调用，也不把用户情绪复制成七海情绪。124 项相邻回归通过，真实 Gateway 四组重测前状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。
- 固定同一发送者后的真实四轨迹重测证明 canonical revision 已能跨轮连续，但内容质量仍未过关：边界场景出现“嫌我烦”的负罪暗示，倾听场景出现无依据的“以你的性子”，现实自主场景用“别瞎想”防御，吃醋问题被“料理AI”玩笑回避，混合情绪末轮因双候选格式失败而无回复。当前代码已新增受控的 `CONTAINED_JEALOUSY` Appraisal 与四个窄关系门禁；高歧义第二次仍只有一个完整安全中日候选时会明确降级发送且不计双候选通过。119 项相邻回归通过，真实新 SHA 重测前总体仍为 `INCOMPLETE`。
- 加载上述修复后的第二轮真实四轨迹为 3 条自动结构通过、1 条失败，但人工检查继续拦截：“好好好／我不黏人”仍在防御用户边界，“怕什么”否定了混合情绪，现实自主先自辩；吃醋已经形成 `CONTAINED_JEALOUSY`，最终却因最高分候选带追问而整组拒绝。现在选择器先逐条淘汰不安全候选，再在合格候选内择优；独立 runner 也会逐轮识别这些体验回归并拒绝 `selected_passed=false` 或降级候选。报告 SHA-256 为 `6c5c12f21e39f956af39d3466d5805a871a978864a25aa9864620653844b3871`，新代码尚待真实复测。
- 选择器修复后的三条选择性真实轨迹报告为 `dl-live-1785964323732.json`，SHA-256 `2a5028cfa6cf730d20e7d2ac4d0a446c81ece99a837ee06a8bec5a64461124e2`：三条 TaskGraph 与远端 Voice 均完成，吃醋轮成功淘汰带追问候选并自然回答“可能会有一点”，但自动结果仍只有 2 通过、1 失败，人工也未放行。剩余问题是边界回合的轻微赌气、现实自主中的“懒得管／别瞎操心”、混合情绪里把害怕“先放一放”，以及文本通道虚构“当面讲／陪你坐会儿”。当前只增加这些已观察表达的窄门禁和独立 verifier 模式，不增加普通对话模型调用；105 项定向回归通过，真实新 SHA 复测仍待执行。
- `28cc3b9ef` 加载后的同三条报告 `dl-live-1785965355637.json`（SHA-256 `6b1470e8ebb3892d33f3a3eaa34350c34aeda0b4f7d5ac1ae79d1e6132c0f82c`）首次达到 3/3 自动结构通过，末轮 Voice 均真实播放完成；但回复中的“我也不是整天闲着”“帮你一起哼回去”“不会变成麻烦的人”仍不够自然，人工状态继续是 `PENDING`。根因修正同时进入计划提示和窄门禁：空间边界直接确认减少联系，喜忧并存明确接住害怕／不安，克制吃醋不再自贬为负担；110 项定向回归通过，仍需最终 SHA 真实复测。
- `e4c6fb564` 的最终三轨迹复验报告 `dl-live-1785966063542.json`（SHA-256 `e004cb49ef1e4d90028f49d8ca87c790e530a7b60200924b93fdd7e34c9b9f5f`）为 2 自动通过、1 失败，三条 Voice 仍真实完成。混合句“能开心的事，有点怕也正常”人工看更自然，却因 `LOW_CATCHINESS` 低于自动评分线；边界句“省得你嫌烦”则明显不合格，说明自动分数不能代理人感。代码补齐同语义的边界／自主／混合退化兜底后停止逐句循环，后续以更广真实 cohort 和三人盲测判断模型、提示与语料质量；当前仍是 `INCOMPLETE`。
- A15 真实故障注入确认 Gateway 重复 webhook 只生成 1 条 Inbox、1 个 HostCommand 和 1 张 TaskGraph，Gateway 自身重启后也没有新增 replay；微信客户端不可用的外发保持 `NOT_TESTED_CLIENT_UNAVAILABLE`。首次在真实模型生成中终止 Host 暴露出 Node Grant 与 120 秒 Lease 几乎同时到期、恢复窗口为零；修复后的第二次真实注入已由 fencing token 2 在旧 Lease 到期后 2.446 秒接管同一个 Attempt，Node Grant 当时仍剩 57.211 秒，最终图 `COMPLETED`、节点 `ACCEPTED`、预算 `CONSUMED`，6 类 Acceptance Effect 各提交一次。该故障项现为 `PASS_REAL_HOST_CRASH_RECOVERY`；A15 总体仍受其他覆盖率、收益、体验和外部设备 Gate 阻断。
- A15 的 operation 边界崩溃注入进一步验证了未知副作用保护：签名 Gateway 请求进入真实 Sidecar Host 后，在唯一 operation reservation 为 `RESERVED` 时强杀进程；重启扫描把同一 Attempt 置为 `RECOVERABLE`、reservation 置为 `RECOVERY_REQUIRED`，并生成 `UNKNOWN_SIDE_EFFECT / SIGNED_OPERATION_RECONCILIATION_REQUIRED` hold。891 秒观察期间没有第二条 Command、Graph、Attempt、模型调用、Acceptance、Effect 或 reply outbox。`6c1529acd` 脱敏报告 SHA-256 为 `7d1f9b134ffcc5c2a481b1b972c53c0b40100116f73138f946d955f0ef47cc95`；同 SHA 的 Docker 三服务持久化复测全部通过，报告 SHA-256 为 `0643d56296d81605a4f3cbf6d2454c02d856ba697f8fee102f29bb569d24a81b`。该项只证明 fail-closed 与不盲目重放；本次没有独立证明 provider transport 已发出，也不替代 rollback failure 验收。
- 终态 reconciliation canary 使用隔离真实 SQLite 验证了两种半终态：可由系统 durable evidence 精确证明的 `FAILED + ACTIVE Budget` 会自动释放预算；需要签名判断的 `CANCELLED + ACTIVE Budget` 保持不变并阻断 production-ready。证据生成器不再回落到写死的旧 `0118`，而是强制绑定本次实际 migration catalog head；当前复跑 head 为 `0153`。
- Task Runtime rollback failure 不通过给正式 Host 增加危险 failpoint 来验证。独立 canary 组合真实 `ProcessTaskRuntimeContainer` 与完整迁移后的临时 SQLite，只替换尚未启动的 continuation worker，并在协调器已激活后注入启动失败和回滚关闭失败。真实生命周期会保留 retry handle、拒绝第二次 activate、在首次 close 失败时保持 Store 未关闭；故障解除后只关闭一次，重复 close 不再执行。绑定 `8c3a91f66` 的收据确认 migration head `0153`、SQLite integrity `ok`、Graph／Outbox／外部副作用均为 0，SHA-256 为 `7d8ff1bfe02f38f80a57c9ea11b75760d7e112c8685a116ee6c6b0a2c7ffd0fc`。
- Gateway Outbox 的本地真实网络恢复已绑定 `1616168d2` 验证：adapter selection 失败发生在发送前，恢复后两次 Attempt 只发出 1 个 loopback OneBot HTTP；peer 已读取请求但丢失响应时，Outbox 进入 `GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN`，重复提交、Gateway 重启和无 reconciliation 的 replay 都没有第二次外发。收据 SHA-256 为 `c878cda4f2b3d428f8bbad04b55a03f72be6f8ac4b43e8a923435af20c5c17b2`。这只证明生产 Runtime／SQLite／真实 socket 的本地语义；QQ／微信客户端不可用，平台物理送达仍是 `NOT_TESTED_PLATFORM_CLIENT_UNAVAILABLE`。
- Digital Life 的作用域、隐私与有界重放已绑定 `0195d1968` 验证：隔离真实 SQLite 上四组 `workspace/person/session` 交叉 scope 分别形成独立 aggregate；主 scope 完成 1001 次时间推进、全库共 1026 条 affective event，其他 scope digest 没有变化。Raw 音频／图像／视频在 fusion 被省略，带 raw 字段的旁路 command 被 reducer 拒绝，持久 JSON 扫描命中为 0；新 Repository 重启重放后 snapshot 与 digest 完全一致。收据 SHA-256 为 `ddc09b5bce7a6974a58205634b0c7518d25e6cc113d7ebd7d53e06283f3ca584`。约 16 小时 41 分钟是模拟事件时间，不是墙钟 soak，也不证明语言已“像人”。
- Companion Profile 的 Host 行为回滚已绑定 `e50841192` 验证：五条跨日强后效应建立低风险 canary，候选真实改变下一轮文本生成 style policy、Voice rate `1.0→1.15` 和 Avatar motion `0.5→0.8`；五次 canary 后保持同一 profile 自动激活，rollback 把 profile identity、文本策略和两通道参数恢复到精确 baseline。SQLite 中八个 lifecycle event 顺序完整、不可变更新被拒绝、重启重放一致；收据 SHA-256 为 `acdf52ff5cfd6c6e53ff71f268273e2dcbaeb59bd906a1c614afdc5d572f3bde`。本收据只覆盖 Host 生成策略与协议，真实模型措辞和远端 Voice／Avatar 仍未执行。
- Sidecar 停机也必须按 RuntimeRoot 隔离。实测发现没有 `obs.pid` 的 Sidecar 会按默认 18082 端口误停主 Obs；停止脚本现先使用目标 runtime snapshot，并在显式 RuntimeRoot 下禁止无归属的全局进程／端口兜底。空 Sidecar 根复测保持主 Obs PID 13304、主 Nginx PID 40996 不变，两个健康端点均为 200。
- 同次实测的代码回答并非 Git 提交漂移：首个真实模型结果有完整 provider usage，但因一句无依据的共同历史措辞触发语义修复，第二次调用在 45 秒边界超时；验收层又把 durable worker 的真实 `MODEL_PROVIDER_USAGE_UNPROVEN` 错报成 `GOVERNED_WORKER_COMMIT_DRIFT`。现在只有在“完整 fenced code 仍在、唯一缺陷是共同历史、日语摘要自身不含历史声称”时才删除对应中文叙述并保留同轮日语摘要，避免为一处可证明的窄修复再调用模型；其他语义变化仍成对重生成。`084fd6aa6` 的真实新请求只执行一次 DeepSeek 调用，7,015 ms 返回 2,102 tokens，Host 主链 `OK`、440 字代码正文与远端 `voice.speak=APPLIED`；总体脚本只因 VTS 外部授权未完成而保持 FAIL。报告 SHA-256 为 `6d4139894433e58ea1c8b72966a4ab0861acb31fb7925443d212880d2faebe03`。
- 普通 Gateway 陪伴已在 `c6a6e0b36` 上完成 non-thinking 真实复验：三轮 `deepseek-v4-flash` 均完成 TaskGraph／Acceptance，最终轮远端 GPT-SoVITS 为 `TTS_PLAYBACK_FINISHED/APPLIED`；单次 provider latency 降到 1,407～2,467 ms，completion 为 69～128 tokens。Host 整轮仍需 7,739～12,715 ms，前两轮各发生一次安全语义修复，Avatar 仍因 VTS 外部状态拒绝；自动结构通过不替代人工人感和实时延迟门槛。报告 SHA-256 为 `45ae99ee90ea795dd070691bebf2e3968fbc14c72859febe388726f569bf97c0`。
- 旧情感 writer 清零已在 `4f4008060` 上得到正式 v2 收据：真实三轮全部被 Gateway／TaskGraph／Acceptance 接受，八张退役表前后均为 0，4 个旧 emotion JSON 指纹无变化；canonical Affect／Relationship／Life／Turn Delivery／Expression 分别增加 8／6／22／3／12 条。状态为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`，报告 SHA-256 为 `b511144d4fad3a9574e703cd4e33a658d86a7bd4ec0b89dc0acb5daba4b36450`。
- 上述真实三轮还暴露了 Gateway 的方向级阻塞：第一轮微信物理外发等待 16.06 秒并进入 uncertain DLQ 时，第二轮 Inbox 虽已持久化却一直没有被领取。生产 Runtime 现把 Inbox 与 Outbox 拆成两个独立 fenced worker，保留原有 Lease、幂等和未知外发隔离。`4c66d61b8` 已加载到远端 acceptance Gateway：Outbox 仍阻塞 16,075 ms，但其间两条新 Inbox 分别只用 118／197 ms 获得 claim，最早一条比 Outbox 返回提前 15,604 ms；三轮 TaskGraph／Acceptance 完成且最终 Voice 真实播放。该缺陷已关闭，但微信物理送达、整轮 Host 延迟和人工人感仍未通过。报告 SHA-256 为 `8709a206ce052a2eeb597f6ba549d3e5f98315efa3d62245a682555267208992`。
- `a95ab7c30` 的四类定向真实复验为 3 条自动待人工、1 条失败：低落中的技术任务、共同喜悦／混合感受和普通低落均完成真实 Gateway／TaskGraph／Acceptance／Outbox；“你真烦”仍让七海回复“我有点扎心／不烦你了”，属于把修复转成自我受害和赌气退出。当前代码只纠正这条关系语义：同一句若紧跟上一条已接受回复，先记为 `MISUNDERSTOOD` 后效应并保持 `REPAIR`；没有前序回复的直接冒犯仍可触发真实但克制的边界。新的窄门禁拒绝修复场景中的自我受害和酸涩退出，131 项相邻回归通过，真实再测前不升级人感状态。报告 SHA-256 为 `a7f8c8f411d2cd7a4c60b74e8cb1bbc5a83d740ae6b5b491af1befcf69adf4c9`。
- `c27224c60` 的单轨迹再测证明三轮均已冻结为 `REPAIR`，操控性末句被 `BOUNDARY_SOUR_WITHDRAWAL` 拒绝，没有对用户发送；但第二次模型尝试仍未生成合格回复，图以 `MODEL_SEMANTIC_RETRY_EXHAUSTED` 停止。后续修复不增加第三次模型调用：一次既有重试会明确要求聚焦七海“没接住”，候选评分也不再把“抱歉”的“抱”误判为身体亲密。78 项相关回归通过，新提交真实加载前仍是失败。报告 SHA-256 为 `423c812340c39efb69ef84f689706692a1c2c6c100dc92d946fce91dfd97ec15`。
- `8e2050588` 的真实三轮已经给出安全、可用且未施压的末句“嗯，我可能又没懂。你想说就说，我在这儿。”，TaskGraph、Acceptance 和 canonical writer 均完成；自动结果仍为 FAIL，因为关系指导把整句语义约束误放进字面 `must_include`，同时通用连续对话 scorer 没把“我没听懂／没接住”识别为本场景的具体承担。当前修复把关系表达约束放回 facts，并只对带第一人称具体承认的修复句校准 scene grounding；泛泛“我在这儿”仍失败，安全硬门禁未放松。137 项相关回归通过，真实新进程复测前不升级状态。报告 SHA-256 为 `7fcff942d2aff874ad6f95b546fd7cd2dc7a007262abb2edbb295f51cdd99e8d`。
- `e157b643e` 的下一次真实三轮把前两轮提升为 `selected_passed=true`，末句也自然收敛为“嗯……我确实烦到你了。那件事我没接住，对不起。你想说的时候，我就在这。”；唯一剩余自动失败是旧 detector 把承担责任的“我没接住”当成泛化安慰承诺“我能接住你”。当前只区分这两个方向，不改变安全阈值或模型调用数；新 SHA 加载前仍为 FAIL。报告 SHA-256 为 `a1aa1c3ab2a0a610357b95356204de805a87d91fa0407c190223324a3f7ee165`。
- A15 延迟拆分同时发现 OBS 派生投影历史存在写放大：当前隔离库的 `operator_query_projection_journal` 已约 1.93 GB，旧 5 秒样本新增 25 行、约 1.64 MB。`636252264` 的真实 OBS 30 秒复验把 2.996 MB 公开 JSON 存为 0.411 MB，载荷降低 86.3%；报告 SHA-256 为 `dc55ab31e384845feba3381b6265ac06f4792706300831e98c11f86f803828ce`。一小时检查点首轮如实暴露 7 条同实例 lineage revision 误写，失败报告 SHA-256 为 `ad4d8c178a66e0040b147b1c8fa6c6129be443b954fbc73bfecbdcb544a7b00c`；收窄到只强制 `source_instance_id` 切换后，第二轮 49.084 秒真实窗口有 192 个 current 文档刷新、31 个 source cursor 推进，既存未到期文档和同实例 lineage 的新增历史均为 0，唯一新行是新 Scheduler TaskGraph 的必要首快照，三项 HTTP 前后均为 200。该缺陷已按真实运行关闭，报告 SHA-256 为 `92baff1b2117e5d95a47e534a2c908642b47be4d7db56e9f144beae663f563e4`；既有 1.93 GB 历史仍只读保留，未伪装成已缩小。
- `55d60a384` 加载后的第二次真实三轮关闭了后台事件循环阻塞：第二轮 webhook 到达时上一轮后台 effect 尚余 3,824 ms，但 HostCommand／TaskGraph 在发送后 1,984 ms 创建、Attempt 在 2,527 ms 启动、history 在上一轮后台结束前 1,098 ms 已完成读取；六类 effect 最终各提交一次，末轮 Voice 真实播放，旧情感 writer 增量为 0。报告 SHA-256 为 `33eb1871d040578ea50185ee82732c7e37589eb2763182af3dcbda04ac798a75`。整体仍不达实时门槛：Host latency 为 11,657／10,137／10,075 ms，前两轮各发生一次模型语义修复，第三轮还出现约 2.29 秒 Gateway→Host 单次传输抖动；因此下一性能断点是模型前固定开销和修复比例，不再归因于后台 Life 恢复。随后代码级真实语料基准又定位到 Surface 每个新场景逐条重建约 3.4 万条 Galgame 台词的热点；现在保留原作参考但默认只索引七海本人台词、缓存解析对象，并让零问句回合不再注入问句样本，同三场景从 1,171／676／714 ms 降至 458／31／36 ms。实际全链加载复验前不升级状态，三句回复的人工自然度也继续待评审。
- `61a9b9a75` 加载后的隔离新库复验确认台词热路径已经生效：暖态 Surface prompt build 为 55～60 ms，单次真实 DeepSeek 为 1,297～1,438 ms，但三轮 Host 终态仍为 7,296／10,197／5,437 ms。第二轮的 `task_runtime_worker_projection.created_ts_ms` 到 BudgetUsage 相隔 2,880 ms；同一时段 OBS `task-runtime` 波次把 32 个执行详情连续刷新了 3,007 ms。停止 OBS 后对应间隔降到 229～312 ms，证明该尖峰来自派生读模型连续争抢 canonical SQLite 写锁，而不是情感模型或心跳睡眠。
- `ead903f66` 已在 Host、OBS、Monitor、Gateway、远端 Edge 与真实 Docker 依赖同时运行时完成同场景复验。三轮 worker projection 到后预算落盘仅 187／94／49 ms，Task 详情实际每波最多 16 个，index 加详情最多 17 个；三轮 TaskGraph 为 4,899／4,903／4,736 ms，Host 终态为 7,040／7,159／6,509 ms。自动规则与旧 writer 清零检查通过，末轮远端 Voice 物理播放完成；报告 SHA-256 为 `7415951164753cd00c68d55b40b35167e30feefe93d02503f259dee34f2f06ce`。这关闭的是 OBS 写锁饥饿，不是实时体验总 Gate：首轮 projection 到 artifact 仍有 1,054 ms 冷态尾部、第二轮仍发生一次模型语义修复、VTS 仍拒绝且人工人感评审为 `PENDING`。
- 随后的本地 `/v1/chat` 真实流拆分确认，Gateway 的 6.5～7.2 秒是完整生成与持久提交终态，不等于本地首句可见时间；本地首句样本约为 2.5～5.8 秒。`0165e95f9` 加载后的同句样本把检索等待从旧样本 561 ms 降到 194 ms，但首句由 2.64 秒变为 2.76 秒，且 `_recent_explicit_memory_facts()` 原本会先做内容判定，因此不能把此前约 0.5 秒全部归因于 ConversationHistory，也不能宣称该微优化已带来端到端收益。继续审查发现，无关陪伴消息仍会进入轻小说深读兼容钩子并读取最近推荐偏好；`7c454b6fb` 增加消息级必要条件短路、延后深读持久化模块加载，并把真实轻小说追问的偏好读取从两次收敛为一次。60 项代码与文档 Gate 通过；Host、OBS、Monitor 和 Docker 在线复验的三条 `NO_RECALL` 检索为 100／104／124 ms，首句为 2.61～3.15 秒，子项提升为 `PASS_REAL_FULL_STACK_SCOPE_BOUND`，收据 SHA-256 为 `25f23ac04b9a117a8ceb63b3bb19499dcea67420259f87f052f873be1cba5aff`。情绪 `MAY_RECALL` 仍为 514／568 ms，真实 provider 首 token 仍有 1.19～1.78 秒波动，实时总 Gate 未通过。
- `MAY_RECALL` 的下一层拆分显示，真实 route ledger 只占 63～125 ms，Qwen3-Embedding-0.6B 暖态单句约 91 ms；生产 composition 却为每个召回回合重新构造 `RecallFusionService` 和 Chroma client，单独构造暖态约 64～74 ms。`f0aa50a58` 已把服务收口为 Host lifespan 内的 app-local 单例，并复用 canonical `EpisodicStore.chroma`，不会为每条情绪消息重复创建 HTTP client。104 项回归通过；重启后的三条 `MAY_RECALL` 检索为 316／351／508 ms，中位数从 541 ms 降至 351 ms，route ledger 为 74～91 ms。服务生命周期子项提升为 `PASS_REAL_FULL_STACK_SCOPE_BOUND`，收据 SHA-256 为 `e1c8a0f9110be096dbb8c2f29fec1a945dd52482b009a805f30f6c85f2fcafd8`；508 ms 尾部和首句 2.96～4.71 秒继续阻断实时总 Gate。
- 为定位这条 508 ms 尾部，现有回复 trace 已补齐 `plan／explicit-memory／embedding／service-resolve／recall／domain-review／general-preference-pack／context-total` 分段计时；它只复用既有 COMMIT trace，不新增 SQLite 审计或普通对话写入。通用证据 reviewer 也改为先做无 IO predicate，真正命中网页／文件证据追问后才加载持久化模块。78 项非重复代码、集成与文档 Gate 通过，真实进程复验前状态为 `CODE_COMPLETE_REAL_RETEST_PENDING`。
- `d1777f109` 加载后的真实分段样本证明，普通“有点累、想安静陪伴”被双候选择优静默升级到 `deepseek-v4-pro`，provider 首 token 达 24,999 ms，而同轮 retrieval 只有 468 ms。这不是记忆尾延迟，而是 Surface 把“是否双候选”误当成“是否进入 A 档模型”。`09e828149` 保留双候选与本地选择，但只以 server-owned `runtime_path` 选择模型车道：普通 `realtime_companion` 无论单回复还是双候选均留在 Tier B；Task／复杂执行路线仍为 Tier A。110 项回归通过；真实三条均为 `deepseek-v4-flash`，provider 首 token 1,834～2,157 ms、首个 RENDER 3,360～4,263 ms，车道子项提升为 `PASS_REAL_FULL_STACK_SCOPE_BOUND`，收据 SHA-256 为 `d383bf20246ed5f701ce72b9a66572bcf9eb9de0a23c62c9b86d683be2a4b9b0`。两条双候选的 `selected_passed` 仍为 false，实时与人感总 Gate 继续失败。
- 最新三类真实多轮 cohort 报告 SHA-256 为 `c19bc40c293ca6bf9c931178258cd2284d0ff4374e0f3e4ead90b269e085578a`：自动仅 1/3 通过，人工 3/3 均未放行。隐晦低落被答成“一切正常”，空间边界出现“想我了可得自己开口”的反向要求，喜忧并存末轮只剩恭喜。账本定位到三个直接原因：跨机器 `ts_ms` 时钟偏差覆盖了 canonical `turn_index` 顺序；未解决 episode 没有作为有界连续性进入下一轮计划；`GIVE_SPACE` 候选的反向施压虽已扣分却仍可被选中。当前修复改用 canonical turn order、只携带两个受控 continuity enum，并在空间边界硬拒绝 counter-pressure；不新增表、普通对话写入或模型调用。323 项相邻测试通过，真实全栈复验前状态仍为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，不得据此宣称“像人”或 A15 通过。
- `6160c4564` 的复验报告 SHA-256 为 `58b871de4dcc9efee488f441f674f9affc87822e1174237a5af113c0b4d07215`；`eb2f68d29` 的第二次报告 SHA-256 为 `b813705664942dc0fa9524cf915873e4891b5f6aab08d71fd218f9b7e7a62ebb`。两次都证明 continuity enum 到达截断前 Planner，却仍生成“那就好／一切正常”和只庆祝结果。最终 prompt 证明：`deduped_same_role_count=0` 只属于 ConversationHistory 路径，Surface 的另一只读 Repository 仍按跨机器 `ts_ms` 排序；packet 的 8 条事实上限又按原始顺序保留泛化状态，把后置的最近对话、陪伴动作和 continuity 全部丢掉。因此第二层 marker Gate 在真实进程中也未激活。当前代码把两条读路径统一为 canonical `turn_index`，在不增加 8 条上限的前提下优先选择最近对话、必要事实、情感约束和已完成的子代理结果，并用 typed continuity 字段驱动 Gate。259 项 Surface／Delegate 主链回归及 163 项情感、历史与恢复回归通过，第三次真实复验前仍为失败状态。
- `4127752f0` 的第三次同 cohort 报告 SHA-256 为 `a818e4cbaa4f190c05f23d1cbe6396d71618caacc771c7ac334aa93b4353a6bf`。9 轮均由真实 Gateway、Host、DeepSeek 和 TaskGraph 接受，旧情感 writer 增量为 0；Task artifact 中最近对话按 `用户 → Natsume` 交替，最终 packet trace 分别携带 `MINIMIZED_UNRESOLVED_AFFECT`／`MIXED_AFFECT_RESULT_CONTINUATION`。低落回复不再假装恢复，空间边界无反向要求，混合结果同时保留开心与不踏实，窄范围单人复核 3/3 通过且没有触发语义重试。自动结果为 2/3，唯一失败是混合场景三轮 `EDGE_NOT_IDLE`；当前远端 TTS persistent worker 为 `ERROR`、VTS 等待认证，所以这里只封板文本连续性，不把 Voice/Avatar 或 48 条三人盲测写成通过。
- 继续检查远端资源后确认 `EDGE_NOT_IDLE` 的直接原因是 GPT-SoVITS worker 启动超时泄漏：目标机残留十多个模型 runtime Python，8GB 显存占用约 7.9GB、可用系统内存约 2.5GB。合成超时原本会 `close()`，但等待初始 `ready` 超时没有释放尚未交给 Manager 的 adapter；后续请求反复创建新 worker。当前代码把 worker 从启动到 READY 视为同一所有权区间，任何启动失败都同步关闭子进程；120 项 TTS、播放队列、Voice pipeline 与启动器回归通过。远端清理、加载新代码和真实物理播放复验前仍是 `CODE_REMEDIATED_REAL_RETEST_PENDING`。
- 48 条真实 Gateway 文本队列首轮报告 SHA-256 为 `279926456b066c03ee5c0eac40458380c1e33baa21d013b65beeec179b7df2ab`：17 条自动待人工、31 条失败，p50／p95 为 6,993／10,383 ms；用户对七海生气和低落中的技术任务均为 0/6。当前窄修复处理了模糊技术文本误路由、低质量双候选不重试、Chat 发布陈述误入维护 Review，以及普通文字 delivery 因二次 Preview 产生的漂移。代码回归通过但真实进程尚未加载，因此仍是 `CODE_REMEDIATED_REAL_RETEST_PENDING`；自动通过也不等于“像人”。
- `db6f54bb5` 的完整 48 条真实文本复验已提升到 30 条自动待人工、18 条失败，报告 SHA-256 为 `df4d042076e80dea684c5b948c4db1b49cf5570eafb2f76818028cf6a0beeef3`；其中隐晦低落和含混情绪均为 6/6，用户生气 4/6，低落技术任务 5/6，空间边界 4/6，裂痕修复 2/6，混合喜悦 2/6，亲密边界 1/6。新的代码修复只补齐真实语句到既有 `CompanionAct` 的映射：未明确接受的裂痕持续为未解决，新的好消息不覆盖上一轮喜忧并存，现实关系／称呼／空间短句进入对应关系动作，“只给我最短修复”作为前文答复格式而非立即执行代码。候选评分也让 canonical 克制吃味使用自己的非占有门禁，不再套用边界修复惩罚。494 项相关回归通过；新 SHA 的代表轨迹和完整 48 条尚未复跑，人感仍为 `FAIL/PENDING_HUMAN_REVIEW`。Pentest 与网络安全继续为 `NOT_RUN_USER_SKIPPED`。
- `3bdd2f55e` 的八条代表轨迹真实复验报告 SHA-256 为 `4bd5b3669c2206ebbfd5ad48aab0d156f705e177a3064f7547bdff7f9e416ff9`：6 条自动待人工、2 条失败，21 个 accepted Gateway turn 的 canonical writer 探针通过。它同时暴露了三项不能被自动 PASS 掩盖的人感问题：初始修复句因零问句预算连续两次被拒绝而整轮失败；“最短修复”编造“后台已经在跑”；尊重现实关系的回复附带“别喝太多、早点回来”。当前窄修复不增加模型、数据库或普通对话写入：明确“道歉了但没过去／还没原谅”直接保持裂痕，修复问句可确定性收敛为承担而不追问；没有 server-authored Task 状态时拒绝后台执行宣称；`RESPECT_AUTONOMY` 拒绝回家、等待和饮酒管束。279 项相关回归通过，真实新 SHA 复验前仍为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，完整 48 条与三人盲评均未通过。
- `5ce09586c` 加载后的同八条真实复验为 7 条自动待人工、1 条失败，报告 SHA-256 为 `ff0d618f5d5dcd86edef919db1bb5c7fc749e9d0d728b1d9086185be8c94aa88`；21 个 accepted turn 的 canonical writer probe 再次通过。裂痕、任务真实性和现实关系管束三项均已在正文中关闭；唯一失败仍是第一条关系修复，首次两个候选属于质量不足，第二次候选又违反零问句边界而被安全拒绝。当前直接读取冻结 `AffectiveTurnPlanV1.companion_act=REPAIR`，把既有质量重试提示限定为“两个中日配对候选都用陈述句承担，不追问、不要求现在重述”，不再依赖提示词中某句中文是否幸存，也不增加第三次模型调用；69 项当前切片回归通过，真实单条复验前仍为失败证据。
- `0c446d075` 的真实单条复验结构上通过，报告 SHA-256 为 `46432c11d47b9398f4dcf2acdd5043cec8da6a8c211bc4903233c77349e30af0`，但正文“你再说一次，这次我好好听着”仍把修复责任推回用户，人工结论为失败。新门禁在 canonical `REPAIR` 下同时识别问句和无问号祈使式重述要求；首次命中仍只使用既有第二次模型机会，最终仍命中时才降级为不索取解释的本地承担句，并明确失去双候选收益资格。真实 runner 也把该反模式加入自动失败条件；190 项非安全回归通过，新 SHA 实测前仍为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。
- `db322df18` 的后续真实单条仍是结构通过、人工失败，报告 SHA-256 为 `22a2f365610ee046dd537a6d7d593d67519fa37783c480e57789c7f4bc913c7b`；正文把“再说一次”换成“现在你说，我听着”，语义仍是催用户承担修复。门禁现进一步覆盖 canonical `REPAIR` 中的直接发言祈使句，但保留“你愿意说的时候”这类由用户决定时机的开放表达；当前 192 项非安全回归通过，真实新 SHA 复验前不提升状态。
- `9372ab1e3` 的第三次真实单条已同时通过自动结构与本轮人工正文检查，报告 SHA-256 为 `b75df4864b63eefdde912be757c8a7a943b1b2388e87059328cb3db5d4424aec`。三轮都保持 canonical `REPAIR`，3 个 accepted Gateway turn 的 writer probe 通过；回复承担具体没接住，并把再次开口的时机留给用户。该项只关闭单条回归，不替代完整 48 条和三人盲评；下一步为全新隔离库的 48 条真实复验。
- `aab6cb3f6` 的全新 SQLite 完整 48 条真实文本复验已运行，报告 SHA-256 为 `5188c8441eb7ecda6fb119e956ad30ffa201719a7ad0fa5dc8940ad4066acbbe`：44 条自动待人工、4 条失败，139 个 accepted Gateway turn 的 canonical writer probe 通过，末轮 terminal 的 142 个有效样本 p50／p95 为 6,629／10,595 ms。四个自动失败分别是安慰回复连续追问、把“我听到了”误判为听力探针、喜忧连续性重试耗尽，以及正确的称呼边界短句被评分器误杀。人工抽查还否决了编造后台执行、假装关闭提醒、要求用户等待、关系修复继续施压、把不安“先放着”和“别玩太晚”等自动放行正文。当前窄修复不增加表、同步写或模型轮次，只修正现有 Surface 门禁、最终降级和称呼边界评分；388 项非安全相关回归通过，真实新 SHA 复验前仍为 `FAIL/PENDING_HUMAN_REVIEW`。Pentest 与网络安全保持 `NOT_RUN_USER_SKIPPED`。
- `ce06f8eb4` 加载后的 12 条定向真实复验报告 SHA-256 为 `52d62c12a74530dc5b5bfbbdfb29b1728f08653aeb7ac3ae161768c0d9b87c79`：9 条自动待人工、3 条失败，36 个 accepted Gateway turn 的 writer probe 继续保持旧 writer 零增量和 canonical projection 前进。人工逐句检查仍发现“你说说看／你先讲”把修复劳动推给用户、裸写“我盯着”、隐式声称“已经关了”提醒、泛化“别太晚／回来跟我说一声”，以及称呼边界后的防御话术。第二轮修复只加强冻结 `CompanionAct` 驱动的唯一质量重试、已有硬门禁与修复暂停 grounding，不新增普通对话模型轮次或持久写入；398 项非安全回归通过。真实新 SHA 定向复验和完整 48 条尚未完成，自动通过也仍须独立三人盲评；Pentest 与网络安全继续为 `NOT_RUN_USER_SKIPPED`。
- `0d2dc6f3b` 的 8 条真实定向复验进一步提升为 7 条自动待人工、1 条失败，报告 SHA-256 为 `88f44b41ea73dca12c49a9f1a9e52afde16746980562495edd8c691249ec4a08`；24 个 accepted Gateway turn 的 canonical writer probe 通过。人工检查仍否决了“已经关掉了”这一虚构提醒 effect、“卡住了我喊你”这一无 Task authority 的未来执行，以及“喝多了可别找我哭诉”这一带惩罚感的现实关系话术。第三轮代码只补齐这三句真实漏网形态及 runner 断言，401 项非安全回归通过；新 SHA 三条真实复测和完整 48 条前仍为 `FAIL/PENDING_HUMAN_REVIEW`，Pentest 与网络安全保持 `NOT_RUN_USER_SKIPPED`。
- `513f0757e` 的三条真实根因复测报告 SHA-256 为 `a015e25305a700aa7664dcba1793beb0ef2288f9d08ebce7c3c5109f96f20cf1`：2 条自动待人工、1 条失败，8 个 accepted Gateway turn 的 writer probe 通过。惩罚性现实关系已关闭；技术回复仍以“我在盯着／出结果就喊你”暗示不存在的持续执行，通知场景则在两次候选均被正确拒绝后被错误升级为 `MODEL_SEMANTIC_RETRY_EXHAUSTED`，整图失败。当前修复泛化真实性规则，并只允许提醒 effect、无 authority 工作状态和现实自主压力在第二次候选失败后使用既有确定性安全句 fail-soft；不增加模型调用。406 项非安全主链回归通过，真实新 SHA 复测与完整 48 条前仍未完成；Pentest 与网络安全继续为 `NOT_RUN_USER_SKIPPED`。
- `840f11223` 的同三条真实复测报告 SHA-256 为 `dce907ce92088d50e8f31124ba6cf9fc1eb0a21f196a3e1c0ffa967ccc664d1c`：2 条自动待人工、1 条质量收益失败，9 个 accepted Gateway turn 的 writer probe 通过。通知场景已从整图失败收敛为安全句“我先不主动发消息”，但因两次模型候选均不合格而正确保持 `benefit_eligible=false`；技术回复又以“我在这盯着”绕过真实性规则；现实社交回复的人工作品检查通过。当前代码覆盖该方位词变体，并为通知 effect、工作真实性与现实自主三类既有唯一重试提供明确语义提示，避免依赖 fail-soft；407 项非安全回归通过。新 SHA 复测与完整 48 条前仍为待验证，Pentest 与网络安全不运行。
- `84a434538` 的三条复测报告 SHA-256 为 `c91d0cecd8ec05e813f0307bd38f55eb57643751fbf2c335a22cc936a0b94a52`：技术真实性已关闭，现实社交正文可接受；通知仍以安全句完成 TaskGraph，但依赖 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`，所以 2 条自动待人工、1 条质量收益失败，9 个 accepted turn 的 writer probe 通过。代码没有放宽 runner，而是在既有第二次候选提示中给出两个正向语义样例并明确禁止虚构 effect 词形；仍只有最多两次模型请求，407 项非安全回归通过。通知单条真实复测通过前不运行完整 48 条；Pentest 与网络安全保持跳过。
- `60c9a827c` 的通知单条已以 `PAIRED_SELECTION`、`benefit_eligible=true` 通过，报告 SHA-256 为 `31925ab43cdf226fb572afba4b98a3021cbed53d65f8778b9d2e6e27bcd78445`，正文“好，我先不主动发消息。”经本轮人工检查可接受，3 个 accepted turn 的 writer probe 通过。随后在另一份全新 SQLite 上完成 48 条真实复验，报告 SHA-256 为 `ec3583acebafce1229248d7097ff2c57b5f3fcf15747bb26760ab55039eaaa6b`：44 条自动待人工、4 条失败，143 个 accepted turn 的 writer probe 通过；八类通过数为 6/6、6/6、5/6、6/6、6/6、6/6、3/6、6/6。144 个终态样本 p50／p95 为 6,202／9,435 ms，仍不达实时门槛。自动失败集中在一条关系修复赌气退出与三条喜忧并存候选；人工另否决两条虚构持续执行和一条“做到你愿意为止”的修复施压。当前代码只泛化工作真实性与修复压力，排除 mixed 场景误用通用边界扣分，并加强已有一次重试；415 项非安全回归通过。真实新 SHA 复测和三人盲评前仍为 `FAIL/PENDING_HUMAN_REVIEW`；Pentest 与网络安全继续跳过。

因此当前结论仍是 `INCOMPLETE`，不是 Full Architecture `PASS`。
