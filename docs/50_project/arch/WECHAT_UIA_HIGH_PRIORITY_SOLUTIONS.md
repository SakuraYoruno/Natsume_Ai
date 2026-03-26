受众：MASTER、实现层
标题：微信适配器高优先级问题解决方案

# 微信适配器高优先级问题解决方案

> 日期：2026-03-26 | 版本：v1.0 | 状态：已实施（commit 48c2e02）

## 现状总结

| # | 问题 | 严重程度 | 影响 |
|---|------|---------|------|
| H1 | 只能监听当前激活聊天（无多聊天轮询） | ⚠️ 高 | 用户手动切换聊天后，之前聊天的新消息完全遗漏 |
| H2 | 群聊中无法区分不同成员 | ⚠️ 高 | sender/from_wxid/conversation_id 全是群名，Host Brain 无法知道"谁在说话" |
| H3 | 仅支持文本消息 | ⚠️ 高 | 图片/语音/视频/表情/文件全部被忽略 |

---

## H1：多聊天轮询方案

### 问题根因

`process_cycle()` 每次只调用 `collect_inbound_messages()`，而该方法仅读取**当前打开的聊天窗口**的消息。虽然 `open_chat()` 已经实现了主动切换聊天的能力，但只在**出站发送**时调用，**入站轮询**完全不用。

### 方案：「通知列表嗅探」 + 「按需切换」 双模式

**核心思路**：不做"暴力轮流切换"（会导致 UI 跳动），而是通过**左侧聊天列表的未读标记**检测哪些聊天有新消息，只在检测到新消息时才切换过去收取。

#### 方案细节

**Step 1：新增 `_scan_chat_list_unread()` 方法** — UIA Client 层

```python
def _scan_chat_list_unread(self, root: IUIAutomationElement) -> list[tuple[str, int]]:
    """扫描左侧聊天列表，返回有未读消息的聊天 [(chat_name, unread_count), ...]。
    
    微信左侧列表中，每个聊天项是 ListItem，包含：
    - 聊天名称（Text 控件）
    - 未读数字气泡（Text 控件，纯数字，位于右上角）
    
    通过位置约束（left_panel_ratio_max）限定在左侧面板。
    """
    root_rect = self._rect(root)
    if root_rect is None:
        return []
    left, top, right, bottom = root_rect
    width = max(1, right - left)
    max_x = left + int(width * DEFAULT_LEFT_PANEL_RATIO_MAX)
    
    results: list[tuple[str, int]] = []
    for element in self._descendants(root):
        if self._control_type(element) not in {UIA_ListItemControlTypeId}:
            continue
        rect = self._rect(element)
        if rect is None:
            continue
        el_left, el_top, el_right, _el_bottom = rect
        if el_right > max_x:
            continue
        
        # 提取子控件：聊天名 + 未读数
        chat_name = ""
        unread_count = 0
        for child in self._descendants(element, limit=20):
            if self._control_type(child) != UIA_TextControlTypeId:
                continue
            text = self._name(child)
            if not text:
                continue
            # 纯数字且 <= 3 位 → 未读计数
            if re.fullmatch(r"\d{1,3}", text):
                child_rect = self._rect(child)
                if child_rect:
                    cx = (child_rect[0] + child_rect[2]) / 2
                    # 未读气泡在右侧
                    if cx > (el_left + el_right) / 2:
                        unread_count = int(text)
                        continue
            # 过滤噪音文本（时间、系统标签等）
            if not _is_noise_text(text) and len(text) > len(chat_name):
                chat_name = text
        
        if chat_name and unread_count > 0:
            if not _is_blocked_chat_target(chat_name):
                results.append((chat_name, unread_count))
    
    return results
```

**Step 2：修改 `WeChatPureUiaInboundBridge.process_cycle()`** — 增加多聊天调度

```python
def process_cycle(self) -> int:
    if not self._enabled:
        return 0
    now_mono = time.monotonic()
    if now_mono < self._next_poll_at:
        return 0
    self._next_poll_at = now_mono + self._poll_interval_s
    
    # === 新增：扫描左侧列表的未读聊天 ===
    root = self._client.get_main_element()
    if root is None:
        return 0
    
    # 先收取当前聊天的消息（原有逻辑）
    forwarded = self._collect_and_forward(root)
    
    # 再检查是否有其他聊天有未读消息
    if self._multi_chat_enabled:
        unread_chats = self._client._scan_chat_list_unread(root)
        current_title = self._client._active_chat_title(root)
        for chat_name, count in unread_chats:
            if _normalize_match_text(chat_name) == _normalize_match_text(current_title):
                continue  # 当前聊天已处理
            # 切换到未读聊天
            ok, resolved, detail = self._client.open_chat(target=chat_name)
            if ok:
                time.sleep(0.3)  # 等待 UI 刷新
                new_root = self._client.get_main_element() or root
                forwarded += self._collect_and_forward(new_root)
        
        # 切回原聊天（避免用户感知到跳动）
        if unread_chats and current_title:
            self._client.open_chat(target=current_title)
    
    return forwarded
```

**Step 3：环境变量控制**

```env
# 是否启用多聊天轮询（默认 false，需用户手动开启）
EDGE_WECHAT_MULTI_CHAT_POLL=false
# 多聊天轮询间隔（秒，比普通轮询慢，避免频繁切换）
EDGE_WECHAT_MULTI_CHAT_POLL_INTERVAL_S=5.0
```

#### 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 切换聊天时 UI 跳动 | ①只在检测到未读时切换 ②收完切回原聊天 ③默认关闭，需手动开启 |
| 切换过程中遗漏消息 | baseline 机制重置：每次切到新聊天时重建 known_signatures |
| 切换速度受 UI 响应影响 | 多聊天轮询间隔设为 5s（远慢于普通轮询的 1.2s） |
| 聊天列表需滚动 | 初期只处理可见区域的未读聊天，后续可扩展滚动逻辑 |

#### 改动范围

| 文件 | 改动 |
|------|------|
| `tools/wechat_uia_bridge.py` | 新增 `_scan_chat_list_unread()`；修改 `process_cycle()` |
| `.env.example` | 新增 `EDGE_WECHAT_MULTI_CHAT_POLL` 等变量 |

---

## H2：群聊发送者识别方案

### 问题根因

`_message_item_candidates()` 遍历消息项子控件时，把所有 Text 控件的文本拼接为 content，完全没有尝试区分"发送者昵称"和"消息正文"。最终 sender 固定为 `active_title`（群名）。

### 方案：「位置启发式」群成员昵称提取

**核心思路**：微信群聊中，每条他人消息的气泡**上方**或**左上方**会显示发送者昵称。利用子控件的**相对位置关系**来区分昵称和正文。

#### 方案细节

**Step 1：修改 `_message_item_candidates()` — 昵称提取逻辑**

```python
def _message_item_candidates(self, root, *, active_title: str) -> list[UiaMessageCandidate]:
    # ... 现有逻辑获取 class_targets、items 等 ...
    
    for element in self._descendants(root):
        if _normalize_match_text(self._class_name(element)) not in class_targets:
            continue
        rect = self._rect(element)
        if rect is None:
            continue
        el_left, el_top, el_right, _el_bottom = rect
        center_x = int((el_left + el_right) / 2)
        if center_x <= min_x or center_x >= max_x:
            continue
        
        # === 新增：分离昵称和正文 ===
        nickname = ""
        texts: list[str] = []
        children = self._descendants(element, limit=120)
        text_children = []
        
        for child in children:
            if self._control_type(child) != UIA_TextControlTypeId:
                continue
            text_value = self._name(child)
            if _is_noise_text(text_value, active_title=active_title):
                continue
            child_rect = self._rect(child)
            if child_rect is None:
                continue
            text_children.append((child_rect, text_value))
        
        if len(text_children) >= 2:
            # 按 top 排序
            text_children.sort(key=lambda x: x[0][1])
            first_rect, first_text = text_children[0]
            second_rect, second_text = text_children[1]
            
            # 启发式规则：
            # 1. 第一个文本控件在第二个上方至少 5px
            # 2. 第一个文本控件较短（通常昵称 < 20 字符）
            # 3. 第一个文本控件的字体大小（通过高度推断）较小
            y_gap = second_rect[1] - first_rect[3]  # 第二个 top - 第一个 bottom
            first_height = first_rect[3] - first_rect[1]
            second_height = second_rect[3] - second_rect[1]
            
            if (y_gap >= 2 
                and len(first_text) <= 20 
                and first_height <= second_height):
                # 第一个是昵称
                nickname = first_text
                texts = [t for _, t in text_children[1:] if t not in texts]
            else:
                texts = [t for _, t in text_children if t not in texts]
        else:
            texts = [t for _, t in text_children]
        
        content = _normalize_message_text(" ".join(texts))
        if not content or _is_recent_outbound_echo(content, active_chat=active_title):
            continue
        
        # sender 优先用提取到的 nickname，fallback 到 active_title
        sender = nickname or active_title or "active_chat"
        
        items.append(UiaMessageCandidate(
            sender, content,
            f"{active_title}|{sender}|{content}|{el_top}", el_top
        ))
    
    items.sort(key=lambda item: item.top)
    return items
```

**Step 2：修改 `process_cycle()` webhook payload** — 传递群聊元数据

```python
body = {
    "id": msg_id,
    "msg_id": msg_id,
    "sender": item.sender,          # 现在可能是个人昵称
    "sender_name": item.sender,     # 新增：明确的发送者名称
    "from_wxid": item.sender,       # 群聊中为昵称，私聊中为聊天标题
    "receiver": "local_wechat",
    "to_wxid": "local_wechat",
    "conversation_id": active_title, # 保持为群名/聊天标题
    "content": item.content,
    "text": item.content,
    "type": 1,
    "msg_type": "1",
    "ts": int(ts_ms / 1000),
    "bridge_backend": "uia_hook",
    # === 新增群聊标记 ===
    "is_group_hint": item.sender != active_title,  # sender ≠ 标题 → 可能是群聊
}
```

**Step 3：修改 `WeChatAdapter.parse_inbound()`** — 利用群聊提示

```python
# 在 parse_inbound 中增加群聊检测逻辑
is_group = (
    conversation_id.endswith("@chatroom")  # 原有逻辑
    or bool(raw_payload.get("is_group_hint"))  # 新增：UIA bridge 提示
)

# sender_name 优先使用 bridge 传来的 sender_name
sender_name = str(
    raw_payload.get("sender_name") 
    or raw_payload.get("sender") 
    or from_wxid 
    or ""
).strip() or None
```

#### 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 位置启发式可能不准 | ①设置合理阈值 ②fallback 到 active_title（不会更差） ③日志记录识别详情 |
| 微信版本升级改变 UI 布局 | ①核心参数（y_gap 阈值等）通过环境变量可配置 ②降级不影响功能 |
| 自己发的消息也有昵称 | ①通过 x 坐标判断（右侧 = 自己发的） ②结合回显过滤 |
| 无法获取群成员的 wxid | UIA 树天然无法获取 wxid，只能获取显示昵称，这是 UIA 方案的根本限制 |

#### 改动范围

| 文件 | 改动 |
|------|------|
| `tools/wechat_uia_bridge.py` | 修改 `_message_item_candidates()` 的昵称提取逻辑；修改 `process_cycle()` payload |
| `edge_client/gateway/adapters/wechat_adapter.py` | 修改 `parse_inbound()` 的 is_group 判定逻辑 |

---

## H3：多媒体消息支持方案

### 问题根因

UIA Bridge 在提取消息时只关注 `UIA_TextControlTypeId`，完全忽略图片/语音/视频控件。且微信桌面版的 UIA 树**不直接暴露媒体文件路径**——这是 UIA 方案的根本局限。

### 方案：「类型感知 + 占位通知」 分阶段实现

这个问题比前两个复杂得多，建议分**两个阶段**推进。

#### 阶段一：媒体消息类型检测 + 占位通知（可快速实现）

**目标**：让系统知道"有人发了一张图片"，即使无法获取图片内容。

**Step 1：扩展消息项类型检测**

```python
# 新增消息项 CSS 类匹配（在 schema 中扩展）
# 微信 4.x 中不同类型的消息项有不同的 CSS 类名
WECHAT_MEDIA_ITEM_CLASSES = {
    "mmui::ChatImageItemView": "image",      # 图片消息
    "mmui::ChatVoiceItemView": "voice",       # 语音消息
    "mmui::ChatVideoItemView": "video",       # 视频消息
    "mmui::ChatFileItemView": "file",         # 文件消息
    "mmui::ChatEmojiItemView": "emoji",       # 表情消息
    "mmui::ChatLinkItemView": "link",         # 链接消息
}
```

**Step 2：新增 `_message_media_candidates()` 方法**

```python
def _message_media_candidates(self, root, *, active_title: str) -> list[UiaMessageCandidate]:
    """检测非文本消息项，生成占位通知。"""
    root_rect = self._rect(root)
    if root_rect is None:
        return []
    left, _top, right, _bottom = root_rect
    width = max(1, right - left)
    min_x = left + int(width * 0.22)
    max_x = left + int(width * DEFAULT_INBOUND_X_RATIO_MAX)
    
    items: list[UiaMessageCandidate] = []
    for element in self._descendants(root):
        class_name = self._class_name(element)
        class_norm = _normalize_match_text(class_name)
        
        media_type = None
        for cls_hint, mtype in WECHAT_MEDIA_ITEM_CLASSES.items():
            if _normalize_match_text(cls_hint) in class_norm:
                media_type = mtype
                break
        if media_type is None:
            continue
        
        rect = self._rect(element)
        if rect is None:
            continue
        el_left, el_top, el_right, _el_bottom = rect
        center_x = int((el_left + el_right) / 2)
        if center_x <= min_x or center_x >= max_x:
            continue
        
        # 生成占位内容
        content = f"[{media_type}]"
        signature = f"{active_title}|{media_type}|{el_top}"
        
        items.append(UiaMessageCandidate(
            active_title or "active_chat",
            content,
            signature,
            el_top
        ))
    
    items.sort(key=lambda item: item.top)
    return items
```

**Step 3：修改 `collect_inbound_messages()` — 合并文本和媒体消息**

```python
def collect_inbound_messages(self) -> tuple[str, list[UiaMessageCandidate]]:
    root = self.get_main_element()
    if root is None:
        return "", []
    active_title = self._active_chat_title(root)
    if active_title and (_is_blocked_chat_target(active_title) or not _is_allowed_contact_target(active_title)):
        return active_title, []
    
    # 文本消息（原有逻辑）
    text_messages = self._message_item_candidates(root, active_title=active_title)
    if not text_messages:
        text_messages = self._message_text_fallback(root, active_title=active_title)
    
    # 媒体消息（新增）
    media_messages = self._message_media_candidates(root, active_title=active_title)
    
    # 按 top 坐标合并排序
    all_messages = text_messages + media_messages
    all_messages.sort(key=lambda item: item.top)
    return active_title, all_messages
```

**Step 4：修改 `process_cycle()` payload — 传递消息类型**

```python
# 根据 content 判断消息类型
MEDIA_TYPE_MAP = {
    "[image]": 3,
    "[voice]": 34,
    "[video]": 43,
    "[emoji]": 47,
    "[link]": 49,
    "[file]": 49,
}

msg_type_code = MEDIA_TYPE_MAP.get(item.content, 1)
body = {
    # ... 现有字段 ...
    "type": msg_type_code,
    "msg_type": str(msg_type_code),
}
```

#### 阶段二：图片/文件实际获取（已实施）

选择了**监控微信缓存目录**方案（可行性 ★★★★），实现了 `WeChatCacheWatcher` 类：

| 组件 | 说明 |
|------|------|
| `WeChatCacheWatcher` | 监控 FileStorage 7 个子目录（Image/Video/File/Voice/Thumb/Emoji/Cache），TTL 时间窗口关联 |
| `consume_file()` | 防止重复匹配，文件被消费后标记 |
| `UiaMessageCandidate.media_path` | 新增可选字段，关联到的文件路径 |
| `send_image` 命令 | 剪贴板粘贴方式发送图片（clipboard → Ctrl+V → send） |
| 命令队列扩展 | `ALLOWED_WECHAT_COMMAND_ACTIONS` 新增 `send_image`、`send_file` |

**环境变量**：

```env
EDGE_WECHAT_CACHE_DIR=              # 微信 FileStorage 目录路径
EDGE_WECHAT_CACHE_WATCH_ENABLED=false  # 是否启用缓存监控
EDGE_WECHAT_CACHE_WATCH_TTL_S=30     # 文件关联 TTL（秒）
```

#### 改动范围

| 文件 | 改动 |
|------|------|
| `tools/wechat_uia_bridge.py` | 新增 `_message_media_candidates()`、`WeChatCacheWatcher`；修改 `collect_inbound_messages()` 和 `process_cycle()`；新增 `send_image`/`send_file` 命令处理 |
| `edge_client/gateway/adapters/wechat_adapter.py` | 修改 `send_reply()` 支持 `MsgType.IMAGE`；新增 `_send_image_via_bridge_queue()` |
| `edge_client/channels/wechat_command_queue.py` | `ALLOWED_WECHAT_COMMAND_ACTIONS` 扩展 |

---

## 实施优先级建议

| 顺序 | 问题 | 预计工时 | 理由 |
|------|------|---------|------|
| 1️⃣ | H2 群聊发送者识别 | 2h | 改动最小，价值最高——群聊是主要使用场景 |
| 2️⃣ | H3-阶段一 媒体类型感知 | 2h | 让系统感知多媒体消息的存在，为后续打基础 |
| 3️⃣ | H1 多聊天轮询 | 3h | 功能完整但需要谨慎测试 UI 切换的稳定性 |
| 4️⃣ | H3-阶段二 媒体内容获取 | 3h | 监控缓存目录 + send_image 发图 |

> **实施状态**：H1/H2/H3（含两个阶段）已全部实施完毕，commit `48c2e02`，推送至 `codex` 分支。

---

## 注意事项

1. **所有改动默认关闭**：通过环境变量控制开关，不影响现有稳定功能
2. **向后兼容**：新字段都是可选的，不修改现有接口签名
3. **日志充分**：所有新逻辑都需要 debug 级别日志，便于排查问题
4. **测试先行**：每个功能都需要在真实微信窗口上验证 UIA 树结构
