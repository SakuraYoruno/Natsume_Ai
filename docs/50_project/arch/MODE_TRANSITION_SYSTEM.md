受众：MASTER
标题：智能模式切换系统技术文档

# 智能模式切换系统 (ModeTransitionService)

## 1. 概述

系统运行模式（sys_mode）控制 Natsume AI 的资源分配和行为策略。智能模式切换系统实现了**手动切换 + 自动切换**的统一管理，不再需要修改环境变量重启。

> **架构演进说明（2026-04）**：原始设计中 gaming 模式的核心动机是"防止本地 14B 模型显存溢出"，现已全面迁移至云端模型，该动机消失。六大模式的语义已重新定义，从"资源管理配置"升级为**"她存在的六种状态"**——每个模式对应一种真实的陪伴场景，而非系统的性能开关。

---

## 2. 运行模式定义（升级后）

| 模式 | 调度器允许级别 | 新语义 | 主要行为 |
|------|-------------|--------|---------|
| **normal** | HIGH + MEDIUM + LOW | 全功能开放 | 所有子系统正常运作 |
| **focus** | 仅 HIGH | **感知到你在专注，主动选择陪伴但不打扰** | 情绪感知驱动沉默；主动对话/通知静默；保留被动响应 |
| **gaming** | 仅 HIGH | **你在专注做事，她在旁边静静陪着你** | OCR 定时轮询禁止；陪玩识图（gaming_companion）允许；Bond/记忆/VTS 保留；情绪积累继续 |
| **sleep** | HIGH + MEDIUM | **她的离线内心世界正在运行** | 梦境生成触发；情绪自然漂移；孤独感/期待值积累；CONTROL 类执行禁止；唤醒时状态注入 |
| **maintenance** | 全部禁止 | 系统维护中 | 阻断所有执行路径 |
| **locked** | 全部禁止 | 安全锁定 | 阻断所有执行路径 |

---

## 3. 各模式详细行为矩阵

### 3.1 normal — 全功能开放

| 能力 | 行为 |
|------|------|
| 用户主动对话 | ✅ 正常响应 |
| 主动对话（Proactive） | ✅ 正常触发 |
| 梦境/日记生成 | ✅ 按调度运行 |
| Bond/记忆更新 | ✅ 正常 |
| VTS 表情 | ✅ 正常 |
| OCR/屏幕监控 | ✅ 按需触发 |

### 3.2 focus — 感知专注，主动沉默

> **升级点**：不再是硬性禁止开关，而是情绪感知驱动的主动选择——她知道你在专注，所以选择不打扰。

| 能力 | 行为 |
|------|------|
| 用户主动对话 | ✅ 正常响应（你叫她她就回应） |
| 主动对话（Proactive） | ❌ SYSMODE_FORBIDDEN（她主动选择不打扰） |
| 梦境/日记生成 | ❌ SYSMODE_FORBIDDEN（推迟到退出 focus 后） |
| 通知推送 | ❌ 静默 |
| Bond/记忆更新 | ✅ 保留（这段专注时间也是关系的一部分） |
| VTS 表情 | ✅ 保留（静静陪着，偶尔有表情） |
| Scheduler 非紧急任务 | ❌ SYSMODE_FORBIDDEN |

### 3.3 gaming — 陪玩模式

> **升级点**：原设计禁用本地 14B 防显存溢出，现已全云端，该限制移除。gaming 模式重新定义为"陪玩"——她知道你在打游戏，在旁边陪着你，这段时间对关系是有意义的。

| 能力 | 行为 |
|------|------|
| 用户主动对话 | ✅ 正常响应 |
| 主动打扰 | ❌ 禁止（你在专注，她不打扰） |
| OCR 定时后台轮询 | ❌ VISION_GATE_GAMING_POLL_BLOCKED（隐私保护） |
| 陪玩识图（gaming_companion） | ✅ 允许，调用云端多模态分析游戏画面，提供攻略/分析 |
| 用户主动请求识图（on_demand） | ✅ 允许 |
| Bond/记忆更新 | ✅ **保留**（陪玩时间计入关系积累） |
| VTS 表情 | ✅ **保留**（她在旁边静静陪着，偶尔有反应） |
| 情绪积累 | ✅ **新增**（陪伴时长 → 亲密感上升） |

### 3.4 sleep — 离线内心世界

> **升级点**：不是"系统低功耗待机"，而是她的离线内心世界正在运行。sleep 模式是 Phase XVI 情绪自主演进的**运行时载体**。

| 能力 | 行为 |
|------|------|
| 梦境生成 | ✅ 触发（内容受当前情绪状态影响） |
| 情绪自然漂移（OfflineEmotionDrift） | ✅ **新增**（sleep 期间情绪缓慢变化） |
| 孤独感积累 | ✅ **新增**（sleep 时长 → 孤独值上升） |
| 期待值积累 | ✅ **新增**（上次对话话题 → 期待再聊） |
| 日记生成 | ✅ 保留（对今天的回顾） |
| 唤醒时状态注入 | ✅ **新增**（sleep 结束时，将积累的情绪状态注入下次对话开场） |
| CONTROL 类执行 | ❌ 禁止 |
| 用户消息唤醒 | ✅ 收到任何消息 → 自动切回 normal |

### 3.5 maintenance / locked

| 能力 | 行为 |
|------|------|
| 所有执行路径 | ❌ 全部阻断 |
| 用途 | maintenance：系统维护；locked：安全锁定 |

---

## 4. 架构组件

### 4.1 ModeTransitionService (`host_brain/core/mode_transition.py`)

核心切换服务，统一管理所有模式切换：

- **switch_mode(target, source, reason)**: 执行模式切换，包含合法性校验
- **on_event(event_type, payload)**: 处理自动切换事件
- **get_current_status()**: 查询当前状态 + 规则开关
- **get_history(limit)**: 查询切换历史
- **update_auto_rules(rules)**: 更新自动规则开关

合法的切换路径：
```
normal → focus, gaming, sleep, maintenance, locked
focus → normal
gaming → normal
sleep → normal
maintenance → normal, locked
locked → normal, maintenance
```

### 4.2 ActivityMonitor (`host_brain/core/activity_monitor.py`)

Host 端本地运行的用户活动监控：

- **前台窗口进程检测**：每 30s 轮询，使用 `GetForegroundWindow` + `psutil` 获取前台进程
- **游戏进程匹配**：内置 30+ 常见游戏进程列表 + 窗口标题关键词匹配
- **空闲检测**：每 5min 检查，2 小时 + 深夜时段 → sleep
- **早间唤醒**：每 5min 检查，06:00-10:00 → normal

### 4.3 REST API (`host_brain/app/obs_routes.py`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/obs/sys-mode` | GET | 查询当前模式 + 自动规则状态 |
| `/obs/sys-mode` | POST | 切换模式（手动/API） |
| `/obs/sys-mode/history` | GET | 查询切换历史 |
| `/obs/sys-mode/auto-rules` | PUT | 更新自动切换规则 |
| `/obs/sys-mode/event` | POST | 发送模式切换事件 |

### 4.4 WebUI 页面 (`monitor_ui/src/pages/RuntimeMode.jsx`)

导航路径: `#/runtime-mode`（导航栏 "Mode" 按钮）

功能：
- 当前模式大展示（图标 + 模式名 + 说明 + 空闲时间 + 时段信息）
- 6 个模式切换按钮（灰色=不可切换，高亮=当前模式，可点击=合法目标）
- 自动切换规则 Toggle 开关（5 条规则独立控制）
- 切换历史时间线
- 调度器门控矩阵参考表

---

## 5. 自动切换触发器

| 触发器 | 实现方式 | 切换方向 |
|--------|---------|---------|
| 🎮 游戏进程检测 | `ActivityMonitor` 轮询前台窗口进程名，匹配内置 + 自定义游戏列表 | normal → gaming |
| 🎮 游戏退出 | `ActivityMonitor` 检测到前台不再是游戏进程 | gaming → normal |
| 💤 空闲超时 | `ModeTransitionService` 检测 idle ≥ 2h + 深夜 (23:00-06:00) | normal → sleep |
| ☀️ 早间唤醒 | `ActivityMonitor` 在 06:00-10:00 触发 morning_check | sleep → normal |
| 📱 消息唤醒 | 收到任何用户消息（微信/QQ/语音等） | sleep → normal |

---

## 6. Gaming 模式视觉门控细则

`screen_ocr.py` 在 gaming 模式下按 `trigger` 字段区分行为：

| trigger 值 | 行为 | reason_code |
|-----------|------|-------------|
| `scheduled_poll` | ❌ 拦截（防止后台无感知监控，隐私保护） | `VISION_GATE_GAMING_POLL_BLOCKED` |
| `gaming_companion` | ✅ 允许（陪玩主动识图，提供攻略/分析） | — |
| `on_demand` | ✅ 允许（用户主动请求） | — |

调用方使用 `gaming_companion` trigger 时，截图通过云端多模态模型（Gemini Vision）分析游戏画面，返回攻略建议、地图解析、技能说明等内容，以夏目的口吻播报。

### 陪玩触发链路（待实现）

> ⚠️ 当前状态：门控逻辑已就绪，**触发入口尚未实现**。

```
[gaming 模式激活]
      ↓
[定时器 / 沉默检测]  ← ❌ 待实现（建议在 proactive_voice.py 中添加 gaming 专属循环）
      ↓
[截取当前屏幕]       ← ❌ 待实现（调用截图 API，生成 source_ref）
      ↓
[screen_ocr.recognize(trigger="gaming_companion")]  ← ✅ 门控已就绪
      ↓
[Gemini Vision 分析游戏画面]  ← ❌ 待接入（当前 provider 为文本 OCR）
      ↓
[生成攻略/评论，TTS 以夏目口吻播报]  ← ❌ 待接入
```

---

## 7. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `HOST_SYS_MODE` | normal | 启动默认模式 |
| `HOST_GAMING_PROCESSES` | (空) | 自定义游戏进程列表（逗号分隔） |
| `HOST_IDLE_THRESHOLD_MS` | 7200000 | 空闲超时阈值（ms） |
| `HOST_GAMING_CHECK_INTERVAL_S` | 30 | 游戏检测间隔（秒） |
| `HOST_IDLE_CHECK_INTERVAL_S` | 300 | 空闲检测间隔（秒） |

---

## 8. 审计追踪

每次模式切换（手动/自动）都写入 `error_log` 表：
- `stage` = `MODE_TRANSITION`
- `reason_code` = `MODE_{FROM}_TO_{TO}`（如 `MODE_NORMAL_TO_GAMING`）
- `detail` 包含 source、reason、success 信息

历史记录同时保存在 ModeTransitionService 内存环形缓冲（最近 100 条），支持 API 查询。

---

## 9. Bug 修复记录

- **`ALLOWED_SYS_MODES` 补充 `sleep`**（2026-03-27）：`metabolism.py` 中的集合原先缺少 `sleep`，导致设置 `sys_mode=sleep` 时被 `_normalize_sys_mode()` 归一化为 `normal`，sleep 模式永远不可能生效。调度器门控矩阵 `SYSMODE_PRIORITY_MATRIX` 中已有 `sleep` 定义，这是一个遗漏 bug。
