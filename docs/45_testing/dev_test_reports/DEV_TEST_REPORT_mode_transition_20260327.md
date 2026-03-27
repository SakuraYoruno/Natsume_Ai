受众：MASTER
标题：智能模式切换系统开发实测报告

# DEV_TEST_REPORT: 智能模式切换系统 (ModeTransitionService)

- **日期**: 2026-03-27
- **开发节点**: v_wenloliu workstation
- **功能**: 系统运行模式智能切换（手动 + 自动）
- **涉及文件**: 9 个新增/修改

---

## 1. 变更摘要

### 1.1 Bug 修复
| # | 文件 | 修复内容 | 严重度 |
|---|------|---------|--------|
| BF-1 | `host_brain/core/metabolism.py` | `ALLOWED_SYS_MODES` 集合补充 `sleep`（原先缺失导致 sleep 模式永远被归一化为 normal） | P0 |

### 1.2 新增文件
| # | 文件 | 说明 |
|---|------|------|
| NF-1 | `host_brain/core/mode_transition.py` | ModeTransitionService 核心服务（~310 行）—— 手动+自动模式切换统一管理、合法路径校验、审计日志 |
| NF-2 | `host_brain/core/activity_monitor.py` | ActivityMonitor 用户活动监控（~310 行）—— Host 端游戏进程检测、空闲检测、早间唤醒 |
| NF-3 | `host_brain/app/monitor_ui/src/pages/RuntimeMode.jsx` | WebUI 运行模式管理页面（~280 行）—— 模式切换、自动规则开关、切换历史、影响矩阵 |
| NF-4 | `docs/50_project/arch/MODE_TRANSITION_SYSTEM.md` | 技术文档 |

### 1.3 修改文件
| # | 文件 | 修改内容 |
|---|------|---------|
| MF-1 | `host_brain/app/obs_routes.py` | 新增 5 个 REST API 端点：GET/POST `/obs/sys-mode`、GET `/obs/sys-mode/history`、PUT `/obs/sys-mode/auto-rules`、POST `/obs/sys-mode/event` |
| MF-2 | `host_brain/app/monitor_ui/src/api/obs.js` | 新增 5 个 API 函数 |
| MF-3 | `host_brain/app/monitor_ui/src/App.jsx` | 注册 RuntimeMode 路由 |
| MF-4 | `host_brain/app/monitor_ui/src/components/Layout.jsx` | 导航栏添加 "Mode" 入口 |
| MF-5 | `.env.example` | 添加 6 个新配置项注释 |
| MF-6 | `docs/00_index/00_DOCMAP.md` | 注册新文档 |

---

## 2. 本机已验证项

| # | 验证项 | 方式 | 结果 |
|---|--------|------|------|
| V-1 | `ALLOWED_SYS_MODES` 包含 `sleep` | 代码检查 | ✅ `{"normal", "focus", "gaming", "sleep", "maintenance", "locked"}` |
| V-2 | `_normalize_sys_mode("sleep")` 返回 `"sleep"` | 逻辑推演 | ✅ sleep ∈ ALLOWED_SYS_MODES，直接返回 |
| V-3 | `SYSMODE_PRIORITY_MATRIX["sleep"]` 存在 | 代码检查 task_registry.py | ✅ `{HIGH, MEDIUM}` |
| V-4 | ModeTransitionService.switch_mode 合法路径校验 | 代码审查 | ✅ VALID_TRANSITIONS 定义了 6×N 合法切换路径 |
| V-5 | ModeTransitionService.on_event 5 种自动触发器 | 代码审查 | ✅ gaming_detect/exit, idle_check, morning_check, user_message |
| V-6 | ActivityMonitor 游戏进程检测 API | 代码审查 | ✅ GetForegroundWindow + psutil，30+ 进程匹配 |
| V-7 | obs_routes.py 5 个新端点无语法错误 | Lint 检查 | ✅ 0 errors |
| V-8 | RuntimeMode.jsx 页面无语法错误 | Lint 检查 | ✅ 0 errors |
| V-9 | obs.js API 层 5 个函数格式正确 | 代码审查 | ✅ 与现有 API 风格一致 |
| V-10 | App.jsx 路由注册 + Layout.jsx 导航注册 | 代码审查 | ✅ `/runtime-mode` 路由、`Zap` 图标 |
| V-11 | .env.example 新增 6 个配置项 | 代码检查 | ✅ HOST_SYS_MODE / HOST_GAMING_PROCESSES / HOST_IDLE_THRESHOLD_MS / HOST_GAMING_CHECK_INTERVAL_S / HOST_IDLE_CHECK_INTERVAL_S |
| V-12 | DOCMAP 注册 | 代码检查 | ✅ MODE_TRANSITION_SYSTEM.md 已注册 |

---

## 3. 待真机验证测试清单

### P0（阻塞级 — 必须通过）

| 用例 | 测试步骤 | 预期结果 |
|------|---------|---------|
| T-MODE-01 | `GET /obs/sys-mode` | 返回 `{"sys_mode": "normal", "auto_rules": {...}, ...}` |
| T-MODE-02 | `POST /obs/sys-mode {"target_mode":"gaming","reason":"测试"}` | 返回 `{"success": true, "from_mode": "normal", "to_mode": "gaming"}` |
| T-MODE-03 | 在 gaming 模式下 `POST /obs/sys-mode {"target_mode":"normal"}` | 成功切回 normal |
| T-MODE-04 | 尝试非法切换 `POST /obs/sys-mode {"target_mode":"sleep"}` （从 gaming） | 返回 `{"success": false, "error": "transition gaming → sleep not allowed"}` |
| T-MODE-05 | sleep 模式生效：`POST /obs/sys-mode {"target_mode":"sleep"}` → 检查调度器是否屏蔽 LOW 任务 | sleep 模式下 LOW 优先级任务被 SYSMODE_FORBIDDEN 拒绝 |

### P1（重要 — 应该通过）

| 用例 | 测试步骤 | 预期结果 |
|------|---------|---------|
| T-MODE-06 | 打开游戏（如原神），等 30s | ActivityMonitor 检测到游戏进程，自动切换到 gaming |
| T-MODE-07 | 关闭游戏，等 30s | 自动切回 normal |
| T-MODE-08 | WebUI 打开 `#/runtime-mode`，点击模式按钮切换 | 页面正常渲染，切换成功有绿色提示 |
| T-MODE-09 | WebUI 切换自动规则开关 | toggle 正常工作，刷新后状态保持 |
| T-MODE-10 | `GET /obs/sys-mode/history` | 返回之前的切换记录列表 |

### P2（优化 — 可延后）

| 用例 | 测试步骤 | 预期结果 |
|------|---------|---------|
| T-MODE-11 | 2 小时不操作 + 深夜 (23:00-06:00) | 自动切换到 sleep |
| T-MODE-12 | 在 sleep 模式下发送微信消息 | 自动从 sleep 唤醒到 normal |
| T-MODE-13 | `POST /obs/sys-mode/event?event_type=gaming_process_detected` | 通过 API 模拟事件也能触发切换 |

---

## 4. 架构图

```
┌──────────────────────────────────────────────────────────┐
│                     WebUI RuntimeMode                     │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ │
│  │normal│ │focus │ │gaming│ │sleep │ │maint.│ │locked│ │
│  └──┬───┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ │
│     │  click                                              │
└─────┼────────────────────────────────────────────────────┘
      │ POST /obs/sys-mode
      ▼
┌──────────────────────────────────────────────────────────┐
│               ModeTransitionService                       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ switch_mode  │  │  on_event    │  │  auto_rules {}   │ │
│  │ (手动切换)   │  │ (自动触发)   │  │  toggle 开关     │ │
│  └──────┬──────┘  └──────┬───────┘  └──────────────────┘ │
│         │ 合法性校验      │                                │
│         ▼                ▼                                │
│  ┌──────────────────────────┐   ┌────────────────────┐   │
│  │ set_sys_mode(target)     │   │ audit_log 写入      │   │
│  │ → evolve_hard_state()    │   │ MODE_TRANSITION     │   │
│  └──────────────────────────┘   └────────────────────┘   │
└──────────────────────────────────────────────────────────┘
      ▲ on_event("gaming_process_detected")
      │
┌──────────────────────────────────────────────────────────┐
│               ActivityMonitor (Host 端)                    │
│  ┌────────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ 游戏进程检测    │  │ 空闲检测      │  │ 早间唤醒       │ │
│  │ 30s 轮询       │  │ 5min 轮询     │  │ 5min 轮询     │ │
│  │ GetForeground  │  │ idle_check    │  │ morning_check │ │
│  │ Window+psutil  │  │              │  │              │ │
│  └────────────────┘  └──────────────┘  └───────────────┘ │
└──────────────────────────────────────────────────────────┘
```

---

## 5. 注意事项

1. **ModeTransitionService 依赖注入**：需要在 `dependencies.py` 中注册 `get_hard_state` 和 `set_sys_mode` 函数。如果这两个函数尚未实现，obs_routes 会自动降级为 `_FallbackModeService`（通过环境变量读写 `HOST_SYS_MODE`）。

2. **ActivityMonitor 启动**：需要在 Host 启动流程中调用 `ActivityMonitor.start()`。如果未启动，只是没有自动切换功能，手动切换（API + WebUI）不受影响。

3. **psutil 依赖**：`ActivityMonitor` 的游戏进程检测依赖 `psutil` 包。如果未安装，会降级使用 Windows API 获取进程名。建议 `requirements.txt` 中确认包含 `psutil`。

4. **前端构建**：修改了 WebUI 源码，需要重新 `npm run build` 生成 `static/monitor/` 产物。
