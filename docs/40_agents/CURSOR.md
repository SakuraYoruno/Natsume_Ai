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

# 后续待处理任务（严格审计后遗留，2026-03-11 18:12）

> 以下任务来自严格代码审计（见 `docs/dataflow_report_20260311.md` 第十六节 §16.7）。
> 所有任务均以代码真实执行语义为准，不允许把 TODO 写成"已解决"。

## P1（高优先级）

### TODO-1：ContextPackBuilder 升级为 ACTIVE

**当前状态**：`SHADOW`
- `_cpb.build()` 的结果只写入 `audit_refs.context_pack_builder`，不影响 `_build_stream_chat_prompt` 的 `retrieval_evidence_items`
- 证据：`chat_stream_handler.py::_sse_event_generator`，`context_pack_builder_summary` 只写入 `audit_refs`

**目标**：将截断结果回写到 `retrieval_evidence_items`，使 token 预算控制真正影响 prompt 构建

**最小改动方案**：
```python
# chat_stream_handler.py::_sse_event_generator
# 在 _cpb.build() 之后，将截断结果回写
if _cp and _cp.slots:
    _truncated_items = []
    for slot_name, slot in _cp.slots.items():
        if slot and slot.content:
            _truncated_items.append({
                "slot_name": slot_name,
                "text": slot.content,
                "source": slot_name,
            })
    if _truncated_items:
        retrieval_evidence_items = _truncated_items
```

**前置条件**：
1. 先在 SHADOW 模式下观察 `audit_refs.context_pack_builder` 数据，确认截断行为符合预期
2. 验证 `slot.content` 格式与 `_build_stream_chat_prompt` 期望的 `retrieval_evidence_items` 格式兼容

**风险**：截断过激可能丢失重要召回结果

**回退方式**：添加 `HOST_CONTEXT_PACK_ACTIVE` 环境变量开关（当前代码无此开关，需同步添加）

---

### TODO-2：`/v1/edge/events` 添加事件类型白名单

**当前状态**：无事件类型枚举约束，无来源校验
- 证据：`system_handler.py::receive_edge_events_response`，`event_type` 字段无白名单过滤

**目标**：添加最小安全边界，防止非法事件类型注入

**最小改动方案**：
```python
# system_handler.py
ALLOWED_EDGE_EVENT_TYPES = frozenset({
    "ASR_FINAL", "ASR_PARTIAL", "EMOTION_DETECTED", "USER_REACTION",
    "WECHAT_MSG_RECEIVED", "USER_INPUT_FINAL", "TTS_STARTED", "TTS_DONE",
    "VTS_EXPRESSION_SET", "EDGE_HEALTH_PING",
})

# 在 receive_edge_events_response 入口处校验
if event.event_type not in ALLOWED_EDGE_EVENT_TYPES:
    raise HTTPException(status_code=400, detail=f"Unknown event_type: {event.event_type}")
```

**安全前提**（当前暂不加鉴权的前提）：
1. Host Brain 仅部署在内网，不暴露公网
2. Edge Client 与 Host Brain 在同一受信任网络
3. 无多租户场景（单用户部署）

---

## P2（中优先级）

### TODO-3：RoutingMatrix 升级为 ACTIVE

**当前状态**：`SHADOW`
- `select_with_fallback()` 结果写入 `routing_matrix_decision`，但不影响实际 `chat_provider_name/model_id` 选择（仍由 8B 健康探测决定）
- 证据：`graph_config_builder.py::_resolve_host_llm_runtime_core`

**目标**：将 `_decision.selected_model` 映射回 `chat_provider_name/model_id`，使路由矩阵真正影响模型选择

**注意**：需保留 8B 健康探测作为 fallback，不能直接替换

---

### TODO-4：StateArbiter 接入业务查询

**当前状态**：`WIRED`
- 3 个状态源已注册（WorldCanon/StyleCanon/EpisodicStore），但全局无任何 `resolve()` 调用
- 证据：`http_hooks.py::runtime_lifespan`，全局搜索无 `arbiter.resolve(` 调用

**推荐方案（方案 B）**：
- 在 `_build_stream_chat_prompt` 中调用 `StateArbiter.resolve("world_canon_facts", session_id)` 获取世界观事实
- 将结果注入 `retrieval_summary` 的 `world_canon` 槽位
- 接入后补测试

**保守方案（方案 A）**：
- 不改主业务逻辑，保持 `WIRED` 状态
- 仅修正文档口径，不得写成"已完成接入"

---

## P3（低优先级）

### TODO-5：SCENE_VISION_UI E2E 联调验证

**当前状态**：`WIRED`（非 `E2E_VERIFIED`）
- 白名单已加入，`_vision_ui_sse_generator` 完整实现（Gemini Vision 多模态）
- 但无 E2E 联调证据
- 证据：`graph_config_builder.py::ALLOWED_EXPLICIT_SCENE_TYPES`，`chat_handler_vision.py::_vision_ui_sse_generator`

**验证步骤**：
1. 启动 Host Brain + Edge Client 双端
2. 发送 `scene_type=SCENE_VISION_UI` + `image_upload_consent=true` 请求
3. 验证 Gemini Vision 分析链路完整执行
4. 确认 `audit_refs.vision_input` 真实写入
5. 验证无 consent 的降级逻辑成立

**升级条件**：完成上述 5 步验证后，可将等级升为 `E2E_VERIFIED`

---

### TODO-6：MainChain 骨架节点填充

**当前状态**：`WIRED`（适配层）
- 骨架节点全是 `return True`，`run_as_sse_adapter` 只透传给 `_unified_chat_sse_generator`
- 证据：`main_chain.py::MainChain._NODE_FACTORIES`，各节点 `execute()` 直接 `return True`

**目标**：填充真实业务逻辑，使 MainChain 成为真正的编排器而非透传适配层

**建议最小改动**：先补充节点级审计追踪（写入 `ChainContext.node_timings`），再逐步填充各节点业务逻辑

---

## 完成标准

| 任务 | 完成标准 | 升级后等级 |
|------|---------|-----------|
| TODO-1 | `retrieval_evidence_items` 真实使用截断结果，有测试验证 | `ACTIVE` |
| TODO-2 | 白名单校验上线，非法事件类型返回 400 | `ACTIVE`（安全边界完整） |
| TODO-3 | 路由矩阵决策真实影响模型选择，有 fallback | `ACTIVE` |
| TODO-4 | 至少一条业务路径调用 `resolve()`，有测试 | `ACTIVE` |
| TODO-5 | 双端 E2E 联调通过，有截图/日志证据 | `E2E_VERIFIED` |
| TODO-6 | 骨架节点填充真实逻辑，`run_as_sse_adapter` 不再只是透传 | `ACTIVE` |
