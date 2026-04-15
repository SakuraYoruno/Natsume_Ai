# 架构修复计划（ARCH-FIX-PLAN）

**文档编号**：ARCH-FIX-PLAN-001
**日期**：2026-04-15
**版本**：v1.3
**状态**：P0/P1/P3 全部完成，P2 待执行
**受众**：核心开发团队

---

## 0. 背景

本文档基于对当前项目架构的全面审查，汇总所有已识别的问题并给出修复方案。
问题来源包括：

- 自研机制修复方案（SELF_BUILT_MECHANISM_FIX_PLAN.md）中已识别但尚未完成的项
- 复杂任务拆解流程审查中发现的问题（P1-1 ~ P3-10）
- 整体架构评估中发现的新问题（plan_graph 死锁、超大文件、降级路径不一致等）

---

## 1. 问题全景

### 1.1 优先级分类

| 优先级 | 定义 | 问题数 |
|--------|------|--------|
| **P0** | 可能导致生产挂起/数据错误的严重 Bug | 1 |
| **P1** | 影响正确性的功能性问题 | 5 |
| **P2** | 影响质量和可靠性的工程问题 | 12 |
| **P3** | 影响可维护性的技术债务 | 8 |

---

## 2. P0 — 紧急（可能导致生产挂起）

### ✅ P0-1：`plan_graph.py` 子图节点存在 asyncio 死锁风险（已完成）

**文件**：`host_brain/app/plan_graph.py`

**根因**：
`_task_route_node` 是同步函数，内部用 `asyncio.run_coroutine_threadsafe` 调用异步的
`dispatcher.dispatch()`。但 `run_plan_graph()` 本身通过 `asyncio.to_thread(graph.invoke, ...)`
在线程池中执行，此时 `asyncio.get_event_loop()` 拿到的 loop 是**调用方的主事件循环**，
而 `asyncio.to_thread` 运行在独立线程中，`loop.is_running()` 为 True，
`run_coroutine_threadsafe` 会把协程提交到主 loop，但主 loop 正在等待 `to_thread` 完成，
形成**循环等待死锁**。

```python
# 当前有问题的代码（plan_graph.py _task_route_node）
loop = asyncio.get_event_loop()
if loop.is_running():
    future = asyncio.run_coroutine_threadsafe(dispatcher.dispatch(req), loop)
    resp = future.result(timeout=35)  # ← 死锁：主 loop 在等 to_thread，to_thread 在等主 loop
```

**修复方案**：
子图节点改为纯同步调用，使用 `asyncio.run()` 在新的独立事件循环中执行，
彻底避免与外层 loop 的交互。

```python
# 修复后
import asyncio
# 在独立线程中，直接用 asyncio.run() 创建新 loop 执行
resp = asyncio.run(dispatcher.dispatch(req))
```

**涉及文件**：`host_brain/app/plan_graph.py`
**工作量**：0.5d
**验收**：并发执行 10 个子任务，无超时/挂起

---

## 3. P1 — 高优先级（影响正确性）

### ✅ P1-1：`_execute_skill_dag` 降级路径数据不一致（已完成）

**文件**：`host_brain/app/executive_runtime_core.py`

**根因**：
`_execute_skill_dag` 优先走 `run_plan_graph`（LangGraph 父子图），失败时降级到手动
`SkillDag` 执行。但降级时使用的 `plan.nodes` 是由 `_maybe_build_multi_step_plan()`
从 `intent_draft.planned_steps` 构建的，而 `run_plan_graph` 使用的子任务是由
`PlanningAgent.create_plan()` 实时拆解的。两者**不是同一份数据**，降级实际上是在
执行一个不同的计划，而非真正的"降级重试"。

**修复方案**：
降级时直接返回失败结果，不再尝试用不同数据重新执行。
或者：在 `run_plan_graph` 失败时，将已拆解的 `sub_tasks` 传递给降级路径使用。

**涉及文件**：`host_brain/app/executive_runtime_core.py`
**工作量**：1d

---

### ✅ P1-2：`synthesize_node` 结果是结构化拼接，不是 LLM 语义整合（已完成）

**文件**：`host_brain/app/plan_graph.py`

**根因**：
`_synthesize_node` 当前实现是字符串拼接：
```python
parts.append(f"[{idx}] {desc}：{result_text}")
final_answer = "\n".join(parts)
```
文档注释说"语义整合"，但实际没有调用 LLM 做真正的语义综合，
多步任务的结果质量与单步无异。

**修复方案**：
`synthesize_node` 调用 LLM gateway，将所有子任务结果作为上下文，
生成一个连贯的最终回复。

**涉及文件**：`host_brain/app/plan_graph.py`
**工作量**：1d

---

### ✅ P1-3：`RULE_ROUTE_MAP` 与 `_hard_rule_route` 中的 skill 验证存在竞态（已完成）

**文件**：`host_brain/app/executive_runtime_core.py`

**根因**：
`_hard_rule_route` 中对 `RULE_ROUTE_MAP` 命中的 skill 路由做了 `skill_registry.get()` 验证，
但 `skill_registry` 是延迟加载的单例，首次调用时可能尚未完成初始化，
导致合法的 skill 被误判为不存在，回退到 `chat` 路由。

**修复方案**：
在 `_hard_rule_route` 中对 skill 验证失败时，降级到 `delegate` 路由而非 `chat`，
保留意图语义。

**涉及文件**：`host_brain/app/executive_runtime_core.py`
**工作量**：0.5d

---

### ✅ P1-4：`PlanningAgent.create_plan()` 中 act 步骤的 `depends_on` 逻辑错误（已完成）

**文件**：`host_brain/agents/planning_agent.py`

**根因**：
```python
# 当前代码
depends_on=[1] if step_id == 2 else [],  # 第一个 act 依赖 reason，后续 act 不依赖任何步骤
```
后续 act 步骤 `depends_on=[]`，意味着它们可以与第一个 act 并行执行。
但实际上很多任务是顺序依赖的（如"先查询再分析"），
`allow_parallel` 应该由 LLM 在拆解时判断，而不是默认全部并行。

**修复方案**：
在 `_decompose_request` 中增加顺序依赖推断：
含有"然后/再/接着/之后"等顺序词的子任务，自动设置 `depends_on=[前一步]`。

**涉及文件**：`host_brain/agents/planning_agent.py`
**工作量**：0.5d

---

### ✅ P1-5：`_collect_task_result` 节点写入父图 `task_results` 的 Reducer 在并发时可能丢失结果（已完成）

**文件**：`host_brain/app/plan_graph.py`

**根因**：
LangGraph `Send API` 并行执行多个子图时，每个子图的 `_collect_task_result` 节点
都会向父图的 `task_results` 字段写入一个列表。
`_merge_task_results` Reducer 是 `left + right`，但 LangGraph 的 Reducer 合并顺序
在并发场景下不保证与 `task_index` 一致，可能导致结果乱序（虽然后续有排序，但
如果某个子图的写入被覆盖则会丢失）。

**修复方案**：
验证 LangGraph `Send API` + Reducer 在并发场景下的正确性，
或改为在 `synthesize_node` 中通过 `task_index` 去重合并。

**涉及文件**：`host_brain/app/plan_graph.py`
**工作量**：0.5d（验证）+ 1d（修复）

---

## 4. P2 — 中优先级（影响质量和可靠性）

### P2-1：`obs_routes_stats.py` 单文件 308KB，维护性灾难

**文件**：`host_brain/app/obs_routes_stats.py`（308 KB）

**根因**：
所有可观测性统计路由全部堆在一个文件中，包含大量重复的查询逻辑、
格式化逻辑和路由定义，没有按功能模块拆分。

**修复方案**：
按功能拆分为：
- `obs_routes_stats_session.py`：会话统计
- `obs_routes_stats_skill.py`：技能执行统计
- `obs_routes_stats_governor.py`：Governor 裁决统计
- `obs_routes_stats_tribe.py`：TRIBE 族状态统计
- `obs_routes_stats_plan.py`：规划执行统计

**涉及文件**：`host_brain/app/obs_routes_stats.py`
**工作量**：2d

---

### P2-2：`chat_context_builder.py` 单文件 138KB，职责不清

**文件**：`host_brain/app/chat_context_builder.py`（138 KB）

**根因**：
Chat 上下文构建逻辑（记忆召回、情感上下文、关系状态、检索结果等）
全部混在一个文件中，任何一个子功能的修改都需要在 138KB 的文件中定位。

**修复方案**：
按上下文类型拆分：
- `chat_context_memory.py`：记忆召回上下文
- `chat_context_emotion.py`：情感/情绪上下文
- `chat_context_bond.py`：关系状态上下文
- `chat_context_retrieval.py`：检索结果上下文
- `chat_context_builder.py`：保留为组合入口（仅调用上述模块）

**涉及文件**：`host_brain/app/chat_context_builder.py`
**工作量**：2d

---

### P2-3：`agent_runtime_shared.py` 104KB，命名误导

**文件**：`host_brain/app/agent_runtime_shared.py`（104 KB）

**根因**：
命名为"shared"但实际包含大量非共享的业务逻辑，
是历史上多次重构后的"垃圾桶文件"。

**修复方案**：
审查内容，将真正共享的工具函数提取到 `host_brain/core/`，
业务逻辑归还到对应的 Agent Handler 文件。

**涉及文件**：`host_brain/app/agent_runtime_shared.py`
**工作量**：3d

---

### P2-4：`prompt_template_defs.py` 272KB，模板定义与代码混合

**文件**：`host_brain/app/prompt_template_defs.py`（272 KB）

**根因**：
所有 prompt 模板定义（Python 字符串常量）全部在一个文件中，
导致文件体积极大，且模板内容与 Python 代码混合，不利于非开发人员维护。

**修复方案**：
将 prompt 模板迁移到 `prompts/` 目录下的 YAML/JSON 文件，
`prompt_template_defs.py` 改为加载器，只保留加载逻辑。

**涉及文件**：`host_brain/app/prompt_template_defs.py`、`prompts/`
**工作量**：3d

---

### P2-5：`dependencies.py` 57KB，初始化逻辑混入依赖注入

**文件**：`host_brain/app/dependencies.py`（57 KB）

**根因**：
依赖注入文件不应该包含业务初始化逻辑，但当前 `dependencies.py` 中
包含大量的服务初始化代码（数据库连接、缓存预热、配置加载等），
导致文件体积过大，且启动时序难以控制。

**修复方案**：
将初始化逻辑迁移到 `host_brain/app/startup.py`，
`dependencies.py` 只保留 `get_xxx()` 形式的单例获取函数。

**涉及文件**：`host_brain/app/dependencies.py`
**工作量**：2d

---

### P2-6：`input_routes.py` 104KB，路由文件不应包含业务逻辑

**文件**：`host_brain/app/input_routes.py`（104 KB）

**根因**：
FastAPI 路由文件应该只做参数解析和响应格式化，
但 `input_routes.py` 包含大量的业务处理逻辑，
导致路由层和业务层耦合。

**修复方案**：
将业务逻辑提取到对应的 service 层，
路由文件只保留 `@router.post` 装饰器和参数/响应处理。

**涉及文件**：`host_brain/app/input_routes.py`
**工作量**：3d

---

### P2-7：`obs_routes_core.py` 3345 行，可观测性核心路由过于庞大

**文件**：`host_brain/app/obs_routes_core.py`（3345 行）

**根因**：
可观测性核心路由将所有核心监控端点（健康检查、指标查询、事件流、告警等）
全部堆在一个文件中，是扫描发现的最大单文件，严重影响可维护性。

**修复方案**：
按功能拆分为：
- `obs_routes_health.py`：健康检查与存活探针
- `obs_routes_metrics.py`：指标查询端点
- `obs_routes_events.py`：事件流与告警端点
- `obs_routes_core.py`：保留为路由注册入口（仅 include_router）

**涉及文件**：`host_brain/app/obs_routes_core.py`
**工作量**：2d

---

### P2-8：`obs_routes_stats_input.py` 3019 行，输入统计路由过大

**文件**：`host_brain/app/obs_routes_stats_input.py`（3019 行）

**根因**：
obs_routes_stats 拆分后，`obs_routes_stats_input.py` 子文件本身仍然超过 3000 行，
说明拆分粒度不够，内部仍存在多个独立的统计维度混合。

**修复方案**：
按统计维度二次拆分：
- `obs_routes_stats_input_text.py`：文本输入统计
- `obs_routes_stats_input_voice.py`：语音输入统计
- `obs_routes_stats_input_multimodal.py`：多模态输入统计
- `obs_routes_stats_input.py`：保留为聚合入口

**涉及文件**：`host_brain/app/obs_routes_stats_input.py`
**工作量**：2d

---

### P2-9：`obs_routes_stats_capability.py` 2892 行，能力统计路由过大

**文件**：`host_brain/app/obs_routes_stats_capability.py`（2892 行）

**根因**：
与 P2-8 同理，能力统计子文件仍然接近 3000 行，
内部混合了 skill 执行统计、工具调用统计、Agent 能力统计等多个维度。

**修复方案**：
按能力类型二次拆分：
- `obs_routes_stats_capability_skill.py`：Skill 执行统计
- `obs_routes_stats_capability_tool.py`：工具调用统计
- `obs_routes_stats_capability_agent.py`：Agent 能力统计
- `obs_routes_stats_capability.py`：保留为聚合入口

**涉及文件**：`host_brain/app/obs_routes_stats_capability.py`
**工作量**：2d

---

### P2-10：`prompt_builder.py` 2090 行，Prompt 构建逻辑过于集中

**文件**：`host_brain/app/prompt_builder.py`（2090 行）

**根因**：
Prompt 构建器将所有 Agent 类型的 prompt 组装逻辑（chat、executive、planning、
tribe 等）全部放在一个文件中，每次新增 Agent 类型都需要修改同一个文件。

**修复方案**：
按 Agent 类型拆分：
- `prompt_builder_chat.py`：Chat Agent prompt 构建
- `prompt_builder_executive.py`：Executive Agent prompt 构建
- `prompt_builder_planning.py`：Planning Agent prompt 构建
- `prompt_builder_tribe.py`：TRIBE 族 Agent prompt 构建
- `prompt_builder.py`：保留为统一入口（工厂函数）

**涉及文件**：`host_brain/app/prompt_builder.py`
**工作量**：2d

---

### P2-11：`graph_config_builder.py` 1947 行，图配置构建逻辑过于集中

**文件**：`host_brain/app/graph_config_builder.py`（1947 行）

**根因**：
图配置构建器将所有图类型（plan_graph、skill_dag、tribe_graph 等）的
节点定义、边定义、条件路由全部混在一个文件中，图结构变更时难以定位。

**修复方案**：
按图类型拆分：
- `graph_config_plan.py`：plan_graph 配置
- `graph_config_skill.py`：skill_dag 配置
- `graph_config_tribe.py`：tribe_graph 配置
- `graph_config_builder.py`：保留为配置加载入口

**涉及文件**：`host_brain/app/graph_config_builder.py`
**工作量**：2d

---

### P2-12：`chat_agent.py` 1928 行，Chat Agent 职责过多

**文件**：`host_brain/app/chat_agent.py`（1928 行）

**根因**：
Chat Agent 承担了消息预处理、上下文注入、LLM 调用、流式输出、
后处理（情感标注、记忆写入）等多个职责，导致文件体积接近 2000 行。

**修复方案**：
按处理阶段拆分：
- `chat_agent_preprocessor.py`：消息预处理与上下文注入
- `chat_agent_postprocessor.py`：后处理（情感标注、记忆写入）
- `chat_agent.py`：保留为主流程协调器（调用上述模块 + LLM 调用）

**涉及文件**：`host_brain/app/chat_agent.py`
**工作量**：2d

---

## 5. P3 — 低优先级（技术债务）

### ✅ P3-1：`cortex_agent.py` 1144 行，职责过多（已完成）

**文件**：`host_brain/tribe/cortex_agent.py`（1144 行）

**根因**：
CortexAgent 作为 TRIBE 族的路由意图聚合 Agent，
承担了意图分析、路由决策、上下文聚合、事件发布等多个职责。

**修复方案**：
拆分为：
- `cortex_intent_analyzer.py`：意图分析
- `cortex_route_decider.py`：路由决策
- `cortex_agent.py`：保留为协调器（调用上述模块）

**涉及文件**：`host_brain/tribe/cortex_agent.py`
**工作量**：2d

---

### ✅ P3-2：`http_hooks.py` + `http_hooks_runtime_helpers.py` 共 142KB，职责重叠（已完成）

**文件**：`host_brain/app/http_hooks.py`（72 KB）、`http_hooks_runtime_helpers.py`（71 KB）

**根因**：
两个文件命名相似，职责边界不清晰，存在大量重复的 HTTP 请求处理逻辑。

**修复方案**：
合并后按功能重新拆分：
- `http_hooks_inbound.py`：入站 Hook
- `http_hooks_outbound.py`：出站 Hook
- `http_hooks_middleware.py`：中间件 Hook

**涉及文件**：`host_brain/app/http_hooks.py`、`host_brain/app/http_hooks_runtime_helpers.py`
**工作量**：2d

---

### ✅ P3-3：`RULE_ROUTE_MAP` 关键词覆盖不完整，需要扩充（已完成）

**文件**：`host_brain/agents/planning_agent.py`

**根因**：
当前 `RULE_ROUTE_MAP` 只有约 15 个关键词，覆盖场景有限，
大量本可以精确匹配的请求仍然走 LLM 意图节点，增加不必要的延迟。

**修复方案**：
基于历史请求日志，统计高频意图模式，扩充 `RULE_ROUTE_MAP` 到 50+ 条目，
并增加正则表达式支持（当前只支持字符串包含匹配）。

**涉及文件**：`host_brain/agents/planning_agent.py`
**工作量**：1d

---

### ✅ P3-4：`GovernorRulesCache` 缺少 `CortexAgent` 接入（已完成）

**文件**：`host_brain/tribe/cortex_agent.py`、`host_brain/integrations/governor_rules_cache.py`

**根因**：
文档 §4.1 要求四个 TRIBE 族 Agent 都接入 `GovernorRulesCache`，
但 `CortexAgent` 的 `update_cortex_intent_allowlist()` 方法从未被调用，
`intent_allowlist_extra` 字段永远为空，`CortexAgent` 对 Governor 的影响路径缺失。

**修复方案**：
在 `CortexAgent` 的意图模型更新事件处理器中，
调用 `GovernorRulesCache.update_cortex_intent_allowlist()`。

**涉及文件**：`host_brain/tribe/cortex_agent.py`
**工作量**：0.5d

---

### ✅ P3-5：`graph_executor.py` 36KB，节点分发逻辑过于集中（已完成）

**文件**：`host_brain/integrations/graph_executor.py`（36 KB）

**根因**：
`_run_single_step` 函数承担了所有节点类型的分发逻辑（model_node、handler_node、
agent_dispatch_node、barrier_join），导致函数体过长，难以单独测试各分支。

**修复方案**：
将各节点类型的执行逻辑提取为独立函数：
- `_execute_model_node()`
- `_execute_handler_node()`
- `_execute_agent_dispatch_node()`
- `_execute_barrier_join_node()`

**涉及文件**：`host_brain/integrations/graph_executor.py`
**工作量**：1d

---

### ✅ P3-6：`skill_dag_planner.py` 中循环依赖处理只有警告，没有错误上报（已完成）

**文件**：`host_brain/app/skill_dag_planner.py`

**根因**：
循环依赖时强制按 `step_index` 顺序执行，只打 warning 日志，
但不向调用方返回任何错误信息，调用方无法感知依赖关系被忽略。

**修复方案**：
在 `SkillDagPlan` 中增加 `has_cycle_warning: bool` 字段，
`_build_execution_batches` 检测到循环依赖时设置该字段，
调用方可以据此决定是否降级处理。

**涉及文件**：`host_brain/app/skill_dag_planner.py`
**工作量**：0.5d

---

### ✅ P3-7：`sub_agent_protocol.py` 中 `vision_agent` 复用 `intent_agent` handler 没有文档说明（已完成）

**文件**：`host_brain/app/sub_agent_protocol.py`

**根因**：
```python
# vision_agent 暂未实现独立 handler，复用 intent_agent
if AGENT_VISION not in self._handler_map and AGENT_INTENT in self._handler_map:
    self._handler_map[AGENT_VISION] = self._handler_map[AGENT_INTENT]
```
这是一个临时方案，但没有 TODO 标注，容易被遗忘。

**修复方案**：
增加 TODO 注释，并在 `AGENT_CAPABILITIES` 中标注 vision_agent 的实现状态。
长期：实现独立的 `vision_agent_handler.py`。

**涉及文件**：`host_brain/app/sub_agent_protocol.py`
**工作量**：0.5d（注释）+ 3d（实现独立 handler）

---

### ✅ P3-8：`plan_graph.py` 单例 `_plan_graph_instance` 没有线程安全保护（已完成）

**文件**：`host_brain/app/plan_graph.py`

**根因**：
```python
_plan_graph_instance: Any = None

def get_plan_graph() -> Any:
    global _plan_graph_instance
    if _plan_graph_instance is None:
        _plan_graph_instance = build_plan_graph()  # 多线程并发时可能构建多次
    return _plan_graph_instance
```
在多线程环境下，可能同时有多个线程进入 `if _plan_graph_instance is None` 分支，
导致 `build_plan_graph()` 被调用多次。

**修复方案**：
使用 `threading.Lock` 保护单例初始化，或改用 `functools.lru_cache`。

**涉及文件**：`host_brain/app/plan_graph.py`
**工作量**：0.5d

---

## 6. 执行顺序

```
✅ 第一阶段（P0，已完成）：
  └── P0-1：plan_graph.py asyncio 死锁修复 ✅

✅ 第二阶段（P1，已完成）：
  ├── P1-1：_execute_skill_dag 降级路径数据不一致 ✅
  ├── P1-2：synthesize_node 改为 LLM 语义整合 ✅
  ├── P1-3：RULE_ROUTE_MAP skill 验证降级策略 ✅
  ├── P1-4：PlanningAgent depends_on 顺序依赖推断 ✅
  └── P1-5：task_results Reducer 并发正确性验证 ✅

🔲 第三阶段（P2，待执行）：
  ├── P2-1：obs_routes_stats.py 拆分（308KB）
  ├── P2-2：chat_context_builder.py 拆分（138KB）
  ├── P2-3：agent_runtime_shared.py 清理（104KB）
  ├── P2-4：prompt_template_defs.py 迁移到 YAML（272KB）
  ├── P2-5：dependencies.py 初始化逻辑分离
  ├── P2-6：input_routes.py 业务逻辑提取
  ├── P2-7：obs_routes_core.py 拆分（3345行）
  ├── P2-8：obs_routes_stats_input.py 二次拆分（3019行）
  ├── P2-9：obs_routes_stats_capability.py 二次拆分（2892行）
  ├── P2-10：prompt_builder.py 按 Agent 类型拆分（2090行）
  ├── P2-11：graph_config_builder.py 按图类型拆分（1947行）
  └── P2-12：chat_agent.py 按处理阶段拆分（1928行）

✅ 第四阶段（P3，全部已完成）：
  ├── P3-1：cortex_agent.py 拆分（1144行）✅
  ├── P3-2：http_hooks.py 合并重组 ✅
  ├── P3-3：RULE_ROUTE_MAP 扩充到 50+ 条目 ✅
  ├── P3-4：CortexAgent 接入 GovernorRulesCache ✅
  ├── P3-5：graph_executor.py 节点分发逻辑提取 ✅
  ├── P3-6：skill_dag_planner.py 循环依赖错误上报 ✅
  ├── P3-7：vision_agent TODO 注释 ✅
  └── P3-8：plan_graph.py 单例线程安全保护 ✅
```

---

## 7. 工作量汇总

| 阶段 | 问题数 | 总工作量 |
|------|--------|----------|
| P0（立即） | 1 | 0.5d |
| P1（本周） | 5 | 4d |
| P2（下一迭代） | 12 | 27d |
| P3（规划中） | 8 | 11d |
| **合计** | **26** | **42.5d** |

---

## 8. 不修复的内容（有意为之）

| 问题 | 不修复原因 |
|------|-----------|
| 将 AIRLOCK 交给 TRIBE 族 | AIRLOCK 是数据管道操作，不是认知决策，职责不同 |
| 将 Evidence Plane 交给 TRIBE 族 | Evidence Plane 需要 SQLite 事务原子性，TRIBE 族是异步事件驱动 |
| 用 CortexAgent 替代 Governor 裁决 | Governor 需要同步阻断，CortexAgent 是异步认知循环 |
| 删除 SkillDag 降级路径 | 作为 plan_graph 失败时的最后防线，保留有价值 |
| 合并 edge_client 和 host_brain | 两者职责正交（接入层 vs 执行层），分离是正确的 |

---

*文档版本：v1.3 | 最后更新：2026-04-15 | P0/P1/P3 全部完成，P2 待执行（12项，含扫描新增 P2-7~P2-12）*
