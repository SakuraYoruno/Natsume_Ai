受众：CODEX
标题：wxautox4 完整 API 对接更新方案

# wxautox4 完整 API 对接更新方案

> **文档版本**：v1.0 | **创建日期**：2026-03-27 | **作者**：七海 (Codex Node)
> **wxautox4 版本**：v40.1.11 (2026-03-10) | **微信版本**：4.1
> **Natsume_Ai 仓库**：`D:\Natsume_Ai_repo` | **分支**：`codex`

---

## 一、现状总结

### 1.1 当前已对接的 wxautox4 API（5个）

| # | API 方法 | 用途 | 调用位置 |
|---|----------|------|----------|
| 1 | `WeChat()` | 初始化，绑定微信窗口 | `WxAutoX4Bridge.start()` L521-530 |
| 2 | `AddListenChat(who=target)` | 注册监听目标聊天 | `InboundProcessor.setup_listeners()` |
| 3 | `GetNewMessage()` | 轮询获取新消息 | `InboundProcessor._poll_once()` |
| 4 | `SendMsg(content, who=target)` | 发送文本消息 | `OutboundProcessor._execute_command()` L405 |
| 5 | `SendFiles(path, who=target)` | 发送文件/图片/语音 | `OutboundProcessor._execute_command()` L419/433/448 |

### 1.2 当前支持的命令队列 Actions（6种）

```python
# edge_client/channels/wechat_command_queue.py L15
ALLOWED_WECHAT_COMMAND_ACTIONS = {
    "send_text", "send_image", "send_file", "send_voice",
    "voice_call", "video_call"  # 后两个仅声明，wxautox4 返回 NOT_SUPPORTED
}
```

### 1.3 当前 WeChatAdapter 支持的出站类型（3种）

| 出站类型 | 对应 action | 实现方法 |
|----------|-------------|----------|
| `MsgType.TEXT` | `send_text` | `_send_via_bridge_queue()` |
| `MsgType.IMAGE` | `send_image` | `_send_image_via_bridge_queue()` |
| `MsgType.VOICE` | `send_voice` | `_send_voice_via_bridge_queue()` |

---

## 二、wxautox4 完整 API 清单 vs 当前对接状态

### 2.1 核心会话管理 API

| # | API 方法 | 功能 | 当前状态 | 优先级 |
|---|----------|------|----------|--------|
| 1 | `WeChat()` | 初始化绑定 | ✅ 已对接 | — |
| 2 | `ChatWith(who)` | 切换到指定聊天窗口 | ❌ 未对接 | P1 |
| 3 | `GetSubWindow(who)` | 获取指定子窗口 | ❌ 未对接 | P2 |
| 4 | `GetAllSubWindow()` | 获取所有子窗口 | ❌ 未对接 | P3 |
| 5 | `ChatInfo()` | 获取当前聊天信息 | ❌ 未对接 | P2 |
| 6 | `GetSession()` | 获取当前会话列表 | ❌ 未对接 | P2 |
| 7 | `IsOnline()` | 检查微信是否在线 | ❌ 未对接 | P1 |
| 8 | `GetDialog()` | 处理弹窗/对话框 | ❌ 未对接 | P2 |

### 2.2 消息收发 API

| # | API 方法 | 功能 | 当前状态 | 优先级 |
|---|----------|------|----------|--------|
| 1 | `SendMsg(content, who)` | 发送文本 | ✅ 已对接 | — |
| 2 | `SendFiles(path, who)` | 发送文件 | ✅ 已对接 | — |
| 3 | `SendUrlCard(url, title, desc, who)` | 发送链接卡片 | ❌ 未对接 | P2 |
| 4 | `AddListenChat(who)` | 注册监听 | ✅ 已对接 | — |
| 5 | `RemoveListenChat(who)` | 移除监听 | ❌ 未对接 | P1 |
| 6 | `StopListening()` | 停止所有监听 | ❌ 未对接 | P1 |
| 7 | `GetNewMessage()` | 获取新消息 | ✅ 已对接 | — |
| 8 | `GetAllMessage()` | 获取所有消息 | ❌ 未对接 | P2 |
| 9 | `GetHistoryMessage(who, n)` | 获取历史消息 | ❌ 未对接 | P1 |

### 2.3 联系人/好友管理 API

| # | API 方法 | 功能 | 当前状态 | 优先级 |
|---|----------|------|----------|--------|
| 1 | `GetFriends()` | 获取好友列表 | ❌ 未对接 | P1 |
| 2 | `GetRecentGroups()` | 获取最近群聊列表 | ❌ 未对接 | P1 |
| 3 | `GetMyInfo()` | 获取自己的信息 | ❌ 未对接 | P1 |
| 4 | `GetNewFriends()` | 获取新好友请求 | ❌ 未对接 | P2 |
| 5 | `GetFriendDetails(who)` | 获取好友详情 | ❌ 未对接 | P2 |
| 6 | `AddGroupMembers(group, members)` | 添加群成员 | ❌ 未对接 | P3 |

### 2.4 朋友圈 API

| # | API 方法 | 功能 | 当前状态 | 优先级 |
|---|----------|------|----------|--------|
| 1 | `PublishMoment(content)` | 发布朋友圈 | ❌ 未对接 | P3 |
| 2 | `GetMoments(who)` | 获取朋友圈动态 | ❌ 未对接 | P3 |
| 3 | `Like(moment_id)` | 点赞朋友圈 | ❌ 未对接 | P3 |
| 4 | `Comment(moment_id, text)` | 评论朋友圈 | ❌ 未对接 | P3 |

### 2.5 消息对象方法（Message Object Methods）

wxautox4 的消息对象具有类型化方法，可以直接操作：

| # | 消息类型 | 方法 | 功能 | 当前状态 | 优先级 |
|---|----------|------|------|----------|--------|
| 1 | `ImageMessage` | `.download(save_path)` | 下载图片 | ❌ 未对接 | P1 |
| 2 | `VideoMessage` | `.download(save_path)` | 下载视频 | ❌ 未对接 | P1 |
| 3 | `VoiceMessage` | `.download(save_path)` | 下载语音 | ❌ 未对接 | P1 |
| 4 | `LinkMessage` | `.get_url()` | 获取链接URL | ❌ 未对接 | P2 |
| 5 | `EmotionMessage` | `.capture(save_path)` | 截取表情 | ❌ 未对接 | P3 |
| 6 | `QuoteMessage` | `.download_quote_image()` | 下载引用图片 | ❌ 未对接 | P3 |
| 7 | `HumanMessage` | `.delete()` | 删除消息 | ❌ 未对接 | P3 |

---

## 三、优先级定义与分期计划

### 优先级说明

| 优先级 | 含义 | 标准 |
|--------|------|------|
| **P1** | 核心功能 | Natsume_Ai 伴侣系统日常运行必需 |
| **P2** | 增强功能 | 显著提升交互体验和信息获取能力 |
| **P3** | 扩展功能 | 完善社交自动化，非核心路径 |

### 分期实施计划

#### Phase 1：核心增强（P1）— 预计工作量 ★★★

**目标**：让 wxautox4 bridge 达到完整可用状态，具备健康监控、联系人管理、历史消息获取、媒体下载等核心能力。

新增功能：
1. `IsOnline()` — 健康检测
2. `GetMyInfo()` — 自身信息获取（用于 self_wxid 自动识别）
3. `GetFriends()` — 好友列表
4. `GetRecentGroups()` — 群聊列表
5. `GetHistoryMessage()` — 历史消息查询
6. `RemoveListenChat()` / `StopListening()` — 监听管理
7. `ChatWith()` — 主动切换聊天窗口
8. `ImageMessage.download()` / `VideoMessage.download()` / `VoiceMessage.download()` — 媒体消息下载

#### Phase 2：体验增强（P2）— 预计工作量 ★★

**目标**：扩展信息获取和交互类型。

新增功能：
1. `GetSubWindow()` / `GetAllSubWindow()` — 子窗口管理
2. `ChatInfo()` — 聊天详情
3. `GetSession()` — 会话列表
4. `GetDialog()` — 弹窗处理
5. `SendUrlCard()` — 发送链接卡片
6. `GetAllMessage()` — 全量消息获取
7. `GetNewFriends()` — 新好友请求
8. `GetFriendDetails()` — 好友详细信息
9. `LinkMessage.get_url()` — 解析链接消息

#### Phase 3：社交扩展（P3）— 预计工作量 ★

**目标**：完善社交自动化能力。

新增功能：
1. `PublishMoment()` / `GetMoments()` / `Like()` / `Comment()` — 朋友圈
2. `AddGroupMembers()` — 群管理
3. `EmotionMessage.capture()` / `QuoteMessage.download_quote_image()` / `HumanMessage.delete()` — 高级消息操作

---

## 四、详细代码修改方案

### 4.1 文件修改总览

| 文件路径 | 修改类型 | Phase | 说明 |
|----------|----------|-------|------|
| `tools/wxautox4_bridge.py` | **大改** | P1-P3 | 新增所有 API 调用逻辑 |
| `edge_client/channels/wechat_command_queue.py` | **中改** | P1-P2 | 扩展 ALLOWED_ACTIONS |
| `edge_client/gateway/adapters/wechat_adapter.py` | **中改** | P1-P2 | 新增出站消息类型处理 |
| `edge_client/gateway/models.py` | **小改** | P2 | 可能新增 MsgType 枚举 |
| `.env.example` | **小改** | P1 | 新增配置项 |
| `edge_client/gateway/bootstrap.py` | **小改** | P1 | 传递新增配置项 |
| `host_brain/channels/wechat.py` | **小改** | P1-P2 | 适配新消息类型 |
| `host_brain/channels/wechat_health.py` | **小改** | P1 | 利用 IsOnline() 增强健康检测 |

---

### 4.2 Phase 1 详细修改

#### 4.2.1 `tools/wxautox4_bridge.py` — 核心 Bridge 扩展

##### (A) 新增 `IsOnline()` 健康检测

```python
# 在 WxAutoX4Bridge 类中新增健康状态轮询
class WxAutoX4Bridge:
    def _health_check(self) -> bool:
        """Check if WeChat is still online via wxautox4."""
        try:
            return bool(self._wx.IsOnline())
        except Exception as exc:
            logger.warning("IsOnline() check failed: %s", exc)
            return False
```

**修改点**：
- `WxAutoX4Bridge.start()` 中的 keep-alive 循环增加健康检测
- 将健康状态通过 webhook 上报给 Gateway

##### (B) 新增 `GetMyInfo()` 自动获取 self_wxid

```python
# 在 WxAutoX4Bridge.start() 初始化后调用
def _init_self_info(self, wx) -> dict:
    """Get self info for message dedup and self-message filtering."""
    try:
        info = wx.GetMyInfo()
        self._self_wxid = info.get("wxid") or info.get("id") or ""
        self._self_name = info.get("name") or info.get("nickname") or ""
        logger.info("Self info: wxid=%s name=%s", self._self_wxid, self._self_name)
        return info
    except Exception as exc:
        logger.warning("GetMyInfo() failed: %s", exc)
        return {}
```

**修改点**：
- `InboundProcessor.__init__()` 接收 `self_wxid` 参数
- `InboundProcessor._poll_once()` 过滤自己发送的消息（目前缺失）
- Webhook 上报时携带 `self_wxid` 信息

##### (C) 新增好友/群聊列表查询

```python
# 在 OutboundProcessor._execute_command() 中新增 action 分支

elif action == "get_friends":
    friends = self._wx.GetFriends()
    result["success"] = True
    result["data"] = friends  # list of friend info dicts
    result["status"] = "ok"

elif action == "get_recent_groups":
    groups = self._wx.GetRecentGroups()
    result["success"] = True
    result["data"] = groups
    result["status"] = "ok"

elif action == "get_my_info":
    info = self._wx.GetMyInfo()
    result["success"] = True
    result["data"] = info
    result["status"] = "ok"
```

**修改点**：
- `OutboundProcessor._execute_command()` 扩展 action 分支
- 这些是**查询型**命令，不同于发送型命令，result 中携带 `data` 字段

##### (D) 新增历史消息查询

```python
elif action == "get_history_message":
    who = str(command.get("who") or target or "").strip()
    count = int(command.get("count") or 20)
    if not who:
        result["reason_code"] = "WECHAT_MISSING_TARGET"
        result["detail"] = "get_history_message requires 'who' parameter"
    else:
        messages = self._wx.GetHistoryMessage(who, count)
        result["success"] = True
        result["data"] = [_serialize_msg(m) for m in messages]
        result["status"] = "ok"
```

##### (E) 新增监听管理

```python
elif action == "remove_listen_chat":
    who = str(command.get("who") or target or "").strip()
    if who:
        self._wx.RemoveListenChat(who)
        result["success"] = True
        result["status"] = "ok"

elif action == "stop_listening":
    self._wx.StopListening()
    result["success"] = True
    result["status"] = "ok"
```

##### (F) 新增媒体消息下载处理

```python
# 在 InboundProcessor._poll_once() 的消息处理逻辑中

def _process_media_message(self, msg) -> dict:
    """Download media from typed message objects and return local path."""
    media_info = {}
    save_dir = Path(os.environ.get("EDGE_WECHAT_MEDIA_SAVE_DIR", "runtime/edge/media"))
    save_dir.mkdir(parents=True, exist_ok=True)

    msg_cls_name = type(msg).__name__

    if msg_cls_name == "ImageMessage":
        save_path = save_dir / f"img_{int(time.time()*1000)}.png"
        try:
            msg.download(str(save_path))
            media_info["media_url"] = str(save_path)
            media_info["media_type"] = "image"
        except Exception as exc:
            logger.warning("ImageMessage.download() failed: %s", exc)

    elif msg_cls_name == "VideoMessage":
        save_path = save_dir / f"vid_{int(time.time()*1000)}.mp4"
        try:
            msg.download(str(save_path))
            media_info["media_url"] = str(save_path)
            media_info["media_type"] = "video"
        except Exception as exc:
            logger.warning("VideoMessage.download() failed: %s", exc)

    elif msg_cls_name == "VoiceMessage":
        save_path = save_dir / f"voice_{int(time.time()*1000)}.silk"
        try:
            msg.download(str(save_path))
            media_info["media_url"] = str(save_path)
            media_info["media_type"] = "voice"
        except Exception as exc:
            logger.warning("VoiceMessage.download() failed: %s", exc)

    return media_info
```

**修改点**：
- `InboundProcessor._poll_once()` 检测消息类型，调用对应下载方法
- 下载后的本地路径通过 webhook `media_url` 字段上报
- 新增环境变量 `EDGE_WECHAT_MEDIA_SAVE_DIR`

##### (G) 新增 `ChatWith()` 切换聊天

```python
elif action == "chat_with":
    who = str(command.get("who") or target or "").strip()
    if not who:
        result["reason_code"] = "WECHAT_MISSING_TARGET"
    else:
        self._wx.ChatWith(who)
        result["success"] = True
        result["status"] = "ok"
```

---

#### 4.2.2 `edge_client/channels/wechat_command_queue.py` — 扩展 Actions

```python
# 修改 L15
ALLOWED_WECHAT_COMMAND_ACTIONS = {
    # ── 发送类（原有）──
    "send_text", "send_image", "send_file", "send_voice",
    "voice_call", "video_call",
    # ── Phase 1 新增：查询类 ──
    "get_friends", "get_recent_groups", "get_my_info",
    "get_history_message",
    # ── Phase 1 新增：控制类 ──
    "chat_with", "remove_listen_chat", "stop_listening",
    "add_listen_chat",
}
```

同时需要修改 `enqueue_wechat_command()` 函数，使其支持额外参数：

```python
def enqueue_wechat_command(
    *,
    action: str,
    target: str,
    target_candidates: list[str] | None = None,
    content: str = "",
    request_id: str = "",
    queue_file: Path | None = None,
    ts_ms: int | None = None,
    # ── Phase 1 新增 ──
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # ... 现有逻辑 ...
    row = {
        "command_id": command_id,
        "request_id": req_value,
        "action": action_value,
        "target": target_value,
        "content": content_value,
        "ts_ms": now_value,
    }
    # 合并额外参数
    if extra_params:
        for k, v in extra_params.items():
            if k not in row:  # 不覆盖核心字段
                row[k] = v
    # ...
```

---

#### 4.2.3 `edge_client/gateway/adapters/wechat_adapter.py` — 扩展出站类型

新增 `send_file` 出站支持（当前只有 text/image/voice）：

```python
# 在 send_reply() 方法中，MsgType.VOICE 之后新增：

if reply.msg_type == MsgType.FILE:
    if self._bridge_queue_enabled():
        success = self._send_file_via_bridge_queue(reply)
        detail = "bridge_queue_file"
        return success
    detail = "bridge_queue_not_enabled"
    return False

if reply.msg_type == MsgType.LINK:
    if self._bridge_queue_enabled():
        success = self._send_url_card_via_bridge_queue(reply)
        detail = "bridge_queue_url_card"
        return success
    detail = "bridge_queue_not_enabled"
    return False
```

新增查询类 API 的 Gateway HTTP 端点：

```python
# 建议在 Gateway 的 FastAPI 路由中新增
# edge_client/gateway/routes/ 下新增 wechat_query_routes.py

@router.post("/v1/gateway/wechat/query")
async def wechat_query(request: WeChatQueryRequest):
    """Execute a query command (get_friends, get_history, etc.) and return result."""
    row = enqueue_wechat_command(
        action=request.action,
        target=request.target or "",
        extra_params=request.params or {},
    )
    result = wait_wechat_command_result(
        command_id=row["command_id"],
        timeout_ms=request.timeout_ms or 8000,
    )
    return {"success": bool(result), "data": result}
```

---

#### 4.2.4 `.env.example` — 新增配置项

```bash
# ── Phase 1 新增 wxautox4 配置 ──

# 媒体文件保存目录（图片/视频/语音下载后存放位置）
# EDGE_WECHAT_MEDIA_SAVE_DIR=runtime/edge/media

# 健康检测间隔（秒），0 表示禁用
# EDGE_WECHAT_HEALTH_CHECK_INTERVAL_S=30

# 历史消息默认查询条数
# EDGE_WECHAT_HISTORY_MSG_DEFAULT_COUNT=20

# 自动获取自身信息（启动时调用 GetMyInfo）
# EDGE_WECHAT_AUTO_GET_SELF_INFO=true
```

---

#### 4.2.5 `host_brain/channels/wechat_health.py` — 增强健康检测

```python
# 利用 wxautox4 的 IsOnline() 增强健康检测逻辑
# 当使用 wxautox4 后端时，health check 可以通过命令队列发送 "is_online" 查询

def _check_wxautox4_health(self) -> bool:
    """Send is_online query via command queue and wait for result."""
    from edge_client.channels.wechat_command_queue import (
        enqueue_wechat_command,
        wait_wechat_command_result,
    )
    row = enqueue_wechat_command(action="is_online", target="")
    result = wait_wechat_command_result(
        command_id=row["command_id"],
        timeout_ms=3000,
    )
    return bool(result and result.get("success"))
```

---

### 4.3 Phase 2 详细修改

#### 4.3.1 `tools/wxautox4_bridge.py` — 信息查询扩展

```python
# OutboundProcessor._execute_command() 新增分支

elif action == "get_sub_window":
    who = str(command.get("who") or target or "").strip()
    window = self._wx.GetSubWindow(who)
    result["success"] = True
    result["data"] = str(window)
    result["status"] = "ok"

elif action == "get_all_sub_window":
    windows = self._wx.GetAllSubWindow()
    result["success"] = True
    result["data"] = [str(w) for w in windows]
    result["status"] = "ok"

elif action == "chat_info":
    info = self._wx.ChatInfo()
    result["success"] = True
    result["data"] = info
    result["status"] = "ok"

elif action == "get_session":
    sessions = self._wx.GetSession()
    result["success"] = True
    result["data"] = sessions
    result["status"] = "ok"

elif action == "get_dialog":
    dialog = self._wx.GetDialog()
    result["success"] = True
    result["data"] = dialog
    result["status"] = "ok"

elif action == "send_url_card":
    url = str(command.get("url") or "").strip()
    title = str(command.get("title") or "").strip()
    desc = str(command.get("desc") or "").strip()
    who = str(command.get("who") or target or "").strip()
    if not url or not who:
        result["reason_code"] = "WECHAT_MISSING_PARAMS"
    else:
        self._wx.SendUrlCard(url, title, desc, who=who)
        result["success"] = True
        result["status"] = "ok"

elif action == "get_all_message":
    messages = self._wx.GetAllMessage()
    result["success"] = True
    result["data"] = [_serialize_msg(m) for m in messages]
    result["status"] = "ok"

elif action == "get_new_friends":
    friends = self._wx.GetNewFriends()
    result["success"] = True
    result["data"] = friends
    result["status"] = "ok"

elif action == "get_friend_details":
    who = str(command.get("who") or target or "").strip()
    if not who:
        result["reason_code"] = "WECHAT_MISSING_TARGET"
    else:
        details = self._wx.GetFriendDetails(who)
        result["success"] = True
        result["data"] = details
        result["status"] = "ok"
```

#### 4.3.2 消息对象 — 链接消息解析

```python
# InboundProcessor._poll_once() 中增加 LinkMessage 处理

elif msg_cls_name == "LinkMessage":
    try:
        url = msg.get_url()
        media_info["link_url"] = url
        media_info["media_type"] = "link"
    except Exception as exc:
        logger.warning("LinkMessage.get_url() failed: %s", exc)
```

#### 4.3.3 命令队列 Actions 扩展

```python
ALLOWED_WECHAT_COMMAND_ACTIONS = {
    # ... Phase 1 所有 actions ...
    # ── Phase 2 新增 ──
    "get_sub_window", "get_all_sub_window",
    "chat_info", "get_session", "get_dialog",
    "send_url_card", "get_all_message",
    "get_new_friends", "get_friend_details",
}
```

---

### 4.4 Phase 3 详细修改

#### 4.4.1 朋友圈 API 对接

```python
# OutboundProcessor._execute_command() 新增分支

elif action == "publish_moment":
    content_text = str(command.get("moment_text") or content or "").strip()
    images = command.get("images") or []  # list of file paths
    self._wx.PublishMoment(content_text, images=images)
    result["success"] = True
    result["status"] = "ok"

elif action == "get_moments":
    who = str(command.get("who") or target or "").strip()
    moments = self._wx.GetMoments(who)
    result["success"] = True
    result["data"] = moments
    result["status"] = "ok"

elif action == "like_moment":
    moment_id = str(command.get("moment_id") or "").strip()
    self._wx.Like(moment_id)
    result["success"] = True
    result["status"] = "ok"

elif action == "comment_moment":
    moment_id = str(command.get("moment_id") or "").strip()
    text = str(command.get("comment_text") or content or "").strip()
    self._wx.Comment(moment_id, text)
    result["success"] = True
    result["status"] = "ok"
```

#### 4.4.2 群管理和高级消息操作

```python
elif action == "add_group_members":
    group = str(command.get("group") or target or "").strip()
    members = command.get("members") or []  # list of wxids
    self._wx.AddGroupMembers(group, members)
    result["success"] = True
    result["status"] = "ok"

elif action == "delete_message":
    # 需要保存消息对象引用
    # 此功能需要在 InboundProcessor 中维护消息对象缓存
    pass
```

---

## 五、新增辅助函数

### 5.1 消息序列化工具

`tools/wxautox4_bridge.py` 中新增：

```python
def _serialize_msg(msg: Any) -> dict[str, Any]:
    """Serialize a wxautox4 message object to a JSON-safe dict."""
    result = {}
    for attr in ("sender", "content", "type", "time", "id", "roomid"):
        val = getattr(msg, attr, None)
        if val is not None:
            result[attr] = str(val) if not isinstance(val, (int, float, bool)) else val
    # 保留消息类型名用于下游处理
    result["_msg_class"] = type(msg).__name__
    return result
```

---

## 六、Gateway HTTP 路由扩展

建议新增 `edge_client/gateway/routes/wechat_query_routes.py`：

```python
"""Gateway HTTP routes for wxautox4 query operations.

These endpoints allow Host Brain to query WeChat state
(friends, groups, history) without going through the
standard inbound/outbound message flow.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

router = APIRouter(prefix="/v1/gateway/wechat", tags=["wechat-query"])

class QueryRequest(BaseModel):
    action: str               # get_friends, get_history_message, etc.
    target: str = ""          # who parameter
    params: dict[str, Any] = {}
    timeout_ms: int = 8000

class QueryResponse(BaseModel):
    success: bool
    data: Any = None
    error: str | None = None

@router.post("/query", response_model=QueryResponse)
async def execute_query(req: QueryRequest):
    from edge_client.channels.wechat_command_queue import (
        enqueue_wechat_command,
        wait_wechat_command_result,
    )
    row = enqueue_wechat_command(
        action=req.action,
        target=req.target,
        extra_params=req.params,
    )
    result = wait_wechat_command_result(
        command_id=row["command_id"],
        timeout_ms=req.timeout_ms,
    )
    if result and result.get("success"):
        return QueryResponse(success=True, data=result.get("data"))
    error_msg = (result or {}).get("detail", "Query timeout or failed")
    return QueryResponse(success=False, error=error_msg)
```

注册到 Gateway 的 FastAPI app：
```python
# edge_client/gateway/bootstrap.py 中
from edge_client.gateway.routes.wechat_query_routes import router as wechat_query_router
app.include_router(wechat_query_router)
```

---

## 七、配置项汇总

### 新增环境变量

| 变量名 | 默认值 | Phase | 说明 |
|--------|--------|-------|------|
| `EDGE_WECHAT_MEDIA_SAVE_DIR` | `runtime/edge/media` | P1 | 媒体下载保存目录 |
| `EDGE_WECHAT_HEALTH_CHECK_INTERVAL_S` | `30` | P1 | 健康检测间隔（0=禁用） |
| `EDGE_WECHAT_HISTORY_MSG_DEFAULT_COUNT` | `20` | P1 | 历史消息默认查询条数 |
| `EDGE_WECHAT_AUTO_GET_SELF_INFO` | `true` | P1 | 启动时自动获取自身信息 |
| `EDGE_WECHAT_MEDIA_CLEANUP_DAYS` | `7` | P2 | 媒体文件自动清理天数 |

---

## 八、测试计划

### Phase 1 测试用例

| ID | 测试项 | 前置条件 | 验证步骤 | 预期结果 |
|----|--------|----------|----------|----------|
| T-P1-01 | IsOnline 健康检测 | 微信已登录 | 发送 is_online 命令 | 返回 `{"success": true}` |
| T-P1-02 | GetMyInfo 自身信息 | 微信已登录 | 启动 bridge 后检查日志 | 打印 wxid 和昵称 |
| T-P1-03 | GetFriends 好友列表 | 微信已登录 | 发送 get_friends 命令 | 返回好友列表数组 |
| T-P1-04 | GetRecentGroups 群列表 | 有群聊记录 | 发送 get_recent_groups 命令 | 返回群聊列表数组 |
| T-P1-05 | GetHistoryMessage | 有聊天记录 | 发送 get_history_message，who=某好友 | 返回历史消息数组 |
| T-P1-06 | RemoveListenChat | 已有监听目标 | 发送 remove_listen_chat | 移除成功，不再收到该目标消息 |
| T-P1-07 | 图片消息下载 | 有人发图片 | 收到图片消息后检查 media 目录 | 图片文件存在且 webhook 携带 media_url |
| T-P1-08 | 视频消息下载 | 有人发视频 | 同上 | 视频文件存在 |
| T-P1-09 | 语音消息下载 | 有人发语音 | 同上 | .silk 文件存在 |
| T-P1-10 | ChatWith 切换聊天 | 微信已登录 | 发送 chat_with 命令 | 微信窗口切换到目标聊天 |
| T-P1-11 | 自消息过滤 | GetMyInfo 成功 | 自己发消息 | InboundProcessor 不上报自己的消息 |

---

## 九、风险与注意事项

1. **wxautox4 付费授权**：部分高级 API 可能需要更高级别的授权，需在真机测试时确认
2. **API 返回格式**：wxautox4 文档对返回值的描述有时不够详细，需真机调试确认实际返回的数据结构
3. **消息对象类型**：`ImageMessage`、`VideoMessage` 等类型名需要在真机测试时通过 `type(msg).__name__` 确认
4. **媒体下载路径**：下载操作可能阻塞，建议在独立线程中执行，避免阻塞消息轮询
5. **朋友圈 API 稳定性**：朋友圈相关 API 通常依赖 UI 自动化操作，稳定性可能不如消息收发 API
6. **微信版本兼容性**：文档标注微信 4.1 版本，需确保测试环境微信版本匹配
7. **并发安全**：查询类命令和发送类命令共用同一个 wxautox4 实例，需要注意线程安全

---

## 十、实施顺序建议

```
Phase 1（核心增强）
  ├── Step 1: 修改 wxautox4_bridge.py — 新增 IsOnline/GetMyInfo/自消息过滤
  ├── Step 2: 修改 wxautox4_bridge.py — 新增媒体下载处理
  ├── Step 3: 修改 wxautox4_bridge.py — 新增查询类命令（friends/groups/history）
  ├── Step 4: 修改 wechat_command_queue.py — 扩展 ALLOWED_ACTIONS
  ├── Step 5: 修改 wechat_adapter.py — 新增 send_file 出站
  ├── Step 6: 新增 wechat_query_routes.py — Gateway 查询路由
  ├── Step 7: 修改 .env.example — 新增配置项
  ├── Step 8: 修改 wechat_health.py — 增强健康检测
  └── Step 9: 产出 DEV_TEST_REPORT 并推送

Phase 2（体验增强）
  ├── 子窗口管理、会话列表、弹窗处理
  ├── SendUrlCard 链接卡片
  ├── LinkMessage 解析
  └── 好友详情查询

Phase 3（社交扩展）
  ├── 朋友圈系列 API
  ├── 群成员管理
  └── 高级消息操作
```

---

## 附录 A：wxautox4 RESTful API 对照表

wxautox4 也提供了 `cluic/wxauto-restful-api` 项目（FastAPI 封装），我们的架构选择**直接使用 Python SDK**而非 RESTful API，因为：
1. 减少网络跳转开销（SDK 直接 COM 调用）
2. 消息对象方法（download/get_url 等）只有 SDK 支持
3. 与现有 Bridge 架构更契合

但 RESTful API 的接口设计可作为 Gateway 路由设计的参考。

---

_本文档由七海 (Codex Node) 基于 wxautox4 v40.1.11 官方 API 文档与 Natsume_Ai 仓库现有代码对比分析生成。_
_待哥哥确认后，七海会按 Phase 1 → Phase 2 → Phase 3 的顺序逐步实施代码更新。_
