受众：MASTER
标题：智能模式切换系统技术文档

# 智能模式切换系统 (ModeTransitionService)

## 1. 概述

系统运行模式（sys_mode）控制 Natsume AI 的资源分配和行为策略。智能模式切换系统实现了**手动切换 + 自动切换**的统一管理，不再需要修改环境变量重启。

## 2. 运行模式定义

| 模式 | 调度器允许级别 | 主要影响 |
|------|-------------|---------|
| **normal** | HIGH + MEDIUM + LOW | 全功能开放 |
| **focus** | 仅 HIGH | 主动对话/通知静默，保留被动响应 |
| **gaming** | 仅 HIGH | Host 8B 禁用→Cloud/Edge 3B、OCR 屏蔽、VTS/Bond/记忆跳过、延迟预算 <2s |
| **sleep** | HIGH + MEDIUM | 梦境生成触发、CONTROL 类执行禁止 |
| **maintenance** | 全部禁止 | 阻断所有执行路径 |
| **locked** | 全部禁止 | 阻断所有执行路径 |

## 3. 架构组件

### 3.1 ModeTransitionService (`host_brain/core/mode_transition.py`)

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

### 3.2 ActivityMonitor (`host_brain/core/activity_monitor.py`)

Host 端本地运行的用户活动监控：

- **前台窗口进程检测**：每 30s 轮询，使用 `GetForegroundWindow` + `psutil` 获取前台进程
- **游戏进程匹配**：内置 30+ 常见游戏进程列表 + 窗口标题关键词匹配
- **空闲检测**：每 5min 检查，2 小时 + 深夜时段 → sleep
- **早间唤醒**：每 5min 检查，06:00-10:00 → normal

### 3.3 REST API (`host_brain/app/obs_routes.py`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/obs/sys-mode` | GET | 查询当前模式 + 自动规则状态 |
| `/obs/sys-mode` | POST | 切换模式（手动/API） |
| `/obs/sys-mode/history` | GET | 查询切换历史 |
| `/obs/sys-mode/auto-rules` | PUT | 更新自动切换规则 |
| `/obs/sys-mode/event` | POST | 发送模式切换事件 |

### 3.4 WebUI 页面 (`monitor_ui/src/pages/RuntimeMode.jsx`)

导航路径: `#/runtime-mode`（导航栏 "Mode" 按钮）

功能：
- 当前模式大展示（图标 + 模式名 + 说明 + 空闲时间 + 时段信息）
- 6 个模式切换按钮（灰色=不可切换，高亮=当前模式，可点击=合法目标）
- 自动切换规则 Toggle 开关（5 条规则独立控制）
- 切换历史时间线
- 调度器门控矩阵参考表

## 4. 自动切换触发器

| 触发器 | 实现方式 | 切换方向 |
|--------|---------|---------|
| 🎮 游戏进程检测 | `ActivityMonitor` 轮询前台窗口进程名，匹配内置 + 自定义游戏列表 | normal → gaming |
| 🎮 游戏退出 | `ActivityMonitor` 检测到前台不再是游戏进程 | gaming → normal |
| 💤 空闲超时 | `ModeTransitionService` 检测 idle ≥ 2h + 深夜 (23:00-06:00) | normal → sleep |
| ☀️ 早间唤醒 | `ActivityMonitor` 在 06:00-10:00 触发 morning_check | sleep → normal |
| 📱 消息唤醒 | 收到任何用户消息（微信/QQ/语音等） | sleep → normal |

## 5. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `HOST_SYS_MODE` | normal | 启动默认模式 |
| `HOST_GAMING_PROCESSES` | (空) | 自定义游戏进程列表（逗号分隔） |
| `HOST_IDLE_THRESHOLD_MS` | 7200000 | 空闲超时阈值（ms） |
| `HOST_GAMING_CHECK_INTERVAL_S` | 30 | 游戏检测间隔（秒） |
| `HOST_IDLE_CHECK_INTERVAL_S` | 300 | 空闲检测间隔（秒） |

## 6. Bug 修复

- **`ALLOWED_SYS_MODES` 补充 `sleep`**：`metabolism.py` 中的集合原先缺少 `sleep`，导致设置 `sys_mode=sleep` 时被 `_normalize_sys_mode()` 归一化为 `normal`，sleep 模式永远不可能生效。调度器门控矩阵 `SYSMODE_PRIORITY_MATRIX` 中已有 `sleep` 定义，这是一个遗漏 bug。

## 7. 审计追踪

每次模式切换（手动/自动）都写入 `error_log` 表：
- `stage` = `MODE_TRANSITION`
- `reason_code` = `MODE_{FROM}_TO_{TO}`（如 `MODE_NORMAL_TO_GAMING`）
- `detail` 包含 source、reason、success 信息

历史记录同时保存在 ModeTransitionService 内存环形缓冲（最近 100 条），支持 API 查询。
