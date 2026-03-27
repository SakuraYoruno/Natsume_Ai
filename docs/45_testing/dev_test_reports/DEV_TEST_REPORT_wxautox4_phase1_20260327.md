受众：MASTER|CODEX|PROJECT
标题：wxautox4 Bridge Phase 1 开发实测报告

# DEV TEST REPORT — wxautox4 Bridge Phase 1

**日期**：2026-03-27  
**作者**：Codex（开发节点）  
**状态**：OPEN（待运行节点真机验证）

---

## 1. 变更摘要

本次实施 **wxautox4 三期更新计划 Phase 1（P1 核心增强）**，在不破坏现有基础发送功能的前提下，大幅扩展了 bridge 的 API 覆盖范围。

### 修改文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `tools/wxautox4_bridge.py` | 大改（+~350行） | 新增 HealthMonitor、Phase 1 query/listen/nav actions、媒体下载 |
| `edge_client/channels/wechat_command_queue.py` | 中改 | ALLOWED_ACTIONS 扩展到 16 个；enqueue_wechat_command 新增 extra_fields 参数 |
| `edge_client/gateway/adapters/wechat_adapter.py` | 中改 | send_reply 新增 FILE 分支；新增 query_bridge/get_friends/get_groups 等方法 |
| `.env.example` | 小改 | 新增 EDGE_WECHAT_WXAUTOX4_HEALTH_INTERVAL_S 和 EDGE_WECHAT_WXAUTOX4_MEDIA_DOWNLOAD_DIR |

---

## 2. Phase 1 新增功能详解

### 2.1 HealthMonitor（IsOnline 周期检测）

新增 `HealthMonitor` 线程，每隔 `EDGE_WECHAT_WXAUTOX4_HEALTH_INTERVAL_S`（默认 30s）调用 `wx.IsOnline()` 检测微信是否在线。

- 状态变化时打印 ONLINE/OFFLINE 日志
- 为后续推送健康事件到 Gateway 预留扩展点
- 独立 daemon 线程，不影响消息收发

### 2.2 GetMyInfo 启动日志

Bridge 启动时调用 `wx.GetMyInfo()` 并打印自身账号信息，方便确认绑定了正确的微信账号。

### 2.3 媒体自动下载（InboundProcessor 增强）

新增 `media_download_dir` 配置项（`EDGE_WECHAT_WXAUTOX4_MEDIA_DOWNLOAD_DIR`）。

- 当收到 image(3)/voice(34)/video(43) 类型消息时，调用 `msg.download(savepath=dir)` 下载到本地
- 本地文件路径写入 webhook body 的 `media_url` 字段
- 配置为空（默认）则跳过下载，行为与 Phase 0 完全一致

### 2.4 Phase 1 新增 OutboundProcessor Actions

| Action | 对应 wxautox4 API | 需要 target | 说明 |
|--------|------------------|-------------|------|
| `get_my_info` | `wx.GetMyInfo()` | 否 | 返回自身账号信息 |
| `get_friends` | `wx.GetFriends()` | 否 | 返回好友列表 |
| `get_groups` | `wx.GetRecentGroups()` | 否 | 返回最近群聊列表（附 GetAllSubWindow 降级） |
| `get_recent_chats` | `wx.GetSession()` | 否 | 返回最近会话列表 |
| `get_history_messages` | `wx.GetHistoryMessage(who, savepath?)` | 是 | 返回指定聊天历史消息 |
| `listen_add` | `wx.AddListenChat(who)` | 是 | 动态添加监听目标 |
| `listen_remove` | `wx.RemoveListenChat(who)` | 是 | 移除监听目标 |
| `listen_stop` | `wx.StopListening()` | 否 | 停止所有监听 |
| `chat_with` | `wx.ChatWith(who)` | 是 | 导航到指定聊天窗口 |

所有结果写入 `wechat_command_result.jsonl` 的 `data` 字段（JSON 序列化）。

### 2.5 WeChatAdapter 增强

- `send_reply()` 新增 `MsgType.FILE` 分支，调用 `_send_file_via_bridge_queue()`
- 新增 `query_bridge(action, target, extra)` 通用查询方法
- 新增便捷方法：`get_my_info()`、`get_friends()`、`get_groups()`、`get_recent_chats()`、`get_history_messages(target, savepath)`

### 2.6 enqueue_wechat_command extra_fields

`enqueue_wechat_command` 新增 `extra_fields: dict | None` 参数，支持将 `savepath` 等额外字段写入 JSONL command row，供 bridge 读取。

---

## 3. 新增环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `EDGE_WECHAT_WXAUTOX4_HEALTH_INTERVAL_S` | `30` | IsOnline 健康检测间隔（秒） |
| `EDGE_WECHAT_WXAUTOX4_MEDIA_DOWNLOAD_DIR` | `` | 媒体自动下载目录，空 = 禁用 |

---

## 4. 向后兼容性说明

- ✅ 所有原有的 `send_text/send_image/send_file/send_voice` 逻辑**未改动**
- ✅ JSONL 协议格式**未变化**，新增字段均为可选
- ✅ 未设置 `EDGE_WECHAT_WXAUTOX4_MEDIA_DOWNLOAD_DIR` 时行为与 Phase 0 完全一致
- ✅ `enqueue_wechat_command` 的 `extra_fields=None` 默认值保持向后兼容

---

## 5. 静态验证结果

| 文件 | Lint 报错数 | 状态 |
|------|------------|------|
| `tools/wxautox4_bridge.py` | 0 | ✅ PASS |
| `edge_client/channels/wechat_command_queue.py` | 0 | ✅ PASS |
| `edge_client/gateway/adapters/wechat_adapter.py` | 0 | ✅ PASS |

---

## 6. 待真机验证测试用例（T-P1-xx）

| 用例 ID | 测试内容 | 预期结果 | 优先级 |
|---------|---------|---------|--------|
| T-P1-01 | Bridge 启动后 GetMyInfo 日志输出 | 日志打印自身微信账号昵称和 wxid | P1 |
| T-P1-02 | IsOnline 健康检测触发 | 每 30s 打印 "WeChat online status: ONLINE ✓" | P1 |
| T-P1-03 | get_friends action | 返回好友列表，count > 0 | P1 |
| T-P1-04 | get_groups action | 返回群聊列表 | P1 |
| T-P1-05 | get_recent_chats action | 返回最近会话列表 | P1 |
| T-P1-06 | get_history_messages action | 返回指定聊天的历史消息列表 | P1 |
| T-P1-07 | listen_add / listen_remove action | 动态添加/移除监听目标后 GetNewMessage 行为变化 | P2 |
| T-P1-08 | chat_with action | 微信窗口切换到指定聊天 | P2 |
| T-P1-09 | 收到图片时媒体自动下载（需配置 MEDIA_DOWNLOAD_DIR） | media_url 字段含本地文件路径 | P2 |
| T-P1-10 | send_file via WeChatAdapter（MsgType.FILE） | 文件发送成功 | P1 |

---

## 7. 已知风险和注意事项

1. **GetRecentGroups / GetAllSubWindow fallback**：wxautox4 v40.1.x 文档中 `GetRecentGroups` 与 `GetAllSubWindow` 行为差异待真机确认，代码已做自动降级
2. **msg.download() 方法**：不是所有消息类型都有此方法，代码已做 AttributeError 捕获
3. **历史消息 savepath**：`GetHistoryMessage` 不传 savepath 时直接返回对象列表，传 savepath 时行为待确认（可能是保存 CSV/JSON 文件）
4. **query action 超时**：查询类操作默认超时 = 发送超时 × 3，如查询数据量大可能仍超时

---

## 8. 下一步

- 运行节点执行真机验证（T-P1-01 ~ T-P1-10）
- 提交 RUNTIME_FEEDBACK 报告
- 确认稳定后实施 Phase 2（子窗口管理、SendUrlCard、GetAllMessage 等）
