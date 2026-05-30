受众：PROJECT
标题：Skill-First 类人体架构执行补充方案（P6D-Executive Ownership Inversion）日期：2026-04-12

# Skill-First 类人体架构执行补充方案（P6D-Executive Ownership Inversion）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 Executive 执行“控制权”从 `dispatch_agent` 进一步反转到 `executive_runtime`。
- 历史 `dispatch_core` 符号仅保留兼容转发，不再直接承载主实现入口语义。

## 2. 关键实现
- `dispatch_agent`：
  - 真实旧实现重命名为：
    - `_dispatch_agent_executive_core_legacy_impl`
  - 兼容入口调整为：
    - `_dispatch_agent_executive_core_sse_generator_impl` → 转发到 `executive_runtime._executive_runtime_core_sse_generator_impl`
    - `_executive_router_sse_generator_impl` → 转发到 `executive_runtime.executive_runtime_sse_generator`
- `executive_runtime`：
  - 新增核心函数：
    - `_executive_runtime_core_sse_generator_impl`
  - `executive_runtime_sse_generator` 改为先进入本模块核心，再桥接 legacy 执行实现。

## 3. 链路状态
- 主链路：
  - `cortex_orchestrator -> executive_router -> executive_runtime_sse_generator -> _executive_runtime_core_sse_generator_impl -> dispatch_legacy_core`
- 兼容链路：
  - `dispatch_agent.dispatch_agent_sse_generator -> executive_runtime_sse_generator`
  - `dispatch_agent._dispatch_agent_executive_core_sse_generator_impl -> executive_runtime_core`

## 4. 结果
- Executive 运行时已成为“第一归属点”，旧实现被下沉为 legacy 组件。
- 后续 P6E 可在 `executive_runtime` 内逐段替换 legacy 调用，最终移除 `dispatch_agent` 中的 legacy 核心实现。

