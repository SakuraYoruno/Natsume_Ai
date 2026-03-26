受众：CODEX
标题：QQ 适配器增强版运行反馈报告

# 运行反馈报告：QQ 适配器增强版

| 字段 | 值 |
|------|-----|
| **功能名称** | QQ 适配器增强版 |
| **对应开发报告** | `DEV_TEST_REPORT_qq_adapter_enhancement_20260326.md` |
| **运行节点** | `edge-lan` |
| **报告日期** | 2026-03-26 |
| **测试人** | Codex（RUN 节点） |
| **总体结论** | 🟡 部分通过 |

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| **操作系统** | Windows 11 专业版 10.0.26100 |
| **Python** | 3.11.6（`.venv`） |
| **GPU** | GameViewer Virtual Display Adapter |
| **微信版本** | N/A |
| **VTS 版本** | N/A |
| **Git Commit** | `7e6651e7` |
| **NapCat** | v4.17.53 |
| **QQ / OneBot** | QQNT 已登录；OneBot HTTP API `127.0.0.1:3000` 可用 |

### 环境差异说明

- 当前 QQ 账号无群聊（`get_group_list` 返回 0），因此群聊收发用例无法在本轮完成。
- 当前环境可确认 OneBot、Gateway、Edge 主服务均已启动，但 Host Brain 未参与自动生成回复，本轮 outbound 用例通过 `POST /v1/gateway/reply` 验证发送链路。
- 在 2026-03-26 18:18（Asia/Shanghai）收到一次真实外部私聊入站；Gateway 已解析消息，但上报 Host Brain 时超时并写入缓存，同时 `http://127.0.0.1:18080/health` 无法连接。

---

## 2. 测试执行记录

### T-01: QQ 私聊消息接收 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 已收到一次来自外部好友账号的真实私聊入站；Gateway `inbound_parsed` 从 0 增至 1，说明 QQ → OneBot → Gateway 解析链路正常。但 `EdgeInputReporter` 在 `source=GATEWAY` 上报 Host Brain 时超时并写入缓存，Host Brain 收到消息未确认。 |

**证据**（日志/接口）：
```json
{
  "onebot_status": "ok",
  "onebot_good": true,
  "gateway_outbound_ready": true,
  "qq_stats": {
    "inbound_parsed": 1,
    "inbound_skipped": 0,
    "inbound_notice": 6,
    "inbound_request": 0
  },
  "host_health": "unreachable"
}
```

```text
2026-03-26 18:18:01 WARNING EdgeInputReporter source=GATEWAY timed out and cached the message
2026-03-26 18:19:10 WARNING EdgeInputReporter retrying cached GATEWAY message
```

**偏差说明**：
- 已确认外部私聊能到达 QQ adapter 并被 Gateway 解析。
- 失败点位于 Gateway → Host Brain 上报阶段；当前 `127.0.0.1:18080` 不可达，因此“Host Brain 收到消息”这一验收条件未满足。

---

### T-02: QQ 群聊消息接收 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 当前登录 QQ 账号无群聊，无法构造 `@Bot` 入站消息。 |

**偏差说明**：
- 环境阻塞，不是代码阻塞。
- 需先让 Bot 加入至少一个测试群，再复测 `qq_group_{group_id}` 与 `_at_self=True`。

---

### T-03: QQ 私聊文本回复 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | 通过 `POST /v1/gateway/reply` 向 Bot 自身会话发送文本消息，Gateway 返回 `sent`，QQ adapter `outbound_sent` 递增。 |

**证据**（日志/接口）：
```json
{
  "status": "sent",
  "platform": "qq"
}
```

---

### T-04: QQ 群聊文本回复 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 因当前账号无群聊，无法执行 `qq_group_*` 的实际出站回发。 |

**偏差说明**：
- 环境阻塞，不是代码阻塞。

---

### T-05: QQ 图片发送（URL） — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | 使用 `https://httpbin.org/image/png` 作为 `IMAGE` 内容，通过 `POST /v1/gateway/reply` 发往 Bot 自身会话，Gateway 返回 `sent`，`outbound_sent` 递增。 |

**证据**（接口）：
```json
{
  "status": "sent",
  "platform": "qq"
}
```

---

### T-06: QQ 语音发送 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | 在远端临时生成 1 秒静音 wav，通过 `POST /v1/gateway/reply` 发往 Bot 自身会话，Gateway 返回 `sent`。 |

**证据**（接口）：
```json
{
  "status": "sent",
  "platform": "qq"
}
```

---

### T-07: Health 端点检查 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | `GET /v1/gateway/health` 返回 QQ adapter `status="degraded"`，但 OneBot `/get_status` 同时返回 `status="ok"` 且 `data.good=true`。 |

**证据**（接口）：
```json
{
  "adapter_status": "degraded",
  "onebot_status": "ok",
  "onebot_good": true
}
```

**偏差说明**：
- 现象表明 QQ adapter 健康判断读取了错误字段路径。
- 真机现象与开发报告的预期 `ok` 不一致，需开发节点修复后重测。

---

### T-08: 单元测试通过 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | 运行 `D:\Natsume_Ai\.venv\Scripts\python.exe -m pytest tests/contract/test_qq_adapter.py -v`，25 项全部通过。 |

**证据**：
```text
============================= test session starts =============================
collecting ... collected 25 items
...
============================= 25 passed in 0.85s ==============================
```

---

### T-09: 默认平台验证 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | 在移除 `EDGE_GATEWAY_PLATFORMS` 的配置探针中加载 `edge_client.config.load_edge_config()`，返回 `('qq',)`。 |

**证据**：
```text
('qq',)
```

---

## 3. 测试结果汇总

| 优先级 | 总数 | PASS | FAIL | PARTIAL | 通过率 |
|--------|------|------|------|---------|--------|
| P0（阻塞） | 6 | 3 | 0 | 3 | 50% |
| P1（重要） | 3 | 2 | 1 | 0 | 67% |
| P2（增强） | 0 | 0 | 0 | 0 | N/A |
| **合计** | **9** | **5** | **1** | **3** | **56%** |

### 闭环判定

- [ ] P0 100% PASS
- [ ] P1 ≥ 80% PASS
- [x] P2 已评估

**结论**：🟡 需修复后重测

---

## 4. 新发现问题

| # | 问题描述 | 严重程度 | 复现步骤 | 建议 |
|---|---------|---------|---------|------|
| 1 | QQ adapter 健康检查误报 `degraded`：OneBot `data.good=true`，但 Gateway 健康仍非 `ok` | P1 | 启动 NapCat + Gateway 后访问 `/v1/gateway/health` | 修正 `qq_adapter.py` 中健康状态对 OneBot 返回字段的解析路径 |
| 2 | QQ 图片发送的本地文件路径分支在真机上失败，OneBot 返回 `rich media transfer failed` | P1 | 通过 `POST /v1/gateway/reply` 发送 `msg_type=image` 且仅传 `media_path` | 对 `file:///` 本地图片路径在 NapCat/QQNT 上做兼容性排查；必要时降级为 URL/上传策略 |
| 3 | 当前 Bot 账号无群聊，导致群收发回归用例无法执行 | P2 | `get_group_list` 返回 0 | 准备专用测试群后重测 T-02 / T-04 |
| 4 | Host HTTP 服务在 `127.0.0.1:18080` 不可达，导致真实 QQ 私聊入站在 Gateway 层超时并缓存，无法完成 Host Brain 接收闭环 | P0 | 发送外部 QQ 私聊后观察到 `inbound_parsed=1`，同时 `EdgeInputReporter` 记录 `source=GATEWAY` timeout；访问 `/health` 连接失败 | 先恢复 Host 服务，再复测 T-01，并检查缓存消息是否自动补投 |

---

## 5. 性能观察（可选）

| 指标 | 实测值 | 基线/预期 | 评价 |
|------|--------|---------|------|
| Gateway 健康接口 | 可用，返回 200 | 应返回 200 | ✅ 正常 |
| OneBot `/get_status` | 可用，返回 `ok` | 应返回 `ok` | ✅ 正常 |
| QQ outbound stats | `outbound_sent=3`, `outbound_failed=1` | 至少文本/图片 URL/语音可发送 | ⚠️ 本地文件图片失败 |

---

## 6. 建议与补充

- 先恢复 Host `127.0.0.1:18080` 服务，再观察缓存中的 GATEWAY 消息是否自动补投。
- 优先修复 QQ adapter 的健康状态解析错误，否则 `/v1/gateway/health` 会持续误报。
- 建议为 QQ 回归准备一个固定测试群和一个外部测试好友账号，补齐 T-02 / T-04 的真机闭环。
- 图片发送能力在开发报告中宣称支持 `file/url/base64`，但本轮真机仅验证 URL 成功，本地文件路径失败，建议开发节点补充 NapCat 实机兼容性测试。

---

## 7. 附件清单

| # | 类型 | 文件名/路径 | 说明 |
|---|------|-----------|------|
| 1 | 日志 | `D:\Natsume_Ai\runtime\edge\logs\edge.stderr.log` | Gateway/QQ adapter 运行日志，包含图片发送失败证据 |
| 2 | 接口证据 | `http://127.0.0.1:3000/get_status` | OneBot 状态查询 |
| 3 | 接口证据 | `http://127.0.0.1:19000/v1/gateway/health` | Gateway 健康与 adapter stats |
