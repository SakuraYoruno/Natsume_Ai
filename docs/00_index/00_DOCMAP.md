受众：MASTER
标题：Natsume AI 文档地图与权威顺序

# Natsume AI 文档地图与权威顺序

版本：v8.5
维护日期：2026-07-18
状态：`CURRENT_AUTHORITY_MAP`

## 1. 权威顺序

1. 生产代码、production composition、migration 和机器状态。
2. [当前代码事实](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)。
3. [稳定目标架构](../50_project/arch/TARGET_ARCHITECTURE.md)。
4. [当前架构差距](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)。
5. [唯一活动执行合同](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。
6. PRD、DevSpec、Trace 和 TestPlan。
7. 历史计划、固定 receipt 和审计快照。

README、理论、系统总览、模块地图和数据流负责解释系统，但不能覆盖上述状态 authority。历史 `DONE/PASS` 只对固定提交、环境和声明范围成立。

## 2. 文档信息架构

```mermaid
flowchart TD
    README["README\n项目入口、启动与导航"]
    THEORY["Theory\n为什么这样设计"]
    SYSTEM["System Architecture\n系统怎样组织"]
    MODULES["Functional Modules\n谁负责什么"]
    FLOW["Data Flow + Diagrams\n状态和副作用怎样流动"]
    CURRENT["Current\n代码现在是什么"]
    TARGET["Target\n最终应是什么"]
    GAP["Gap\n还差什么"]
    EXEC["Execution\n按什么顺序做"]
    PRODUCT["PRD / DevSpec / Trace / TestPlan"]
    HISTORY["History\n固定范围证据"]

    README --> THEORY
    README --> SYSTEM
    SYSTEM --> MODULES
    SYSTEM --> FLOW
    CURRENT --> GAP
    TARGET --> GAP
    GAP --> EXEC
    THEORY --> PRODUCT
    SYSTEM --> PRODUCT
    EXEC --> PRODUCT
    HISTORY -."scope-bound evidence".-> CURRENT
```

## 3. 按问题阅读

| 问题 | 文档 |
|---|---|
| 这是什么项目、如何运行？ | [README](../../README.md) |
| 为什么模型不能直接执行或修改人格？ | [核心理论](../15_theory/Theory.md) |
| Host、Obs、Gateway、Edge 如何协作？ | [系统架构](../50_project/arch/SYSTEM_ARCHITECTURE.md) |
| 某个功能由哪个包负责？ | [功能模块](../50_project/arch/FUNCTIONAL_MODULES.md) |
| 请求、Memory、Gateway、Edge、receipt 怎么流？ | [关键数据流](../50_project/arch/DATA_FLOW_ARCHITECTURE.md) |
| 想快速看图？ | [架构图册](../50_project/arch/diagrams/00_INDEX.md) |
| 前端如何查询和发 Operator 命令？ | [Monitor UI 架构](../50_project/arch/MONITOR_UI_ARCHITECTURE.md) |
| 服务如何部署、Docker 启动什么？ | [部署架构](../50_project/arch/DEPLOYMENT_ARCHITECTURE.md) |
| 哪些能力真的可用？ | [当前代码事实](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md) |
| 为什么不能封板？ | [架构差距矩阵](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md) |
| 下一阶段做什么？ | [正式执行合同](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) |

## 4. 当前状态摘要

- Full Architecture：`INCOMPLETE`。
- Host `chat/skill/delegate/plan`：`WIRED`。
- Scheduler：`WIRED`；Internal Agent：`WIRED`（v2 仅为 executor 兼容边界）。
- Durable DAG：`PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。B5A/B5B 以显式 `result_node_id`、0091 和 canonical projection 冻结整图成功；B5C 已推进到 0098。Cancellation、Operation 签名对账与 Effect 签名对账均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`，开启 cancellation 时要求两类对账入口、Secure Ingress `ENFORCE`、可用 Gateway 和 verification-only Ed25519 公钥。0098 只追加 immutable case/proof sidecar：PENDING proof 可作为新 cancellation decision evidence；FINALIZED correction 只在 trusted read 验证 immutable legacy decision ref/payload digest，绝不改写旧 decision、fence 或 evidence。proof 的 case/intent/effect/fence/Attempt/resolution/actor/Outbox 与 `graph_revision` 均被签名和物理 trigger 绑定。生产门禁只阻断实际持久化且未修复/未隔离的 legacy inventory；仓库无 SQLite 资产不能证明外部生产数据已清理。Budget 高竞争、部署密钥/凭据、完整 key lifecycle 与真实 canary 仍缺，atomic、bounded 与 cancellation 均保持 OFF。
- PlanningSearch：内核可用；Code D16 有 dynamic pure-shadow catalog/compile，D17 有 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` 的双 detached worktree 本地实验机械链，D18 有独立 0106 preregistration → paired evidence → `VERIFIED_CLEAN` → terminal receipt 的 append-only durable ledger。D19 仅为已预注册的冻结 `python -m pytest -q <repo-relative-test>` 在同一主机受信 worktree 子进程中强制 argv、测试次数和总验证时限，并把无路径命令回执嵌入 arm evidence；它不是模型/插件/网络/文件系统隔离、通用硬预算或 production influence。跨领域真实收益仍 `UNPROVEN`。
- Persona 演化：`CENTRAL_FENCE_WIRED / PROPOSAL_PIPELINE_MISSING`。
- 长期演化程序：作为整体尚未实现。

机器状态：[current_architecture_status.json](../../configs/release/current_architecture_status.json)。摘要变化时必须先更新机器状态、Current 和 Gap，不能只改本地图。

## 5. 稳定合同

- [产品需求](../20_prd/PRD.md)：产品 WHAT、用户主权和验收边界。
- [工程合同](../30_devspec/DevSpec.md)：工程 MUST/MUST NOT。
- [执行任务](../30_devspec/DevTasks.md)：活动执行波的任务映射。
- [需求追溯](../60_trace/requirements_trace.md)：FR → code → test → execution wave。
- [测试计划](../../tests/TESTPLAN.md)：Gate 和测试层级。

## 6. 目录地图

| 目录 | 定位 |
|---|---|
| `10_whitepaper` | 产品宪法、用户主权和人格原则 |
| `15_theory` | 唯一核心理论总纲 |
| `20_prd` | 稳定产品需求，只写 WHAT |
| `30_devspec` | 稳定工程合同与唯一活动执行文档 |
| `35_quality` | 可复用质量规范与范围受限的旧审计 |
| `40_agents` | Codex 协作约束 |
| `45_testing` | 当前专项机器证据和实验报告 |
| `50_project` | 当前架构、部署和工程指南 |
| `50_release` | 固定提交的发布与回滚证据 |
| `60_trace` | 当前需求追溯、风险和故障记录 |
| `_history` | 历史快照，不作为 current authority |

## 7. 维护规则

1. 需求、理论、当前事实、目标、差距、执行状态和历史证据必须分文件维护。
2. 生产架构变化时按 [文档同步工作流](../50_project/guides/DOCUMENT_SYNC_WORKFLOW.md) 更新对应文档。
3. 新架构状态必须同步机器 JSON、Current、Gap、Execution 和 Trace。
4. 旧方案、完成报告和文件清单快照不得留在当前架构目录冒充现状。
5. 临时调查、命令日志和运行输出进入被忽略的 `artifacts/`，可变运行数据进入 `runtime/`。
6. 删除或移动文档时同步本地图、目录 index、链接和文档门禁策略。
