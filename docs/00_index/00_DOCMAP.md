受众：MASTER
标题：Natsume AI 文档地图

# 文档地图

本页是当前文档入口。判断能力是否完成时，优先级固定为：生产代码与真实运行证据 > 机器状态 > 当前权威文档 > 历史归档。

## 当前权威文档

| 问题 | 文档 | 职责 |
|---|---|---|
| 项目是什么、如何启动 | [README](../../README.md) | 产品入口、能力矩阵、快速启动 |
| 为什么这样设计 | [Theory](../15_theory/Theory.md) | 情感生命原则与工程取舍 |
| 要交付什么体验 | [PRD](../20_prd/PRD.md) | 用户目标、需求与验收指标 |
| 代码边界是什么 | [DevSpec](../30_devspec/DevSpec.md) | 合同、组件、状态和约束 |
| 现在还做什么 | [DevTasks](../30_devspec/DevTasks.md) | 唯一当前任务清单 |
| 当前如何执行 | [执行合同](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) | 当前波次、退出条件和交付协议 |
| 当前代码真实形态 | [当前代码架构](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md) | as-is 事实 |
| 系统如何协作 | [系统架构](../50_project/arch/SYSTEM_ARCHITECTURE.md) | 进程与 authority 边界 |
| 功能由谁负责 | [功能模块](../50_project/arch/FUNCTIONAL_MODULES.md) | 模块职责与生产接线 |
| 数据如何流动 | [数据流](../50_project/arch/DATA_FLOW_ARCHITECTURE.md) | 对话、情感、工具与交付流 |
| 最终目标是什么 | [目标架构](../50_project/arch/TARGET_ARCHITECTURE.md) | to-be 架构 |
| 还差什么 | [缺口矩阵](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md) | 未完成项与优先级 |
| 如何部署 | [部署架构](../50_project/arch/DEPLOYMENT_ARCHITECTURE.md) | 服务、端口、配置和启停 |
| Monitor 如何工作 | [Monitor UI 架构](../50_project/arch/MONITOR_UI_ARCHITECTURE.md) | Operator 读写边界 |
| 如何验证 | [测试计划](../../tests/TESTPLAN.md) | 测试层次和真实验收 |
| 需求证据在哪里 | [需求追溯](../60_trace/requirements_trace.md) | 需求到代码／测试／状态映射 |
| 机器如何判断状态 | [机器状态](../../configs/release/current_architecture_status.json) | 可机读的唯一当前状态摘要 |

## 图表

[架构图索引](../50_project/arch/diagrams/00_INDEX.md) 收录运行拓扑、主链、召回、执行和观测图。图表帮助理解，不单独构成完成证据。

## 测试证据与发布记录

- [`docs/45_testing/`](../45_testing/index.md)：当前测试入口及被配置硬引用的 scope-bound 收据。
- [`docs/50_release/`](../50_release/index.md)：工具生成的固定发布证据，不是当前状态入口。
- [`docs/60_trace/`](../60_trace/index.md)：当前需求追溯，不再保存累计任务流水账。
- `artifacts/local-gates/`：本地临时证据目录，默认不提交。

## 历史归档

[`docs/_history/`](../_history/README.md) 中的旧计划、审计和阶段回执仅解释演进过程。历史文档中的 `PASS`、`DONE` 或阶段编号不得覆盖当前机器状态，也不得作为生产能力完成证明。

## 状态词

| 状态 | 含义 |
|---|---|
| `WIRED` | 已进入生产调用点，但不代表完成最终体验验收 |
| `LOCAL_PASS` | 本机真实执行通过，尚未覆盖目标部署环境 |
| `PARTIAL` | 只有部分路径、边界或证据完成 |
| `NOT_TESTED` | 环境、凭据或设备未提供，未执行 |
| `ACCEPTANCE_PENDING_EXTERNAL` | 本地强制链通过，只缺外部环境 |
| `INCOMPLETE` | 存在代码、体验或验收缺口，不能发布完成结论 |
| `PASS` | 所有强制门槛与适用真实集成均通过 |
