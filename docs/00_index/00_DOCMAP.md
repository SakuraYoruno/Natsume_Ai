受众：MASTER
标题：文档地图（唯一入口）

# 文档地图

版本：v7.0
维护日期：2026-07-15
状态：`CURRENT_AUTHORITY_MAP`

## 权威顺序

1. 生产代码、生产 composition、migration 与机器状态。
2. 当前代码事实：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`。
3. 稳定目标架构：`../50_project/arch/TARGET_ARCHITECTURE.md`。
4. 当前差距解释：`../50_project/arch/ARCHITECTURE_GAP_MATRIX.md`。
5. 唯一活动执行合同：`../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md`。
6. PRD、DevSpec、Trace。
7. 历史计划与固定 receipt。

历史 `DONE/PASS` 只对其提交和范围成立，不能覆盖当前代码审计。

## 当前状态

- Full Architecture：`INCOMPLETE`。
- Host `chat/skill/delegate/plan`：已接 governed runtime。
- Scheduler/Internal Agent：合同不兼容，当前 `BROKEN`。
- 通用 PlanningSearch：算法内核存在；真实跨领域收益 `UNPROVEN`。
- 长期演化：World/Scene/Identity 等只有局部资产，Evolution Lab、Skill Compiler、Knowledge OS、Identity Migration 未形成完整闭环。

机器可读状态：`../../configs/release/current_architecture_status.json`。

## 快速入口

- 仓库入口：`../../README.md`
- 当前代码事实：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- 目标架构：`../50_project/arch/TARGET_ARCHITECTURE.md`
- 差距矩阵：`../50_project/arch/ARCHITECTURE_GAP_MATRIX.md`
- 正式执行：`../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md`
- 产品需求：`../20_prd/PRD.md`
- 工程契约：`../30_devspec/DevSpec.md`
- 当前追溯：`../60_trace/requirements_trace.md`
- 历史程序：`../_history/programs/2026-07/README.md`

## 目录地图

| 目录 | 定位 |
|---|---|
| `10_whitepaper` | 人格、用户主权和产品宪法 |
| `15_theory` | 理论与设计参考，不承担当前状态 |
| `20_prd` | 稳定产品需求，只写 WHAT |
| `30_devspec` | 稳定工程合同与唯一活动执行文档 |
| `35_quality` | 质量规范与可复用审计 |
| `40_agents` | Codex 协作约束 |
| `45_testing` | 当前专项机器证据 |
| `50_project` | 当前架构、运行和工程指南 |
| `50_release` | 固定提交的发布与回滚证据 |
| `60_trace` | 当前 FR→代码→测试→执行波追溯 |
| `_history` | 历史快照，永不作为 current authority |

## 维护规则

- 需求、目标、当前事实、执行状态和历史证据必须分文件维护。
- 新架构状态必须同时更新机器 JSON 与差距矩阵。
- 临时调查、命令日志和运行输出写入被忽略的 `artifacts/`。
- 删除或移动文档时同步本地图、目录 index 与文档 lint 策略。
