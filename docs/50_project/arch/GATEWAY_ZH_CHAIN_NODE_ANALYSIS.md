受众：MASTER
标题：Gateway 中文文字链路完整节点分析（当前代码实况）

版本：v2.1
日期：2026-06-23

---

定位：本文只分析当前代码里“外部平台消息进入 Gateway 后，如何生成中文文字回复并回送平台”的真实链路。
若与旧文档中的 `SCENE_GATEWAY / SceneRouter / main_chain / top_agent` 口径冲突，以本文和代码为准。

## 0. 代码证据入口

- `edge_client/gateway/server.py`
- `edge_client/gateway/router.py`
- `edge_client/gateway/outbound.py`
- `edge_client/gateway/models.py`
- `edge_client/gateway/bootstrap.py`
- `edge_client/channels/input_reporter.py`
- `host_brain/app/input_routes.py`
- `host_brain/app/input_service_ack.py`
- `host_brain/app/schemas.py`
- `host_brain/app/agent_runtime_shared.py`
- `host_brain/app/surface_reply_runtime.py`
- `host_brain/app/surface_main_chain_adapter.py`
- `host_brain/app/reply_envelope.py`
- `host_brain/persona/surface_main_chain.py`
- `host_brain/app/chat_retrieval_helpers.py`
- `host_brain/retrieval/recall_fusion.py`
- `host_brain/channels/edge_rpc_client.py`

---

## 一、当前总链路

当前 Gateway 中文文字链路已经不是旧的“双入口 + SCENE_GATEWAY 场景分流”，而是：

```text
平台 Webhook
  -> edge_client/gateway/server.py
  -> adapter.parse_inbound()
  -> MessageNormalizer.normalize()
  -> GatewayRouter.route()
  -> EdgeInputReporter._send()
  -> POST /v1/host/input
  -> input_routes._run_cortex_orchestrator_pipeline()
  -> ChatRequest(render_target_lang="zh")
  -> cortex_orchestrator / executive runtime
  -> surface_reply_runtime / surface_main_chain_adapter
  -> SurfaceMainChain
  -> ReplyEnvelope COMMIT
  -> input_routes._deliver_gateway_reply()
  -> host_brain/channels/edge_rpc_client.py
  -> POST /v1/gateway/reply
  -> OutboundDispatcher.dispatch()
  -> 平台适配器 send_reply()
```

---

## 二、Gateway 入站层

### 2.1 当前服务面

`edge_client/gateway/server.py` 当前提供的核心端点：

- `POST /v1/gateway/{platform}/webhook`
- `POST /v1/gateway/reply`
- `GET /v1/gateway/health`
- `GET /v1/gateway/stats`

### 2.2 当前入站步骤

| 步骤 | 当前模块 | 说明 |
|---|---|---|
| 原始平台消息进入 | `server.py` | 收到平台 webhook |
| 平台适配 | `adapters/*.py` | `parse_inbound()` 转成 `UnifiedMessage` |
| 统一信封 | `models.py` | `UnifiedMessage.as_report_payload()` 固定生成 `source="GATEWAY"` |
| 标准化 | `normalizer.py` | 去重、脱敏、校验 |
| 路由上报 | `router.py` | `GatewayRouter.route()` 调 `EdgeInputReporter._send()` |

当前 `GatewayConfig` 与 `bootstrap.py` 识别的平台集合包括：

- `wechat`
- `qq`
- `feishu`
- `wecom`
- `telegram`
- `line`

---

## 三、Host 入口层

### 3.1 当前回流入口

Gateway 消息进入 Host 的真实入口是：

- `POST /v1/host/input`

不是旧文档里的：

- `POST /v1/input`
- `SCENE_GATEWAY`
- `MainChain.run_as_sse_adapter()`

### 3.2 当前字段校验

`host_brain/app/input_routes.py` 对 Gateway 输入会校验：

- `content`
- `conversation_id`
- `platform`

并把：

- `session_id = conversation_id`

作为当前 Gateway 会话主键使用。

---

## 四、Gateway 中文输出是如何被固定下来的

### 4.1 `ChatRequest` 注入

`input_routes._run_cortex_orchestrator_pipeline()` 当前会构造：

- `reply_channel = platform`
- `reply_target = conversation_id`
- `input_source = source`
- `render_target_lang = "zh"`（当 `source == "GATEWAY"`）
- `suppress_edge_tts = True`
- `suppress_edge_vts = True`

也就是说，Gateway 文本链的中文输出不是靠旧的 `SCENE_GATEWAY` 场景常量，而是直接在 `ChatRequest` 上注入 `render_target_lang="zh"`。

### 4.2 主链如何消费这个语言字段

`host_brain/app/agent_runtime_shared.py` 当前会先读取：

- `request_body.render_target_lang`

若注入值是 `zh` 或 `ja`，就直接使用；否则才回退到 `infer_render_target_lang(...)` 推断。

因此，对 Gateway 来说：

- 文字通道优先使用显式注入的 `zh`
- 不需要再依赖旧的 `pre_scene = SCENE_GATEWAY`

### 4.3 `ChatRequest` 契约本身也写明了这点

`host_brain/app/schemas.py` 当前注释已经明确：

- Gateway 通道固定注入 `'zh'`
- 未指定时才做语言推断

### 4.4 SurfaceMainChain / ReplyEnvelope 侧口径

Gateway 文本最终仍由 SurfaceMainChain 生成，并由 ReplyEnvelope 收口。`render_target_lang="zh"` 会作为 Host 构造的请求上下文进入后续编排，约束最终文字通道：

- SurfaceMainChain 只生成用户可见文本，不直接声明执行事实。
- ReplyEnvelope 决定该文本是否可发送、可持久化、可缓存。
- Gateway ACK、任务状态和失败降级不得写入人格记忆。

这进一步保证了 Gateway 文本回复会走中文输出。

---

## 五、Gateway 中文链路中的检索

当前 Gateway 中文链不会走旧的 `RecallFusionNode` 路径，而是复用当前主链检索：

```text
input_routes
  -> cortex_orchestrator
  -> agent_runtime_shared
  -> chat_retrieval_helpers._run_retrieval
  -> RecallFusionServiceContract.recall(...)
  -> surface_memory_recall / MemoryRecallPacket
  -> SurfaceMainChain facts
```

在这条链里，召回层只提供候选事实和 evidence refs；最终是否进入表达由 surface memory recall、packet compiler、necessary_facts / must_not_do 与 ReplyEnvelope 收口共同决定。

因此，当前 Gateway 中文链的“中文保障”主要来自：

- `ChatRequest(render_target_lang="zh")`
- SurfaceMainChain 对中文文字通道的收口
- Gateway 出站层只发送 ReplyEnvelope 允许发送的最终文本

---

## 六、COMMIT 后的回复回送

### 6.1 主回复

`input_routes._deliver_gateway_reply()` 当前会：

1. 读取 `platform` 和 `conversation_id`
2. 用 `ReplyFormDecider` 决定回复形态
3. 调 `post_gateway_reply(...)`

`post_gateway_reply(...)` 位于：

- `host_brain/channels/edge_rpc_client.py`

它会向 Gateway Server 的：

- `POST /v1/gateway/reply`

发送统一回复请求。

### 6.2 Gateway 出站层

`edge_client/gateway/server.py` 收到 `/v1/gateway/reply` 后会：

1. 反序列化为 `UnifiedReply`
2. 交给 `OutboundDispatcher.dispatch()`
3. 再由对应平台适配器的 `send_reply()` 完成真实发送

所以当前代码里，Host 不直接把文字发回微信/QQ，而是统一交给 Gateway 的出站分发层。

---

## 七、ACK / 进度回复也是同一条出站链

除了最终 COMMIT 文本，Gateway 入口的两类附加回复也走相同出站面：

- 即时 ACK：`input_service_ack.deliver_gateway_immediate_ack()`
- 进度回复 / 快路回复：`input_routes.py` 中的 gateway progress / reply 逻辑

这些路径最终也都会落到：

- `post_gateway_reply(...)`
- `POST /v1/gateway/reply`

因此，当前 Gateway 中文文字链不只是“最终回复链”，还是外部平台文本出站的统一通道。

---

## 八、语音与文字的当前关系

当前代码的关键现实是：

- Gateway 文字输出固定中文
- Gateway 入口默认抑制 Edge TTS / VTS 的直接补发，避免文字平台重复播报
- 如需语音二次回复，会在 `_deliver_gateway_reply()` 中走独立语音分支
- 语音附加链不得反向改变 Gateway 文字 COMMIT 的事实与语言

所以本文讨论的是“中文文字链路”。
可选语音回复是另一条附加链，不应再和文字主链混写成旧的单一 `top_agent` 方案。

---

## 九、当前不再适用的旧口径

以下说法不再代表当前实现：

- Gateway 通过 `SCENE_GATEWAY` 进入主链
- `SceneRouter.route_sse(scene_type="GATEWAY")` 是当前分流点
- `host_brain/app/graph/main_chain.py` 是 Gateway 中文链主入口
- `gateway_orchestrator_sse_generator -> top_agent.py` 是当前回复生成链
- 外部输入主入口仍是 `/v1/input`

当前正确口径是：

1. Gateway 入站先在 Edge 侧标准化，再上报到 `POST /v1/host/input`。
2. Host 用 `ChatRequest(render_target_lang="zh")` 把文字通道固定到中文。
3. SurfaceMainChain 生成最终文本，ReplyEnvelope 收口。
4. 最终回送统一经 `post_gateway_reply()` 和 Gateway `OutboundDispatcher` 完成。
