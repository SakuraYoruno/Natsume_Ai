受众：MASTER
标题：P0/P1 修复开发实测报告 — ModeTransition 依赖注入 + ActivityMonitor 启动集成 + .env.example 补齐

# DEV_TEST_REPORT — P0/P1 修复（2026-03-27）

- **commit**: `acd3546`
- **分支**: codex
- **变更**: 3 files changed, +234 lines

---

## 修复内容

### P0-1: dependencies.py 添加 get_hard_state() / set_sys_mode()

**问题**: ModeTransitionService 依赖 `get_hard_state` 和 `set_sys_mode` 两个函数，但 dependencies.py 中从未定义。obs_routes.py 的 `_get_mode_transition_service()` 试图 import 它们时必定失败，导致永久降级为 FallbackModeService。

**修复**: 
- 在 dependencies.py 添加进程级 `_hard_state` 存储（线程安全 Lock + Optional 单例）
- `get_hard_state()`: 延迟初始化，从 `HOST_SYS_MODE` 环境变量读取初始 sys_mode
- `set_sys_mode(target_mode)`: 通过 `evolve_hard_state` 产生新 immutable HardState，同步更新 `os.environ["HOST_SYS_MODE"]`

### P0-2: ActivityMonitor 集成到 runtime_lifespan

**问题**: `activity_monitor.py` 已创建但从未在启动流程中实例化/启动，游戏进程检测和空闲检测完全不工作。

**修复**:
- 在 `http_hooks.py` 的 `runtime_lifespan()` 中，VTS 主事件循环注册之后、服务健康心跳之前：
  1. 创建 `ModeTransitionService` 实例（注入 `get_hard_state` + `set_sys_mode` + `ledger`）
  2. 创建 `ActivityMonitor` 实例，调用 `start()`
  3. 记录到 `new_module_statuses`
- shutdown 阶段调用 `ActivityMonitor.stop()` 优雅终止

### P1-4: .env.example 补齐 90+ 缺失环境变量

**问题**: 代码审计发现 92 个通过 `os.environ.get()` 使用的变量未在 `.env.example` 注册。

**修复**: 补齐以下分类的关键变量（含注释说明和默认值）：
- LLM Base URL / 模型覆盖（14 项）
- Host → Edge RPC 通信（8 项）
- 语义召回 SemanticRecaller（5 项）
- 情绪系统（17 项）
- Bond 图谱持久化（4 项）
- 检索缓存（3 项）
- Agent 步数限制（3 项）
- 其他 Host 配置（12 项）

---

## 真机验证用例

| # | 优先级 | 用例 | 验证方法 | 预期结果 |
|---|--------|------|----------|----------|
| 1 | P0 | 启动后 ModeTransitionService 非降级 | 启动日志搜索 `ModeTransitionService` | 日志显示 "initialized"，非 "fallback" |
| 2 | P0 | 启动后 ActivityMonitor 正常启动 | 启动日志搜索 `ActivityMonitor` | 日志显示 "已启动" + interval 参数 |
| 3 | P0 | GET /obs/sys-mode 不返回 degraded=true | `curl http://127.0.0.1:18080/obs/sys-mode` | `degraded: false` |
| 4 | P0 | POST /obs/sys-mode 切换成功 | `curl -X POST ... target_mode=focus` | `success: true` |
| 5 | P0 | HardState 初始化正确读取 HOST_SYS_MODE | 设置 `HOST_SYS_MODE=maintenance` 后启动 | GET 返回 `sys_mode: maintenance` |
| 6 | P1 | 优雅关闭时 ActivityMonitor 停止 | `Ctrl+C` 关闭服务 | 日志显示 "ActivityMonitor 已停止" |
| 7 | P1 | 游戏进程检测触发模式切换 | 打开游戏后 30s 内 | GET 返回 `sys_mode: gaming` |

---

## 备注

- **WebUI 构建产物过期**（原 P0-3）需要在运行节点执行 `cd monitor_ui && npm run build`
- **单元测试**（原 P1-5）量大，计划单独版本处理
