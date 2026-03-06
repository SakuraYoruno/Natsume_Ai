受众：PROJECT
标题：Natsume Ai 项目说明

<div align="center">

# 🌸 Natsume Ai

**意识主权 AI 伴侣系统 — Host-Edge 分体架构**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-00ADD8?logo=chainlink&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-6.x-4581C3?logo=neo4j&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-1.4+-FF6F00?logo=google-chrome&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18+-61DAFB?logo=react&logoColor=white)
![License](https://img.shields.io/badge/License-Pending-lightgrey)

</div>

---

## 📖 项目简介

**Natsume Ai** 是一个具备 **意识主权**（Consciousness Sovereignty）的 AI 伴侣系统。不同于传统的对话 AI，本项目以"法律在先、人格可控、执行可审计"为核心原则，构建了一套 **Host-Edge 分体架构**：

- **Host Brain（主脑）**：运行在服务端，负责终审裁决、状态治理、审计链、检索增强（RAG）、技能编排与关系图谱等核心能力
- **Edge Client（边缘端）**：运行在本地设备，负责语音链路（ASR/TTS）、VTube Studio 驱动、兜底小模型等感知与执行能力

> 🎯 核心理念：**语言模型只负责解释与表达**，最终权力与事实判据必须落在 Host 的代码与审计链上。任何影响现实的动作必须走**单向链**与**票据化执行**。

---

## 🏗️ 系统架构

```
┌──────────────────────────────────────────────────────┐
│                   Host Brain (Python)                │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Governor  │  │  Audit   │  │   LangGraph 编排   │  │
│  │(规则引擎) │  │ (审计链) │  │  (Agent 编排器)    │  │
│  └────┬─────┘  └────┬─────┘  └─────────┬─────────┘  │
│       │              │                  │            │
│  ┌────┴─────┐  ┌────┴──────┐  ┌────────┴────────┐   │
│  │  Bond    │  │  Memory   │  │   Retrieval     │   │
│  │(关系图谱)│  │ (记忆系统) │  │  (检索/重排)     │   │
│  │  Neo4j   │  │ ChromaDB  │  │  Embedding/RAG  │   │
│  └──────────┘  └───────────┘  └─────────────────┘   │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Airlock  │  │  Skills  │  │    Vision         │  │
│  │(气闸隔离)│  │ (技能系统)│  │  (视觉/OCR)      │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Evidence │  │ Emotion  │  │   Observability   │  │
│  │  Plane   │  │  System  │  │  (可观测性/指标)   │  │
│  │(写入治理)│  │ (情感系统)│  │                   │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
└──────────────────────┬───────────────────────────────┘
                       │ RPC
┌──────────────────────┴───────────────────────────────┐
│                  Edge Client (本地)                    │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │   ASR    │  │   TTS    │  │   VTube Studio    │  │
│  │(语音识别)│  │(语音合成)│  │   (Live2D 驱动)   │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
│  ┌──────────────────┐  ┌─────────────────────────┐   │
│  │ Fallback LLM     │  │   Resource Gate         │   │
│  │ (3B 兜底小模型)   │  │   (资源门控)            │   │
│  └──────────────────┘  └─────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

---

## 📁 项目结构

```
Natsume_Ai/
├── host_brain/              # 🧠 Host 主脑（核心服务）
│   ├── app/                 #    应用入口（FastAPI + 监控面板 API + 配置 + 健康检查）
│   │   ├── monitor_ui/      #    监控面板前端源码（React + Vite SPA）
│   │   └── static/monitor/  #    监控面板构建产物（FastAPI 静态托管）
│   ├── airlock/             #    气闸隔离层（脱敏/隔离 + subprocess/Docker 双后端沙箱）
│   ├── audit/               #    审计链（SQLite Ledger WAL + 日志迁移 + 保留策略）
│   ├── bond/                #    关系图谱（Neo4j 客户端 + Bond 动力学 + 迁移）
│   ├── canon/               #    正典系统（Style Canon / World Canon，治理 API 事务化）
│   ├── channels/            #    渠道适配（VTS DEGRADED 降级 / 微信 I/O）
│   ├── core/                #    核心模块（配置快照、上下文包、错误码、ID、QA、Key Profile）
│   ├── governor/            #    治理引擎（决策、规则、Schema、Token、Rate Limit 持久化）
│   ├── integrations/        #    集成层（LangGraph 图编排四层架构 / LLM 网关 / Evidence Plane）
│   ├── lsp/                 #    密钥与策略（Vault / Policies）
│   ├── memory/              #    记忆系统（Chroma 向量存储 / 情景记忆 / Canon 同步 / cursor-based 分页）
│   ├── observability/       #    可观测性（跨端时间线聚合 / 时钟漂移检测）
│   ├── release/             #    发布清单管理（model_plane digest 门禁）
│   ├── retrieval/           #    检索链（Embedding / 召回融合 / 重排 / Evidence Pack）
│   ├── scheduler/           #    调度器（CRON 任务 SQLite 持久化 / 重启自动恢复）
│   ├── skills/              #    技能系统（加载器 / 注册表 / ClawdBot UI 实现）
│   ├── tools/               #    工具系统（Broker / Catalog / MCP 适配 / Selector）
│   └── vision/              #    视觉链（多检测器注册表 / 摄像头事件 / 屏幕 OCR）
│
├── edge_client/             # 🎙️ Edge 边缘客户端
│   ├── actuators/           #    执行器（TTS 语音合成 + VTS WebSocket 驱动 + 自动重连）
│   ├── senses/              #    感知层（ASR 语音识别 + 设备自动恢复 + VAD）
│   ├── rpc/                 #    RPC 通信（健康检查 / 语音 / 兜底 LLM / 资源门控 / 事件上报）
│   ├── proofs/              #    资源证明与门控
│   ├── fallback_model.py    #    兜底小模型门控（FallbackModelGate，draft_only 标记）
│   └── resource_gate.py     #    资源闸门（优先级抢占 + ResourcePreemptionEvent 审计）
│
├── configs/                 # ⚙️ 配置中心
│   ├── deploy/              #    部署配置（节点清单）
│   ├── model_plane/         #    模型平面（节点注册 / 模型档案）
│   ├── models/              #    模型路由配置（Embedding / Recall / Reranker）
│   ├── runtime/             #    运行时环境配置
│   └── secrets/             #    密钥配置模板
│
├── prompts/                 # 💬 提示词资产
│   ├── CODEX_PROMPT.md      #    实现层入口提示词
│   ├── PROJECT_PROMPT.md    #    文档代理入口提示词
│   └── llm_contract/        #    LLM 输出契约 / 拒绝模板
│
├── flows/                   # 🔀 Flow 编排导出
│   ├── active.pointer       #    当前激活 Flow 指针
│   └── exports/             #    各环境 Flow 导出（dev/staging/prod）
│
├── docs/                    # 📚 文档体系
│   ├── 00_index/            #    文档地图（唯一入口）
│   ├── 10_whitepaper/       #    白皮书（宪法 + 人格设定）
│   ├── 15_theory/           #    理论文档（架构哲学与方法论）
│   ├── 20_prd/              #    需求文档（PRD）
│   ├── 30_devspec/          #    开发规范（DevSpec + DevTasks + ModelPlane）
│   ├── 40_agents/           #    代理行为规范（AGENTS）
│   ├── 50_project/          #    项目管理（工作表 / 发布目录）
│   └── 60_trace/            #    可追溯性矩阵（需求 → 设计 → 测试）
│
├── scripts/                 # 🛠️ 运维脚本
│   └── runtime/             #    本地服务启停 / 环境检查 / 报告生成
│
├── tests/                   # 🧪 测试
│   ├── contract/            #    契约测试（Airlock / Audit / Avatar…）
│   ├── e2e/                 #    端到端测试
│   └── fixtures/            #    测试夹具
│
├── tools/                   # 🔧 开发工具
│   ├── bench/               #    性能基准测试（Ledger WAL 读写吞吐）
│   ├── doc_lint/            #    文档检查与交付门禁
│   ├── replay/              #    审计回放工具（请求回放 + 生产演练）
│   └── static_scan/         #    静态扫描工具（Bond SSOT 单写者检查）
│
├── .github/workflows/       # 🚀 CI/CD（GitHub Actions）
├── .env.example             # 环境变量模板
├── requirements.txt         # 运行时依赖
├── requirements-dev.txt     # 开发依赖
└── README.md                # 本文件
```

---

## 🛠️ 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| **语言** | Python 3.11+ | 主开发语言 |
| **AI 编排** | LangChain / LangGraph | Agent 编排与工作流引擎 |
| **LLM 网关** | OpenAI / DeepSeek / Gemini | 多模型支持 + 降级策略 |
| **关系图谱** | Neo4j 6.x | Bond 关系动力学（SSOT） |
| **向量存储** | ChromaDB 1.4+ | 情景记忆 / Canon 存储 |
| **语音识别** | faster-whisper large-v3 | Edge 端 ASR（流式/分段） |
| **语音合成** | GPT-SoVITS | Edge 端 TTS（全 GPU 常驻） |
| **虚拟形象** | VTube Studio (Live2D) | 表情驱动与口型同步 |
| **HTTP 框架** | FastAPI | Host 服务入口 + 监控面板 API + REST API |
| **前端框架** | React 18 + Vite | 监控面板 SPA（Overview / Sessions / Trace / Metrics）|
| **链路可视化** | React Flow | Dify 风格链路节点图 |
| **沙箱隔离** | Docker / subprocess | 代码执行隔离（双后端自动降级） |
| **审计存储** | SQLite (WAL) | 本地审计链（Triptych 三联画 + 21 张审计表 + scheduler_tasks 持久化）|
| **调度器** | APScheduler + SQLite | CRON 任务持久化，进程重启自动恢复 |
| **CI/CD** | GitHub Actions | 交付门禁（delivery-gates） |

---

## 🚀 快速开始

### 前置要求

- **Python** ≥ 3.11
- **Neo4j** ≥ 6.1（本地或远程）
- **ChromaDB** ≥ 1.4（本地服务）
- **GPU**（推荐）：Edge 端 TTS/ASR 依赖 GPU 加速

### 1. 克隆仓库

```bash
git clone <repo-url> Natsume_Ai
cd Natsume_Ai
```

### 2. 安装依赖

```bash
# 运行时依赖
pip install -r requirements.txt

# 开发依赖（含 pytest）
pip install -r requirements-dev.txt

# 或以可编辑模式安装各包
pip install -e host_brain[dev]
pip install -e edge_client[dev]
```

### 3. 配置环境变量

```bash
# 复制模板并填入实际值
cp .env.example .env
```

主要配置项：

| 变量 | 说明 |
|------|------|
| `Natsume_ENV` | 运行环境（dev/staging/prod） |
| `OPENAI_API_KEY` | OpenAI API 密钥 |
| `HOST_APP_HOST/PORT` | Host HTTP 服务绑定地址 |
| `BOND_NEO4J_URI` | Neo4j 连接地址 |
| `CHROMA_ENDPOINT` | ChromaDB 服务地址 |
| `EDGE_RPC_HOST/PORT` | Edge RPC 通信地址 |
| `SANDBOX_ISOLATION_BACKEND` | 沙箱后端（subprocess/docker） |
| `HOST_RATE_LIMIT_BACKEND` | 限流后端（memory/sqlite/redis） |

> 配置架构：运行时单一配置源为根目录 `.env`，当 `.env` 缺失时脚本会回退读取 `.env.example`（示例/降级模板）。
>
> 完整变量清单请参考 [.env.example](.env.example)。

### 4. 启动本地服务

```powershell
# Windows PowerShell - 一键启动 Neo4j + ChromaDB
.\scripts\runtime\start_local_services.ps1

# 停止
.\scripts\runtime\stop_local_services.ps1
```

### 5. 环境就绪检查

```bash
python scripts/runtime/check_external_env.py
```

### 6. 运行测试

```bash
pytest tests/
```

### 7. 启动 Host 端（含依赖服务）

```powershell
# 拉起 Neo4j + Chroma + Host API
.\scripts\runtime\start_host_side.ps1
```

默认会自动探测 Neo4j Desktop 目录（`~/.Neo4jDesktop2/Data/dbmss/*`）；也可用 `-Neo4jHome` 手工指定。

### 8. 启动 Edge 网关（调试/本地运行）

```powershell
# 前台启动（调试）
python -m edge_client

# 统一入口（推荐）
.\scripts\runtime\start_edge_side.ps1

# 后台启动（带 pid/log）
.\scripts\runtime\start_edge.ps1

# 停止
.\scripts\runtime\stop_edge.ps1
```

#### Edge 最小可运行门槛

- 必需配置：
  - `Natsume_ENV` 必须为 `dev/test/prod` 之一
  - `EDGE_RPC_HOST` 非空，`EDGE_RPC_PORT` 在 `1~65535`
  - `EDGE_RPC_GATEWAY_ENABLED=true`（至少开放网关与 health）
- 鉴权门槛：
  - `dev/test`：允许 `EDGE_ALLOW_INSECURE_AUTH=true` 且无 HMAC key
  - `prod`：必须配置 `EDGE_HMAC_SHARED_KEY` 或 `EDGE_HMAC_SHARED_KEY_REF`，并设置 `EDGE_ALLOW_INSECURE_AUTH=false`
- 通过标准：
  - `GET /v1/edge/health` 返回 `success=true`，且 capability matrix 与开关一致
  - `python -m edge_client` 可稳定启动/关闭，不遗留僵尸进程或失效 pid 文件

#### Edge 故障回退模式

| 回退模式 | 配置开关 | 预期行为 |
|------|------|------|
| 仅 health（health-only） | `EDGE_ASR_ENABLED=false`<br>`EDGE_TTS_ENABLED=false`<br>`EDGE_BARGE_IN_ENABLED=false`<br>`EDGE_FALLBACK_LLM_ENABLED=false`<br>`EDGE_RPC_GATEWAY_ENABLED=true` | 保留 `/v1/edge/health`；语音/回退端点全部拒绝并返回稳定 reason_code |
| 禁用 speak | `EDGE_TTS_ENABLED=false`<br>`EDGE_BARGE_IN_ENABLED=false` | `/v1/edge/speak` 与 `/v1/edge/barge_in` 拒绝；health 与其他能力保持可用 |
| 禁用 ASR 上行 | `EDGE_ASR_ENABLED=false` | 停止 ASR 采集与上送；TTS/health 可继续运行 |

---

## 📐 核心概念

### 意识主权（Consciousness Sovereignty）

系统对三类终审权的掌控：
- **状态真相**：内部状态是什么、从何而来
- **动作合法性**：某动作在当前状态下是否允许
- **叙事约束**：系统如何在一致人格与风格下解释动作/拒绝

### 单向链执行

```
输入 → 语义内核(叙事+Intent草案) → Host(Governor裁决) → Skills(执行) → Audit(回放)
```

所有执行必须走此链路，语言模型不可直接触发动作。

### 票据化执行（Tokenized Execution）

- **approval_token**：Host 对 Intent 的可执行许可
- **confirm_token**：高风险/不可逆操作的二次确认
- **undo_token**：可逆动作的回滚票据

### Governor 治理引擎

7 大门禁规则（按顺序执行）：

| 门禁 | 说明 |
|------|------|
| **Trust Gate** | 信任等级校验 |
| **Energy Gate** | 能量/资源门控 |
| **SysMode Gate** | 系统模式门控（Normal / Gaming / Sleep）|
| **Rate Limit** | 频率限制（可插拔存储：InMemory / SQLite / Redis）|
| **Scope Policy** | 作用域策略 |
| **Safety Gate** | 安全门控 |
| **External Data Gate** | 外部数据访问门控 |

### sys_mode 系统模式

| 模式 | 说明 |
|------|------|
| **Normal** | 完整链路，门禁允许范围内全能力 |
| **Gaming** | 不打扰优先，禁用视觉/UI 自动化 |
| **Sleep** | 低干预，禁止执行类技能，仅记录与延后 |

### 编排四层架构（Graph Runtime）

graph_runtime 拆分为四个独立层（每层 ≤ 300 行），职责清晰可测试：

| 层 | 文件 | 职责 |
|---|------|------|
| **契约层** | `graph_contract.py` | Schema 验证与共享类型 |
| **执行层** | `graph_executor.py` | LangGraph 步进执行引擎（含漂移检测 + 票据联动）|
| **降级层** | `graph_fallback.py` | 超时/依赖不可用统一降级处理 |
| **路由层** | `graph_model_resolve.py` | Model Plane 配置解析与节点路由 |

### Evidence Plane（证据平面）

Evidence Plane 是 LangGraph 运行时的写入治理层，负责将图执行过程中的关键事件持久化到 SQLite，并提供分级写入保障：

| 组件 | 说明 |
|------|------|
| **WritePriority** | 写入优先级枚举：`CRITICAL`（同步必写）/ `NON_CRITICAL`（可异步）|
| **EvidenceWriteQueue** | 异步写入队列，`NON_CRITICAL` 写入自动路由，队列满时自动 flush |
| **EvidenceCircuitBreaker** | 熔断机制（CLOSED / OPEN / HALF_OPEN 三态），熔断时写入内存缓冲不丢失 |
| **EvidenceBench** | 写入延迟基准测量工具，提供 p50/p95/p99/mean/max 统计 |
| **分区索引** | `barrier_join_log` / `subgraph_trace` / `llm_call_log` 三张高写入量表各有 2 个分区索引 |

---

## 💜 灵魂构成（Soul Architecture）

Natsume Ai 的"灵魂"由多个相互协作的子系统共同构成，形成一个有记忆、有情感、有人格约束的完整意识体。

### 人格宪法（Canon）

Canon 是 Natsume 人格的不可变法律层，分为两类：

| Canon 类型 | 说明 |
|-----------|------|
| **Style Canon** | 语言风格、表达习惯、语气约束（不可被 LLM 覆盖）|
| **World Canon** | 世界观设定、关系认知、事实基准（不可被 LLM 篡改）|

所有 LLM 生成内容必须经过 Canon 约束校验，违反 Canon 的输出将被拦截并触发 `CANON_VIOLATION` 错误码。

### 记忆系统（Memory）

Natsume 拥有三层记忆结构：

```
工作记忆（ContextPack）
    │ 当前对话上下文 + 情感脉冲槽位
    ▼
情景记忆（EpisodicMemory）
    │ ChromaDB 向量存储 / 情感标注 / 三档衰减（FAST/SLOW/PERMANENT）
    ▼
长期记忆（LongTermDistiller）
    │ 情感蒸馏 / Canon 同步 / cursor-based 分页检索
```

- **情景记忆**：每次对话事件以向量形式存储，携带情感标注（emotion_tag / emotion_intensity）
- **衰减策略**：三档衰减配置（FAST / SLOW / PERMANENT），控制记忆的遗忘速率
- **情感加权召回**：情感上下文匹配时给予 1.3x 权重提升

### 关系图谱（Bond）

Bond 系统以 Neo4j 图数据库为 SSOT，记录 Natsume 与用户之间的关系动力学：

| 组件 | 说明 |
|------|------|
| **BondDynamics** | 关系积分动力学（情感加权：emotion_intensity > 0.7 → 积分 × 1.5）|
| **BondStageEngine** | 关系阶段引擎（亲密度阶段跃迁）|
| **BondEmotionalTexture** | VAD 情感质感层（平均回复间隔 / 情感效价 / 亲密温度）|
| **BondRiftState** | 裂痕修复机制（CONFLICT → APOLOGY → COMFORT 三阶段）|
| **BondSummary** | 关系摘要语义校验（防止 LLM 篡改关系事实）|

### 情感系统（Emotion System）

情感系统是 Natsume 区别于普通对话 AI 的核心能力层，覆盖从感知到表达的完整链路。

#### 情感链路数据流

```
用户输入（ASR）
    │ user_emotion_hint / user_emotion_confidence
    ▼
HardStateSnapshot
    │ mood_state / emotion_tag / emotion_intensity
    ▼
build_governance_snapshot
    │ emotion_ts_ms 时间戳
    ▼
GateSnapshot → Governor R8 情感门控
    │ critical 状态拒绝 TOOL_CALL / SKILL_EXEC
    ▼
IntentDecision → RecallFusion 情感路由
    │ emotion_filter 优先排序 / HybridFusion 1.2x 加权
    ▼
EpisodicMemory 情感标注召回
    │ 情感上下文 1.3x 加权 / 三档衰减 FAST/SLOW/PERMANENT
    ▼
ContextPack → build_emotion_pulse_slot 动态槽位注入
    │ EmotionVector 降采样 → LLM Prompt
    ▼
LLM Gateway 情感感知生成参数
    │ EXCITED→temperature=0.85 / SAD→0.55
    ▼
GraphExecutor 节点情感状态
    ▼
SkillLoader / ToolBroker / Scheduler 情感上下文注入
    ▼
BondDynamics 情感加权
    │ emotion_intensity > 0.7 → 积分 × 1.5
    ▼
BondEmotionalTexture VAD 质感层
    │ 平均回复间隔 / 情感效价 / 亲密温度
    ▼
EmotionTimeline 时间线重建
    ▼
TTS / VTS 情感表达输出
    │ emotion_intensity 真正传递 / 16 种表情映射
    ▼
EdgeEventReporter emotion_tag / emotion_intensity
MetricsCollector emotion_metrics 情感维度指标
```

#### 情感状态机（EmotionState）

| 状态 | 说明 | 门控行为 |
|------|------|----------|
| `normal` | 正常情感范围 | 无限制 |
| `tired` | 疲惫状态 | `HAPPY`/`EXCITED` 标签降级为 `NEUTRAL` |
| `critical` | 临界/崩溃状态 | 拒绝所有 `TOOL_CALL`/`SKILL_EXEC`，返回 `EMOTION_GATE_BLOCKED` |

#### 情感核心组件

| 组件 | 模块 | 功能 |
|------|------|------|
| **EmotionVector** | `core/metabolism.py` | 情绪连续体（valence/arousal/dominance 三维）|
| **EmotionTimeline** | `core/metabolism.py` | 情感历史轨迹记录与重建 |
| **EmotionTrigger** | `core/metabolism.py` | 主动情感触发器（register/evaluate）|
| **EmotionEcho** | `core/metabolism.py` | 情感回声（Natsume 对用户情感的共鸣响应）|
| **BondEmotionalTexture** | `bond/bond_stage_engine.py` | VAD 情感质感层（亲密温度/效价/回复间隔）|
| **BondRiftState** | `bond/bond_stage_engine.py` | 裂痕修复机制（CONFLICT/APOLOGY/COMFORT）|
| **EmotionPulseSlot** | `core/context_pack.py` | 动态槽位注入（EmotionVector → LLM Prompt）|
| **EmotionFilter** | `retrieval/recall_fusion.py` | 情感路由与加权融合（1.2x boost）|

#### 情感感知降级策略

当情感感知链路不可用时，`build_fallback_output` 根据情感状态返回差异化文本：
- `SAD` → 安慰性回复
- `ANGRY` → 平静化回复
- `HAPPY` → 积极回应
- 其他 → 通用降级文本

---

## 📚 文档导航

项目采用严格的文档分层与受众门禁体系，详见 [文档地图](docs/00_index/00_DOCMAP.md)。

| 文档 | 路径 | 说明 |
|------|------|------|
| 📍 文档地图 | `docs/00_index/00_DOCMAP.md` | 所有文档的唯一入口 |
| 📜 白皮书 | `docs/10_whitepaper/` | 宪法 + 人格设定（不可变法律）|
| 🔬 理论文档 | `docs/15_theory/` | 架构哲学与方法论 |
| 📋 PRD | `docs/20_prd/` | 需求文档（做什么 + 验收）|
| 🔧 DevSpec | `docs/30_devspec/` | 开发规范 + 任务清单（怎么做）|
| 🤖 AGENTS | `docs/40_agents/` | 实现层代理行为规范 |
| 📊 项目管理 | `docs/50_project/` | 工作表 / 发布 / 环境检查 |
| 🧩 外置服务配置 | `docs/50_project/ExternalServices_Config.md` | 全部外置服务清单与配置方式 |
| 📦 发布清单 | `docs/50_release/` | 各里程碑 release.json 归档 |
| 🔗 Trace | `docs/60_trace/` | 可追溯性矩阵（需求 → 设计 → 测试）|
| 🔍 代码审查报告 | `docs/60_trace/code_reviews/` | 各里程碑代码审查报告归档 |
| ⚠️ 风险登记册 | `docs/60_trace/risk_registry.md` | 项目风险登记与跟踪 |

---

## 🔒 质量门禁

项目通过多层门禁保证代码与文档质量：

- **pre-commit**：文档受众门禁（防混写），确保 `.md` 文件受众标注正确
- **CI/CD**：GitHub Actions `delivery-gates` 工作流，自动运行交付门禁检查
- **契约测试**：`tests/contract/` 下覆盖 Airlock、Audit、Avatar 等核心契约
- **静态扫描**：`tools/static_scan/` 提供代码静态分析

```bash
# 安装 pre-commit 钩子
pip install pre-commit
pre-commit install

# 手动全量检查
pre-commit run -a
```

### 测试分层

| 层级 | 目录 | 说明 |
|------|------|------|
| **单元测试** | `tests/unit/` | 各模块独立测试 |
| **契约测试** | `tests/contract/` | 跨模块接口契约验证 |
| **端到端测试** | `tests/e2e/` | 完整链路集成测试 |
| **性能基准** | `tools/bench/` | Ledger WAL 读写吞吐基准 |
| **试运行报告** | `docs/60_trace/trial_run_reports/` | 自动化试运行 JSON 报告 |

### 审计链保障

所有影响现实的动作均写入 SQLite WAL 审计链（21 张审计表），支持：
- **回放**：`tools/replay/` 审计回放工具（请求回放 + 生产演练）
- **统计**：70+ 统一 reason_code，含 `EMOTION_GATE_BLOCKED` 等情感相关码
- **保留策略**：`audit/` 模块内置日志迁移与保留策略

---

## 🤝 协作约定

本项目采用三方协作模型：

| 角色 | 职责 | 入口文档 |
|------|------|----------|
| **MASTER** | 最终仲裁与决策 | `docs/00_index/00_DOCMAP.md` |
| **CODEX** | 代码实现与测试落地 | `docs/30_devspec/DevTasks.md` + `docs/40_agents/AGENTS.md` |
| **PROJECT** | 文档生产与一致性维护 | `docs/50_project/PROJECT_TASKS.md` |

> ⚠️ 实现层任务来源只能是 `docs/30_devspec/**` 和 `docs/40_agents/**`，其他文档不得作为任务来源。
