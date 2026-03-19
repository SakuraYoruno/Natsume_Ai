受众：MASTER
标题：host_brain/governor/ + scheduler/ 层接口契约

版本：v1.0
日期：2026-03-19
归属：docs/15_theory/

---

## 概述

`host_brain/governor/` 是 Natsume AI 的治理门控层，负责在每次请求执行前评估 R1–R8 八条规则，决定是否放行（APPROVE）或拒绝（REJECT）。

`host_brain/scheduler/` 是定时调度层，负责按 cron/interval/once 三种模式触发系统主动 Intent，所有调度触发的 Intent 必须经过与用户请求相同的治理链路。

---

## 一、Governor 层

### 1.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `rules.py` | R1–R8 门控规则评估（核心） |
| `decision.py` | 治理决策聚合 |
| `policy_cache.py` | 策略缓存 |
| `rate_limit_checker.py` | 速率限制检查器 |
| `rate_limit_store.py` | 速率限制存储 |
| `route_token.py` | 路由令牌 |
| `schemas.py` | 治理 Schema 定义 |
| `tier_policy.py` | 分层策略 |
| `tokens.py` | TokenService（WRITE 令牌服务） |

### 1.2 GateSnapshot（快照输入）

```python
@dataclass(frozen=True)
class GateSnapshot:
    trust_level: int          # 信任等级 [0, 100]
    energy_level: int         # 能量等级 [0, 100]
    sys_mode: str             # 系统模式（"normal"|"locked"|"maintenance"）
    rate_limit_hit: bool      # 是否触发速率限制
    scope: str                # 请求范围（"host"|"memory"|"system"）
    scope_allowlist: tuple    # 允许的范围列表
    safety_flag: bool         # 安全标志
    airlock_blocked: bool     # 气闸阻断标志
    # Rate Limit 扩展字段
    rate_limit_backend: str | None
    rate_limit_degraded: bool
    rate_limit_degraded_from: str | None
    rate_limit_reason_code: str | None
    # M27-A 情感字段（OPT-19）
    mood_state: str           # "steady"|"elevated"|"critical"
    emotion_tag: str          # "NEUTRAL"|"HAPPY"|"ANGRY"|"FRUSTRATED"|...
    emotion_intensity: float  # [0.0, 1.0]
```

**快照归一化规则**（`_normalize_snapshot`）：
- `trust_level`：clamp 到 [0, 100]，默认 50
- `energy_level`：clamp 到 [0, 100]，默认 60
- `sys_mode`：lowercase，默认 "normal"
- `scope_allowlist`：默认 `("host", "memory", "system")`，不允许为空
- `mood_state`：lowercase，默认 "steady"
- `emotion_tag`：uppercase，默认 "NEUTRAL"
- `emotion_intensity`：float，默认 0.5

### 1.3 evaluate_governor_gates（核心评估函数）

```python
result = evaluate_governor_gates(input_snapshot: dict | None) -> dict
```

**输出结构**：
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
    },
    ...
  ]
}
```

**评估顺序**：R1 → R2 → R3 → R4 → R5 → R6 → R7 → R8（固定顺序，不可跳过）

**决策规则**：
- 首个 `hit == True` 的规则触发 `REJECT`，`reason_code` = 该规则的 `reason_hint`
- 后续规则继续评估（全量 trace），但不覆盖已有 `reason_code`
- 所有规则均未命中 → `APPROVE`

### 1.4 八条门控规则

#### R1_TRUST — 信任门控
```
条件：trust_level < 30
错误码：TRUST_LOW
```

#### R2_ENERGY — 能量门控
```
条件：energy_level < 20
错误码：ENERGY_LOW
```

#### R3_SYSMODE — 系统模式门控
```
条件：sys_mode in ("locked", "maintenance")
错误码：SYSMODE_BLOCKED
```

#### R4_RATE_LIMIT — 速率限制门控
```
条件：rate_limit_hit == True
错误码：RATE_LIMITED
附加信息：rate_limit_backend / rate_limit_degraded / rate_limit_reason_code
```

#### R5_SCOPE — 范围门控
```
条件：scope not in scope_allowlist
错误码：SCOPE_DENY
```

#### R6_SAFETY — 安全门控
```
条件：safety_flag == True
错误码：SAFETY_BLOCKED
```

#### R7_AIRLOCK — 气闸门控
```
条件：airlock_blocked == True
错误码：AIRLOCK_BLOCKED
```

#### R8_EMOTION — 情感感知门控（M27-A OPT-19）
```
条件：mood_state == "critical" AND emotion_tag in {"ANGRY", "FRUSTRATED"}
错误码：EMOTION_GATE_BLOCKED
附加字段：
  simplified_prompt: bool  # mood_state == "critical" 时为 True（即使未 hit）
  degraded_hint: str | None  # hit 时附加用户可见降级说明
```

**R8 特殊行为**：
- `mood_state == "critical"` 时，无论是否 hit，均注入 `simplified_prompt = True`
- 仅 `critical + ANGRY/FRUSTRATED` 组合才触发 REJECT
- `simplified_prompt` 标记供下游 Prompt 构建层使用（简化提示词）

### 1.5 错误码对照

| 错误码 | 来源规则 | 含义 |
|--------|---------|------|
| `TRUST_LOW` | R1 | 信任等级不足 |
| `ENERGY_LOW` | R2 | 能量等级不足 |
| `SYSMODE_BLOCKED` | R3 | 系统模式禁止 |
| `RATE_LIMITED` | R4 | 速率限制触发 |
| `SCOPE_DENY` | R5 | 范围不在白名单 |
| `SAFETY_BLOCKED` | R6 | 安全标志触发 |
| `AIRLOCK_BLOCKED` | R7 | 气闸阻断 |
| `EMOTION_GATE_BLOCKED` | R8 | 情感状态触发门控 |

### 1.6 TokenService（WRITE 令牌服务）

`governor/tokens.py` 提供 WRITE 操作的令牌验证服务：

```python
validation = token_service.validate_execution_tokens(
    intent_id: str,
    skill_name: str,
    session_id: str,
    seq: int,
    approval_token_ref: str | None,
    confirm_token_ref: str | None,
    require_confirm: bool,
    now_ts_ms: int,
) -> TokenValidationResult
```

- `side_effect_level == "WRITE"` 的工具必须通过令牌验证
- `auth_level == "CONFIRM"` 的工具额外需要 `confirm_token_ref`
- 验证失败返回 `TOKEN_INVALID` 或 `CONFIRM_REQUIRED`

---

## 二、Scheduler 层

### 2.1 模块清单

| 模块文件 | 职责 |
|---------|------|
| `scheduler.py` | NatsumeScheduler 主体（核心） |
| `task_registry.py` | 任务注册表与配置 |
| `health.py` | 调度器健康状态报告 |
| `health_check_prompt.py` | 心跳检测 Prompt 模板 |
| `scheduler_health_mixin.py` | 健康状态 Mixin |
| `scheduler_routes.py` | 调度器 HTTP 路由 |

### 2.2 SchedulerTaskConfig（任务配置）

```python
@dataclass
class SchedulerTaskConfig:
    task_id: str                    # 任务唯一 ID
    trigger_mode: TriggerMode       # INTERVAL | CRON | ONCE
    trigger_config: dict            # 触发参数（见下表）
    intent_template: dict           # Intent 模板（含 skill_name/args）
    priority: TaskPriority          # HIGH | NORMAL | LOW | MEDIUM
    enabled: bool                   # 是否启用
    max_consecutive_failures: int   # 最大连续失败次数（超出则暂停）
```

**trigger_config 格式**：

| trigger_mode | 参数 | 说明 |
|-------------|------|------|
| `INTERVAL` | `{"seconds": 900}` | 每 N 秒触发一次 |
| `CRON` | `{"hour": 3, "minute": 0}` | 每天 HH:MM 触发 |
| `ONCE` | `{"delay_seconds": 0}` | 延迟 N 秒后触发一次 |

### 2.3 NatsumeScheduler（调度器主体）

```python
scheduler = NatsumeScheduler(
    registry: SchedulerTaskRegistry,
    health_reporter: SchedulerHealthReporter,
    intent_dispatcher: IntentDispatcher | None,  # (intent_dict) -> result_dict
    get_sys_mode: Callable[[], str] | None,
    ledger: Any | None,
    persist_tasks: bool = True,                  # F-40：任务持久化
    get_emotion_state: Callable[[], dict] | None, # M29-N：情感状态获取
)
```

### 2.4 生命周期

```
scheduler.start()
  ├── 从 DB 恢复持久化任务（F-40）
  ├── 为所有 enabled 任务启动定时器
  └── 启动 cron 检查线程

scheduler.stop()
  ├── 取消所有定时器
  └── 等待 cron 检查线程退出（timeout=5s）
```

### 2.5 任务触发流程

```
_trigger_task(task_id)
  ├── 检查任务是否存在 / 启用
  ├── 检查连续失败次数（≥ max → SUSPENDED）
  ├── sys_mode 门控（不允许的模式 → REJECTED）
  ├── 情感状态门控（critical + 非 HIGH 优先级 → REJECTED）
  ├── 生成 system_initiated_intent（含 emotion_tag/mood_state）
  ├── 调用 intent_dispatcher(intent)
  ├── 更新错误计数 / 健康状态
  └── 写入 scheduler_event_log 审计
```

### 2.6 system_initiated_intent 结构

```json
{
  "intent_id": "intent_xxx",
  "request_id": "req_xxx",
  "proposer_node_id": "scheduler",
  "task_id": "task_id",
  "skill_name": "skill_name",
  "args": {},
  "priority": "NORMAL",
  "trigger_mode": "INTERVAL",
  "ts_ms": 1234567890,
  "emotion_tag": "NEUTRAL",
  "mood_state": "steady"
}
```

**约束**：`proposer_node_id` 固定为 `"scheduler"`，用于审计追责。

### 2.7 情感状态门控（M29-N）

调度器在触发任务前检查情感状态：

```
mood_state == "critical" AND task.priority != HIGH
  → REJECTED（reason_code: "EMOTION_GATE_BLOCKED"）
```

- 仅 `HIGH` 优先级任务在 `critical` 状态下仍可执行
- 情感状态通过 `get_emotion_state()` 回调获取

### 2.8 心跳健康检测（TASK-0200）

```python
scheduler.register_agent_health_check(
    interval_seconds=1800,       # 默认 30 分钟
    heartbeat_prompt=None,       # None 时使用默认模板
    max_response_chars=MAX_RESPONSE_CHARS,
    llm_caller=None,             # None 时降级为 LLM_UNAVAILABLE_RESPONSE
)
```

**心跳流程**：
1. 调用 `llm_caller(heartbeat_prompt)` 获取 LLM 响应
2. `parse_heartbeat_response(response)` 解析状态（"OK" | "ALERT"）
3. 状态为 "OK" → 静默丢弃，不写审计
4. 状态为 "ALERT" → 写入 `HEARTBEAT_ALERT` 审计事件
5. 无论状态如何，执行 Edge 传输层探针检查（TASK-0211 M23-B）

**Edge 探针检查**（`_check_edge_transport_probes`）：
- 调用 `build_edge_transport_probe_statuses(available=True)`
- 任意探针 DOWN → 写入 `EDGE_PROBE_ALERT` 审计事件
- `edge_client` 不可用时降级跳过（不崩溃）

### 2.9 任务持久化（F-40）

```python
# 注册并持久化
scheduler.register_task(task)

# 注销并删除持久化记录
scheduler.unregister_task(task_id)

# 重启后自动恢复（start() 时调用）
scheduler._load_persisted_tasks()
```

持久化存储在 `scheduler_tasks` 表（SQLiteLedger）。

### 2.10 健康状态

| 状态 | 含义 |
|------|------|
| `STATUS_OK` | 正常运行 |
| `STATUS_ERROR` | 最近一次执行失败 |
| `STATUS_SUSPENDED` | 连续失败次数超限，已暂停 |

```python
# 查看所有任务健康状态
scheduler.list_health() -> list[dict]

# 手动重置错误计数（恢复暂停任务）
scheduler.reset_error_count(task_id)
```

---

## 三、Governor × Scheduler 协作关系

```
NatsumeScheduler._trigger_task()
  ├── sys_mode 门控（直接检查，不走 evaluate_governor_gates）
  ├── 情感状态门控（直接检查，不走 evaluate_governor_gates）
  └── 生成 system_initiated_intent
        ↓
  intent_dispatcher(intent)
        ↓
  Governor.evaluate_governor_gates()  ← 完整 R1-R8 评估
        ↓
  TokenService.validate_execution_tokens()  ← WRITE 令牌验证
        ↓
  ToolBroker.execute()
```

**关键约束**：调度器触发的 Intent 与用户请求享有**相同的治理链路**（R1–R8 + 令牌验证），不存在调度器绕过治理的路径。

---

## 四、与其他层的接口关系

- **Governor → integrations/**：`evaluate_governor_gates` 的结果注入 `GraphState.governor_result`（见 `INTEGRATIONS_LAYER_CONTRACT.md`）
- **Scheduler → tools/**：调度器生成的 Intent 最终由 `ToolBroker.execute()` 执行（见 `TOOLS_LAYER_CONTRACT.md`）
- **Governor → core/**：`GateSnapshot` 的 `energy_level`/`mood_state`/`emotion_tag` 来自 `metabolism.py` 和 `prompt_context.py`（见 `CORE_LAYER_MODULE_REFERENCE.md`）
