受众：PROJECT
标题：Skill-First 类人体架构执行补充方案（P5-AutoTune）

# Skill-First 类人体架构执行补充方案（P5-AutoTune）
日期：2026-04-12  
状态：`COMPLETED`

## 1. 本轮目标
- 为 Capability Forge canary 发布链路补齐“策略自优化”能力。
- 在不引入额外后台任务的前提下，实现运行时闭环调参与可观测输出。

## 2. 实现范围
- `CapabilityForgeService` 新增 canary 自调参逻辑：
  - 输入：窗口样本量、窗口失败率、目标失败率、冷却样本数、当前 canary 流量与回滚阈值。
  - 输出：`SCALE_UP / SCALE_DOWN / HOLD` 三态调参决策。
- 调参动作：
  - `SCALE_UP`：在低失败率区间提高 canary 流量，并适度放宽回滚失败率阈值。
  - `SCALE_DOWN`：在高失败率区间降低 canary 流量，并收紧回滚失败率阈值。
  - `HOLD`：目标区间内维持当前策略。
- 运行时状态扩展：
  - `runtime_stats` 增加 `adaptive_tune_count / last_tune_action / last_tune_reason / last_tune_ts_ms`。
  - `release_plan` 增加 `adaptive` 配置段（可开关、样本阈值、步长、边界、目标失败率）。
- 可观测增强：
  - 新增接口 `GET /obs/capability-gaps/forge-runtime/tuning`，输出 proposal 级调参快照和聚合摘要。

## 3. 风险控制
- 自调参仅在 `CANARY` 阶段生效，`SHADOW/FULL/ROLLED_BACK` 不调。
- 保持原自动回滚判据：连续失败与窗口失败率两条安全线仍可触发回滚。
- 冷却样本限制避免高频抖动。

## 4. 验收结果
- 单测覆盖：
  - 低失败率触发 `SCALE_UP`；
  - 高失败率触发 `SCALE_DOWN`；
  - 观测接口返回调参摘要与条目。
- 回归测试通过，未引入现有链路回归。
