# F-24 / F-40 微信实测步骤（本机手动执行）

**前提**：你已在 Edge 端用微信小号登录成功。

**重要**：Hook（如 wcf）必须安装在 **Edge 所在的那台机器** 上并在该机运行，因为 Hook 要附着/连接**本机**的微信进程才能拿到消息；不能从别的机器去“Hook”另一台机上的微信。

---

## 一、Edge 所在机器：启用微信监听并启动 Edge

1. **配置环境变量**（在 Edge 所在机器的 `.env` 或启动前导出）：
   - `EDGE_WECHAT_LISTENER_ENABLED=1`  
     启用微信监听；Edge 启动时会创建 WeChatListener 并等待 Hook 推送。
   - `EDGE_EVENT_REPORT_HOST_URL=http://<Host_IP>:<Host_端口>`  
     例如 `http://192.168.1.100:18080`。用于把收到的微信消息上报到 Host 的 `POST /v1/host/input`。
   - （可选）`EDGE_WECHAT_SELF_WXID=你的小号wxid`，用于过滤自己发出的消息；不设则不过滤。
   - （可选）`EDGE_WECHAT_PRIVATE_ONLY=1` 只处理私聊；`EDGE_WECHAT_TEXT_ONLY=1` 只处理文本（默认 true）。

2. **启动 Edge**（与平时一致）：
   ```powershell
   # 在 Edge 所在机器上
   python -m edge_client
   ```
   若配置正确，日志中会出现：`WeChatListener started (host_url=...); POST to /v1/edge/wechat/callback to push messages`。

---

## 二、Edge 所在机器：安装 Hook 并推送消息到 Edge

Hook **必须在这台机器上**运行：微信小号在这台机登录，Hook 要注入/连接这台机上的微信进程才能拿到消息。

### 推荐：WeChatFerry（wcferry）+ 本仓库桥接脚本

1. **安装 wcferry**（在 Edge 机当前 Python 环境）：
   ```bash
   pip install wcferry
   ```
   若原仓库已停更，可改用社区 fork，例如查阅 [WeChatFerryX](https://github.com/lzb112/WeChatFerryX) 的安装说明。

2. **配置 .env**（同机）：
   - `EDGE_WECHAT_LISTENER_ENABLED=1`（Edge 进程内 WeChatListener 接收 callback）
   - `EDGE_EVENT_REPORT_HOST_URL=http://<Host_IP>:18080`（上报 Host，且 WeChatListener 依赖此项）
   - **`EDGE_WECHAT_BRIDGE_ENABLED=1`**（**启动脚本**用，为 1 时随 Edge 一并启动 wcferry 桥接；缺此项则桥接不会起）

3. **用 Edge 启动脚本统一启动**（推荐）：
   ```powershell
   .\scripts\runtime\start_edge.ps1
   ```
   脚本会先启动 Edge，再在 `EDGE_WECHAT_BRIDGE_ENABLED=1` 时自动启动桥接进程（PID 写入 `runtime/edge/wechat_bridge.pid`）。停止 Edge 时执行 `stop_edge.ps1` 会同时结束桥接。

4. **或手动单独跑桥接**（未设 `EDGE_WECHAT_BRIDGE_ENABLED` 时）：
   ```bash
   python tools/wechat_wcferry_bridge.py
   python tools/wechat_wcferry_bridge.py --edge-url http://127.0.0.1:17800
   ```
   桥接会：用 wcferry 收微信消息 → 转成 JSON → POST 到 `http://127.0.0.1:17800/v1/edge/wechat/callback`。  
   **注意**：wcferry 会注入本机微信进程，首次运行若失败可尝试以管理员权限启动微信或参考 wcferry 文档。

### 若不用桥接脚本：自己写 Hook 推送

若使用其他 Hook 或自写逻辑，只需在「收到新消息」时向 Edge 发：

- **URL**：`http://127.0.0.1:17800/v1/edge/wechat/callback`（端口以你 Edge 为准）
- **Method**：`POST`
- **Body**：JSON，字段兼容 WeChatListener 即可，例如：`id`/`msg_id`、`sender`/`from_wxid`、`receiver`/`to_wxid`、`content`/`text`、`type`（1=文本）、`roomid`（群聊时）等。

`/v1/edge/wechat/callback` 只接受本机（127.0.0.1）调用，无需对公网开放。

---

## 三、实测流程

1. **发一条真实消息到微信小号**  
   用另一台设备或账号给该小号发一条文本，例如「你好」。

2. **确认 Hook 已推送到 Edge**  
   - 若 wcf 有日志，确认已 POST 到 `http://127.0.0.1:17800/v1/edge/wechat/callback` 且返回 200。
   - Edge 日志中可看到消息被接收；若 listener 未启动会返回 `(listener not active)`。

3. **确认 Host 处理并回复**  
   - Host 收到 `POST /v1/host/input`（source=WECHAT）后会走主链并生成回复。
   - 若已配置 WeChat 回复 egress，回复应回到微信小号会话中。

4. **去重**  
   - 同一 `msg_id` 再发一次，应被去重（不会重复触发主链）。

---

## 四、结论归档

- 通过：消息不丢、去重有效、回复在微信中可见 → F-24/F-40 可记 **PASS**。
- 未通过：记录现象（如「Hook 未推送」「Edge 返回 listener not active」「Host 无 request_id」「回复未回微信」等）到 `20_chain_quality/` 或 `40_webui_obs/`，并更新总索引。

---

## 五、若暂不接 Hook：模拟实测

不运行 Hook 时，可直接对 Host 调：

```bash
curl -X POST "http://<Host_IP>:18080/v1/host/input" -H "Content-Type: application/json" \
  -d '{"source":"WECHAT","message":"你好","from_wxid":"wxid_test","to_wxid":"wxid_bot","msg_id":"msg_001","content":"你好"}'
```

用于验证 Host 侧 WeChat 入口与主链；F-24/F-40 结论可标为「模拟通过」或 N/A，真实结论需按上文完成监听 + 真实消息实测。
