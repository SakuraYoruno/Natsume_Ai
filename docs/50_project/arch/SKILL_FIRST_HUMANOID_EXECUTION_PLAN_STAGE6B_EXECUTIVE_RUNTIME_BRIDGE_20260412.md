# Skill-First 类人体架构执行补充方案（P6B-Executive Runtime Bridge）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 Executive 执行归属从 `executive_router -> dispatch_agent内部` 改为 `executive_router -> executive_runtime`。
- 将 `dispatch_agent` 公开入口进一步降级为兼容壳，不再直接绑定内部执行函数。

## 2. 关键实现
- 新增执行层模块：
  - `host_brain/app/executive_runtime.py`
  - 暴露 `executive_runtime_sse_generator` 作为 Executive 执行层统一入口。
- 入口改造：
  - `executive_router` 改为调用 `executive_runtime_sse_generator`。
  - `dispatch_agent.dispatch_agent_sse_generator` 改为调用 `executive_runtime_sse_generator`。
- 当前桥接关系：
  - `executive_runtime` 仍桥接 `dispatch_agent._executive_router_sse_generator_impl`（本阶段保持兼容稳定）。

## 3. 测试改造
- 调整：
  - `tests/unit/test_executive_router_entry.py`
  - `tests/unit/test_dispatch_agent_entrypoint.py`
- 新增：
  - `tests/unit/test_executive_runtime_entry.py`（验证 runtime 层到 legacy 执行实现的桥接）。

## 4. 结果
- Executive 入口层与执行层已完成解耦第一步：
  - 路由入口只依赖 `executive_runtime`，不再直接依赖 `dispatch_agent` 内部符号。
- 后续 P6C 可以在不改入口调用方的前提下，将实际执行实现从 `dispatch_agent` 完整迁移到 `executive_runtime` 独立实现。

