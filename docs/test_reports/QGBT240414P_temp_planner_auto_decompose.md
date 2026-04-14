# QGBT240414P - 临时规划子Agent（复杂任务自动拆解）落地留痕

## 目标
实现“复杂任务自动规划拆解”的临时能力：请求进入后，自动识别复杂度，切换到 `plan` 路径，生成一次性任务 DAG 并执行，而不是仅依赖手工 `然后/并且` 拆句。

## 本次改动
1. `executive_runtime_core` 新增复杂任务自动规划判定：
   - `_should_force_complex_plan(...)`
   - 对 `chat/skill/delegate` 请求进行复杂度判定，命中后强制切到 `plan` 路由（`auto_plan:complex_task`）。
2. 计划生成新增“临时规划子Agent”来源：
   - `_build_planning_agent_plan_steps(...)`
   - 使用 `PlanningAgent` 生成一次性步骤，再映射为 `Skill DAG` 节点（包含依赖、风险、family、能力标签）。
3. `plan` 生成优先级：
   - `intent_draft.planned_steps/steps`（显式）  
   - `PlanningAgent` 临时规划（新增）  
   - 文本拆句规则兜底（原有）
4. 配置开关（`.env.example`）：
   - `HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_ENABLED`
   - `HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_MIN_CHARS`
   - `HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_PARALLEL_ENABLED`

## 验证
1. `pytest tests/unit/test_executive_runtime_core_complex_planning.py tests/unit/test_executive_runtime_core_auto_forge.py tests/unit/test_capability_forge_service.py -q`
   - 结果：`12 passed`
2. `pytest tests/unit/test_runtime_capability_synthesizer.py tests/unit/test_executive_runtime_entry.py tests/unit/test_executive_runtime_helpers.py tests/unit/test_executive_runtime_core_auto_forge.py tests/unit/test_executive_runtime_core_complex_planning.py tests/unit/test_capability_forge_service.py -q`
   - 结果：`22 passed`

## 新增测试点
- 无“然后/并且”连接词时，仍可由 PlanningAgent 生成多步计划并落入 DAG。
- `chat` 且非动作意图时，不强制进入 plan。
- `chat` 且动作缺口命中 + 复杂度命中时，强制进入 plan。

## 结论
“自动规划新的子智能体”已覆盖你说的临时场景：  
现在可以按复杂度自动拆解成一次性子任务计划执行，不依赖长期子Agent沉淀。
