# Skill-First 执行留痕（P6F-Direct Route Handler Migration）日期：2026-04-12
批次：`ACC#24`
状态：`COMPLETED`

## 1. 目标
- 将 `recent_turn` 与 `task_progress` 直达分支从 dispatch legacy core 迁入 executive runtime。
- 保持线上行为一致并补齐测试覆盖。

## 2. 代码变更
- `host_brain/app/executive_runtime.py`
- `host_brain/app/dispatch_agent.py`
- `tests/unit/test_executive_runtime_helpers.py`
- `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_STAGE6F_DIRECT_ROUTE_HANDLER_MIGRATION_20260412.md`
- `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412_STAGE6F.md`

## 3. 执行命令与结果
```powershell
pytest tests/unit/test_executive_runtime_helpers.py tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_executive_runtime_entry.py tests/unit/test_dispatch_agent_entrypoint.py tests/unit/test_dispatch_agent_core_entrypoint.py -q
```
- 结果：`10 passed`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_input_routes_dispatch.py tests/unit/test_executive_runtime_helpers.py tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_executive_runtime_entry.py tests/unit/test_dispatch_agent_entrypoint.py tests/unit/test_dispatch_agent_core_entrypoint.py -q
```
- 结果：`84 passed`

## 4. 风险与后续
- 仍有 `chat/delegate/skill` 三条主分支留在 legacy core。
- 下一步（P6G）迁移上述分支后，可进入 legacy core 下线阶段。

