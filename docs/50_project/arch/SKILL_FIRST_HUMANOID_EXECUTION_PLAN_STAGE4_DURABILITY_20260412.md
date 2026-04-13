# Skill-First 类人体架构执行补充方案（P4-Durability）
日期：2026-04-12  
状态：`COMPLETED`

## 1. 本轮目标
- 将 Capability Forge proposal 的持久化主路径从 `error_log` 迁移到独立审计表，降低事件混载和查询歧义。
- 在 Forge 运行时引入可配置的自动回滚触发规则，避免 canary 失败持续扩散。

## 2. 核心实现
- 审计层新增独立表 `capability_forge_proposal_log`：
  - 列定义与 JSON 字段白名单已并入 ledger 常量与 schema。
  - `SQLiteLedger` 新增 proposal 写入与查询方法（按条件分页、最新快照聚合）。
- Forge 服务持久化路径切换：
  - 状态事件优先写入 `capability_forge_proposal_log`。
  - 保留 `error_log` 回放作为历史兼容 fallback，不作为主写入路径。
- 自动回滚规则（canary）：
  - proposal artifact 内新增运行时统计结构与阈值参数。
  - 执行结果回传后累计成功/失败与连续失败计数。
  - 达到连续失败阈值时自动触发 rollback，并记录回滚事件。
- Dispatch 接线增强：
  - 运行时 capability 执行结果会回写 Forge 服务，用于驱动自动回滚判定闭环。

## 3. 影响评估
- 正向影响：
  - proposal 状态追踪从“错误事件复用”升级为“能力审计专表”，可运维性提升。
  - canary 阶段具备自动熔断/回滚能力，发布安全性提升。
- 兼容策略：
  - 旧版本遗留数据仍可通过 `error_log` 回放恢复，避免历史状态丢失。

## 4. 下一步建议
- 将自动回滚指标接入统一观测面板（按 proposal 维度展示失败率、触发次数、最近触发时间）。
- 增加基于时间窗口的失败率回滚策略（补充“连续失败”之外的统计判据）。
