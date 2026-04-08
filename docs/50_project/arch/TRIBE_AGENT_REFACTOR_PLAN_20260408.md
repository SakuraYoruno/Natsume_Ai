# 🧬 Natsume_Ai · 多智能体族重构完整方案

> **文档版本**：v1.0
> **创建日期**：2026-04-08
> **状态**：草案（待评审）
> **存放路径**：`docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md`

---

## 一、现有架构的精确理解

在给出方案前，先澄清几个关键事实：

### 1.1 `GraphState` 的真实规模

`GraphState` 是一个**严格 TypedDict**，已有 **40+ 个字段**，分布在多个迭代中。它是 Graph Runtime 专用的执行状态，**不是**全局共享总线——它只在 `graph_runtime.py` 执行路径中流转，主聊天链路（`top_agent → dispatch_agent → chat_agent`）并不经过它。

### 1.2 `http_hooks.py` 的真实角色

它是 FastAPI 的 `lifespan` 容器，负责：
- 启动时初始化 `StateArbiter`、`WorldCanon`、`StyleCanon`、`EpisodicStore`
- 通过 `_bootstrap_personality_scheduler()` 创建 `NatsumeScheduler` 并注册所有定时任务
- 定时任务通过 `intent_dispatcher → run_proactive_intent_dispatch → SkillLoader` 执行

### 1.3 `NatsumeScheduler` 的真实结构

基于 `threading.Timer` + `threading.Thread`（cron 检查线程），已有完整的：
- 情感状态门控（`mood_state == "critical"` 时跳过低优先级任务）
- sys_mode 门控（`mode_allowlist` 白名单）
- 审计写入（`_log_scheduler_event`）
- 持久化恢复（`_load_persisted_tasks`）

### 1.4 现有 Skill 注册体系

`personality_registry.py` 已经将 `inner_world_tick`、`silence_tick`、`body_sense_tick`、`emergent_growth_weekly` 等全部注册为 `SkillMeta`，通过 `scheduler_allowed=True` 标记允许调度器触发。这套体系已经是 **ANSAgent 的雏形**。

---

## 二、族边界精确划分

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CortexAgent（大脑皮层）                           │
│   top_agent / dispatch_agent / chat_agent / chat_context_builder        │
│   prompt_builder / execution_profile / scene_router / governor          │
│                    [请求驱动 · 主链路 Supervisor]                         │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ 族间协调层（TribeEventBus）
         ┌─────────────────────┼──────────────────────────────────┐
         ▼                     ▼                    ▼              ▼
┌──────────────┐   ┌──────────────────┐   ┌──────────────┐  ┌──────────────┐
│ LimbicAgent  │   │HippocampusAgent  │   │PrefrontalAgent│  │SocialBrain   │
│  边缘系统    │   │   海马体         │   │  前额叶       │  │  Agent       │
│  [事件驱动]  │   │  [请求驱动]      │   │[定时+事件驱动]│  │  [事件驱动]  │
└──────────────┘   └──────────────────┘   └──────────────┘  └──────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         ANSAgent（自主神经系统）                           │
│   NatsumeScheduler / personality_registry / inner_world_skill           │
│   silence_skill / body_sense_skill / emergent_growth_skill              │
│   proactive_voice / inner_activity_agent / activity_monitor             │
│   mode_transition / meta_learning_loop                                  │
│                    [持续后台运行 · 节律驱动]                               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 各族模块归属

| 智能体族 | 人体类比 | 核心模块 | 运行模式 |
|---|---|---|---|
| `CortexAgent` | 大脑皮层 | `top_agent` / `dispatch_agent` / `chat_agent` / `governor` / `airlock` | 请求驱动 |
| `LimbicAgent` | 边缘系统 | `motive_engine` / `emotional_silence` / `inner_world` / `body_sense` / `emotion_timeline` / `metabolism` | 事件驱动 |
| `HippocampusAgent` | 海马体 | `episodic_store` / `recall_fusion` / `agentic_retrieval_loop` / `long_term_distiller` / `embedding` / `reranker` | 请求驱动 |
| `PrefrontalAgent` | 前额叶 | `self_model` / `self_reflection` / `drift_detector` / `trait_drift_guard` / `evolution_orchestrator` / `emergent_growth_engine` | 定时 + 事件驱动 |
| `SocialBrainAgent` | 社交脑 | `bond/` / `bond_aware_style` / `expression_adapter` / `style_distiller` | 事件驱动 |
| `ANSAgent` | 自主神经系统 | `NatsumeScheduler` / `inner_world_skill` / `silence_skill` / `body_sense_skill` / `activity_monitor` / `mode_transition` | 持续后台运行 |

---

## 三、协调机制设计

### 3.1 族间通信的三种模式

| 模式 | 适用场景 | 实现方式 |
|---|---|---|
| **同步 RPC 调用** | 请求驱动族（HippocampusAgent） | 直接函数调用 |
| **asyncio.Queue 事件** | 事件驱动族（LimbicAgent / SocialBrainAgent） | TribeEventBus |
| **定时触发** | 后台族（ANSAgent） | NatsumeScheduler（现有，无需改造） |

### 3.2 TribeEventBus 设计

```python
# host_brain/tribe/event_bus.py

from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from enum import Enum

class TribeEvent(str, Enum):
    # ANSAgent → LimbicAgent
    BODY_SENSE_UPDATED       = "body_sense_updated"
    INNER_WORLD_TICKED       = "inner_world_ticked"
    # CortexAgent → LimbicAgent
    CONVERSATION_STARTED     = "conversation_started"
    CONVERSATION_ENDED       = "conversation_ended"
    # LimbicAgent → CortexAgent
    SILENCE_WILLINGNESS_CHANGED = "silence_willingness_changed"
    EMOTION_STATE_CHANGED    = "emotion_state_changed"
    # CortexAgent → SocialBrainAgent / PrefrontalAgent
    INTERACTION_RECORDED     = "interaction_recorded"
    # SocialBrainAgent → CortexAgent
    BOND_STAGE_CHANGED       = "bond_stage_changed"
    # ANSAgent → PrefrontalAgent
    EVOLUTION_CYCLE_TRIGGERED = "evolution_cycle_triggered"

@dataclass
class TribeEventPayload:
    event_type: TribeEvent
    source_tribe: str
    data: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = 0

EventHandler = Callable[[TribeEventPayload], Awaitable[None]]

class TribeEventBus:
    """轻量级族间事件总线，基于 asyncio.Queue。"""

    _instance: TribeEventBus | None = None

    def __init__(self) -> None:
        self._subscribers: dict[TribeEvent, list[EventHandler]] = {}
        self._queue: asyncio.Queue[TribeEventPayload] = asyncio.Queue(maxsize=256)
        self._running = False

    @classmethod
    def get_instance(cls) -> TribeEventBus:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def subscribe(self, event_type: TribeEvent, handler: EventHandler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    async def emit(self, payload: TribeEventPayload) -> None:
        """非阻塞发布事件（队列满时丢弃，记录警告）。"""
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            import logging
            logging.getLogger(__name__).warning(
                "TribeEventBus queue full, dropping event: %s", payload.event_type
            )

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                for handler in self._subscribers.get(payload.event_type, []):
                    try:
                        await handler(payload)
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).error(
                            "TribeEventBus handler error: %s", exc
                        )
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        self._running = False
```

### 3.3 族 Agent 基类

```python
# host_brain/tribe/base_agent.py

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
from host_brain.tribe.event_bus import TribeEventBus, TribeEvent, TribeEventPayload
from host_brain.core.ids import now_ms

class TribeAgent(ABC):
    """族 Agent 基类，提供事件订阅/发布能力。"""

    tribe_id: str  # 子类必须声明

    def __init__(self) -> None:
        self._bus = TribeEventBus.get_instance()
        self._register_subscriptions()

    @abstractmethod
    def _register_subscriptions(self) -> None:
        """子类在此注册感兴趣的事件。"""
        ...

    async def emit(self, event_type: TribeEvent, data: dict[str, Any]) -> None:
        await self._bus.emit(TribeEventPayload(
            event_type=event_type,
            source_tribe=self.tribe_id,
            data=data,
            ts_ms=now_ms(),
        ))

    def subscribe(self, event_type: TribeEvent, handler) -> None:
        self._bus.subscribe(event_type, handler)
```

---

## 四、各族 Agent 详细设计

### 4.1 ANSAgent（自主神经系统）— Phase 1 首先实施

**职责**：持续后台节律，驱动生命体征。

| 模块 | 现有位置 | 迁移工作量 |
|---|---|---|
| `NatsumeScheduler` | `scheduler/scheduler.py` | 🟢 零改造，加壳即可 |
| `inner_world_skill` | `skills/impl/inner_world_skill.py` | 🟢 零改造 |
| `silence_skill` | `skills/impl/silence_skill.py` | 🟢 零改造 |
| `body_sense_skill` | `skills/impl/body_sense_skill.py` | 🟢 零改造 |
| `emergent_growth_skill` | `skills/impl/emergent_growth_skill.py` | 🟢 零改造 |
| `proactive_voice` | `persona/proactive_voice.py` | 🟡 需提取调度接口 |
| `activity_monitor` | `core/activity_monitor.py` | 🟢 零改造 |
| `mode_transition` | `core/mode_transition.py` | 🟢 零改造 |
| `meta_learning_loop` | `core/meta_learning_loop.py` | 🟢 零改造 |

**线程安全桥接**（Timer 线程 → asyncio 事件循环）：

```python
class ANSAgent(TribeAgent):
    tribe_id = "ans"

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit_from_thread(self, event_type: TribeEvent, data: dict) -> None:
        """从 threading.Timer 线程安全地发布事件到 asyncio 事件循环。"""
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(
            self.emit(event_type, data),
            self._loop,
        )
```

### 4.2 HippocampusAgent（海马体）— Phase 2 实施

**职责**：记忆存取，纯数据服务，对外暴露同步 RPC 接口。

```python
class HippocampusAgent(TribeAgent):
    tribe_id = "hippocampus"

    def _register_subscriptions(self) -> None:
        pass  # 纯数据服务，不订阅事件

    async def recall(self, query: str, user_id: str, top_k: int = 8) -> dict:
        """5路召回融合入口。"""
        ...

    async def store_episodic(self, content: str, metadata: dict) -> str:
        """写入情节记忆。"""
        ...

    async def distill_long_term(self, user_id: str) -> dict:
        """触发长期记忆蒸馏。"""
        ...
```

### 4.3 LimbicAgent（边缘系统）— Phase 3 实施

**职责**：情绪动机，内在状态机。

**⚠️ 关键依赖解耦**：`inner_world.py` 对 `evolution_orchestrator` 的直接调用，需改为 `emit EVOLUTION_CYCLE_TRIGGERED` 事件，由 PrefrontalAgent 订阅处理。

```python
class LimbicAgent(TribeAgent):
    tribe_id = "limbic"

    def _register_subscriptions(self) -> None:
        self.subscribe(TribeEvent.BODY_SENSE_UPDATED, self._on_body_sense_updated)
        self.subscribe(TribeEvent.INNER_WORLD_TICKED, self._on_inner_world_ticked)
        self.subscribe(TribeEvent.CONVERSATION_STARTED, self._on_conversation_started)
        self.subscribe(TribeEvent.CONVERSATION_ENDED, self._on_conversation_ended)

    # 对外暴露的查询接口（同步，供 chat_context_builder 调用）
    def get_emotion_context(self, user_id: str) -> dict: ...
    def get_silence_willingness(self, user_id: str) -> dict: ...
```

### 4.4 PrefrontalAgent（前额叶）— Phase 4 实施

**职责**：自我模型，人格演化，长期反思。

```python
class PrefrontalAgent(TribeAgent):
    tribe_id = "prefrontal"

    def _register_subscriptions(self) -> None:
        self.subscribe(TribeEvent.EVOLUTION_CYCLE_TRIGGERED, self._on_evolution_cycle)
        self.subscribe(TribeEvent.INTERACTION_RECORDED, self._on_interaction_recorded)
```

### 4.5 SocialBrainAgent（社交脑）— Phase 4 实施

**职责**：关系感知，风格适配。

```python
class SocialBrainAgent(TribeAgent):
    tribe_id = "social_brain"

    def _register_subscriptions(self) -> None:
        self.subscribe(TribeEvent.INTERACTION_RECORDED, self._on_interaction_recorded)

    # 对外暴露的查询接口（同步，供 prompt_builder 调用）
    def get_bond_style(self, user_id: str) -> dict: ...
```

### 4.6 CortexAgent（大脑皮层）— Phase 5 最终收口

**职责**：意识与决策，Supervisor，主链路协调。

```python
class CortexAgent(TribeAgent):
    tribe_id = "cortex"

    def _register_subscriptions(self) -> None:
        self.subscribe(TribeEvent.SILENCE_WILLINGNESS_CHANGED, self._on_silence_changed)
        self.subscribe(TribeEvent.EMOTION_STATE_CHANGED, self._on_emotion_changed)
        self.subscribe(TribeEvent.BOND_STAGE_CHANGED, self._on_bond_stage_changed)

    async def on_conversation_start(self, user_id: str, session_id: str) -> None:
        await self.emit(TribeEvent.CONVERSATION_STARTED, {
            "user_id": user_id, "session_id": session_id
        })

    async def on_conversation_end(self, user_id: str, session_id: str, summary: dict) -> None:
        await self.emit(TribeEvent.CONVERSATION_ENDED, {...})
        await self.emit(TribeEvent.INTERACTION_RECORDED, {...})
```

---

## 五、GraphState 扩展策略

**不扩展 `GraphState`**，而是采用独立族状态对象（`TribeStateRegistry`）：

```python
# host_brain/tribe/tribe_state.py

@dataclass
class LimbicTribeState:
    current_emotion_tag: str = "NEUTRAL"
    mood_state: str = "steady"
    silence_willingness: float = 1.0
    loneliness_level: float = 0.0
    fatigue_level: float = 0.0

@dataclass
class PrefrontalTribeState:
    self_model_version: str = ""
    last_reflection_ts: int = 0
    drift_score: float = 0.0

@dataclass
class SocialBrainTribeState:
    bond_stage: str = "stranger"
    style_profile_version: str = ""

class TribeStateRegistry:
    """族状态注册表（单例）。"""
    _instance: TribeStateRegistry | None = None

    def __init__(self) -> None:
        self.ans = ANSTribeState()
        self.limbic = LimbicTribeState()
        self.hippocampus = HippocampusTribeState()
        self.prefrontal = PrefrontalTribeState()
        self.social_brain = SocialBrainTribeState()
```

---

## 六、完整迁移路线图

```
Phase 0（当前 → 2周）：基础设施建设
  ├── 新建 host_brain/tribe/ 目录
  ├── 实现 TribeEventBus（event_bus.py）
  ├── 实现 TribeAgent 基类（base_agent.py）
  ├── 实现 TribeStateRegistry（tribe_state.py）
  └── 在 http_hooks.py lifespan 中初始化 TribeEventBus

Phase 1（2周 → 4周）：ANSAgent 独立
  ├── 创建 ans_agent.py（加壳，不动现有逻辑）
  ├── 在 inner_world_skill 执行后 emit INNER_WORLD_TICKED 事件
  ├── 在 body_sense_skill 执行后 emit BODY_SENSE_UPDATED 事件
  ├── 在 http_hooks.py 中初始化 ANSAgent 单例
  └── 验证：所有 scheduler 任务正常运行，事件正常发布

Phase 2（4周 → 6周）：HippocampusAgent 独立
  ├── 创建 hippocampus_agent.py
  ├── 将 recall_fusion / episodic 的调用入口统一到 HippocampusAgent.recall()
  ├── chat_context_builder.py 改为通过 HippocampusAgent 接口调用
  └── 验证：5路召回结果与改造前一致

Phase 3（6周 → 10周）：LimbicAgent 独立
  ├── 解耦 inner_world.py 对 evolution_orchestrator 的直接调用
  │   └── 改为 emit EVOLUTION_CYCLE_TRIGGERED 事件
  ├── 创建 limbic_agent.py
  ├── 订阅 BODY_SENSE_UPDATED / INNER_WORLD_TICKED / CONVERSATION_STARTED 事件
  ├── 将 emotion_context.py 的组装逻辑迁移到 LimbicAgent.get_emotion_context()
  └── 验证：情感状态、沉默意愿、身体感知正常联动

Phase 4（10周 → 13周）：PrefrontalAgent + SocialBrainAgent 独立
  ├── 创建 prefrontal_agent.py
  │   ├── 订阅 EVOLUTION_CYCLE_TRIGGERED / INTERACTION_RECORDED 事件
  │   └── 将 drift_detector / self_reflection 的调用统一到 PrefrontalAgent
  ├── 创建 social_brain_agent.py
  │   ├── 订阅 INTERACTION_RECORDED 事件
  │   └── 将 bond_aware_style / style_distiller 的调用统一到 SocialBrainAgent
  └── 验证：人格漂移检测、Bond 风格适配正常工作

Phase 5（13周 → 18周）：CortexAgent 收口为纯 Supervisor
  ├── 将 chat_context_builder.py 中的直接模块调用
  │   改为通过各族 Agent 接口调用
  ├── dispatch_agent.py 的 handoff 逻辑改为通过 TribeEventBus 协调
  ├── 最终形态：CortexAgent 只负责意图分类和 handoff 决策
  └── 验证：完整端到端聊天链路正常，延迟无明显增加
```

---

## 七、模块归属决策表

| 模块 | 归属族 | 理由 |
|---|---|---|
| `persona/evolution_orchestrator.py` | PrefrontalAgent | 人格演化编排，属于长期自我模型 |
| `persona/persona_growth.py` | PrefrontalAgent | 人格成长记录 |
| `persona/emergent_growth_engine.py` | PrefrontalAgent | 涌现成长，由 ANSAgent 定时触发 |
| `persona/proactive_voice.py` | ANSAgent | 主动发声由调度器触发，属于后台节律 |
| `persona/body_sense.py` | LimbicAgent | 身体感知影响情绪状态 |
| `persona/expression_adapter.py` | SocialBrainAgent | 表达适配与 Bond 风格相关 |
| `app/reply_form_decider.py` | CortexAgent | 回复形式决策属于主链路 |
| `retrieval/style_distiller.py` | SocialBrainAgent | 风格蒸馏服务于 Bond 感知 |
| `core/metabolism.py` | LimbicAgent | 情感代谢是情绪系统的基础 |
| `core/emotion_timeline.py` | LimbicAgent | 情感时间线 |
| `core/meta_learning_loop.py` | ANSAgent | 元学习循环属于后台节律 |
| `core/activity_monitor.py` | ANSAgent | 活动监控（游戏检测）属于后台感知 |
| `core/mode_transition.py` | ANSAgent | 系统模式切换由后台驱动 |
| `memory/canon_style_store.py` | SocialBrainAgent | 风格 Canon 服务于风格适配 |
| `memory/canon_world_store.py` | HippocampusAgent | 世界观 Canon 属于记忆检索 |
| `retrieval/agentic_retrieval_loop.py` | HippocampusAgent | 主动检索循环 |
| `app/vision/` | CortexAgent | 视觉子系统属于主链路能力 |
| `governor/` | CortexAgent | 治理与安全属于主链路 |
| `airlock/` | CortexAgent | 气闸隔离属于主链路 |

---

## 八、关键风险与缓解措施

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| `inner_world.py` 对 `evolution_orchestrator` 的直接调用 | 🔴 高 | Phase 3 前必须先解耦，改为事件发布 |
| `chat_context_builder.py`（2405行）直接调用多个模块 | 🔴 高 | Phase 2 开始逐步替换为族 Agent 接口，不一次性改 |
| `asyncio.Queue` 与 `threading.Timer` 的线程安全 | 🟡 中 | ANSAgent 的 emit 需要用 `asyncio.run_coroutine_threadsafe` 从 Timer 线程发布到 asyncio 事件循环 |
| 测试覆盖率下降 | 🟡 中 | 每个 Phase 完成后必须运行 `pytest tests/` 全量测试 |
| 延迟增加（事件总线开销） | 🟢 低 | 主链路（CortexAgent → HippocampusAgent）保持同步调用，事件总线只用于非关键路径 |

---

## 九、目录结构

```
host_brain/
└── tribe/                          # 新建目录
    ├── __init__.py
    ├── event_bus.py                # TribeEventBus + TribeEvent 枚举
    ├── base_agent.py               # TribeAgent 基类
    ├── tribe_state.py              # 各族独立状态对象
    ├── ans_agent.py                # ANSAgent（自主神经系统）
    ├── hippocampus_agent.py        # HippocampusAgent（海马体）
    ├── limbic_agent.py             # LimbicAgent（边缘系统）
    ├── prefrontal_agent.py         # PrefrontalAgent（前额叶）
    ├── social_brain_agent.py       # SocialBrainAgent（社交脑）
    ├── cortex_agent.py             # CortexAgent（大脑皮层）
    └── registry.py                 # 族 Agent 注册表（单例管理）
```

---

## 十、核心原则总结

```
✅ 渐进式迁移，每个 Phase 独立可验证
✅ 不扩展 GraphState（避免类型安全问题）
✅ 不改造 NatsumeScheduler（ANSAgent 加壳即可）
✅ 主链路（CortexAgent → HippocampusAgent）保持同步调用
✅ 非关键路径（情绪/人格/关系）走 TribeEventBus 异步事件
✅ 线程安全：asyncio.run_coroutine_threadsafe 桥接 Timer 线程

优先级：
  Phase 0 基础设施 → Phase 1 ANSAgent → Phase 2 HippocampusAgent
  → Phase 3 LimbicAgent（最复杂，需先解耦 inner_world）
  → Phase 4 PrefrontalAgent + SocialBrainAgent
  → Phase 5 CortexAgent 收口（最高风险，放最后）
```
