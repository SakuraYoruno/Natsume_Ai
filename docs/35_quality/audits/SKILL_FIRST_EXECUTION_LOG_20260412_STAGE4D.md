受众：CODEX
标题：Skill-First 执行留痕（P4-Durability）

# Skill-First 执行留痕（P4-Durability）
日期：2026-04-12  
批次：`C#10`  
状态：`COMPLETED`

## 1. 目标
- 验证 Forge 独立审计表与自动回滚改造是否可编译、可测试、可回归。

## 2. 代码范围
- `host_brain/audit/ledger_constants.py`
- `host_brain/audit/ledger_schema.py`
- `host_brain/audit/sqlite_ledger.py`
- `host_brain/app/capability_forge.py`
- `host_brain/app/dispatch_agent.py`
- `tests/unit/test_capability_forge_service.py`

## 3. 执行命令与结果
```powershell
python -m py_compile host_brain/audit/ledger_constants.py host_brain/audit/ledger_schema.py host_brain/audit/sqlite_ledger.py host_brain/app/capability_forge.py host_brain/app/dispatch_agent.py tests/unit/test_capability_forge_service.py
```
- 结果：`PASS`

```powershell
pytest tests/unit/test_capability_forge_service.py -q
```
- 结果：`4 passed in 0.56s`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py -q
```
- 结果：`39 passed in 27.05s`

```powershell
pytest tests/unit/test_capability_gap_telemetry.py tests/unit/test_obs_capability_gaps_stats.py tests/unit/test_obs_capability_forge_proposals.py -q
```
- 结果：`6 passed in 1.26s`

```powershell
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_skill_dag_planner.py tests/unit/test_skill_family_router.py -q
```
- 结果：`75 passed in 25.88s`

```powershell
pytest tests/integration/test_obs_routes_integration.py -q
```
- 结果：`27 passed in 6.10s`

## 4. 核验结论
- Forge proposal 已具备独立审计表持久化路径，`error_log` 仅用于历史兼容回放。
- canary 失败可经运行时结果回写触发自动回滚闭环。
- 本批次回归未发现新增失败。
