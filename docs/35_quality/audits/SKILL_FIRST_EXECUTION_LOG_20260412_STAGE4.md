受众：CODEX
标题：Skill-First 执行留痕（P4-Pre）

# Skill-First 执行留痕（P4-Pre）

日期：2026-04-12  
批次：`#7`  
状态：`COMPLETED`

## 1. 目标
- 建立 capability forge proposal artifact 与审批门控骨架。
- 将 `shadow/canary/full/rollback` 发布状态显式建模并可通过 API 操作。

## 2. 代码变更
- 新增：
  - `host_brain/app/capability_forge.py`
  - `tests/unit/test_capability_forge_service.py`
  - `tests/unit/test_obs_capability_forge_proposals.py`
  - `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_STAGE4_PREP_20260412.md`
  - `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412_STAGE4.md`
- 修改：
  - `host_brain/app/capability_gap_telemetry.py`
  - `host_brain/app/dependencies.py`
  - `host_brain/app/obs_routes_stats.py`

## 3. 执行命令与结果
```powershell
python -m py_compile host_brain/app/capability_forge.py host_brain/app/capability_gap_telemetry.py host_brain/app/obs_routes_stats.py host_brain/app/dependencies.py tests/unit/test_capability_forge_service.py tests/unit/test_obs_capability_forge_proposals.py
```
- 结果：`PASS`

```powershell
pytest tests/unit/test_capability_forge_service.py tests/unit/test_obs_capability_forge_proposals.py tests/unit/test_capability_gap_telemetry.py tests/unit/test_obs_capability_gaps_stats.py -q
```
- 结果：`8 passed in 1.13s`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_skill_dag_planner.py tests/unit/test_skill_family_router.py -q
```
- 结果：`74 passed in 26.28s`

```powershell
pytest tests/integration/test_obs_routes_integration.py -q
```
- 结果：`27 passed in 6.11s`

## 4. 验收结论
- P4 前置已完成：可从缺口候选生成 proposal，带 approval token 门控并进入发布阶段状态机。
- 观测与 API 验证通过，且未引入 dispatch/obs 侧回归。

## 5. 残余风险
- proposal 目前为进程内存态，重启后丢失；正式 P4 需持久化。
- canary/full 目前是控制面状态变更，尚未接入真实流量比例路由执行面。

