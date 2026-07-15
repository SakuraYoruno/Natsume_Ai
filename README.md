受众：PROJECT
标题：Natsume AI 工程总览与运行指南

# Natsume AI

Natsume AI 是一个本地优先、用户主权优先的个人数字生命系统。它把对话、人格表达、长期记忆、关系状态、主动行为、工具执行、设备能力和多平台消息接入放在同一套可审计运行时中，并要求所有真实动作都能回答四个问题：谁批准、执行了什么、依据什么验收、产生了什么副作用。

本仓库不是已经完成的“全自动进化 AI”。它已经具备较完整的 Host、Scheduler、Internal Agent、持久化、授权、任务、模型、Gateway、Edge 和观测基础设施，但可生产的 Durable DAG 并发/恢复、通用智能规划、受治理人格演化以及完整长期演化闭环仍在建设中。当前完成度必须以代码审计和机器状态为准，不能从历史 `PASS/DONE` 或目录数量推导。

## 当前状态

| 范围 | 当前结论 | 说明 |
|---|---|---|
| Host `chat/skill/delegate/plan` | `WIRED` | 四类 canonical route 已进入 Governed Runtime |
| Durable Task Runtime | `PARTIAL_B5_TERMINAL_SUCCESS_SHADOW` | `PRODUCTION_OFF`。B5A 增加显式 `result_node_id` 与持久可选 `completion_policy=ATOMIC_GRAPH_SUCCESS_V1`；B5B 用独立 `atomic_graph_success_enabled`、acceptance effects、projection reader 与完整 grant 门禁封口全节点成功，`bounded_fanout_enabled` 另控 terminal-leaf 实际并行。三处生产 composition 均未启用；B5C 等仍未完成 |
| Scheduler | `WIRED` | 真实 occurrence 已贯通 V3、Budget、child grant、Review、Verification、Acceptance 与 Receipt |
| Internal Agent | `WIRED` | 外部 v2 仅作边界输入，进入 runtime 前编译为 V3；Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay 已贯通 |
| Workspace 隔离 | `PARTIAL_D0_PATH_FENCE` | scoped I/O 与 Verification 已统一拒绝路径逃逸；durable owner、隔离 worktree 和 OS/tenant 边界未完成 |
| 显式 Composition | `INCOMPLETE` | 两批已删除 6 个零消费者全局槽位、4 个死 accessor，并收掉第二批 7 个宽泛重导出；排除 6 个 lock 与 `application_ports` 端口束后仍有 53 个可变服务引用，尚未形成 Host/Obs/Worker 显式 scopes |
| Planning Search | `KERNEL_AVAILABLE / BENEFIT_UNPROVEN` | 公共搜索内核存在；Host 尚不生成竞争方案，跨领域真实收益未证明 |
| Persona 演化 | `CENTRAL_FENCE_WIRED` | legacy 直写已中央拒绝；proposal、Review、版本激活和 rollback 替代链尚未实现 |
| 长期演化 | `INCOMPLETE` | World/Scene/Identity 等有局部资产，Evolution Lab、Skill Compiler、Knowledge OS 等未形成完整生产闭环 |

权威入口：

- [当前代码事实](docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
- [系统架构总览](docs/50_project/arch/SYSTEM_ARCHITECTURE.md)
- [功能模块地图](docs/50_project/arch/FUNCTIONAL_MODULES.md)
- [关键数据流](docs/50_project/arch/DATA_FLOW_ARCHITECTURE.md)
- [目标架构](docs/50_project/arch/TARGET_ARCHITECTURE.md)
- [架构差距矩阵](docs/50_project/arch/ARCHITECTURE_GAP_MATRIX.md)
- [下一代正式执行合同](docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)
- [机器可读状态](configs/release/current_architecture_status.json)

## 系统全景

```mermaid
flowchart LR
    U["用户 / Operator"]
    P["外部消息平台"]
    UI["Monitor UI / Desktop Pet"]

    subgraph EdgeSide["接入与外显面"]
        GW["Unified Gateway"]
        EDGE["Edge RPC\nASR / TTS / Avatar / Device"]
    end

    subgraph Host["Host"]
        IN["Secure Ingress / Input Runtime"]
        GOV["Governor / Scope / Grant"]
        CMD["HostCommand + WorkOrderV3"]
        GRAPH["Immutable TaskGraph"]
        TASK["Durable Task Runtime"]
        WORKER["Worker / Capability Broker / Model Runtime"]
        ACCEPT["Verification / Acceptance"]
        EFFECT["Durable Effect Intents"]
        LIFE["Life / Persona / Memory / Bond / Canon"]
        QUERY["Read-only Projections"]
    end

    U --> IN
    P --> GW --> IN
    IN --> GOV --> CMD --> GRAPH --> TASK --> WORKER --> ACCEPT --> EFFECT
    EFFECT --> LIFE
    EFFECT --> GW
    EFFECT --> EDGE
    LIFE --> QUERY
    TASK --> QUERY
    QUERY --> UI
    EDGE --> UI
```

图中是生产主干的方向，不表示每条入口都已完全接线。Scheduler 与 Internal Agent 已接通；DAG 生产并发/恢复、全局 composition 和长期演化 pipeline 仍是明确缺口，详见差距矩阵。

## 运行面

| 运行面 | 启动入口 | 核心职责 | 主要边界 |
|---|---|---|---|
| Host Main | `python -m host_brain` | 对话入口、Governor、Task/Agent Runtime、Life、Memory、业务 API | 真实命令与长期状态的主要协调面 |
| Observability | `python -m host_brain.obs_main` | 只读运行投影、Trace、健康状态、Operator BFF | 不应成为业务事实 writer |
| Monitor UI | `host_brain/app/monitor_ui/` | Task、Review、健康、数字生命和运维视图 | 通过 Query/Operator API 读取和发命令 |
| Edge RPC | `python -m edge_client` | ASR、TTS、Avatar、桌面与设备 actuator | 物理副作用必须回传 receipt |
| Unified Gateway | Edge lifecycle 中的 `edge_client/gateway/bootstrap.py` | 平台 webhook、消息归一化、Host 回流与出站投递 | 平台适配器不拥有 Host 业务状态 |
| Desktop Pet | `desktop_pet/main.cjs` | Electron + Live2D 本地表现 | 只消费状态和动作，不裁决事实 |
| External Services | `scripts/runtime/docker-compose.external.yml` | Neo4j、Chroma、Redis | 可选依赖；不可用时按配置降级 |

Host 与 Edge 之间的共享数据结构位于 `natsume_contracts/`。该目录是跨进程版本化合同，不是重复实现或可删除的临时目录。

## 核心运行链

### Governed 请求

Host canonical 请求的目标 lineage 是：

```text
request
  → Secure Ingress
  → Governor decision + grant
  → HostCommand + WorkOrderV3 + BudgetPolicy
  → immutable TaskGraph revision
  → node Attempt + Lease + node-bound grant
  → worker / model / capability execution
  → Verification
  → Acceptance
  → durable effect intents
  → effect receipts and read-only projections
```

当前 `chat`、`skill`、`delegate`、`plan`、Scheduler 和 Internal Agent 进入这条主链。`task_progress`、`recent_turn` 只读取 durable projection；Executive 的生产入口、SkillDag 执行和内存任务 registry 已删除。Internal 的既有 Agent executor 只在 child grant 验证后接收由 V3 投影的兼容对象，不再拥有 admission authority。

Durable 启动门禁当前按 `migration → task_store.recover_startup → 构造 publisher → 发布 recovery outbox → 稍后激活 runtime` 执行；恢复步骤抛错会在路由开放前 fail closed。ASGI 会把配置中的逻辑 owner 派生为每次 `ProcessTaskRuntimeContainer` 构造唯一的 execution owner，避免重启后的旧 owner 身份复用。

`schedule_retry` 提交后若仅丢失响应，同一确定性调用会严格核对 Attempt、Node、Lease、Graph、Budget、BudgetUsage 及关联 Outbox 后返回原决定，不重复扣减 retry budget。普通或重试耗尽的 pre-reservation resolver failure 通过 `fail_running_attempt_bundle` 在一个事务内落 `Attempt FAILED`、`Node FAILED`、`Graph FAILED/BLOCKED`、`Lease RELEASED` 和四条 attempt/evidence-bound Outbox；一旦已有 operation reservation，该边界拒绝接管。Budget finalize 仍在该事务之外。

reservation 建立后，Coordinator 启动 `DurableExecutionHeartbeat`，每轮先续 Lease、再续 Operation Reservation，并在流事件及最终结果前执行 checkpoint；任一心跳失败只返回 `RECOVERY_REQUIRED`，绝不 COMMIT。两类续租支持同参响应丢失后的精确重放。

`TaskGraph.completion_policy` 是持久可选 v1 扩展：缺省保持 `LEGACY_NODE_TERMINAL_V1` 行为；`ATOMIC_GRAPH_SUCCESS_V1` 必须显式冻结，并同时指定 sink `result_node_id`。legacy 多 sink 图不得声明单一 result，不能靠最后完成或最后声明的节点隐式选结果。`PlanningGraphCompiler` 默认保持 legacy；只有调用方显式请求，或失败重规划继承前一 revision，才生成 atomic policy。

Coordinator 的 `atomic_graph_success_enabled` 与 `bounded_fanout_enabled` 是两个独立且默认 `false` 的进程能力开关，Host、Scheduler、Internal 三处生产 composition 均未开启。Application 在 `repository.admit` 前确认 atomic capability、node-grant service 与 parent grant；不具备能力的进程对 atomic 图 fail closed，不先写 admission。Coordinator 只接受覆盖 admitted Graph 全部节点的 exact grant map。atomic policy 即使顺序执行也由 outer 统一成功封口；terminal-leaf 实际有界并行还必须启用 bounded fan-out。child 只能提交 Attempt、Node、Lease、Reservation 以及 Budget usage/`EXCEEDED`，不能终结共享 Graph/Budget。

每次 governed 执行会在建立/消费 Budget、解析 worker 之前验证 `0090` parallel failure/review decision schema；启用 B5 terminal success 时还会验证 `0091` success decision schema，缺表或缺列直接 fail-fast。`ACTIVE` 多节点重入若仍发现 live Node、active/latest Attempt 或 `ACTIVE` Lease，会返回 `GOVERNED_EXECUTION_IN_PROGRESS`，不解析新 worker、不重复执行，也不抢占现有 owner authority。结算窗口若出现 ownership fence，或 CAS 后重读发现新的 active Node/Attempt/Lease，也映射为 `IN_PROGRESS`；没有任何 active ownership 事实的纯 CAS 漂移仍是 durable state conflict，不能被软化成“正在执行”。

正常的 node-local 非接受不再触发 fail-fast cancel。outer 会等待所有已启动 sibling 到达 durable node-local terminal。live 与 crash/re-entry 都从 durable Node、Attempt、Reservation 和 BudgetUsage 重建同一 canonical v2 payload/ref；outcomes 覆盖整个 admitted Graph 中所有具备 latest stable Attempt 的 durable terminal Node，不只当前 claimable batch，当前 batch 只决定裁决优先级与 selected node。`RETRY_PENDING` 可作为非 selected durable outcome，并可在重入时重建。Graph `FAILED/BLOCKED`、可选 Budget `RELEASED`、对应 Outbox 和 `0090` decision row 同事务提交，response-loss 只允许 exact replay。BUDGET selected `BUDGET_BLOCKED` 绑定 rejected usage；共享超额后的被动 blocked sibling 不要求本地 usage。decision row 每次读取都校验自身 digest、Graph/Budget Outbox 与 Attempt lineage，BUDGET 另校验 usage；terminal replay 还校验 Graph target/version，并要求 selected outcome 的 Attempt 仍是该节点 current latest。测试覆盖跨批次 canonical digest 稳定、`RETRY_PENDING` 非 selected outcome、live/re-entry identity、response-loss 与 newer-Attempt replay rejection。

全节点 `ACCEPTED` 后，B5 outer 从每个节点已冻结的 durable worker projection 重算 canonical digest，并精确校验 command、request、graph/revision、node/attempt、route、target、receipt 与 node grant，只允许显式 `result_node_id` 对应投影生成整图 effect intents 和公开结果。`complete_parallel_graph_success()` 在一个 Store UoW 中原子提交 `Graph COMPLETED + Budget CONSUMED + Graph/Budget 两条 Outbox + 全图 durable effect intents + 0091 canonical success decision`。已完成图的 replay 只读并复核 0091，不重新编译 effects；连续两次 Store 响应丢失时重读 terminal authority。Skill 同样使用冻结 canonical worker projection，不相信瞬时 executor 返回值。

这仍是 `PARTIAL_B5_TERMINAL_SUCCESS_SHADOW / PRODUCTION_OFF`，不是 Lane B 或完整架构完成。0090/B4 的 failure、Budget、review 与 crash-re-entry 语义原样保留；旧 TaskGraph Outbox 仍缺 `graph_revision`，所以多 revision 的 0090 完整 marker 扫描未解决。历史 `BLOCKED + failure Node` 或 `FAILED + ACTIVE Budget` 半提交状态仍不会自动修复，`WAITING_APPROVAL` 仍是独立专线。B5C durable cancel、跨进程 Outbox claim/CAS、Budget 高竞争、自动 reconciliation/takeover/continuation 与生产 canary 均未完成，因此生产 fan-out 必须保持关闭。

### Planning Search

`host_brain/planning_search/` 是领域无关、无真实副作用端口的搜索内核，提供候选扩展、排序、虚拟状态转换、reward 和 Conditional MCTS。它只能选择规划，不能直接执行工具。

当前 Host Plan 只把 Governor 已批准的步骤合法化并编译为图，并不会从目标生成多套竞争方案。Pentest 已接公共搜索 adapter，但 L3 仍处于未校准 shadow；Research/Code 只有模拟证据。因此不能把“存在 MCTS 类”写成“系统已经具备通用自主规划能力”。

### 状态与副作用

Task Acceptance 会原子登记 effect intent，随后由 durable dispatcher 分别应用对话、Memory、Life、Gateway、Edge 等副作用。正确语义是“验收后可恢复的最终一致”，不是“所有外部系统处在同一个数据库事务”。

详细流程和图见 [关键数据流](docs/50_project/arch/DATA_FLOW_ARCHITECTURE.md) 与 [架构图册](docs/50_project/arch/diagrams/00_INDEX.md)。

## 功能能力

| 能力域 | 对用户提供什么 | 主要实现 |
|---|---|---|
| 对话与最终表达 | 流式对话、人格化表达、媒体/语音响应 | `host_brain/agent_runtime/`、`host_brain/persona/`、`host_brain/surface_runtime/` |
| 治理与安全入口 | 身份、范围、规则裁决、授权 token、拒绝与审计 | `host_brain/secure_ingress/`、`host_brain/governor/` |
| 任务执行 | Durable DAG、Attempt、Lease、Budget、Review、验收与恢复 | `host_brain/orchestration/`、`host_brain/task_runtime/` |
| 模型运行 | Provider 路由、模型尝试、重试、用量与失败证据 | `host_brain/model_runtime/`、`host_brain/integrations/` |
| Capability / Skill | 内置能力目录、授权执行、签名接收与隔离 | `host_brain/capability_plane/`、`host_brain/skills/`、`host_brain/airlock/` |
| 记忆与知识素材 | 对话经历、语义/向量召回、Canon、来源证据 | `host_brain/memory/`、`host_brain/retrieval/`、`host_brain/canon/` |
| 数字生命 | Life 状态、情绪、关系、人格表达、主动性 | `host_brain/digital_life/`、`host_brain/persona/`、`host_brain/bond/`、`host_brain/tribe/` |
| 多平台与设备 | 平台消息、ASR/TTS、Avatar、桌面和设备动作 | `edge_client/gateway/`、`edge_client/actuators/`、`desktop_pet/` |
| 观测与运维 | 健康、Trace、任务投影、Review 定位、Operator 命令 | `host_brain/observability/`、`host_brain/app/obs_*`、Monitor UI |
| 安全实验室 | 有授权、受隔离、可审计的 Pentest 工作流 | `host_brain/pentest/`、`host_brain/security_immune/` |

模块职责、依赖方向、入口和已知限制见 [功能模块地图](docs/50_project/arch/FUNCTIONAL_MODULES.md)。

## 仓库结构

```text
Natsume_Ai/
├─ host_brain/                 Host、领域能力与后端运行时
│  ├─ app/                     ASGI、HTTP adapters、composition wiring、Monitor UI
│  ├─ orchestration/           Command、Plan 编译、Graph revision、node grant
│  ├─ task_runtime/            Durable 执行状态机
│  ├─ planning_search/         无副作用规划搜索内核
│  ├─ model_runtime/           模型路由与 ModelAttempt
│  ├─ digital_life/ persona/   Life 状态与最终表达
│  ├─ memory/ retrieval/       记忆写入、召回与上下文材料
│  ├─ capability_plane/ skills/ 能力治理与内置 Skill
│  └─ persistence/ audit/      Repository、SQLite、Migration、Journal
├─ edge_client/                Edge RPC、Gateway、平台与设备 adapters
├─ natsume_contracts/          Host/Edge 共享的运行时中立合同
├─ desktop_pet/                Electron + Live2D 桌面表现
├─ configs/                    可提交策略、发布与机器状态配置
├─ scripts/runtime/            本地运行、外部服务与验收脚本
├─ tests/                      unit / contract / integration / e2e / security
├─ tools/                      静态扫描、文档门禁和发布工具
├─ docs/                       产品、理论、架构、工程合同与历史证据
├─ runtime/                    本地可变数据，Git 忽略
└─ artifacts/                  临时验收产物，Git 忽略
```

顶层不承载通用 `data/`、`flows/`、`skills/`、`integrations/` 杂物目录。模块资产必须归属于明确 bounded context；可变数据进入 `runtime/`。

## 本地启动

### 1. 环境要求

- Python 3.11 或更高版本。
- Node.js `^20.19.0` 或 `>=22.12.0` 与 npm：Monitor UI 的 Vite 8 构建要求；Desktop Pet 和其他前端工具也使用该运行时。
- Docker：仅 Neo4j、Chroma、Redis 等外部服务需要。
- 真实模型、平台和设备能力需要各自密钥或本机依赖；缺失时应显式降级，不能伪装成功。

### 2. 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .\natsume_contracts -e .\edge_client -e ".\host_brain[dev]"
python -m pip install "pre-commit>=4,<5"
```

复制配置模板并只在本地 `.env` 中填写真实密钥：

```powershell
Copy-Item .env.example .env
```

建议将可变数据显式放入 `runtime/`：

```dotenv
NATSUME_DATA_DIR=runtime/data
NATSUME_AUDIT_DB=runtime/host/audit.sqlite3
```

`.env.core.example`、`.env.optional.example` 和 `.env.experimental.example` 分别解释核心、可选和实验配置；实际字段的最终解释以 `host_brain/app/settings/` 与 `edge_client/config.py` 为准。

### 3. 可选外部服务

```powershell
docker compose -f scripts/runtime/docker-compose.external.yml up -d
docker compose -f scripts/runtime/docker-compose.external.yml ps
```

该 Compose 只启动外部依赖，不会代替 Host、Obs、Edge 或前端进程。

### 4. 启动后端

```powershell
# Host，本文示例显式固定为 18080
python -m host_brain --host 127.0.0.1 --port 18080 --startup-check

# 独立 Observability，本文示例显式固定为 18082
python -m host_brain.obs_main --host 127.0.0.1 --port 18082

# Edge RPC，本文示例显式固定为 17800
python -m edge_client --host 127.0.0.1 --port 17800
```

Gateway 通常由 Edge lifecycle 按 `EDGE_GATEWAY_ENABLED` 与 `EDGE_GATEWAY_PLATFORMS` 装配，不应再单独复制一套业务状态。
省略这些 CLI 参数时，实际绑定地址取决于类型化设置、`.env` 和运行时环境文件；启动后应以进程日志和健康探测为准。

### 5. 启动前端

```powershell
# Monitor UI 开发服务
Set-Location host_brain/app/monitor_ui
npm ci
npm run dev
```

```powershell
# Desktop Pet
Set-Location desktop_pet
npm ci
npm start
```

### 6. 健康与接口

服务启动后，使用运行时 OpenAPI 和健康接口核对真实路由；不要依赖文档中的固定路由数量。常用入口：

- Host：`http://127.0.0.1:18080/docs`、`/health`、`/ready`。
- Observability：`http://127.0.0.1:18082/docs`、`/obs/*`。
- Edge：地址和端口由 `EDGE_RPC_HOST/EDGE_RPC_PORT` 决定。

## 开发与验证

按改动风险选择最小充分验证：

```powershell
# 单元或定向测试
python -m pytest -q tests/unit/<test_file>.py

# 稳定合同与集成测试
python -m pytest -q tests/contract tests/integration

# 全量 Python 测试
python -m pytest -q

# 架构与仓库静态门禁
python tools/static_scan/run_all.py --root .

# 文档权威、受众和新鲜度门禁
python tools/doc_lint/doc_audience_lint.py --root .
python tools/doc_lint/doc_authority_gate.py --root .
python tools/doc_lint/doc_freshness_gate.py --root . --check-all

# 当前暂存改动的提交前检查
python -m pre_commit run
```

PR/推送的 freshness workflow 会把 Git 变更集传入门禁：只要生产代码或工程工具发生变化，同一变更集内就必须包含 README、权威文档或机器架构状态之一；仅依赖旧文档的时间戳不能通过。架构语义变化仍应按 policy 的 source→doc 映射同步对应事实、数据流、图和追溯文档。

前端验证命令由各自 `package.json` 定义。Monitor UI 至少应运行 `npm run build`；涉及 Operator client、Task Runtime 页面或交互时再运行对应 lint、typecheck 和测试。

## 架构约束

1. 一个长期状态只能有一个权威 writer；Projection、Cache、UI 和模型输出不能反向成为事实。
2. 搜索、模拟、benchmark 和 counterfactual 路径不得持有真实工具或外部副作用端口。
3. worker 只执行冻结的 TaskGraph revision；失败重规划必须生成新 revision。
4. 所有模型与工具调用都必须关联 request、graph/node、attempt、grant 和 receipt。
5. Acceptance 之前不得提交不可逆副作用；Acceptance 之后通过 durable intent/outbox 投递。
6. 人格、策略、知识和 capability maturity 的自动变化只能生成 proposal，不能直接修改生产真相。
7. Host、Obs、Gateway、Edge 和 UI 通过版本化合同协作，不共享隐式全局状态。
8. 新功能必须进入现有 bounded context；禁止以顶层杂物目录或第二套 registry 绕过架构。

## 文档体系

| 想了解什么 | 阅读入口 |
|---|---|
| 产品为何存在、用户主权是什么 | [白皮书与宪法](docs/10_whitepaper/index.md) |
| 核心理论和概念模型 | [理论体系](docs/15_theory/index.md) |
| 系统现在如何组织 | [系统架构总览](docs/50_project/arch/SYSTEM_ARCHITECTURE.md) |
| 每个代码域负责什么 | [功能模块地图](docs/50_project/arch/FUNCTIONAL_MODULES.md) |
| 请求、状态和副作用如何流动 | [关键数据流](docs/50_project/arch/DATA_FLOW_ARCHITECTURE.md) |
| 当前哪些能力真的接通 | [当前代码事实](docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md) |
| 最终应演进到哪里 | [目标架构](docs/50_project/arch/TARGET_ARCHITECTURE.md) |
| 目标和代码还差什么 | [架构差距矩阵](docs/50_project/arch/ARCHITECTURE_GAP_MATRIX.md) |
| 接下来按什么顺序实施 | [正式执行合同](docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) |
| 需求如何映射到代码和测试 | [需求追溯矩阵](docs/60_trace/requirements_trace.md) |
| 历史计划为何不再是 authority | [历史程序目录](docs/_history/programs/2026-07/README.md) |

文档权威顺序是：生产代码与 machine status → Current → Target → Gap → Active Execution → PRD/DevSpec/Trace → 历史证据。历史 `DONE/PASS` 只对其固定提交、环境和声明范围成立。

## 提交约定

- 提交信息、PR 标题和 PR 正文统一使用中文。
- 不提交 `.env`、密钥、`runtime/`、`artifacts/`、模型文件或本地服务数据。
- 修改生产架构时同步更新 Current、Gap、机器状态、执行合同和相应数据流图。
- 删除或移动文档时同步更新目录索引与文档门禁策略。
