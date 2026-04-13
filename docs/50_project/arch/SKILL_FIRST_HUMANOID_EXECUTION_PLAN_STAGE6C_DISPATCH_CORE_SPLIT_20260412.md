# Skill-First 类人体架构执行补充方案（P6C-Dispatch Core Split）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 `dispatch_agent` 中“核心执行函数”和“兼容符号”拆分，避免历史符号继续承载真实执行职责。
- 让 `executive_runtime` 显式依赖 `dispatch_core`，为下一步完整迁移到 runtime 独立实现做准备。

## 2. 关键实现
- `dispatch_agent`：
  - 原核心函数重命名为：
    - `_dispatch_agent_executive_core_sse_generator_impl`
  - 兼容符号保留为：
    - `_executive_router_sse_generator_impl`（薄壳，仅转发到 `executive_runtime`）
- `executive_runtime`：
  - 调用目标由旧兼容符号切换到新的 `dispatch_core`：
    - `_dispatch_agent_executive_core_sse_generator_impl`

## 3. 结构效果
- 当前主链路：
  - `cortex_orchestrator -> executive_router -> executive_runtime -> dispatch_core`
- 兼容链路：
  - `dispatch_agent.dispatch_agent_sse_generator -> executive_runtime`
  - `dispatch_agent._executive_router_sse_generator_impl -> executive_runtime`

## 4. 结果
- 历史符号与核心执行职责完成解耦。
- 后续 P6D 可以直接把 `dispatch_core` 的实现逐段搬迁到 `executive_runtime`，且不影响现有调用入口。

