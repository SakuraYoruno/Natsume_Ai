受众：PROJECT
标题：Skill-First 类人体架构执行补充方案（P6G-Chat Route Runtime Migration）日期：2026-04-12

# Skill-First 类人体架构执行补充方案（P6G-Chat Route Runtime Migration）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 `chat` 主分支从 `dispatch_legacy_core` 迁入 `executive_runtime`，保持流式输出能力与行为一致。

## 2. 关键实现
- `executive_runtime` 新增：
  - `stream_chat_route`
  - 职责：记录 handoff，并透传 `chat_agent_sse_generator` 流式事件。
- `dispatch_legacy_core` 改造：
  - 删除 chat 分支内联实现，改为调用 runtime 的 `stream_chat_route`。

## 3. 行为保障
- 仍为逐条 `yield` 事件，无缓冲、无批处理。
- handoff `extra` 继续携带 `skill_family` 与 runtime capability 信息。

## 4. 配套测试
- 扩展 `tests/unit/test_executive_runtime_helpers.py`：
  - `test_stream_chat_route_records_handoff_and_streams_events`
- 覆盖点：
  - handoff 记录参数；
  - chat 事件透传。

## 5. 下一步（P6H）
- 迁移 `delegate` 与 `skill` 两条重执行分支到 runtime。
- 完成后 `dispatch_legacy_core` 可进入收尾下线阶段。

