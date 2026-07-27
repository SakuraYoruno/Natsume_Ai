受众：MASTER
标题：Natsume AI 架构文档目录

# Natsume AI 架构文档目录

版本：v4.8
维护日期：2026-07-27
状态：`CURRENT_ARCHITECTURE_CATALOG`

本目录只保留当前结构说明、稳定目标、差距、专项架构和可维护图册。旧路线图、混写完成报告、旧 Agentic RAG/Gateway 方案和按文件行数生成的模块快照已删除；Git 历史承担追溯。

当前 Plan/Delegate 政策边界：Plan 在 admission 前冻结 Graph、Budget、
Registry/Review 和唯一 result effect owner；Delegate 冻结 target/adapter、
durable Review、exact-zero inner budget 与无直接 Effect。initial、replay、
Review continuation、startup recovery 和 frozen execution 都重开 content
address。定向单元 `89 passed`、真实 SQLite 四路线 `1 passed`；正式
clean-SHA `3db60ff1` 的同库 v3 收据已验证七路，并把当前启用 Plan/Delegate
范围提升为 `COMPLETE`。Scheduler/Internal/Operator 与 NE-01 保持未完成。

当前 Operator 库存为 `14 active governed / 0 blocked / 23 retired`。
退役 ID 不在后端 manifest、domain handler、OpenAPI 或生成式 Monitor
client 中；BFF 只返回 410 replacement，浏览器不发请求。旧文中的
`14/37、23 blocked` 是历史提交口径。替代 authority 与真实 canary尚未
全部完成，因此 Operator 和 NE-01 仍不封板。

当前 Skill 执行边界：canonical Host Registry 的 93 个条目按 exact descriptor 分为 28 个只读、
13 个 proposal-only、3 个 post-Acceptance Workspace 和 49 个 blocked。
只有前 44 个进入运行 manifest；后 49 个等待 typed Acceptance Effect，
catalog 可见不等于执行可用或已完成。干净提交 `c68850f8` 的 v3 同库收据
已经把 44 个启用项的 Skill 路线提升为 `COMPLETE`，但没有授权或完成
49 个 blocked 能力。
旧 Personality Registry 的 19 个 metadata 与动态 MCP Skill 均不进入
governed manifest。

当前 Workspace 写边界：Governed Internal 与三条已登记 risk-4 Direct Agent Skill 都从 frozen source 密封 manifest/binary patch，并只在 Acceptance 后经 0116 晋升；Skill 路径要求 exact consumed Review、server-bound V3 和先于可见 Effect 的 promotion handler，调用方 source branch 不移动。Domain Pack、其它 Skill、一般恢复、handle-level TOCTOU、OS/container tenant 与跨路线 canary 仍为未完成项。

当前 Operation 恢复边界：0130 持久化 exact hold，0131 要求 governed signed reconciliation 精确绑定该 current case，并在既有 Operation/Outbox UoW 后确认 case 已退出 current projection。UNKNOWN/CANCELLATION 已有本地真实 SQLite 证据；checkpoint conflict 继续 quarantine。该项不等于自动副作用归因、一般恢复或 NE-01 完成。

当前节点预算边界：0132 在 READY claim 的同一事务内为每个 Attempt 创建 child Budget Reservation，Graph reservation 仍是唯一 aggregate ceiling；普通 charge、EXCEEDED winner、terminal sibling observation 与 retry 同时写父子 usage。真实 SQLite 已覆盖双根、竞争、重启、篡改和升级回填。全部 cancellation/release、七路线 policy、波次集成与 NE-01 仍未完成。

当前节点验证边界：0133 在 Node transition 同一事务内追加 exact Attempt-bound Verification decision；VERIFIED checkpoint 只读重开该决定。直接拒绝同时终结 Node、Graph 与 graph Budget，多节点拒绝仍复用 0090 settlement。真实 SQLite 已覆盖进程重开、exact replay、篡改回滚和 0132→0133 升级；一般恢复、七路线 commit-bound receipt 与 NE-01 仍未完成。

当前 Chat 提交边界：Acceptance Effect 顺序要求 exact Life projection barrier 先于 external reply；foreground projection receipt 只有在 assistant turn 与 LifeMemory admission 均持久成功后成立。外部 delivery 保持幂等 eventual，commit-bound 七路线验收仍缺，因此该项是 Chat 单控制收口，不是 NE-01 完成。

当前 Agent 模型预算边界：Internal 与 governed Agent Skill 的多轮模型调用
已消费同一 frozen Task Budget remainder；Skill sealed authority 会保留
canonical Budget JSON，只读 V2 WorkOrder 由 server-side context 重建为
V3。缺 usage、cache、重复 measurement 或绑定漂移会停止下一次调用。
program-wide Budget、真实 provider canary 与 NE-01 波次验收仍未完成。

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
