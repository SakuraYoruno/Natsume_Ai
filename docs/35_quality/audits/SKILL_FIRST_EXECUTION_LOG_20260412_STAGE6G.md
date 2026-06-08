受众：CODEX
标题：Skill-First 执行留痕（P6G-Chat Route Runtime Migration）日期：2026-04-12

# Skill-First 执行留痕（P6G-Chat Route Runtime Migration）日期：2026-04-12
批次：`ACC#25`
状态：`COMPLETED`

## 1. 目标
- 将 chat 主分支迁移到 executive runtime，保持流式输出行为一致。

## 2. 代码变更
- `host_brain/app/executive_runtime.py`
- `host_brain/app/dispatch_agent.py`
- `tests/unit/test_executive_runtime_helpers.py`
- `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_STAGE6G_CHAT_ROUTE_RUNTIME_MIGRATION_20260412.md`
- `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412_STAGE6G.md`

## 3. 执行命令与结果
```powershell
pytest tests/unit/test_executive_runtime_helpers.py tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_executive_runtime_entry.py tests/unit/test_dispatch_agent_entrypoint.py tests/unit/test_dispatch_agent_core_entrypoint.py -q
```
- 结果：`11 passed`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_input_routes_dispatch.py tests/unit/test_executive_runtime_helpers.py tests/unit/test_cortex_orchestrator_entry.py tests/unit/test_executive_router_entry.py tests/unit/test_executive_runtime_entry.py tests/unit/test_dispatch_agent_entrypoint.py tests/unit/test_dispatch_agent_core_entrypoint.py -q
```
- 结果：`85 passed`

## 4. 风险与后续
- `delegate` 与 `skill` 分支仍在 legacy core。
- 下一步（P6H）迁移这两条分支，完成 runtime 主流程闭合。

