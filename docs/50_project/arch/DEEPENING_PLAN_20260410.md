# 数字生命体深化执行文档

> **文档版本**：v1.0 | **日期**：2026-04-10 | **基线**：第三十批（T-193~T-197）完成后
>
> 本文档针对「尚有深化空间」的 5 个方向，给出精确的现状分析、缺口定位、逐条任务清单与验收标准。
> 所有任务编号延续主计划，从 **T-198** 开始。

---

## 目录

1. [方向 A：MotorCortex → Edge 执行闭环](#方向-a)
2. [方向 B：GlymphaticAgent 离线巩固与 ANSAgent 联动](#方向-b)
3. [方向 C：CerebellumAgent 与 MotorCortex 反馈闭环](#方向-c)
4. [方向 D：InteroceptionAgent → LimbicAgent 主动驱动](#方向-d)
5. [方向 E：VestibularAgent 路由偏置实际生效](#方向-e)
6. [任务总览](#任务总览)

---

## 方向 A：MotorCortex → Edge 执行闭环 {#方向-a}

### 现状分析

`MotorCortexAgent` 已实现：
- `plan_action()` → 生成 `plan_id`，写入 `TribeStateRegistry`，发布 `MOTOR_ACTION_PLANNED`
- `dispatch_action()` → 调用 `EdgeCommandPusher.push_command()`，发布 `MOTOR_ACTION_DISPATCHED`
- 订阅 `INITIATIVE_DECISION_MADE` / `PLATFORM_CONNECTED` / `PLATFORM_DISCONNECTED`

**缺口**：
1. **执行反馈回流缺失**：`dispatch_action()` 发出指令后，Edge 侧执行结果（成功/失败/延迟）没有回流到 Host 侧 MotorCortex 的状态中。当前 `last_dispatch_outcome.ok` 仅反映 HTTP 推送是否成功，不反映 Edge 侧实际执行结果。
2. **执行追踪链路断裂**：`plan_id` 在 Host 侧生成，但 Edge 侧执行时没有携带 `plan_id` 回传，导致无法按 `plan_id` 追踪完整执行链路。
3. **主动重试缺失**：`dispatch_action()` 在 `ok=False` 时仅记录失败，没有触发 `CerebellumAgent` 的重试建议并实际执行重试。
4. **执行结果未聚合到 vital_signs**：`last_dispatch_ok` 没有被 `TribeStateRegistry.aggregate_vital_signs()` 纳入，上层无法感知执行健康度。

### 任务清单

#### T-198：Edge 侧执行结果回流接口

**文件**：`edge_client/tribe/peripheral_agent.py`（或新增 `edge_client/tribe/motor_feedback_agent.py`）

**实现要点**：
```python
# Edge 侧执行完 motor_action_command 后，向 Host 回传执行结果
def report_motor_execution_result(
    *,
    plan_id: str,
    action: str,
    ok: bool,
    latency_ms: int,
    reason: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """将 Edge 侧执行结果通过 EdgeTribeBridge 回传 Host。"""
    ...
```

**接入点**：`edge_client/app/fastapi_app.py` 中 `receive_command()` 处理 `motor_action_command` 后调用。

**验收**：Edge 执行 `motor_action_command` 后，Host `MotorCortexAgent.export_runtime_state()` 中 `last_dispatch_ok` 反映 Edge 实际执行结果。

---

#### T-199：MotorCortexAgent 接收 Edge 执行反馈

**文件**：`host_brain/tribe/motor_cortex_agent.py`

**实现要点**：
```python
def receive_execution_feedback(
    self,
    *,
    plan_id: str,
    ok: bool,
    latency_ms: int,
    reason: str | None = None,
    source: str = "edge_feedback",
) -> dict[str, Any]:
    """接收 Edge 侧执行反馈，更新 last_dispatch_outcome，并触发 CerebellumAgent 纠偏。"""
    ...
    # 1. 更新 state_registry 中 last_dispatch_outcome（按 plan_id 匹配）
    # 2. 若 ok=False，发布 MOTOR_EXECUTION_FAILED 事件
    # 3. 发布 MOTOR_EXECUTION_FEEDBACK 事件供 CerebellumAgent 订阅
```

新增事件订阅：
```python
bus.subscribe("MOTOR_EXECUTION_FEEDBACK", self._on_execution_feedback)
```

**验收**：`receive_execution_feedback(plan_id=..., ok=False)` 后，`export_runtime_state()["last_dispatch_ok"]` 为 `False`，且 `MOTOR_EXECUTION_FAILED` 事件被发布。

---

#### T-200：vital_signs 纳入执行健康度

**文件**：`host_brain/tribe/tribe_state.py`

**实现要点**：在 `aggregate_vital_signs()` 中新增：
```python
motor_state = self.get_state("motor_cortex_agent") or {}
motor_s = dict(motor_state.get("state") or {})
last_dispatch = dict(motor_s.get("last_dispatch_outcome") or {})
vital["motor_execution_ok"] = bool(last_dispatch.get("ok", True))
vital["motor_last_latency_ms"] = int(last_dispatch.get("latency_ms") or 0)
```

**验收**：`aggregate_vital_signs()["motor_execution_ok"]` 在执行失败后返回 `False`。

---

#### T-201：执行闭环集成测试

**文件**：`tests/integration/test_motor_execution_loop.py`

**覆盖场景**：
- `plan_action()` → `dispatch_action()` → `receive_execution_feedback(ok=True)` 全链路
- `receive_execution_feedback(ok=False)` → `MOTOR_EXECUTION_FAILED` 事件发布
- `vital_signs["motor_execution_ok"]` 反映最新执行状态

---

## 方向 B：GlymphaticAgent 离线巩固与 ANSAgent 联动 {#方向-b}

### 现状分析

`GlymphaticAgent` 已实现：
- 订阅 `CIRCADIAN_PHASE_UPDATED` / `CONVERSATION_ENDED` / `DEFAULT_MODE_TICKED`
- `run_recovery_cycle()` 计算 `consolidation_score` / `noise_decay_factor`
- 读取 `hippocampus_agent.resolve_memory_layer_snapshot()`

**缺口**：
1. **记忆巩固仅评分，不写入**：`consolidation_score` 计算完成后，没有调用 `HippocampusAgent.write_episodic_memory_trace()` 或 `write_autobiographical_memory()` 实际写入巩固结果。
2. **ANSAgent 无感知**：`ANSAgent` 的 `emit_inner_world_ticked()` 不感知 `GlymphaticAgent` 的恢复窗口状态，无法在恢复窗口期间调整节律（如降低主动发声频率）。
3. **噪声衰减未实际执行**：`noise_decay_factor` 计算完成后，没有对 `TribeStateRegistry` 中的历史噪声状态执行实际衰减操作。
4. **恢复窗口未通知 InitiativeGovernor**：恢复窗口期间，`InitiativeGovernorAgent` 应自动提高 `DEFER` 概率，但当前没有联动。

### 任务清单

#### T-202：GlymphaticAgent 实际写入记忆巩固

**文件**：`host_brain/tribe/glymphatic_agent.py`

**实现要点**：在 `run_recovery_cycle()` 的 `recovery_window=True` 分支中：
```python
if recovery_window and consolidation_score >= 0.45:
    try:
        from host_brain.tribe.hippocampus_agent import get_hippocampus_agent
        hippocampus = get_hippocampus_agent()
        # 将高分记忆从 episodic 提升到 semantic
        hippocampus.write_semantic_memory_fact(
            fact=f"glymphatic_consolidation:{resolved_ts}",
            confidence=consolidation_score,
            source="glymphatic_agent",
        )
    except Exception:
        pass
```

新增事件：`GLYMPHATIC_CONSOLIDATION_WRITTEN`（携带 `consolidation_score` / `memory_write_type`）

**验收**：`run_recovery_cycle(force=True)` 后，`HippocampusAgent` 的 `semantic` 层有新记录写入。

---

#### T-203：ANSAgent 感知恢复窗口，调整节律

**文件**：`host_brain/tribe/ans_agent.py`

**实现要点**：
```python
# 订阅 GLYMPHATIC_CYCLE_COMPLETED
bus.subscribe("GLYMPHATIC_CYCLE_COMPLETED", self._on_glymphatic_cycle)

async def _on_glymphatic_cycle(self, event: Any) -> None:
    payload = dict(getattr(event, "payload", None) or {})
    executed = bool(payload.get("executed", False))
    if executed:
        # 恢复窗口期间：降低主动发声频率（写入 ANS 状态）
        self._touch_recovery_mode(active=True, ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()))
    else:
        self._touch_recovery_mode(active=False, ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()))
```

在 `emit_inner_world_ticked()` 中检查恢复模式，若 `recovery_mode=True` 则降低 `proactive_multiplier`。

**验收**：`GLYMPHATIC_CYCLE_COMPLETED(executed=True)` 后，`ANSAgent` 的 `proactive_multiplier` 降低至 `≤ 0.4`。

---

#### T-204：恢复窗口通知 InitiativeGovernor

**文件**：`host_brain/tribe/initiative_governor_agent.py`

**实现要点**：
```python
bus.subscribe("GLYMPHATIC_CYCLE_COMPLETED", self._on_glymphatic_cycle)

async def _on_glymphatic_cycle(self, event: Any) -> None:
    payload = dict(getattr(event, "payload", None) or {})
    # 恢复窗口期间，写入 recovery_mode 标志
    self.state_registry.write(
        namespace="initiative_governor_agent",
        state={"recovery_mode": bool(payload.get("executed", False))},
        writer="initiative_governor_agent",
        merge=True,
        reason="glymphatic_recovery_window",
    )
```

在 `evaluate_initiative()` 中：若 `recovery_mode=True`，`readiness` 阈值提高 `+0.15`（更容易 DEFER）。

**验收**：恢复窗口期间，`evaluate_initiative()` 对同一请求返回 `DEFER` 的概率显著提高。

---

## 方向 C：CerebellumAgent 与 MotorCortex 反馈闭环 {#方向-c}

### 现状分析

`CerebellumAgent` 已实现：
- 订阅 `MOTOR_ACTION_DISPATCHED`，自动调用 `evaluate_correction()`
- 输出 `strategy`（`RETRY_ONCE` / `DEGRADE_OUTPUT` / `LATENCY_COMPENSATE` / `SAFE_FALLBACK` / `NO_ADJUST`）
- 发布 `CEREBELLUM_CORRECTION_DECIDED`

**缺口**：
1. **纠偏策略仅评估，不执行**：`RETRY_ONCE` 策略评估完成后，没有实际触发 `MotorCortexAgent.dispatch_action()` 重试。
2. **MotorCortexAgent 不订阅纠偏事件**：`MotorCortexAgent` 没有订阅 `CEREBELLUM_CORRECTION_DECIDED`，无法根据纠偏策略调整下一次 `plan_action()` 的参数。
3. **延迟补偿未传递给 EdgeCommandPusher**：`LATENCY_COMPENSATE` 策略的 `backoff_ms` 没有被 `EdgeCommandPusher` 实际使用。
4. **纠偏历史未聚合**：`correction_count` 没有被 `vital_signs` 纳入，上层无法感知纠偏频率。

### 任务清单

#### T-205：CerebellumAgent 触发实际重试

**文件**：`host_brain/tribe/cerebellum_agent.py`

**实现要点**：在 `_on_motor_action_dispatched()` 中，若 `strategy == "RETRY_ONCE"` 且 `backoff_ms > 0`：
```python
async def _on_motor_action_dispatched(self, event: Any) -> None:
    payload = dict(getattr(event, "payload", None) or {})
    decision = self.evaluate_correction(execution_feedback=payload, source="motor_action_dispatched")
    if decision["strategy"] == "RETRY_ONCE":
        await asyncio.sleep(decision["backoff_ms"] / 1000.0)
        # 发布重试请求事件
        emit_tribe_event(
            event_type="MOTOR_RETRY_REQUESTED",
            source_agent="cerebellum_agent",
            payload={
                "plan_id": decision["plan_id"],
                "backoff_ms": decision["backoff_ms"],
                "urgency": decision["urgency"],
            },
        )
```

**验收**：`MOTOR_ACTION_DISPATCHED(ok=False)` 后，`MOTOR_RETRY_REQUESTED` 事件在 `backoff_ms` 后被发布。

---

#### T-206：MotorCortexAgent 订阅并执行重试

**文件**：`host_brain/tribe/motor_cortex_agent.py`

**实现要点**：
```python
bus.subscribe("MOTOR_RETRY_REQUESTED", self._on_retry_requested)

async def _on_retry_requested(self, event: Any) -> None:
    payload = dict(getattr(event, "payload", None) or {})
    plan_id = str(payload.get("plan_id") or "")
    # 从 state_registry 恢复 last_plan
    snap = self.state_registry.get_state("motor_cortex_agent") or {}
    last_plan = dict((snap.get("state") or {}).get("last_plan") or {})
    if last_plan.get("plan_id") == plan_id:
        self.dispatch_action(plan=last_plan)
```

**验收**：`MOTOR_RETRY_REQUESTED(plan_id=X)` 后，`MotorCortexAgent` 自动重新执行 `dispatch_action()`。

---

#### T-207：vital_signs 纳入纠偏频率

**文件**：`host_brain/tribe/tribe_state.py`

**实现要点**：在 `aggregate_vital_signs()` 中新增：
```python
cerebellum_state = self.get_state("cerebellum_agent") or {}
cerebellum_s = dict(cerebellum_state.get("state") or {})
vital["cerebellum_correction_count"] = int(cerebellum_s.get("correction_count") or 0)
vital["cerebellum_last_strategy"] = str((cerebellum_s.get("last_correction") or {}).get("strategy") or "NO_ADJUST")
```

**验收**：`aggregate_vital_signs()["cerebellum_correction_count"]` 在纠偏后递增。

---

## 方向 D：InteroceptionAgent → LimbicAgent 主动驱动 {#方向-d}

### 现状分析

`InteroceptionAgent` 已实现：
- 订阅 `CIRCADIAN_PHASE_UPDATED` / `PROPRIOCEPTION_UPDATED` / `EMOTION_STATE_CHANGED` / `IMMUNE_BLOCK` / `CONVERSATION_ENDED`
- 计算 `interoceptive_index` / `body_budget` / `recovery_urgency`
- 发布 `INTEROCEPTION_UPDATED`

`LimbicAgent` 已实现：
- `drive_emotion_metabolism(valence, arousal, dominance)` — 情绪代谢驱动入口
- `drive_emotion_metabolism_from_interaction()` — 交互驱动入口

**缺口**：
1. **`INTEROCEPTION_UPDATED` 无人订阅**：`LimbicAgent` 没有订阅 `INTEROCEPTION_UPDATED`，体内感受指数的变化不会主动影响情绪状态。
2. **高负荷/高压力不触发情绪偏移**：`interoceptive_index >= 0.7` 时（高负荷），情绪应向 `低效价 + 低唤醒` 偏移，但当前没有此路径。
3. **`body_budget` 不影响 `motive`**：`InitiativeGovernorAgent` 的 `motive` 评估没有读取 `body_budget`，导致体能耗尽时仍可能产生高主动意愿。
4. **`recovery_urgency` 不触发 `GlymphaticAgent`**：`recovery_urgency >= 0.75` 时应主动触发恢复周期，但当前没有此联动。

### 任务清单

#### T-208：LimbicAgent 订阅 INTEROCEPTION_UPDATED

**文件**：`host_brain/tribe/limbic_agent.py`

**实现要点**：在 `register_subscriptions()` 中新增：
```python
bus.subscribe("INTEROCEPTION_UPDATED", self._on_interoception_updated)
```

新增处理方法：
```python
async def _on_interoception_updated(self, event: Any) -> None:
    payload = dict(getattr(event, "payload", None) or {})
    interoceptive_index = float(payload.get("interoceptive_index") or 0.0)
    body_budget = float(payload.get("body_budget") or 0.5)
    recovery_urgency = float(payload.get("recovery_urgency") or 0.0)

    # 高负荷：情绪向低效价/低唤醒偏移
    if interoceptive_index >= 0.70:
        valence_shift = -(interoceptive_index - 0.70) * 0.8   # 最大 -0.24
        arousal_shift = -(interoceptive_index - 0.70) * 0.5   # 最大 -0.15
        self.drive_emotion_metabolism(
            valence=valence_shift,
            arousal=arousal_shift,
            dominance=0.0,
            blend_weight=0.35,
            event_kind="interoceptive_overload",
            source="interoception_agent",
        )
    # 体能充足：情绪向轻微正效价偏移
    elif body_budget >= 0.75 and interoceptive_index <= 0.30:
        self.drive_emotion_metabolism(
            valence=0.08,
            arousal=0.05,
            dominance=0.0,
            blend_weight=0.20,
            event_kind="body_budget_surplus",
            source="interoception_agent",
        )
```

**验收**：`INTEROCEPTION_UPDATED(interoceptive_index=0.85)` 后，`LimbicAgent` 的情绪效价向负方向偏移。

---

#### T-209：InitiativeGovernor 读取 body_budget

**文件**：`host_brain/tribe/initiative_governor_agent.py`

**实现要点**：在 `evaluate_initiative()` 中：
```python
# 读取 body_budget
try:
    from host_brain.tribe.interoception_agent import get_interoception_agent
    intero_state = get_interoception_agent().export_runtime_state()
    body_budget = float(intero_state.get("body_budget") or 0.5)
except Exception:
    body_budget = 0.5

# body_budget 过低时，降低 motive 评分
if body_budget <= 0.25:
    motive_score = motive_score * 0.6  # 体能耗尽，主动意愿打折
    reason_code = "LOW_BODY_BUDGET"
```

**验收**：`body_budget=0.15` 时，`evaluate_initiative()` 对中等 `motive` 请求返回 `DEFER`。

---

#### T-210：recovery_urgency 主动触发 GlymphaticAgent

**文件**：`host_brain/tribe/interoception_agent.py`

**实现要点**：在 `evaluate_profile()` 末尾：
```python
# 高恢复紧迫度：主动触发恢复周期
if recovery_urgency >= 0.75:
    try:
        from host_brain.tribe.glymphatic_agent import get_glymphatic_agent
        get_glymphatic_agent().run_recovery_cycle(
            source="interoception_urgency",
            force=True,
            now_ts_ms=resolved_ts,
        )
    except Exception:
        pass
```

**验收**：`evaluate_profile()` 产出 `recovery_urgency >= 0.75` 时，`GlymphaticAgent.run_recovery_cycle()` 被自动调用。

---

## 方向 E：VestibularAgent 路由偏置实际生效 {#方向-e}

### 现状分析

`VestibularAgent` 已实现：
- 订阅 `MODE_TRANSITION` / `PLATFORM_CONNECTED` / `PLATFORM_DISCONNECTED` / `INITIATIVE_DECISION_MADE`
- 计算 `jitter_index` / `damping_factor` / `route_bias`（`normal` / `conservative` / `safe_only`）
- 发布 `VESTIBULAR_STABILITY_UPDATED`

`CortexAgent.route_intent()` 已实现：
- 读取 `vestibular_state.jitter_index`
- `jitter_index >= 0.78` → 返回 `throttle`（硬阈值）

**缺口**：
1. **`route_bias` 字段未被消费**：`route_bias = "conservative"` 时（`jitter >= 0.35`），路由决策没有任何变化，只有 `jitter >= 0.78` 的硬阈值才生效。中间区间（`0.35 ~ 0.78`）的 `conservative` 偏置完全被忽略。
2. **`damping_factor` 未影响 Skill 选择**：`damping_factor` 计算完成后，没有被 `dispatch_agent.py` 的 Skill 路由逻辑读取，无法在抖动时偏向更保守的 Skill。
3. **`safe_only` 偏置未实现**：`route_bias = "safe_only"` 时，应只允许最基础的 `chat` Skill，但当前没有此约束。
4. **`VestibularAgent` 状态未被 `CortexAgent.collect_tribe_context()` 完整传递**：`vestibular_state` 已注入 `tribe_context`，但 `route_intent()` 只读取了 `jitter_index`，没有读取 `route_bias` 和 `damping_factor`。

### 任务清单

#### T-211：route_intent 消费 route_bias

**文件**：`host_brain/tribe/cortex_agent.py`

**实现要点**：在 `route_intent()` 中，在 `vestibular_jitter >= 0.78` 判断之后，新增：
```python
route_bias = str(vestibular_state.get("route_bias") or "normal").strip().lower()
damping_factor = float(vestibular_state.get("damping_factor") or 1.0)

# conservative 偏置：降低置信度，倾向 chat（不走复杂 Skill）
if route_bias == "conservative" and vestibular_jitter >= 0.35:
    # 不中断流程，但在返回结果中标记 conservative 偏置
    # 后续 dispatch_agent 可据此限制 Skill 选择
    pass  # 在 route 结果中附加 vestibular_bias 字段

# safe_only 偏置：强制 chat，禁止 Skill
if route_bias == "safe_only":
    return {
        "route": "chat",
        "reason": "vestibular_safe_only",
        "gate_state": gate_state,
        "confidence": 0.85,
        "vestibular_bias": "safe_only",
        "vestibular_jitter": vestibular_jitter,
    }
```

在所有正常 `route` 返回值中附加 `vestibular_bias` 和 `damping_factor` 字段。

**验收**：`route_bias="safe_only"` 时，`route_intent()` 返回 `route="chat"` 且 `vestibular_bias="safe_only"`。

---

#### T-212：dispatch_agent 读取 vestibular_bias 限制 Skill

**文件**：`host_brain/app/dispatch_agent.py`

**实现要点**：在读取 `_tribe_route_result` 之后：
```python
_vestibular_bias = str(_tribe_route_result.get("vestibular_bias") or "normal").strip().lower()
_damping_factor = float(_tribe_route_result.get("damping_factor") or 1.0)

# conservative：跳过复杂 Skill，直接走 chat
if _vestibular_bias == "conservative":
    _logger.info("[%s] VestibularAgent conservative bias: 跳过 Skill 路由", request_id)
    # 强制走 chat 分支（跳过 Skill 选择逻辑）
    ...

# safe_only：已由 route_intent 返回 chat，此处仅记录审计
if _vestibular_bias == "safe_only":
    _logger.info("[%s] VestibularAgent safe_only bias: 仅允许 chat", request_id)
```

**验收**：`vestibular_bias="conservative"` 时，`dispatch_agent` 跳过 Skill 路由，直接走 `chat` 分支。

---

#### T-213：VestibularAgent 集成测试

**文件**：`tests/integration/test_vestibular_route_bias.py`

**覆盖场景**：
- `jitter_index=0.0` → `route_bias="normal"` → `route_intent()` 正常路由
- `jitter_index=0.45` → `route_bias="conservative"` → `route_intent()` 附加 `vestibular_bias="conservative"`
- `jitter_index=0.80` → `route_bias="safe_only"` → `route_intent()` 返回 `route="chat"` + `vestibular_bias="safe_only"`
- `jitter_index=0.85` → `route_intent()` 返回 `route="throttle"`（硬阈值）

---

## 任务总览 {#任务总览}

| 任务 | 方向 | 文件 | 优先级 | 依赖 |
|---|---|---|---|---|
| T-198 | A | `edge_client/tribe/peripheral_agent.py` | 🔴 P0 | — |
| T-199 | A | `host_brain/tribe/motor_cortex_agent.py` | 🔴 P0 | T-198 |
| T-200 | A | `host_brain/tribe/tribe_state.py` | 🟡 P1 | T-199 |
| T-201 | A | `tests/integration/test_motor_execution_loop.py` | 🟡 P1 | T-199, T-200 |
| T-202 | B | `host_brain/tribe/glymphatic_agent.py` | 🔴 P0 | — |
| T-203 | B | `host_brain/tribe/ans_agent.py` | 🟡 P1 | T-202 |
| T-204 | B | `host_brain/tribe/initiative_governor_agent.py` | 🟡 P1 | T-202 |
| T-205 | C | `host_brain/tribe/cerebellum_agent.py` | 🔴 P0 | — |
| T-206 | C | `host_brain/tribe/motor_cortex_agent.py` | 🔴 P0 | T-205 |
| T-207 | C | `host_brain/tribe/tribe_state.py` | 🟢 P2 | T-205 |
| T-208 | D | `host_brain/tribe/limbic_agent.py` | 🔴 P0 | — |
| T-209 | D | `host_brain/tribe/initiative_governor_agent.py` | 🟡 P1 | — |
| T-210 | D | `host_brain/tribe/interoception_agent.py` | 🟡 P1 | T-202 |
| T-211 | E | `host_brain/tribe/cortex_agent.py` | 🔴 P0 | — |
| T-212 | E | `host_brain/app/dispatch_agent.py` | 🔴 P0 | T-211 |
| T-213 | E | `tests/integration/test_vestibular_route_bias.py` | 🟡 P1 | T-211, T-212 |

### 推荐执行批次

| 批次 | 任务 | 主题 |
|---|---|---|
| **第三十一批** | T-198 ~ T-201 + T-205 ~ T-207 | MotorCortex 执行闭环 + Cerebellum 重试闭环 |
| **第三十二批** | T-202 ~ T-204 + T-208 ~ T-210 | Glymphatic 离线巩固 + Interoception 主动驱动 |
| **第三十三批** | T-211 ~ T-213 | Vestibular 路由偏置全链路生效 |

### 完成后预期效果

```
执行闭环（A+C）  MotorCortex → Edge → 反馈回流 → Cerebellum 重试   ████████████ 100%
离线巩固（B）    Glymphatic 实际写入记忆 + ANS 节律联动              ████████████ 100%
情绪驱动（D）    Interoception → Limbic 主动情绪偏移                 ████████████ 100%
路由偏置（E）    Vestibular route_bias 全链路生效                    ████████████ 100%
整体深度集成                                                         ████████████ 100%
```
