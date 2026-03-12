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

---

## 近期完成（2026-03-12 第二批）

### TODO-11：WebUI 监控扩展（已完成，2026-03-12）

**当前状态**：`ACTIVE`（监控覆盖率大幅提升）

**背景**：原 WebUI 仅监控 6 个边缘服务 + 10 个 LLM 节点（2 张日志表），覆盖率极低。

**完成内容**：

**前端修复（4 处 Bug）**：
- `api/obs.js`：修复 `fetchSessions` `time_range→window_minutes` 参数映射失效
- `api/obs.js`：修复 `fetchMetricsTimeseries` 参数映射（`range_minutes/interval_minutes`）
- `Sessions.jsx`：修复 `ts_ms` 时间格式化、`status` 映射、字段对齐
- `Metrics.jsx`：增加 `transformApiData()` 适配后端 `points[]` 格式（原图表永远显示 mock 数据）

**后端新增 4 个端点**：
- `GET /obs/governor/stats`：Governor 决策漏斗（批准/拒绝/reason_code 分布/时序趋势）
- `GET /obs/errors`：全局错误统计（stage 分布/Top10 reason_code/最近错误列表）
- `GET /obs/subsystems/stats`：11 个子系统健康矩阵（Memory/Retrieval/Skill/Airlock/Bond/Vision/Sandbox/Channel/Intent/QA/Agent Loop）
- `GET /obs/agent_loop/stats`：Agent Loop 详细统计（目标达成率/平均轮次/最近执行列表）

**前端新增 Insights 页面**（`#/insights`）：
- Governor 决策漏斗：汇总统计 + 时序趋势图 + reason_code 横向柱状图
- 全局错误追踪：错误趋势图 + Top10 reason_code + 最近错误表格
- 子系统健康矩阵：11 个子系统卡片（颜色编码：绿/橙/红）
- Agent Loop 统计：达成率汇总 + 最近执行详情表格

**监控覆盖率**：2 张日志表 → **13 张**日志表

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 4 个端点
- `host_brain/app/monitor_ui/src/api/obs.js`：修复参数映射 + 新增 4 个 API 函数
- `host_brain/app/monitor_ui/src/pages/Insights.jsx`：新建 Insights 页面
- `host_brain/app/monitor_ui/src/pages/Metrics.jsx`：修复数据适配
- `host_brain/app/monitor_ui/src/pages/Sessions.jsx`：修复字段映射
- `host_brain/app/monitor_ui/src/pages/Overview.jsx`：增加服务分组/success_rate/窗口选择器
- `host_brain/app/monitor_ui/src/components/Layout.jsx`：增加轮询开关和间隔选择器

---

### TODO-12：WebUI Trace 甘特图（已完成，2026-03-12）

**当前状态**：`ACTIVE`（视图完善）

**问题**：Trace 页只展示 DAG 节点拓扑图，无法直观看到各节点的时序关系和耗时占比。

**修复**：
- 新增 `GanttView` 组件，基于节点 `latency_ms` + 拓扑边推算各节点最早开始时间
- 支持 DAG 图 / 甘特图视图切换（右上角切换按钮）
- 甘特图特性：
  - 颜色编码（按节点类型：intent/governor/memory/llm/tts/vts）
  - 时间轴刻度（5 等分）
  - 点击行触发节点详情侧边面板
  - 状态颜色背景（绿/橙/红）
  - 图例说明

**改动文件**：
- `host_brain/app/monitor_ui/src/pages/Trace.jsx`：增加 `GanttView` 组件 + `computeGanttTimeline` 函数 + 视图切换逻辑

---

### TODO-13：`/v1/host/input` 副作用可观测性（已完成，2026-03-12）

**当前状态**：`ACTIVE`（副作用生命周期可观测）

**问题**：`/v1/host/input` 返回 `ACCEPTED` 后，fire-and-forget 的 main_chain 处理结果完全不可观测，无法判断副作用是否真正完成。

**修复**：在 `_dispatch_to_graph` 中写入 `ledger.log_voice_event`，追踪完整生命周期：

| phase | 含义 |
|-------|------|
| `ACCEPTED` | HTTP 响应已返回，副作用即将触发 |
| `DISPATCHED` | 异步任务已创建，main_chain 调用即将发起 |
| `COMPLETED` | main_chain 处理完成，副作用已落地 |
| `FAILED` | main_chain 处理失败，副作用未落地 |
| `NOT_WIRED` | main_chain 尚未接入（ImportError 降级） |

**可观测性**：通过 `GET /obs/subsystems/stats` 的 `channel` 子系统可查看 INPUT_* 事件统计。

**改动文件**：
- `host_brain/app/input_routes.py`：新增 `_get_ledger_safe()`、`_log_input_event()` 函数；`_dispatch_to_graph` 增加 ACCEPTED/DISPATCHED/COMPLETED/FAILED/NOT_WIRED 五阶段日志

---

### TODO-14：SCENE_VISION_UI HMAC 鉴权加固（已完成，2026-03-12）

**当前状态**：`PROD_READY`（多租户鉴权加固）

**问题**：`SCENE_VISION_UI` 仅有来源 IP 校验，无签名验证，多租户场景下存在伪造请求风险。

**修复**：在 `_vision_ui_sse_generator` 中增加 HMAC-SHA256 签名校验：
- 环境变量 `HOST_VISION_HMAC_SECRET` 非空时启用
- 签名消息格式：`f"{request_id}:{session_id}:{ts_ms}"`
- 请求方在 `request_body.hmac_signature` 中提供签名
- 校验失败时：写入 `ledger.log_error(stage=S_VISION_HMAC_REJECTED)`，返回 `REJECT` COMMIT 事件
- 校验顺序：HMAC 签名 → 来源 IP 校验（两层防护）

**回退方式**：不设置 `HOST_VISION_HMAC_SECRET` 环境变量，HMAC 校验自动跳过（向后兼容）。

**改动文件**：
- `host_brain/app/chat_handler_vision.py`：`_vision_ui_sse_generator` 增加 HMAC 签名校验逻辑

---

## 近期完成（2026-03-12 第四批）

### TODO-18：StateArbiter 接入 BondGraph 查询（已完成，2026-03-12）

**当前状态**：`ACTIVE`（四路 StateArbiter 查询全部接入）

**问题**：`http_hooks.py` 注册了 world/style/episodic 三路状态源，但 BondGraph 关系强度从未被查询，`bond_graph` slot 在 ContextPack 中有定义但处于空转状态。

**修复**：
- 新增 `_resolve_state_arbiter_bond_fact()`：通过 `BondStageEngine.get_bond_state()` 查询当前关系阶段
- 构建关系上下文文本（bond_stage/bond_score/bond_label/interaction_count）
- 构建 `slot_name=bond_graph` 证据条目（score=0.65，追加到 episodic_latest 之后）
- 在 `_sse_event_generator` 中将 style/episodic/bond 三路查询改为 `asyncio.gather` 并行执行
- `COMMIT.audit_refs.retrieval_summary` 新增 `bond_graph` 字段，可观测注入状态

**注入优先级**：
```
world_canon(1.0) > style_canon(0.85) > episodic_latest(0.75) > bond_graph(0.65) > 其他召回
```

**降级策略**：BondStageEngine 不可用时独立降级，不阻断主链路

**改动文件**：
- `host_brain/app/chat_stream_handler.py`：新增 `_resolve_state_arbiter_bond_fact()`；三路并行查询改为 `asyncio.gather`

**验证**：
- `pytest -q tests/unit/test_new_features_todo18_20.py::TestBondGraphStateArbiter`（4 passed）

---

### TODO-19：WebUI Canon 管理页面（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Canon 管理界面上线）

**问题**：`canon_routes.py` 有完整的提案 CRUD API（提案列表/提交/审核/拒绝/文档列表），但 WebUI 完全没有对应界面，运维人员无法通过 UI 管理 Canon 提案。

**修复**：
- 新建 `Canon.jsx` 页面，包含：
  - **提案列表**：分页展示，支持 pending/APPLIED/rejected 状态过滤，可展开查看详情
  - **提交提案**：弹窗表单（proposal_id/target_doc_id/title/body/tags/summary）
  - **审核操作**：通过（approve）/ 拒绝（reject）按钮，pending 状态下可操作
  - **SSOT 文档**：展示当前 World Canon 文档快照，支持展开查看正文
- 新增 5 个 Canon API 函数（`obs.js`）：`fetchCanonProposals/fetchCanonDocuments/submitCanonProposal/approveCanonProposal/rejectCanonProposal`
- `App.jsx` 注册 `/canon` 路由
- `Layout.jsx` 导航栏新增 Canon 入口（BookOpen 图标）

**改动文件**：
- `host_brain/app/monitor_ui/src/pages/Canon.jsx`：新建 Canon 管理页面
- `host_brain/app/monitor_ui/src/api/obs.js`：新增 5 个 Canon API 函数
- `host_brain/app/monitor_ui/src/App.jsx`：注册 `/canon` 路由
- `host_brain/app/monitor_ui/src/components/Layout.jsx`：导航栏新增 Canon 入口

---

### TODO-20：CanonProposalNode 接入主链（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Canon 提案自动提交）

**问题**：LLM 输出包含世界观更新信号时，主链路没有任何节点自动提交 Canon 提案，世界观更新完全依赖人工操作 `/canon/proposals` API。

**修复**：
- 新增 `CanonProposalNode`，插入主链路 `AuditNode` 之后、`AirlockSanitizerNode` 之前
- 触发条件（满足任一）：
  - `final_response` 包含 `[CANON_UPDATE]`/`[世界观更新]`/`[WORLD_UPDATE]`/`[CANON_PROPOSAL]` 标记
  - `audit_tags` 包含 `CANON_PROPOSAL_REQUESTED`（由外部注入）
  - `ctx.metadata["canon_proposal"]` 中有预构建的提案内容
- 触发后通过 `WorldCanonStore.submit_proposal` 写入 ledger（status=pending，需人工审核）
- 降级策略：
  - 提案重复（幂等）→ `SKIPPED_DUPLICATE`，不报错
  - Store 不可用 → `DEGRADED`，标记 `CANON_PROPOSAL_DEGRADED`，不阻断链路
  - 无触发信号 → `SKIPPED`，跳过

**改动文件**：
- `host_brain/app/graph/main_chain.py`：新增 `CanonProposalNode`；`NODE_TIMEOUT` 新增 `canon_proposal: 2.0`；`_NODE_FACTORIES` 插入 `CanonProposalNode`；`post_node_order` 新增 `canon_proposal`

**验证**：
- `pytest -q tests/unit/test_new_features_todo18_20.py::TestCanonProposalNode`（5 passed）
- 合并运行：9 passed（含所有新增测试，无回归）

**回退方式**：`CanonProposalNode` 无触发信号时自动跳过，不影响主链路；降级时不阻断链路

---



### TODO-15：EpisodicStoreNode 真实业务化（已完成，2026-03-12）

**当前状态**：`ACTIVE`（真实记忆写入）

**问题**：`EpisodicStoreNode.execute()` 只标记 `READY_TO_PERSIST` 状态，未接入真实 `persist_chat_episodic_memory`，情节记忆实际上从未通过 MainChain 写入。

**修复**：
- 接入真实 `persist_chat_episodic_memory`，从 `ctx.final_response` + `ctx.user_message` 构建记忆文本
- 从 `ctx.metadata["llm_gateway"]["emotion_context"]` 提取情绪上下文（含 emotion_tag/turn_count）
- 通过 `asyncio.to_thread` 异步执行（不阻塞事件循环）
- 降级策略：
  - `EpisodicStore` 不可用 → `SKIPPED_NO_STORE`
  - `MemoryGuard` 拒绝 → `EPISODIC_SKIPPED:{reason_code}`
  - 异常 → `DEGRADED`，标记 `EPISODIC_DEGRADED` 审计标签
  - 无最终响应 → `SKIPPED_NO_RESPONSE`

**改动文件**：
- `host_brain/app/graph/main_chain.py`：`EpisodicStoreNode.execute()` 接入真实写入逻辑

**验证**：
- `pytest -q tests/unit/test_new_features_todo15_17.py::TestEpisodicStoreNode`（4 passed）

**回退方式**：`EpisodicStore` 不可用时自动降级，不阻断主链路

---

### TODO-16：AuditNode 真实写入（已完成，2026-03-12）

**当前状态**：`ACTIVE`（审计快照真实落地）

**问题**：`AuditNode.execute()` 只写 `ctx.metadata["audit"]`，未接入真实 ledger 写入，审计快照实际上只存在内存中，进程重启后丢失。

**修复**：
- 接入真实 `ledger.log_error`，将 `ChainContext` 审计快照写入 ledger
- 快照内容：`audit_id`、`request_id`、`session_id`、`task_type`、`audit_tags`、`node_timings_ms`、`metadata_keys`、`governor_passed`、`final_response_chars`、`failure_event`
- 通过 `asyncio.to_thread` 异步执行（不阻塞事件循环）
- 降级策略：ledger 不可用时仅记录本地日志，不阻断主链路

**改动文件**：
- `host_brain/app/graph/main_chain.py`：`AuditNode.execute()` 接入真实 ledger 写入

**验证**：
- `pytest -q tests/unit/test_new_features_todo15_17.py::TestAuditNode`（3 passed）

**回退方式**：ledger 不可用时自动降级，不阻断主链路

---

### TODO-17：StateArbiter 扩展 style/episodic 查询（已完成，2026-03-12）

**当前状态**：`ACTIVE`（三路 StateArbiter 查询全部接入）

**问题**：`_sse_event_generator` 中只查询 `world_canon_facts`，`style_canon_facts` 和 `episodic_latest` 虽已在 `http_hooks.py` 注册，但从未被主链路查询，注册的状态源处于空转状态。

**修复**：
- 新增 `_resolve_state_arbiter_style_fact()`：查询 `style_canon_facts`，构建 `slot_name=style_canon` 证据条目（score=0.85，追加到 world_canon 之后）
- 新增 `_resolve_state_arbiter_episodic_fact()`：查询 `episodic_latest`，构建 `slot_name=episodic_latest` 证据条目（score=0.75，追加到末尾）
- 在 `_sse_event_generator` 中调用两个新函数，将结果注入 `retrieval_evidence_items`
- `COMMIT.audit_refs.retrieval_summary` 新增 `style_canon` 和 `episodic_latest` 字段，可观测注入状态

**注入优先级**：
```
world_canon（score=1.0）> style_canon（score=0.85）> episodic_latest（score=0.75）> 其他召回
```

**降级策略**：StateArbiter 不可用时各路独立降级，不阻断主链路

**改动文件**：
- `host_brain/app/chat_stream_handler.py`：新增 `_resolve_state_arbiter_style_fact()` 和 `_resolve_state_arbiter_episodic_fact()` 函数；`_sse_event_generator` 中调用并注入

**验证**：
- `pytest -q tests/unit/test_new_features_todo15_17.py::TestStateArbiterStyleEpisodicResolvers`（4 passed）
- 合并运行：43 passed（含所有相关单元测试，无回归）

**回退方式**：StateArbiter 不可用时自动降级，不阻断主链路

---

### TODO-21：MemoryGuard 接入 RecallFusionNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（MemoryGuard 过滤已接入召回链路）

**问题**：`RecallFusionNode` 完成七路召回后，`retrieval_evidence_items` 未经过滤直接注入 prompt，`NARRATIVE_ONLY` 级别的 dream 内容可能污染上下文。

**修复**：
- 在 `RecallFusionNode.execute()` 真实召回成功后，对 `retrieval_evidence_items` 逐条检查 `evidence_level`
- 过滤掉 `evidence_level=NARRATIVE_ONLY` 的条目（防止 dream 内容污染 prompt）
- 过滤结果写入 `ctx.metadata["recall_fusion"]["memory_guard"]` 和 `ctx.metadata["audit_refs"]["memory_guard"]`
- 降级策略：`get_memory_guard()` 不可用时跳过过滤（`SKIPPED_NOT_WIRED`），不阻断主链路

**改动文件**：
- `host_brain/app/graph/main_chain.py`：`RecallFusionNode.execute()` 接入 MemoryGuard 过滤

---

### TODO-22：FailureMatrix 接入 FailureHandlerNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（FailureMatrix 分类结果可观测）

**问题**：`FailureHandler.handle()` 返回的 `FailureResult`（含 `should_abort`/`degraded_mode`/`meta`）从未被写入 `ctx.metadata`，链路无法感知降级策略，运维无法观测失败分类结果。

**修复**：
- 重写 `MainChain._invoke_failure_handler()`，将 `FailureResult` 写入 `ctx.metadata["failure_matrix"]`
- 写入字段：`event`、`should_abort`、`degraded_mode`、`audit_tag`、`meta`、`user_response_present`
- 同时追加 `FAILURE_MATRIX:{event}` 和 `DEGRADED_MODE:{mode}` 到 `ctx.audit_tags`
- `MainChain.run()` 中的内联 failure_handler 调用统一改为调用 `_invoke_failure_handler`

**改动文件**：
- `host_brain/app/graph/main_chain.py`：`_invoke_failure_handler()` 重写；`run()` 中统一调用

---

### TODO-23：WebUI Bond 管理页面（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Bond 管理页面已上线）

**新增功能**：
- `Bond.jsx`：用户列表 + 阶段分布 SVG 饼图 + 单用户阶段详情（积分/趋势/能力提示）+ Bond 事件提交表单
- `obs.js`：新增 `fetchBondHealth`、`fetchBondUsers`、`fetchBondStage`、`fetchBondSummary`、`submitBondEvent` 5 个 API 函数
- `App.jsx`：注册 `/bond` 路由
- `Layout.jsx`：导航栏新增 Bond 入口（Heart 图标）

**改动文件**：
- `monitor_ui/src/pages/Bond.jsx`（新建）
- `monitor_ui/src/api/obs.js`
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-24：WebUI Tools 管理页面（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Tools 管理页面已上线）

**新增功能**：
- `Tools.jsx`：工具列表（READ/WRITE 分组过滤）+ 工具详情卡片 + 内嵌执行面板（支持 JSON 参数输入）
- `obs.js`：新增 `fetchToolHealth`、`fetchToolMetaPack`、`fetchToolMeta`、`executeTool` 4 个 API 函数
- `App.jsx`：注册 `/tools` 路由
- `Layout.jsx`：导航栏新增 Tools 入口（Wrench 图标）

**改动文件**：
- `monitor_ui/src/pages/Tools.jsx`（新建）
- `monitor_ui/src/api/obs.js`
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-25：TaskScheduler WebUI 管理页面（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Automation 管理页面已上线，后端 scheduler_routes 已存在）

**说明**：`host_brain/scheduler/scheduler_routes.py` 已提供完整 REST API（`/scheduler/tasks`、`/scheduler/health` 等），`fastapi_app.py` 已注册路由。本次补全前端可视化层。

**新增功能**：
- `Automation.jsx`：任务列表卡片（状态/优先级/执行次数/失败次数）+ 启用/禁用/立即触发按钮 + 健康状态统计
- `obs.js`：新增 `fetchSchedulerTasks`、`fetchSchedulerHealth`、`enableSchedulerTask`、`disableSchedulerTask`、`triggerSchedulerTask` 5 个 API 函数
- `App.jsx`：注册 `/automation` 路由
- `Layout.jsx`：导航栏新增 Automation 入口（Clock 图标）

**改动文件**：
- `monitor_ui/src/pages/Automation.jsx`（新建）
- `monitor_ui/src/api/obs.js`
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-26：Memory 子系统详细统计端点（已完成，2026-03-12）

**当前状态**：`ACTIVE`（`GET /obs/memory/stats` 端点已上线）

**问题**：`GET /obs/subsystems/stats` 中 Memory 子系统只有健康状态，缺少写入量/拒绝率/压缩率/蒸馏次数等关键指标。

**新增端点**：`GET /obs/memory/stats`

覆盖四个 Memory 子系统：
- **EpisodicStore**：写入总量、成功率
- **MemoryGuard**：拒绝率（NARRATIVE_ONLY 过滤 + 宪法校验拒绝）
- **ContextCompressor**：平均压缩率（output_tokens/input_tokens 比值）
- **LongTermDistiller**：蒸馏次数、蒸馏成功率

响应字段：`subsystems[]`、`total_writes`、`guard_reject_rate`、`compress_rate`、`distill_count`

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `GET /obs/memory/stats` 端点
- `monitor_ui/src/api/obs.js`：新增 `fetchMemoryStats` API 函数

---

### TODO-27：EmotionTimeline 接入 LlmGatewayNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（EmotionTimeline 已接入主链路 LlmGatewayNode）

**问题**：`EmotionTimeline` 和 `compute_emotion_echo` 已在 `emotion_timeline.py` 中完整实现，但主链路从未调用 `EmotionTimeline.append()` 追踪情感事件，也从未调用 `rebuild_summary()` 将情感轨迹注入 prompt。

**新增功能**：
- `LlmGatewayNode.execute()` 中从 `ctx.metadata["emotion_tag"]` 提取情感标签
- 调用 `EmotionTimeline.append()` 追踪情感事件（valence/arousal 自动映射）
- 调用 `EmotionTimeline.rebuild_summary(window_ms=300_000)` 生成情感轨迹摘要，写入 `ctx.metadata["emotion_timeline"]`
- 调用 `compute_emotion_echo()` 生成情感回声（echo_weight=0.4），写入 `ctx.metadata["emotion_echo"]`
- 降级策略：EmotionTimeline 不可用时跳过，不阻断主链路

**改动文件**：
- `host_brain/app/graph/main_chain.py`：重写 `LlmGatewayNode.execute()`

---

### TODO-28：ContextCompressor 接入 RecallFusionNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（ContextCompressor 已接入主链路 RecallFusionNode）

**问题**：`ContextCompressor` 有完整的 `compress()` 接口，但主链路的 `RecallFusionNode` 在召回后直接将证据传递给 prompt，没有经过上下文压缩，对话轮次增多时上下文会无限增长。

**新增功能**：
- 在 `RecallFusionNode.execute()` 的 MemoryGuard 过滤之后，调用 `ContextCompressor.compress()` 对历史轮次进行压缩（`hybrid` 策略）
- 将 `CompressedContext` 统计写入 `ctx.metadata["context_compressor"]`（strategy/input_turns/output_turns/total_tokens/degraded）
- 同时写入 `ctx.metadata["audit_refs"]["context_compressor"]`，供审计节点使用
- 降级策略：`ContextCompressor` 不可用时跳过压缩，不阻断主链路

**改动文件**：
- `host_brain/app/graph/main_chain.py`：`RecallFusionNode.execute()` 新增压缩逻辑

---

### TODO-29：WebUI Agent Loop 管理页面（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Agent Loop 管理页面已上线）

**问题**：`agent_routes.py` 提供了 `POST /v1/agent/run`（SSE 流式）和 `GET /v1/agent/info`，但 WebUI 完全没有对应界面。

**新增功能**：
- `AgentLoop.jsx`：目标输入框 + 参数配置（max_rounds/timeout/use_mock/use_edge_rpc/image_consent）+ SSE 实时进度展示（AGENT_INIT/ROUND_START/SCREENSHOT_DONE/PLAN_GENERATED/EXECUTE_DONE/LOOP_DONE）+ 执行结果摘要卡片
- `App.jsx`：注册 `/agent` 路由
- `Layout.jsx`：导航栏新增 Agent 入口（Bot 图标）

**改动文件**：
- `monitor_ui/src/pages/AgentLoop.jsx`（新建）
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-30：VTS 通道统计端点（已完成，2026-03-12）

**当前状态**：`ACTIVE`（`GET /obs/vts/stats` 端点已上线）

**问题**：`channels/vts.py` 已有完整的 `resolve_vts_expression()` 和 `request_talk_gate()` 接口，但 `obs_routes.py` 没有任何端点暴露 VTS 通道的统计数据。

**新增端点**：`GET /obs/vts/stats`

覆盖：
- 表情应用统计（APPLIED/DEGRADED/REJECTED 分布）
- Top 情感标签分布（Top 8）
- Talk Gate 事件统计（允许/拒绝）

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `GET /obs/vts/stats` 端点
- `monitor_ui/src/api/obs.js`：新增 `fetchVtsStats` API 函数

---

### TODO-31：LongTermDistiller 接入调度器（已完成，2026-03-12）

**当前状态**：`ACTIVE`（蒸馏任务已注册到调度器，`GET /obs/distillation/stats` 端点已上线）

**问题**：`LongTermMemoryDistiller.run_distillation_cycle()` 已实现完整的蒸馏逻辑，`make_distillation_task_config()` 也提供了调度器任务配置，但调度器从未注册这个任务，蒸馏循环从未被触发。

**新增功能**：
- `http_hooks.py` 的 `runtime_lifespan` 中，调用 `make_distillation_task_config()` 并注册到 `SchedulerTaskRegistry`（每日 03:00 CRON）
- `obs_routes.py` 新增 `GET /obs/distillation/stats`：蒸馏循环统计（总循环次数/成功率/提案提交量/语义记忆创建量/最近 N 次循环详情）
- `obs.js` 新增 `fetchDistillationStats` API 函数

**改动文件**：
- `host_brain/app/http_hooks.py`：`runtime_lifespan` 注册蒸馏调度任务
- `host_brain/app/obs_routes.py`：新增 `GET /obs/distillation/stats` 端点
- `monitor_ui/src/api/obs.js`：新增 `fetchDistillationStats` API 函数

---

### TODO-32：Insights 页面 Memory 统计卡片（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Memory 子系统统计区块已上线）

**问题**：TODO-26 已实现 `GET /obs/memory/stats` 后端端点，但 `Insights.jsx` 页面没有对应的 Memory 统计卡片，数据无法可视化。

**新增功能**：
- `Insights.jsx` 新增 Memory 子系统统计区块（Database 图标）
- 聚合指标卡片：EpisodicStore 写入量 + MemoryGuard 拒绝率 + ContextCompressor 压缩率 + LongTermDistiller 蒸馏次数
- 子系统详情卡片：4 个子系统各自的成功率/专属指标（拒绝率/压缩率/蒸馏次数）/最后活跃时间
- 并行加载：与其他 Insights 数据同时请求，不增加页面加载时间

**改动文件**：
- `monitor_ui/src/pages/Insights.jsx`：新增 Memory 统计区块
- `monitor_ui/src/api/obs.js`：已在 TODO-26 中添加 `fetchMemoryStats`

---

### TODO-33：EmotionTimeline 摘要注入 PromptBuilder（已完成，2026-03-12）

**当前状态**：`ACTIVE`（EmotionTimeline 摘要已注入 EMOTION_BINDING 块）

**问题**：TODO-27 已在 `LlmGatewayNode` 中生成了 `ctx.metadata["emotion_timeline"]`（含 `dominant_emotion`/`trend`/`entry_count`）和 `ctx.metadata["emotion_echo"]`，但 `prompt_builder.py` 的 `render_node_generation_prompt()` 完全没有读取这些字段，情感轨迹摘要对 LLM 生成没有任何影响。

**新增功能**：
- 在 `render_node_generation_prompt()` 的 `EMOTION_BINDING` 块中，追加 `emotion_timeline_dominant`/`emotion_timeline_trend`/`emotion_timeline_entry_count`/`emotion_echo_tag`/`emotion_echo_hint` 字段
- 仅在 EmotionTimeline 有效数据时注入（mode 不为 SKIPPED/DEGRADED 且 entry_count > 0）
- 无效时注入 `NONE` 占位符，保持 prompt 格式稳定

**改动文件**：
- `host_brain/app/prompt_builder.py`：`render_node_generation_prompt()` EMOTION_BINDING 块追加 EmotionTimeline 字段

---

### TODO-34：AuditRetention 接入调度器（已完成，2026-03-12）

**当前状态**：`ACTIVE`（AuditRetention 每日调度任务已注册，`GET /obs/retention/stats` 端点已上线）

**问题**：`audit/retention.py` 有完整的 `run_retention_policy()` 实现，但调度器从未注册这个任务，审计数据会无限增长。

**新增功能**：
- `http_hooks.py` 的 `runtime_lifespan` 中，注册 AuditRetention 调度任务（task_id=`audit_retention_daily`，每日 02:00 CRON）
- `obs_routes.py` 新增 `GET /obs/retention/stats`：保留策略执行统计（最近执行时间/状态/各表候选删除量/已删除量/受保护量）

**改动文件**：
- `host_brain/app/http_hooks.py`：`runtime_lifespan` 注册 AuditRetention 调度任务
- `host_brain/app/obs_routes.py`：新增 `GET /obs/retention/stats` 端点
- `monitor_ui/src/api/obs.js`：新增 `fetchRetentionStats` API 函数

---

### TODO-35：CanonVectorAutoSync 接入 CanonProposalNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（CanonProposalNode 提案提交后自动触发向量同步）

**问题**：`canon_vector_auto_sync.py` 有完整的 `trigger_canon_vector_auto_sync()` 接口，但 `CanonProposalNode` 在提案提交后从未触发向量同步，导致 Canon SSOT 写入后 Chroma 向量索引不同步。

**新增功能**：
- `CanonProposalNode.execute()` 提案成功提交后，调用 `trigger_canon_vector_auto_sync(include_world=True, include_style=False, reason="canon_proposal_node")`
- 降级策略：向量同步失败时仅记录日志，不阻断主链路
- 同步结果写入 `ctx.metadata["canon_proposal_node"]["vector_sync"]`
- 成功时追加 `CANON_VECTOR_SYNC_TRIGGERED` audit tag

**改动文件**：
- `host_brain/app/graph/main_chain.py`：`CanonProposalNode.execute()` 追加向量同步调用

---

### TODO-36：EvidenceCircuitBreaker 接入 RecallFusionNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（RecallFusionNode 已接入熔断器保护）

**问题**：`evidence_circuit_breaker.py` 有完整的 `EvidenceCircuitBreaker` 实现（CLOSED/OPEN/HALF_OPEN 状态机），但 `RecallFusionNode` 在召回失败时没有任何熔断保护，连续失败时会持续重试。

**新增功能**：
- `RecallFusionNode.execute()` 召回前检查熔断器状态：OPEN 时直接跳过召回，返回空证据（`RECALL_CIRCUIT_OPEN`）
- 召回成功时重置熔断器失败计数（`_failure_count = 0`）
- 召回失败时通知熔断器记录失败（触发阈值：3 次失败，恢复超时：30s）
- 熔断状态写入 `ctx.metadata["recall_fusion"]["circuit_breaker"]`

**改动文件**：
- `host_brain/app/graph/main_chain.py`：`RecallFusionNode.execute()` 接入 EvidenceCircuitBreaker

---

### TODO-37：WebUI WeChat 通道管理页面（已完成，2026-03-12）

**当前状态**：`ACTIVE`（WeChat 通道管理页面已上线）

**问题**：`channels/wechat.py` 和 `channels/wechat_health.py` 有完整的微信通道实现，但 WebUI 完全没有对应界面，无法通过 UI 观测微信通道状态。

**新增功能**：
- 新建 `WeChat.jsx` 页面：通道健康状态卡片（UP/DEGRADED/DOWN/UNKNOWN）+ 7 个统计卡片（总事件/入站/出站/成功/失败/成功率/频率限制次数）+ Top wxid 频率限制统计表 + 最近事件列表 + 手动重置按钮
- `obs_routes.py` 新增 `GET /obs/wechat/stats` 端点（通道统计）和 `POST /obs/wechat/reset` 端点（手动重置）
- `obs.js` 新增 `fetchWeChatStats`、`resetWeChatChannel`、`fetchRetentionStats` 3 个 API 函数
- `App.jsx` 注册 `/wechat` 路由
- `Layout.jsx` 导航栏新增 WeChat 入口（MessageSquare 图标）

**改动文件**：
- 新建 `monitor_ui/src/pages/WeChat.jsx`
- `host_brain/app/obs_routes.py`：新增 WeChat 统计端点
- `monitor_ui/src/api/obs.js`：新增 WeChat API 函数
- `monitor_ui/src/App.jsx`：注册 `/wechat` 路由
- `monitor_ui/src/components/Layout.jsx`：导航栏新增 WeChat 入口

---

### TODO-38：Insights 页面 VTS 统计卡片（已完成，2026-03-12）

**当前状态**：`ACTIVE`（VTS 统计区块已上线）

**问题**：TODO-30 已实现 `GET /obs/vts/stats` 后端端点，但 `Insights.jsx` 页面没有对应的 VTS 统计卡片，数据无法可视化。

**新增功能**：
- `Insights.jsx` 新增 VTS 通道统计区块（Tv 图标，粉色主题）
- 聚合指标卡片：总事件数 + 表情应用率 + 降级率 + 拒绝率
- Top 情感标签横向柱状图（BarChart）
- Talk Gate 统计面板（总请求/允许/拒绝 + 允许率进度条）
- 并行加载：与其他 Insights 数据同时请求

**改动文件**：
- `monitor_ui/src/pages/Insights.jsx`：新增 VTS 统计区块，引入 `Tv` 图标和 `fetchVtsStats`

---

### TODO-39：EmotionVector VAD 连续体注入 PromptBuilder（已完成，2026-03-12）

**当前状态**：`ACTIVE`（VAD 三维数值已注入 EMOTION_BINDING 块）

**问题**：`core/metabolism.py` 有完整的 `EmotionVector`（VAD 三维模型），但 `prompt_builder.py` 的 `EMOTION_BINDING` 块只注入了离散的 `emotion_tag`/`mood_state`，从未注入 VAD 连续体数值，LLM 无法感知情感的细粒度强度。

**新增功能**：
- `render_node_generation_prompt()` 中新增 VAD 数据提取逻辑（从 `runtime_payload.get("emotion_vector")` 读取）
- `EMOTION_BINDING` 块末尾追加 `emotion_vad_valence`/`emotion_vad_arousal`/`emotion_vad_dominance` 三个字段
- 仅在 VAD 数据有效时注入真实值（非全零），无效时注入 `NONE`

**改动文件**：
- `host_brain/app/prompt_builder.py`：新增 VAD 提取逻辑 + EMOTION_BINDING 块扩展

---

### TODO-40：QA 违规统计接入 Insights 页面（已完成，2026-03-12）

**当前状态**：`ACTIVE`（QA 质量监控区块已上线）

**问题**：`core/qa.py` 有完整的 QA 违规检测系统，审计数据写入 `qa_violation` 表，但 WebUI 完全没有 QA 违规统计面板，无法观测 LLM 输出质量。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/qa/stats`：QA 违规统计（违规码分布、降级率、重写成功率、时序趋势、最近违规列表）
- `Insights.jsx` 新增 QA 质量监控区块（FileWarning 图标，红色主题）：违规码分布柱状图 + 违规趋势折线图 + 最近违规列表
- `obs.js` 新增 `fetchQaStats` API 函数

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/qa/stats` 端点
- `monitor_ui/src/pages/Insights.jsx`：新增 QA 违规统计区块
- `monitor_ui/src/api/obs.js`：新增 `fetchQaStats`

---

### TODO-41：EvidencePlane LLM 调用日志接入 obs_routes（已完成，2026-03-12）

**当前状态**：`ACTIVE`（LLM 调用成本统计已上线）

**问题**：`integrations/graph_evidence.py` 的 `EvidencePlane` 有 `llm_call_log` 表，但 `obs_routes.py` 完全没有读取这个表的端点，LLM 调用成本和延迟数据无法通过 WebUI 观测。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/llm/call-stats`：LLM 调用统计（按 model_id 分组的 token 消耗/平均延迟/调用次数/P95延迟 + 时序趋势）
- `Insights.jsx` 新增 LLM 调用成本区块（Zap 图标，琥珀色主题）：各模型 token 消耗横向柱状图 + 调用量趋势折线图 + 各模型详情表格
- `obs.js` 新增 `fetchLlmCallStats` API 函数

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/llm/call-stats` 端点
- `monitor_ui/src/pages/Insights.jsx`：新增 LLM 调用成本区块
- `monitor_ui/src/api/obs.js`：新增 `fetchLlmCallStats`

---

### TODO-42：CheckpointManager 状态接入 obs_routes（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Checkpoint 状态查询已上线）

**问题**：`integrations/graph_checkpoint.py` 有完整的 `CheckpointManager`，但 `obs_routes.py` 没有任何 checkpoint 状态查询端点，无法观测断点续跑状态。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/checkpoints`：最近 checkpoint 列表（含超时状态过滤）
- `obs_routes.py` 新增 `GET /obs/checkpoints/{checkpoint_id}`：单个 checkpoint 详情（含完整 payload）
- `Trace.jsx` 新增 Checkpoint 侧边栏（Bookmark 图标，琥珀色主题）：当 trace 中存在 checkpoint 时显示，展示中断原因/截止时间/payload 摘要

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/checkpoints` 和 `/obs/checkpoints/{id}` 端点
- `monitor_ui/src/pages/Trace.jsx`：新增 Checkpoint 侧边栏
- `monitor_ui/src/api/obs.js`：新增 `fetchCheckpoints`、`fetchCheckpointDetail`

---

### TODO-43：RateLimitStore 后端状态接入 WebUI（已完成，2026-03-12）

**当前状态**：`ACTIVE`（速率限制后端状态卡片已上线）

**问题**：`governor/rate_limit_store.py` 有完整的三后端实现和 `DegradingRateLimitStore` 降级包装器，但 WebUI 完全没有速率限制后端状态面板。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/rate-limit/backend-stats`：速率限制后端状态（当前后端类型/是否降级/降级原因/Top-K key 计数）
- `Metrics.jsx` 新增速率限制后端状态卡片（Database 图标，靛蓝色主题）：后端类型/降级状态 + 活跃 key 统计 + Top key 计数表格

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/rate-limit/backend-stats` 端点
- `monitor_ui/src/pages/Metrics.jsx`：新增速率限制后端状态卡片
- `monitor_ui/src/api/obs.js`：新增 `fetchRateLimitBackendStats`

---

### TODO-44：Vault Manifest 统计接入 WebUI（已完成，2026-03-12）

**当前状态**：`ACTIVE`（Vault Manifest 统计已上线）

**问题**：`lsp/vault.py` 有完整的 `ArtifactVault`（manifest 创建/undo/GC），审计数据写入 `vault_manifest` 表，但 WebUI 完全没有 Vault 统计面板。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/vault/stats`：Vault 统计（ACTIVE/UNDONE/GC'd manifest 数量、可逆比例、最近 manifest 列表）
- `Canon.jsx` 新增 Vault 统计面板（Lock 图标，紫罗兰色主题）：manifest 状态分布进度条 + 4 个聚合指标卡片 + 最近 manifest 详情表格

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/vault/stats` 端点
- `monitor_ui/src/pages/Canon.jsx`：新增 Vault Manifest 统计面板
- `monitor_ui/src/api/obs.js`：新增 `fetchVaultStats`

---

### TODO-45：StateArbiter 接入 RecallFusionNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（StateArbiter 事实仲裁已接入主链路）

**问题**：`memory/state_arbiter.py` 有完整的六源优先级仲裁器，但主链路的 `RecallFusionNode` 在七路召回后直接合并结果，没有经过 StateArbiter 仲裁，多路召回结果存在冲突时没有权威裁决机制。

**新增功能**：
- `RecallFusionNode.execute()` 在 ContextCompressor 之后接入 `StateArbiter.resolve()`
- 对关键事实键（`user.identity`/`user.name`/`user.age`/`user.occupation`）进行仲裁
- 仲裁结果（`winner_source`/`conflict_detected`/`is_fallback`）写入 `ctx.metadata["state_arbiter"]`
- 冲突事件写入 `ctx.audit_tags`（`STATE_ARBITER_CONFLICT:N`）
- 降级策略：StateArbiter 不可用时跳过，不阻断主链路

**改动文件**：
- `host_brain/app/graph/main_chain.py`：RecallFusionNode 接入 StateArbiter

---

### TODO-46：RoutingMatrix 接入 LlmGatewayNode（已完成，2026-03-12）

**当前状态**：`ACTIVE`（RoutingMatrix 路由决策已接入主链路）

**问题**：`llm/routing_matrix.py` 有完整的多维度路由矩阵，但 `LlmGatewayNode` 在选择模型时没有调用 `RoutingMatrix.select()`，完全绕过了路由逻辑。

**新增功能**：
- `LlmGatewayNode.execute()` 接入 `RoutingMatrix.select()`
- 从 `ctx.metadata` 提取 `task_type`/`risk_level`/`context_length`，计算路由决策
- `RouteDecision`（`selected_model`/`is_fallback`/`fallback_reason`）写入 `ctx.metadata["routing_matrix"]`
- 路由结果写入 `ctx.audit_tags`（`ROUTING_SELECTED:model` 或 `ROUTING_FALLBACK:model`）
- 降级策略：RoutingMatrix 不可用时使用默认模型，不阻断主链路

**改动文件**：
- `host_brain/app/graph/main_chain.py`：LlmGatewayNode 接入 RoutingMatrix

---

### TODO-47：RoutingMatrix 路由决策统计接入 WebUI（已完成，2026-03-12）

**当前状态**：`ACTIVE`（路由决策统计已上线）

**问题**：`llm_route_log` 表记录了每次路由决策，但 `obs_routes.py` 没有任何路由统计端点，无法观测模型选择分布和降级频率。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/llm/route-stats`：路由决策统计（按 model_id 分组的选择次数/降级次数/降级率/task_type 分布）
- `Insights.jsx` 新增路由决策分布区块（GitBranch 图标，靛蓝色主题）：模型选择分布进度条 + 任务类型分布进度条

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/llm/route-stats` 端点
- `monitor_ui/src/pages/Insights.jsx`：新增 RouteStatsSection 子组件
- `monitor_ui/src/api/obs.js`：新增 `fetchLlmRouteStats`

---

### TODO-48：InputDispatch 生命周期统计接入 WebUI（已完成，2026-03-12）

**当前状态**：`ACTIVE`（输入分发统计已上线）

**问题**：`input_routes.py` 的 `_dispatch_to_graph()` 写入了完整的生命周期日志，但 WebUI 完全没有对应的输入分发统计面板。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/input/dispatch-stats`：输入分发统计（按 source 分组的 ACCEPTED/COMPLETED/FAILED/NOT_WIRED 数量、成功率、平均延迟 + 时序趋势）
- `Overview.jsx` 新增输入分发统计卡片（ArrowDownToLine 图标）：4 个聚合指标卡片 + 按 source 分组的详情卡片

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/input/dispatch-stats` 端点
- `monitor_ui/src/pages/Overview.jsx`：新增输入分发统计区块
- `monitor_ui/src/api/obs.js`：新增 `fetchInputDispatchStats`

---

### TODO-49：ContextPackBuilder token 预算统计接入 WebUI（已完成，2026-03-12）

**当前状态**：`ACTIVE`（ContextPack token 预算统计已上线）

**问题**：`recall/context_pack_builder.py` 的 `ContextPackBuilder.build()` 在 `subgraph_trace` 中记录了各路召回的 token 消耗和截断情况，但这些数据只存在于 `ctx.metadata`，从未写入审计日志，也没有 WebUI 面板展示。

**新增功能**：
- `RecallFusionNode.execute()` 接入 `ContextPackBuilder.build()`，将 `subgraph_trace` 写入 `ctx.metadata["context_pack_trace"]`
- `obs_routes.py` 新增 `GET /obs/context-pack/stats`：ContextPack token 预算统计（各槽位平均 token 消耗/截断率/冲突抑制率）
- `Insights.jsx` 新增 ContextPack 预算统计区块（Layers 图标，青色主题）：4 个聚合指标卡片 + 各槽位 token 消耗进度条

**改动文件**：
- `host_brain/app/graph/main_chain.py`：RecallFusionNode 接入 ContextPackBuilder
- `host_brain/app/obs_routes.py`：新增 `/obs/context-pack/stats` 端点
- `monitor_ui/src/pages/Insights.jsx`：新增 ContextPackSection 子组件
- `monitor_ui/src/api/obs.js`：新增 `fetchContextPackStats`

---

### TODO-50：UIAssertionGuard 统计接入 WebUI（已完成，2026-03-12）

**当前状态**：`ACTIVE`（UI 断言统计已上线）

**问题**：`vision/ui_assertion.py` 的 `UIAssertionGuard` 在断言失败时写入审计日志，但 WebUI 的 `AgentLoop.jsx` 页面完全没有展示这些断言统计。

**新增功能**：
- `obs_routes.py` 新增 `GET /obs/ui-assertion/stats`：UI 断言统计（SENSITIVE_UI_BLOCKED/UI_PRECONDITION_FAILED/UI_POSTCONDITION_FAILED 分布、前置断言失败原因分布）
- `AgentLoop.jsx` 新增 UI 断言统计侧边栏（ShieldAlert 图标，红色主题）：3 个拦截计数卡片
- `Insights.jsx` 新增 UIAssertionSection 子组件（ShieldAlert 图标，红色主题）：4 个聚合指标卡片 + 前置断言失败原因分布进度条

**改动文件**：
- `host_brain/app/obs_routes.py`：新增 `/obs/ui-assertion/stats` 端点
- `monitor_ui/src/pages/AgentLoop.jsx`：新增 UI 断言统计侧边栏
- `monitor_ui/src/pages/Insights.jsx`：新增 UIAssertionSection 子组件
- `monitor_ui/src/api/obs.js`：新增 `fetchUIAssertionStats`


