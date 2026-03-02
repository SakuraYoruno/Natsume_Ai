<div align="center">

# 🌸 Natsume Ai

**意识主权 AI 伴侣系统 — Host-Edge 分体架构**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-00ADD8?logo=chainlink&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-6.x-4581C3?logo=neo4j&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-1.4+-FF6F00?logo=google-chrome&logoColor=white)
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
│   ├── app/                 #    应用入口、配置、健康检查
│   ├── airlock/             #    气闸隔离层（外部数据脱敏/隔离）
│   ├── audit/               #    审计链（SQLite Ledger + 日志迁移）
│   ├── bond/                #    关系图谱（Neo4j 客户端 + Bond 动力学）
│   ├── canon/               #    正典系统（Style Canon / World Canon）
│   ├── channels/            #    渠道适配（VTS / 微信）
│   ├── core/                #    核心模块（配置快照、上下文包、错误码、ID、QA）
│   ├── governor/            #    治理引擎（决策、规则、Schema、Token）
│   ├── integrations/        #    集成层（LangGraph 运行时 / LLM 网关）
│   ├── lsp/                 #    密钥与策略（Vault / Policies）
│   ├── memory/              #    记忆系统（Chroma 向量存储 / 情景记忆）
│   ├── release/             #    发布清单管理
│   ├── retrieval/           #    检索链（Embedding / 召回融合 / 重排）
│   ├── skills/              #    技能系统（加载器 / 注册表 / 实现）
│   ├── tools/               #    工具系统（Broker / Catalog / MCP 适配）
│   └── vision/              #    视觉链（摄像头事件 / 屏幕 OCR）
│
├── edge_client/             # 🎙️ Edge 边缘客户端
│   ├── actuators/           #    执行器（语音合成 TTS）
│   ├── senses/              #    感知层（语音识别 ASR）
│   ├── rpc/                 #    RPC 通信（健康检查 / 语音 / 兜底 LLM / 资源门控）
│   └── proofs/              #    资源证明与门控
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
│   ├── doc_lint/            #    文档检查与交付门禁
│   ├── replay/              #    审计回放工具
│   └── static_scan/         #    静态扫描工具
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
| **审计存储** | SQLite | 本地审计链（Triptych 三联画） |
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
| `NATSUME_ENV` | 运行环境（dev/staging/prod） |
| `OPENAI_API_KEY` | OpenAI API 密钥 |
| `BOND_NEO4J_URI` | Neo4j 连接地址 |
| `CHROMA_ENDPOINT` | ChromaDB 服务地址 |
| `EDGE_RPC_HOST/PORT` | Edge RPC 通信地址 |

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

7 大门禁规则：Trust Gate → Energy Gate → SysMode Gate → Rate Limit → Scope Policy → Safety Gate → External Data Gate

### sys_mode 系统模式

| 模式 | 说明 |
|------|------|
| **Normal** | 完整链路，门禁允许范围内全能力 |
| **Gaming** | 不打扰优先，禁用视觉/UI 自动化 |
| **Sleep** | 低干预，禁止执行类技能，仅记录与延后 |

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
