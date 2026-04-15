# Gateway 架构重设计方案（GATEWAY-ARCH-REDESIGN）

**文档编号**：GATEWAY-ARCH-REDESIGN-001
**日期**：2026-04-15
**版本**：v1.0（草稿，待微调）
**状态**：规划中，待执行
**受众**：核心开发团队

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
