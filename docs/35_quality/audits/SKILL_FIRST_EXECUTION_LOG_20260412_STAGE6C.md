受众：CODEX
标题：Skill-First 执行留痕（P6C-Dispatch Core Split）日期：2026-04-12

# Skill-First 执行留痕（P6C-Dispatch Core Split）日期：2026-04-12
批次：`ACC#21`
状态：`COMPLETED`

## 1. 目标
- 拆分 `dispatch_agent` 的核心执行函数与兼容符号。
- 将 `executive_runtime` 的调用目标切换到新的 `dispatch_core` 符号。

## 2. 代码变更
- `host_brain/app/dispatch_agent.py`
- `host_brain/app/executive_runtime.py`
- `tests/unit/test_executive_runtime_entry.py`
- `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_STAGE6C_DISPATCH_CORE_SPLIT_20260412.md`
- `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412_STAGE6C.md`

## 3. 执行命令与结果
```powershell
pytest tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_executive_runtime_entry.py tests/unit/test_dispatch_agent_entrypoint.py -q
```
- 结果：`4 passed`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_input_routes_dispatch.py tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_executive_runtime_entry.py tests/unit/test_dispatch_agent_entrypoint.py -q
```
- 结果：`78 passed`

## 4. 风险与后续
- 真实执行逻辑仍位于 `dispatch_core`（`dispatch_agent.py`）内部，尚未完全迁入 `executive_runtime` 文件。
- 下一步建议（P6D）：
  - 逐段迁移 `dispatch_core` 逻辑至 `executive_runtime` 原生实现；
  - `dispatch_agent` 最终保留纯兼容转发层，清理历史执行实现。

