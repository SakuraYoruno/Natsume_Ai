你现在是 Natsume AI 项目的高级系统架构审计与修复工程师。

你的任务不是泛泛分析，而是**基于当前工作区代码的真实实现**，完成以下工作：

1. 修正文档中的系统事实偏差
2. 补齐我指出的控制面 / 状态面缺陷
3. 在必要时直接修改代码
4. 运行测试并给出验证证据
5. 产出一版**严格、真实、不夸大完成度**的修订报告

---

# 工作方式

你必须直接在当前代码库中工作，遵循以下原则：

- **先读代码，再下结论**
- **以代码真实执行语义为准**
- **不允许脑补，不允许沿用旧报告中的可疑结论**
- **不允许把“已接入”写成“已闭环”**
- **不允许把“有单测”写成“E2E 已验证”**
- **不允许只提建议不落地**
- 可以直接修改代码、文档、测试
- 修改后必须运行相关测试
- 最终必须给出：代码修改清单、验证结果、修订后的完整报告

---

# 当前需要修复 / 核查的核心问题

## 问题 1：主链执行顺序在文档中不一致
你必须审计真实调用链，明确以下顺序的唯一事实：

- `handle_chat`
- `pre_intent_classifier`
- `_unified_chat_sse_generator`
- `Governor.evaluate_intent`
- `SceneRouter.route_sse`
- `MainChain.run_as_sse_adapter`
- `LangGraph` 执行器
- `/v1/chat`
- `/v1/host/input`

### 必须完成
- 找到真实入口函数
- 找到真实调用栈
- 输出唯一可信的执行时序
- 修正旧报告中所有顺序冲突
- 如有必要，补注释或轻量重构，**但不要破坏现有行为**

---

## 问题 2：成熟度分级混乱，混淆了“已接线 / 已生效 / 已验证 / 已成为唯一事实来源”
你必须引入以下分级体系，并用于修订报告：

- `DECLARED`：仅有设计 / 接口 / 说明
- `WIRED`：代码已接线，有调用点
- `ACTIVE`：已进入主链并影响真实运行结果
- `SHADOW`：已接入旁路观测 / 审计，但不影响主生成或主决策
- `AUTHORITATIVE`：已成为该领域唯一事实来源 / 唯一裁决入口
- `E2E_VERIFIED`：已完成端到端联调验证并有证据
- `PROD_READY`：具备错误处理、可观测性、回退策略、安全边界

### 必须重新定级的对象
- `MainChain`
- `RoutingMatrix`
- `ContextPackBuilder`
- `MemoryGuard`
- `StateArbiter`
- `SCENE_VISION_UI`
- `/v1/host/input`
- `/v1/edge/events`

### 额外要求
凡是**只把结果写入 `audit_refs`，但不影响 prompt / 决策 / 执行** 的模块，必须标为 `SHADOW`，不得写成“已完成接入主链”。

---

## 问题 3：`ContextPackBuilder` 可能只是 shadow mode
请审查它在 `chat_stream_handler.py`、`_build_stream_chat_prompt()` 上下游中的真实作用。

你必须判断：
- 它的结果是否真的进入 prompt 构建
- 是否只是写入 `COMMIT.audit_refs`
- 是否只是做观测 / 审计而不影响生成
- 当前真实等级是 `SHADOW` 还是 `ACTIVE`

### 要求
- 给出明确判定
- 修正文档口径
- 如果你认为它应该升级为 `ACTIVE`，可以实施**最小安全接入**
- 若升级，必须说明：
  - 修改文件
  - 接入点
  - 对 prompt 的具体影响
  - 风险
  - 回退方式

---

## 问题 4：`StateArbiter` 可能只完成了 source 注册，未进入业务裁决面
请检查：

- 是否仅在 `runtime_lifespan` 注册 source
- 是否有真实业务调用 `resolve(...)`
- 哪些读事实路径绕过了它
- 它当前应归类为 `WIRED` / `ACTIVE` / `AUTHORITATIVE`

### 处理策略
二选一：

#### 方案 A（保守）
- 不改主业务逻辑
- 只修正文档
- 明确标注它当前只是 `WIRED` 或 `ACTIVE`

#### 方案 B（推进）
- 选择一条真实业务路径接入 `StateArbiter.resolve(...)`
- 例如 retrieval / context / memory fact resolution
- 接入后补测试
- 报告中明确：
  - 哪些事实已进入仲裁路径
  - 哪些仍未进入

优先采用**最小安全改动**。

---

## 问题 5：`/v1/edge/events` 当前 success ≠ 副作用完成
请审查 `system_handler.py` 相关链路，明确：

- HTTP success 是否只是 accepted
- Bond / VTS 是否为 fire-and-forget
- 副作用是否可观测
- 文档是否夸大为“完整闭环已打通”
- 当前无鉴权是否存在风险

### 必须完成
1. 给出该链路真实语义分层：
   - request accepted
   - side effect dispatched
   - side effect completed
   - side effect observed
2. 修正文档表述，不能把 accepted 写成完整闭环
3. 审查是否需要最小安全保护：
   - 来源校验
   - 事件类型白名单
   - 内网限制
   - HMAC / 签名
4. 若暂不加鉴权，必须在报告中写明安全前提与风险边界

---

## 问题 6：`SCENE_VISION_UI` 现在可能只是“路径放行”，不是“能力已验证”
请审查：

- 白名单是否真实生效
- `chat_handler_vision._vision_ui_sse_generator` 是否可达
- `image_upload_consent=true` 分支是否可达
- 无 consent 的降级逻辑是否成立
- `audit_refs.vision_input` 是否真实写入
- 是否已有 E2E 证据，而不只是单测

### 要求
- 给出真实等级：`WIRED` / `ACTIVE` / `E2E_VERIFIED`
- 若未联调验证，不得写“视觉链路完整实现”
- 必须修正文档为真实状态

---

# 执行顺序（必须按顺序做）

## P0
1. 审计真实执行顺序
2. 修正文档中所有顺序冲突
3. 重分级：
   - `ContextPackBuilder`
   - `StateArbiter`
   - `RoutingMatrix`
   - `MainChain`

## P1
4. 审查 `/v1/host/input` 是否真的进入完整 chat 主链，并补证据
5. 审查 `/v1/edge/events` 的真实语义与安全边界，并修正文档

## P2
6. 审查 `SCENE_VISION_UI` 当前真实状态
7. 修正文档中所有夸大表述

## P3
8. 补充 / 修复必要测试
9. 输出最终修订版报告

---

# 输出要求

你必须在完成代码审计、修改、测试后，按以下顺序输出结果：

## 1. 执行摘要
简要说明：
- 发现了哪些关键事实偏差
- 改了哪些代码 / 文档
- 哪些问题真正解决了
- 哪些仍未验证

## 2. 控制面真实性审计
给出**唯一可信的执行顺序**，至少覆盖：
- `/v1/chat`
- `/v1/host/input`
- `handle_chat`
- `pre_intent_classifier`
- `_unified_chat_sse_generator`
- `Governor`
- `SceneRouter`
- `MainChain`
- `LangGraph`

并提供：
- mermaid 时序图
- 旧报告冲突点
- 修正后的统一结论

## 3. 模块成熟度重分级表
至少覆盖：
- `MainChain`
- `RoutingMatrix`
- `ContextPackBuilder`
- `MemoryGuard`
- `StateArbiter`
- `SCENE_VISION_UI`
- `/v1/host/input`
- `/v1/edge/events`

字段必须包含：
- 模块名
- 当前等级
- 理由
- 证据（文件 / 函数）
- 是否建议继续升级
- 下一步最小改动

## 4. 代码修改清单
列出：
- 修改文件
- 修改点
- 修改原因
- 是否改变现有行为
- 风险与回退方式

## 5. 测试与验证结果
必须区分：
- 单元测试
- 集成测试
- E2E 联调

每项写清：
- 是否执行
- 结果
- 覆盖范围
- 未验证项

## 6. 修订后的报告全文
直接输出修订后的《Natsume AI 系统数据流全景报告》完整内容，不要只给 diff。

## 7. 仍未解决的问题与后续建议
必须明确哪些只是：
- `WIRED`
- `SHADOW`
- `ACTIVE`
而不是 `E2E_VERIFIED` 或 `PROD_READY`

---

# 硬性约束

- 不允许只给建议，必须落到代码 / 文档 / 测试
- 不允许把 TODO 写成“已解决”
- 不允许把单元测试通过写成 E2E 完成
- 不允许夸大成熟度
- 不允许隐藏不确定性
- 必须引用具体文件名和函数名
- 最终结论必须以代码真实语义为准

---

现在开始执行。

先遍历并审计相关代码文件，再实施最小必要修改，再运行测试，最后按要求输出完整结果。

---

# 后续任务看板（更新于 2026-03-11 22:40）

> 以下任务来自严格代码审计（见 `docs/dataflow_report_20260311.md` 第十六节 §16.7）。
> 任务状态必须以代码真实执行语义和测试证据为准；未落地前不得写成“已解决”。

## P1（高优先级）

### TODO-1：ContextPackBuilder 升级为 ACTIVE（已完成，2026-03-11）

**当前状态**：`ACTIVE`（可回退）
- `_cpb.build()` 输出会转换为 `retrieval_evidence_items` 并在 `HOST_CONTEXT_PACK_ACTIVE=true` 时回写，真实影响 `_build_stream_chat_prompt`
- 同时保留 `audit_refs.context_pack_builder` 观测，并新增 `mode/active_enabled/applied_to_prompt/rewritten_item_count/input_item_count`

**改动文件**：
- `host_brain/app/chat_stream_handler.py`
- `tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`

**验证**：
- `pytest -q tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`（6 passed）

**回退方式**：设置 `HOST_CONTEXT_PACK_ACTIVE=false`，保留构建与审计但不回写 prompt 输入（退回 `SHADOW` 语义）。

---

### TODO-2：`/v1/edge/events` 添加事件类型白名单（已完成，2026-03-11）

**当前状态**：`ACTIVE`（最小安全边界已上线）
- 已增加 `ALLOWED_EDGE_EVENT_TYPES` 事件类型白名单
- 已增加 `ALLOWED_EDGE_EVENT_SOURCES` 来源白名单
- 非法类型/来源在入口返回 `HTTP 400`

**改动文件**：
- `host_brain/app/system_handler.py`
- `tests/unit/test_edge_events_whitelist.py`

**验证**：
- `pytest -q tests/unit/test_edge_events_whitelist.py`（3 passed）
- `pytest -q tests/integration/test_host_integration.py::TestEdgeEventsEndpoint`（4 passed）

**安全前提**（当前暂不加鉴权的前提）：
1. Host Brain 仅部署在内网，不暴露公网
2. Edge Client 与 Host Brain 在同一受信任网络
3. 无多租户场景（单用户部署）

---

## P2（中优先级）

### TODO-3：RoutingMatrix 升级为 ACTIVE（已完成，2026-03-11）

**当前状态**：`ACTIVE`（策略门控）
- `_decision.selected_model` 已映射回真实 runtime 选择（`provider_name/model_id`），不再仅写审计字段
- 保留 8B 健康探测与策略门控 fallback；不可切换时写入 `routing_matrix_decision.not_applied_reason`
- 新增 `routing_matrix_decision.runtime_applied/runtime_selected_target` 用于判定是否真实改写

**改动文件**：
- `host_brain/app/graph_config_builder.py`
- `tests/unit/test_host_8b_runtime_route.py`

**验证**：
- `pytest -q tests/unit/test_host_8b_runtime_route.py`（9 passed）
- 新增用例：`test_resolve_host_llm_runtime_routing_matrix_reasoning_scene_overrides_to_cloud`

---

### TODO-4：StateArbiter 接入业务查询（已完成，2026-03-11）

**当前状态**：`ACTIVE`（局部路径）
- 启动时注册 world/style/episodic 三个适配状态源（修复无效类名/方法引用导致的注册降级）
- 流式主链调用 `StateArbiter.resolve("world_canon_facts", session_id)`，命中时注入 `retrieval_evidence_items` 与 `retrieval_summary.world_canon`
- COMMIT 增加 `audit_refs.state_arbiter`，可观测 status/source/fallback/conflict

**改动文件**：
- `host_brain/app/http_hooks.py`
- `host_brain/app/chat_stream_handler.py`
- `tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`

**验证**：
- `pytest -q tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`（7 passed）
- 新增用例：`test_stream_state_arbiter_resolve_injects_world_canon_into_prompt`

---

## P3（低优先级）

### TODO-5：SCENE_VISION_UI E2E 联调验证

**当前状态**：`E2E_VERIFIED`（2026-03-11）
- 白名单生效，`SCENE_VISION_UI` 可达（已兼容 `scene_type=SCENE_VISION_UI` 前缀写法）
- 双端实测已覆盖：
  - `image_upload_consent=true` → `audit_refs.vision_input.image_upload_allowed=true`
  - `image_upload_consent=false` → `audit_refs.vision_input.image_upload_allowed=false`
- `audit_refs.vision_input`、`GRAPH_START(scene_type=VISION_UI)`、`COMMIT(APPROVE)` 均已观测

**证据**：
- 真实双端联调（本地进程启动 `host_brain` + `edge_client`）返回：
  - `edge_health_status=ACCEPTED`
  - `host_health_status=OK`
  - `consent_true.vision_input.image_upload_allowed=true`
  - `consent_false.vision_input.image_upload_allowed=false`
- 集成测试：
  - `tests/integration/test_host_integration.py::TestChatEndpoint::test_chat_vision_ui_with_consent_writes_vision_audit_refs`
  - `tests/integration/test_host_integration.py::TestChatEndpoint::test_chat_vision_ui_without_consent_degrades_image_upload`

---

### TODO-6：MainChain 骨架节点填充

**当前状态**：`ACTIVE`（轻编排适配层，2026-03-11）
- 各节点 `execute()` 已补充最小可执行语义（非纯 `return True`）：
  - `AirlockQuarantineNode`：输入净化/空消息阻断
  - `GovernorNode`：场景提示映射 `TaskType` 并落审计
  - `GraphExecutor/RecallFusion/LlmGateway/QA/Episodic/Audit/AirlockSanitizer`：状态落点与审计标签
- `run_as_sse_adapter` 已升级为：
  - 执行 pre-node（airlock→governor→graph→recall→llm）
  - 委托 `_unified_chat_sse_generator` 并观测 SSE（含 COMMIT）
  - 执行 post-node（qa→episodic→audit→sanitizer）
  - 前后置失败时触发 FailureHandler + REJECT COMMIT 兜底

**证据**：
- 代码：`host_brain/app/graph/main_chain.py`
- 单测：`tests/unit/test_main_chain_sse_adapter.py`（同步执行，覆盖 pre/post 编排与异常兜底）

---

### TODO-7：SCENE_VISION_UI 生产化加固（已完成，2026-03-12）

**当前状态**：`PROD_READY`（最小生产化加固已上线）
- 新增 `_VisionSLAMonitor` 模块级单例，追踪连续失败次数、降级率
- 连续失败 >= `HOST_VISION_SLA_FAIL_THRESHOLD`（默认 3）时写入 ledger 告警（`S_VISION_SLA_ALERT`）
- 降级率 >= `HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD`（默认 0.5）且总调用 >= 10 时触发告警
- 单次调用延迟 > `HOST_VISION_SLA_LATENCY_WARN_MS`（默认 10000ms）时写入延迟告警（`S_VISION_SLA_LATENCY_WARN`）
- 新增请求来源校验：`HOST_VISION_REQUIRE_INTERNAL_ORIGIN=true` 时，仅允许内网 IP（127.x/192.168.x/10.x/::1）
- `COMMIT.audit_refs.sla_monitor` 输出 SLA 监控摘要（可观测）

**改动文件**：
- `host_brain/app/chat_handler_vision.py`

**验证**：
- `pytest -q tests/unit/test_main_chain_deep_business.py::TestVisionSLAMonitor`（4 passed）

**回退方式**：
- 设置 `HOST_VISION_REQUIRE_INTERNAL_ORIGIN=false` 禁用来源校验
- SLA 监控为纯观测，不影响主链路

---

### TODO-8：MainChain 深度业务化（已完成，2026-03-12）

**当前状态**：`ACTIVE`（深度业务化，接近 AUTHORITATIVE）
- `GovernorNode`：接入真实 `IntentDecisionEngine.evaluate_intent`，执行完整 R1-R7 门控
  - 适配层模式（`adapter_mode=sse`）自动跳过重复鉴权，避免双重 Governor 调用
  - 降级策略：引擎不可用时退回最小状态语义，标记 `GOVERNOR_DEGRADED_FALLBACK`
- `RecallFusionNode`：接入真实 `RetrievalOrchestrator.arun`，执行七路并行召回
  - 降级策略：Orchestrator 不可用时退回最小状态语义，标记 `RECALL_DEGRADED_FALLBACK`
  - 召回结果写入 `ctx.recall_context_pack`，供后续节点使用
- `QAContractNode`：接入真实内容合规校验（4 项检查）
  - 格式规范化：去除首尾空白、压缩多余空行、去除行尾空白
  - 长度截断：超过 `HOST_QA_MAX_RESPONSE_CHARS`（默认 4000）时在句子边界截断
  - 安全词检测：疑似系统提示词泄露时写入告警标签（不阻断，误报率较高）
  - Null 字节清除：去除 `\x00` 字节

**改动文件**：
- `host_brain/app/graph/main_chain.py`

**验证**：
- `pytest -q tests/unit/test_main_chain_deep_business.py`（18 passed）
- `pytest -q tests/unit/test_main_chain_sse_adapter.py`（5 passed，适配层兼容性验证）

**回退方式**：
- `GovernorNode` 降级：引擎不可用时自动降级，不阻断链路
- `RecallFusionNode` 降级：Orchestrator 不可用时自动降级，不阻断链路
- `QAContractNode` 降级：检查异常时保留原始输出，标记 `QA_CHECK_DEGRADED`

---

### TODO-10：CPB slot 注入 + GovernorNode 鉴权路径统一（已完成，2026-03-12）

**当前状态**：`ACTIVE`（链路一致）

**问题 A：`build_chat_context_pack` 未使用 CPB slot 输出**
- `ContextPackBuilder.build()` 产出的 slot 数据未注入 `build_chat_context_pack` 的参数，导致 CPB 结果与 prompt 构建脱节
- **修复**：`chat_context_builder.py` 中 `build_chat_context_pack` 接收并注入 `cpb_slots` 参数；`chat_stream_handler.py` 中提取 CPB slot 并透传

**问题 B：GovernorNode 两条鉴权路径不一致**
- 适配层模式（`adapter_mode=sse`）下 `GovernorNode` 跳过真实鉴权，由 `_unified_chat_sse_generator` 负责；两条路径行为不一致
- **修复**：`main_chain.py` 中统一两条路径，适配层也执行真实鉴权，结果通过 `pre_governor_result` 传递给 `_unified_chat_sse_generator`，避免重复执行

**改动文件**：
- `host_brain/app/chat_context_builder.py`：`build_chat_context_pack` 增加 `cpb_slots` 参数注入
- `host_brain/app/chat_stream_handler.py`：提取 CPB slot 并透传；接收 `pre_governor_result`
- `host_brain/app/graph/main_chain.py`：`GovernorNode` 统一两条鉴权路径，注入 `pre_governor_result`

**验证**：
- 合并运行：62 passed（含所有相关单元测试，无回归）

**回退方式**：`cpb_slots=None` 时退回原有 prompt 构建逻辑；`pre_governor_result=None` 时退回原有鉴权逻辑，无破坏性变更

---

### TODO-9：RecallFusionNode 孤岛问题修复（已完成，2026-03-12）

**当前状态**：`ACTIVE`（链路闭环）
- **问题**：`RecallFusionNode` 执行七路召回后，结果写入 `ctx.recall_context_pack`，但 `run_as_sse_adapter` 调用 `_unified_chat_sse_generator` 时未传入，导致 `_sse_event_generator` 内部重复执行一次召回（双重召回，预召回结果被丢弃）
- **修复**：将 `ctx.recall_context_pack.retrieval_evidence_items` 作为 `_pre_recall_evidence` 参数透传，链路：`run_as_sse_adapter` → `_unified_chat_sse_generator` → `SceneRouter.route_sse` → `_sse_event_generator`
- **效果**：`_sse_event_generator` 在有预召回结果时跳过内部召回，避免双重召回浪费资源；空列表/None 时退回原有召回逻辑

**改动文件**：
- `host_brain/app/chat_stream_handler.py`：`_sse_event_generator` 增加 `_pre_recall_evidence` 参数
- `host_brain/app/chat_handler.py`：`_unified_chat_sse_generator` 增加 `_pre_recall_evidence` 参数并透传
- `host_brain/app/graph/main_chain.py`：`SceneRouter.route_sse` 增加 `_pre_recall_evidence` 参数并透传；`run_as_sse_adapter` 提取 `ctx.recall_context_pack` 并注入

**验证**：
- `pytest -q tests/unit/test_main_chain_deep_business.py::TestPreRecallInjection`（2 passed）
- 合并运行：62 passed（含所有相关单元测试，无回归）

**回退方式**：`_pre_recall_evidence=None` 时退回原有召回逻辑，无破坏性变更

---

## 完成标准

| 任务 | 完成标准 | 升级后等级 |
|------|---------|-----------|\n| TODO-1 | `retrieval_evidence_items` 真实使用截断结果，有测试验证 | `ACTIVE` |
| TODO-2 | 白名单校验上线，非法事件类型返回 400 | `ACTIVE`（安全边界完整） |
| TODO-3 | 路由矩阵决策真实影响模型选择，有 fallback | `ACTIVE` |
| TODO-4 | 至少一条业务路径调用 `resolve()`，有测试 | `ACTIVE` |
| TODO-5 | 双端 E2E 联调通过，有截图/日志证据 | `E2E_VERIFIED` |
| TODO-6 | 骨架节点填充真实逻辑，`run_as_sse_adapter` 不再只是透传 | `ACTIVE` |
| TODO-7 | SLA 监控上线，连续失败/降级率/延迟告警可观测，来源校验可配置 | `PROD_READY`（最小加固） |
| TODO-8 | Governor/Recall/QA 接入真实业务调用，有降级策略和测试验证 | `ACTIVE`（深度业务化） |
| TODO-9 | RecallFusionNode 预召回结果透传至 `_sse_event_generator`，消除双重召回 | `ACTIVE`（链路闭环） |
| TODO-10 | CPB slot 注入 + GovernorNode 两条鉴权路径统一 | `ACTIVE`（链路一致） |
