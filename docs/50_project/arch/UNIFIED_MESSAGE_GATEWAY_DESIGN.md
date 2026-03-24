# 外部消息统一网关（Unified Message Gateway）设计方案

> 文档版本：v0.1
> 创建日期：2026-03-24
> 状态：设计草稿
> 关联模块：`edge_client/gateway/`

---

## 一、当前问题诊断

### 1.1 两套并行链路

| 方案 | 文件 | 核心问题 |
|------|------|---------|
| **A: WeChatFerry Hook** | `edge_client/channels/wechat_listener.py` | wcferry 原仓库停更，新版微信兼容性差，注入失败率高 |
| **B: UIA 桌面自动化** | `tools/wechat_uia_bridge.py`（215KB/5878行） | 视觉模型+OCR+pyautogui 极脆弱，不可靠、不可扩展 |

### 1.2 具体问题根因

| 问题现象 | 根因 |
|---------|------|
| 右下角微信闪烁检测不到 | Tray 图标闪烁检测用 mss 截图 + gemma3:4b 判断，本地小模型识别任务栏图标极不可靠 |
| 视觉模型不起作用 | Ollama 跑本地 gemma3:4b 做 UI 理解，JSON 结构化输出（page/target_visible/x_ratio/y_ratio）经常解析失败或坐标偏差大 |
| 点击位置错误/点到头像 | pyautogui 基于视觉模型返回的 ratio 坐标点击，模型坐标不准，且没有 UIA 元素校验兜底 |
| 消息监听失败 | UIA Hook（WinEvent）→ 截图 → OCR/Vision 提取文本，整个链路脆弱，依赖窗口焦点状态 |

**核心结论**：当前方案本质上是"用眼睛看屏幕+用鼠标点击"，和真人操作一样脆弱。这种方式不可靠、不可扩展，也无法支持多平台（QQ/飞书/企微）。

### 1.3 现有可复用资产

| 资产 | 文件 | 复用方式 |
|------|------|---------|
| ✅ `WeChatMessage` 数据结构 | `channels/wechat_listener.py` | 升级为 `UnifiedMessage` |
| ✅ `EdgeInputReporter` HTTP POST 通道 | `channels/input_reporter.py` | 直接复用，上报到 `/v1/host/input` |
| ✅ `WeChatListener._parse_raw()` 消息解析 | `channels/wechat_listener.py` | 提炼为微信适配器基底 |
| ✅ `_MessageDeduplicator` 去重器 | `channels/wechat_listener.py` | 上移到 Gateway 公共层 |
| ✅ `desensitize_content()` 脱敏函数 | `channels/wechat_listener.py` | 上移到 Gateway 公共层 |
| ❌ `wechat_uia_bridge.py` | `tools/wechat_uia_bridge.py` | **彻底废弃** |

---

## 二、架构全景

```
外部消息源
  微信 (ntchat/ComWeChatBot)  ──HTTP Callback──┐
  QQ (NapCat/OneBot v11)      ──OneBot WS/HTTP─┤
  飞书 (官方 Open API)         ──Event Sub──────┤
  企微 (企业微信 API)           ──Callback URL───┘
                                                │
                          ┌─────────────────────▼──────────────────────┐
                          │   edge_client/gateway/  :19000              │
                          │                                             │
                          │  WebhookServer (FastAPI)                    │
                          │       │                                     │
                          │  PlatformAdapters (wechat/qq/feishu/wecom)  │
                          │       │                                     │
                          │  MessageNormalizer (去重 + 脱敏 + 校验)      │
                          │       │                                     │
                          │  MessageRouter ──────────────────────────── │──► EdgeInputReporter
                          │                                             │         │
                          │  OutboundDispatcher ◄────────────────────── │◄── Host Brain Reply
                          └─────────────────────────────────────────────┘
                                                │
                                    POST /v1/host/input
                                                │
                          ┌─────────────────────▼──────────────────────┐
                          │   Host Brain  :8000                         │
                          │   /v1/host/input → Main Chain → Reply Egress│
                          └─────────────────────────────────────────────┘
```

---

## 三、目录结构

```
edge_client/gateway/                     # 新增统一消息网关
├── __init__.py
├── server.py                            # FastAPI Webhook Server (:19000)
├── models.py                            # UnifiedMessage / UnifiedReply 数据模型
├── normalizer.py                        # 消息标准化（去重/脱敏/校验）
├── router.py                            # 消息路由到 Host Brain（复用 EdgeInputReporter）
├── outbound.py                          # 回复分发器（Host → 各平台发送）
├── adapters/                            # 平台适配器
│   ├── __init__.py
│   ├── base.py                          # AbstractPlatformAdapter 抽象基类
│   ├── wechat_adapter.py               # 微信适配器（ntchat / ComWeChatBot）
│   ├── qq_adapter.py                    # QQ 适配器（OneBot v11 协议）
│   ├── feishu_adapter.py               # 飞书适配器（Open API）
│   └── wecom_adapter.py                # 企微适配器
└── media/                               # 多媒体处理（Phase 2）
    ├── __init__.py
    ├── image_handler.py
    ├── voice_handler.py
    └── file_handler.py
```

---

## 四、核心数据模型

### 4.1 `UnifiedMessage` — 跨平台统一消息信封

```python
@dataclass
class UnifiedMessage:
    msg_id: str                          # 消息唯一 ID
    platform: Platform                   # wechat | qq | feishu | wecom
    from_id: str                         # 发送者 ID（wxid / QQ号 / open_id）
    to_id: str                           # 接收者 ID
    conversation_id: str                 # 会话 ID（群/私聊统一）
    msg_type: MsgType                    # text | image | voice | ...
    content: str                         # 文本内容
    media_url: str | None = None         # 媒体文件 URL/本地路径
    media_data: bytes | None = None      # 媒体二进制数据
    reply_to_msg_id: str | None = None   # 引用的消息 ID
    is_group: bool = False               # 是否群消息
    sender_name: str | None = None       # 发送者昵称
    ts_ms: int = ...                     # 时间戳(ms)
    raw_extra: dict[str, Any] = ...      # 平台原始附加数据
```

### 4.2 `UnifiedReply` — 跨平台统一回复

```python
@dataclass
class UnifiedReply:
    conversation_id: str
    platform: Platform
    msg_type: MsgType = MsgType.TEXT
    content: str = ""
    media_data: bytes | None = None
    media_path: str | None = None
    reply_to_msg_id: str | None = None
    ts_ms: int = ...
```

### 4.3 `Platform` 枚举

```python
class Platform(str, Enum):
    WECHAT = "wechat"
    QQ = "qq"
    FEISHU = "feishu"
    WECOM = "wecom"
```

### 4.4 `MsgType` 枚举

```python
class MsgType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    FILE = "file"
    EMOJI = "emoji"
    LINK = "link"
    SYSTEM = "system"
```

---

## 五、适配器设计

### 5.1 抽象基类 `AbstractPlatformAdapter`

```python
class AbstractPlatformAdapter(ABC):
    """职责：
    1. Inbound: 将平台原始消息 → UnifiedMessage
    2. Outbound: 将 UnifiedReply → 平台 API 发送
    3. 健康检查
    """

    @property
    @abstractmethod
    def platform(self) -> Platform: ...

    @abstractmethod
    def parse_inbound(self, raw: dict[str, Any]) -> UnifiedMessage | None: ...

    @abstractmethod
    async def send_reply(self, reply: UnifiedReply) -> bool: ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]: ...

    def validate_webhook(self, headers: dict, body: bytes) -> bool:
        return True  # 各平台自行实现签名验证
```

### 5.2 微信适配器 `WeChatAdapter`

- **接入方式**：ntchat / ComWeChatBotClient / WeChatFerryX（HTTP 回调）
- **字段兼容**：兼容 ntchat / wcferry / ComWeChatBot 多种字段命名
- **过滤逻辑**：自动过滤系统消息、自己发出的消息
- **消息类型映射**：复用现有 `wechat_listener.py` 的 `_TYPE_MAP`

```python
_WX_TYPE_MAP = {
    "1": MsgType.TEXT,   "3": MsgType.IMAGE,
    "34": MsgType.VOICE, "43": MsgType.VIDEO,
    "49": MsgType.LINK,  "10000": MsgType.SYSTEM,
}
```

### 5.3 QQ 适配器 `QQAdapter`

- **接入方式**：NapCat / Lagrange.Core → OneBot v11 HTTP/WebSocket
- **事件过滤**：只处理 `post_type == "message"` 事件
- **会话 ID**：群消息 `qq_group_{group_id}`，私聊 `qq_dm_{user_id}`

### 5.4 飞书适配器 `FeishuAdapter`（Phase 3）

- **接入方式**：飞书官方 Open API Event Subscription
- **签名验证**：飞书 Encrypt Key 验证

### 5.5 企微适配器 `WeComAdapter`（Phase 3）

- **接入方式**：企业微信开放 API Callback URL
- **签名验证**：企微 Token + EncodingAESKey

---

## 六、Gateway Server 设计

### 6.1 端点设计

| 端点 | 方法 | 说明 |
|------|------|------|
| `/webhook/{platform}` | POST | 统一 Webhook 入口（wechat/qq/feishu/wecom） |
| `/reply` | POST | Host Brain 回调，将回复发送到对应平台 |
| `/health` | GET | 各平台适配器健康检查 |

### 6.2 消息处理流程

```
POST /webhook/{platform}
    │
    ├─ 1. 适配器解析 adapter.parse_inbound(raw) → UnifiedMessage
    │
    ├─ 2. 标准化 normalizer.process(msg)
    │       ├─ 去重（复用 _MessageDeduplicator）
    │       ├─ 脱敏（复用 desensitize_content）
    │       └─ 校验（必填字段检查）
    │
    ├─ 3. 路由 router.route(msg)
    │       └─ EdgeInputReporter.report_gateway(payload)
    │               └─ POST /v1/host/input
    │
    └─ 4. 返回 {"status": "accepted", "msg_id": "..."}
```

### 6.3 回复流程

```
Host Brain 处理完成
    │
    └─ POST /reply {"platform": "wechat", "conversation_id": "...", "content": "..."}
            │
            └─ OutboundDispatcher → adapter.send_reply(reply)
                    └─ 调用对应平台 API 发送消息
```

---

## 七、与现有代码的集成

### 7.1 `EdgeInputReporter` 兼容改造

新增 `GATEWAY` 来源类型和 `report_gateway()` 方法：

```python
class InputSourceType(str, Enum):
    ASR = "ASR"
    WECHAT = "WECHAT"
    GATEWAY = "GATEWAY"   # 新增

def report_gateway(self, *, payload: dict[str, Any]) -> InputReportResult:
    """上报统一网关消息到 Host Brain。"""
    return self._send(payload, InputSourceType.GATEWAY)
```

### 7.2 `EdgeConfig` 新增配置项

```
EDGE_GATEWAY_ENABLED=true          # 是否启用统一消息网关（默认 true）
EDGE_GATEWAY_PORT=19000            # 网关监听端口（默认 19000）
EDGE_GATEWAY_PLATFORMS=wechat,qq   # 启用的平台（逗号分隔）
WECHAT_HOOK_API_BASE=              # ntchat/ComWeChatBot API 地址
WECHAT_SELF_WXID=                  # 微信小号 wxid（用于过滤自发消息）
QQ_ONEBOT_API_BASE=                # OneBot HTTP API 地址
QQ_SELF_ID=                        # QQ 号（用于过滤自发消息）
```

### 7.3 `bootstrap.py` 集成点

在 `EdgeRuntime` 启动时，若 `gateway_enabled=True`，额外启动 Gateway Server（uvicorn :19000）。

### 7.4 Host 侧 `/v1/host/input` 适配

Host 侧需识别 `source=GATEWAY` 并提取 `platform` 字段，路由到正确处理链路。回复时通过 Gateway 的 `/reply` 端点回发。

---

## 八、微信接入技术选型

| 方案 | 维护状态 | 适用微信版本 | 稳定性 | 接入方式 |
|------|---------|------------|--------|---------|
| **ntchat** ⭐推荐 | 活跃 | 3.9.x | ⭐⭐⭐⭐ | HTTP 回调 |
| **ComWeChatBotClient** | 活跃 | 3.9.x / 4.x | ⭐⭐⭐ | COM Hook |
| **WeChatFerryX** | 分支维护 | 3.9.x | ⭐⭐⭐ | DLL 注入 |
| ~~WeChatFerry (原版)~~ | ❌ 停更 | 3.9.x 旧版 | ⭐ | DLL 注入 |
| ~~UIA 桌面自动化~~ | ❌ 废弃 | 任意 | ⭐ | 截图+OCR |

**建议首选 ntchat**：HTTP 回调模式与 Gateway Webhook 天然契合，无需额外桥接。

---

## 九、实施路线图

### Phase 1：核心框架（约 3 天）

- [ ] `gateway/models.py` — `UnifiedMessage` / `UnifiedReply` / `Platform` / `MsgType`
- [ ] `gateway/adapters/base.py` — `AbstractPlatformAdapter` 抽象基类
- [ ] `gateway/normalizer.py` — 去重 + 脱敏 + 校验（复用现有组件）
- [ ] `gateway/router.py` — 路由到 Host Brain（复用 `EdgeInputReporter`）
- [ ] `gateway/server.py` — FastAPI Webhook Server

### Phase 2：微信适配器（约 3 天）

- [ ] `gateway/adapters/wechat_adapter.py` — ntchat / ComWeChatBot 接入
- [ ] `gateway/outbound.py` — 回复分发器
- [ ] `edge_client/config.py` 扩展 + `bootstrap.py` 集成
- [ ] `channels/input_reporter.py` 新增 `GATEWAY` 来源

### Phase 3：QQ 适配器（约 2 天）

- [ ] `gateway/adapters/qq_adapter.py` — OneBot v11 HTTP 接入
- [ ] OneBot WebSocket 长连接支持

### Phase 4：飞书 / 企微（约 3 天）

- [ ] `gateway/adapters/feishu_adapter.py` — 飞书官方 Open API
- [ ] `gateway/adapters/wecom_adapter.py` — 企业微信 API

### Phase 5：多媒体 + 清理（约 3 天）

- [ ] `gateway/media/` — 图片/语音/文件收发
- [ ] 废弃 `tools/wechat_uia_bridge.py`
- [ ] Host 侧 reply 回调集成

---

## 十、关键设计决策

### 决策 1：为什么不复用现有 `wechat_listener.py`？

`wechat_listener.py` 是为 wcferry 单一平台设计的，数据结构（`WeChatMessage`）和路由逻辑都是微信专用的。Gateway 需要跨平台统一抽象，因此选择**提炼其中的通用逻辑**（去重、脱敏、类型映射）而非直接复用整个文件。

### 决策 2：为什么 Gateway 独立运行在 :19000 而非复用 :17800？

Edge RPC Gateway（:17800）是 Host→Edge 的**下行通道**（Host 调用 Edge 的 ASR/TTS/资源门控等能力）。Unified Message Gateway（:19000）是外部平台→Edge 的**上行通道**（外部消息进入系统）。两者方向相反、职责不同，独立端口避免路由混乱。

### 决策 3：为什么复用 `EdgeInputReporter` 而非直接 HTTP POST？

`EdgeInputReporter` 已实现 HMAC 签名、失败缓存、后台重试等生产级特性，直接复用可避免重复实现这些基础设施。

### 决策 4：消息去重粒度

去重基于 `(platform, msg_id)` 组合键，而非单独的 `msg_id`，避免不同平台的消息 ID 碰撞。

---

## 十一、废弃计划

| 文件 | 废弃时间 | 替代方案 |
|------|---------|---------|
| `tools/wechat_uia_bridge.py` | Phase 5 完成后 | `gateway/adapters/wechat_adapter.py` |
| `edge_client/channels/wechat_listener.py` | Phase 2 完成后（部分逻辑上移） | `gateway/adapters/wechat_adapter.py` + `gateway/normalizer.py` |
