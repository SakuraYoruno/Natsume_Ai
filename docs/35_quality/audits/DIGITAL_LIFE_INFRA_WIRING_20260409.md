受众：CODEX
标题：🧬 数字生命架构 · 开发审计记录

# 🧬 数字生命架构 · 开发审计记录

> **文档编号**：DIGITAL_LIFE_INFRA_WIRING_20260409
> **创建日期**：2026-04-09
> **关联方案**：`docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md`
> **状态**：已完成
> **存放路径**：`docs/35_quality/audits/DIGITAL_LIFE_INFRA_WIRING_20260409.md`

---

## 一、本次开发背景

在 2026-04-08 完成多智能体族重构方案文档（v1.3）后，代码已落地了族目录、各族 Agent 实现、事件总线、handoff 协调器等基础设施。

本次（2026-04-09）对照文档进行精确差异分析，生成待完成清单，并完成以下 6 项开发任务。

---

## 二、待完成清单（本次开发前状态）

| # | 项目 | 所属 Phase | 优先级 | 状态 |
|---|---|---|---|---|
| 1 | `http_hooks.py` lifespan 启动时初始化 `InMemoryTribeEventBus` 单例 + `HostTribeRegistry` 单例 | Phase 0 | 🔴 高 | ✅ 已完成 |
| 2 | `ANSAgent` 补全实现：`set_event_loop()` + `emit_from_thread()` + 接管 scheduler 事件发布能力 | Phase 1 | 🔴 高 | ✅ 已完成 |
| 3 | `inner_world_skill` 执行后 `emit INNER_WORLD_TICKED` 事件到 TribeEventBus | Phase 1 | 🔴 高 | ✅ 已完成 |
| 4 | `body_sense_skill` 执行后 `emit BODY_SENSE_UPDATED` 事件到 TribeEventBus | Phase 1 | 🔴 高 | ✅ 已完成 |
| 5 | `CortexAgent` 补充 `emit_conversation_started()` / `emit_conversation_ended()` 并发布 `CONVERSATION_STARTED` / `CONVERSATION_ENDED` / `INTERACTION_RECORDED` | Phase 5 | 🟡 中 | ✅ 已完成 |
| 6 | `dispatch_agent.py` 的 `_record_dispatch_handoff` 补充 `INTERACTION_RECORDED` 事件发布 | Phase 5 | 🟡 中 | ✅ 已完成 |

---

## 三、本次改动详情

### 3.1 `host_brain/app/http_hooks.py`

**改动类型**：新增启动初始化逻辑

**改动位置**：`lifespan` 函数，`start_heartbeat` 之后、`yield` 之前

**改动内容**：
- 新增 `[Tribe 基础设施]` 初始化块
  - 调用 `get_tribe_event_bus()` 确保 `InMemoryTribeEventBus` 单例已创建
  - 调用 `get_host_tribe_registry()` 确保 `HostTribeRegistry` 单例已创建
  - 写入 `new_module_statuses["tribe_infrastructure"]` 供启动报告展示
- 新增 `[ANSAgent]` 事件循环注册块
  - 调用 `get_ans_agent().set_event_loop(asyncio.get_running_loop())`
  - 确保 `ANSAgent.emit_from_thread()` 可从 `threading.Timer` 线程安全发布事件

**影响范围**：仅启动阶段，不影响主链路

---

### 3.2 `host_brain/tribe/ans_agent.py`

**改动类型**：完整重写（从纯壳层升级为可运行实现）

**改动前**：仅有 `descriptor` + `describe()` 方法，无任何事件发布能力

**改动后**：
- `set_event_loop(loop)` — 注册主 asyncio 事件循环
- `emit_from_thread(event_type, payload)` — 线程安全事件发布（`asyncio.run_coroutine_threadsafe`）
- `_async_emit(event_type, payload)` — 在主 asyncio 线程中执行的实际发布协程
- `emit_inner_world_ticked(tick_result)` — 供 `inner_world_skill` 调用
- `emit_body_sense_updated(sense_result)` — 供 `body_sense_skill` 调用
- 新增 `get_ans_agent()` / `set_ans_agent()` 单例管理函数

**关键设计**：
- 所有 `emit_from_thread` 调用均静默降级（不抛出异常），避免影响 Scheduler 主流程
- 事件循环未注册时直接跳过，不阻塞

---

### 3.3 `host_brain/skills/impl/inner_world_skill.py`

**改动类型**：在 `execute_inner_world_tick` 函数末尾新增事件发布

**改动位置**：`execute_inner_world_tick` 函数，`return result` 之前

**改动内容**：
```python
# Phase 1：向 TribeEventBus 发布 INNER_WORLD_TICKED 事件
try:
    from host_brain.tribe.ans_agent import get_ans_agent
    get_ans_agent().emit_inner_world_ticked(tick_result={...})
except Exception:
    pass
```

**影响范围**：仅在 `inner_world_tick` Skill 执行完成后追加，不影响原有逻辑

---

### 3.4 `host_brain/skills/impl/body_sense_skill.py`

**改动类型**：在 `execute_body_sense_tick` 函数末尾新增事件发布

**改动位置**：`execute_body_sense_tick` 函数，`return result` 之前

**改动内容**：
```python
# Phase 1：向 TribeEventBus 发布 BODY_SENSE_UPDATED 事件
try:
    from host_brain.tribe.ans_agent import get_ans_agent
    get_ans_agent().emit_body_sense_updated(sense_result={...})
except Exception:
    pass
```

**影响范围**：仅在 `body_sense_tick` Skill 执行完成后追加，不影响原有逻辑

---

### 3.5 `host_brain/tribe/cortex_agent.py`

**改动类型**：新增两个方法

**新增方法**：
- `emit_conversation_started(user_id, session_id, request_id)` — 发布 `CONVERSATION_STARTED` 事件
- `emit_conversation_ended(user_id, session_id, request_id, emotion_tag, emotion_intensity, summary)` — 发布 `CONVERSATION_ENDED` + `INTERACTION_RECORDED` 事件

**事件目标**：
- `CONVERSATION_STARTED` → `target_agent="limbic_agent"`（触发沉默意愿重置、body_sense 快照）
- `CONVERSATION_ENDED` → `target_agent="limbic_agent"`（触发 session_end 收口）
- `INTERACTION_RECORDED` → 无特定目标（PrefrontalAgent / SocialBrainAgent 均可订阅）

---

### 3.6 `host_brain/app/top_agent.py`

**改动类型**：新增两处调用

**改动 1**：在 `top_agent_sse_generator` 中，governor 通过后、`task_progress_answer` 检查之前，新增：
```python
# Phase 5：对话开始时向 TribeEventBus 发布 CONVERSATION_STARTED 事件
get_cortex_agent().emit_conversation_started(user_id=..., session_id=..., request_id=...)
```

**改动 2**：在 `_async_post_process` 中，`post_process_bond_update` 之后，新增：
```python
# Phase 5：对话结束时向 TribeEventBus 发布 CONVERSATION_ENDED + INTERACTION_RECORDED 事件
get_cortex_agent().emit_conversation_ended(user_id=..., session_id=..., ...)
```

**影响范围**：均在 `try/except` 保护下，降级不影响主链路

---

### 3.7 `host_brain/app/dispatch_agent.py`

**改动类型**：在 `_record_dispatch_handoff` 函数末尾新增事件发布

**改动内容**：
```python
# Phase 5：dispatch 路由决策完成后，向 TribeEventBus 发布 INTERACTION_RECORDED 事件
emit_tribe_event(
    event_type="INTERACTION_RECORDED",
    source_agent="dispatch_agent",
    payload={"request_id": ..., "session_id": ..., "route": ..., "target_id": ..., "reason": ...},
)
```

**影响范围**：在 `try/except` 保护下，降级不影响 dispatch 主流程

---

## 四、事件流完整性验证

本次改动完成后，以下事件链路已经打通：

```
NatsumeScheduler (Timer 线程)
  → inner_world_skill.execute_inner_world_tick()
    → ANSAgent.emit_inner_world_ticked()
      → ANSAgent.emit_from_thread("INNER_WORLD_TICKED", ...)
        → asyncio.run_coroutine_threadsafe → TribeEventBus.publish()

NatsumeScheduler (Timer 线程)
  → body_sense_skill.execute_body_sense_tick()
    → ANSAgent.emit_body_sense_updated()
      → ANSAgent.emit_from_thread("BODY_SENSE_UPDATED", ...)
        → asyncio.run_coroutine_threadsafe → TribeEventBus.publish()

top_agent_sse_generator (asyncio 主线程)
  → CortexAgent.emit_conversation_started()
    → emit_tribe_event("CONVERSATION_STARTED") → TribeEventBus.publish()

_async_post_process (asyncio 主线程)
  → CortexAgent.emit_conversation_ended()
    → emit_tribe_event("CONVERSATION_ENDED") → TribeEventBus.publish()
    → emit_tribe_event("INTERACTION_RECORDED") → TribeEventBus.publish()

dispatch_agent._record_dispatch_handoff (asyncio 主线程)
  → emit_tribe_event("INTERACTION_RECORDED") → TribeEventBus.publish()
```

---

## 五、启动报告新增字段

本次改动后，`/obs/startup-report` 中将新增以下字段：

```json
{
  "tribe_infrastructure": "OK (families=8, agents=10)"
}
```

---

## 六、已知限制与后续迭代建议

| 限制 | 说明 | 建议后续处理 |
|---|---|---|
| `TribeEventBus` 无订阅分发 | 当前 `InMemoryTribeEventBus` 只做存储，没有 `subscribe/dispatch` 机制 | 后续 Phase 3 LimbicAgent 独立时，补充订阅分发循环 |
| `ANSAgent` 未接管 Scheduler 实例 | 当前只是加壳，`NatsumeScheduler` 仍由 `http_hooks.py` 直接管理 | Phase 1 完整版：将 Scheduler 实例注入 ANSAgent |
| `CONVERSATION_STARTED` 无 LimbicAgent 订阅 | 事件已发布，但 LimbicAgent 目前通过 `handle_signal` 直接调用，未走 bus 订阅 | Phase 3 LimbicAgent 独立时，改为 bus 订阅模式 |
| `INTERACTION_RECORDED` 无 PrefrontalAgent 订阅 | 事件已发布，但 PrefrontalAgent 目前通过直接调用触发 | Phase 4 完整版：改为 bus 订阅模式 |

---

## 七、文件变更汇总

| 文件 | 变更类型 | 关键改动 |
|---|---|---|
| `host_brain/app/http_hooks.py` | 修改 | 新增 Tribe 基础设施初始化 + ANSAgent 事件循环注册 |
| `host_brain/tribe/ans_agent.py` | 重写 | 从纯壳层升级为完整 ANSAgent 实现 |
| `host_brain/skills/impl/inner_world_skill.py` | 修改 | 新增 `emit_inner_world_ticked` 调用 |
| `host_brain/skills/impl/body_sense_skill.py` | 修改 | 新增 `emit_body_sense_updated` 调用 |
| `host_brain/tribe/cortex_agent.py` | 修改 | 新增 `emit_conversation_started` / `emit_conversation_ended` |
| `host_brain/app/top_agent.py` | 修改 | 新增对话开始/结束的 Tribe 事件发布 |
| `host_brain/app/dispatch_agent.py` | 修改 | 新增 `INTERACTION_RECORDED` 事件发布 |
