受众：CODEX
标题：开发实测报告 — wxautox4 Bridge 集成

# 开发实测报告：wxautox4 Bridge 集成

| 字段 | 值 |
|------|-----|
| **功能名称** | wxautox4 Bridge — 微信自动化 bridge 后端替换 |
| **开发节点** | v_wenloliu workstation |
| **报告日期** | 2026-03-27 |
| **报告版本** | v1 |
| **关联 Commit** | fae0c34 |
| **关联分支** | codex |
| **状态** | 🔵 OPEN（待真机验证） |

---

## 1. 变更摘要

新增 wxautox4 作为微信自动化 bridge 的可选后端，与现有自研 UIA Bridge（`tools/wechat_uia_bridge.py`）完全兼容的外部协议（JSONL 命令队列 + webhook POST）。通过 `.env` 中 `EDGE_WECHAT_BRIDGE_BACKEND=wxautox4` 一行配置即可切换，无需修改任何上层代码。

wxautox4 是 Cluic 开发的 Python 微信自动化库（基于 Windows UIAutomation），需付费激活，官方适配微信 4.0.5+。

---

## 2. 修改文件清单

| # | 文件路径 | 改动类型 | 改动说明 |
|---|---------|---------|---------|
| 1 | `tools/wxautox4_bridge.py` | **新增** | wxautox4 封装的 bridge 进程主文件（~400行），实现 InboundProcessor（消息轮询 → webhook POST）+ OutboundProcessor（JSONL 命令消费 → wxautox4 API 执行） |
| 2 | `edge_client/gateway/bootstrap.py` | 修改 | 新增 `_wechat_bridge_backend()` 辅助函数；`_wechat_uia_bridge_enabled()` 扩展支持 `wxautox4` backend；`_start_uia_bridge_process()` 根据 backend 选择启动 `wxautox4_bridge.py` 或 `wechat_uia_bridge.py`；watchdog 日志更新 |
| 3 | `edge_client/gateway/adapters/wechat_adapter.py` | 修改 | `_bridge_queue_enabled()` 从 `backend == "uia"` 改为 `backend in {"uia", "wxautox4"}`，让命令队列在 wxautox4 模式下也能工作 |
| 4 | `.env.example` | 修改 | `EDGE_WECHAT_BRIDGE_BACKEND` 注释更新，新增 wxautox4 专用配置项（`EDGE_WECHAT_WXAUTOX4_LISTEN_TARGETS`、`EDGE_WECHAT_INBOUND_POLL_INTERVAL_S`） |
| 5 | `requirements.txt` | 修改 | 新增 `wxautox4` 可选依赖注释说明 |

---

## 3. 本机已验证项

以下项目已在开发节点通过验证：

| # | 验证项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | Lint (IDE diagnostics) | ✅ PASS | 所有修改文件 0 error / 0 warning |
| 2 | 类型检查 (pyright) | ✅ PASS | IDE 无类型错误 |
| 3 | 单元测试 | ⏭️ SKIP | wxautox4 需真机微信窗口，开发机无法运行 |
| 4 | 导入检查 | ⏭️ SKIP | wxautox4 包需激活后才能 import，开发机未安装 |
| 5 | 协议兼容性检查 | ✅ PASS | 对比 `wechat_uia_bridge.py` 的 webhook payload 字段：`id`、`msg_id`、`sender`、`sender_name`、`from_wxid`、`receiver`、`to_wxid`、`conversation_id`、`content`、`text`、`type`、`msg_type`、`ts`、`bridge_backend`、`is_group_hint`、`media_url` — 完全一致 |
| 6 | 命令结果格式检查 | ✅ PASS | 对比命令结果字段：`command_id`、`request_id`、`action`、`target`、`content`、`ts_ms`、`success`、`status`、`reason_code`、`detail`、`resolved_target` — 完全一致 |
| 7 | CLI 参数兼容性 | ✅ PASS | `--edge-url`、`--command-queue-file`、`--command-result-file`、`--command-poll-interval` 与 UIA Bridge 参数名一致，新增 `--inbound-poll-interval`、`--dedup-window`、`--listen-targets` |

---

## 4. 真机实测清单

> 以下测试项需要在**运行节点**执行。每项包含可执行的步骤和可断言的预期结果。

### T-01: wxautox4 环境安装与激活 — P0

**前置条件**：
- 微信 4.0.5 已安装并登录
- Python 3.8+ 环境（Natsume_Ai 虚拟环境）

**测试步骤**：
1. `pip install wxautox4`
2. `wxautox4 --version`（确认安装成功）
3. `wxautox4 -a <激活码>`（购买后激活）
4. `python -c "from wxautox4 import WeChat; wx = WeChat(); print('OK')"`

**预期结果**：
- 步骤 2 输出版本号
- 步骤 3 输出激活成功
- 步骤 4 输出 `OK`，无报错

---

### T-02: wxautox4 Bridge 独立启动 — P0

**前置条件**：
- T-01 通过
- 微信 4.0.5 已登录在桌面

**测试步骤**：
1. `cd D:\Natsume_Ai_repo`
2. `python tools/wxautox4_bridge.py --edge-url http://127.0.0.1:19000`
3. 观察控制台日志

**预期结果**：
- 输出 `wxautox4 Bridge for Natsume_Ai`
- 输出 `wxautox4 bound to WeChat window successfully!`
- 输出 `InboundProcessor started`
- 输出 `OutboundProcessor started`
- 输出 `wxautox4 Bridge is running. Press Ctrl+C to stop.`
- 无异常报错

**验证命令/方法**：
```bash
python tools/wxautox4_bridge.py --edge-url http://127.0.0.1:19000
```

---

### T-03: Inbound 消息转发 — P0

**前置条件**：
- T-02 通过
- Gateway 已启动（`EDGE_WECHAT_BRIDGE_BACKEND=wxautox4`）

**测试步骤**：
1. 修改 `.env`：`EDGE_WECHAT_BRIDGE_ENABLED=true` + `EDGE_WECHAT_BRIDGE_BACKEND=wxautox4`
2. 启动 Gateway
3. 用另一个微信号给当前登录号发一条文本消息
4. 观察 wxautox4 Bridge 的日志和 Gateway 的日志

**预期结果**：
- wxautox4 Bridge 日志输出 `forwarded inbound: sender=XXX content=...`
- Gateway 日志显示收到 webhook POST 并解析成功
- Host Brain 收到入站事件

---

### T-04: Outbound 命令执行（send_text） — P0

**前置条件**：
- T-03 通过

**测试步骤**：
1. 通过 Host Brain 向某个微信好友发送一条回复消息
2. 观察 `runtime/edge/wechat_command_queue.jsonl` 出现新行
3. 观察 wxautox4 Bridge 日志
4. 观察 `runtime/edge/wechat_command_result.jsonl` 出现结果

**预期结果**：
- 命令队列出现 `send_text` 命令
- wxautox4 Bridge 日志输出 `command XXX: send_text to 'XXX' OK`
- 命令结果文件出现 `"success":true,"status":"ok"`
- 微信窗口中对方收到消息

---

### T-05: 热切换回 UIA Bridge — P1

**前置条件**：
- T-04 通过

**测试步骤**：
1. 停止 Gateway
2. 修改 `.env`：`EDGE_WECHAT_BRIDGE_BACKEND=uia`
3. 重启 Gateway
4. 确认自研 UIA Bridge 子进程启动
5. 测试 inbound + outbound

**预期结果**：
- Gateway 日志显示 `UIA Bridge 子进程已启动`（而非 wxautox4 Bridge）
- inbound 和 outbound 功能正常

---

### T-06: Outbound 命令执行（send_image） — P1

**前置条件**：
- T-03 通过
- 有一张测试图片文件

**测试步骤**：
1. 通过命令队列发送一条 `send_image` 命令（content 或 media_path 为图片路径）
2. 观察 wxautox4 Bridge 日志

**预期结果**：
- 日志输出 `command XXX: send_image to 'XXX' OK`
- 对方微信收到图片

---

### T-07: 消息去重验证 — P2

**前置条件**：
- T-03 通过

**测试步骤**：
1. 快速连续发送 3 条相同内容的消息
2. 观察 wxautox4 Bridge 的转发日志

**预期结果**：
- 第一条消息正常转发
- 后续相同内容消息在 dedup 窗口内被过滤（日志中 deduped 计数增加）

---

### T-08: 群聊消息识别 — P2

**前置条件**：
- T-03 通过
- 有一个包含多人的微信群

**测试步骤**：
1. 在微信群中让其他成员发一条消息
2. 观察 webhook payload 中的 `is_group_hint` 和 `conversation_id`

**预期结果**：
- `is_group_hint` 为 `true`
- `conversation_id` 为群聊名称（而非个人发送者）

---

## 5. 实测统计

| 优先级 | 总数 | 说明 |
|--------|------|------|
| P0（阻塞） | 4 | T-01 ~ T-04，必须全部通过 |
| P1（重要） | 2 | T-05 ~ T-06，应通过 ≥ 80% |
| P2（增强） | 2 | T-07 ~ T-08，尽量通过 |

---

## 6. 已知限制与风险

| # | 描述 | 影响范围 | 缓解措施 |
|---|------|---------|---------|
| 1 | wxautox4 需购买激活码 | 成本 | 先购买最短期限试用 |
| 2 | wxautox4 `GetNewMessage()` 行为未完全验证 | inbound | T-03 真机验证，不行就换 `AddListenChat` + 回调 |
| 3 | wxautox4 版本兼容性（4.0.5 vs 4.1） | 环境 | 优先试 4.0.5，不行再试 4.1 |
| 4 | `voice_call`/`video_call` 不支持 | outbound | 返回明确错误码，不影响主流程 |
| 5 | 群聊消息的 roomid 解析取决于 wxautox4 消息对象结构 | inbound | T-08 真机验证确认 |

---

## 7. 回滚方案

如果真机测试发现严重问题：

```bash
# 切回自研 UIA Bridge（无需回退代码）
# 修改 .env：
EDGE_WECHAT_BRIDGE_BACKEND=uia
# 重启 Gateway 即可
```

**受影响文件回滚优先级**：
1. `.env` — 一行改回 `uia` 即刻回退
2. `edge_client/gateway/adapters/wechat_adapter.py` — `_bridge_queue_enabled` 改动向后兼容，无需回退
3. `edge_client/gateway/bootstrap.py` — 改动向后兼容，无需回退
4. `tools/wxautox4_bridge.py` — 新增文件，不影响现有功能，无需删除

---

## 8. 数据流图

```
┌──────────────────── Gateway 进程（不变） ──────────────────────┐
│                                                                │
│  WeChatAdapter ─── wechat_command_queue.py ──→ JSONL 文件 ──┐  │
│       ↑                                                      │  │
│       │ webhook POST                                         │  │
│       │ /v1/gateway/wechat/webhook                           │  │
│                                                              │  │
└──────────────────────────────────────────────────────────────┘  │
                        ↑                                         │
                        │ HTTP POST                               ↓
┌──────────────── wxautox4_bridge.py 子进程 ───────────────────────┐
│                                                                  │
│  InboundProcessor:                                               │
│    wxautox4.GetNewMessage() → 去重 → webhook POST               │
│                                                                  │
│  OutboundProcessor:                                              │
│    poll JSONL queue → wxautox4.SendMsg/SendFiles → write result  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
          ↕ UIAutomation (wxautox4 封装)
┌─────────────────┐
│   微信 4.0.5    │
└─────────────────┘
```

---

<!-- 运行节点请在下方填写结果，或在 runtime_feedback_reports/ 中新建反馈报告 -->
