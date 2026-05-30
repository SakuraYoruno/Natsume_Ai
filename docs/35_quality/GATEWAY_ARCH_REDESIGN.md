受众：CODEX
标题：Gateway 架构重设计方案（GATEWAY-ARCH-REDESIGN）

# Gateway 架构重设计方案（GATEWAY-ARCH-REDESIGN）

**文档编号**：GATEWAY-ARCH-REDESIGN-001
**日期**：2026-04-15
**版本**：v1.0（草稿，待微调）
**状态**：质量层整改方案留痕（2026-04-19 已校准，不作为当前主链定义）
**受众**：核心开发团队

> 当前代码校准说明（2026-04-19）
> - 本文是 2026-04-15 的 Gateway 架构债盘点与整改方案，不是当前 Gateway 主链事实定义。
> - 当前 Gateway 真实链路应以 `edge_client/gateway/server.py -> GatewayRouter -> /v1/host/input -> host_brain/app/input_routes.py:receive_input() -> _run_cortex_orchestrator_pipeline() -> host_brain/channels/edge_rpc_client.py:post_gateway_reply() -> /v1/gateway/reply` 为准。
> - 文中对 `pre_intent_classifier`、`build_gateway_intent_draft`、ASR/Gateway 双路径割裂、TTS/VTS 抑制等问题的分析可作为质量背景与整改依据，但是否仍成立应以当前代码、`UNIFIED_MESSAGE_GATEWAY_DESIGN.md` 与 `GATEWAY_ZH_CHAIN_NODE_ANALYSIS.md` 为准。

---

## 0. 背景与问题根因

### 0.1 历史演化时间线

```
早期架构：
  MainChain + SceneRouter（单体）
  Gateway = 第三方平台接入层（微信/QQ webhook）
  ASR = 本地语音识别链路
  两条链路独立，source 字段区分来源

Phase XIV v4（2026-03-31）：
  SceneRouter 被删，改成 pre_intent_classifier 规则兜底
  意图识别从"图内节点"变成"HTTP 层函数"

Phase XV 之后：
  cortex_orchestrator 统一接管所有请求
  pre_intent_classifier 分流作用彻底失效，成为残留物
  build_gateway_intent_draft 作为 GATEWAY 专属意图识别补丁存在

当前状态（2026-04-15）：
  两套路由并存，决策割裂
  ASR 链路意图识别永远返回空壳 → 永远走 chat
  Gateway 链路意图识别质量差（Flash 偏向 chat）
  Gateway 被当作"接入层"而非"感知-行动循环"
```

### 0.2 架构债的核心问题

**问题 1：两层路由决策割裂**

| 层 | 位置 | 实际作用 | 问题 |
|----|------|---------|------|
| `pre_intent_classifier`（规则）| `graph_config_router.py` | 只影响 `/v1/chat` 的 `intent_draft.action` | 对路由无实质影响，是残留物 |
| `build_gateway_intent_draft`（规则+LLM）| `input_service_gateway.py` | 给 GATEWAY 来源填充 `intent_draft.route` | Flash 偏向 chat，误判率高 |
| `cortex_route_decider.route_intent()` | `tribe/cortex_route_decider.py` | 生理门控（throttle/silent/chat）| 不做任务意图识别，是主路径 |

**问题 2：ASR 链路结构性缺陷**

```
ASR 消息进来
  ↓
_run_cortex_orchestrator_pipeline
  ↓
build_gateway_intent_draft(source="ASR")
  → source != "GATEWAY" → 直接返回空壳 {"route": "chat"}
  ↓
executive_router → 永远 chat
```

ASR 来的代码请求、翻译请求、执行请求，**全部被路由到 chat**，这是结构性缺陷。

**问题 3：Gateway 定位错误**

当前 Gateway 被实现为"接入层"（webhook 接收 → 转发到 Host Brain），
但设计意图是让 Natsume **像真人一样使用社交软件**：
- 收到消息 → 感知（看到/听到）→ 理解 → 思考 → 回复
- 主动发朋友圈、发动态、发状态
- 语音通话时实时 ASR + TTS
- 回复时 VTS 同步做说话动画

**问题 4：suppress_edge_tts/vts 被强制关闭**

Gateway 回复时 `suppress_edge_tts=True`、`suppress_edge_vts=True`，
导致 Natsume 在微信里回复消息时，本地既不发声也不做动画，
与"真人使用社交软件"的体验目标完全相反。

---

## 1. 重设计目标

### 1.1 Gateway 的正确定位

```
Gateway = Natsume 使用社交软件的完整感知-行动循环

不是：HTTP 接入层（webhook → 转发）
而是：社交感知器（接收消息 → 感知 → 理解 → 决策 → 行动）
```

### 1.2 与 ASR 链路的关系

| | ASR 链路（生命体模式）| Gateway 链路（社交软件模式）|
|---|---|---|
| **本质** | 听觉皮层 → 理解 → 发声 | 社交感知 → 理解 → 文字/语音回复 |
| **感知方式** | 麦克风实时语音 | 文字/语音消息接收 |
| **回应方式** | TTS 合成 → 扬声器发声 | 文字推送 + 可选语音消息 |
| **VTS 联动** | ✅ 说话动画 | ✅ 应该有（当前被关闭）|
| **TTS 联动** | ✅ 本地发声 | ✅ 应该有（当前被关闭）|
| **意图识别** | ❌ 永远 chat（结构性缺陷）| ⚠️ 有但质量差（Flash 误判）|

**两条链路复用同一套 ASR/TTS/VTS 基础设施，只是触发方式不同。**

---

## 2. 架构改造方案

### 2.1 意图识别统一下沉（最高优先级）

**目标**：删除 `pre_intent_classifier` 和 `build_gateway_intent_draft` 的意图识别逻辑，
统一由 `cortex_route_decider.route_intent()` 完成。

**改造前**：
```
ASR/Web 消息 → intent_draft = {"route": "chat"}（空壳）→ 永远 chat
GATEWAY 消息 → build_gateway_intent_draft（规则+LLM）→ 填充 intent_draft
```

**改造后**：
```
所有消息（ASR/Web/GATEWAY）
  ↓
cortex_orchestrator
  ├── collect_tribe_context()  ← 20+ 族状态聚合（已有）
  └── route_intent(message)    ← 生理门控 + 任务意图识别（新增）
        ├── 生理门控：throttle/silent/open（已有）
        └── 任务意图：chat/code/translate/exec（新增）
              ↓
            intent_draft.route = "chat" / "delegate:code_agent" / "skill:xxx"
              ↓
            executive_router 按 tribe 意图分发
```

**涉及文件**：
- `host_brain/tribe/cortex_route_decider.py`：扩展 `route_intent()` 加任务意图分类
- `host_brain/app/input_service_gateway.py`：删除 `build_gateway_intent_draft` 的 LLM 意图识别逻辑，保留字段标准化
- `host_brain/app/graph_config_router.py`：删除 `pre_intent_classifier` 函数
- `host_brain/app/chat_handler.py`：移除对 `pre_intent_classifier` 的调用

**工作量**：2d

---

### 2.2 Gateway 回复时 TTS/VTS 联动恢复

**目标**：Gateway 回复时，Natsume 本地同步发声 + VTS 说话动画。

**改造前**：
```python
# input_routes.py / input_service_gateway.py
suppress_edge_tts=True   # ← 强制关闭 TTS
suppress_edge_vts=True   # ← 强制关闭 VTS
```

**改造后**：
```python
# Gateway 回复时，TTS/VTS 与 ASR 链路一致
suppress_edge_tts=False  # Natsume 在说话时本地也发声
suppress_edge_vts=False  # VTS 同步做说话动画
```

**可选增强**：Gateway 回复时，TTS 合成的音频同时编码为 `.silk` 发送语音消息，
实现"Natsume 既在本地说话，也在微信里发了语音消息"的效果。

**涉及文件**：
- `host_brain/app/input_service_gateway.py`：移除 `suppress_edge_tts/vts=True`
- `host_brain/app/input_routes.py`：确认 GATEWAY 来源不再强制 suppress

**工作量**：0.5d

---

### 2.3 收到消息时的 VTS 联动

**目标**：Natsume 收到社交消息时，VTS 做对应的感知动作。

**改造方案**：
在 `edge_client/gateway/router.py` 的 `route()` 方法中，
收到消息时向 `PeripheralAgent` 发布感知事件：

```python
# 收到文字消息
peripheral_agent.publish_message_received(
    platform=msg.platform.value,
    msg_type="text",
    sender=msg.sender_name,
)
# → VTS 做"看到消息"表情（眼神移向屏幕方向）

# 收到语音消息（ASR 识别后）
peripheral_agent.publish_voice_received(
    platform=msg.platform.value,
    transcript=asr_result["text"],
)
# → VTS 做"听到声音"动作（侧耳倾听）
```

**涉及文件**：
- `edge_client/gateway/router.py`：在 `route()` 中增加感知事件发布
- `edge_client/tribe/peripheral_agent.py`：增加 `publish_message_received()` / `publish_voice_received()` 方法
- `edge_client/actuators/vts_driver.py`：增加对应的 VTS 表情/动作触发

**工作量**：1d

---

### 2.4 朋友圈/动态发布能力

**目标**：Natsume 能主动发朋友圈、发状态，像真人使用微信一样。

**改造方案**：

在 `UnifiedReply` 中增加新消息类型：
```python
class MsgType(str, Enum):
    # 现有类型...
    MOMENTS = "moments"      # 朋友圈/动态
    STATUS = "status"        # 状态更新
    MOMENTS_COMMENT = "moments_comment"  # 朋友圈评论
```

在 `WeChatAdapter` 中实现对应的发送方法：
```python
def _send_moments_via_bridge_queue(self, reply: UnifiedReply) -> bool:
    """发布朋友圈/动态。"""
    # action="send_moments"
    # content=reply.content（文字内容）
    # media_path=reply.media_path（可选图片/视频）
```

**涉及文件**：
- `edge_client/gateway/models.py`：增加 `MOMENTS` / `STATUS` / `MOMENTS_COMMENT` 类型
- `edge_client/gateway/adapters/wechat_adapter.py`：实现 `_send_moments_via_bridge_queue()`
- `edge_client/channels/wechat_command_queue.py`：增加 `send_moments` action 支持

**工作量**：1d

---

### 2.5 语音回复联动（TTS → .silk 同步发送）

**目标**：Gateway 回复时，TTS 合成的音频同时编码为 `.silk` 发送语音消息。

**改造方案**：

在 `_deliver_gateway_reply()` 中，当回复类型为文字时：
1. 正常发送文字消息（拟人化分段）
2. 同时触发 TTS 合成
3. TTS 音频编码为 `.silk`
4. 发送语音消息（可配置是否启用）

```python
# 配置项
EDGE_GATEWAY_VOICE_REPLY_ENABLED=true  # 是否同时发送语音消息
EDGE_GATEWAY_VOICE_REPLY_PLATFORMS=wechat  # 哪些平台启用语音回复
```

**涉及文件**：
- `host_brain/app/input_service_gateway.py`：在 `_deliver_gateway_reply()` 中增加语音回复逻辑
- `edge_client/gateway/adapters/wechat_adapter.py`：`_send_voice_via_bridge_queue()` 已实现，直接复用

**工作量**：1.5d

---

### 2.6 语音通话实时 ASR 流（长期目标）

**目标**：语音通话时，实时 ASR 识别对方语音，TTS 实时回复，VTS 做通话状态动画。

**当前状态**：
- `WeChatAdapter._send_call_via_bridge_queue()` 只发送通话指令（`voice_call`/`video_call`）
- 没有接入实时语音流
- 微信语音/视频通话走 TRTC 私有 P2P 协议，与现有 ASR 链路不兼容

**改造方案（分阶段）**：

**阶段 1（可行）**：通话前后的状态联动
- 发起通话时：VTS 做"打电话"状态动画
- 通话结束时：VTS 恢复正常状态
- 通话期间：TTS 静音（避免双重发声）

**阶段 2（需要研究）**：实时语音流接入
- 研究 TRTC SDK 是否提供音频流回调
- 或通过系统音频捕获（loopback）接入现有 ASR 链路
- 实现真正的实时语音对话

**涉及文件**：
- `edge_client/gateway/adapters/wechat_adapter.py`：`_send_call_via_bridge_queue()` 增加状态事件
- `edge_client/tribe/peripheral_agent.py`：增加通话状态事件
- `edge_client/actuators/vts_driver.py`：增加通话状态动画

**工作量**：阶段 1 = 0.5d，阶段 2 = 待评估

---

## 3. 删除清单

以下代码在改造完成后应当删除：

| 代码 | 位置 | 删除原因 |
|------|------|---------|
| `pre_intent_classifier()` 函数 | `host_brain/app/graph_config_router.py` | 意图识别下沉到 tribe 层后，此函数无用 |
| `resolve_chat_scene_type()` 中对 `pre_intent_classifier` 的调用 | `host_brain/app/graph_config_router.py` | 同上 |
| `chat_handler.py` 中对 `pre_intent_classifier` 的调用 | `host_brain/app/chat_handler.py` | 同上 |
| `build_gateway_intent_draft()` 整个函数 | `host_brain/app/input_service_gateway.py` | 意图识别下沉到 tribe 层后，此函数无任何保留价值，字段标准化由 `cortex_orchestrator` 直接完成 |
| `run_gateway_intent_route_llm()` 整个函数 | `host_brain/app/input_service_gateway.py` | `build_gateway_intent_draft` 的 LLM 子函数，随主函数一并删除 |
| `build_gateway_rule_route_decision()` 整个函数 | `host_brain/app/input_service_gateway.py` | `build_gateway_intent_draft` 的规则子函数，规则逻辑迁移到 `cortex_route_decider` 后删除 |
| `apply_gateway_force_override()` 整个函数 | `host_brain/app/input_service_gateway.py` | 强制覆盖逻辑迁移到 `cortex_route_decider` 后删除 |
| `source == "GATEWAY"` 的意图识别特殊分支 | `host_brain/app/input_routes.py` | 所有来源统一走 tribe 意图识别后，source 不再影响意图识别路径 |
| `suppress_edge_tts=True` | `host_brain/app/input_service_gateway.py` | Gateway 回复时应该发声 |
| `suppress_edge_vts=True` | `host_brain/app/input_service_gateway.py` | Gateway 回复时应该有 VTS 动画 |

---

## 4. 新增能力清单

| 能力 | 涉及文件 | 优先级 |
|------|---------|--------|
| 意图识别统一下沉到 tribe 层 | `cortex_route_decider.py`、`input_service_gateway.py`、`graph_config_router.py` | P0（最高）|
| Gateway 回复时 TTS/VTS 联动恢复 | `input_service_gateway.py` | P0（最高）|
| 收到消息时 VTS 感知动作 | `gateway/router.py`、`peripheral_agent.py`、`vts_driver.py` | P1 |
| 朋友圈/动态发布 | `gateway/models.py`、`wechat_adapter.py` | P1 |
| 语音回复联动（TTS→.silk）| `input_service_gateway.py`、`wechat_adapter.py` | P2 |
| 语音通话状态联动（阶段 1）| `wechat_adapter.py`、`peripheral_agent.py`、`vts_driver.py` | P2 |
| 语音通话实时 ASR 流（阶段 2）| 待评估 | P3（长期）|

---

## 5. 执行顺序

```
第一批（P0，最高优先级，解决结构性缺陷）：
  ├── 意图识别统一下沉：cortex_route_decider.route_intent() 扩展任务意图分类
  ├── 删除 pre_intent_classifier（从 graph_config_router.py 和 chat_handler.py）
  ├── 删除 build_gateway_intent_draft 的 LLM 意图识别逻辑
  └── 移除 suppress_edge_tts/vts=True

第二批（P1，恢复感知-行动循环）：
  ├── 收到消息时 VTS 感知动作（gateway/router.py + peripheral_agent.py）
  └── 朋友圈/动态发布（models.py + wechat_adapter.py）

第三批（P2，增强体验）：
  ├── 语音回复联动（TTS→.silk 同步发送）
  └── 语音通话状态联动（阶段 1）

第四批（P3，长期目标）：
  └── 语音通话实时 ASR 流（阶段 2，需要研究 TRTC SDK）
```

---

## 6. 工作量汇总

| 批次 | 内容 | 工作量 |
|------|------|--------|
| 第一批（P0）| 意图识别下沉 + suppress 移除 | 2.5d |
| 第二批（P1）| VTS 感知动作 + 朋友圈发布 | 2d |
| 第三批（P2）| 语音回复联动 + 通话状态联动 | 2d |
| 第四批（P3）| 语音通话实时 ASR 流 | 待评估 |
| **合计** | | **6.5d + 待评估** |

---

## 7. 不修复的内容（有意为之）

| 内容 | 不修复原因 |
|------|-----------|
| 删除 `source` 字段区分 | `source` 字段仍有价值（用于日志追踪、统计分析），只是不再影响意图识别路径 |
| 合并 ASR 链路和 Gateway 链路为一条 | 两条链路的感知方式不同（实时语音 vs 消息接收），保持独立是正确的 |
| 保留 `build_gateway_intent_draft` 函数 | ~~保留函数体，只删除其中的 LLM 意图识别逻辑，字段标准化逻辑保留~~ **已修正：整个函数直接删除**。该函数存在的唯一理由是在接入层做意图识别，意图识别下沉到 tribe 层后，字段标准化由 `cortex_orchestrator` 直接完成，此函数无任何保留价值 |
| 微信语音/视频通话实时流（阶段 2）| TRTC 私有协议，技术可行性待评估，不列入近期计划 |

---

## 8. 关键设计约束

### 8.1 意图识别的双层设计

改造后的意图识别应保持双层结构：

```
第一层（规则快速预判，< 1ms）：
  cortex_route_decider._classify_task_intent_by_rules(message)
  → 关键词匹配：代码/翻译/执行等高置信度场景
  → 命中 → 直接返回，不走 LLM

第二层（LLM 兜底，仅在规则未命中时触发）：
  cortex_route_decider._classify_task_intent_by_llm(message, tribe_context)
  → 使用 Flash 模型（低延迟）
  → 结合族状态（情绪/Bond/内分泌）做综合判断
  → 输出：chat / delegate:code_agent / skill:xxx / exec
```

### 8.2 Gateway 回复的 TTS 行为

Gateway 回复时的 TTS 行为应可配置：

```
EDGE_GATEWAY_TTS_ENABLED=true          # 是否本地发声（默认 true）
EDGE_GATEWAY_TTS_VOICE_MSG_ENABLED=false  # 是否同时发送语音消息（默认 false）
EDGE_GATEWAY_TTS_VOICE_MSG_PLATFORMS=wechat  # 哪些平台发送语音消息
```

### 8.3 VTS 联动的优先级

VTS 动作优先级（高优先级覆盖低优先级）：
1. 通话状态（最高）
2. 说话动画（TTS 发声时）
3. 收到消息感知动作
4. 空闲状态（最低）

---

## 9. 配套修复：supervisor_dispatch_handler（Phase XV 补丁）

### 9.1 问题

`supervisor_dispatch_handler` 是 LangGraph Supervised 图（`PROFILE_WORKFLOW_SUPERVISED`）中的节点，
Phase XV 改造后 `scene_type` 从 `pre_intent_classifier` 的输出变为 `AUTO`，
导致 `_SCENE_DEFAULT_AGENT.get("AUTO")` 返回 `None`，兜底 `chat_agent`，
所有非 chat 请求在 Supervised 图路径下都被错误路由。

### 9.2 修复方案

**优先级**：`intent_draft.route/target_id` > `scene_type` 映射（兜底）

```python
# 新增 route → agent 映射
_ROUTE_TO_AGENT = {
    "chat":         "chat_agent",
    "delegate":     "",            # 由 target_id 决定
    "skill":        "intent_agent",
    "plan":         "intent_agent",
    "task_progress": "chat_agent",
    "recent_turn":  "chat_agent",
}

# delegate 时 target_id → agent_id
_DELEGATE_TARGET_TO_AGENT = {
    "code_agent":      "code_agent",
    "translate_agent": "translate_agent",
    "intent_agent":    "intent_agent",
    "vision_agent":    "intent_agent",   # 暂复用
    ...
}
```

**SUPERVISOR_MODE=llm 不再独立调用 LLM**：
`intent_draft.route_source="llm"` 时说明 `classify_task_intent` 已走过 LLM 兜底，
`supervisor_dispatch_handler` 直接消费结果，不重复调用。

### 9.3 涉及文件

- `host_brain/app/handlers/supervisor_dispatch_handler.py`：主修复
- `host_brain/app/graph_config_router.py`：删除重复的 `SUPERVISOR_DISPATCH_NODE_ID` 常量

---

*文档版本：v1.1 | 最后更新：2026-04-15 | 新增 §9 supervisor_dispatch_handler 配套修复*

---

## 10. 器官族接管主流程路线图（Phase B+C 实现记录）

### 10.1 22 个器官族当前接入状态

| 层级 | 器官族 | 接入状态 | 接入方式 |
|------|--------|---------|---------|
| 感知层 | ASR/Vision | ✅ 已接入 | Edge 侧完成，文字直接进入决策链 |
| 记忆层 | HippocampusAgent | ✅ 主链路 | 唯一记忆门面，三通道写入，Agentic RAG |
| 情感层 | LimbicAgent | ✅ 深度集成 | Prompt 构建 8 个调用点 + **Phase B-1 路由覆盖** |
| 免疫层 | ImmuneAgent | ✅ 门控+路由 | 输入门控 + **Phase B-2 路由覆盖** |
| 认知层 | ProprioceptionAgent | ✅ 路由覆盖 | **Phase B-3 认知负荷路由覆盖** |
| 语言层 | LanguageSystemAgent | ⚠️ Broca 断裂 | Broca 产出写 state_registry，**G2 已补映射** |
| 执行层 | MotorCortexAgent | ✅ 统一发声 | **Phase C 新增 dispatch_speak()** |
| 节律层 | ANSAgent/CircadianAgent | ✅ 后台运行 | 定时触发，主动发声节律 |
| 主动发声 | DefaultModeAgent | ⚠️ 事件断裂 | **G3 已补 PROACTIVE_VOICE_TRIGGERED 消费** |
| 纠偏层 | CerebellumAgent | ✅ 已接入 | 执行失败纠偏，联动 Endocrine/PainReward |
| 其余 B 级 | 13 个算法族 | ✅ 旁路运行 | 通过 Prompt 上下文软性影响 LLM 输出 |
| C 级骨架 | PeripheralAgent/SomaticAgent | ⏳ 等待 edge_client | 事件处理和状态写入已实现 |

### 10.2 Phase A：打通三个发声桥（已完成）

```
G1: chat_agent 对话完成 → 取 narrative → 组装 speak payload → POST /v1/edge/speak
G2: Broca.warmth_weight/tone → emotion_tag/emotion_intensity 映射表 → 注入 speak payload
G3: CortexAgent 订阅 PROACTIVE_VOICE_TRIGGERED → expression.content → TTS 链路
```

**效果**：七海现在真正能"开口说话"——对话回复、主动发声、情绪表达三条路全部打通。

### 10.3 Phase B：器官族状态影响路由决策（已完成）

在 `executive_runtime_core._executive_runtime_core_impl()` 中，`_hard_rule_route()` 调用前注入三层器官族状态检查：

```
B-1: LimbicAgent.compute_motive_state()
     → withdraw > 0.80 时强制 chat 路由
     → 七海情绪极度退缩时不执行 skill/delegate，优先情绪回应

B-2: ImmuneAgent.export_runtime_state()
     → risk_level=critical/high AND last_decision=BLOCK/REPORT 时强制 chat
     → 高风险时避免触发执行副作用

B-3: ProprioceptionAgent.export_runtime_state()
     → cognitive_load_index > 0.85 时强制 chat
     → 认知负荷极高时避免复杂执行链加重负担

B-4: 器官族路由影响写入 trace_stage_meta
     → organ_route_override / organ_route_reason / organ_route_meta
     → plan 路由和 skill/delegate 路由均覆盖，完整可观测
```

**覆盖规则**：器官族覆盖只作用于 `skill/delegate/plan/chat` 路由，不覆盖 `recent_turn/task_progress`（这两个是功能性快速路径，不受情绪状态影响）。

### 10.4 Phase C：MotorCortexAgent 统一发声入口（已完成）

新增 `MotorCortexAgent.dispatch_speak()` 方法，将所有发声统一走 MotorCortex 分发：

```python
motor_cortex.dispatch_speak(
    text="...",
    emotion_tag="LOVING",
    emotion_intensity=0.7,
    utterance_id="utt_xxx",
    request_id="req_xxx",
    session_id="sess_xxx",
    source="broca",          # 来源：broca/proactive/chat_agent
    require_gate=True,       # 主动发声时需要 InitiativeGovernor 门控
    motive=0.65,
)
```

**替代原有分散调用**：原来 `chat_agent_postprocessor`、`CortexAgent` 等各自直接 `POST /v1/edge/speak`，现在统一经过 MotorCortex，确保：
1. 发声前可选 InitiativeGovernor 门控
2. 发声结果写入 `state_registry`（`last_speak_outcome`）
3. 发布 `MOTOR_SPEAK_DISPATCHED` 事件到 TribeEventBus
4. 发声失败时联动 LimbicAgent 轻度挫败感

### 10.5 当前剩余缺口

| 缺口 | 描述 | 优先级 |
|------|------|--------|
| Broca→MotorCortex 桥 | LanguageSystemAgent.broca_generate_plan() 产出后，应调用 dispatch_speak() 而非直接 TTS | ✅ 已修复（P0） |
| chat_agent→dispatch_speak 迁移 | G1 补的直接调用改为走 MotorCortexAgent.dispatch_speak() | ✅ 已完成 |
| PrefrontalAgent 真实推理 | 当前读静态快照，需要接入小模型做实时意图规划 | ✅ Phase D 已完成 |
| 器官族并行激活 | 当前串行调用，真实神经系统是并行激活 | ✅ P1 已完成（asyncio.gather） |
| LimbicAgent 连续动态系统 | 当前离散事件驱动，需要持续运转的情绪物理模型 | 中（体验提升，待规划） |

### 10.6 Phase D：PrefrontalAgent 真实推理接管（已完成，2026-04-15）

**目标**：LLM 从"总控制器"降级为"语言生成器"，PrefrontalAgent 基于器官族完整状态产出意图计划。

#### 实现内容

**P2-1：`PrefrontalAgent.plan_response()` 方法**（`host_brain/tribe/prefrontal_agent.py`）

```python
# 规则快速路径（<1ms）：免疫高风险/严重漂移/极度退缩直接返回
# LLM 推理路径（gemini-2.0-flash，<500ms）：基于器官族完整状态产出意图计划
plan = prefrontal_agent.plan_response(
    user_message=...,
    motive_state=limbic.compute_motive_state(),
    recall_summary=hippocampus.recall_result,
    bond_stage=bond_summary["stage"],
    immune_risk_level=immune.risk_level,
    drift_alert_level=prefrontal.alert_level,
    emotion_tag=emotion_context["emotion_tag"],
    ...
)
# 输出：{"action": "speak|skill|delegate|wait", "target": "...", "task": "...", "tone_hint": "...", "urgency": 0.7}
```

**P2-2：`executive_runtime_core` 接入 `plan_response`**（`host_brain/app/executive_runtime_core.py`）

```
PREFRONTAL_PLAN_ENABLED=true 时启用（默认关，灰度）：
  1. plan_response 的 action=skill/delegate 时覆盖 _hard_rule_route 结果
  2. prefrontal_task/tone_hint 注入 intent_draft 传递给 chat_agent
  3. 跳过 _maybe_synthesize_runtime_capability（前额叶已规划，无需 LLM 再次合成）
  4. 结果写入 organ_route_meta 可观测性
```

**P2-3：`executive.yaml` 新增 `prefrontal_agent.plan_response.prompt` 模板**

- `system_prompt`：前额叶皮质角色设定 + 决策规则（action/tone_hint/urgency 约束）
- `user_prompt_template`：器官族完整状态格式化注入（14 个字段）

**P2-4：`chat_agent` 注入 `prefrontal_task` 字段**（`host_brain/app/chat_agent.py`）

```python
# 从 intent_draft 读取 prefrontal_task/prefrontal_tone_hint
# 构建【前额叶规划】context block 追加到 extra_context_prompt
# LLM 感知"我要说什么"的目标，退化为纯语言生成器
_prefrontal_context_block = f"【前额叶规划】当前回应目标：{prefrontal_task}（语气：{tone_hint}）"
```

**P3-1：`classify_task_intent` Phase D 短路**（`host_brain/tribe/cortex_route_decider.py`）

```python
# PREFRONTAL_PLAN_ENABLED=true 且 prefrontal_plan 已存在时：
# - 高优先级规则（进度查询/最近对话）仍运行
# - 跳过 LLM 兜底，直接返回 chat（executive_runtime_core 用 plan_response 覆盖路由）
# reason_code: "PREFRONTAL_PLAN_BYPASS"
```

**P3-2：`executive_runtime_core` 跳过 `_maybe_synthesize_runtime_capability`**

```python
# PREFRONTAL_PLAN_ENABLED=true 且 prefrontal_plan.action 存在时：
# 跳过 runtime_capability 合成（前额叶已规划，无需 LLM 再次合成路由）
```

#### 完整链路（Phase D 启用后）

```
外界刺激（用户消息）
    ↓
ImmuneAgent 门控
    ↓
HippocampusAgent 记忆检索
    ↓
LimbicAgent 情绪激活 + MirrorNeuronAgent 共情
    ↓
PrefrontalAgent.plan_response()（小模型，<500ms）
  输入：motive_state + recall_summary + bond_stage + immune_risk + drift_alert + emotion
  输出：{ action, target, task, tone_hint, urgency }
    ↓
executive_runtime_core 路由覆盖（plan_response.action 优先）
    ↓
chat_agent（纯语言生成器）
  接收 prefrontal_task 作为"我要说什么"的目标
  LLM 只负责把 task 转成流畅自然语言
    ↓
MotorCortexAgent.dispatch_speak() → TTS → 语音输出
```

#### 灰度开关

```bash
# 启用 Phase D（默认关）
PREFRONTAL_PLAN_ENABLED=true

# 禁用 cortex_route_decider LLM 兜底（Phase D 启用时自动跳过）
HOST_CORTEX_INTENT_LLM_ENABLED=false
```

---

*文档版本：v1.3 | 最后更新：2026-04-15 | 新增 §10.6 Phase D 实现记录（PrefrontalAgent 真实推理接管）*
