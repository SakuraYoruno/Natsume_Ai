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

## 6. 仍未解决的问题与后续建议
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

补充要求：
- 直接修改工作区文件，不要只停留在分析
- 优先做最小安全改动，不做无关重构
- 修改后先跑相关测试；测不过时先修到通过
- 最终给我的是“实际修改结果 + 修订报告”，不是下一步计划

现在开始执行。

先遍历并审计相关代码文件，再实施最小必要修改，再运行测试，最后按要求输出完整结果。
