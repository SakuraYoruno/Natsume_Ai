受众：CODEX
标题：LINE Messaging API 适配器开发实测报告

# DEV_TEST_REPORT — LINE Messaging API 适配器

| 字段           | 值                                   |
| -------------- | ------------------------------------ |
| **报告日期**   | 2026-03-26                           |
| **任务描述**   | 实现 LINE Messaging API 平台适配器   |
| **开发节点**   | DEV (v_wenloliu workstation)         |
| **分支**       | codex                                |
| **状态**       | ✅ 开发完成，待真机验证               |

---

## 1. 变更摘要

### 新增文件

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `edge_client/gateway/adapters/line_adapter.py` | ~310 | LINE Messaging API 适配器核心实现 |

### 修改文件

| 文件路径 | 改动要点 |
|----------|----------|
| `edge_client/gateway/models.py` | `Platform` 枚举新增 `LINE = "line"` |
| `edge_client/config.py` | EdgeConfig 新增 `line_channel_access_token`、`line_channel_secret` 字段；`EDGE_FIELD_TO_ENV_KEY` 映射；`as_dict()`、`_normalize_platforms()`、`load_edge_config()` 相应更新 |
| `edge_client/gateway/bootstrap.py` | GatewayConfig 新增 LINE 字段；`_normalize_platforms()` 合法值集合新增 `"line"`；`start_gateway()` 新增 LINE 适配器注册块 |
| `edge_client/runtime/bootstrap.py` | GatewayConfig 构造传入 `line_channel_access_token`、`line_channel_secret` |
| `.env.example` | 新增 LINE 配置模板区段（含中文注释） |

---

## 2. 架构设计

### 2.1 适配器定位

LINE 适配器采用与飞书适配器相同的 **Webhook + REST API** 模式：

```
LINE App → LINE Platform → Webhook POST → Gateway Server → LineAdapter.parse_inbound()
                                                              ↓
                                                        UnifiedMessage → Normalizer → Router → Host Brain
                                                              ↓
Host Brain → UnifiedReply → OutboundDispatcher → LineAdapter.send_reply()
                                                              ↓
                                                   LINE Reply API / Push API → LINE Platform → LINE App
```

### 2.2 核心能力

| 能力 | 实现状态 | 说明 |
|------|---------|------|
| Webhook 签名验证 | ✅ | HMAC-SHA256 + Base64，使用 Channel Secret |
| 消息事件解析 | ✅ | text / image / video / audio / file / sticker / location |
| 私聊/群聊识别 | ✅ | source.type = user / group / room |
| Reply API 发送 | ✅ | 使用 replyToken 回复（限时有效） |
| Push API 发送 | ✅ | 主动推送消息（无 replyToken 时降级使用） |
| 长文本拆分 | ✅ | LINE 单条限 5000 字符，自动拆分最多 5 条 |
| 图片消息发送 | ✅ | 需提供 HTTPS 可访问的图片 URL |
| Health 检查 | ✅ | 调用 LINE Bot Info API 验证 token |
| 统计计数 | ✅ | inbound_parsed / inbound_skipped / outbound_sent / outbound_failed |

### 2.3 消息类型映射

| LINE 消息类型 | → MsgType | 内容提取 |
|--------------|-----------|----------|
| text | TEXT | message.text |
| image | IMAGE | "[图片]" |
| video | VIDEO | "[视频]" |
| audio | VOICE | "[语音]" |
| file | FILE | "[文件:filename]" |
| sticker | EMOJI | "[贴图:packageId/stickerId]" |
| location | TEXT | "[位置:title address (lat,lng)]" |

---

## 3. 环境变量配置

| 环境变量 | 必填 | 说明 |
|----------|------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | ✅ | LINE Channel Access Token（长期有效） |
| `LINE_CHANNEL_SECRET` | ✅ | LINE Channel Secret（Webhook 签名验证） |
| `EDGE_GATEWAY_PLATFORMS` | — | 需包含 `line`（如 `line,qq`） |

### Webhook URL 配置

在 LINE Developers Console 中设置 Webhook URL 为：
```
https://<your-domain>/v1/gateway/line/webhook
```

> ⚠️ LINE 要求 Webhook 地址必须为 HTTPS。本地开发可使用 ngrok 暴露。

---

## 4. 静态验证结果

| 检查项 | 结果 |
|--------|------|
| Lint 错误 | ✅ 0 errors |
| 类型标注完整性 | ✅ 全部方法含类型标注 |
| AbstractPlatformAdapter 接口实现 | ✅ platform_id / parse_inbound / send_reply / health |
| 与现有适配器模式一致性 | ✅ 遵循飞书适配器的 Webhook + REST 架构模式 |
| EdgeConfig 字段 → ENV 映射 → load → 构造链路完整 | ✅ |
| GatewayConfig 字段 → start_gateway 注册链路完整 | ✅ |
| runtime/bootstrap 传参链路完整 | ✅ |
| .env.example 文档完整 | ✅ 含中文注释 |

---

## 5. 真机验证清单（RUN 节点）

以下项需在真机环境验证：

- [ ] LINE Developers Console 创建 Messaging API Channel
- [ ] 获取 Channel Access Token 和 Channel Secret
- [ ] 配置 Webhook URL（ngrok 或公网 HTTPS）
- [ ] `.env` 填入 LINE 凭据，`EDGE_GATEWAY_PLATFORMS` 包含 `line`
- [ ] 启动 Gateway，确认日志输出 `LINE Messaging API 适配器已注册`
- [ ] LINE App 发送文本消息 → 确认 Gateway 收到 webhook 并解析为 UnifiedMessage
- [ ] 确认 Host Brain 收到并处理消息
- [ ] 确认回复通过 Reply API 发回 LINE App
- [ ] 测试图片/贴图/位置消息的解析
- [ ] Health API 返回 `status: ok`
- [ ] 签名验证：篡改 body 后确认请求被拒绝

---

## 6. 已知限制 & 后续迭代

| 项目 | 说明 |
|------|------|
| Flex Message | 暂未支持 LINE 的 Flex Message 富文本格式，后续可扩展 |
| 图片上传 | 当前图片发送依赖 HTTPS URL，不支持本地文件上传到 LINE CDN |
| 多媒体下载 | 收到图片/语音/视频时仅记录描述文本，未下载原始媒体文件 |
| Webhook 重试 | LINE 的 webhook 超时重发机制需在 Gateway server 层做幂等处理 |
| Rich Menu | LINE Rich Menu 功能未接入 |

---

## 7. 文件清单

```
新增:
  edge_client/gateway/adapters/line_adapter.py

修改:
  edge_client/gateway/models.py
  edge_client/config.py
  edge_client/gateway/bootstrap.py
  edge_client/runtime/bootstrap.py
  .env.example
  docs/45_testing/dev_test_reports/DEV_TEST_REPORT_line_adapter_20260326.md (本报告)
```
