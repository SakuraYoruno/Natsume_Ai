受众：CODEX
标题：Skill-First 执行留痕（P6A-Entrypoint 去兼容）日期：2026-04-12

# Skill-First 执行留痕（P6A-Entrypoint 去兼容）日期：2026-04-12
批次：`ACC#19`
状态：`COMPLETED`

## 1. 目标
- 去除 `cortex_orchestrator`/`executive_router` 对旧公开入口的直接委托。
- 保留兼容壳，确保现网与历史调用不受影响。

## 2. 代码变更
- `host_brain/app/cortex_orchestrator.py`
- `host_brain/app/executive_router.py`
- `host_brain/app/dispatch_agent.py`
- `tests/unit/test_cortex_orchestrator_entry.py`
- `tests/unit/test_executive_router_entry.py`
- `tests/unit/test_dispatch_agent_entrypoint.py`（新增）
- `tests/unit/test_dispatch_agent_tribe_handoff.py`
- `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_STAGE6A_DECOMPAT_ENTRYPOINT_20260412.md`
- `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412_STAGE6A.md`

## 3. 执行命令与结果
```powershell
pytest tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_dispatch_agent_entrypoint.py -q
```
- 结果：`3 passed`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_input_routes_dispatch.py tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_dispatch_agent_entrypoint.py -q
```
- 结果：`77 passed`

## 4. 风险与后续
- 当前仍存在“执行实现位于 `dispatch_agent.py`”的历史包袱，但已通过 `executive_router` 持有主入口。
- 下一步建议（P6B）：
  - 把 `_executive_router_sse_generator_impl` 正式迁移到 `executive_router` 模块或 `executive_runtime` 新模块。
  - 逐步削减 `dispatch_agent.py` 为纯兼容转发层，最终可移除。

