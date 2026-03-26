受众：MASTER
标题：QQ 适配器增强版开发实测报告

---

# QQ 适配器增强版 — 开发实测报告

**日期**：2026-03-26  
**节点**：DEV（开发节点）  
**状态**：✅ 代码完成 / 🟡 等待 RUN 节点真机验证  

---

## 1. 变更概述

### 1.1 配置默认值调整

将系统默认启用平台从 `wechat,qq` 改为仅 `qq`，因微信 4.x UIAutomation 树无法完整拉起，微信适配器降级为休眠状态。

| 文件 | 改动行 | 原值 | 新值 |
|------|--------|------|------|
| `.env.example` L456 | `EDGE_GATEWAY_PLATFORMS` | `wechat,qq` | `qq` |
| `edge_client/config.py` L124 | `gateway_platforms` 默认 | `("wechat", "qq")` | `("qq",)` |
| `edge_client/config.py` L410 | 环境变量解析默认值 | `"wechat,qq"` | `"qq"` |
| `edge_client/config.py` L420 | 空集 fallback | `["wechat", "qq"]` | `["qq"]` |
| `edge_client/gateway/bootstrap.py` L25 | `GatewayConfig.enabled_platforms` | `("wechat", "qq")` | `("qq",)` |
| `edge_client/gateway/bootstrap.py` L113 | `_normalize_platforms` 空集 fallback | `"wechat"` | `"qq"` |

### 1.2 QQ 适配器增强（qq_adapter.py）

从 270 行扩展至约 380 行，新增功能：

| 功能 | 描述 | 实现方式 |
|------|------|----------|
| **VOICE 发送** | 支持语音消息发送 | OneBot v11 `record` 消息段 |
| **@ 提醒检测** | 识别群消息中 @自己 和 @全体成员 | `_check_at_self()` 方法，标记到 `raw_extra._at_self` |
| **URL 图片发送** | 通过 HTTP URL 发送图片 | `content` 以 `http://` / `https://` 开头直接使用 |
| **base64 图片发送** | 通过二进制数据发送图片 | `media_data` → `base64://` 编码 |
| **notice 事件处理** | 群撤回、好友撤回、群成员变动、戳一戳等 | `_handle_notice()` 方法，记录日志 |
| **request 事件处理** | 加好友、加群请求 | `_handle_request()` 方法，记录日志 |
| **消息段构造重构** | 统一的消息段构造逻辑 | `_build_message_segments()` + `_resolve_media_uri()` |
| **stats 增强** | 新增 notice/request 计数 | `inbound_notice` / `inbound_request` |

### 1.3 单元测试（新建 test_qq_adapter.py）

创建 `tests/contract/test_qq_adapter.py`，约 300 行，涵盖：

| 测试组 | 编号 | 覆盖 |
|--------|------|------|
| T-QQ-INBOUND | 01~08 | 私聊/群聊解析、自过滤、@检测、图片/语音类型识别、非 message 跳过 |
| T-QQ-NOTICE | 01 | 群消息撤回事件记录 |
| T-QQ-REQUEST | 01 | 加好友请求记录 |
| T-QQ-OUTBOUND | 01~09 | TEXT 私聊/群聊、IMAGE(file/url/base64)、VOICE、不支持类型、未知会话ID、API失败 |
| T-QQ-HEALTH | 01~02 | 正常/异常状态 |
| T-QQ-UTIL | 01~03 | 文本提取过滤 at、fallback、media URI 优先级 |

---

## 2. 文件变更清单

| 文件 | 操作 | 行数变化 |
|------|------|----------|
| `.env.example` | 修改 | 1 行 |
| `edge_client/config.py` | 修改 | 3 行 |
| `edge_client/gateway/bootstrap.py` | 修改 | 2 行 |
| `edge_client/gateway/adapters/qq_adapter.py` | 重写增强 | 270 → ~380 行 |
| `tests/contract/test_qq_adapter.py` | 新建 | ~300 行 |

---

## 3. 静态验证

### 3.1 Lint 检查

| 文件 | 错误 | 警告 |
|------|------|------|
| `.env.example` | 0 | 0 |
| `edge_client/config.py` | 0 | 0 |
| `edge_client/gateway/bootstrap.py` | 0 | 0 |
| `edge_client/gateway/adapters/qq_adapter.py` | 0 | 0 |
| `tests/contract/test_qq_adapter.py` | 0 | 0 |

### 3.2 接口兼容性

- `QQAdapter` 严格实现 `AbstractPlatformAdapter` 的 `platform_id` / `parse_inbound()` / `send_reply()` 三个抽象方法
- `health()` 可选方法保持不变
- 新增方法均为内部方法（`_` 前缀），不影响外部接口

### 3.3 Host Brain 侧影响

**无需改动**。Host Brain 通过 `GATEWAY` source 统一处理所有平台消息，完全平台无关。

---

## 4. 真机测试清单（RUN 节点执行）

| # | 测试项 | 优先级 | 预期结果 | 状态 |
|---|--------|--------|----------|------|
| T-01 | QQ 私聊消息接收 | P0 | 发送私聊文本给 Bot → Host Brain 收到 `qq_dm_{user_id}` 消息 | 🟡 待验证 |
| T-02 | QQ 群聊消息接收 | P0 | 在群中 @Bot 发消息 → Host Brain 收到 `qq_group_{group_id}` 消息，`_at_self=True` | 🟡 待验证 |
| T-03 | QQ 私聊文本回复 | P0 | Host Brain 回复 → Bot 通过 OneBot API 发送私聊文本 | 🟡 待验证 |
| T-04 | QQ 群聊文本回复 | P0 | Host Brain 回复 → Bot 通过 OneBot API 发送群聊文本 | 🟡 待验证 |
| T-05 | QQ 图片发送（URL） | P1 | 发送包含 URL 的 IMAGE 回复 → 对方收到图片 | 🟡 待验证 |
| T-06 | QQ 语音发送 | P1 | 发送 VOICE 回复 → 对方收到语音 | 🟡 待验证 |
| T-07 | Health 端点检查 | P1 | GET `/v1/gateway/health` → QQ 适配器状态 `ok` | 🟡 待验证 |
| T-08 | 单元测试通过 | P0 | `python -m pytest tests/contract/test_qq_adapter.py -v` 全部通过 | 🟡 待验证 |
| T-09 | 默认平台验证 | P0 | 不设 `EDGE_GATEWAY_PLATFORMS` 环境变量 → 只启用 QQ 适配器 | 🟡 待验证 |

---

## 5. 已知限制

1. **VIDEO 发送**：暂未实现（`_SENDABLE_MSG_TYPES` 不含 VIDEO），需 OneBot 实现端支持
2. **notice/request 自动处理**：当前仅记录日志，自动同意加好友等策略需上层实现
3. **图片 base64 大小**：base64 编码后体积增大约 33%，大图建议使用 URL 或本地文件
4. **群聊 @回复**：当前回复不自动带 @ 对方，需上层在 content 中插入 `[CQ:at,qq=xxx]` 或在 raw_extra 中指定

---

## 6. 下一步

- [ ] RUN 节点执行 T-01 ~ T-09 真机测试
- [ ] 根据反馈修复问题
- [ ] 考虑实现群聊 @回复（outbound 时自动带 at 消息段）
