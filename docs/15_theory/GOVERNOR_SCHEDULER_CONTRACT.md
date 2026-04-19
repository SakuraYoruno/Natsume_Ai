受众：MASTER
标题：host_brain/governor/ + scheduler/ 层接口契约（当前代码实况）

版本：v2.0
日期：2026-04-19
归属：docs/15_theory/

---

定位：本文描述当前代码里的 Governor 与 Scheduler 契约。
需要特别注意：当前仓库里实际存在两套“调度”能力，一套是 `host_brain/scheduler/` 的声明式 Intent 调度器，另一套是 `host_brain/app/automation/task_scheduler.py` 的 Host 常驻任务调度器。若与旧文档冲突，以本文和代码为准。

## 0. 代码证据入口

- `host_brain/governor/rules.py`
- `host_brain/governor/decision.py`
- `host_brain/governor/tokens.py`
- `host_brain/governor/schemas.py`
- `host_brain/governor/policy_cache.py`
- `host_brain/governor/tier_policy.py`
- `host_brain/scheduler/task_registry.py`
- `host_brain/scheduler/scheduler.py`
- `host_brain/scheduler/health.py`
- `host_brain/scheduler/scheduler_routes.py`
- `host_brain/app/automation/task_scheduler.py`
- `host_brain/app/automation/scheduler_routes.py`
- `host_brain/app/fastapi_app.py`

---

## 一、Governor 层

### 1.1 当前模块清单

| 模块文件 | 当前职责 |
|---|---|
| `rules.py` | `evaluate_governor_gates()`：R1-R8 固定顺序评估 |
| `decision.py` | 治理决策聚合、行级整理、审计行收口 |
| `tokens.py` | approval / confirm / undo token 发放与绑定校验 |
| `schemas.py` | Governor 输入输出 schema 校验 |
| `policy_cache.py` | 策略缓存 |
| `tier_policy.py` | 分层策略与阈值辅助 |
| `rate_limit_checker.py` / `rate_limit_store.py` | 速率限制判定与存储 |

### 1.2 GateSnapshot 当前结构

`rules.py` 当前 `GateSnapshot`：

```python
GateSnapshot(
    trust_level: int,
    energy_level: int,
    sys_mode: str,
    rate_limit_hit: bool,
    scope: str,
    scope_allowlist: tuple[str, ...],
    safety_flag: bool,
    airlock_blocked: bool,
    rate_limit_backend: str | None = None,
    rate_limit_degraded: bool = False,
    rate_limit_degraded_from: str | None = None,
    rate_limit_reason_code: str | None = None,
    mood_state: str = "steady",
    emotion_tag: str = "NEUTRAL",
    emotion_intensity: float = 0.5,
)
```

归一化规则当前包括：

- `trust_level` clamp 到 `[0, 100]`
- `energy_level` clamp 到 `[0, 100]`
- `sys_mode` lower-case，默认 `normal`
- `scope_allowlist` 为空时回退到 `("host", "memory", "system")`
- `mood_state` lower-case，默认 `steady`
- `emotion_tag` upper-case，默认 `NEUTRAL`

### 1.3 当前评估入口

```python
result = evaluate_governor_gates(input_snapshot: dict | None) -> dict
```

输出结构稳定为：

```json
{
  "decision": "APPROVE" | "REJECT",
  "reason_code": null | "<错误码>",
  "rule_trace": [
    {
      "gate_id": "R1_TRUST",
      "hit": false,
      "threshold_summary": {...},
      "snapshot_summary": {...},
      "reason_hint": "PASS"
    }
  ]
}
```

评估顺序固定为：

`R1 -> R2 -> R3 -> R4 -> R5 -> R6 -> R7 -> R8`

决策规则当前是：

- 首个 `hit=True` 的 gate 决定总 `reason_code`
- 后续规则仍继续执行，保留完整 trace
- 全部未命中时返回 `APPROVE`

### 1.4 R1-R8 当前规则

| Gate | 命中条件 | 错误码 |
|---|---|---|
| `R1_TRUST` | `trust_level < 30` | `TRUST_LOW` |
| `R2_ENERGY` | `energy_level < 20` | `ENERGY_LOW` |
| `R3_SYSMODE` | `sys_mode in ("locked", "maintenance")` | `SYSMODE_BLOCKED` |
| `R4_RATE_LIMIT` | `rate_limit_hit is True` | `RATE_LIMITED` |
| `R5_SCOPE` | `scope not in scope_allowlist` | `SCOPE_DENY` |
| `R6_SAFETY` | `safety_flag is True` | `SAFETY_BLOCKED` |
| `R7_AIRLOCK` | `airlock_blocked is True` | `AIRLOCK_BLOCKED` |
| `R8_EMOTION` | `mood_state == "critical"` 且 `emotion_tag in {"ANGRY","FRUSTRATED"}` | `EMOTION_GATE_BLOCKED` |

### 1.5 R8 的当前特殊行为

`R8_EMOTION` 除了拒绝判定，还有两个当前行为：

- `mood_state == "critical"` 时会写入 `simplified_prompt = True`
- 命中时会附带 `degraded_hint`

所以它不只是“拦不拦”，还会把降级提示透传给下游。

### 1.6 TokenService 当前契约

`governor/tokens.py` 当前支持三类 token：

- `approval`
- `confirm`
- `undo`

关键数据结构：

```python
TokenRecord(
    token_ref: str,
    token_type: str,
    intent_id: str,
    skill_name: str,
    session_id: str,
    seq: int,
    issued_ts_ms: int,
    expiry_ts_ms: int,
    nonce: str,
    used: bool = False,
)
```

当前对外关键接口：

- `issue_approval_token(...)`
- `issue_confirm_token(...)`
- `issue_undo_token(...)`
- `validate_bound_token(...)`
- `validate_execution_tokens(...)`

`validate_execution_tokens(...)` 当前逻辑是：

1. 先校验 `approval_token_ref`，暂不消费
2. 若 `require_confirm=True`，再校验 `confirm_token_ref`
3. 两者都通过后再统一消费

这个顺序是当前代码里明确的重试保护，避免 confirm 失败时提前消耗 approval token。

---

## 二、Scheduler 层：当前有两套

### 2.1 `host_brain/scheduler/`：声明式 Intent 调度器

这套调度器面向“系统主动 Intent”，核心是：

- `SchedulerTaskRegistry`
- `NatsumeScheduler`
- `SchedulerHealthReporter`

它的文档口径是：

- 按 `cron / interval / once` 触发
- 生成 `system_initiated_intent`
- 仍应走治理链和审计链

### 2.2 `host_brain/app/automation/task_scheduler.py`：Host 常驻任务调度器

这套调度器面向 Host 进程内部的后台协程任务，核心是：

- `ScheduledTask`
- `HostTaskScheduler`

它不是声明式 Intent registry，而是常驻循环任务管理器。

因此当前不要把两者混成同一个组件。

---

## 三、声明式 Scheduler（`host_brain/scheduler/`）

### 3.1 任务配置

`task_registry.py` 当前的 `SchedulerTaskConfig`：

```python
SchedulerTaskConfig(
    task_id: str,
    trigger_mode: TriggerMode,      # cron / interval / once
    trigger_config: dict[str, Any],
    intent_template: dict[str, Any],
    priority: TaskPriority = MEDIUM,
    mode_allowlist: list[str] | None = None,
    enabled: bool = True,
    max_consecutive_failures: int = 3,
    description: str = "",
)
```

### 3.2 触发模式

| trigger_mode | 当前含义 |
|---|---|
| `cron` | 定时点触发 |
| `interval` | 周期触发 |
| `once` | 单次触发 |

### 3.3 sys_mode 门控矩阵

`SYSMODE_PRIORITY_MATRIX` 当前规则：

| sys_mode | 允许优先级 |
|---|---|
| `normal` | `HIGH`, `MEDIUM`, `LOW` |
| `sleep` | `HIGH`, `MEDIUM` |
| `gaming` | `HIGH` |
| `focus` | `HIGH` |
| `maintenance` | 空 |
| `locked` | 空 |

若任务显式配置 `mode_allowlist`，则以 allowlist 优先。

### 3.4 NatsumeScheduler 当前职责

`scheduler.py` 里的 `NatsumeScheduler` 当前负责：

- 从 registry 读取 enabled 任务
- 启动 interval/once timer
- 启动 cron 检查线程
- 维护错误计数、上次执行时间、健康状态
- 在运行时做 endocrine / immune / sys_mode 等门控
- 可选从 ledger 恢复持久化任务

### 3.5 健康快照

`SchedulerHealthReporter.build_health_snapshot()` 当前返回：

```json
{
  "scheduler_status": "HEALTHY" | "DEGRADED",
  "total_tasks": 0,
  "enabled_tasks": 0,
  "suspended_tasks": 0,
  "error_tasks": 0,
  "tasks": [...]
}
```

任务级别会带：

- `status`
- `last_run_ts`
- `next_run_ts`
- `error_count`
- `suspended_reason`
- `last_reason_code`
- `last_gate_state`
- `last_mood_state`
- `last_endocrine_cortisol`
- `last_endocrine_energy`
- `last_immune_decision`
- `last_immune_risk_level`
- `last_immune_reason_codes`

### 3.6 HTTP 路由

`host_brain/scheduler/scheduler_routes.py` 当前挂载到：

- `POST /scheduler/tasks`
- `GET /scheduler/tasks`
- `GET /scheduler/tasks/{task_id}`
- `PATCH /scheduler/tasks/{task_id}`
- `DELETE /scheduler/tasks/{task_id}`
- `POST /scheduler/tasks/{task_id}/enable`
- `POST /scheduler/tasks/{task_id}/disable`
- `GET /scheduler/health`

---

## 四、Host 常驻任务调度器（`app/automation/task_scheduler.py`）

### 4.1 当前定位

这套调度器面向“Host 进程内后台协程任务”，不是声明式 intent registry。

当前关键类型：

```python
ScheduledTask(
    task_id: str,
    name: str,
    fn: TaskFn,
    interval_s: float = 60.0,
    max_retries: int = 3,
    retry_delay_s: float = 5.0,
    enabled: bool = True,
    priority: TaskPriority = P2,
    depends_on: list[str] = [],
)
```

任务状态枚举当前包括：

- `PENDING`
- `RUNNING`
- `SUCCESS`
- `FAILED`
- `DISABLED`
- `CANCELLED`
- `BLOCKED`

### 4.2 HostTaskScheduler 当前能力

`HostTaskScheduler` 当前提供：

- `register()` / `unregister()`
- `enable()` / `disable()`
- `start()` / `stop()`
- `trigger_now()`
- `register_task()`
- `get_all_tasks_status()`
- `health()`

当前调度循环特征：

- 以秒级循环检查任务
- 按优先级排序
- 检查依赖是否满足
- 支持失败重试
- 可即时触发单任务

### 4.3 当前路由面

`host_brain/app/automation/scheduler_routes.py` 被 `fastapi_app.py` 以 `prefix="/host-scheduler"` 挂载。

当前端点：

- `GET /host-scheduler/tasks`
- `GET /host-scheduler/health`
- `POST /host-scheduler/tasks/{task_id}/enable`
- `POST /host-scheduler/tasks/{task_id}/disable`
- `POST /host-scheduler/tasks/{task_id}/trigger`
- `PATCH /host-scheduler/tasks/{task_id}`

---

## 五、当前代码里的关系图

```text
/v1/chat
  -> chat_handler
  -> governor rules / decision
  -> thalamus / cortex_orchestrator / executive runtime

/scheduler/*
  -> SchedulerTaskRegistry + NatsumeScheduler + SchedulerHealthReporter

/host-scheduler/*
  -> HostTaskScheduler
```

也就是说，Governor 是请求治理层；Scheduler 现在分成“主动 Intent 调度”和“Host 常驻任务调度”两块。

---

## 六、当前不再适用的旧口径

以下说法不再准确：

- 仓库里只有一套 scheduler
- 所有调度路由都挂在同一实现上
- scheduler 只负责 cron/interval/once，而不区分“声明式 Intent”和“Host 协程任务”
- token 验证会在 confirm 前直接消费 approval token

当前正确口径应是：

1. Governor 仍以 `evaluate_governor_gates()` 为核心，R1-R8 全量 trace 保持不变。
2. `TokenService.validate_execution_tokens()` 采用“先校验、后统一消费”的当前实现。
3. `host_brain/scheduler/` 与 `app/automation/task_scheduler.py` 是两套不同调度面。
4. Host Main 同时暴露 `/scheduler/*` 与 `/host-scheduler/*` 两类调度接口。
