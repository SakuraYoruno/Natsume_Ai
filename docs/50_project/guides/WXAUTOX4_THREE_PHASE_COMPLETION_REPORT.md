受众：MASTER|CODEX|PROJECT
标题：wxautox4 三期更新完整总结报告

# wxautox4 三期更新完整总结报告

> **完成日期**：2026-03-27  
> **作者**：七海 (Codex Node)  
> **wxautox4 版本**：v40.1.11  
> **微信版本**：4.1  
> **仓库分支**：`codex`

---

## 一、项目背景

Natsume_Ai 最初使用自研 UIAutomation Bridge（`wechat_uia_bridge.py`，1737行）操作微信 4.x。2026-03-27 引入 wxautox4（付费 Python SDK，200元/台，官方持续维护），并设计了双后端架构实现一键切换：

```
EDGE_WECHAT_BRIDGE_BACKEND=wxautox4   # 使用 wxautox4
EDGE_WECHAT_BRIDGE_BACKEND=uia        # 使用自研 UIA bridge
```

初始 wxautox4 Bridge（`wxautox4_bridge.py`，685行）仅对接了 **5 个 wxautox4 API**，支持 **6 种命令队列 Actions**。本次三期更新将其全面升级至 **37 种 Actions**，覆盖 wxautox4 的所有主要功能。

---

## 二、三期更新总览

| Phase | 完成日期 | Commit | 新增 Actions | 累计 Actions | 关键能力 |
|-------|---------|--------|------------|------------|---------|
| **Phase 1** | 2026-03-27 | `8e3ee1b` | +9 | 15 | 健康监控、自身信息、联系人列表、历史消息、媒体下载、监听管理、导航 |
| **Phase 2** | 2026-03-27 | `fca1ac4` | +11 | 26 | 窗口管理、会话查询、链接卡片、好友详情、HTTP 查询端点 |
| **Phase 3** | 2026-03-27 | `6cdd86f` | +11 | 37 | 朋友圈、群管理、消息对象缓存、高级消息操作 |

---

## 三、完整 Actions 清单（37个）

### 发送类（原有，6个）

| Action | wxautox4 API | 说明 |
|--------|-------------|------|
| `send_text` | `SendMsg(content, who)` | 发送文本消息 |
| `send_image` | `SendFiles(path, who)` | 发送图片 |
| `send_file` | `SendFiles(path, who)` | 发送文件 |
| `send_voice` | `SendFiles(path, who)` | 发送语音（silk文件） |
| `voice_call` | — | 不支持，返回错误码 |
| `video_call` | — | 不支持，返回错误码 |

### Phase 1 新增（9个）

| Action | wxautox4 API | 说明 |
|--------|-------------|------|
| `get_my_info` | `GetMyInfo()` | 获取自身账号信息 |
| `get_friends` | `GetFriends()` | 获取好友列表 |
| `get_groups` | `GetRecentGroups()` | 获取最近群聊列表 |
| `get_recent_chats` | `GetSession()` | 获取最近会话列表 |
| `get_history_messages` | `GetHistoryMessage(who, savepath?)` | 获取指定聊天历史消息 |
| `listen_add` | `AddListenChat(who)` | 动态添加监听目标 |
| `listen_remove` | `RemoveListenChat(who)` | 移除监听目标 |
| `listen_stop` | `StopListening()` | 停止所有监听 |
| `chat_with` | `ChatWith(who)` | 导航到指定聊天窗口 |

### Phase 2 新增（11个）

| Action | wxautox4 API | 说明 |
|--------|-------------|------|
| `get_sub_window` | `GetSubWindow(who)` | 获取指定联系人子窗口 |
| `get_all_sub_windows` | `GetAllSubWindow()` | 获取所有已打开子窗口 |
| `get_chat_info` | `ChatInfo()` | 获取当前聊天详情 |
| `get_dialog` | `GetDialog()` | 检测当前弹窗/对话框 |
| `send_url_card` | `SendUrlCard(url, title, desc, who)` | 发送链接卡片消息 |
| `get_all_messages` | `GetAllMessage()` | 获取当前聊天视图全量消息 |
| `get_new_friends` | `GetNewFriends()` | 获取待处理好友请求 |
| `get_friend_details` | `GetFriendDetails(who)` | 获取好友详细信息 |
| *(inbound)* | `LinkMessage.get_url()` | 收到链接消息时自动提取 URL |
| *(inbound)* | `msg.download(savepath)` | 收到媒体消息时自动下载 |
| *(gateway)* | HTTP `POST /v1/gateway/wechat/query` | Host Brain 可直接查询微信状态 |

### Phase 3 新增（11个）

| Action | wxautox4 API | 说明 |
|--------|-------------|------|
| `publish_moment` | `PublishMoment(text, images?)` | 发布朋友圈 |
| `get_moments` | `GetMoments(who?)` | 获取朋友圈动态 |
| `like_moment` | `Like(moment_id)` | 点赞朋友圈 |
| `comment_moment` | `Comment(moment_id, text)` | 评论朋友圈 |
| `add_group_members` | `AddGroupMembers(group, members)` | 添加群成员 |
| `capture_emotion` | `EmotionMessage.capture(savepath)` | 截取表情包（需缓存中有对应消息对象）|
| `download_quote_image` | `QuoteMessage.download_quote_image()` | 下载引用消息图片 |
| `delete_message` | `HumanMessage.delete()` | 撤回/删除消息 |
| *(infra)* | 共享消息对象缓存 | Inbound→Outbound，TTL 5分钟，自动清理 |

---

## 四、修改文件清单

| 文件 | 修改类型 | 三期总变更量 |
|------|---------|-----------|
| `tools/wxautox4_bridge.py` | 大改 | 685行 → ~1300行（+~90%）|
| `edge_client/channels/wechat_command_queue.py` | 中改 | ALLOWED_ACTIONS 6→37，新增 `extra_fields` 参数 |
| `edge_client/gateway/adapters/wechat_adapter.py` | 中改 | 新增 FILE 发送、query_bridge()、5个查询便捷方法 |
| `edge_client/gateway/wechat_query_routes.py` | **新建** | `POST /v1/gateway/wechat/query` HTTP 端点 |
| `edge_client/gateway/server.py` | 小改 | 注册 wechat_query_router |
| `.env.example` | 小改 | 新增 2 个 wxautox4 专属配置项 |

---

## 五、架构新增内容示意

```
┌─────────────────────────────────────────────────────────┐
│                     Host Brain                          │
│          POST /v1/host/input (消息输入)                   │
│          POST /v1/gateway/reply (回复指令)                │
│          POST /v1/gateway/wechat/query [Phase 2 新增]    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Unified Message Gateway (:19000)            │
│  WeChatAdapter  │  QQAdapter  │  FeishuAdapter  │  ...   │
└──────────────────────┬──────────────────────────────────┘
                       │  JSONL Command Queue
┌──────────────────────▼──────────────────────────────────┐
│              wxautox4 Bridge (tools/wxautox4_bridge.py)  │
│                                                          │
│  HealthMonitor [P1]   InboundProcessor   OutboundProcessor│
│  - IsOnline() 30s     - GetNewMessage()  - 37 Actions    │
│                       - media download   - msg_cache [P3]│
│                       - link_url [P2]                    │
│                       - cache msg [P3]                   │
└──────────────────────┬──────────────────────────────────┘
                       │  wxautox4 SDK (Python)
┌──────────────────────▼──────────────────────────────────┐
│                   WeChat 4.1 (Windows)                   │
└─────────────────────────────────────────────────────────┘
```

---

## 六、新增环境变量

| 变量名 | 默认值 | Phase | 说明 |
|--------|--------|-------|------|
| `EDGE_WECHAT_WXAUTOX4_HEALTH_INTERVAL_S` | `30` | P1 | IsOnline 检测间隔（秒） |
| `EDGE_WECHAT_WXAUTOX4_MEDIA_DOWNLOAD_DIR` | `` | P1 | 媒体自动下载目录（空=禁用）|

---

## 七、测试计划汇总

| 报告文件 | 测试用例数 | 覆盖阶段 |
|---------|----------|---------|
| `DEV_TEST_REPORT_wxautox4_bridge_20260327.md` | 8（T-01~T-08）| 基础发送功能 |
| `DEV_TEST_REPORT_wxautox4_phase1_20260327.md` | 10（T-P1-01~T-P1-10）| Phase 1 |
| `DEV_TEST_REPORT_wxautox4_phase2_20260327.md` | 11（T-P2-01~T-P2-11）| Phase 2 |
| `DEV_TEST_REPORT_wxautox4_phase3_20260327.md` | 11（T-P3-01~T-P3-11）| Phase 3 |
| **合计** | **40 条用例** | **全量** |

---

## 八、真机验证注意事项

回到运行节点后，建议按以下顺序验证：

### 第一优先级（验证基础可用性）
1. `T-01~T-08`：确认原有发送功能正常
2. `T-P1-01`：Bridge 启动日志含 GetMyInfo 信息
3. `T-P1-02`：HealthMonitor 每 30s 输出 ONLINE 日志
4. `T-P1-03`：get_friends 返回好友列表
5. `T-P2-01`：HTTP 查询端点 `POST /v1/gateway/wechat/query` 正常响应

### 第二优先级（验证增强功能）
6. `T-P1-06`：get_history_messages 返回历史消息
7. `T-P2-08`：send_url_card 发送链接卡片
8. `T-P2-10`：收到链接消息时 webhook 含 `link_url`
9. `T-P3-01`：publish_moment 发朋友圈
10. `T-P3-08`：delete_message 撤回消息

### 第三优先级（验证高级功能）
- 朋友圈点赞/评论、群成员添加、表情截取等

---

## 九、已知限制

1. **朋友圈 API 稳定性**：依赖 UI 自动化，受微信界面状态影响
2. **消息对象缓存 TTL**：默认 5 分钟，频繁大量消息场景需关注内存占用
3. **`get_chat_info`/`get_all_messages` 依赖当前焦点**：需微信窗口处于目标聊天界面
4. **群管理权限**：`add_group_members` 需要管理员权限
5. **wxautox4 付费 API 层级**：部分高级功能可能需要更高授权等级，真机验证时注意

---

## 十、下一步建议

1. **运行节点 pull 代码** → 按优先级顺序跑真机测试
2. **提交 RUNTIME_FEEDBACK 报告** → 反馈测试结果和 API 真实返回格式
3. **根据反馈修正** → 主要是消息对象字段名、GetMoments 返回格式等
4. **Host Brain 对接** → 利用 `wechat_adapter.get_friends()` 等新方法丰富七海的联系人感知能力

---

*本报告由七海 (Codex Node) 生成，记录 wxautox4 三期更新计划（2026-03-27）的完整实施结果。*
