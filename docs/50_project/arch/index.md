受众：MASTER
标题：Natsume AI 架构文档目录

# Natsume AI 架构文档目录

版本：v4.0
维护日期：2026-07-15
状态：`CURRENT_ARCHITECTURE_CATALOG`

本目录只保留当前结构说明、稳定目标、差距、专项架构和可维护图册。旧路线图、混写完成报告、旧 Agentic RAG/Gateway 方案和按文件行数生成的模块快照已删除；Git 历史承担追溯。

## 推荐阅读顺序

1. [系统架构总览](SYSTEM_ARCHITECTURE.md)：先建立进程、分层、authority 和状态模型。
2. [功能模块地图](FUNCTIONAL_MODULES.md)：查功能、代码 owner、依赖和当前缺口。
3. [关键数据流](DATA_FLOW_ARCHITECTURE.md)：追踪入口、TaskGraph、Memory、Gateway、Edge 和 receipt。
4. [架构图册](diagrams/00_INDEX.md)：快速查看七张当前拓扑/流程图。
5. [当前代码事实](CURRENT_CODE_ARCHITECTURE.md)：核对哪些链真正接通。
6. [目标架构](TARGET_ARCHITECTURE.md)：理解最终必须成立的边界。
7. [架构差距矩阵](ARCHITECTURE_GAP_MATRIX.md)：查看当前与目标之间的可执行差距。

## 当前结构文档

| 文档 | 回答的问题 | 是否记录完成度 |
|---|---|---:|
| [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) | 系统如何分层、进程如何协作、authority 在哪里 | 只标必要的当前边界 |
| [FUNCTIONAL_MODULES.md](FUNCTIONAL_MODULES.md) | 每个功能和代码包负责什么 | 是，按模块范围定级 |
| [DATA_FLOW_ARCHITECTURE.md](DATA_FLOW_ARCHITECTURE.md) | 数据、状态、副作用和回执如何流动 | 是，区分 WIRED/PARTIAL/BROKEN/TARGET |
| [MONITOR_UI_ARCHITECTURE.md](MONITOR_UI_ARCHITECTURE.md) | Operator 前端如何组织、查询和发命令 | 是，按真实源码与 API 边界 |
| [DEPLOYMENT_ARCHITECTURE.md](DEPLOYMENT_ARCHITECTURE.md) | Host/Obs/Edge/Gateway/UI 和外部服务如何部署 | 是，区分本地、可选和环境依赖 |
| [diagrams/00_INDEX.md](diagrams/00_INDEX.md) | 关键关系的可视化速查 | 跟随 Current/Data Flow |

## 状态与演进 authority

| 文档 | 唯一职责 |
|---|---|
| [CURRENT_CODE_ARCHITECTURE.md](CURRENT_CODE_ARCHITECTURE.md) | 生产代码现在是什么 |
| [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md) | 最终稳定目标，不写任务状态 |
| [ARCHITECTURE_GAP_MATRIX.md](ARCHITECTURE_GAP_MATRIX.md) | 当前与目标的差距解释 |
| [正式执行合同](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) | 唯一活动执行顺序、依赖和 Gate |
| [机器状态](../../../configs/release/current_architecture_status.json) | 自动门禁消费的结构化状态 |

## 专项架构

- [Pentest 模块架构](PENTEST_MODULES_ARCHITECTURE.md)：授权安全实验室的模块和边界。
- [环境与外部服务](DEPLOYMENT_ARCHITECTURE.md#6-外部服务)：Neo4j、Chroma、Redis 与可选依赖。
- [Monitor UI](MONITOR_UI_ARCHITECTURE.md)：数字生命驾驶舱、Task/Review 和 Operator 命令。

专项文档与 Current/Gap 冲突时，以生产 composition、机器状态和 Current 为准。

## 已删除的污染文档

以下类型不再保留在当前目录：

- `CODEX_*PLAN/IDEAS/CLOSURE*` 路线图与完成报告；
- 旧 `AGENTIC_RAG_ARCHITECTURE` 文件名兼容文档；
- 混合历史 Gateway 方案和当前代码事实的重复文档；
- 旧模式语义、LLM 恢复计划和按固定节点枚举的数据流分析；
- “存在文档即通过”的单元测试。

恢复这些文件不会增加产品能力，只会重新引入第二套完成口径。

## 更新触发矩阵

| 代码变化 | 必须同步 |
|---|---|
| 进程、composition、bounded context | System、Current、Functional Modules |
| route、TaskGraph、Attempt、Budget、Review、Acceptance | Data Flow、图 02/04、Current、Gap |
| Life、Memory、Persona、Scene、World writer | Theory、Data Flow、图 03/05、Current、Gap |
| Gateway、Edge、平台或设备合同 | Data Flow、图 01/06、Deployment |
| Obs API、Monitor route/query/command | Monitor UI、图 07、Functional Modules |
| 启动脚本、环境变量、Docker 或端口 owner | Deployment、README |
| 当前能力状态 | Machine Status、Current、Gap、Execution、Trace |

详细流程见 [文档同步工作流](../guides/DOCUMENT_SYNC_WORKFLOW.md)。
