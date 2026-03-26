受众：CODEX
标题：Telegram Userbot 适配器开发实测报告

# 开发实测报告：Telegram Userbot 适配器

| 字段 | 值 |
|------|-----|
| **功能名称** | Telegram Userbot 适配器（Telethon MTProto） |
| **开发节点** | v_wenloliu workstation (DEV) |
| **报告日期** | 2026-03-26 |
| **报告版本** | v1 |
| **关联 Commit** | （待提交后补充） |
| **关联分支** | codex |
| **状态** | 🔵 OPEN（待真机验证） |

---

## 1. 变更摘要

微信 4.x UIAutomation 方案经 P0/P1 多轮验证均无法恢复聊天 UIA 树（三种策略 S1/S2/S3 均以 `child_count=1, descendant_count=0` 告终），决定引入 **Telegram Userbot** 作为替代通讯通道。

使用 Telethon 库以 **普通用户身份**（MTProto 协议）登录 Telegram，对方看到的是正常的联系人（非 Bot），具备"人感"。适配器完全遵循 Unified Message Gateway 的 `AbstractPlatformAdapter` 基类规范，无缝集成到现有的 normalizer → router → outbound 管线中。

人感增强特性包括：模拟打字延迟（"正在输入..." 提示）、可配置随机延迟抖动（±20%）、长文分段发送（≤5 段，每段 ≤300 字符）、在线状态管理。

---

## 2. 修改文件清单

| # | 文件路径 | 改动类型 | 改动说明 |
|---|---------|---------|---------|
| 1 | `edge_client/gateway/models.py` | 修改 | `Platform` 枚举新增 `TELEGRAM = "telegram"` |
| 2 | `edge_client/gateway/adapters/telegram_adapter.py` | 新增 | Telegram Userbot 完整适配器（~480 行），含 Inbound/Outbound/人感增强/健康检查 |
| 3 | `edge_client/gateway/bootstrap.py` | 修改 | `GatewayConfig` 新增 11 个 Telegram 字段 + `_normalize_platforms()` 支持 `"telegram"` + `start_gateway()` 新增 Telegram 注册块 |
| 4 | `edge_client/config.py` | 修改 | `EdgeConfig` 新增 12 个 Telegram 字段 + `EDGE_FIELD_TO_ENV_KEY` 映射 + `load_edge_config()` 读取逻辑 + `as_dict()` 序列化 + 平台验证列表添加 `"telegram"` |
| 5 | `.env.example` | 修改 | 新增 Telegram 配置段（15 个环境变量，含详细中文注释） |
| 6 | `docs/50_project/arch/UNIFIED_MESSAGE_GATEWAY_DESIGN.md` | 修改 | 新增 §5.6 Telegram 适配器说明 + Phase 6 实施路线 + 架构全景图/目录结构/枚举定义更新 |
| 7 | `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` | 修改 | 适配器列表/模块描述/API 端点文档添加 Telegram |

---

## 3. 本机已验证项

以下项目已在开发节点通过验证：

| # | 验证项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | Lint (ruff/pyright) | ✅ PASS | `bootstrap.py` 和 `config.py` 零 lint error |
| 2 | 类型检查 | ✅ PASS | 所有类型标注正确，`int | None` 等联合类型语法一致 |
| 3 | 单元测试 | ⏭️ SKIP | Telegram 适配器依赖 Telethon 库和真实 API，需真机环境 |
| 4 | 导入检查 | ⏭️ SKIP | Telethon 未安装于 DEV 节点，需 RUN 节点执行 `pip install telethon` |
| 5 | 代码风格一致性 | ✅ PASS | 适配器代码遵循现有 qq/feishu/wecom 适配器的结构模式 |
| 6 | 配置兼容性 | ✅ PASS | 新增字段全部有默认值，不影响已有平台（wechat/qq/feishu/wecom） |
| 7 | 文档一致性 | ✅ PASS | 架构文档已同步更新，版本号/日期已递增 |

---

## 4. 真机实测清单

> 以下测试项需要在**运行节点**执行。每项包含可执行的步骤和可断言的预期结果。

### T-01: Telethon 依赖安装 — P0

**前置条件**：
- Python 3.10+ 虚拟环境已激活

**测试步骤**：
1. `pip install telethon`
2. `python -c "from telethon import TelegramClient; print('OK')"`

**预期结果**：
- 安装成功，导入无错误，输出 `OK`

---

### T-02: TelegramAdapter 模块导入 — P0

**前置条件**：
- T-01 通过
- 当前工作目录为仓库根目录

**测试步骤**：
1. `python -c "from edge_client.gateway.adapters.telegram_adapter import TelegramAdapter; print('import OK')"`

**预期结果**：
- 无 ImportError，输出 `import OK`

---

### T-03: EdgeConfig 加载 Telegram 字段 — P0

**前置条件**：
- 在 `.env` 中设置 `TELEGRAM_API_ID=12345` 和 `TELEGRAM_API_HASH=abc123`

**测试步骤**：
```bash
python -c "
from edge_client.config import load_edge_config
import os
os.environ['TELEGRAM_API_ID'] = '12345'
os.environ['TELEGRAM_API_HASH'] = 'abc123'
os.environ['EDGE_GATEWAY_PLATFORMS'] = 'telegram'
result = load_edge_config()
if result.success and result.config:
    c = result.config
    assert c.telegram_api_id == 12345
    assert c.telegram_api_hash == 'abc123'
    assert 'telegram' in c.gateway_platforms
    print('config load OK')
else:
    print('FAIL:', result.issues)
"
```

**预期结果**：
- 输出 `config load OK`

---

### T-04: Gateway bootstrap 注册 Telegram 适配器 — P0

**前置条件**：
- T-01 ~ T-03 通过
- `.env` 中配置了 `TELEGRAM_API_ID`、`TELEGRAM_API_HASH`、`EDGE_GATEWAY_PLATFORMS=telegram`

**测试步骤**：
```bash
python -c "
from edge_client.gateway.bootstrap import GatewayConfig, _normalize_platforms
platforms = _normalize_platforms(('telegram', 'wechat'))
assert 'telegram' in platforms
assert 'wechat' in platforms
print('normalize OK, platforms:', platforms)
"
```

**预期结果**：
- 输出包含 `telegram` 和 `wechat`

---

### T-05: Telegram Userbot 首次登录 — P0

**前置条件**：
- 从 https://my.telegram.org 获取 API ID 和 API Hash
- 准备好 Telegram 手机号（可接收验证码）
- 在 `.env` 中配置 `TELEGRAM_API_ID`、`TELEGRAM_API_HASH`、`TELEGRAM_PHONE`
- 网络可访问 Telegram（直连或配置代理 `TELEGRAM_PROXY_*`）

**测试步骤**：
```python
# 单独运行 Telethon 登录测试
from telethon import TelegramClient

api_id = <你的 API ID>
api_hash = "<你的 API Hash>"
phone = "<你的手机号>"

client = TelegramClient("nanami", api_id, api_hash)
client.start(phone=phone)  # 首次需输入验证码
me = client.get_me()
print(f"登录成功: {me.first_name} (ID: {me.id})")
client.disconnect()
```

**预期结果**：
- 终端提示输入验证码
- 输入后成功登录，输出用户名和 ID
- 当前目录生成 `nanami.session` 文件（后续启动免验证）

---

### T-06: Telegram 消息收发集成测试 — P1

**前置条件**：
- T-05 通过（session 文件已存在）
- `.env` 配置完整
- `EDGE_GATEWAY_PLATFORMS=telegram`

**测试步骤**：
1. 启动 Edge Client（含 Gateway）
2. 使用另一个 Telegram 账号向 Userbot 发送消息 "你好七海"
3. 观察 Gateway 日志，确认消息被接收并路由

**预期结果**：
- Gateway 日志显示 `[Telegram] 收到消息` 相关日志
- 消息成功转换为 `UnifiedMessage`（`platform=telegram`）
- `conversation_id` 格式为 `tg_dm_<user_id>`

---

### T-07: 人感增强 — 打字延迟 + 分段发送 — P1

**前置条件**：
- T-06 通过

**测试步骤**：
1. 通过 Gateway `/v1/gateway/reply` 端点发送回复：
```json
{
  "platform": "telegram",
  "conversation_id": "tg_dm_<对方用户ID>",
  "content": "这是第一段话。\n\n这是第二段话，比较长一些。\n\n第三段话用来测试分段发送效果。"
}
```
2. 在对方 Telegram 客户端观察

**预期结果**：
- 对方看到 "正在输入..." 提示
- 消息被分成多段发送（按 `\n\n` 切分）
- 每段之间有可感知的间隔（1-3 秒）
- 没有 bot 标签

---

### T-08: 代理配置测试（国内网络） — P1

**前置条件**：
- 国内网络环境（直连 Telegram 不通）
- 可用的 socks5/http 代理

**测试步骤**：
1. 在 `.env` 中配置代理：
```
TELEGRAM_PROXY_TYPE=socks5
TELEGRAM_PROXY_ADDR=127.0.0.1
TELEGRAM_PROXY_PORT=1080
```
2. 启动 Telegram 适配器
3. 验证连接成功

**预期结果**：
- 通过代理成功连接 Telegram
- 健康检查返回 `connected: true`

---

### T-09: 健康检查端点 — P2

**前置条件**：
- Gateway 已启动且 Telegram 适配器已注册

**测试步骤**：
```bash
curl http://localhost:19000/v1/gateway/health
```

**预期结果**：
- 返回 JSON 中包含 `telegram` 平台的健康状态
- 包含 `connected`、`user_id`、`username`、统计信息等字段

---

### T-10: 群聊消息接收 — P2

**前置条件**：
- Userbot 已加入某个 Telegram 群
- T-06 通过

**测试步骤**：
1. 在群里其他成员发一条消息
2. 观察 Gateway 日志

**预期结果**：
- 消息被接收，`is_group=True`
- `conversation_id` 格式为 `tg_group_<chat_id>`

---

## 5. 实测统计

| 优先级 | 总数 | 说明 |
|--------|------|------|
| P0（阻塞） | 5 | 必须全部通过（T-01 ~ T-05） |
| P1（重要） | 3 | 应通过 ≥ 80%（T-06 ~ T-08） |
| P2（增强） | 2 | 尽量通过（T-09 ~ T-10） |

---

## 6. 已知限制与风险

| # | 描述 | 影响范围 | 缓解措施 |
|---|------|---------|---------|
| 1 | 首次登录需在终端手动输入验证码 | 部署/重启 | session 文件持久化，后续免验证 |
| 2 | Telegram 限速（频率限制） | 高频发送 | 适配器内置延迟和分段逻辑 |
| 3 | 国内网络直连 Telegram 不通 | 连接 | 支持 socks5/http 代理配置 |
| 4 | Telegram 可能封禁 Userbot 行为 | 长期使用 | 人感增强降低检测风险；建议使用非新注册号 |
| 5 | 异步事件循环与 Gateway 同步代码的桥接 | bootstrap | 使用 `asyncio.run()` / `loop.create_task()` 桥接 |

---

## 7. 回滚方案

如果真机测试发现严重问题：

```bash
# 回退到前一个稳定 commit（添加 Telegram 前）
git revert <COMMIT_HASH>
```

**受影响文件回滚优先级**：
1. `edge_client/gateway/bootstrap.py` — 移除 Telegram 注册块
2. `edge_client/config.py` — 移除 Telegram 字段
3. `edge_client/gateway/adapters/telegram_adapter.py` — 删除整个文件
4. `edge_client/gateway/models.py` — 移除 `TELEGRAM` 枚举值

已有平台（wechat/qq/feishu/wecom）完全不受影响——所有新增字段均有默认值，Telegram 注册块仅在 `enabled_platforms` 包含 `"telegram"` 时激活。

---

## 8. 数据流图

```
Telegram 用户
    │
    │ MTProto 协议（Telethon）
    │
    ▼
TelegramAdapter.start_listening()
    │
    │ events.NewMessage → _telethon_event_to_unified()
    │
    ▼
UnifiedMessage(platform=TELEGRAM)
    │
    │ → MessageNormalizer.normalize()
    │ → GatewayRouter.route()
    │ → EdgeInputReporter.report_gateway()
    │
    ▼
POST /v1/host/input (Host Brain :8000)
    │
    │ 主链处理 → SceneRouter → SCENE_GATEWAY
    │
    ▼
UnifiedReply(platform=TELEGRAM)
    │
    │ → OutboundDispatcher → TelegramAdapter.send_reply()
    │ → _send_text_with_typing() [人感增强]
    │
    ▼
Telegram 用户看到
  ✅ "正在输入..." → 分段消息 → 正常联系人（非 Bot）
```

---

<!-- 运行节点请在下方填写结果，或在 runtime_feedback_reports/ 中新建反馈报告 -->
