# Skill-First 执行留痕（P4-Runtime）

日期：2026-04-12  
批次：`#8`  
状态：`COMPLETED`

## 1. 目标
- 为 Forge proposal 增加持久化回放能力。
- 将 canary/full 运行时流量命中接入 dispatch 主链路。

## 2. 代码变更
- 主要修改：
  - `host_brain/app/capability_forge.py`
  - `host_brain/app/dispatch_agent.py`
  - `host_brain/app/obs_routes_stats.py`
  - `tests/unit/test_capability_forge_service.py`
  - `tests/unit/test_dispatch_agent_helpers.py`
  - `tests/unit/test_obs_capability_forge_proposals.py`
- 新增文档：
  - `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_STAGE4_RUNTIME_20260412.md`
  - `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412_STAGE4B.md`

## 3. 执行命令与结果
```powershell
python -m py_compile host_brain/app/capability_forge.py host_brain/app/dispatch_agent.py host_brain/app/obs_routes_stats.py tests/unit/test_capability_forge_service.py tests/unit/test_obs_capability_forge_proposals.py tests/unit/test_dispatch_agent_helpers.py
```
- 结果：`PASS`

```powershell
pytest tests/unit/test_capability_forge_service.py tests/unit/test_obs_capability_forge_proposals.py tests/unit/test_dispatch_agent_helpers.py -q
```
- 结果：`43 passed in 25.26s`

```powershell
pytest tests/unit/test_capability_gap_telemetry.py tests/unit/test_obs_capability_gaps_stats.py tests/unit/test_capability_forge_service.py tests/unit/test_obs_capability_forge_proposals.py -q
```
- 结果：`9 passed in 1.45s`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_skill_dag_planner.py tests/unit/test_skill_family_router.py -q
```
- 结果：`75 passed in 27.02s`

```powershell
pytest tests/integration/test_obs_routes_integration.py -q
```
- 结果：`27 passed in 6.17s`

## 4. 验收结论
- Forge proposal 已具备持久化回放，服务重启后可恢复。
- canary/full 已接入 dispatch 运行时覆盖决策，不再只停留在控制面状态。
- 回归测试通过，未发现现有主链路回归。

## 5. 风险与后续
- 当前 proposal 持久化基于 `error_log` 事件承载，建议后续迁移到独立表。
- canary 运行时命中统计尚未入统一指标面板，建议下一步补 observability 指标。

