# 数字生命多智能体族蓝图 v1（2026-04-08）

- 受众：MASTER / 核心研发
- 状态：蓝图留痕（2026-04-19 已校准；原文为 Draft / 可直接进入 P0 实施）
- 目标：在保持“单一人格核心”的前提下，完成数字生命系统的多智能体族重构。

---

当前代码校准说明（2026-04-19）：
- 本文是 2026-04-08 的多智能体族蓝图，不是当前运行时事实文档。
- 当前仓库并不存在 `host_brain/app/top_agent.py`；现行入口以 `host_brain/app/chat_handler.py -> cortex_orchestrator.py -> agent_runtime_shared.py -> executive_runtime*` 为准，`/v1/host/input` 回流以 `host_brain/app/input_routes.py` 为准。
- 文中出现的 `ContextPack`、`StateArbiter`、主 DAG / 子图 DAG、P0-P3 迁移路线，应按蓝图/阶段设想理解，不直接作为当前任务来源。
- 其中 `inner_activity_agent.py`、`inner_world.py`、`chat_context_builder.py`、`monitor_ui/src` 是仓库中的真实模块，但本文对它们的“代码映射”属于当时方案映射，不等同于当前接口契约。

## 1. 问题定义（为何要重构）

当前问题集中在三类：

1. **人格与执行耦合过深**  
   人格逻辑、策略逻辑、工具执行逻辑混杂，导致“说话像系统拼接结果”，而非稳定人格输出。
2. **链路可观测不足**  
   WebUI 只能看到部分节点，难以确认 `planner -> rag -> motive/policy -> dispatch -> subagent -> chat` 的真实路径。
3. **兜底逻辑侵入语义**  
   部分 fallback 会“改题意”，导致对话牛头不对马嘴。

---

## 2. 设计原则（必须遵守）

1. **单人格核心原则**  
   `PersonaCoreAgent` 是唯一人格主导者；其余 agent 只提供结构化事实，不直接决定人格话术。
2. **单写权限原则**  
   长期人格状态（SoulState）只有核心人格族可写，避免多源写入漂移。
3. **结构化协议原则**  
   agent 之间只传 `ContextPack` 与 `AgentEnvelope`，禁止字符串拼接式隐式协议。
4. **最小兜底原则**  
   保留防空/防越界/防系统话兜底；移除会改题意的“陪伴式硬改写”兜底。
5. **可追踪原则**  
   每次请求必须能追踪到主 DAG 与子图 DAG，支持逐节点输入输出摘要与耗时。

---

## 3. 智能体族总体架构

### 3.1 智能体族划分

1. **核心人格族**
   - `PersonaCoreAgent`（唯一最终人格裁决）
2. **感知族**
   - `ASRAgent` / `OCRAgent` / `SceneAgent` / `EmotionSensorAgent`
3. **记忆族**
   - `EpisodicMemoryAgent` / `SemanticMemoryAgent` / `BondMemoryAgent` / `ResidentMemoryAgent`
4. **规划推理族**
   - `QueryPlannerAgent` / `AgenticRAGAgent` / `EvidenceFusionAgent`
5. **动机情感族**
   - `MotiveAgent` / `ResponsePolicyAgent` / `SilenceAgent` / `BodySenseAgent` / `InnerActivityAgent`
6. **执行族**
   - `DispatchAgent` / `ToolExecAgent` / `SkillPoolAgent`
7. **保障族**
   - `SafetyGuardAgent` / `QualityJudgeAgent` / `TraceAgent`

### 3.2 主链路（同步）

`Input -> Planner -> AgenticRAG -> Motive/Policy -> PersonaCore -> Dispatch -> Chat -> Output`

说明：
- `PersonaCore` 读取全量上下文，输出“人格一致”的最终回复草案与执行意图。
- `SafetyGuard/QualityJudge` 在出站前做边界门控，不改题意。

### 3.3 旁路（异步）

- `InnerActivityAgent`（内心流）
- 记忆蒸馏（episodic -> semantic）
- 成长评估（self model / reflection）
- 观测聚合（trace/metrics）

---

## 4. 状态域与写权限模型

### 4.1 状态域

1. `SoulState`：人格、关系、成长、长期倾向
2. `RuntimeState`：本轮动机、策略、情绪、感知快照
3. `MemoryState`：episodic/semantic/bond/resident
4. `ExecutionState`：任务执行与工具调用状态

### 4.2 写权限

- `PersonaCoreAgent`：`SoulState`（唯一写）
- 动机情感族：`RuntimeState` 指定字段
- 记忆族：`MemoryState` 指定字段
- 执行族：`ExecutionState`

所有写入都通过 `StateArbiter`，校验：
- `schema_version`
- `writer_agent_id`
- `state_version`
- `conflict_policy`

---

## 5. 统一协议（AgentEnvelope / ContextPack）

## 5.1 `AgentEnvelope`（最小规范）

```json
{
  "agent_id": "motive_agent",
  "status": "OK",
  "confidence": 0.86,
  "reason": "dominant continuity drive",
  "schema_version": "1.0",
  "input_refs": ["ctx:runtime", "ctx:memory"],
  "output": {},
  "ts_ms": 1775635000000
}
```

### 5.2 `ContextPack`（最小规范）

```json
{
  "request_meta": {},
  "planner": {},
  "retrieval": {},
  "motive": {},
  "response_policy": {},
  "soul_state": {},
  "offline_state": {},
  "trace_refs": []
}
```

---

## 6. 可观测性目标（WebUI / Trace）

必须支持三层视图：

1. **全局时间线层**：请求级总览（状态、延迟、降级原因）
2. **请求 DAG 层**：主链路节点（planner/rag/motive/policy/dispatch/chat）
3. **子图 DAG 层**：每个 subagent 的内部节点图（节点输入摘要、输出摘要、耗时、错误）

必须新增字段：
- `node_id` / `parent_node_id`
- `agent_family`
- `degrade_code`
- `fallback_used`
- `output_digest`

---

## 7. 迁移路线（P0/P1/P2/P3）

### P0：地基统一（先把协议和可观测打通）
- `AgentEnvelope + ContextPack + StateArbiter` 落地
- PersonaCore 单写权限生效
- 主链路 trace DAG + subagent 子图打通
- 删除“改题意兜底”，仅保留边界兜底

### P1：能力拆族与路由统一
- 感知族、记忆族、规划族模块边界固化
- Dispatch/Tool 统一协议
- WebUI 展示完整链路并支持错误归因

### P2：数字生命增强
- `InnerActivityAgent` 与 SoulState 深耦合（去复读、提升自我相关思考）
- 非监督成长回路：观察 -> 反思 -> 轻量策略更新

### P3：收口与评测
- 回归评估集 + A/B
- 延迟/稳定性优化
- 发布前门禁与回滚预案

---

## 8. 风险与回滚

1. **风险：多 agent 协议引入复杂度**
   - 对策：先做最小协议 v1，禁用可选字段膨胀。
2. **风险：链路变长导致延迟升高**
   - 对策：同步链路最小化，旁路异步化；设置 P95 预算。
3. **风险：人格被中间层冲淡**
   - 对策：PersonaCore 单人格最终裁决，不允许中间层改写人格话术。

回滚策略：
- 全部新链路挂在 feature flag 下；
- 保留旧主链路入口（只读）一个迭代窗口；
- 出现重大回归时可一键切回旧链路。

---

## 9. 当时设想的代码映射（2026-04-08 草案）

- `host_brain/persona/inner_activity_agent.py`：已作为动机情感族子模块起点
- `host_brain/persona/inner_world.py`：内心流引擎（需继续协议化）
- `host_brain/app/top_agent.py`：当时设想的主链路编排入口（当前仓库不存在，应以 `chat_handler.py -> cortex_orchestrator.py -> agent_runtime_shared.py` 现行链为准）
- `host_brain/app/chat_context_builder.py`：状态注入中心（需升级为 ContextPack builder）
- `host_brain/app/monitor_ui/src`：trace 可视化（需补全子图级展示）

---

## 10. 当时的执行结论（历史）

2026-04-08 的原始结论是“本蓝图不是先讨论后实现，可以直接进入 `P0`”。  
截至 2026-04-19，这一表述仅保留为阶段留痕；现行整理、实现与验收应以 `README.md`、`docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`、`docs/50_project/arch/AGENT_ORCHESTRATION_DETAIL.md` 和实际代码为准。  
历史执行稿仍可参见：`docs/35_quality/audits/DIGITAL_LIFE_MULTI_AGENT_P0_EXECUTION_20260408.md`
