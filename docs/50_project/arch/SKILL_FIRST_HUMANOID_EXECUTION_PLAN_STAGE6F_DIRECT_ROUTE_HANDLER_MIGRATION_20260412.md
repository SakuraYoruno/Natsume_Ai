受众：PROJECT
标题：Skill-First 类人体架构执行补充方案（P6F-Direct Route Handler Migration）日期：2026-04-12

# Skill-First 类人体架构执行补充方案（P6F-Direct Route Handler Migration）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 `dispatch_legacy_core` 中的两个直达分支迁入 `executive_runtime`：
  - `recent_turn`
  - `task_progress`
- 让 legacy core 从“直接实现”转为“调用 runtime handler”。

## 2. 关键实现
- `executive_runtime` 新增处理器：
  - `try_handle_recent_turn_direct_route`
  - `try_handle_task_progress_direct_route`
- `dispatch_legacy_core` 改造：
  - 删除原有 `recent_turn` 分支内部实现，改为调用 runtime handler。
  - 删除原有 `task_progress` 分支内部实现，改为调用 runtime handler。
- 行为保持：
  - COMMIT 事件结构不变；
  - post_process 异步留痕不变；
  - handoff/audit 路径与 reason_code 保持一致。

## 3. 架构收益
- `executive_runtime` 从“桥接+helper”升级为“承接主流程分支”的执行层。
- `dispatch_legacy_core` 再次瘦身，主流程迁移进入可持续拆分阶段。

## 4. 配套测试
- 扩展 `tests/unit/test_executive_runtime_helpers.py`，新增：
  - `test_try_handle_recent_turn_direct_route_returns_commit_event`
  - `test_try_handle_task_progress_direct_route_returns_commit_event`
- 用例覆盖：
  - 事件产出；
  - handoff 调用；
  - post_process 调度触发。

## 5. 下一步（P6G）
- 继续迁移 `chat/delegate/skill` 三条主执行分支到 `executive_runtime`。
- 完成后可下线 `dispatch_agent._dispatch_agent_executive_core_legacy_impl`。

