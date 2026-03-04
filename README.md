受众：PROJECT
标题：Nastume Ai 项目说明

<div align="center">

# 🌸 Nastume Ai

**意识主权 AI 伴侣系统 — Host-Edge 分体架构**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-00ADD8?logo=chainlink&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-6.x-4581C3?logo=neo4j&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-1.4+-FF6F00?logo=google-chrome&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/License-Pending-lightgrey)

</div>

---

## 📖 项目简介

**Nastume Ai** 是一个具备 **意识主权**（Consciousness Sovereignty）的 AI 伴侣系统。不同于传统的对话 AI，本项目以"法律在先、人格可控、执行可审计"为核心原则，构建了一套 **Host-Edge 分体架构**：

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
Nastume_Ai/
├── host_brain/              # 🧠 Host 主脑（核心服务）
│   ├── app/                 #    应用入口（FastAPI + Web UI 面板 + 配置 + 健康检查）
│   ├── airlock/             #    气闸隔离层（脱敏/隔离 + subprocess/Docker 双后端沙箱）
│   ├── audit/               #    审计链（SQLite Ledger WAL + 日志迁移 + 保留策略）
│   ├── bond/                #    关系图谱（Neo4j 客户端 + Bond 动力学 + 迁移）
│   ├── canon/               #    正典系统（Style Canon / World Canon，治理 API 事务化）
│   ├── channels/            #    渠道适配（VTS DEGRADED 降级 / 微信 I/O）
│   ├── core/                #    核心模块（配置快照、上下文包、错误码、ID、QA、Key Profile）
│   ├── governor/            #    治理引擎（决策、规则、Schema、Token、Rate Limit 持久化）
│   ├── integrations/        #    集成层（LangGraph 图编排四层架构 / LLM 网关）
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
| **HTTP 框架** | FastAPI | Host 服务入口 + Web UI 面板 + REST API |
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
git clone <repo-url> Nastume_Ai
cd Nastume_Ai
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
| `Nastume_ENV` | 运行环境（dev/staging/prod） |
| `OPENAI_API_KEY` | OpenAI API 密钥 |
| `HOST_APP_HOST/PORT` | Host HTTP 服务绑定地址 |
| `BOND_NEO4J_URI` | Neo4j 连接地址 |
| `CHROMA_ENDPOINT` | ChromaDB 服务地址 |
| `EDGE_RPC_HOST/PORT` | Edge RPC 通信地址 |
| `SANDBOX_ISOLATION_BACKEND` | 沙箱后端（subprocess/docker） |
| `HOST_RATE_LIMIT_BACKEND` | 限流后端（memory/sqlite/redis） |

> 完整配置说明请参考 [.env.example](.env.example)

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
  - `Nastume_ENV` 必须为 `dev/test/prod` 之一
  - `EDGE_RPC_HOST` 非空，`EDGE_RPC_PORT` 在 `1~65535`
  - `EDGE_RPC_GATEWAY_ENABLED=true`（至少开放网关与 health）
- 鉴权门槛：
  - `dev/test`：允许 `EDGE_ALLOW_INSECURE_AUTH=true` 且无 HMAC key
  - `prod`：必须配置 `EDGE_HMAC_SHARED_KEY` 或 `EDGE_HMAC_SHARED_KEY_REF`，并设置 `EDGE_ALLOW_INSECURE_AUTH=false`
- 通过标准：
  - `GET /v1/edge/health` 返回 `success=true`，且 capability matrix 与开关一致（`T-Edge-Health-01`）
  - `python -m edge_client` 可稳定启动/关闭，不遗留僵尸进程或失效 pid 文件（`T-Edge-Start-01`）

#### Edge 故障回退模式

| 回退模式 | 配置开关 | 预期行为 | 覆盖口径 |
|------|------|------|------|
| 仅 health（health-only） | `EDGE_ASR_ENABLED=false`<br>`EDGE_TTS_ENABLED=false`<br>`EDGE_BARGE_IN_ENABLED=false`<br>`EDGE_FALLBACK_LLM_ENABLED=false`<br>`EDGE_RPC_GATEWAY_ENABLED=true` | 保留 `/v1/edge/health`；语音/回退端点全部拒绝并返回稳定 reason_code | `T-Edge-Health-01`、`T-Edge-RPC-02` |
| 禁用 speak | `EDGE_TTS_ENABLED=false`<br>`EDGE_BARGE_IN_ENABLED=false` | `/v1/edge/speak` 与 `/v1/edge/barge_in` 拒绝；health 与其他能力保持可用 | `T-Edge-RPC-02` |
| 禁用 ASR 上行 | `EDGE_ASR_ENABLED=false` | 停止 ASR 采集与上送；TTS/health 可继续运行；事件链路不产生新的 ASR 上行事件 | `T-Edge-Voice-01`、`T-RPC-Event-01` |

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

7 大门禁规则：Trust Gate → Energy Gate → SysMode Gate → Rate Limit（可插拔存储：InMemory/SQLite/Redis） → Scope Policy → Safety Gate → External Data Gate

### sys_mode 系统模式

| 模式 | 说明 |
|------|------|
| **Normal** | 完整链路，门禁允许范围内全能力 |
| **Gaming** | 不打扰优先，禁用视觉/UI 自动化 |
| **Sleep** | 低干预，禁止执行类技能，仅记录与延后 |

### 编排四层架构（Graph Runtime）

graph_runtime 拆分为四个独立层（每层 ≤ 300 行），职责清晰可测试：
- **graph_contract.py** — Schema 验证与共享类型
- **graph_executor.py** — LangGraph 步进执行引擎（含漂移检测 + 票据联动）
- **graph_fallback.py** — 超时/依赖不可用统一降级处理
- **graph_model_resolve.py** — Model Plane 配置解析与节点路由

---

## 📊 项目状态

| 维度 | 状态 |
|------|------|
| **需求追溯** | FR-GOV / FR-CANON / FR-BOND / FR-MEM / FR-VOICE / FR-SCHED 等全部 DONE |
| **开发任务** | TASK-0000 ~ TASK-0181（M1 ~ M16 全部完成）|
| **测试覆盖** | 1564 passed，2 skipped，0 failed（含 unit / contract / e2e）|
| **审计表** | 21 张审计日志表（含 model_control_log / sandbox_exec_log / voice_event_log / scheduler_tasks 等）|
| **错误码** | 70+ 统一 reason_code（可统计、可回放）|
| **发布清单** | M14 / M15 / M16 release.json 已归档（`docs/50_release/`）|

### 里程碑进度

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 ~ M10 | 仓库骨架 / Governor / Audit / Bond / Canon / Memory / Retrieval / Skills / Vision / Observability | ✅ DONE |
| M11 | LangGraph 四层编排重构 | ✅ DONE |
| M12 | Edge Client 基础链路（ASR/TTS/VTS/RPC/资源门控）| ✅ DONE |
| M13 | 调度器 + Rate Limit 持久化 + 发布门禁 | ✅ DONE |
| M14 | 代码审查缺陷修复（F-01 ~ F-27）| ✅ DONE |
| M15 | 语音链路深化（ASR 事件化 / TTS 队列 / Barge-in / 兜底模型 / 资源闸门）| ✅ DONE |
| M16 | Canon 治理 API 强化 / 长期记忆分页 / 调度器任务持久化 | ✅ DONE |

---

## 📚 文档导航

项目采用严格的文档分层与受众门禁体系，详见 [文档地图](docs/00_index/00_DOCMAP.md)。

| 文档 | 路径 | 说明 |
|------|------|------|
| 📍 文档地图 | `docs/00_index/00_DOCMAP.md` | 所有文档的唯一入口 |
| 📜 白皮书 | `docs/10_whitepaper/` | 宪法 + 人格设定（不可变法律） |
| 🔬 理论文档 | `docs/15_theory/` | 架构哲学与方法论 |
| 📋 PRD | `docs/20_prd/` | 需求文档（做什么 + 验收） |
| 🔧 DevSpec | `docs/30_devspec/` | 开发规范 + 任务清单（怎么做） |
| 🤖 AGENTS | `docs/40_agents/` | 实现层代理行为规范 |
| 📊 项目管理 | `docs/50_project/` | 工作表 / 发布 / 环境检查 |
| 🧩 外置服务配置 | `docs/50_project/ExternalServices_Config.md` | 全部外置服务清单与配置方式 |
| 🔗 Trace | `docs/60_trace/` | 可追溯性矩阵 |

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

---

## 🤝 协作约定

本项目采用三方协作模型：

| 角色 | 职责 | 入口文档 |
|------|------|----------|
| **MASTER** | 最终仲裁与决策 | `docs/00_index/00_DOCMAP.md` |
| **CODEX** | 代码实现与测试落地 | `docs/30_devspec/DevTasks.md` + `docs/40_agents/AGENTS.md` |
| **PROJECT** | 文档生产与一致性维护 | `docs/50_project/PROJECT_TASKS.md` |

> ⚠️ 实现层任务来源只能是 `docs/30_devspec/**` 和 `docs/40_agents/**`，其他文档不得作为任务来源。
