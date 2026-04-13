# Skill-First 类人体架构执行补充方案（P6A-Entrypoint 去兼容）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 让 `cortex_orchestrator` 与 `executive_router` 成为真实运行入口，不再委托旧公开入口函数。
- 保留 `top_agent` / `dispatch_agent` 兼容壳，避免线上调用与测试断裂。

## 2. 关键实现
- `cortex_orchestrator` 改造：
  - 从“调用 `top_agent.top_agent_sse_generator`”切换为“直接调用共享运行时实现 `_cortex_orchestrator_sse_generator_impl`”。
- `executive_router` 改造：
  - 从“调用 `dispatch_agent.dispatch_agent_sse_generator`”切换为“调用内部实现 `_executive_router_sse_generator_impl`”。
- `dispatch_agent` 改造：
  - 原主函数重命名为 `_executive_router_sse_generator_impl`（执行实现）。
  - 公开入口 `dispatch_agent_sse_generator` 保留为兼容壳，仅转发到内部实现。

## 3. 影响范围
- 入口所有权明确：
  - Cortex Family 入口：`host_brain.app.cortex_orchestrator`
  - Executive Family 入口：`host_brain.app.executive_router`
- 兼容层仍可用：
  - `host_brain.app.top_agent.top_agent_sse_generator`
  - `host_brain.app.dispatch_agent.dispatch_agent_sse_generator`

## 4. 配套测试改造
- 更新入口单测断言目标：
  - `test_cortex_orchestrator_entry.py` 改为断言调用共享运行时实现。
  - `test_executive_router_entry.py` 改为断言调用 dispatch 内部执行实现。
- 新增兼容壳回归：
  - `test_dispatch_agent_entrypoint.py` 覆盖旧公开入口到内部实现的转发。
- 修复事件噪声导致的不稳定断言：
  - `test_dispatch_agent_tribe_handoff.py` 从“固定 1 条事件”调整为“筛选 CHAT 事件断言”。

## 5. 结论
- 本阶段完成“入口去兼容第一刀”：真实流量主链路由新入口拥有，旧入口退化为兼容壳。
- 下一阶段（P6B）建议：继续下沉 `dispatch_agent` 内部执行实现到 `executive_router` 独立模块，最终删除旧文件中的核心编排逻辑。

