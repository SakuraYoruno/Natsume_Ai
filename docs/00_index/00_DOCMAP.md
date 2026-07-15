受众：MASTER
标题：文档地图（唯一入口）

# 文档地图

版本：v6.0
维护日期：2026-07-15
状态：当前导航

本文件是仓库文档的唯一总入口。代码、配置、测试与机器可读验收证据是事实源；文档用于解释当前事实和约束，不用历史任务数量代替架构完成度。

## 事实优先级

1. 生产代码、composition、配置和迁移。
2. 当前架构文档与正式执行合同。
3. 自动化测试和机器可读验收证据。
4. 历史计划、报告与 Git 历史。

若不同层级冲突，必须按上述顺序修正文档；不得用旧报告覆盖当前代码事实。

## 当前状态

- Core Infrastructure 重构已有正式发布证据。
- Universal Governed Planning Runtime 的 GR-00～GR-06 已有机器可读验收证据。
- Full Architecture 仍为 `INCOMPLETE`；全局 service locator 等剩余架构缺口不能被局部 `PASS` 代替。
- `DevTasks.md` 仅保留当前程序注册与历史编号说明，不再承载逐任务流水账。

## 快速入口

- 仓库与运行入口：`../../README.md`
- 当前代码架构：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- 完整目标架构：`../50_project/arch/NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md`
- 当前正式执行合同：`../30_devspec/NATSUME_AI_UNIVERSAL_GOVERNED_PLANNING_EXECUTION_20260714.md`
- Core Infrastructure 历史合同与封板纠错：`../30_devspec/NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md`
- 当前程序注册表：`../30_devspec/DevTasks.md`
- GR-02A 搜索基准：`../45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json`
- GR-06 全路线验收：`../45_testing/test_reports/gr06_universal_governed_runtime_live_acceptance_20260715.json`
- 需求追溯：`../60_trace/requirements_trace.md`

## 目录地图

| 目录 | 定位 | 入口 |
|---|---|---|
| `10_whitepaper` | 宪法、人格与不可变约束 | `../10_whitepaper/index.md` |
| `15_theory` | 理论与层级合同 | `../15_theory/index.md` |
| `20_prd` | 产品需求与验收边界 | `../20_prd/index.md` |
| `30_devspec` | 工程合同、执行入口与机器证据 | `../30_devspec/index.md` |
| `35_quality` | 仍有复用价值的质量规范与基线审计 | `../35_quality/index.md` |
| `40_agents` | Codex 协作约束 | `../40_agents/index.md` |
| `45_testing` | 当前专项验收证据与 Pentest 测试说明 | `../45_testing/index.md` |
| `50_project` | 当前项目入口、架构与指南 | `../50_project/index.md` |
| `50_release` | Core/M48 发布证据、变更日志与回滚 | `../50_release/index.md` |
| `60_trace` | 需求、风险与链路追溯 | `../60_trace/index.md` |

## 文档维护规则

- 新架构结论必须指向生产接线和测试证据。
- 临时调查记录、逐任务重复报告、生成日志和本地实测输出不提交到 `docs/`。
- 长期证据优先保存 JSON、manifest、receipt、seal；同一事实不再同时保留大量 Markdown 复述。
- 已完成任务的完整正文通过 Git 历史追溯；当前入口只保留有效合同和状态。
- 删除或重命名文档时，同步更新本地图、所在目录 `index.md` 与文档 lint 策略。
