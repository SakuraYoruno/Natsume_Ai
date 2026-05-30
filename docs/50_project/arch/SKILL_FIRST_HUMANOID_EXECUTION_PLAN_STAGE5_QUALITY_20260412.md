受众：PROJECT
标题：Skill-First 类人体架构执行补充方案（P5-Quality）

# Skill-First 类人体架构执行补充方案（P5-Quality）
日期：2026-04-12  
状态：`COMPLETED`

## 1. 本轮目标
- 将 Forge 自调参从“仅失败率驱动”升级为“统一健康评分驱动”。
- 输出 proposal 级质量快照，支持前端与运维直接观察收敛状态。

## 2. 健康评分模型
- 评分输入（窗口级）：
  - 成功率：`window_success_rate`
  - 时延：`window_p95_latency_ms`（结合 `latency_guard_ms`）
  - 稳定性：失败连续性与窗口失败率组合
- 评分输出：
  - `health_score`（0~100）
  - `stability_score`
  - `latency_ema_ms`
- 权重可配置（adaptive）：
  - `weight_success_rate`（默认 0.55）
  - `weight_latency`（默认 0.25）
  - `weight_stability`（默认 0.20）

## 3. 调参策略升级
- `SCALE_UP` 条件（canary）：
  - `health_score` 高于目标健康分带宽；
  - 窗口失败率在低区间；
  - 窗口 P95 时延未越过 guard。
- `SCALE_DOWN` 条件（canary）：
  - `health_score` 偏低，或窗口失败率偏高，或时延越过 guard。
- `HOLD`：
  - 目标区间内不调参。

## 4. 可观测增强
- 新增接口：
  - `GET /obs/capability-gaps/forge-runtime/quality`
- 输出：
  - 聚合：`tracked_proposals / unhealthy_proposals / critical_proposals / avg_health_score / avg_window_failure_rate / avg_window_p95_latency_ms`
  - 明细：proposal 级 `health_score / window_failure_rate / window_p95_latency_ms / last_tune_action` 等。

## 5. 结果
- Forge canary 策略从单一阈值演进为多指标评分闭环，具备更稳健的自动收敛能力。
