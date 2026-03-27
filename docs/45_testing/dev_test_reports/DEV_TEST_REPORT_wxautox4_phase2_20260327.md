受众：MASTER|CODEX|PROJECT
标题：wxautox4 Bridge Phase 2 开发实测报告

# DEV TEST REPORT — wxautox4 Bridge Phase 2

**日期**：2026-03-27  
**作者**：Codex（开发节点）  
**状态**：OPEN（待运行节点真机验证）

---

## 1. 变更摘要

本次实施 **wxautox4 三期更新计划 Phase 2（体验增强）**，在 Phase 1 基础上继续扩展信息查询和交互能力。

### 修改文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `tools/wxautox4_bridge.py` | 中改（+~120行） | 新增 8 个 Phase 2 actions、LinkMessage.get_url() 解析、_extract_link_url() 方法 |
| `edge_client/channels/wechat_command_queue.py` | 小改 | ALLOWED_ACTIONS 从 16 扩展到 26 个 |
| `edge_client/gateway/wechat_query_routes.py` | **新建** | Gateway HTTP 查询端点 POST /v1/gateway/wechat/query |
| `edge_client/gateway/server.py` | 小改 | 注册 wechat_query_router |

---

## 2. Phase 2 新增功能详解

### 2.1 新增 OutboundProcessor Actions（8个）

| Action | 对应 API | 需要 target | 额外参数 | 说明 |
|--------|---------|-------------|---------|------|
| `get_sub_window` | `wx.GetSubWindow(who)` | 是 | — | 获取指定联系人的子窗口对象 |
| `get_all_sub_windows` | `wx.GetAllSubWindow()` | 否 | — | 获取所有已打开的子窗口列表 |
| `get_chat_info` | `wx.ChatInfo()` | 否 | — | 获取当前聚焦聊天的详情 |
| `get_dialog` | `wx.GetDialog()` | 否 | — | 检测并返回当前弹窗/对话框 |
| `send_url_card` | `wx.SendUrlCard(url, title, desc, who)` | 是 | `url`（必填）、`title`、`desc` | 发送链接卡片消息 |
| `get_all_messages` | `wx.GetAllMessage()` | 否 | — | 获取当前聊天视图的全部消息 |
| `get_new_friends` | `wx.GetNewFriends()` | 否 | — | 获取待处理好友请求列表 |
| `get_friend_details` | `wx.GetFriendDetails(who)` | 是 | — | 获取指定好友的详细信息 |

### 2.2 LinkMessage.get_url() 解析（InboundProcessor 增强）

收到链接类型（type=49）消息时，调用 `msg.get_url()` 提取 URL：
- URL 写入 webhook body 的 `link_url` 字段
- 已有 `media_url` 字段不受影响，两者可共存
- AttributeError / 非 LinkMessage 对象会静默跳过

### 2.3 新 Gateway HTTP 端点

**`POST /v1/gateway/wechat/query`**

Host Brain 可通过此端点直接查询微信状态，无需走消息轮询通道：

```json
// 请求
{
  "action": "get_friends",
  "target": "",
  "extra": {},
  "timeout_ms": 24000
}

// 成功响应
{
  "success": true,
  "data": [...],
  "count": 42
}

// 失败响应（HTTP 504）
{
  "success": false,
  "reason_code": "TIMEOUT",
  "detail": "No result within 24000ms"
}
```

支持 `extra` 字段透传任意参数（如 `send_url_card` 的 `url`/`title`/`desc`，`get_history_messages` 的 `savepath`）。

---

## 3. 向后兼容性说明

- ✅ Phase 1 所有功能**未改动**
- ✅ `wechat_query_routes.py` 是新增文件，不影响现有路由
- ✅ `server.py` 仅新增一行 `app.include_router()`，不改任何现有端点
- ✅ `_extract_link_url()` 是新增私有方法，`_try_download_media()` 逻辑不变

---

## 4. 静态验证结果

| 文件 | Lint 报错数 | 状态 |
|------|------------|------|
| `tools/wxautox4_bridge.py` | 0 | ✅ PASS |
| `edge_client/channels/wechat_command_queue.py` | 0 | ✅ PASS |
| `edge_client/gateway/wechat_query_routes.py` | 0 | ✅ PASS |
| `edge_client/gateway/server.py` | 0 | ✅ PASS |

---

## 5. 待真机验证测试用例（T-P2-xx）

| 用例 ID | 测试内容 | 预期结果 | 优先级 |
|---------|---------|---------|--------|
| T-P2-01 | GET /v1/gateway/wechat/query + get_friends | HTTP 200，data 包含好友列表 | P1 |
| T-P2-02 | GET /v1/gateway/wechat/query + get_new_friends | 返回待处理好友请求（可为空数组） | P1 |
| T-P2-03 | get_friend_details action + 某好友 wxid | 返回好友昵称、头像等详情 | P1 |
| T-P2-04 | get_chat_info action | 返回当前聊天窗口信息 | P2 |
| T-P2-05 | get_sub_window action + 某好友 | 返回子窗口对象描述 | P2 |
| T-P2-06 | get_all_sub_windows action | 返回所有已打开子窗口列表 | P2 |
| T-P2-07 | get_dialog action（无弹窗时） | `has_dialog: false` | P2 |
| T-P2-08 | send_url_card action + url/title/desc | 对方收到链接卡片消息 | P1 |
| T-P2-09 | get_all_messages action（在某聊天窗口激活时） | 返回当前聊天的全部消息列表 | P2 |
| T-P2-10 | 收到链接消息时 webhook body 含 link_url 字段 | link_url 为有效 URL 字符串 | P1 |
| T-P2-11 | /v1/gateway/wechat/query 传入未知 action | HTTP 400 错误响应 | P1 |

---

## 6. 已知风险和注意事项

1. **get_chat_info 依赖当前焦点聊天**：需要微信窗口处于某个聊天界面，否则返回值可能为 None
2. **get_all_messages 数据量**：数据量大时序列化耗时，建议不要频繁调用
3. **send_url_card UI 依赖**：部分版本可能依赖 UI 操作，稳定性待真机确认
4. **get_dialog 轮询时机**：需在弹窗出现时立即调用，否则返回 None
5. **wechat_query_routes extra 字段**：extra 中的 content 字段会被提取为标准 content 参数，其余字段作为 extra_fields

---

## 7. 下一步

- 运行节点执行真机验证（T-P2-01 ~ T-P2-11）
- 提交 RUNTIME_FEEDBACK 报告
- 确认稳定后实施 Phase 3（朋友圈系列 API、群管理、高级消息操作）
