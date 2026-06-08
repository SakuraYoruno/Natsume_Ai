受众：CODEX
标题：Skill-First 重构执行留痕（2026-04-12）

# Skill-First 重构执行留痕（2026-04-12）
## 0. 留痕规则
- 记录每批次真实命令、变更文件、测试结果。
- 记录失败和修复，不做“只报成功”。
- 记录下一步与残余风险，避免断点丢失。

## 1. 批次 #1（文档固化）
- 状态：`COMPLETED`
- 动作：创建完整方案文档与执行留痕文档。
- 文件：
  - `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_20260412.md`
  - `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412.md`

## 2. 批次 #2（P1 落地）
- 状态：`COMPLETED`
- 目标：Skill-First 路由契约、SkillMeta 扩展、基础兼容回归。
- 已执行（命令证据）：
```powershell
python -m py_compile host_brain/app/dispatch_agent.py host_brain/skills/registry.py host_brain/app/model_router.py host_brain/app/skill_family_router.py host_brain/app/model_capability_registry.py
pytest tests/unit/test_permission_guard.py tests/unit/test_skill_family_router.py tests/unit/test_model_router_runtime.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_dispatch_agent_helpers.py -q
pytest tests/unit/test_input_routes_worker.py tests/unit/test_chat_handler_top_agent.py tests/unit/test_obs_routes_trace_status.py tests/integration/test_obs_routes_integration.py -k trace -q
pytest tests/integration/test_chat_agent_soul_pipeline.py -k "host_input_gateway_route_retrieval_init_failure_still_delivers_soul_reply" -q
```
- 结果：
  - 相关回归通过（`94 passed` / `17 passed` / `1 passed`）。

## 3. 批次 #3（P2 DAG 执行语义）
- 状态：`COMPLETED`
- 目标：Skill DAG 正式化、批次执行、依赖守卫、节点重试、测试留痕。

### 3.1 代码变更
- 新增：
  - `host_brain/app/skill_dag_planner.py`
  - `tests/unit/test_skill_dag_planner.py`
- 改造：
  - `host_brain/app/dispatch_agent.py`
    - `_maybe_build_multi_step_plan()` 输出 `execution_batches/parallel_window/dag`
    - 新增 `_execute_multi_step_plan_v2()`（DAG 批次执行）
    - 旧 `_execute_multi_step_plan()` 保留并转发到 v2
  - `tests/unit/test_dispatch_agent_skill_routing.py`
    - 新增“节点重试成功”与“依赖失败跳过”用例
  - `tests/unit/test_dispatch_agent_helpers.py`
    - 适配默认重试带来的调用次数变化

### 3.2 执行命令与结果
```powershell
python -m py_compile host_brain/app/skill_dag_planner.py host_brain/app/dispatch_agent.py tests/unit/test_skill_dag_planner.py tests/unit/test_dispatch_agent_skill_routing.py
pytest tests/unit/test_skill_dag_planner.py tests/unit/test_dispatch_agent_skill_routing.py -q
pytest tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_skill_dag_planner.py tests/unit/test_skill_family_router.py tests/unit/test_model_router_runtime.py tests/unit/test_permission_guard.py -q
pytest tests/unit/test_input_routes_worker.py tests/unit/test_obs_routes_trace_status.py tests/integration/test_obs_routes_integration.py -k trace -q
pytest tests/integration/test_chat_agent_soul_pipeline.py -k "host_input_gateway_route_retrieval_init_failure_still_delivers_soul_reply" -q
```
- 结果：
  - `32 passed`
  - `99 passed`
  - `17 passed, 26 deselected`
  - `1 passed, 30 deselected`

### 3.3 中途失败与修复
- 失败：
  - `test_execute_multi_step_plan_parallel_partial_failure_with_recovery` 初次失败。
  - 原因：P2 引入默认重试后，`delegate` 节点调用次数增加，原断言过于严格。
- 修复：
  - 调整断言为“包含关键调用且末次为 repair_agent”。
  - 回归后全套通过。

## 4. 当前结论
- P1 完成，P2 核心执行链路完成并通过回归。
- 下一步进入 P3：`Capability Gap Detector + Runtime Synthesizer`。

## 5. 残余风险
- 默认重试策略仍较通用，后续需要按 `skill_family` 与风险等级细化。
- 旧执行函数仍保留（兼容目的），后续可在稳定窗口后清理旧实现壳层。

## 6. 批次 #4（P3 第一段：能力缺口 + 临时能力合成）
- 状态：`COMPLETED`
- 目标：把“chat 误落动作请求”纳入能力缺口闭环，自动生成临时能力并改走执行链路。

### 6.1 代码变更
- 新增：
  - `host_brain/app/capability_gap_detector.py`
  - `host_brain/app/runtime_capability_synthesizer.py`
  - `tests/unit/test_capability_gap_detector.py`
  - `tests/unit/test_runtime_capability_synthesizer.py`
- 改造：
  - `host_brain/app/dependencies.py`
    - 新增 `get_runtime_capability_registry()`
  - `host_brain/app/dispatch_agent.py`
    - 新增 `_maybe_synthesize_runtime_capability()`
    - 路由链路接入 `gap detect -> runtime synth -> delegate`
    - 新增 `CAPABILITY_SYNTHESIZED` 事件
    - trace/handoff/task meta 写入 `runtime_capability_id/capability_gap_reason`
  - `host_brain/app/skill_family_router.py`
    - 新增 `route_type="gap"` 决策分支
  - `tests/unit/test_dispatch_agent_helpers.py`
    - 新增 runtime capability 合成相关单测
  - `tests/unit/test_skill_family_router.py`
    - 新增 gap 路由决策单测

### 6.2 执行命令与结果
```powershell
python -m py_compile host_brain/app/capability_gap_detector.py host_brain/app/runtime_capability_synthesizer.py host_brain/app/dispatch_agent.py host_brain/app/dependencies.py host_brain/app/skill_family_router.py tests/unit/test_capability_gap_detector.py tests/unit/test_runtime_capability_synthesizer.py tests/unit/test_dispatch_agent_helpers.py tests/unit/test_skill_family_router.py
pytest tests/unit/test_capability_gap_detector.py tests/unit/test_runtime_capability_synthesizer.py tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_skill_family_router.py tests/unit/test_skill_dag_planner.py -q
pytest tests/unit/test_model_router_runtime.py tests/unit/test_permission_guard.py tests/unit/test_input_routes_worker.py tests/unit/test_obs_routes_trace_status.py tests/integration/test_obs_routes_integration.py -k "not slow" -q
pytest tests/integration/test_chat_agent_soul_pipeline.py -k "host_input_gateway_route_retrieval_init_failure_still_delivers_soul_reply" -q
```
- 结果：
  - `80 passed`
  - `72 passed`
  - `1 passed, 30 deselected`

### 6.3 结论
- P3 已进入可运行状态（第一段完成）：系统可识别能力缺口并自动切换到临时执行能力。
- 下一步：进入 P3 第二段（临时能力执行反馈学习 + 缺口统计面板 + 提升候选转 P4 Forge）。

## 7. 批次 #5（P3 第二段：执行反馈聚合 + 可观测接口）
- 状态：`COMPLETED`
- 目标：把“能力缺口闭环”从仅触发升级为“可统计、可追踪、可运营”。

### 7.1 代码变更
- 新增：
  - `host_brain/app/capability_gap_telemetry.py`
  - `tests/unit/test_capability_gap_telemetry.py`
  - `tests/unit/test_obs_capability_gaps_stats.py`
- 改造：
  - `host_brain/app/dependencies.py`
    - 新增 `get_capability_gap_telemetry()`
  - `host_brain/app/dispatch_agent.py`
    - 缺口触发后记录 `record_detected`
    - 临时能力执行完成后记录 `record_execution`（plan/delegate/skill）
  - `host_brain/app/obs_routes_stats.py`
    - 新增 `GET /obs/capability-gaps/stats`
  - `tests/unit/test_dispatch_agent_helpers.py`
    - 增加 telemetry 断言（detected/executed）

### 7.2 执行命令与结果
```powershell
python -m py_compile host_brain/app/capability_gap_telemetry.py host_brain/app/obs_routes_stats.py host_brain/app/dispatch_agent.py host_brain/app/dependencies.py tests/unit/test_capability_gap_telemetry.py tests/unit/test_obs_capability_gaps_stats.py tests/unit/test_dispatch_agent_helpers.py
pytest tests/unit/test_capability_gap_detector.py tests/unit/test_runtime_capability_synthesizer.py tests/unit/test_capability_gap_telemetry.py tests/unit/test_obs_capability_gaps_stats.py tests/unit/test_dispatch_agent_helpers.py tests/unit/test_dispatch_agent_skill_routing.py tests/unit/test_skill_family_router.py tests/unit/test_skill_dag_planner.py -q
pytest tests/unit/test_model_router_runtime.py tests/unit/test_permission_guard.py tests/unit/test_input_routes_worker.py tests/unit/test_obs_routes_trace_status.py tests/integration/test_obs_routes_integration.py -k "not slow" -q
pytest tests/integration/test_chat_agent_soul_pipeline.py -k "host_input_gateway_route_retrieval_init_failure_still_delivers_soul_reply" -q
```
- 结果：
  - `83 passed`
  - `72 passed`
  - `1 passed, 30 deselected`

### 7.3 结论
- P3 第二段完成：能力缺口触发与执行结果可在运行时统一观测。
- P4 前置输入已具备：可基于缺口热度与成功率筛选“转正锻造”候选能力。
