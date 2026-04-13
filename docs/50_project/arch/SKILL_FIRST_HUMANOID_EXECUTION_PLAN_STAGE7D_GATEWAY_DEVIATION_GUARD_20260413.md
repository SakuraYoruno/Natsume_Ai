# Skill-First Humanoid Execution Plan Stage 7D (2026-04-13)

## 目标

- 增加“全局 ReAct 免疫校正”在 Gateway 执行链路的落地点：检测执行偏航并自动纠偏。
- 覆盖“执行成功但方向偏了”的场景，而非仅关注报错失败。

## 设计

1. 偏航检测
- 在 worker 拿到首轮 `pipeline_result` 后执行偏航评估。
- 评估条件：
  - `source=GATEWAY`
  - `decision=APPROVE`
  - 原始请求是执行意图
  - 原始请求关键词与产出文本覆盖率低于阈值

2. 纠偏回路
- 命中偏航后先发送人格化纠偏提示（不生硬中断用户体验）。
- 自动触发一次“重规划重执行”：
  - 将原始请求 + 偏航输出拼装为纠偏指令
  - 调用同一 orchestrator 再执行一次
  - 若覆盖率恢复到阈值以上，替换最终输出

3. 可观测留痕
- 新增事件：
  - `INPUT_DEVIATION_DETECTED`
  - `INPUT_DEVIATION_RECOVERED`
- 完成事件附带：
  - `deviation_reason`
  - `deviation_overlap_ratio`
  - `deviation_retry_applied`
  - `deviation_retry_overlap_ratio`

## 配置

- `HOST_GATEWAY_DEVIATION_GUARD_ENABLED`（默认 `true`）
- `HOST_GATEWAY_DEVIATION_MIN_KEYWORDS`（默认 `3`）
- `HOST_GATEWAY_DEVIATION_MIN_OVERLAP`（默认 `0.18`）
- `HOST_GATEWAY_DEVIATION_NOTICE_ENABLED`（默认 `true`）
- `HOST_GATEWAY_DEVIATION_NOTICE_WAIT_RESULT`（默认 `false`）
- `HOST_GATEWAY_DEVIATION_NOTICE_TIMEOUT_MS`（默认 `2500`）
- `HOST_GATEWAY_DEVIATION_RETRY_ENABLED`（默认 `true`）

## 风险与缓解

- 风险：关键词规则误报偏航。
- 缓解：最小关键词数量 + 覆盖率阈值双门控，且仅重试一次。
- 风险：重试增加时延。
- 缓解：只在偏航命中触发；普通链路零额外开销。
