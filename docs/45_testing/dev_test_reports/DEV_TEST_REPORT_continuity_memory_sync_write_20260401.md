受众：CODEX
标题：开发实测报告 — 连续性记忆同步写入修复

# DEV_TEST_REPORT: continuity_memory_sync_write — 2026-04-01

## 1. 修复目标

**问题**：用户反馈"每次问夏目蓝刚才说了什么，她回复说不知道上次说的是什么"。

**根因分析**：`INPUT_COMPLETED` 事件写入 `voice_event_log` 表的操作在
`_async_post_process()` 中异步执行（`asyncio.create_task()`），与 COMMIT SSE
发送是 fire-and-forget 关系。当用户快速发送下一轮对话时，异步写入可能尚未完成，
导致 `_build_recent_session_context_block()` 查询历史时拿不到上一轮的记录。

## 2. 修改内容

### 2.1 `host_brain/app/top_agent.py`

| 位置 | 改动 | 说明 |
|------|------|------|
| 快速路径（recent_turn_direct）COMMIT 前 | **新增** 同步 `log_voice_event(INPUT_COMPLETED)` | 确保快速路径也能及时写入历史 |
| 正常 LLM 路径 COMMIT 前（原 §10） | **新增** 同步 `log_voice_event(INPUT_COMPLETED)` | 确保 LLM 路径下一轮对话可查历史 |
| `_async_post_process()` 内 | **移除** 原 `log_voice_event(INPUT_COMPLETED)` 调用 | 避免重复写入，改为 NOTE 注释 |

**关键设计决策**：
- `stage` 字段从 `"top_agent.post_process"` → `"top_agent.pre_commit"` 以标识新的写入时机
- 写入操作包裹在 `try/except` 中，失败不影响 COMMIT 发送（降级保护）
- SQLite 单次 INSERT 通常 <5ms，对 SSE 延迟影响可忽略

### 2.2 `tests/unit/test_top_agent_post_process.py`

| 改动 | 说明 |
|------|------|
| 更新 `test_async_post_process_writes_episodic_memory` 断言 | 验证 `_async_post_process` **不再**写入 `INPUT_COMPLETED`（因为已移至主流程） |

## 3. 影响的出口路径

| 出口路径 | 修改前 | 修改后 |
|----------|--------|--------|
| **recent_turn_direct** (快速路径) | COMMIT → async INPUT_COMPLETED | sync INPUT_COMPLETED → COMMIT → async(其他) |
| **chat_direct** (正常 LLM) | COMMIT → async INPUT_COMPLETED | sync INPUT_COMPLETED → COMMIT → async(其他) |
| **delegate** (子 Agent) | COMMIT → async INPUT_COMPLETED | sync INPUT_COMPLETED → COMMIT → async(其他) |

## 4. 测试结果

```
14 passed in 0.55s

tests/unit/test_top_agent_sse.py (6/6 PASSED)
  - test_rejects_blank_message_at_airlock
  - test_rejects_when_governor_blocks
  - test_recent_turn_query_is_answered_from_continuity_without_llm
  - test_success_path_emits_commit_from_top_agent
  - test_delegate_path_passes_gateway_into_subagent_context
  - test_delegate_result_is_rendered_without_internal_react_marker

tests/unit/test_top_agent_post_process.py (2/2 PASSED)
  - test_async_post_process_writes_episodic_memory (断言已更新)
  - test_async_post_process_swallow_persistence_failure

tests/unit/test_top_agent_prompt.py (6/6 PASSED)
  - test_build_top_agent_prompt_includes_runtime_payload_and_capabilities
  - test_build_recent_session_context_block_formats_previous_turns
  - test_build_recent_session_context_block_filters_japanese_and_corrupted_rows_for_zh
  - test_build_recent_session_context_block_uses_person_id_across_sessions
  - test_build_recent_session_context_block_adds_older_history_summary
  - test_build_top_agent_prompt_appends_recent_session_context
```

## 5. Lint 检查

`top_agent.py`: **0 errors, 0 warnings**

## 6. 预期效果

修复后，用户发送第 N 轮对话时，`voice_event_log` 表中已包含第 1~(N-1) 轮的
`INPUT_COMPLETED` 记录，`_build_recent_session_context_block()` 可正常查询并
注入到 prompt 中，夏目蓝将能正确回忆之前的对话内容。

## 7. 真机验证建议

1. 启动 Host 端服务
2. 连续对话两轮以上
3. 第二轮问"我刚才说了什么"或"你还记得我之前说的吗"
4. 验证夏目蓝能正确复述第一轮的内容

## 8. 回滚方案

若出现问题，将 `_async_post_process()` 中的 NOTE 注释替换回原来的
`log_voice_event(INPUT_COMPLETED)` 调用，并删除两个出口路径中的同步写入块即可。
