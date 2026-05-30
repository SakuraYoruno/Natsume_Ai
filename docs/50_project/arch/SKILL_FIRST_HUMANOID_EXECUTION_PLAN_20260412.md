受众：PROJECT
标题：Skill-First 类人体架构完整执行方案（2026-04-12）

# Skill-First 类人体架构完整执行方案（2026-04-12）
## 1. 目标定义（最终态）
- 业务主路由只面向 `Skill Family`，不直接路由裸 `subagent`。
- 自动规划与并行执行由 `Executive Cortex` 统一编排，输出可观测 `Skill DAG`。
- 模型选择由 `Model Router` 统一负责，执行子体只声明能力需求，不自行选型。
- Gemini 收敛为单 key；模型可多候选降级，key 不再做多路轮询。
- 支持能力缺口闭环：`Gap Detector -> Runtime Synthesizer -> Capability Forge -> Promotion Pipeline -> Rollback Guard`。
- 全链路审计可追踪：`request -> intent -> skill_family -> planner_node -> executor -> model -> key_source -> rc -> latency`。

## 2. 分层架构（目标形态）
1. Gateway（神经通道）
- 仅做消息收发、会话映射、媒体处理、重试。
- 不承载业务决策。
2. Intent Cortex（皮层理解）
- 意图识别、约束抽取、风险分级、模态判定。
3. Executive Cortex（前额叶）
- DAG 分解、拓扑编排、并行窗口调度、失败恢复。
4. Skill Family（器官能力层）
- social/code/translation/system/browser/desktop/memory/scheduler 等能力家族。
5. Executor（执行子体）
- tool/subagent/workflow 作为 skill 后端。
6. Governor/Immune（免疫层）
- 风险门控、审批令牌、审计、回滚。

## 3. 关键模块
- `Model Capability Registry`：模型能力标签、稳定性档、成本档。
- `Model Router`：按任务类型、风险、预算、上下文选择模型与单 key 来源。
- `Capability Gap Loop`：能力缺口检测、临时能力、长期能力锻造、灰度、回滚。

## 4. 稳定契约
### 4.1 SkillMeta（已扩展）
- `family: str`
- `capability_tags: tuple[str, ...]`
- `parallelizable: bool`
- `fallback_policy: str`

### 4.2 路由决策契约
- `SkillRouteDecision`
  - `route_type`
  - `skill_family`
  - `skill_id/executor_target`
  - `reason_code`
  - `risk_level`
  - `requires_model_profile`

### 4.3 Trace / 审计字段
- `cortex_orchestrator_used`
- `executive_router_used`
- `skill_family`
- `skill_id`
- `planner_node_id`
- `model_route.role/model_id/key_source`

## 5. 分期执行（P1-P5）
### P1（已完成）
- Skill-First 路由契约与兼容层落地。
- SkillMeta 元数据扩展并接入并行安全判定。
- Dispatch 链路接入 family/risk/capability 维度。
- Model Router 接口统一并补齐 executive_router 语义。

### P2（进行中，核心已落地）
- Skill DAG Planner 正式化（依赖图、执行批次、并行窗口）。
- 节点级重试与依赖失败守卫（局部重试，避免全链路重跑）。
- 执行结构化回执（node_id/dependencies/attempt_count/idempotency_key）。

### P3（待执行）
- Capability Gap Detector + Runtime Synthesizer（临时能力）。

### P4（待执行）
- Capability Forge + Promotion + Rollback（长期能力闭环）。

### P5（待执行）
- 策略自优化（成功率/时延/成本多目标调参）。

## 6. 本轮代码落实（P2）
- 新增：`host_brain/app/skill_dag_planner.py`
  - DAG 节点标准化：`node_id/dependencies/retry_limit/retry_backoff_ms/idempotency_key`
  - 执行批次生成：`execution_batches`
  - 计划合并：`merge_plan_steps_with_dag()`
- 改造：`host_brain/app/dispatch_agent.py`
  - `_maybe_build_multi_step_plan()` 输出 `execution_batches/parallel_window/dag`
  - `_execute_multi_step_plan_v2()` 完成批次执行、依赖守卫、节点重试
  - 保留旧入口 `_execute_multi_step_plan()` 并转发到 v2（兼容）
- 新增测试：`tests/unit/test_skill_dag_planner.py`
- 扩展测试：`tests/unit/test_dispatch_agent_skill_routing.py`（重试、依赖失败）

## 7. 验收标准
- 功能：旧入口兼容，不降级。
- 稳定：失败节点可局部重跑，依赖失败可被守卫拦截。
- 可观测：能展示 `Skill DAG -> batch -> node attempt -> result`。
- 安全：高风险动作依旧受 governor 门控，不绕过审批链。

## 8. 风险与缓解
- 风险：默认重试导致某些节点调用次数上升。
- 缓解：支持 `retry_limit=0` 显式禁用，后续按 skill family 细化默认策略。
- 风险：旧模板文案与新结构化回执可能存在表述差异。
- 缓解：保留模板覆盖能力，前端展示优先读取结构化字段。

## 9. P3 当前进度（能力缺口闭环第一步）
- 状态：`IN PROGRESS`
- 已落地：
  - `Capability Gap Detector`
    - 新增 `host_brain/app/capability_gap_detector.py`
    - 检测“动作型意图误落 chat”场景，输出 `reason_code/confidence/missing_capability_tags/suggested_family`
  - `Runtime Synthesizer`
    - 新增 `host_brain/app/runtime_capability_synthesizer.py`
    - 自动生成临时能力规格 `runtime_capability_id`，并映射执行目标（`code_agent/translate_agent/vision_agent/repair_agent/intent_agent`）
    - 提供进程级临时能力注册表 `RuntimeCapabilityRegistry`
  - `Dispatch 接线`
    - `dispatch_agent` 新增 `_maybe_synthesize_runtime_capability()`
    - 在路由阶段自动执行：`gap detect -> runtime synth -> 路由改写(delegate)` 
    - 新增 SSE 事件 `CAPABILITY_SYNTHESIZED`
    - trace/handoff/task meta 增加 `runtime_capability_id/capability_gap_reason`
  - `Dependencies 接线`
    - `dependencies.py` 新增 `get_runtime_capability_registry()`
- 兼容性：
  - 默认只在 `route=chat` 且识别为动作请求时触发，不影响已有 skill/delegate 路径。
  - 可通过环境变量 `HOST_RUNTIME_CAPABILITY_SYNTH_ENABLED=false` 关闭。

## 10. P3 当前进度（能力缺口闭环第二步）
- 状态：`IN PROGRESS`（观测与反馈回路已落地）
- 已落地：
  - 新增 `host_brain/app/capability_gap_telemetry.py`
    - 聚合指标：`detected_total/executed_total/success_total/failed_total/success_rate`
    - 维度统计：`by_reason/by_family/by_executor`
    - 最近事件流：`GAP_DETECTED/RUNTIME_CAPABILITY_EXECUTION`
  - `dispatch_agent` 接入执行反馈：
    - 缺口触发时上报 detected
    - plan/delegate/skill 完成后上报 execution（success/fail + error_code + latency）
  - 观测接口：
    - 新增 `GET /obs/capability-gaps/stats`（位于 `obs_routes_stats.py`）
    - 用于前端与运营侧查看能力缺口闭环效果
- 下一步（P3 第三段）：
  - 引入“缺口热点排行榜 + 转正候选（P4 Forge 输入）”
  - 加入按 `skill_family` 的自动策略调优建议
