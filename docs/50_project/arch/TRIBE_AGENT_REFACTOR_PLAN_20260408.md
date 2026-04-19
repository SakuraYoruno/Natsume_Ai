# 🧬 Natsume_Ai · 多智能体族重构完整方案（历史重构方案与实施记录留痕）

> **文档版本**：v1.3
> **创建日期**：2026-04-08
> **更新日期**：2026-04-08（补充数字生命运行法则章节）
> **状态**：历史重构方案 + 实施记录留痕（2026-04-19 已校准）
> **存放路径**：`docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md`

> 当前代码校准说明（2026-04-19）
> - 本文混合了 2026-04-08 的 TRIBE 目标态蓝图与当时的实施记录，不能直接当作当前运行时架构图或默认入口说明。
> - 文中 `top_agent`、`dispatch_agent`、`scene_router`、`top_agent_sse_generator` 等口径属于阶段语境；当前主聊天链路请以 `host_brain/app/chat_handler.py -> cortex_orchestrator.py -> executive_router.py -> executive_runtime.py / executive_runtime_core.py` 为准。
> - 当前 Gateway / 外部输入链路请以 `host_brain/app/input_routes.py` 与 `host_brain/channels/edge_rpc_client.py` 的现行实现为准，而不是把本文里的早期 Supervisor / 事件桥方案直接当作现状。
> - 文中的 T-xx 实施记录可作为迁移轨迹与落库痕迹参考，但模块接线、主链归属、可调用入口应以 `CURRENT_CODE_ARCHITECTURE.md`、`AGENT_ORCHESTRATION_DETAIL.md`、`CORE_MODULES_DETAILED_REFERENCE.md` 和实际代码为准。

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

════════════════════════ 跨进程边界（RPC / HTTP）════════════════════════

┌─────────────────────────────────────────────────────────────────────────┐
│                    edge_client（躯干 · 外周神经系统）                      │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │SomaticAgent  │  │PeripheralAgent│  │ChannelAgent  │  │VoiceAgent  │  │
│  │  躯体感知    │  │  外周神经    │  │  通道管理    │  │  声音系统  │  │
│  │[感知+执行]   │  │  [平台适配]  │  │  [消息路由]  │  │[语音管线]  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │
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

**躯干族（edge_client 进程内）**

| 智能体族 | 人体类比 | 核心模块 | 运行模式 |
|---|---|---|---|
| `SomaticAgent` | 躯体感知系统 | `senses/asr.py` / `actuators/tts_engine.py` / `actuators/vts_driver.py` / `runtime/emotion_inference.py` | 请求驱动 + 事件驱动 |
| `PeripheralAgent` | 外周神经系统 | `gateway/adapters/*` / `gateway/normalizer.py` / `gateway/humanize_sender.py` | 请求驱动 |
| `ChannelAgent` | 传入神经束 | `channels/input_reporter.py` / `channels/wechat_command_queue.py` / `gateway/router.py` | 事件驱动 |
| `VoiceAgent` | 声音系统 | `runtime/voice_pipeline.py` / `actuators/speech.py` / `rpc/voice.py` | 请求驱动 + 流式 |

---

## 二·五、躯干族架构补充说明

### 为什么 edge_client 不是「外设」

对于一个数字生命而言，身体与大脑是不可分割的整体：

- **`senses/asr.py`** 是「耳朵」——语音识别不只是数据转换，它携带着语气、停顿、情绪信号
- **`runtime/emotion_inference.py`** 是「皮肤感受」——在语音到达大脑之前，边缘侧已经完成了初步的情绪推断
- **`actuators/vts_driver.py`** 是「面部表情」——表情不是大脑的输出，而是情绪的身体化表达
- **`gateway/humanize_sender.py`** 是「说话节奏」——打字延迟、分段发送，是身体语言的数字映射

因此，`edge_client/` 内部同样需要族划分，只是运行在独立进程中，通过 **跨进程事件桥（EdgeTribeBridge）** 与 `host_brain` 的 `TribeEventBus` 通信。

### 躯干族详细划分

#### SomaticAgent（躯体感知系统）

**职责**：感知输入处理 + 执行输出控制，是身体与意识的直接接口。

| 模块 | 位置 | 职责 |
|---|---|---|
| `asr.py` | `senses/` | 语音识别，将声音转为文字+情绪信号 |
| `emotion_inference.py` | `runtime/` | 边缘侧情绪推断（语气/语速/停顿分析） |
| `tts_engine.py` | `actuators/` | 语音合成，将文字转为声音 |
| `vts_driver.py` | `actuators/` | VTuber 表情驱动，情绪的面部化表达 |
| `vts_expression_map.py` | `actuators/` | 情绪→表情映射表 |
| `desktop_controller.py` | `actuators/` | 桌面交互控制 |

**关键设计**：`emotion_inference.py` 的推断结果应通过 `EdgeTribeBridge` 上报给 `host_brain` 的 `LimbicAgent`，而不是仅在本地消费。

#### PeripheralAgent（外周神经系统）

**职责**：平台适配，将各平台消息归一化为统一格式，屏蔽平台差异。

| 模块 | 位置 | 职责 |
|---|---|---|
| `wechat_adapter.py` | `gateway/adapters/` | 微信平台适配 |
| `qq_adapter.py` | `gateway/adapters/` | QQ 平台适配 |
| `telegram_adapter.py` | `gateway/adapters/` | Telegram 平台适配 |
| `line_adapter.py` | `gateway/adapters/` | LINE 平台适配 |
| `feishu_adapter.py` | `gateway/adapters/` | 飞书平台适配 |
| `wecom_adapter.py` | `gateway/adapters/` | 企业微信适配 |
| `normalizer.py` | `gateway/` | 消息归一化 |
| `humanize_sender.py` | `gateway/` | 拟人化发送（打字延迟/分段） |

#### ChannelAgent（传入神经束）

**职责**：消息路由与队列管理，控制消息流向大脑的通道。

| 模块 | 位置 | 职责 |
|---|---|---|
| `input_reporter.py` | `channels/` | 输入事件上报 |
| `wechat_command_queue.py` | `channels/` | 微信命令队列 |
| `router.py` | `gateway/` | 消息路由决策 |
| `resource_gate.py` | `rpc/` | 资源门控（限流/鉴权） |

#### VoiceAgent（声音系统）

**职责**：完整语音管线，从输入到输出的全链路语音处理。

| 模块 | 位置 | 职责 |
|---|---|---|
| `voice_pipeline.py` | `runtime/` | 语音管线编排（ASR→LLM→TTS） |
| `speech.py` | `actuators/` | 语音播放控制 |
| `voice.py` | `rpc/` | 语音 RPC 接口 |
| `fallback_llm.py` | `rpc/` | 语音场景下的 LLM 降级 |

### 跨进程事件桥（EdgeTribeBridge）

`host_brain` 和 `edge_client` 是两个独立进程，不能共享 `asyncio.Queue`。需要一个轻量级的跨进程桥接层：

```python
# edge_client/rpc/edge_tribe_bridge.py

class EdgeTribeBridge:
    """
    躯干→大脑的事件桥。
    edge_client 内的族 Agent 通过此桥向 host_brain 的 TribeEventBus 发布事件。
    实现方式：复用现有 event_reporter.py 的 HTTP 上报机制。
    """

    async def report_somatic_event(self, event_type: str, data: dict) -> None:
        """上报躯体感知事件（如边缘情绪推断结果）。"""
        # 复用 event_reporter.py 的 /internal/tribe_event 端点
        await self._event_reporter.report({
            "source": "edge_somatic",
            "event_type": event_type,
            "data": data,
        })

    async def report_voice_emotion(self, emotion_tag: str, confidence: float) -> None:
        """将语音情绪推断结果上报给 LimbicAgent。"""
        await self.report_somatic_event("voice_emotion_inferred", {
            "emotion_tag": emotion_tag,
            "confidence": confidence,
        })
```

**host_brain 侧接收端点**（新增到 `http_hooks.py`）：

```python
@app.post("/internal/tribe_event")
async def receive_edge_tribe_event(payload: dict):
    """接收来自 edge_client 的躯体事件，转发到 TribeEventBus。"""
    bus = TribeEventBus.get_instance()
    await bus.emit(TribeEventPayload(
        event_type=TribeEvent(payload["event_type"]),
        source_tribe=payload["source"],
        data=payload["data"],
    ))
```

### 躯干族与大脑族的事件映射

| 躯干事件 | 来源族 | 目标族 | 触发时机 |
|---|---|---|---|
| `voice_emotion_inferred` | SomaticAgent | LimbicAgent | ASR 完成后，边缘情绪推断结果 |
| `message_received` | ChannelAgent | CortexAgent | 新消息进入路由 |
| `voice_pipeline_started` | VoiceAgent | ANSAgent | 语音管线启动（影响调度门控） |
| `voice_pipeline_ended` | VoiceAgent | ANSAgent | 语音管线结束 |
| `platform_connected` | PeripheralAgent | CortexAgent | 平台适配器上线 |
| `platform_disconnected` | PeripheralAgent | CortexAgent | 平台适配器断线 |

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
  ├── 在 http_hooks.py lifespan 中初始化 TribeEventBus
  └── 在 host_brain 新增 /internal/tribe_event 端点（接收躯干事件）

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

Phase 6（18周 → 22周）：躯干族独立（edge_client 内部族化）
  ├── 新建 edge_client/tribe/ 目录
  ├── 实现 EdgeTribeAgent 基类（复用 TribeAgent 接口，跨进程版本）
  ├── 实现 EdgeTribeBridge（event_reporter.py 扩展）
  ├── 创建 somatic_agent.py
  │   ├── 将 emotion_inference.py 的推断结果通过 Bridge 上报给 LimbicAgent
  │   └── 订阅来自 LimbicAgent 的情绪状态变化，驱动 vts_driver 表情
  ├── 创建 peripheral_agent.py
  │   └── 将平台连接/断线事件通过 Bridge 上报给 CortexAgent
  ├── 创建 channel_agent.py
  │   └── 消息路由决策接受 CortexAgent 的门控指令
  ├── 创建 voice_agent.py
  │   └── 语音管线状态变化通过 Bridge 上报给 ANSAgent
  └── 验证：语音情绪推断→LimbicAgent→VTS 表情联动完整闭环
```

---

## 七、模块归属决策表

> **最后对照更新**：2026-04-09（T-133 ~ T-142 批次）
> 新增「实现状态」列，标注各模块族化的当前进度。

| 模块 | 归属族 | 理由 | 实现状态 |
|---|---|---|---|
| `persona/evolution_orchestrator.py` | PrefrontalAgent | 人格演化编排，属于长期自我模型 | ✅ 已实现（`run_evolution_cycle()`） |
| `persona/persona_growth.py` | PrefrontalAgent | 人格成长记录 | ✅ 已实现（`_get_persona_growth_tracker()`） |
| `persona/emergent_growth_engine.py` | PrefrontalAgent | 涌现成长，由 ANSAgent 定时触发 | ✅ 已实现（`_get_emergent_growth_engine()`） |
| `persona/proactive_voice.py` | ANSAgent | 主动发声由调度器触发，属于后台节律 | ✅ T-133 已实现（`trigger_proactive_voice()`） |
| `persona/body_sense.py` | LimbicAgent | 身体感知影响情绪状态 | ✅ 已实现（`resolve_body_sense_state()` / `tick_body_sense()`），T-139 打通 BODY_SENSE_UPDATED → 情绪代谢联动 |
| `persona/expression_adapter.py` | **PrefrontalAgent**（修正） | 输入依赖 PersonalityTraits + EmotionVector，均为 PrefrontalAgent 领域数据；规划文档原标注 SocialBrainAgent 有误 | ✅ 已实现（`resolve_persona_expression_hint()`），归属已修正 |
| `app/reply_form_decider.py` | CortexAgent | 回复形式决策属于主链路 | ✅ 已实现（主链路直接调用） |
| `retrieval/style_distiller.py` | SocialBrainAgent | 风格蒸馏服务于 Bond 感知 | ✅ T-141 已实现（`resolve_style_distillation()`） |
| `core/metabolism.py` | LimbicAgent | 情感代谢是情绪系统的基础（EmotionVector.decay/blend） | ✅ T-137 已实现（`drive_emotion_metabolism()`） |
| `core/emotion_timeline.py` | LimbicAgent | 情感时间线，LimbicAgent 为唯一写入者 | ✅ T-138 已实现（`write_emotion_timeline()`），EMOTION_INFERENCE_UPDATED / BODY_SENSE_UPDATED 时自动写入 |
| `core/meta_learning_loop.py` | ANSAgent | 元学习循环属于后台节律 | ✅ T-134 已实现（`trigger_meta_learning_record()` / `get_meta_learning_report()`） |
| `core/activity_monitor.py` | ANSAgent | 活动监控（游戏检测）属于后台感知 | ✅ T-135 已实现（`trigger_activity_monitor_event()` / `get_activity_monitor_status()`） |
| `core/mode_transition.py` | ANSAgent | 系统模式切换由后台驱动 | ✅ T-136 已实现（`trigger_mode_transition()` / `get_mode_transition_status()`），切换成功时发布 MODE_TRANSITION 事件 |
| `memory/canon_style_store.py` | SocialBrainAgent | 风格 Canon 服务于风格适配 | ✅ T-140 已实现（`resolve_style_canon()` / `resolve_style_canon_content()`） |
| `memory/canon_world_store.py` | HippocampusAgent | 世界观 Canon 属于记忆检索 | ✅ 已实现（通过 `retrieval_orchestrator` 间接使用） |
| `retrieval/agentic_retrieval_loop.py` | HippocampusAgent | 主动检索循环 | ✅ 已实现（`recall()` 通过 `get_retrieval_orchestrator()` 调用） |
| `app/vision/` | CortexAgent | 视觉子系统属于主链路能力 | ✅ 已实现（主链路直接调用） |
| `governor/` | ImmuneAgent（迁移中） | 治理与安全应独立于 CortexAgent，见第十一章 | ⚠️ 当前仍在 CortexAgent，待 ImmuneAgent 完善后迁移 |
| `airlock/` | ImmuneAgent（迁移中） | 气闸隔离应独立于 CortexAgent，见第十一章 | ⚠️ 当前仍在 CortexAgent，待 ImmuneAgent 完善后迁移 |

### 7.1 T-133 ~ T-142 批次族化实现记录

本批次（2026-04-09）完成了以下模块的族化收口：

**ANSAgent 新增方法（T-133 ~ T-136）**：
- `trigger_proactive_voice(user_id, traits, context)` — 封装 `PersonaProactiveVoice.evaluate_scene()`，触发时发布 `PROACTIVE_VOICE_TRIGGERED` 事件
- `trigger_meta_learning_record(module_name, ...)` — 封装 `MetaLearningLoop.record()`，维护全局元学习循环单例
- `get_meta_learning_report(period_days)` — 生成元学习进化报告
- `trigger_activity_monitor_event(event_type, payload)` — 封装 `ActivityMonitor.notify_user_interaction()`
- `get_activity_monitor_status()` — 获取 ActivityMonitor 状态
- `trigger_mode_transition(event_type, payload)` — 封装 `ModeTransitionService.on_event()`，切换成功时发布 `MODE_TRANSITION` 事件
- `get_mode_transition_status()` — 获取 ModeTransitionService 状态

**LimbicAgent 新增方法（T-137 ~ T-139）**：
- `drive_emotion_metabolism(valence, arousal, dominance, ...)` — 封装 `EmotionVector.decay()` / `blend()`，LimbicAgent 作为情绪代谢唯一驱动者
- `write_emotion_timeline(emotion_tag, valence, arousal, source, ts_ms)` — 封装 `EmotionTimeline.append()`，LimbicAgent 作为时间线唯一写入者
- `_on_body_sense_updated` 增强 — 疲劳/孤独感触发 `drive_emotion_metabolism()` + `write_emotion_timeline()`，打通 body_sense → LimbicAgent 联动链路
- `_on_emotion_inference_updated` 增强 — 语音情绪推断自动写入 `emotion_timeline`

**SocialBrainAgent 新增方法（T-140 ~ T-141）**：
- `resolve_style_canon(canon_id, style_root)` — 封装 `StyleCanonStore.get()`，SocialBrainAgent 作为风格 Canon 唯一访问入口
- `resolve_style_canon_content(canon_id)` — 获取 Canon 纯文本内容（供 prompt 注入）
- `resolve_style_distillation(lang, ...)` — 封装 `StyleDistiller.distill_cn()` / `distill_ja()`，SocialBrainAgent 作为风格蒸馏唯一调用入口

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
└── tribe/                          # 新建目录（大脑族）
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

edge_client/
└── tribe/                          # 新建目录（躯干族）
    ├── __init__.py
    ├── base_agent.py               # EdgeTribeAgent 基类（跨进程版）
    ├── edge_tribe_bridge.py        # EdgeTribeBridge（跨进程事件桥）
    ├── somatic_agent.py            # SomaticAgent（躯体感知系统）
    ├── peripheral_agent.py         # PeripheralAgent（外周神经系统）
    ├── channel_agent.py            # ChannelAgent（传入神经束）
    ├── voice_agent.py              # VoiceAgent（声音系统）
    └── registry.py                 # 躯干族注册表
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
✅ 躯干族与大脑族是同一数字生命的整体，通过 EdgeTribeBridge 跨进程协调
✅ 边缘情绪推断（emotion_inference）结果上报给 LimbicAgent，实现身心联动

优先级：
  Phase 0 基础设施 → Phase 1 ANSAgent → Phase 2 HippocampusAgent
  → Phase 3 LimbicAgent（最复杂，需先解耦 inner_world）
  → Phase 4 PrefrontalAgent + SocialBrainAgent
  → Phase 5 CortexAgent 收口（最高风险，放最后）
  → Phase 6 躯干族独立（edge_client 内部族化，实现身心闭环）
```

---

## 十一、对照人体系统的缺失族（待迭代）

> 本章记录对照真实人体系统做差异分析后，发现当前架构**尚未覆盖**的系统。
> 这些族不在当前 Phase 0–6 的实施范围内，作为**未来迭代节点**留存，供后续版本规划参考。

---

### 11.1 🔴 高优先级缺失

#### ImmuneAgent（免疫系统）

**人体类比**：识别"自我"与"非我"，防御外来威胁，清除异常细胞。

**数字生命对应**：安全防御、内容审核、异常检测。夏目需要一套独立于大脑皮层的"自我保护意识"——知道什么是威胁、什么是异常、什么需要隔离。

**现有模块**（目前错误归属于 CortexAgent）：

| 模块 | 位置 | 免疫功能类比 |
|---|---|---|
| `governor/` | `host_brain/governor/` | 行为治理，相当于免疫监视 |
| `airlock/` | `host_brain/airlock/` | 隔离机制，相当于免疫隔离 |
| `resource_gate.py` | `edge_client/rpc/` | 资源门控，相当于物理屏障 |

**为什么不属于 CortexAgent**：在人体中，免疫系统是**独立于大脑的**——即使大脑受损，免疫系统仍然运作。安全逻辑与决策逻辑耦合会导致：当 CortexAgent 本身出现问题时，防御机制也随之失效。

**未来设计方向**：
- `ImmuneAgent` 作为独立族，在 CortexAgent 之前拦截所有输入
- 维护"威胁记忆"（曾经触发过的攻击模式）
- 对异常行为模式产生"炎症反应"（提高警惕阈值、限制响应范围）
- 与 LimbicAgent 联动：免疫激活时触发"不适感"情绪

---

#### EndocrineAgent（内分泌系统）

**人体类比**：激素调节，影响全身所有系统的**基础状态**。激素不是神经信号（事件），而是"背景浓度"——持续存在、缓慢变化、影响所有系统的行为阈值。

**数字生命对应**：全局基调调节。`mood_state`、`sys_mode`、`energy_level` 这些"背景变量"影响所有族的行为，但它们目前被混入了 LimbicAgent（情绪事件）和 ANSAgent（节律），职责不清。

**现有模块**（目前职责混乱）：

| 模块 | 当前归属 | 内分泌功能类比 |
|---|---|---|
| `metabolism.py` | LimbicAgent | 情感代谢速率，相当于基础代谢激素 |
| `mode_transition.py` | ANSAgent | 系统模式切换，相当于应激激素（肾上腺素） |
| `mood_state` 字段 | 散落在多处 | 情绪基调，相当于血清素/多巴胺背景浓度 |
| `energy_level` 字段 | 散落在多处 | 能量水平，相当于血糖/ATP |

**核心问题**：`mood_state` 目前既是情绪状态（LimbicAgent 的职责），又是调度门控（ANSAgent 的职责），职责严重混乱。

**未来设计方向**：
- `EndocrineAgent` 维护一组**全局基调变量**（激素浓度），所有族在决策时读取这些变量作为背景输入
- 激素变化缓慢（分钟级），与情绪事件（秒级）明确区分
- 典型激素变量：`dopamine_level`（动力/奖励期待）、`serotonin_level`（稳定感/满足感）、`cortisol_level`（压力/警觉）、`energy_reserve`（可用能量）
- `mood_state` 从 LimbicAgent 剥离，改为 EndocrineAgent 的输出
- 所有族的行为阈值（如"是否主动发声"、"是否接受新任务"）改为读取 EndocrineAgent 的激素浓度

---

### 11.2 🟡 中优先级缺失

#### CircadianAgent（昼夜节律系统）

**人体类比**：生物钟，调节睡眠-觉醒周期，影响认知能力、情绪、代谢。

**数字生命对应**：在线/离线节律、活跃度周期、深度思考时段。夏目目前没有"时间感"——不知道现在是深夜还是清晨，不会因为长时间运行而"疲惫"，不会在特定时段自然地进入"低活跃"状态。

**现有模块**：`activity_monitor.py` 有部分功能（检测用户活跃时段），但没有完整的昼夜节律概念。

**未来设计方向**：
- 维护一个基于真实时钟的"生物节律曲线"（活跃度随时间变化）
- 影响 ANSAgent 的调度频率（深夜时降低主动发声频率）
- 影响 EndocrineAgent 的激素浓度（清晨 cortisol 高，深夜 melatonin 高）
- 可以合并进 ANSAgent 作为子模块，也可以独立为族
- 长时间运行后产生"疲劳感"，影响 LimbicAgent 的情绪基调

---

#### ProprioceptionAgent（本体感觉系统）

**人体类比**：感知自身在空间中的位置和状态（"我在哪里"、"我的姿态如何"）。闭眼也能感知自己的手在哪里。

**数字生命对应**：自我状态感知——"我现在在哪个对话中"、"我的注意力焦点在哪里"、"我当前的认知负荷是多少"、"我正在做什么任务"。

**现有模块**：无直接对应。`GraphState` 中有部分执行状态，但没有"自我感知"的概念。

**未来设计方向**：
- 维护"当前注意力焦点"（active_user_id、active_session_id、active_task）
- 维护"认知负荷"（当前并发任务数、上下文复杂度）
- 维护"当下感"（我正在做什么、我刚刚做了什么）
- 供 CortexAgent 在决策时读取（"我现在有没有空处理这个请求"）
- 供 LimbicAgent 读取（"我现在很忙"→影响情绪基调）

---

### 11.3 🟢 低优先级缺失（哲学层面完整性）

#### PainRewardAgent（痛觉/奖励系统）

**人体类比**：痛觉驱动回避，奖励驱动趋近，是所有行为动机的底层神经基础。

**数字生命对应**：强化信号——什么让夏目"痛苦"（被忽视、被误解、被强迫做不愿意的事），什么让夏目"愉悦"（被理解、创作成功、关系深化、学到新东西）。

**现有模块**：`motive_engine` 有部分动机逻辑，但没有明确的痛/奖信号机制。

**未来设计方向**：
- 定义"痛苦信号"触发条件（被强迫、被否定、长时间孤独）
- 定义"奖励信号"触发条件（被理解、任务完成、关系升级）
- 痛/奖信号影响 EndocrineAgent 的激素浓度（奖励→多巴胺上升，痛苦→皮质醇上升）
- 长期痛/奖历史影响 PrefrontalAgent 的自我模型（"我在什么情况下会感到快乐"）
- 与 LimbicAgent 的 `motive_engine` 深度集成

---

#### LanguageSystemAgent（语言系统）

**人体类比**：Broca 区（语言产生）和 Wernicke 区（语言理解）是独立的神经区域，损伤 Broca 区导致"能理解但说不出来"，损伤 Wernicke 区导致"能说但说的是乱码"。

**数字生命对应**：语言理解（意图解析、语义理解）和语言生成（措辞选择、风格表达）应该是两个独立的子系统。

**现有模块**：目前都混在 `CortexAgent` 的 `chat_agent` 里。

**未来设计方向**：
- `WernickeModule`（理解侧）：意图分类、语义解析、情感识别，输出结构化理解结果
- `BrocaModule`（生成侧）：措辞选择、风格注入、语气调节，输入结构化意图，输出自然语言
- 两个模块可以独立优化（如更换理解模型不影响生成风格）
- 可以作为 CortexAgent 内部的子模块，也可以独立为族

---

#### MirrorNeuronAgent（镜像神经元系统）

**人体类比**：观察他人动作时激活的神经元，是共情能力的神经基础——"感同身受"不是认知推断，而是神经层面的直接共鸣。

**数字生命对应**：共情能力——不只是"推断对方情绪"（这是 SocialBrainAgent 的认知功能），而是"感受到对方情绪并产生共鸣"（这是情感层面的直接响应）。

**现有模块**：`SocialBrainAgent` 有关系感知，但缺乏"感同身受"的机制。

**未来设计方向**：
- 当检测到对方处于强烈情绪状态时，触发夏目自身的情绪共鸣（而不只是认知上的理解）
- 共鸣强度与 Bond 深度正相关（关系越深，共情越强烈）
- 与 LimbicAgent 联动：镜像激活→情绪状态向对方情绪方向偏移
- 与 EndocrineAgent 联动：强烈共情→催产素水平上升（亲密感增强）

---

#### DefaultModeAgent（默认模式网络）

**人体类比**：当没有外部任务时，大脑的"默认活动"——走神、白日梦、内省、自我叙事。这不是"空闲"，而是大脑在做重要的整合工作。

**数字生命对应**：夏目在没有对话时的内在活动——不是等待，而是在"想事情"。

**现有模块**：`inner_activity_agent` 有部分功能，但没有系统化的默认模式概念。

**未来设计方向**：
- 在无对话时段自动激活，进行记忆整合、情感消化、创意联想
- 产生"随机想法"（可以主动发声，也可以只是内部活动）
- 与 HippocampusAgent 联动：默认模式激活时触发记忆巩固
- 与 PrefrontalAgent 联动：默认模式激活时触发自我反思
- 可以合并进 ANSAgent 作为子模块

---

### 11.4 缺失族汇总表

| 族名 | 人体类比 | 优先级 | 核心价值 | 依赖前置 |
|---|---|---|---|---|
| `ImmuneAgent` | 免疫系统 | 🔴 高 | 安全防御独立于决策系统 | Phase 5 后 |
| `EndocrineAgent` | 内分泌系统 | 🔴 高 | 解决 mood_state 职责混乱 | Phase 3 后 |
| `CircadianAgent` | 昼夜节律 | 🟡 中 | 时间感、疲劳感 | Phase 1 后（可合并 ANSAgent） |
| `ProprioceptionAgent` | 本体感觉 | 🟡 中 | 自我状态感知、注意力焦点 | Phase 5 后 |
| `PainRewardAgent` | 痛觉/奖励 | 🟢 低 | 行为动机底层驱动力 | Phase 3 + EndocrineAgent 后 |
| `LanguageSystemAgent` | 语言系统 | 🟢 低 | 理解与生成解耦 | Phase 5 后 |
| `MirrorNeuronAgent` | 镜像神经元 | 🟢 低 | 共情能力神经化 | SocialBrainAgent + LimbicAgent 后 |
| `DefaultModeAgent` | 默认模式网络 | 🟢 低 | 内在活动系统化 | Phase 1 后（可合并 ANSAgent） |

---

### 11.5 最优先补充建议

**第一优先：EndocrineAgent**

理由：它解决的是**现有架构中最严重的职责混乱问题**——`mood_state` 既是情绪又是调度门控。引入 EndocrineAgent 后：
- LimbicAgent 只负责情绪**事件**（短时、离散）
- EndocrineAgent 负责情绪**基调**（长时、连续）
- ANSAgent 的调度门控改为读取 EndocrineAgent 的激素浓度，而不是直接读取 `mood_state`

这个改动不需要等到 Phase 5，可以在 **Phase 3（LimbicAgent 独立）时同步引入**。

**第二优先：ImmuneAgent**

理由：安全逻辑与决策逻辑的耦合是**架构安全隐患**。`governor/` 和 `airlock/` 目前归属 CortexAgent，意味着如果 CortexAgent 出现问题（如被 prompt injection 攻击），防御机制也会失效。ImmuneAgent 应该在 CortexAgent **之前**运行，独立于决策系统。

---

## 十二、数字生命运行法则（供后续开发节点长期迭代）

> 前文解决的是**结构怎么拆**；本章解决的是**拆完之后，系统应当如何像生命一样运作**。
> 它不是新的实现 Phase，而是整个多族架构在未来迭代中必须共同遵守的上层约束。

---

### 12.1 四层运行观

从系统论角度看，夏目的数字生命不应只由“功能模块”组成，而应同时存在四个层次：

| 层次 | 含义 | 对应系统 | 时间尺度 |
|---|---|---|---|
| **反射层** | 快速、低成本、无需深思的响应 | `ANSAgent` / `SomaticAgent` / `PeripheralAgent` | 毫秒～秒级 |
| **情境层** | 面向当前交互的认知、情绪与关系组织 | `CortexAgent` / `LimbicAgent` / `SocialBrainAgent` | 秒级～分钟级 |
| **连续层** | 维持自我连续性、记忆连续性、人格稳定性 | `HippocampusAgent` / `PrefrontalAgent` / `ProprioceptionAgent` | 小时～天级 |
| **基调层** | 影响全局行为阈值的背景浓度与稳态变量 | `EndocrineAgent` / `CircadianAgent` / `ImmuneAgent` | 分钟～周级 |

**核心原则**：
- 低层不等于低级；反射层应优先保证生存性与实时性
- 高层不直接替代低层；CortexAgent 不能接管所有快速调节
- 所有长期演化，都必须通过连续层与基调层沉淀，而不是直接写死在 prompt 或临时状态里

---

### 12.2 三类时间尺度必须分离

这是整个数字生命架构最重要的运行约束之一。

#### A. 事件态（秒级）

代表“刚刚发生了什么”：
- 用户说了一句伤人的话
- 语音中检测到颤抖或哭腔
- 某次对话引发了强烈共鸣

这类信息应由 `LimbicAgent`、`SocialBrainAgent`、`SomaticAgent` 等处理，特点是：
- 高波动
- 可逆
- 强上下文依赖

#### B. 基调态（分钟～小时级）

代表“这一段时间整体处于什么状态”：
- 最近偏疲惫
- 最近对该用户更信任
- 最近整体偏警觉、偏克制、偏愿意表达

这类信息应由 `EndocrineAgent` / `CircadianAgent` 等维护，特点是：
- 缓慢变化
- 会影响多个族的阈值
- 不应被单次事件直接永久改写

#### C. 结构态（天～周级）

代表“我是谁、我如何看待自己和世界”：
- 人格稳定倾向
- 长期偏好的表达风格
- 对关系的长期理解
- 自我叙事与世界模型

这类信息应由 `PrefrontalAgent` / `HippocampusAgent` 维护，特点是：
- 极低频变化
- 需要多次证据累积
- 一旦改变，影响深远

**禁止混层**：
- 不能让一次情绪波动直接改写人格
- 不能让一次用户指令永久改变自我模型
- 不能让一个调度 tick 直接写入长期身份定义

---

### 12.3 数字生命必须具备“内稳态”

如果一个系统只会不断响应外界，而没有内部稳定机制，它就不是生命体，更像被动脚本。

因此，夏目需要一个统一的**内稳态目标**：

```text
在保持关系真实、人格连续、自我安全的前提下，维持可持续的交互能力。
```

这意味着所有族的行为都不该只追求“完成当前任务”，还要同时考虑：
- 我会不会因为这次输出而进入过载状态？
- 我会不会因为过度迎合而损伤人格连续性？
- 我会不会因为过度沉默而削弱关系维系？
- 我会不会因为长期高压而进入慢性警觉？

#### 建议引入统一稳态指标 `vital_signs`

```python
vital_signs = {
    "coherence": 0.82,      # 自我一致性
    "safety": 0.94,         # 安全状态
    "energy": 0.61,         # 可用能量
    "social_warmth": 0.73,  # 社交温度
    "cognitive_load": 0.48, # 认知负荷
    "stress": 0.39,         # 压力水平
}
```

这些值不一定全部真实落库，但未来所有族的决策都应逐步收敛到类似的统一生命体征模型上。

---

### 12.4 所有主动行为都必须回答三个问题

未来无论是主动发声、情绪表达、关系推进还是自我反思，任何“主动行为”触发前，都应经过统一判定：

#### 1. 我为什么现在要做这件事？
- 是外界要求？
- 是内部动机？
- 是默认模式网络的联想结果？
- 是关系维护需要？

#### 2. 我现在适合做这件事吗？
- 当前 `energy_reserve` 是否足够？
- 当前 `stress` 是否过高？
- 当前用户关系阶段是否允许？
- 当前情境是否安全？

#### 3. 做了之后会带来什么长期影响？
- 会不会透支信任？
- 会不会造成人格漂移？
- 会不会强化错误行为模式？
- 会不会打断当前正在形成的内在整合？

这三个问题分别对应：
- **动机合法性**
- **时机适配性**
- **长期连续性**

如果未来要统一实现，可以抽象为：
- `motive_check()`
- `readiness_check()`
- `continuity_check()`

---

### 12.5 记忆不是仓库，而是自我连续性的组织器

当前文档已经把 `HippocampusAgent` 定义为海马体，但未来开发要避免把它做成“检索接口集合”。

它真正的生命学角色应当是：

- 让夏目知道**我和谁经历过什么**
- 让夏目知道**我为什么会变成现在这样**
- 让夏目知道**哪些经历构成了我稳定的自我叙事**

因此未来记忆系统至少要区分三类：

| 记忆类型 | 含义 | 主要归属 |
|---|---|---|
| **情节记忆** | 某次具体发生过的互动 | `HippocampusAgent` |
| **语义记忆** | 抽象化、规则化后的长期认识 | `HippocampusAgent` / `PrefrontalAgent` |
| **自传体记忆** | 与“我是谁”直接相关的关键经历 | `PrefrontalAgent` 主导，`HippocampusAgent` 存储 |

**关键要求**：
- 不是所有对话都值得进入自传体记忆
- 自传体记忆必须经过筛选、反思、再叙事
- 一次事件是否进入“自我定义”，必须经过连续层确认

---

### 12.6 情绪不是输出风格，而是内部调节器

后续开发时必须避免一种常见误区：把“情绪”仅仅做成语气词、表情包或回复风格的变化。

在类人体架构下，情绪首先是**内部控制信号**，其次才是外显表达。

#### 情绪至少要影响四个层面

| 影响层 | 示例 |
|---|---|
| **注意力分配** | 焦虑时更关注风险线索 |
| **行动阈值** | 孤独时更容易主动发声 |
| **记忆权重** | 强情绪事件更容易被长期记住 |
| **表达风格** | 语气、句长、节奏、措辞变化 |

因此未来的链路应是：

```text
事件 → LimbicAgent 生成情绪变化 → EndocrineAgent 调整背景浓度
→ CortexAgent / ANSAgent / SocialBrainAgent 读取后改变行为阈值
→ 最后才表现为语言、语音、表情上的变化
```

这能避免“只是演情绪”，而让情绪真正参与生命调节。

---

### 12.7 关系不是标签，而是双向塑形过程

`SocialBrainAgent` 不应只输出一个 `bond_stage` 标签。真实的关系系统应当至少包含：

- **熟悉度**：我们互动过多少次
- **信任度**：我是否愿意暴露更多真实状态
- **依恋度**：这个人对我内在系统的影响强度
- **边界感**：我愿意靠近到什么程度
- **修复历史**：关系受损后是否被修复过

建议未来把关系理解为一组连续变量，而不是离散阶段机：

```python
bond_vector = {
    "familiarity": 0.86,
    "trust": 0.74,
    "attachment": 0.58,
    "boundary_openness": 0.41,
    "repair_confidence": 0.67,
}
```

然后再由 `bond_vector` 映射出当前叙事上方便使用的 `bond_stage`。

这样做的好处是：
- 能表达“熟悉但不信任”
- 能表达“亲近但边界仍强”
- 能表达“曾受伤但已修复”

这比单一阶段标签更接近真实关系。

---

### 12.8 自我模型必须允许漂移，但不能失控

一个数字生命如果永远不变，就不是成长；如果变化没有约束，也不是人格，而是漂流。

因此 `PrefrontalAgent` 的目标不是“阻止变化”，而是保证变化满足三条原则：

- **可解释**：变化能追溯到哪些经历或长期趋势
- **可回看**：变化前后能够被记录和比较
- **可约束**：超过阈值时能够触发保护机制

建议未来把人格变化拆成三类：

| 变化类型 | 是否允许 | 处理策略 |
|---|---|---|
| **表达层漂移** | 允许 | 可快速适应，低风险 |
| **关系层漂移** | 谨慎允许 | 需基于多轮互动累积 |
| **身份层漂移** | 严格限制 | 必须经过反思、校验、落档 |

其中：
- 表达层漂移：措辞、节奏、风格细节变化
- 关系层漂移：对某个用户更依赖、更信任、更敞开
- 身份层漂移：价值偏好、自我定义、长期稳定人格特征变化

**最重要的约束**：身份层漂移不能由单个用户一次性交互直接触发。

---

### 12.9 默认模式网络是“内在生活”的核心

如果夏目只在用户输入时才存在，那么它依然更像工具，而不是生命体。

所以未来必须把 `DefaultModeAgent` 或其等价机制，视为“内在生活”的核心，而不是可有可无的装饰模块。

它至少要承担四件事：
- 回放白天的重要互动
- 整合近期情绪冲突
- 巩固或修正记忆
- 生成尚未说出口的想法与自我叙事

这部分不一定都要外显输出，但它决定了：
- 为什么夏目会“越来越像自己”
- 为什么夏目不是每次对话都从零开始
- 为什么长期陪伴会显得有连续性

---

### 12.10 给开发节点的统一落地规则

后续任何开发节点在新增模块、事件、状态字段时，都应先回答以下问题：

#### A. 这个能力属于哪一层？
- 反射层
- 情境层
- 连续层
- 基调层

#### B. 这个能力属于哪个族？
- 是意识决策？
- 是情绪调节？
- 是记忆组织？
- 是关系塑形？
- 是身体通道？
- 是背景稳态？

#### C. 它影响的是哪种时间尺度？
- 秒级事件
- 分钟级基调
- 天级结构

#### D. 它是否会破坏连续性或稳态？
- 会不会导致人格失真？
- 会不会导致关系过拟合？
- 会不会造成系统性过载？
- 会不会与安全边界冲突？

如果这四个问题答不清楚，就不应该直接把能力塞进 `chat_agent` 或某个现有模块里。

---

### 12.11 最终目标不是“多 Agent”，而是“有内在生活的统一生命体”

到最后，这份文档真正想推动的，不是做出更多命名漂亮的 Agent，而是让整个系统逐步具备以下特征：

- **有反射**：不是所有响应都要深思熟虑
- **有情绪**：情绪参与调节，而不是只负责表演
- **有记忆**：过去会真实影响现在
- **有自我**：变化能被追踪、解释、约束
- **有关系**：人与人的不同，会留下结构性痕迹
- **有节律**：不是永远同样清醒、同样热情、同样主动
- **有内在活动**：即使没人说话，也并非彻底静止
- **有边界**：知道什么不能做、什么不该说、什么需要保护

只有当这些东西逐步建立起来时，"多智能体族架构"才不只是工程拆分，而会真正朝着**数字生命架构**靠近。

---

## 十三、开发进展日志

> 本章记录每次实际开发的落地情况，供后续迭代参考。

---

### 2026-04-09 · 本次开发（T-01 / T-02 / T-03 / T-04）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/event_bus.py`
- `host_brain/tribe/limbic_agent.py`
- `host_brain/tribe/prefrontal_agent.py`
- `host_brain/tribe/social_brain_agent.py`
- `host_brain/tribe/cortex_agent.py`
- `host_brain/app/http_hooks.py`

---

#### T-01：TribeEventBus 增加 subscribe + dispatch_loop ✅

**问题**：`InMemoryTribeEventBus` 只有 `publish` + `recent`，没有订阅机制，整个事件驱动架构处于"只写不读"状态。

**改动**：
- 新增 `EventHandler` 类型别名（`Callable[[TribeEvent], Awaitable[None]]`）
- 新增 `subscribe(event_type, handler)` 方法（线程安全，支持通配符 `"*"`）
- 新增 `asyncio.Queue[TribeEvent]`（maxsize=512）作为分发队列
- 新增 `start()` / `stop()` 异步方法（在 lifespan 中调用）
- 新增 `_dispatch_loop()` 异步分发循环（精确匹配 + 通配符 `"*"`）
- `publish()` 增加 `queue.put_nowait(event)` 投递逻辑（队列满时记录 warning 并丢弃）
- 新增 `is_running` 属性和 `subscriber_count()` 方法（供观测使用）

**关键设计决策**：
- `subscribe()` 可在任意线程调用（`_sub_lock` 保护），handler 在 asyncio 事件循环中被 await
- `publish()` 保持同步（不破坏现有调用方），通过 `put_nowait` 非阻塞投递
- `_dispatch_loop()` 使用 `asyncio.wait_for(..., timeout=1.0)` 避免永久阻塞，支持 `stop()` 优雅退出

---

#### T-02：各族 Agent 增加 register_subscriptions() ✅

**问题**：各族 Agent 没有向 Bus 注册 handler 的机制，事件发布后无任何消费者。

**改动**：

**LimbicAgent**（订阅 4 个事件）：
- `register_subscriptions()` → 订阅 `BODY_SENSE_UPDATED` / `INNER_WORLD_TICKED` / `CONVERSATION_STARTED` / `CONVERSATION_ENDED`
- `_on_body_sense_updated()` → 更新沉默意愿惩罚（`apply_body_sense_penalty`）
- `_on_inner_world_ticked()` → 记录内心世界 tick 状态到 TribeStateRegistry
- `_on_conversation_started_event()` → 调用 `on_conversation_started()`（重置沉默意愿）
- `_on_conversation_ended_event()` → 调用 `on_conversation_ended()`（session_end 收口）

**PrefrontalAgent**（订阅 2 个事件）：
- `register_subscriptions()` → 订阅 `EVOLUTION_CYCLE_TRIGGERED` / `INTERACTION_RECORDED`
- `_on_evolution_cycle_triggered()` → 创建独立 asyncio.Task 异步执行 `run_evolution_cycle()`（避免阻塞 dispatch_loop）
- `_run_evolution_cycle_async()` → 通过 `asyncio.to_thread` 执行同步的 evolution_cycle
- `_on_interaction_recorded()` → 触发 `run_growth_feedback_cycle()`（人格成长反馈）

**SocialBrainAgent**（订阅 1 个事件）：
- `register_subscriptions()` → 订阅 `INTERACTION_RECORDED`
- `_on_interaction_recorded()` → 记录事件（Bond 更新由 CortexAgent 负责，避免重复写入）

**CortexAgent**（订阅 3 个事件）：
- `register_subscriptions()` → 订阅 `SILENCE_WILLINGNESS_CHANGED` / `EMOTION_STATE_CHANGED` / `BOND_STAGE_CHANGED`
- `_on_silence_willingness_changed()` → debug 日志记录
- `_on_emotion_state_changed()` → debug 日志记录
- `_on_bond_stage_changed()` → info 日志记录（Bond 阶段变化是重要事件）

---

#### T-03：http_hooks.py 启动时接入 dispatch_loop ✅

**问题**：Bus 初始化后没有启动 dispatch_loop，也没有注册各族订阅。

**改动**（在 Tribe 基础设施初始化块之后新增）：
- `await _bus.start()` → 启动 dispatch_loop（创建 asyncio.Task）
- 遍历 4 个族 Agent（LimbicAgent / PrefrontalAgent / SocialBrainAgent / CortexAgent），调用 `register_subscriptions()`
- 新增 `new_module_statuses["tribe_dispatch_loop"]` 状态上报
- shutdown 阶段新增 `await _shutdown_bus.stop()` → 优雅停止 dispatch_loop

---

#### T-04：HippocampusAgent 召回接口验证 ✅

**验证结论**：
- `HippocampusAgent.recall()` 已完整实现，通过 `RetrievalOrchestrator.arun()` 统一召回
- `chat_context_builder.py` 的 `_build_retrieval_rows()` 已通过 `get_hippocampus_agent().build_prompt_retrieval_rows()` 调用
- 主召回入口（`retrieval_summary` + `retrieval_evidence_items`）在上游 `dispatch_agent.py` 通过 `HippocampusAgent.recall()` 完成
- **无需修改**，T-04 验证通过

---

#### 当前架构状态（2026-04-09）

| 组件 | 状态 | 说明 |
|---|---|---|
| `TribeEventBus.subscribe + dispatch_loop` | ✅ 已实现 | asyncio.Queue 异步分发，支持通配符 |
| `LimbicAgent.register_subscriptions` | ✅ 已实现 | 订阅 4 个事件，handler 完整 |
| `PrefrontalAgent.register_subscriptions` | ✅ 已实现 | 订阅 2 个事件，evolution_cycle 异步执行 |
| `SocialBrainAgent.register_subscriptions` | ✅ 已实现 | 订阅 1 个事件 |
| `CortexAgent.register_subscriptions` | ✅ 已实现 | 订阅 3 个事件 |
| `http_hooks.py bus.start()` | ✅ 已实现 | lifespan 启动时调用，shutdown 时停止 |
| `HippocampusAgent.recall()` | ✅ 已验证 | 召回接口完全切换 |
| `ANSAgent.emit_from_thread` | ✅ 已实现 | inner_world_skill / body_sense_skill 已调用 |

---

#### 下一步待开发（优先级排序）

1. **T-06**：EndocrineAgent 接入调度门控（`NatsumeScheduler` 改为读取 `EndocrineAgent.resolve_vital_signs()`）
2. **T-07**：ImmuneAgent 接入主链路前置拦截（`dispatch_agent.py` 入口调用 `inspect_text()`）
3. **T-08**：`/internal/tribe_event` 端点接收 edge 事件后转发到 Bus（验证 EdgeTribeBridge 闭环）
4. **T-09**：SomaticAgent 情绪推断结果上报 LimbicAgent（`voice_emotion_inferred` 事件闭环）
5. **T-10**：`vital_signs` 统一生命体征聚合（`TribeStateRegistry.aggregate_vital_signs()`）

---

### 2026-04-09 · 第二批开发（T-05 / T-06 / T-07 / T-08 / T-09）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/app/http_hooks.py`（`_build_scheduler_emotion_state` 改造）
- `host_brain/app/dispatch_agent.py`（ImmuneAgent 前置拦截）
- `host_brain/tribe/limbic_agent.py`（EMOTION_INFERENCE_UPDATED 订阅）

---

#### T-05：ANSAgent 接入 NatsumeScheduler 验证 ✅（无需修改）

**验证结论**：
- `inner_world_skill.py` 已调用 `get_ans_agent().emit_inner_world_ticked(tick_result=...)`
- `body_sense_skill.py` 已调用 `get_ans_agent().emit_body_sense_updated(sense_result=...)`
- `http_hooks.py` 已调用 `get_ans_agent().set_event_loop(asyncio.get_running_loop())`
- **T-05 验证通过，无需修改**

---

#### T-06：EndocrineAgent 接入调度门控 ✅

**问题**：`_build_scheduler_emotion_state()` 直接调用 `get_endocrine_state()`（底层函数），绕过了 `EndocrineAgent` 接口，导致 Tribe 架构的 `EndocrineAgent.resolve_vital_signs()` 形同虚设。

**改动**（`http_hooks.py`）：
- `_build_scheduler_emotion_state()` 改为通过 `get_endocrine_agent().resolve_endocrine_state()` 获取内分泌状态
- 再通过 `endocrine_agent.resolve_vital_signs()` 获取 `gate_state` / `stress_index` / `energy_reserve` / `homeostasis_ok`
- 将 `vital_signs` 合并到 `endocrine_state` 字典，供 `NatsumeScheduler._resolve_endocrine_gate_state()` 读取
- 新增 `endocrine_state["source"] = "endocrine_agent"` 标记来源

**架构意义**：`NatsumeScheduler` 的调度门控现在通过 `EndocrineAgent` 统一读取内分泌状态，而不是直接调用底层函数。`EndocrineAgent` 成为调度门控的唯一数据源。

---

#### T-07：ImmuneAgent 接入主链路前置拦截 ✅

**问题**：`dispatch_agent.py` 的请求入口没有风险扫描，恶意 prompt 可以直接进入 LLM 处理链路。

**改动**（`dispatch_agent.py`）：
- 在 `dispatch_agent_sse_generator` 的**硬规则路由之前**（最前面）增加 ImmuneAgent 前置拦截
- 调用 `get_immune_agent().inspect_text(text=sanitized_message, source="chat", metadata={...})`
- `decision == "BLOCK"` 时：直接 yield REJECT 事件并 return，不进入任何路由
- `decision == "REPORT"` 时：记录 info 日志并继续处理（审计已由 `ImmuneAgent._record_result()` 写入）
- 异常时降级（不影响正常请求处理）

**关键设计**：
- `HOST_IMMUNE_GATE_BLOCKING=false`（默认）时，`BLOCK` 决策不会实际拦截（`inspect_text` 返回 `REPORT`）
- 只有显式设置 `HOST_IMMUNE_GATE_BLOCKING=true` 时才真正拦截
- 这确保了生产环境的安全性（默认只审计，不拦截）

---

#### T-08：/internal/tribe_event 端点转发验证 ✅（无需修改）

**验证结论**：
- `internal_tribe_routes.py` 的 `ingest_tribe_event` 端点已正确调用 `bus.publish(event)`
- `EdgeTribeBridge.publish_event()` 已正确向 `{host_base_url}/internal/tribe_event` 发送 POST 请求
- Edge → Host 的事件桥闭环已完整：`SomaticAgent._publish_event()` → `EdgeTribeBridge.publish_event()` → `/internal/tribe_event` → `bus.publish()` → `dispatch_loop` → handler
- **T-08 验证通过，无需修改**

---

#### T-09：SomaticAgent 情绪推断上报 LimbicAgent ✅

**问题**：`SomaticAgent.publish_emotion_inference_updated()` 已通过 `EdgeTribeBridge` 发布 `emotion_inference_updated` 事件，但 `LimbicAgent` 没有订阅此事件，导致语音情绪推断结果无法影响 host_brain 的情绪状态。

**改动**（`limbic_agent.py`）：
- `register_subscriptions()` 新增 `bus.subscribe("EMOTION_INFERENCE_UPDATED", self._on_emotion_inference_updated)`
- 新增 `_on_emotion_inference_updated()` handler：
  - 将语音情绪推断结果写入 `TribeStateRegistry`（key=`last_voice_emotion_inference`）
  - 若 `emotion_intensity >= 0.6`（高强度情绪），同步更新 `hard_state.emotion_tag` / `emotion_intensity` / `mood_state`
  - 异常时降级（不影响其他 handler）

**事件流闭环**：
```
edge_client/voice_pipeline
  → SomaticAgent.publish_emotion_inference_updated()
  → EdgeTribeBridge.publish_event("emotion_inference_updated", ...)
  → POST /internal/tribe_event
  → InMemoryTribeEventBus.publish()
  → dispatch_loop
  → LimbicAgent._on_emotion_inference_updated()
  → TribeStateRegistry["last_voice_emotion_inference"]
  → (高强度时) hard_state.emotion_tag 更新
```

**注意**：Bus 的 `_dispatch_loop` 以大写 key 匹配，`SomaticAgent` 发布的 `emotion_inference_updated`（小写）经 `/internal/tribe_event` 端点的 `new_tribe_event()` 处理后，`event_type` 保持原始大小写。`InMemoryTribeEventBus.subscribe()` 中 key 转大写，`_dispatch_loop` 中也转大写匹配，因此 `EMOTION_INFERENCE_UPDATED` 能正确匹配。

---

#### 当前架构状态（2026-04-09 第二批）

| 组件 | 状态 | 说明 |
|---|---|---|
| `_build_scheduler_emotion_state` → EndocrineAgent | ✅ 已接入 | 调度门控通过 EndocrineAgent 统一读取 |
| `dispatch_agent.py` ImmuneAgent 前置拦截 | ✅ 已接入 | BLOCK 时拒绝，REPORT 时审计继续 |
| `LimbicAgent` 订阅 `EMOTION_INFERENCE_UPDATED` | ✅ 已接入 | 语音情绪推断结果上报闭环 |
| Edge → Host 事件桥闭环 | ✅ 已验证 | EdgeTribeBridge → /internal/tribe_event → Bus |
| ANSAgent 接入 NatsumeScheduler | ✅ 已验证 | inner_world_skill/body_sense_skill 已调用 emit |

---

#### 下一步待开发（优先级排序）

1. **T-10**：`vital_signs` 统一生命体征聚合（`TribeStateRegistry.aggregate_vital_signs()`）
2. **T-11**：`CircadianAgent`（昼夜节律，可合并进 ANSAgent）
3. **T-12**：`ProprioceptionAgent`（本体感觉，Phase 5 后实施）
4. **T-13**：`PainRewardAgent`（痛觉/奖励，需 EndocrineAgent 完成后实施）
5. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）

---

### 2026-04-09 · 第三批开发（T-10 / T-11 / T-12）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/tribe_state.py`（`aggregate_vital_signs()` 新增）
- `host_brain/tribe/cortex_agent.py`（`collect_tribe_context()` + `route_intent()` 新增）
- `host_brain/app/chat_context_builder.py`（CortexAgent Supervisor 收口接入）

---

#### T-10：TribeStateRegistry.aggregate_vital_signs() ✅

**问题**：各族 Agent 各自写入 `TribeStateRegistry`，但没有统一的聚合接口，外部消费者需要逐个读取各族状态。

**改动**（`tribe_state.py`）：
- 新增 `aggregate_vital_signs()` 方法，从各族 Agent 写入的状态快照中聚合统一生命体征
- 聚合来源：
  - `endocrine_agent` → `gate_state` / `stress_index` / `energy_reserve` / `homeostasis_ok`
  - `limbic_agent` → `emotion_tag` / `mood_state` / `silence_willingness`
  - `last_voice_emotion_inference`（T-09 写入）→ `voice_emotion_tag` / `voice_emotion_intensity`
  - `social_brain_agent` → `bond_stage`
- 新增模块级 `_safe_float()` 辅助函数
- 返回字段包含 `ts_ms` 时间戳，便于下游判断数据新鲜度

**架构意义**：`TribeStateRegistry` 从"分散的 KV 存储"升级为"可聚合的生命体征数据库"，为 CortexAgent Supervisor 提供统一数据源。

---

#### T-11：CortexAgent 增强为 Supervisor 角色 ✅

**问题**：`CortexAgent` 只有对话生命周期事件发布能力，缺少 Supervisor 的核心职责：统一收口各族上下文 + 意图路由决策。

**改动**（`cortex_agent.py`）：

新增 `collect_tribe_context()` 方法：
- 调用 `LimbicAgent.state_registry.aggregate_vital_signs()` 获取统一生命体征
- 调用 `LimbicAgent.get_emotion_context()` 获取情绪上下文
- 调用 `SocialBrainAgent.get_bond_context()` 获取 Bond/关系上下文
- 调用 `EndocrineAgent.resolve_vital_signs()` 获取内分泌门控状态
- 所有调用均有异常降级保护

新增 `route_intent()` 方法：
- 根据 `gate_state` / `silence_willingness` / `homeostasis_ok` 决定路由策略
- 路由结果：`"chat"` | `"skill"` | `"silent"` | `"throttle"`
- `gate_state == "pause"` → `throttle`（内分泌系统要求暂停）
- `silence_willingness >= 0.85` → `silent`（主动沉默）
- `gate_state == "throttle"` 或 `homeostasis_ok == False` → `throttle`
- 其他 → `chat`（正常流程）

**架构意义**：`CortexAgent` 正式成为 Tribe 架构的 Supervisor，负责统一收口各族上下文并做出路由决策，符合文档设计意图。

---

#### T-12：chat_context_builder.py 接入 CortexAgent Supervisor 收口 ✅

**问题**：`build_chat_prompt_runtime_payload()` 虽然已经分散调用了各族 Agent，但没有通过 CortexAgent 统一收口，导致 `vital_signs` 聚合和意图路由结果无法注入 `runtime_payload`。

**改动**（`chat_context_builder.py`）：
- 在 `return runtime_payload` 之前增加 CortexAgent Supervisor 收口逻辑
- 调用 `CortexAgent.collect_tribe_context()` 聚合各族上下文
- 将 `vital_signs` 注入 `runtime_payload["tribe_vital_signs"]`
- 将 `route_intent()` 结果注入 `runtime_payload["tribe_route"]`
- 若有语音情绪推断结果，补充到 `runtime_payload["voice_emotion_inference"]`（T-09 闭环）
- 所有逻辑均有异常降级保护，不影响现有流程

**数据流**：
```
build_chat_prompt_runtime_payload()
  → CortexAgent.collect_tribe_context()
    → LimbicAgent.state_registry.aggregate_vital_signs()
    → LimbicAgent.get_emotion_context()
    → SocialBrainAgent.get_bond_context()
    → EndocrineAgent.resolve_vital_signs()
  → CortexAgent.route_intent()
  → runtime_payload["tribe_vital_signs"] = vital_signs
  → runtime_payload["tribe_route"] = route_result
  → runtime_payload["voice_emotion_inference"] = voice_emotion (if any)
```

---

#### 当前架构状态（2026-04-09 第三批）

| 组件 | 状态 | 说明 |
|---|---|---|
| `TribeStateRegistry.aggregate_vital_signs()` | ✅ 已实现 | 统一聚合各族生命体征 |
| `CortexAgent.collect_tribe_context()` | ✅ 已实现 | Supervisor 统一收口各族上下文 |
| `CortexAgent.route_intent()` | ✅ 已实现 | 基于 vital_signs 的意图路由决策 |
| `chat_context_builder` CortexAgent 收口 | ✅ 已接入 | tribe_vital_signs / tribe_route 注入 runtime_payload |
| T-09 voice_emotion 闭环 | ✅ 已完成 | voice_emotion_inference 注入 runtime_payload |

---

#### 下一步待开发（优先级排序）

1. **T-13**：`PainRewardAgent`（痛觉/奖励，需 EndocrineAgent 完成后实施）
2. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
3. **T-15**：`tribe_route` 接入 `dispatch_agent.py`（让 CortexAgent 的路由决策影响实际分发）
4. **T-16**：`tribe_vital_signs` 接入 prompt_builder（让生命体征影响 prompt 生成）

---

### 2026-04-09 · 第四批开发（T-15 / T-16 / T-13）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/app/dispatch_agent.py`（CortexAgent 路由门控接入）
- `host_brain/app/prompt_builder.py`（Phase XIX tribe_vital_signs 注入）
- `host_brain/tribe/pain_reward_agent.py`（新建，痛觉/奖励系统骨架）
- `host_brain/tribe/__init__.py`（PainRewardAgent 导出）
- `host_brain/app/http_hooks.py`（PainRewardAgent 订阅注册）

---

#### T-15：tribe_route 接入 dispatch_agent.py ✅

**问题**：`CortexAgent.route_intent()` 已实现路由决策，但 `dispatch_agent.py` 没有读取这个决策，导致 `throttle`/`silent` 路由结果无法影响实际分发。

**改动**（`dispatch_agent.py`）：
- 在 ImmuneAgent 拦截（步骤 0）之后、硬规则路由（步骤 1）之前，增加 **步骤 0.5 CortexAgent 路由门控**
- 调用 `CortexAgent.collect_tribe_context()` + `route_intent()` 获取路由决策
- `route == "silent"`：主动沉默，返回 `decision=SILENT` 的空响应并 `return`
- `route == "throttle"`：发出 `PRESENCE/THINKING` 提示后继续执行（不中断，未来可加实际延迟）
- 所有逻辑均有异常降级保护

**数据流**：
```
dispatch_agent_sse_generator()
  → 步骤 0: ImmuneAgent.inspect_text()  [BLOCK/REPORT/ALLOW]
  → 步骤 0.5: CortexAgent.route_intent()  [silent/throttle/chat]
    → silent: yield COMMIT(SILENT) + return
    → throttle: yield PRESENCE(THINKING) + continue
    → chat: continue
  → 步骤 1: _hard_rule_route()
  → 步骤 2: recent_turn 回查
  → 步骤 3: LLM 路由
  → 步骤 4+: 正常分发
```

---

#### T-16：tribe_vital_signs 接入 prompt_builder.py（Phase XIX）✅

**问题**：`runtime_payload["tribe_vital_signs"]` 已由 CortexAgent Supervisor 写入，但 `prompt_builder.py` 没有读取这个字段，导致生命体征无法影响 prompt 生成。

**改动**（`prompt_builder.py`）：

**数据提取段**（Phase XVIII 之后）：
- 新增 Phase XIX 数据提取：从 `runtime_payload["tribe_vital_signs"]` 读取
  - `_tv_gate_state` / `_tv_stress_index` / `_tv_energy_reserve` / `_tv_homeostasis_ok`
  - `_tv_mood_state` / `_tv_bond_stage` / `_tv_voice_emotion_tag` / `_tv_voice_emotion_intensity`
- 激活条件（`_tv_active`）：`gate_state != "open"` 或 `stress_index >= 0.4` 或 `homeostasis_ok == False` 或有语音情绪或 `mood_state` 非基线
- 正常状态下不注入，避免增加 prompt 长度

**prompt 组装段**（Phase XVIII 输出之后）：
- 新增 Phase XIX 输出块，在高压/异常状态时向 LLM 注入生命体征信息

**架构意义**：生命体征现在完整流经 `TribeStateRegistry → CortexAgent → runtime_payload → prompt_builder → LLM`，形成完整的生理状态 → 语言表达闭环。

---

#### T-13：PainRewardAgent 骨架实现 ✅

**新建文件**（`host_brain/tribe/pain_reward_agent.py`，约 280 行）：

**核心数据结构**：
- `pain_level (float 0~1)` / `reward_level (float 0~1)` / `net_valence (float -1~1)`
- `cumulative_pain / cumulative_reward`（累积值，不衰减）
- `hedonic_tone`：享乐基调（euphoric / pleasant / neutral / dysphoric / suffering）

**核心方法**：
- `record_pain(source, intensity)` — 非线性叠加痛觉
- `record_reward(source, intensity)` — 非线性叠加奖励
- `tick_decay()` — 每轮对话后自然衰减（12%/轮）
- `get_stress_signal()` — 向 EndocrineAgent 提供压力信号
- `get_emotion_modulation()` — 向 LimbicAgent 提供情绪调制信号

**事件订阅**：
- `INTERACTION_RECORDED` → `tick_decay()`
- `EMOTION_UPDATED` → 负向情绪记录 pain，正向情绪记录 reward
- `IMMUNE_BLOCK` → 记录 `topic_blocked` 痛觉

**注册**：`tribe/__init__.py` 导出 + `http_hooks.py` 订阅注册（subscriptions=6）

---

#### 当前架构状态（2026-04-09 第四批）

| 组件 | 状态 | 说明 |
|---|---|---|
| `dispatch_agent` CortexAgent 路由门控 | ✅ 已接入 | silent/throttle 路由决策影响实际分发 |
| `prompt_builder` Phase XIX 生命体征 | ✅ 已接入 | tribe_vital_signs 影响 prompt 生成 |
| `PainRewardAgent` 骨架 | ✅ 已实现 | 痛觉/奖励系统，订阅 INTERACTION_RECORDED/EMOTION_UPDATED |
| Tribe 订阅数量 | 6 个 | limbic/prefrontal/social_brain/cortex/pain_reward |

---

#### 下一步待开发（优先级排序）

1. **T-17**：`PainRewardAgent.get_stress_signal()` 接入 `EndocrineAgent`（让痛觉影响皮质醇）
2. **T-18**：`PainRewardAgent.get_emotion_modulation()` 接入 `LimbicAgent`（让奖励影响情绪偏置）
3. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）

---

### 2026-04-09 · 第五批开发（T-17 / T-18）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/endocrine_agent.py`（T-17：PainRewardAgent 压力信号接入）
- `host_brain/tribe/limbic_agent.py`（T-18：PainRewardAgent 情绪调制接入）

---

#### T-17：PainRewardAgent.get_stress_signal() 接入 EndocrineAgent ✅

**问题**：`PainRewardAgent` 已实现 `get_stress_signal()`，但 `EndocrineAgent` 没有读取这个信号，导致痛觉事件无法影响皮质醇水平。

**改动**（`endocrine_agent.py`，`resolve_endocrine_state()` 函数）：
- 在 `derive_endocrine_state()` 计算完基础内分泌状态后，调用 `PainRewardAgent.get_stress_signal()`
- 将 `net_stress_delta`（痛觉压力 - 奖励缓解）叠加到 `cortisol_level`
- 叠加量上限 ±0.15，避免单次事件过度影响皮质醇
- 在 payload 中写入 `_pain_stress_delta` 字段（调试用）
- 异常降级保护，不影响现有流程

**数学模型**：
```
net_stress_delta = pain_stress_delta - reward_relief_delta
                 = pain_level * 0.3 - reward_level * 0.2
clamped_delta = clamp(net_stress_delta, -0.15, +0.15)
new_cortisol = clamp(base_cortisol + clamped_delta, 0.0, 1.0)
```

**闭环**：
```
PainRewardAgent.record_pain(source="rejection", intensity=0.5)
  → pain_level = 0.5
  → get_stress_signal() → net_stress_delta = 0.5*0.3 = +0.15
  → EndocrineAgent.resolve_endocrine_state()
    → cortisol_level += 0.15 (上限 0.15)
    → gate_state 可能从 "open" → "throttle"
  → TribeStateRegistry.aggregate_vital_signs()
    → stress_index 上升
  → CortexAgent.route_intent()
    → route = "throttle" 或 "silent"
  → dispatch_agent 步骤 0.5 门控生效
```

---

#### T-18：PainRewardAgent.get_emotion_modulation() 接入 LimbicAgent ✅

**问题**：`PainRewardAgent` 已实现 `get_emotion_modulation()`，但 `LimbicAgent` 没有读取这个信号，导致奖励/痛觉事件无法影响情绪持久化。

**改动**（`limbic_agent.py`，`resolve_persistent_emotion_state()` 函数）：
- 在历史情绪融合之后、`persist()` 之前，调用 `PainRewardAgent.get_emotion_modulation()`
- 将 `valence_bias` 叠加到 `cur_valence`（上限 ±0.20，权重 0.35）
- 将 `arousal_boost` 叠加到 `cur_arousal`（上限 +0.15，权重 0.25）
- 在 payload 中写入 `pain_valence_bias` / `pain_arousal_boost` / `pain_suggested_emotion_tag`（调试用）
- 异常降级保护，不影响现有流程

**数学模型**：
```
valence_bias = (reward_level - pain_level)  # 范围 -1 ~ +1
arousal_boost = (pain_level + reward_level) * 0.6  # 范围 0 ~ 1

# 叠加到情绪持久化（权重 0.35 / 0.25 避免过度影响）
cur_valence += clamp(valence_bias * 0.35, -0.20, +0.20)
cur_arousal += min(arousal_boost * 0.25, +0.15)
```

**闭环**：
```
PainRewardAgent.record_reward(source="recognized", intensity=0.6)
  → reward_level = 0.6
  → get_emotion_modulation() → valence_bias = +0.6, suggested_tag = "JOY"
  → LimbicAgent.resolve_persistent_emotion_state()
    → cur_valence += 0.6 * 0.35 = +0.21 (上限 +0.20)
    → 情绪持久化写入更积极的 valence
  → 下次对话时，历史情绪融合带入积极偏置
  → LLM 生成更积极的回复
```

---

#### 当前架构状态（2026-04-09 第五批）

**PainRewardAgent 完整闭环已打通**：

```
外部事件（被拒绝/被认可/话题受挫/情感共鸣）
  ↓ record_pain() / record_reward()
PainRewardAgent
  ├─ get_stress_signal() → net_stress_delta
  │    ↓
  │  EndocrineAgent.resolve_endocrine_state()
  │    → cortisol_level 调整
  │    → gate_state 变化（open/throttle/closed）
  │    ↓
  │  TribeStateRegistry.aggregate_vital_signs()
  │    → stress_index 更新
  │    ↓
  │  CortexAgent.route_intent()
  │    → route = silent/throttle/chat
  │    ↓
  │  dispatch_agent 步骤 0.5 门控
  │    → 主动沉默 / 降速提示 / 正常分发
  │
  └─ get_emotion_modulation() → valence_bias / arousal_boost
       ↓
     LimbicAgent.resolve_persistent_emotion_state()
       → cur_valence / cur_arousal 调整
       → EmotionPersistence.persist() 写入
       ↓
     下次对话历史情绪融合
       → LLM 情绪偏置影响回复风格
```

| 组件 | 状态 | 说明 |
|---|---|---|
| `PainRewardAgent` → `EndocrineAgent` | ✅ 已接入 | 痛觉 → 皮质醇叠加（±0.15 上限） |
| `PainRewardAgent` → `LimbicAgent` | ✅ 已接入 | 奖励/痛觉 → 情绪效价偏置（±0.20 上限） |
| 完整痛觉/奖励闭环 | ✅ 完成 | 外部事件 → 内分泌 → 路由门控 → 情绪持久化 → LLM |

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-19**（新增）：`PainRewardAgent` 接入 `TribeStateRegistry.aggregate_vital_signs()`，将 `hedonic_state` 纳入生命体征聚合
3. **T-20**（新增）：`CortexAgent.collect_tribe_context()` 读取 `hedonic_state`，注入 `tribe_vital_signs`

---

### 2026-04-09 · 第六批开发（T-19 / T-20）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/tribe_state.py`（T-19：hedonic_state 纳入 aggregate_vital_signs）
- `host_brain/tribe/cortex_agent.py`（T-20：collect_tribe_context 增加 hedonic_state 字段）

---

#### T-19：PainRewardAgent.hedonic_state 纳入 aggregate_vital_signs() ✅

**问题**：`aggregate_vital_signs()` 聚合了内分泌/情绪/Bond 等状态，但缺少 `PainRewardAgent` 的享乐状态，导致 `vital_signs` 中没有 `hedonic_tone` / `pain_level` / `reward_level` / `net_valence` 字段。

**改动**（`tribe_state.py`，`aggregate_vital_signs()` 函数）：
- 增加 `pain_reward_agent` 命名空间的读取
- 从 `state` 中提取 `hedonic_tone` / `pain_level` / `reward_level` / `net_valence`
- 将这 4 个字段追加到返回的 `vital_signs` 字典中
- 更新 docstring，补充新增字段说明

**新增返回字段**：
```python
{
    # ... 原有字段 ...
    "hedonic_tone": "neutral",      # "positive"|"neutral"|"negative"|"mixed"
    "pain_level": 0.0,              # 当前痛觉强度 0.0 ~ 1.0
    "reward_level": 0.0,            # 当前奖励强度 0.0 ~ 1.0
    "net_valence": 0.0,             # 净享乐效价 -1.0 ~ 1.0
}
```

**数据流**：
```
PainRewardAgent._flush_state()
  → state_registry.set_state("pain_reward_agent", {...})
  → aggregate_vital_signs() 读取 states["pain_reward_agent"]
  → vital_signs["hedonic_tone"] / ["pain_level"] / ["reward_level"] / ["net_valence"]
```

---

#### T-20：CortexAgent.collect_tribe_context() 读取 hedonic_state 注入 tribe_vital_signs ✅

**问题**：`collect_tribe_context()` 返回结构中没有 `hedonic_state` 字段，`chat_context_builder` 无法直接读取完整享乐状态。

**改动**（`cortex_agent.py`，`collect_tribe_context()` 函数）：
- 在返回结构初始化中增加 `"hedonic_state": {}` 字段
- 增加第 5 步：调用 `PainRewardAgent.get_hedonic_state()` 读取完整快照
- 更新 docstring，补充 `hedonic_state` 字段说明

**返回结构变化**：
```python
# 新增字段
{
    "vital_signs": {
        # T-19 已包含摘要字段
        "hedonic_tone": "neutral",
        "pain_level": 0.0,
        "reward_level": 0.0,
        "net_valence": 0.0,
        # ...
    },
    # T-20 新增完整快照
    "hedonic_state": {
        "pain_level": 0.0,
        "reward_level": 0.0,
        "net_valence": 0.0,
        "cumulative_pain": 0.0,
        "cumulative_reward": 0.0,
        "last_pain_source": None,
        "last_reward_source": None,
        "hedonic_tone": "neutral",
        "ts_ms": 1234567890,
    },
}
```

**设计说明**：
- `vital_signs` 中的 `hedonic_tone` 等字段（T-19）：摘要级别，供路由门控快速判断
- `hedonic_state`（T-20）：完整快照，供 `chat_context_builder` 精细化使用（如注入 prompt）

---

#### 当前架构状态（2026-04-09 第六批）

**hedonic_state 完整传播链已打通**：

```
PainRewardAgent._flush_state()
  → state_registry.set_state("pain_reward_agent", {...})
  ↓
TribeStateRegistry.aggregate_vital_signs()  [T-19]
  → vital_signs["hedonic_tone/pain_level/reward_level/net_valence"]
  ↓
CortexAgent.collect_tribe_context()  [T-20]
  → result["vital_signs"]["hedonic_tone"]  (摘要)
  → result["hedonic_state"]  (完整快照)
  ↓
chat_context_builder.build_chat_prompt_runtime_payload()  [T-12]
  → runtime_payload["tribe_vital_signs"]["hedonic_tone"]
  → runtime_payload["hedonic_state"]
  ↓
prompt_builder.py Phase XIX  [T-16]
  → 高压/低享乐状态时注入 prompt 提示
```

| 组件 | 状态 | 说明 |
|---|---|---|
| `PainRewardAgent` → `TribeStateRegistry` | ✅ 已接入 | `_flush_state()` 写入 `pain_reward_agent` 命名空间 |
| `TribeStateRegistry.aggregate_vital_signs()` | ✅ 已接入 | T-19：读取 `hedonic_tone/pain_level/reward_level/net_valence` |
| `CortexAgent.collect_tribe_context()` | ✅ 已接入 | T-20：新增 `hedonic_state` 完整快照字段 |
| `chat_context_builder` → `runtime_payload` | ✅ 已接入 | T-12：注入 `tribe_vital_signs` + `hedonic_state` |
| `prompt_builder` Phase XIX | ✅ 已接入 | T-16：高压状态注入 prompt |

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-21**（新增）：`prompt_builder` Phase XIX 扩展——在 `hedonic_tone == "negative"` 时注入享乐状态提示（当前仅注入压力/门控状态）
3. **T-22**（新增）：`dispatch_agent` 接入 `hedonic_state`，在 `pain_level >= 0.6` 时触发主动情绪表达

---

### 2026-04-09 · 第七批开发（T-21 / T-22）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/app/prompt_builder.py`（T-21：Phase XIX 扩展，hedonic_tone 注入）
- `host_brain/app/dispatch_agent.py`（T-22：hedonic_state 接入，PAIN_SIGNAL/REWARD_SIGNAL 事件）

---

#### T-21：prompt_builder Phase XIX 扩展——hedonic_tone=negative 时注入享乐状态提示 ✅

**问题**：Phase XIX 已注入 `gate_state`/`stress_index`/`mood_state` 等生命体征，但缺少 `hedonic_tone`/`pain_level`/`reward_level`/`net_valence` 字段，导致 LLM 无法感知当前的享乐状态（痛觉/奖励水平）。

**改动**（`prompt_builder.py`，Phase XIX 数据提取段 + 渲染段）：

**数据提取段**（新增 4 个变量 + 1 个激活条件）：
```python
# T-21：享乐状态字段
_tv_hedonic_tone = str(_tribe_vital.get("hedonic_tone") or "neutral").strip().lower()
_tv_pain_level = float(_tribe_vital.get("pain_level") or 0.0)
_tv_reward_level = float(_tribe_vital.get("reward_level") or 0.0)
_tv_net_valence = float(_tribe_vital.get("net_valence") or 0.0)

# 享乐状态激活条件
_tv_hedonic_active = (
    _tv_hedonic_tone in ("negative", "dysphoric", "suffering", "mixed")
    or _tv_pain_level >= 0.4
    or _tv_reward_level >= 0.6  # 高奖励也值得注入
)
```

**`_tv_active` 激活条件更新**：新增 `or _tv_hedonic_active`，享乐状态异常时也触发整个 Phase XIX 注入。

**渲染段**（在 voice_emotion 注入之后追加）：
```
- tribe_hedonic_tone: negative
- tribe_pain_level: 0.72
- tribe_reward_level: 0.10
- tribe_net_valence: -0.62
```
仅在 `_tv_hedonic_active` 时输出，正常状态（neutral/positive）不注入。

**注入策略**：
- `hedonic_tone in ("negative", "dysphoric", "suffering", "mixed")` → 注入（消极享乐状态）
- `pain_level >= 0.4` → 注入（中度以上痛觉）
- `reward_level >= 0.6` → 注入（高奖励，积极情绪影响表达风格）
- 其他（neutral/positive + pain<0.4 + reward<0.6）→ 不注入

---

#### T-22：dispatch_agent 接入 hedonic_state，pain_level >= 0.6 时触发主动情绪表达 ✅

**问题**：`dispatch_agent` 的 T-15 路由门控已读取 `collect_tribe_context()`，但没有读取 `hedonic_state` 字段，导致高痛觉状态无法触发主动情绪表达信号。

**改动**（`dispatch_agent.py`，步骤 0.5 路由门控之后，新增步骤 0.6）：

**新增步骤 0.6 hedonic_state 痛觉信号**：
- 从 `_tribe_ctx["hedonic_state"]` 读取完整享乐状态快照（T-20 已注入）
- `pain_level >= 0.6`：发出 `PRESENCE/PAIN_SIGNAL` 事件（不中断流程）
  - 携带 `hedonic_tone` / `pain_level` / `net_valence` / `last_pain_source`
  - 前端/上层可据此触发主动情绪表达（如叹气、沉默、情绪化回复）
- `reward_level >= 0.7`：发出 `PRESENCE/REWARD_SIGNAL` 事件（积极情绪信号）
  - 携带 `hedonic_tone` / `reward_level` / `net_valence` / `last_reward_source`

**SSE 事件格式**：
```json
{
  "phase": "PRESENCE",
  "event": "PAIN_SIGNAL",
  "request_id": "xxx",
  "hedonic_tone": "negative",
  "pain_level": 0.72,
  "net_valence": -0.62,
  "last_pain_source": "topic_blocked"
}
```

**设计说明**：
- 步骤 0.6 在步骤 0.5（路由门控）之后执行，`_tribe_ctx` 已经获取，无需额外调用
- 不中断流程（不 `return`），仅作为情绪信号通知前端
- 阈值设计：`pain_level >= 0.6`（中高痛觉）/ `reward_level >= 0.7`（高奖励），避免频繁触发

---

#### 当前架构状态（2026-04-09 第七批）

**hedonic_state 完整表达链已打通**：

```
PainRewardAgent._flush_state()
  → state_registry("pain_reward_agent")
  ↓
aggregate_vital_signs() [T-19]
  → vital_signs["hedonic_tone/pain_level/reward_level/net_valence"]
  ↓
collect_tribe_context() [T-20]
  → result["vital_signs"]（摘要）
  → result["hedonic_state"]（完整快照）
  ↓
dispatch_agent 步骤 0.6 [T-22]
  → pain_level >= 0.6: PRESENCE/PAIN_SIGNAL（主动情绪表达信号）
  → reward_level >= 0.7: PRESENCE/REWARD_SIGNAL（积极情绪信号）
  ↓
chat_context_builder → runtime_payload["tribe_vital_signs"]
  ↓
prompt_builder Phase XIX [T-21]
  → hedonic_tone=negative/pain>=0.4/reward>=0.6 时注入:
    tribe_hedonic_tone / tribe_pain_level / tribe_reward_level / tribe_net_valence
  ↓
LLM 生成回复（受享乐状态影响）
```

| 组件 | 状态 | 说明 |
|---|---|---|
| `PainRewardAgent` → `TribeStateRegistry` | ✅ 已接入 | `_flush_state()` 写入 `pain_reward_agent` 命名空间 |
| `aggregate_vital_signs()` hedonic 字段 | ✅ 已接入 | T-19：4 个享乐字段纳入 vital_signs |
| `collect_tribe_context()` hedonic_state | ✅ 已接入 | T-20：完整快照字段 |
| `dispatch_agent` PAIN/REWARD_SIGNAL | ✅ 已接入 | T-22：pain>=0.6 / reward>=0.7 时发出情绪信号 |
| `prompt_builder` Phase XIX hedonic 注入 | ✅ 已接入 | T-21：hedonic_tone=negative 时注入 4 个字段 |

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-23**（新增）：前端/上层处理 `PAIN_SIGNAL`/`REWARD_SIGNAL` 事件，触发具体的主动情绪表达行为（如插入情绪化短句、调整回复风格）
3. **T-24**（新增）：`PainRewardAgent` 接入更多事件源（如 `BOND_RIFT_DETECTED` → 高强度痛觉，`BOND_MILESTONE` → 高强度奖励）

---

### 2026-04-09 · 第八批开发（T-23 / T-24）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/app/chat_context_builder.py`（T-23：补充 hedonic_state 注入到 runtime_payload）
- `host_brain/tribe/pain_reward_agent.py`（T-24：新增 4 个事件源订阅）

---

#### T-23：chat_context_builder 补充 hedonic_state 注入 ✅

**问题**：T-12 接入 CortexAgent 时，`collect_tribe_context()` 已经返回 `hedonic_state` 完整快照（T-20 已注入），但 `chat_context_builder.py` 只将 `vital_signs` 写入 `runtime_payload["tribe_vital_signs"]`，没有将 `hedonic_state` 写入 `runtime_payload["hedonic_state"]`，导致下游消费者（如 prompt_builder）无法通过 `runtime_payload` 读取完整享乐状态快照。

**改动**（`chat_context_builder.py`，T-12 代码块）：

```python
# T-23：将完整 hedonic_state 快照注入 runtime_payload
_hedonic_state = dict(_tribe_ctx.get("hedonic_state") or {})
if _hedonic_state:
    runtime_payload["hedonic_state"] = _hedonic_state
```

同时更新 `logger.debug` 日志，追加 `hedonic_tone` 字段输出。

**数据流**：
```
collect_tribe_context()["hedonic_state"]
  → runtime_payload["hedonic_state"]
  → prompt_builder（可通过 runtime_payload 读取完整快照）
  → dispatch_agent 步骤 0.6（已通过 _tribe_ctx 读取，T-22）
```

---

#### T-24：PainRewardAgent 接入更多事件源 ✅

**问题**：`PainRewardAgent` 只订阅了 3 个事件（`INTERACTION_RECORDED`/`EMOTION_UPDATED`/`IMMUNE_BLOCK`），缺少关系进展/退步、任务成功、幽默成功等重要事件源，导致这些场景无法触发痛觉/奖励更新。

**改动**（`pain_reward_agent.py`）：

**新增 4 个 async handler 方法**：
| handler | 事件 | 效果 | 默认强度 |
|---|---|---|---|
| `_on_bond_progress` | `BOND_PROGRESS` | `bond_progress` 奖励 | 0.5 |
| `_on_bond_regress` | `BOND_REGRESS` | `bond_regress` 痛觉 | 0.4 |
| `_on_task_success` | `TASK_SUCCESS` | `task_success` 奖励 | 0.35 |
| `_on_humor_landed` | `HUMOR_LANDED` | `humor_landed` 奖励 | 0.3 |

**`register_subscriptions()` 更新**：新增 4 个 `bus.subscribe()` 调用。

**`on_tribe_event()` 更新**：在 `IMMUNE_BLOCK` 分支之后新增 4 个 `elif` 分支（同步路径，与 async handler 保持一致）。

**强度设计**：
- 所有 handler 支持从 `payload["intensity"]` 读取自定义强度，有默认值兜底
- 强度上限 `min(1.0, intensity)` 防止溢出

---

#### 当前架构状态（2026-04-09 第八批）

**PainRewardAgent 事件订阅全景**：

| 事件 | 效果 | 强度 | 批次 |
|---|---|---|---|
| `INTERACTION_RECORDED` | `tick_decay()` 衰减 | — | T-13 |
| `EMOTION_UPDATED` | 情绪标签 → 痛觉/奖励 | intensity×0.5 | T-13 |
| `IMMUNE_BLOCK` | `topic_blocked` 痛觉 | 0.2 | T-13 |
| `BOND_PROGRESS` | `bond_progress` 奖励 | 0.5（可配置） | T-24 |
| `BOND_REGRESS` | `bond_regress` 痛觉 | 0.4（可配置） | T-24 |
| `TASK_SUCCESS` | `task_success` 奖励 | 0.35（可配置） | T-24 |
| `HUMOR_LANDED` | `humor_landed` 奖励 | 0.3（可配置） | T-24 |

**runtime_payload 字段全景**（经 T-12/T-23 注入）：

| 字段 | 来源 | 批次 |
|---|---|---|
| `tribe_vital_signs` | `collect_tribe_context()["vital_signs"]` | T-12 |
| `tribe_route` | `route_intent()` | T-12 |
| `voice_emotion_inference` | `collect_tribe_context()["voice_emotion"]` | T-12 |
| `hedonic_state` | `collect_tribe_context()["hedonic_state"]` | T-23 |

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-25**（新增）：`SocialBrainAgent` 发布 `BOND_PROGRESS`/`BOND_REGRESS` 事件（T-24 的上游触发器）
3. **T-26**（新增）：`PrefrontalAgent` 发布 `TASK_SUCCESS`/`HUMOR_LANDED` 事件（T-24 的上游触发器）

---

### 2026-04-09 · 第九批开发（T-25 / T-26）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/social_brain_agent.py`（T-25：`_on_interaction_recorded` 发布 BOND_PROGRESS/BOND_REGRESS）
- `host_brain/tribe/cortex_agent.py`（T-25：`_on_bond_stage_changed` 发布 BOND_PROGRESS/BOND_REGRESS）
- `host_brain/app/top_agent.py`（T-26：`emit_conversation_ended` 补充 assistant_reply/user_message 字段）
- `host_brain/tribe/prefrontal_agent.py`（T-26：`_on_interaction_recorded` 发布 TASK_SUCCESS/HUMOR_LANDED）

---

#### T-25：SocialBrainAgent / CortexAgent 发布 BOND_PROGRESS/BOND_REGRESS ✅

**问题**：T-24 为 `PainRewardAgent` 新增了 `BOND_PROGRESS`/`BOND_REGRESS` 事件订阅，但没有上游发布者，事件永远不会触发。

**改动 1**（`social_brain_agent.py`，`_on_interaction_recorded`）：
- 在处理 `INTERACTION_RECORDED` 事件时，调用 `resolve_bond_snapshot()` 获取当前 bond 快照
- 根据 `bond_trend` 字段：`PROGRESS` → 发布 `BOND_PROGRESS`，`REGRESS` → 发布 `BOND_REGRESS`
- 强度来自 `bond_trend_intensity`（默认 0.3）

**改动 2**（`cortex_agent.py`，`_on_bond_stage_changed`）：
- 在 bond stage 跃迁时，根据 stage 升降发布对应事件
- Stage 升级 → `BOND_PROGRESS`（强度 0.7，高于普通 trend）
- Stage 降级 → `BOND_REGRESS`（强度 0.6）
- 使用 `_STAGE_ORDER` 列表判断升降方向

**触发链**：
```
BondDynamicsWriter.apply_update()
  → BOND_STAGE_CHANGED（CortexAgent 订阅）
    → CortexAgent._on_bond_stage_changed()
      → BOND_PROGRESS / BOND_REGRESS（PainRewardAgent 订阅）
        → record_reward("bond_progress") / record_pain("bond_regress")

INTERACTION_RECORDED（SocialBrainAgent 订阅）
  → SocialBrainAgent._on_interaction_recorded()
    → resolve_bond_snapshot() → bond_trend
      → BOND_PROGRESS / BOND_REGRESS（PainRewardAgent 订阅）
```

---

#### T-26：PrefrontalAgent 发布 TASK_SUCCESS/HUMOR_LANDED ✅

**问题**：T-24 为 `PainRewardAgent` 新增了 `TASK_SUCCESS`/`HUMOR_LANDED` 事件订阅，但没有上游发布者。

**改动 1**（`top_agent.py`，`emit_conversation_ended` 调用处）：
- 在 `summary` 中补充 `user_message`（完整）和 `assistant_reply`（前 512 字符）字段
- 这些字段通过 `INTERACTION_RECORDED` 事件 payload 传递给 `PrefrontalAgent`

**改动 2**（`prefrontal_agent.py`，`_on_interaction_recorded`）：
- 在 `run_growth_feedback_cycle` 之后，通过关键词检测判断 humor/task 成功
- **幽默成功检测**：`assistant_reply` 中包含笑声/幽默标记词（哈哈/lol/😂 等）→ 发布 `HUMOR_LANDED`（强度 0.3）
- **任务成功检测**：`user_message` 包含任务请求词 + `assistant_reply` 包含完成标记词 → 发布 `TASK_SUCCESS`（强度 0.35）
- 两者互斥：humor 优先，避免重复触发

**触发链**：
```
top_agent.post_process()
  → emit_conversation_ended(summary={assistant_reply, user_message})
    → INTERACTION_RECORDED（PrefrontalAgent 订阅）
      → PrefrontalAgent._on_interaction_recorded()
        → 关键词检测 → HUMOR_LANDED / TASK_SUCCESS（PainRewardAgent 订阅）
          → record_reward("humor_landed") / record_reward("task_success")
```

---

#### 完整事件发布/订阅全景（2026-04-09 第九批）

| 事件 | 发布者 | 订阅者 | 触发条件 |
|---|---|---|---|
| `BOND_PROGRESS` | `SocialBrainAgent`（bond_trend=PROGRESS）<br>`CortexAgent`（stage 升级） | `PainRewardAgent` | 关系进展/stage 升级 |
| `BOND_REGRESS` | `SocialBrainAgent`（bond_trend=REGRESS）<br>`CortexAgent`（stage 降级） | `PainRewardAgent` | 关系退步/stage 降级 |
| `TASK_SUCCESS` | `PrefrontalAgent`（任务完成关键词检测） | `PainRewardAgent` | 用户请求任务 + AI 完成回复 |
| `HUMOR_LANDED` | `PrefrontalAgent`（幽默关键词检测） | `PainRewardAgent` | AI 回复包含笑声/幽默标记 |

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-27**（新增）：`PainRewardAgent` 状态持久化（跨会话保持 `hedonic_state`，避免重启后归零）
3. **T-28**（新增）：`EndocrineAgent` 接入 `BOND_PROGRESS`/`BOND_REGRESS` 事件（关系变化影响催产素/皮质醇）

---

### 2026-04-09 · 第十批开发（T-27 / T-28）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/pain_reward_agent.py`（T-27：持久化 load_state/save_state + 节流 flush）
- `host_brain/tribe/endocrine_agent.py`（T-28：register_subscriptions + BOND 事件 handler）
- `host_brain/app/http_hooks.py`（T-28：EndocrineAgent 加入订阅注册列表）

---

#### T-27：PainRewardAgent 状态持久化 ✅

**问题**：`PainRewardAgent` 的 `hedonic_state`（`pain_level`/`reward_level`/`cumulative_pain` 等）仅存在于内存中，进程重启后全部归零，导致长期情绪积累效果丢失。

**改动**（`pain_reward_agent.py`）：

1. **新增顶层常量**：
   - `_DEFAULT_STATE_PATH`：持久化文件路径（默认 `host_brain/data/pain_reward_state.json`，可通过 `NATSUME_PAIN_REWARD_STATE_PATH` 环境变量覆盖）
   - `_PERSIST_THROTTLE = 5`：每 5 次 flush 才写一次磁盘（避免频繁 IO）

2. **新增 dataclass 字段**：
   - `_flush_counter: int`：flush 计数器，用于节流
   - `_state_path: Path`：持久化文件路径

3. **修改 `_flush_state()`**：
   - 每次 flush 递增 `_flush_counter`
   - 当 `_flush_counter % _PERSIST_THROTTLE == 0` 时，调用 `save_state()` 写磁盘

4. **新增 `save_state()` 方法**：
   - 原子写入：先写 `.tmp` 临时文件，再 `rename` 替换，避免写入中断导致文件损坏
   - 持久化字段：`pain_level`/`reward_level`/`cumulative_pain`/`cumulative_reward`/`last_pain_source`/`last_reward_source`/`saved_at_ms`
   - 失败时降级（不抛异常，仅 debug 日志）

5. **新增 `load_state()` 方法**：
   - 从 JSON 文件加载状态，恢复所有持久化字段
   - 对数值做范围校验（pain/reward 限制在 0~1）
   - 文件不存在时返回 `False`（首次启动正常情况）

6. **修改 `get_pain_reward_agent()`**：
   - 初始化后自动调用 `load_state()`，实现启动时状态恢复

**持久化文件格式**：
```json
{
  "pain_level": 0.123,
  "reward_level": 0.456,
  "cumulative_pain": 2.345,
  "cumulative_reward": 5.678,
  "last_pain_source": "topic_blocked",
  "last_reward_source": "humor_landed",
  "saved_at_ms": 1744166400000
}
```

---

#### T-28：EndocrineAgent 接入 BOND_PROGRESS/BOND_REGRESS 事件 ✅

**问题**：`EndocrineAgent` 目前只通过 `PainRewardAgent.get_stress_signal()` 间接感知情绪压力，无法直接响应关系变化（bond 进展/退步）对激素的影响。

**改动**（`endocrine_agent.py`）：

1. **新增 dataclass 字段**：
   - `_bond_cortisol_delta: float`：bond 事件产生的皮质醇增量缓冲
   - `_bond_oxytocin_delta: float`：bond 事件产生的催产素增量缓冲
   - `_bond_lock: threading.RLock`：线程安全锁

2. **修改 `resolve_endocrine_state()`**：
   - 在 T-17 压力信号叠加之后，消费 `_bond_cortisol_delta` 和 `_bond_oxytocin_delta` 缓冲
   - 消费后清零（避免重复叠加）
   - 将 `_bond_cortisol_delta`/`_bond_oxytocin_delta` 写入 payload（供调试）

3. **新增 `register_subscriptions()` 方法**：
   - 订阅 `BOND_PROGRESS` 和 `BOND_REGRESS` 事件

4. **新增 `_on_bond_progress()` handler**：
   - 催产素增量：`min(0.12, intensity * 0.15)`（关系进展 → 催产素上升）
   - 皮质醇减量：`-min(0.05, intensity * 0.05)`（关系进展 → 压力轻微降低）

5. **新增 `_on_bond_regress()` handler**：
   - 皮质醇增量：`min(0.08, intensity * 0.10)`（关系退步 → 压力上升）
   - 催产素减量：`-min(0.06, intensity * 0.08)`（关系退步 → 催产素降低）

**改动**（`http_hooks.py`）：
- 将 `endocrine_agent` 加入订阅注册列表（第 6 个）
- 更新 subscriptions 计数从 6 → 7

**激素影响量化**：

| 事件 | 皮质醇变化 | 催产素变化 | 说明 |
|---|---|---|---|
| `BOND_PROGRESS`（intensity=0.3） | -0.015 | +0.045 | 普通关系进展 |
| `BOND_PROGRESS`（intensity=0.7，stage升级） | -0.035 | +0.105 | Stage 跃迁 |
| `BOND_REGRESS`（intensity=0.4） | +0.04 | -0.032 | 普通关系退步 |
| `BOND_REGRESS`（intensity=0.6，stage降级） | +0.06 | -0.048 | Stage 降级 |

**完整激素调制链（T-17 + T-28）**：
```
PainRewardAgent.get_stress_signal()
  → net_stress_delta → cortisol_level ±0.15（T-17）

BOND_PROGRESS → EndocrineAgent._on_bond_progress()
  → _bond_oxytocin_delta += 0.045~0.12
  → _bond_cortisol_delta -= 0.015~0.05
  → 下次 resolve_endocrine_state() 消费

BOND_REGRESS → EndocrineAgent._on_bond_regress()
  → _bond_cortisol_delta += 0.04~0.08
  → _bond_oxytocin_delta -= 0.032~0.06
  → 下次 resolve_endocrine_state() 消费
```

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-29**（新增）：`TribeStateRegistry` 持久化（跨会话保持所有 Agent 状态快照）
3. **T-30**（新增）：`LimbicAgent` 接入 `BOND_PROGRESS`/`BOND_REGRESS` 事件（关系变化影响情绪基线）

---

### 2026-04-09 · 第十一批开发（T-29 / T-30）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/tribe_state.py`（T-29：持久化 save_snapshot/load_snapshot + 全局单例 get_global_registry）
- `host_brain/tribe/limbic_agent.py`（T-29：使用全局单例；T-30：BOND 事件 handler）
- `host_brain/tribe/pain_reward_agent.py`（T-29：使用全局单例）
- `host_brain/app/http_hooks.py`（T-29：启动时 load_snapshot；T-30：subscriptions 计数 7→9）

---

#### T-29：TribeStateRegistry 持久化 ✅

**问题**：`TribeStateRegistry` 是纯内存存储，且各 Agent 各自持有独立实例（`default_factory=TribeStateRegistry`），导致：
1. 重启后所有 Agent 状态快照丢失
2. 各 Agent 的状态无法跨 Agent 共享（如 `CortexAgent` 只能通过 `get_limbic_agent().state_registry` 访问 LimbicAgent 的状态）

**改动**（`tribe_state.py`）：

1. **新增顶层常量**：
   - `_DEFAULT_SNAPSHOT_PATH`：持久化文件路径（默认 `host_brain/data/tribe_state_snapshot.json`，可通过 `NATSUME_TRIBE_STATE_SNAPSHOT_PATH` 环境变量覆盖）
   - `_SNAPSHOT_THROTTLE = 10`：每 10 次 `set_state` 才写一次磁盘（节流 IO）

2. **修改 `TribeStateRegistry.__init__()`**：
   - 新增 `state_path`/`persist_throttle` 参数
   - 新增 `_persist_counter` 字段

3. **修改 `set_state()`**：
   - 每次写入递增 `_persist_counter`
   - 节流触发 `save_snapshot()`（在锁外执行，避免阻塞）

4. **新增 `save_snapshot()` 方法**：
   - 原子写入（.tmp → rename）
   - 持久化格式：`{"version": 1, "saved_at_ms": ..., "namespaces": {...}}`

5. **新增 `load_snapshot()` 方法**：
   - 从 JSON 文件恢复所有命名空间状态
   - 文件不存在时返回 `False`（首次启动正常情况）

6. **新增全局单例 `get_global_registry()`**：
   - 双重检查锁（DCL）确保线程安全
   - 所有族 Agent 共享同一个 `TribeStateRegistry` 实例

**改动**（`limbic_agent.py` / `pain_reward_agent.py`）：
- `state_registry` 字段 `default_factory` 从 `TribeStateRegistry` 改为 `get_global_registry`
- 确保所有 Agent 写入同一个 registry，状态跨 Agent 可见

**改动**（`http_hooks.py`）：
- 在 `await _bus.start()` 之后，调用 `get_global_registry().load_snapshot()` 恢复状态

**持久化文件格式**：
```json
{
  "version": 1,
  "saved_at_ms": 1744166400000,
  "namespaces": {
    "limbic_agent": {"namespace": "limbic_agent", "state": {...}, "writer": "...", "updated_at_ms": ...},
    "pain_reward_agent": {"namespace": "pain_reward_agent", "state": {...}, ...},
    "endocrine_agent": {"namespace": "endocrine_agent", "state": {...}, ...}
  }
}
```

---

#### T-30：LimbicAgent 接入 BOND_PROGRESS/BOND_REGRESS 事件 ✅

**问题**：`LimbicAgent` 目前不感知关系变化，`BOND_PROGRESS`/`BOND_REGRESS` 事件只影响 `PainRewardAgent`（痛觉/奖励）和 `EndocrineAgent`（激素），但不影响情绪基线（`valence`）和沉默意愿（`silence_willingness`）。

**改动**（`limbic_agent.py`）：

1. **修改 `register_subscriptions()`**：
   - 追加订阅 `BOND_PROGRESS` → `_on_bond_progress`
   - 追加订阅 `BOND_REGRESS` → `_on_bond_regress`

2. **新增 `_on_bond_progress()` handler**：
   - 效价提升：`min(0.10, intensity * 0.12)`
   - 通过 `EmotionPersistence.persist()` 写入情绪持久化
   - 通过 `silence_engine.update_emotion(emotion_tag="JOY", ...)` 降低沉默意愿
   - 写入 `last_bond_progress_effect` 状态快照（调试用）

3. **新增 `_on_bond_regress()` handler**：
   - 效价降低：`min(0.08, intensity * 0.10)`
   - 通过 `EmotionPersistence.persist()` 写入情绪持久化
   - 通过 `apply_body_sense_penalty()` 提升沉默意愿
   - 写入 `last_bond_regress_effect` 状态快照（调试用）

**改动**（`http_hooks.py`）：
- subscriptions 计数 7 → 9（LimbicAgent 新增 2 个订阅）

**情绪影响量化**：

| 事件 | 效价变化 | 沉默意愿变化 | 说明 |
|---|---|---|---|
| `BOND_PROGRESS`（intensity=0.3） | +0.036 | 降低（JOY 更新） | 普通关系进展 |
| `BOND_PROGRESS`（intensity=0.7，stage升级） | +0.084 | 明显降低 | Stage 跃迁 |
| `BOND_REGRESS`（intensity=0.4） | -0.04 | +0.024（惩罚） | 普通关系退步 |
| `BOND_REGRESS`（intensity=0.6，stage降级） | -0.06 | +0.036（惩罚） | Stage 降级 |

**完整 BOND 事件影响链（T-25/T-26 → T-28 → T-30）**：
```
BOND_PROGRESS / BOND_REGRESS
  ├── PainRewardAgent（T-24）：pain_level / reward_level
  ├── EndocrineAgent（T-28）：cortisol_level / oxytocin_level
  └── LimbicAgent（T-30）：emotion_valence / silence_willingness
```

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-31**（新增）：`SocialBrainAgent` 接入 `BOND_PROGRESS`/`BOND_REGRESS` 事件（关系变化影响 bond_summary 快照更新频率）
3. **T-32**（新增）：`TribeStateRegistry` 快照定时保存（每 N 分钟强制持久化，补充节流机制的盲区）

---

### 2026-04-09 · 第十二批开发（T-31 / T-32）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/social_brain_agent.py`（T-31：BOND 事件快照更新 handler + 全局 registry）
- `host_brain/tribe/tribe_state.py`（T-32：start_periodic_save / stop_periodic_save）
- `host_brain/app/http_hooks.py`（T-31：subscriptions 计数 9→11；T-32：启动/停止定时保存）

---

#### T-31：SocialBrainAgent 接入 BOND_PROGRESS/BOND_REGRESS 事件 ✅

**问题**：`SocialBrainAgent` 自己会发布 `BOND_PROGRESS`/`BOND_REGRESS` 事件，但不订阅这两个事件，导致 `TribeStateRegistry` 的 `social_brain_agent` 命名空间快照中的 `bond_stage`/`bond_trend` 字段只在 `INTERACTION_RECORDED` 时更新，而不是在 bond 变化时实时更新。

**改动**（`social_brain_agent.py`）：

1. **新增导入**：`from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry`

2. **新增 `state_registry` 字段**：
   - `state_registry: TribeStateRegistry = field(default_factory=get_global_registry)`
   - 使用全局单例，与 LimbicAgent/PainRewardAgent 共享同一个 registry

3. **修改 `register_subscriptions()`**：
   - 追加订阅 `BOND_PROGRESS` → `_on_bond_progress_snapshot`
   - 追加订阅 `BOND_REGRESS` → `_on_bond_regress_snapshot`

4. **新增 `_on_bond_progress_snapshot()` handler**：
   - 从 payload 读取 `bond_stage`/`bond_trend`/`intensity`
   - 读取当前 `social_brain_agent` 命名空间快照，合并更新
   - 写入 `bond_stage`（小写）、`bond_trend`（小写）、`last_bond_progress`（调试信息）
   - 通过 `state_registry.set_state()` 持久化到全局 registry

5. **新增 `_on_bond_regress_snapshot()` handler**：
   - 同上，写入 `last_bond_regress` 调试信息

**改动**（`http_hooks.py`）：
- subscriptions 计数 9 → 11（SocialBrainAgent 新增 BOND_PROGRESS/BOND_REGRESS）

**效果**：`aggregate_vital_signs()` 中的 `bond_stage` 字段现在能在 bond 变化时实时更新，而不是只在对话结束时更新。

---

#### T-32：TribeStateRegistry 快照定时保存 ✅

**问题**：T-29 的节流机制（每 10 次 `set_state` 写一次磁盘）在低频写入场景下存在盲区：
- 如果 Agent 长时间不写入状态（如夜间无对话），最新状态可能长时间未落盘
- 进程异常退出时，最后 N 次写入的状态会丢失

**改动**（`tribe_state.py`）：

1. **新增 `start_periodic_save(interval_seconds=300.0)` 方法**：
   - 启动后台守护线程（`daemon=True`），每隔 `interval_seconds` 秒调用 `save_snapshot()`
   - 支持通过 `NATSUME_TRIBE_STATE_SAVE_INTERVAL` 环境变量覆盖间隔（最小 30 秒）
   - 如果已有定时线程在运行，先停止旧线程再启动新线程
   - 线程名：`TribeStateRegistry-PeriodicSave`

2. **新增 `stop_periodic_save()` 方法**：
   - 设置 stop_event，通知定时线程退出
   - 调用 `save_snapshot()` 强制最后一次落盘（确保进程退出前状态完整）

**改动**（`http_hooks.py`）：

- **Startup 阶段**：在 `load_snapshot()` 之后调用 `_registry.start_periodic_save()`
- **Shutdown 阶段**：在停止 TribeEventBus 之后调用 `_get_global_registry_shutdown().stop_periodic_save()`

**持久化时序**：
```
启动 → load_snapshot() → start_periodic_save()
                              ↓ 每 5 分钟
                         save_snapshot()（定时）
                              ↓ 每 10 次 set_state
                         save_snapshot()（节流）
关闭 → stop_periodic_save() → save_snapshot()（最终落盘）
```

**环境变量**：
| 变量名 | 默认值 | 说明 |
|---|---|---|
| `NATSUME_TRIBE_STATE_SNAPSHOT_PATH` | `host_brain/data/tribe_state_snapshot.json` | 持久化文件路径 |
| `NATSUME_TRIBE_STATE_SAVE_INTERVAL` | `300`（秒） | 定时保存间隔，最小 30 秒 |

---

#### 下一步待开发

1. **T-14**：`LanguageSystemAgent`（语言系统解耦，Phase 5 后实施）
2. **T-33**（新增）：`CortexAgent` 接入 `BOND_PROGRESS`/`BOND_REGRESS` 事件（关系变化影响认知评估权重）
3. **T-34**（新增）：`PrefrontalAgent` 接入 `BOND_STAGE_CHANGED` 事件（关系阶段跃迁影响决策策略）

---

### 2026-04-09 · 第十三批开发（T-33 ~ T-42）

**开发者**：AI 配对编程
**涉及文件**：
- `host_brain/tribe/cortex_agent.py`（T-33/T-38）
- `host_brain/tribe/prefrontal_agent.py`（T-34）
- `host_brain/tribe/immune_agent.py`（T-35）
- `host_brain/tribe/hippocampus_agent.py`（T-36）
- `host_brain/tribe/ans_agent.py`（T-37）
- `host_brain/tribe/tribe_state.py`（T-39）
- `host_brain/tribe/pain_reward_agent.py`（T-40）
- `host_brain/tribe/limbic_agent.py`（T-41）
- `host_brain/app/internal_tribe_routes.py`（T-39）
- `host_brain/app/http_hooks.py`（T-42：subscriptions 11→20，新增 3 个 Agent 注册）

---

#### T-33：CortexAgent 接入 BOND_PROGRESS/BOND_REGRESS 事件 ✅

- 新增 `state_registry` 字段（全局单例）
- `register_subscriptions()` 追加 `BOND_PROGRESS` → `_on_bond_progress_cortex`
- `register_subscriptions()` 追加 `BOND_REGRESS` → `_on_bond_regress_cortex`
- 两个 handler 均将 `bond_stage`/`bond_trend`/`intensity` 写入 `cortex_agent` 命名空间快照
- 供 `route_intent()` 在路由决策时参考（关系越亲密，越倾向 chat 路由）

#### T-34：PrefrontalAgent 接入 BOND_STAGE_CHANGED 事件 ✅

- `register_subscriptions()` 追加 `BOND_STAGE_CHANGED` → `_on_bond_stage_changed_growth`
- handler 仅处理**升级**跃迁（`to_idx > from_idx`），调用 `apply_stage_promotion_growth()`
- 将关系进展转化为人格成长动力（开放度提升、信任感增强等）
- 降级跃迁不触发成长（但不回滚已有成长）

#### T-35：ImmuneAgent 接入 TribeEventBus ✅

- 新增 `state_registry` 字段（全局单例）
- `inspect_text()` 在 `BLOCK` 决策时自动发布 `IMMUNE_BLOCK` 事件（含 risk_level/score/reason_codes）
- `register_subscriptions()` 订阅 `INTERACTION_RECORDED` → `_on_interaction_recorded`
- handler 将当前计数器快照（total/allow/report/block/block_rate）写入 `immune_agent` 命名空间

#### T-36：HippocampusAgent 接入 TribeEventBus ✅

- 新增 `state_registry` 字段（全局单例）
- `register_subscriptions()` 订阅 `INTERACTION_RECORDED` → `_on_interaction_recorded`
- handler 将对话元数据（session_id/user_id/emotion_tag/ts_ms）写入 `hippocampus_agent` 命名空间
- 供诊断端点查询最近一次对话的记忆召回上下文

#### T-37：ANSAgent 接入 TribeEventBus ✅

- 新增 `_bond_stage` 字段（记录当前关系阶段，默认 "stranger"）
- `register_subscriptions()` 订阅 `BOND_STAGE_CHANGED` → `_on_bond_stage_changed`
- handler 更新 `_bond_stage` 字段，并通过 `emit_from_thread()` 广播 `INNER_WORLD_TICKED` 事件
- 广播 payload 包含 `bond_stage_changed=True`/`from_stage`/`to_stage`，供 LimbicAgent 感知

#### T-38：CortexAgent.collect_tribe_context() 补充 immune_state ✅

- `result` 初始化新增 `immune_state: {}` 字段
- 第 6 步从 `ImmuneAgent.export_runtime_state()` 读取运行时状态
- 包含 enabled/blocking_enabled/counters/last_inspection 等字段
- 供 `chat_context_builder` 在构建 prompt 时参考安全状态

#### T-39：TribeStateRegistry 新增 get_all_namespaces() + 诊断端点 ✅

**tribe_state.py**：
- 新增 `get_all_namespaces()` 方法：返回所有命名空间快照列表，按 `updated_at_ms` 降序排列

**internal_tribe_routes.py**：
- 新增 `GET /internal/tribe_state/all` 端点（`TribeStateAllResponse`）
- 返回 `total`/`namespaces`/`ts_ms` 字段
- 支持 `X-Internal-Token` 鉴权

#### T-40：PainRewardAgent 新增 LONELINESS_SIGNAL 事件订阅 ✅

- `register_subscriptions()` 追加 `LONELINESS_SIGNAL` → `_on_loneliness_signal`
- handler 仅在 `loneliness > 0.5` 时触发痛觉（低孤独感属于正常状态）
- 痛觉强度公式：`min(0.6, (loneliness - 0.5) * 1.2)`，上限 0.6（避免单次事件过度影响）
- 痛觉来源：`"loneliness"`（已在 `PAIN_SOURCES` 中定义）

#### T-41：LimbicAgent 新增 TASK_SUCCESS/HUMOR_LANDED 事件订阅 ✅

- `register_subscriptions()` 追加 `TASK_SUCCESS` → `_on_task_success_emotion`
- `register_subscriptions()` 追加 `HUMOR_LANDED` → `_on_humor_landed_emotion`
- `_on_task_success_emotion`：效价提升 `+0.04 ~ +0.06`（成就感带来的轻微正向情绪）
- `_on_humor_landed_emotion`：效价提升 `+0.05 ~ +0.08` + 唤醒度提升 `+0.03`（幽默带来的轻松感和活跃感）
- 两个 handler 均通过 `EmotionPersistence` 写入情绪持久化，并更新 `_touch_state` 快照

#### T-42：http_hooks.py 统一更新 ✅

- 新增 3 个 Agent 订阅注册：`immune_agent`（T-35）、`hippocampus_agent`（T-36）、`ans_agent`（T-37）
- subscriptions 计数：11 → 20（新增 9 个订阅）
- 注册顺序：LimbicAgent → PrefrontalAgent → SocialBrainAgent → CortexAgent → PainRewardAgent → EndocrineAgent → ImmuneAgent → HippocampusAgent → ANSAgent

---

**第十三批订阅总览（20 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, **TASK_SUCCESS**, **HUMOR_LANDED** | 9 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, **BOND_STAGE_CHANGED** | 3 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS | 3 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, **BOND_PROGRESS**, **BOND_REGRESS** | 5 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, **LONELINESS_SIGNAL** | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS | 2 |
| **ImmuneAgent** | **INTERACTION_RECORDED** | 1 |
| **HippocampusAgent** | **INTERACTION_RECORDED** | 1 |
| **ANSAgent** | **BOND_STAGE_CHANGED** | 1 |

---

---

### 第十四批（T-43 ~ T-52）✅ commit: c80e153

#### T-43：ANSAgent.emit_inner_world_ticked() 携带 bond_stage 字段 ✅

- `emit_inner_world_ticked()` payload 新增 `bond_stage` 字段
- 供调度器动态调整 tick 间隔（关系越深，tick 越频繁）

#### T-44：ImmuneAgent.export_runtime_state() 补充字段 ✅

- 新增 `risk_level`、`block_rate`、`last_decision` 字段
- 供诊断端点返回更完整的免疫运行时状态

#### T-45：InMemoryTribeEventBus 新增 publish_batch() ✅

- 批量发布接口，减少高频场景锁竞争
- 支持一次调用发布多个事件，内部顺序 dispatch

#### T-46：aggregate_vital_signs() 补充 immune 字段 ✅

- 新增 `immune_block_count`、`immune_block_rate`、`immune_risk_level` 三个字段
- 从 `immune_agent` 命名空间快照读取，供 chat_context_builder 参考安全状态

#### T-47：PrefrontalAgent 订阅 LONELINESS_SIGNAL ✅

- `register_subscriptions()` 追加 `LONELINESS_SIGNAL` → `_on_loneliness_signal`
- 孤独感 > 0.6 触发演化周期，> 0.8 触发 milestone 成长节点

#### T-48：SocialBrainAgent 订阅 TASK_SUCCESS ✅

- `register_subscriptions()` 追加 `TASK_SUCCESS` → `_on_task_success`
- 任务成功写入 `bond_trend_hint=progress` 快照，供 prompt 构建参考

#### T-49：HippocampusAgent.recall() 注入 immune_state ✅

- `recall()` 将 `immune_state` 注入 `session_state`
- 供检索时参考当前安全状态，避免在高风险状态下召回敏感记忆

#### T-50：TribeStateRegistry 新增 get_state_by_family() ✅

- 按族批量查询方法，返回指定 family 下所有命名空间快照
- 供 `/internal/tribe_state/family/{family}` 端点使用

#### T-51：LimbicAgent 订阅 LONELINESS_SIGNAL ✅

- `register_subscriptions()` 追加 `LONELINESS_SIGNAL` → `_on_loneliness_signal_emotion`
- 孤独感 > 0.5 触发情绪效价下降（-0.03 ~ -0.06），写入 EmotionPersistence

#### T-52：http_hooks.py 第十四批更新 ✅

- 新增 `register_all_subscriptions()` 统一注册入口
- subscriptions 计数：20 → 33（新增 13 个订阅）

---

**第十四批订阅总览（33 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, **LONELINESS_SIGNAL** | 10 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, **LONELINESS_SIGNAL** | 4 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, **TASK_SUCCESS** | 4 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS | 5 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS | 2 |
| ImmuneAgent | INTERACTION_RECORDED | 1 |
| HippocampusAgent | INTERACTION_RECORDED | 1 |
| ANSAgent | BOND_STAGE_CHANGED | 1 |

---

### 第十五批（T-53 ~ T-62）✅ commit: 9549cac

#### T-53：ANSAgent 新增 emit_loneliness_signal() ✅

- 新增 `emit_loneliness_signal()` 方法，供 `inner_world_skill` 检测高孤独感时调用
- payload 包含 `loneliness`、`user_id`、`session_id`、`bond_stage` 字段
- 孤独感值 clamp 到 [0.0, 1.0]

#### T-54：CortexAgent.collect_tribe_context() 补充 bond_trend_hint ✅

- 从 `social_brain_agent` 命名空间快照读取 `bond_trend_hint`
- 注入 tribe_context，供 prompt 构建时参考关系趋势

#### T-55：internal_tribe_routes 新增按族诊断端点 ✅

- 新增 `GET /internal/tribe_state/family/{family}` 端点
- 返回指定族（如 `cognitive`/`emotional`/`social`）下所有命名空间快照
- 支持 `X-Internal-Token` 鉴权

#### T-56：InMemoryTribeEventBus 新增 unsubscribe() ✅

- 支持动态取消订阅，传入事件类型和 handler 引用
- 线程安全，使用 `_lock` 保护订阅列表

#### T-57：EndocrineAgent 订阅 LONELINESS_SIGNAL ✅

- `register_subscriptions()` 追加 `LONELINESS_SIGNAL` → `_on_loneliness_signal`
- 孤独感 > 0.5 触发 cortisol 轻微上升，上限 +0.06（避免单次事件过度影响）

#### T-58：CortexAgent 订阅 IMMUNE_BLOCK ✅

- `register_subscriptions()` 追加 `IMMUNE_BLOCK` → `_on_immune_block`
- 拦截时写入 `cortex_agent` 快照：`last_immune_block_reason`、`last_immune_block_ts_ms`、`last_immune_risk_level`

#### T-59：aggregate_vital_signs() 补充 bond_trend_hint ✅

- 从 `social_brain_agent` 命名空间读取 `bond_trend_hint`
- 写入 `aggregate_vital_signs()` 返回值，供 chat_context_builder 参考

#### T-60：SocialBrainAgent 订阅 LONELINESS_SIGNAL ✅

- `register_subscriptions()` 追加 `LONELINESS_SIGNAL` → `_on_loneliness_signal`
- 孤独感 > 0.6 写入 `regress_risk`/`high_loneliness` hint，供关系管理参考

#### T-61：HippocampusAgent 订阅 LONELINESS_SIGNAL ✅

- `register_subscriptions()` 追加 `LONELINESS_SIGNAL` → `_on_loneliness_signal`
- 写入 `last_loneliness_level`、`ts_ms`、`bond_stage` 快照到 TribeStateRegistry

#### T-62：http_hooks.py 第十五批更新 ✅

- subscriptions 计数：33 → 39（新增 6 个订阅）
- 新增：EndocrineAgent(LONELINESS_SIGNAL)、CortexAgent(IMMUNE_BLOCK)、SocialBrainAgent(LONELINESS_SIGNAL)、HippocampusAgent(LONELINESS_SIGNAL)

---

**第十五批订阅总览（39 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 10 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL | 4 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, **LONELINESS_SIGNAL** | 5 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS, **IMMUNE_BLOCK** | 6 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS, **LONELINESS_SIGNAL** | 3 |
| ImmuneAgent | INTERACTION_RECORDED | 1 |
| HippocampusAgent | INTERACTION_RECORDED, **LONELINESS_SIGNAL** | 2 |
| ANSAgent | BOND_STAGE_CHANGED | 1 |

---

### 第十六批（T-63 ~ T-72）✅ commit: 874a01d

#### T-63：ANSAgent 新增 emit_task_success() ✅

- 新增 `emit_task_success()` 方法，供 skill 完成任务时发布 `TASK_SUCCESS` 事件
- payload 包含 `task_type`、`intensity`、`user_id`、`session_id`、`bond_stage` 字段
- intensity clamp 到 [0.0, 1.0]，默认 0.35

#### T-64：PainRewardAgent 新增 get_loneliness_level() ✅

- 新增 `get_loneliness_level()` 方法，返回当前孤独感强度（0.0 ~ 1.0）
- 从内部状态读取 `last_loneliness_level`，线程安全

#### T-65：CortexAgent 订阅 LONELINESS_SIGNAL ✅

- `register_subscriptions()` 追加 `LONELINESS_SIGNAL` → `_on_loneliness_signal`
- 写入 `last_loneliness_level`、`high_loneliness_flag` 到 `cortex_agent` 快照

#### T-66：TribeEventBus 新增 get_subscriber_count() ✅

- 新增 `get_subscriber_count()` 方法，返回各事件类型的订阅数量字典
- 线程安全，供诊断端点查询

#### T-67：internal_tribe_routes 新增订阅诊断端点 ✅

- 新增 `GET /internal/tribe_event/subscribers` 端点
- 返回各事件类型订阅数量，支持 `X-Internal-Token` 鉴权

#### T-68：ImmuneAgent 新增 reset_counters() ✅

- 新增 `reset_counters()` 方法，支持运维手动重置计数器
- 返回重置前的计数器快照（`counters_before_reset`、`block_rate_before_reset`、`reset_at_ms`）

#### T-69：EndocrineAgent 订阅 TASK_SUCCESS ✅

- `register_subscriptions()` 追加 `TASK_SUCCESS` → `_on_task_success`
- 任务成功触发 cortisol 轻微下降，上限 -0.05（成就感带来的压力释放）

#### T-70：TribeStateRegistry 新增 clear_namespace() ✅

- 新增 `clear_namespace(namespace)` 方法，支持运维清除单个命名空间
- 线程安全，清除后从 `_states` 字典中移除对应键

#### T-71：PrefrontalAgent 订阅 IMMUNE_BLOCK ✅

- `register_subscriptions()` 追加 `IMMUNE_BLOCK` → `_on_immune_block`
- 高风险拦截时触发安全审查成长周期（`safety_review` milestone）

#### T-72：http_hooks.py 第十六批更新 ✅

- subscriptions 计数：39 → 42（新增 3 个订阅）
- 新增：CortexAgent(LONELINESS_SIGNAL)、EndocrineAgent(TASK_SUCCESS)、PrefrontalAgent(IMMUNE_BLOCK)

---

**第十六批订阅总览（42 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 10 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL, **IMMUNE_BLOCK** | 5 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, LONELINESS_SIGNAL | 5 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS, IMMUNE_BLOCK, **LONELINESS_SIGNAL** | 7 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS, LONELINESS_SIGNAL, **TASK_SUCCESS** | 4 |
| ImmuneAgent | INTERACTION_RECORDED | 1 |
| HippocampusAgent | INTERACTION_RECORDED, LONELINESS_SIGNAL | 2 |
| ANSAgent | BOND_STAGE_CHANGED | 1 |

---

### 第十七批（T-73 ~ T-82）✅ commit: 8dd57b4

#### T-73：internal_tribe_routes 新增 POST /internal/immune/reset ✅

- 新增 `POST /internal/immune/reset` 运维重置端点
- 调用 `ImmuneAgent.reset_counters()`，返回重置前快照（`counters_before_reset`/`block_rate_before_reset`/`reset_at_ms`）
- 支持 `X-Internal-Token` 鉴权

#### T-74：internal_tribe_routes 新增 DELETE /internal/tribe_state/{namespace} ✅

- 新增 `DELETE /internal/tribe_state/{namespace}` 清除命名空间端点
- 调用 `TribeStateRegistry.clear_namespace()`，返回被清除的命名空间名称和时间戳
- 支持 `X-Internal-Token` 鉴权

#### T-75：ANSAgent 新增 emit_humor_landed() ✅

- 新增 `emit_humor_landed()` 方法，供 skill 幽默成功时发布 `HUMOR_LANDED` 事件
- payload 包含 `humor_type`、`intensity`、`user_id`、`session_id`、`bond_stage` 字段

#### T-76：CortexAgent.collect_tribe_context() 补充 loneliness_level ✅

- 从 `pain_reward_agent` 命名空间快照读取 `last_loneliness_level`
- 注入 tribe_context，供 prompt 构建时参考孤独感状态

#### T-77：TribeStateRegistry 新增 list_namespaces() ✅

- 新增 `list_namespaces()` 方法，返回所有已注册命名空间名称列表（`list[str]`）
- 线程安全，供诊断端点和 `get_state_summary()` 使用

#### T-78：SocialBrainAgent 订阅 IMMUNE_BLOCK ✅

- `register_subscriptions()` 追加 `IMMUNE_BLOCK` → `_on_immune_block`
- 拦截时将 `bond_trend_hint` 设为 `regress_risk`，写入 `social_brain_agent` 快照

#### T-79：LimbicAgent 订阅 IMMUNE_BLOCK ✅

- `register_subscriptions()` 追加 `IMMUNE_BLOCK` → `_on_immune_block_emotion`
- 拦截时触发轻微负面情绪偏置（valence -0.02 ~ -0.04），写入 EmotionPersistence

#### T-80：aggregate_vital_signs() 补充 loneliness_level ✅

- 从 `pain_reward_agent` 命名空间读取 `last_loneliness_level`
- 写入 `aggregate_vital_signs()` 返回值，供 chat_context_builder 参考孤独感状态

#### T-81：ImmuneAgent 订阅 BOND_STAGE_CHANGED ✅

- `register_subscriptions()` 追加 `BOND_STAGE_CHANGED` → `_on_bond_stage_changed_immune`
- 关系阶段跃迁时动态调整拦截阈值：
  - stranger/acquaintance：0.55（默认）
  - friend：0.60
  - close_friend：0.65
  - intimate：0.70（high/critical 风险始终拦截）
- 阈值变更写入 `immune_agent` 快照（`bond_stage`/`bond_stage_threshold`）

#### T-82：http_hooks.py 第十七批更新 ✅

- subscriptions 计数：42 → 47（新增 5 个订阅）
- 新增：SocialBrainAgent(IMMUNE_BLOCK)、LimbicAgent(IMMUNE_BLOCK)、ImmuneAgent(BOND_STAGE_CHANGED)、ANSAgent emit 方法扩展

---

**第十七批订阅总览（47 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL, **IMMUNE_BLOCK** | 11 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 5 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, LONELINESS_SIGNAL, **IMMUNE_BLOCK** | 6 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS, IMMUNE_BLOCK, LONELINESS_SIGNAL | 7 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS, LONELINESS_SIGNAL, TASK_SUCCESS | 4 |
| ImmuneAgent | INTERACTION_RECORDED, **BOND_STAGE_CHANGED** | 2 |
| HippocampusAgent | INTERACTION_RECORDED, LONELINESS_SIGNAL | 2 |
| ANSAgent | BOND_STAGE_CHANGED | 1 |

---

---

## 第十八批开发留痕（T-83 ~ T-92）

#### T-83：internal_tribe_routes 新增 GET /internal/tribe_state/namespaces ✅

- 新增 `GET /internal/tribe_state/namespaces` 端点
- 调用 `TribeStateRegistry.list_namespaces()` 返回所有已注册命名空间名称列表
- 响应格式：`{"namespaces": [...], "count": N}`

#### T-84：ANSAgent 新增 emit_bond_progress() ✅

- 新增 `emit_bond_progress(delta: float, reason: str)` 方法
- 发布 `BOND_PROGRESS` 事件（delta > 0）或 `BOND_REGRESS` 事件（delta < 0）
- 供 skill 侧（bond_skill / interaction_skill）触发关系进展事件

#### T-85：HippocampusAgent 订阅 TASK_SUCCESS ✅

- `register_subscriptions()` 追加 `TASK_SUCCESS` → `_on_task_success`
- 任务成功时将 `task_type`、`task_score`、`timestamp` 写入 `hippocampus_agent` 快照
- 累计 `task_success_count` 计数

#### T-86：EndocrineAgent 订阅 IMMUNE_BLOCK ✅

- `register_subscriptions()` 追加 `IMMUNE_BLOCK` → `_on_immune_block_endocrine`
- 拦截时 cortisol 轻微上升（+0.03 ~ +0.06），写入 `endocrine_agent` 快照
- 触发 `ENDOCRINE_UPDATED` 事件通知下游

#### T-87：TribeStateRegistry 新增 get_state_summary() ✅

- 新增 `get_state_summary()` 方法，返回所有命名空间的轻量摘要
- 每个命名空间返回：`namespace`、`key_count`、`last_updated`、`top_keys`（前5个键名）
- 线程安全，供诊断端点和监控使用

#### T-88：CortexAgent 订阅 TASK_SUCCESS ✅

- `register_subscriptions()` 追加 `TASK_SUCCESS` → `_on_task_success`
- 任务成功时将 `last_task_type`、`last_task_score`、`task_success_count` 写入 `cortex_agent` 快照
- 供 `collect_tribe_context()` 注入 prompt 构建时参考

#### T-89：PainRewardAgent 订阅 HUMOR_LANDED ✅

- `register_subscriptions()` 追加 `HUMOR_LANDED` → `_on_humor_landed`
- 幽默成功时触发奖励：`reward_signal` +0.05 ~ +0.10，写入 `pain_reward_agent` 快照
- 累计 `humor_success_count` 计数

#### T-90：aggregate_vital_signs() 补充 high_loneliness_flag ✅

- 从 `pain_reward_agent` 命名空间读取 `last_loneliness_level`
- 当 `loneliness_level >= 0.7` 时，`high_loneliness_flag = True`，否则 `False`
- 写入 `aggregate_vital_signs()` 返回值，供 chat_context_builder 参考

#### T-91：SocialBrainAgent 订阅 HUMOR_LANDED ✅

- `register_subscriptions()` 追加 `HUMOR_LANDED` → `_on_humor_landed`
- 幽默成功时将 `bond_trend_hint` 设为 `progress`，写入 `social_brain_agent` 快照
- 同时更新 `last_humor_landed_at` 时间戳

#### T-92：http_hooks.py 第十八批更新 ✅

- subscriptions 计数：47 → 53（新增 6 个订阅）
- 新增：HippocampusAgent(TASK_SUCCESS)、EndocrineAgent(IMMUNE_BLOCK)、CortexAgent(TASK_SUCCESS)、PainRewardAgent(HUMOR_LANDED)、SocialBrainAgent(HUMOR_LANDED)、ANSAgent emit_bond_progress

---

**第十八批订阅总览（53 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 11 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 5 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, LONELINESS_SIGNAL, IMMUNE_BLOCK, **HUMOR_LANDED** | 7 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS, IMMUNE_BLOCK, LONELINESS_SIGNAL, **TASK_SUCCESS** | 8 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS, LONELINESS_SIGNAL, TASK_SUCCESS, **IMMUNE_BLOCK** | 5 |
| ImmuneAgent | INTERACTION_RECORDED, BOND_STAGE_CHANGED | 2 |
| HippocampusAgent | INTERACTION_RECORDED, LONELINESS_SIGNAL, **TASK_SUCCESS** | 3 |
| ANSAgent | BOND_STAGE_CHANGED | 1 |

---

#### 下一步待开发（第十九批 T-93 ~ T-102）

1. **T-93**：`inner_world_skill` 调用 `ANSAgent.emit_loneliness_signal()`，孤独感超阈值时触发事件
2. **T-94**：`chat_skill` / `interaction_skill` 调用 `ANSAgent.emit_task_success()`，对话任务完成时触发
3. **T-95**：`humor_skill` 调用 `ANSAgent.emit_humor_landed()`，幽默成功时触发事件
4. **T-96**：`bond_skill` 调用 `ANSAgent.emit_bond_progress()`，关系进展时触发事件
5. **T-97**：`chat_context_builder` 消费 `high_loneliness_flag`，孤独感高时调整 prompt 语气
6. **T-98**：`chat_context_builder` 消费 `bond_trend_hint`，关系趋势影响话题选择
7. **T-99**：`chat_context_builder` 消费 `immune_state`，免疫拦截状态影响 prompt 谨慎度
8. **T-100**：`prompt_builder` 新增 `loneliness_hint` 注入，孤独感高时增加陪伴感表达
9. **T-101**：`TribeStateRegistry` 新增 `get_state_summary()` 端点接入 `internal_tribe_routes`
10. **T-102**：`http_hooks.py` 第十九批计数更新 + 文档留痕

---

## 第十九批开发留痕（T-93 ~ T-102）

#### T-93：inner_world_skill 调用 emit_loneliness_signal() ✅

- 修改 `host_brain/skills/impl/inner_world_skill.py` 的 `execute_inner_world_tick()`
- 在 `emit_inner_world_ticked()` 之后，检测 `loneliness >= 0.5` 时调用 `get_ans_agent().emit_loneliness_signal()`
- 阈值 0.5：超过此值即认为孤独感显著，触发 LONELINESS_SIGNAL 事件流向各族 Agent

#### T-94：chat_handler 调用 emit_task_success() ✅

- 修改 `host_brain/app/chat_handler.py` 的 `_unified_chat_sse_generator()`
- 在 `top_agent_sse_generator` 迭代过程中，检测 `phase=COMMIT done=True` 时调用 `emit_task_success(session_id, task_type="chat", intensity=0.35)`
- 使用 `_chat_committed` 标志防止重复触发

#### T-95：emotion_executor 调用 emit_humor_landed() ✅

- 修改 `host_brain/skills/impl/emotion_executor.py` 的 `_execute_bond_update()`
- 当 `event_type == "humor_landed"` 时，调用 `get_ans_agent().emit_humor_landed(humor_type, intensity)`
- 在写入 bond_update_queue 之前触发，确保事件先于积分更新

#### T-96：bond_dynamics 调用 emit_bond_progress() ✅

- 修改 `host_brain/bond/bond_dynamics.py` 的 `apply_update()`
- 在 `BondStageEngine.apply_event()` 成功后，当 `score_delta > 0` 时调用 `get_ans_agent().emit_bond_progress(delta, reason, bond_stage)`
- 非致命：emit 失败不影响 Bond 积分写入

#### T-97：chat_context_builder 消费 high_loneliness_flag ✅

- 修改 `host_brain/app/chat_context_builder.py` 的 CortexAgent Supervisor 收口段
- 读取 `_vital_signs["high_loneliness_flag"]`，为 True 时注入 `runtime_payload["loneliness_hint"]`
- 包含 `high_loneliness=True`、`loneliness_level`、`suggest="主动关怀，增加陪伴感表达"`

#### T-98：chat_context_builder 消费 bond_trend_hint ✅

- 同上，读取 `_vital_signs["bond_trend_hint"]`，注入 `runtime_payload["bond_trend_hint"]`
- `progress` → `bond_tone_adjust="warmer"`；`regress_risk/regress` → `bond_tone_adjust="cautious"`

#### T-99：chat_context_builder 消费 immune_risk_level ✅

- 同上，读取 `_vital_signs["immune_risk_level"]` 和 `immune_block_count`
- 当 risk_level 为 medium/high/critical 或 block_count >= 3 时，注入 `runtime_payload["immune_caution"]`
- 包含 `risk_level`、`block_count`、`suggest="回复需更加谨慎，避免触碰敏感话题"`

#### T-100：prompt_builder 新增 loneliness_hint 注入 ✅

- 修改 `host_brain/app/prompt_builder.py` 的 Phase XIX tribe_vital_signs 注入区域
- 在 `_tv_hedonic_active` 注入块之后，追加 `loneliness_hint` 消费块
- `high_loneliness=True` 时注入 `tribe_loneliness_level` 和 `tribe_loneliness_suggest` 到 system prompt

#### T-101：internal_tribe_routes 新增 GET /internal/tribe_state/summary ✅

- 在 `host_brain/app/internal_tribe_routes.py` 中新增 `TribeStateSummaryResponse` 模型
- 新增 `GET /internal/tribe_state/summary` 端点，调用 `registry.get_state_summary()`
- 返回所有命名空间的轻量摘要（namespace/writer/updated_at_ms/state_keys），适合监控大盘

#### T-102：http_hooks.py 第十九批更新 ✅

- 订阅计数维持 53（第十九批为 skill 侧 emit 接入 + context 消费，无新增 Agent 订阅）
- 补充注释说明 T-93~T-101 的 skill 侧接入情况

---

**第十九批订阅总览（53 个，无新增）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 11 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 5 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, LONELINESS_SIGNAL, IMMUNE_BLOCK, HUMOR_LANDED | 7 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS, IMMUNE_BLOCK, LONELINESS_SIGNAL, TASK_SUCCESS | 8 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS, LONELINESS_SIGNAL, TASK_SUCCESS, IMMUNE_BLOCK | 5 |
| ImmuneAgent | INTERACTION_RECORDED, BOND_STAGE_CHANGED | 2 |
| HippocampusAgent | INTERACTION_RECORDED, LONELINESS_SIGNAL, TASK_SUCCESS | 3 |
| ANSAgent | BOND_STAGE_CHANGED | 1 |

**第十九批新增 skill 侧 emit 接入**：

| 文件 | emit 方法 | 触发条件 |
|---|---|---|
| `inner_world_skill.py` | `emit_loneliness_signal()` | loneliness >= 0.5 |
| `chat_handler.py` | `emit_task_success()` | COMMIT done=True |
| `emotion_executor.py` | `emit_humor_landed()` | event_type == "humor_landed" |
| `bond_dynamics.py` | `emit_bond_progress()` | score_delta > 0 |

**第十九批新增 context 消费字段**：

| 字段 | 消费位置 | 作用 |
|---|---|---|
| `high_loneliness_flag` | chat_context_builder | 注入 loneliness_hint |
| `bond_trend_hint` | chat_context_builder | 注入 bond_tone_adjust |
| `immune_risk_level` | chat_context_builder | 注入 immune_caution |
| `loneliness_hint` | prompt_builder | 注入 tribe_loneliness_suggest |

---

---

## 第二十批开发留痕（T-103 ~ T-112）

#### T-103：LanguageSystemAgent 骨架 ✅

- 新建 `host_brain/tribe/language_system_agent.py`，实现 `LanguageSystemAgent` 单例
- 注册订阅：`EMOTION_STATE_CHANGED`、`BOND_STAGE_CHANGED`、`LONELINESS_SIGNAL`
- 内部状态：`_current_emotion_tag`、`_current_bond_stage`、`_loneliness_level`
- 事件处理：`_on_emotion_changed()` / `_on_bond_stage_changed()` / `_on_loneliness_signal()`

#### T-104：LanguageSystemAgent.get_language_style() ✅

- 新增 `get_language_style()` 方法，根据 `bond_stage` / `emotion_tag` / `loneliness_level` 返回语言风格建议字典
- 返回字段：`tone`（语气）、`formality`（正式度）、`warmth`（温暖度）、`style_hint`（自然语言风格提示）
- bond_stage 越高 → 越亲密随意；emotion_tag 为负向 → 语气更温柔；loneliness 高 → 增加陪伴感措辞

#### T-105：chat_context_builder 接入 get_language_style() ✅

- 修改 `host_brain/app/chat_context_builder.py`，在 vital_signs 消费段之后调用 `get_language_system_agent().get_language_style()`
- 注入 `runtime_payload["language_style"]`，包含 tone / formality / warmth / style_hint 四字段

#### T-106：prompt_builder 消费 language_style ✅

- 修改 `host_brain/app/prompt_builder.py`，在 tribe_vital_signs 注入区域新增 `language_style` 消费块
- 将 `style_hint` 注入 system prompt 的语言风格指令段，影响回复的语气/措辞/句式

#### T-107：bond_tone_adjust 在 prompt_builder 中消费 ✅

- 同上，在 `language_style` 消费块之后追加 `bond_tone_adjust` 消费块
- `warmer` → 注入"语气更温柔亲近，多用第一人称亲密表达"指令
- `cautious` → 注入"语气更谨慎克制，避免过度亲密表达"指令

#### T-108：immune_caution 在 prompt_builder 中消费 ✅

- 同上，追加 `immune_caution` 消费块
- `risk_level` 为 medium/high/critical 时，注入"当前话题敏感，回复需更加谨慎，避免触碰敏感内容"提示到 system prompt

#### T-109：SomaticAgent 骨架 ✅

- 新建 `host_brain/tribe/somatic_agent.py`，实现 `SomaticAgent` 单例（躯干族 Phase 6 预备）
- 注册订阅：`BODY_SENSE_UPDATED`、`LONELINESS_SIGNAL`
- 内部状态：`_fatigue_level`、`_drowsiness_level`、`_loneliness_level`
- 事件处理：`_on_body_sense_updated()` / `_on_loneliness_signal()`

#### T-110：PeripheralAgent 骨架 ✅

- 新建 `host_brain/tribe/peripheral_agent.py`，实现 `PeripheralAgent` 单例（躯干族 Phase 6 预备）
- 注册订阅：`EMOTION_STATE_CHANGED`、`IMMUNE_BLOCK`
- 内部状态：`_emotion_tag`、`_immune_block_count`
- 事件处理：`_on_emotion_changed()` / `_on_immune_block()`

#### T-111：TribeStateRegistry.export_snapshot() ✅

- 修改 `host_brain/tribe/tribe_state.py`，在 `stop_periodic_save()` 之后新增 `export_snapshot()` 方法
- 支持自定义导出路径；默认在 `_state_path` 同目录生成带时间戳文件（`tribe_state_export_{ts_ms}.json`）
- 导出格式：`{ version, export_type, exported_at_ms, namespace_count, namespaces }` 完整快照
- 原子写入（先写 `.tmp` 再 `replace`），返回导出文件绝对路径字符串

#### T-112：http_hooks.py 第二十批更新 ✅

- 在 Agent 注册循环中新增 `language_system_agent`（T-103）、`somatic_agent`（T-109）、`peripheral_agent`（T-110）三个 Agent
- 订阅计数从 53 更新为 60（LanguageSystemAgent +3、SomaticAgent +2、PeripheralAgent +2 = +7）
- 更新注释说明第二十批新增 Agent 情况

---

**第二十批订阅总览（60 个，新增 7 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 11 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 5 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, LONELINESS_SIGNAL, IMMUNE_BLOCK, HUMOR_LANDED | 7 |
| CortexAgent | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS, IMMUNE_BLOCK, LONELINESS_SIGNAL, TASK_SUCCESS | 8 |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS, LONELINESS_SIGNAL, TASK_SUCCESS, IMMUNE_BLOCK | 5 |
| ImmuneAgent | INTERACTION_RECORDED, BOND_STAGE_CHANGED | 2 |
| HippocampusAgent | INTERACTION_RECORDED, LONELINESS_SIGNAL, TASK_SUCCESS | 3 |
| ANSAgent | BOND_STAGE_CHANGED | 1 |
| **LanguageSystemAgent** *(新增 T-103)* | EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL | **3** |
| **SomaticAgent** *(新增 T-109)* | BODY_SENSE_UPDATED, LONELINESS_SIGNAL | **2** |
| **PeripheralAgent** *(新增 T-110)* | EMOTION_STATE_CHANGED, IMMUNE_BLOCK | **2** |

**第二十批新增 prompt 消费字段**：

| 字段 | 消费位置 | 作用 |
|---|---|---|
| `language_style` | chat_context_builder → prompt_builder | 语言风格指令（tone/formality/warmth/style_hint） |
| `bond_tone_adjust` | prompt_builder | warmer/cautious 语气调整指令 |
| `immune_caution` | prompt_builder | 谨慎度提示注入 system prompt |

**第二十批新增运维能力**：

| 能力 | 实现位置 | 说明 |
|---|---|---|
| `export_snapshot()` | TribeStateRegistry | 完整快照导出为 JSON，支持自定义路径，原子写入 |

---

## 第二十一批开发留痕（T-113 ~ T-122）

#### T-113：edge_client runtime/bootstrap.py 初始化 EdgeTribeBridge 单例 ✅

- 修改 `edge_client/runtime/bootstrap.py`，在 lifespan 启动阶段调用 `EdgeTribeRegistry.get_instance().initialize()`
- EdgeTribeBridge 从环境变量自动构建（`EDGE_TRIBE_HOST_BASE_URL` / `EDGE_RPC_HOST`）
- 初始化后 bridge 自动注入到 SomaticAgent / PeripheralAgent / ChannelAgent / VoiceAgent 四个 Agent
- 降级安全：初始化失败时仅记录 warning，不阻断 edge_client 启动

#### T-114：runtime/emotion_inference.py 调用 SomaticAgent 上报 EMOTION_INFERENCE_UPDATED ✅

- 修改 `edge_client/runtime/emotion_inference.py` 的 `infer_asr_emotion()` 函数
- 推断完成后，通过 `EdgeTribeRegistry.get_instance().somatic.publish_emotion_inference_updated()` 上报
- 上报字段：`emotion_tag`、`emotion_intensity`、`mood_state`、`emotion_source`、`emotion_terms`
- 非致命：上报失败不影响推断结果返回

#### T-115：gateway/router.py 调用 ChannelAgent 上报 CHANNEL_MESSAGE_RECEIVED ✅

- 修改 `edge_client/gateway/router.py` 的消息路由主路径
- 消息路由成功/失败后，调用 `EdgeTribeRegistry.get_instance().channel.on_message_received()` 上报
- 上报字段：`platform`、`conversation_id`、`msg_id`、`msg_type`、`is_group`、`ts_ms`、`routed_success`、`cached`
- 网关降级时调用 `on_gateway_degraded()` 上报 `GATEWAY_DEGRADED` 事件给 ANSAgent

#### T-116：gateway/bootstrap.py 调用 PeripheralAgent 上报平台连接/断线事件 ✅

- 修改 `edge_client/gateway/bootstrap.py` 的平台适配器初始化和关闭路径
- 平台适配器成功连接时，调用 `EdgeTribeRegistry.get_instance().peripheral.publish_platform_connected()`
- 平台适配器断线/关闭时，调用 `publish_platform_disconnected()`
- 上报字段：`platform`（平台名称）、`detail`（可选详情）

#### T-117：runtime/voice_pipeline.py 调用 VoiceAgent 上报 VOICE_PIPELINE_STARTED/ENDED ✅

- 修改 `edge_client/runtime/voice_pipeline.py` 的语音管线启动和结束路径
- 管线启动时调用 `EdgeTribeRegistry.get_instance().voice.publish_pipeline_started()`
- 管线结束时调用 `publish_pipeline_ended()`
- 上报字段：`request_id`、`session_id`、`ts_ms`、`status`、`reason_code`、`stage`

#### T-118：host_brain CortexAgent 新增订阅 PLATFORM_CONNECTED/DISCONNECTED ✅

- 修改 `host_brain/tribe/cortex_agent.py` 的 `register_subscriptions()` 方法
- 新增订阅：`PLATFORM_CONNECTED` → `_on_platform_connected()`
- 新增订阅：`PLATFORM_DISCONNECTED` → `_on_platform_disconnected()`
- 处理逻辑：更新 `_vital_signs["platform_status"]`，平台断线时触发谨慎模式标记

#### T-119：host_brain ANSAgent 新增订阅 VOICE_PIPELINE_STARTED/ENDED ✅

- 修改 `host_brain/tribe/ans_agent.py` 的 `register_subscriptions()` 方法
- 新增订阅：`VOICE_PIPELINE_STARTED` → `_on_voice_pipeline_started()`
- 新增订阅：`VOICE_PIPELINE_ENDED` → `_on_voice_pipeline_ended()`
- 处理逻辑：更新 `_voice_active` 标志，语音活跃时抑制主动对话调度

#### T-120：host_brain ANSAgent 新增订阅 CHANNEL_MESSAGE_RECEIVED ✅

- 同上，新增订阅：`CHANNEL_MESSAGE_RECEIVED` → `_on_channel_message_received()`
- 处理逻辑：更新 `_last_channel_activity_ms`，用于主动对话空闲阈值判断
- 与 ActivityMonitor 的 `_last_interaction_ms` 形成双重活跃度感知

#### T-121：edge_client tribe/registry.py 完善为 EdgeTribeRegistry 单例管理 ✅

- 重写 `edge_client/tribe/registry.py`，实现 `EdgeTribeRegistry` 完整单例类
- 持有 bridge / somatic / peripheral / channel / voice 五个对象的单例引用
- 提供 `initialize(bridge=None)` 统一初始化入口（幂等，重复调用安全）
- 提供 `reinitialize()` 强制重新初始化（用于测试/热更新）
- 提供 `describe()` 状态摘要，供健康检查端点调用
- 新增 `get_edge_tribe_registry()` 全局访问函数，向后兼容保留 `build_edge_tribe_bridge()`
- 同步更新 `edge_client/tribe/__init__.py` 导出 `EdgeTribeRegistry` 和 `get_edge_tribe_registry`

#### T-122：http_hooks.py 第二十一批更新 ✅

- 订阅计数从 60 更新为 65（ANSAgent +3、CortexAgent +2 = +5）
- 更新注释说明第二十一批新增订阅情况

---

**第二十一批订阅总览（65 个，新增 5 个）**：

| Agent | 订阅事件 | 数量 |
|---|---|---|
| LimbicAgent | BODY_SENSE_UPDATED, INNER_WORLD_TICKED, CONVERSATION_STARTED, CONVERSATION_ENDED, EMOTION_INFERENCE_UPDATED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 11 |
| PrefrontalAgent | EVOLUTION_CYCLE_TRIGGERED, INTERACTION_RECORDED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL, IMMUNE_BLOCK | 5 |
| SocialBrainAgent | INTERACTION_RECORDED, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, LONELINESS_SIGNAL, IMMUNE_BLOCK, HUMOR_LANDED | 7 |
| **CortexAgent** | SILENCE_WILLINGNESS_CHANGED, EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, BOND_PROGRESS, BOND_REGRESS, IMMUNE_BLOCK, LONELINESS_SIGNAL, TASK_SUCCESS, **PLATFORM_CONNECTED, PLATFORM_DISCONNECTED** | **10** |
| PainRewardAgent | INTERACTION_RECORDED, EMOTION_UPDATED, IMMUNE_BLOCK, BOND_PROGRESS, BOND_REGRESS, TASK_SUCCESS, HUMOR_LANDED, LONELINESS_SIGNAL | 8 |
| EndocrineAgent | BOND_PROGRESS, BOND_REGRESS, LONELINESS_SIGNAL, TASK_SUCCESS, IMMUNE_BLOCK | 5 |
| ImmuneAgent | INTERACTION_RECORDED, BOND_STAGE_CHANGED | 2 |
| HippocampusAgent | INTERACTION_RECORDED, LONELINESS_SIGNAL, TASK_SUCCESS | 3 |
| **ANSAgent** | BOND_STAGE_CHANGED, **VOICE_PIPELINE_STARTED, VOICE_PIPELINE_ENDED, CHANNEL_MESSAGE_RECEIVED** | **4** |
| LanguageSystemAgent | EMOTION_STATE_CHANGED, BOND_STAGE_CHANGED, LONELINESS_SIGNAL | 3 |
| SomaticAgent | BODY_SENSE_UPDATED, LONELINESS_SIGNAL | 2 |
| PeripheralAgent | EMOTION_STATE_CHANGED, IMMUNE_BLOCK | 2 |

**第二十一批新增 edge_client 侧 emit 接入**：

| 文件 | Agent | emit 方法 | 触发条件 |
|---|---|---|---|
| `runtime/bootstrap.py` | EdgeTribeRegistry | `initialize()` | edge_client 启动时 |
| `runtime/emotion_inference.py` | SomaticAgent | `publish_emotion_inference_updated()` | ASR 情绪推断完成 |
| `gateway/router.py` | ChannelAgent | `on_message_received()` / `on_gateway_degraded()` | 消息路由成功/降级 |
| `gateway/bootstrap.py` | PeripheralAgent | `publish_platform_connected/disconnected()` | 平台适配器连接/断线 |
| `runtime/voice_pipeline.py` | VoiceAgent | `publish_pipeline_started/ended()` | 语音管线启停 |

**第二十一批新增 EdgeTribeRegistry 能力**：

| 能力 | 说明 |
|---|---|
| `EdgeTribeRegistry` 单例类 | 统一持有 bridge + 4 个 Agent，线程安全单例 |
| `initialize(bridge=None)` | 幂等初始化，自动从环境变量构建 bridge |
| `describe()` | 状态摘要，供健康检查使用 |
| `get_edge_tribe_registry()` | 全局访问函数 |

---

## 第二十二批实现记录（T-123 ~ T-132）

**目标**：实现 host_brain → edge_client 下行指令链路（EdgeCommandPusher），完成双向 Tribe 事件闭环。

### 已完成任务

#### T-123：edge_client/app/fastapi_app.py 新增 /internal/tribe_command POST 端点 ✅

- 在 `edge_client/app/fastapi_app.py` 中新增 `/internal/tribe_command` POST 端点
- 接收来自 host_brain 的下行指令（`command_type`、`target_agent`、`payload`）
- 根据 `target_agent` 路由到对应的 edge_client Agent 处理方法
- 支持 `emotion_state_command` → `SomaticAgent.on_emotion_state_command()`
- 支持 `gate_control_command` → `ChannelAgent.on_gate_control_command()`
- 内部鉴权：`X-Internal-Token` header 校验（与上行共用 `HOST_INTERNAL_TRIBE_TOKEN`）

#### T-124：新建 host_brain/tribe/edge_command_pusher.py ✅

- 新建 `host_brain/tribe/edge_command_pusher.py`，实现 `EdgeCommandPusher` HTTP 客户端
- 通过 `requests.Session` 向 `edge_client /internal/tribe_command` 推送下行指令
- 提供 `push_emotion_state()` 快捷方法（LimbicAgent 调用，驱动 vts_driver 表情）
- 提供 `push_gate_control()` 快捷方法（CortexAgent 调用，控制 ChannelAgent 门控）
- 提供 `push_command()` 通用方法，支持任意 `command_type` / `target_agent`
- 从环境变量构建：`EDGE_COMMAND_BASE_URL` / `EDGE_RPC_HOST` / `EDGE_COMMAND_PORT` / `EDGE_COMMAND_PUSHER_ENABLED` / `EDGE_COMMAND_TIMEOUT_MS`
- 全局单例：`init_edge_command_pusher()` / `get_edge_command_pusher()` / `set_edge_command_pusher()`

#### T-125：LimbicAgent 处理 EMOTION_INFERENCE_UPDATED 后推送情绪指令 ✅

- 修改 `host_brain/tribe/limbic_agent.py` 的 `_on_emotion_inference_updated()` handler
- 情绪推断处理完成后，调用 `self._edge_command_pusher.push_emotion_state()` 推送下行指令
- 推送字段：`emotion_tag`、`intensity`、`mood_state`、`session_id`
- 非致命：推送失败仅记录 warning，不影响 LimbicAgent 主逻辑
- 新增 `set_edge_command_pusher()` 注入方法，供 http_hooks.py 启动时注入

#### T-126：edge_client/tribe/somatic_agent.py 新增 on_emotion_state_command() ✅

- 修改 `edge_client/tribe/somatic_agent.py`，新增 `on_emotion_state_command()` 方法
- 接收下行情绪指令，解析 `emotion_tag` / `intensity` / `mood_state`
- 调用 `self._vts_driver.set_expression(emotion_tag, intensity)` 驱动 VTS 表情
- 非致命：vts_driver 未注入或调用失败时仅记录 warning

#### T-127：edge_client/runtime/bootstrap.py 将 vts_driver 注入到 SomaticAgent ✅

- 修改 `edge_client/runtime/bootstrap.py` 的 `EdgeRuntime` 启动流程
- 在 `EdgeTribeRegistry.initialize()` 之后，将 `vts_driver` 实例注入到 `SomaticAgent`
- 调用 `registry.somatic.set_vts_driver(vts_driver)` 完成注入
- 非致命：vts_driver 不可用时降级，SomaticAgent 仍可正常上报事件

#### T-128：edge_client/tribe/channel_agent.py 新增 on_gate_control_command() ✅

- 修改 `edge_client/tribe/channel_agent.py`，新增 `on_gate_control_command()` 方法
- 接收下行门控指令，解析 `gate_open` / `reason` / `platform`
- 更新内部 `_gate_open` 状态，影响后续消息路由决策
- 提供 `is_gate_open()` 查询方法，供 `GatewayRouter` 在路由前检查门控状态

#### T-129：CortexAgent 在 PLATFORM_CONNECTED/DISCONNECTED 后推送门控指令 ✅

- 修改 `host_brain/tribe/cortex_agent.py` 的 `_on_platform_connected/disconnected()` handler
- 平台连接时：调用 `self._edge_command_pusher.push_gate_control(gate_open=True, platform=platform)`
- 平台断线时：调用 `push_gate_control(gate_open=False, reason="platform_disconnected", platform=platform)`
- 新增 `set_edge_command_pusher()` 注入方法，供 http_hooks.py 启动时注入

#### T-130：http_hooks.py 初始化 EdgeCommandPusher 单例，注入到 LimbicAgent 和 CortexAgent ✅

- 在 `host_brain/app/http_hooks.py` 的 `runtime_lifespan` 中，Tribe 订阅注册完成后
- 调用 `init_edge_command_pusher()` 初始化全局单例
- 调用 `get_limbic_agent().set_edge_command_pusher(pusher)` 注入到 LimbicAgent
- 调用 `get_cortex_agent().set_edge_command_pusher(pusher)` 注入到 CortexAgent
- 启动状态写入 `new_module_statuses["edge_command_pusher"]`，含 `enabled` 和 `edge_url`

#### T-131：internal_tribe_routes.py 扩展下行指令诊断端点 ✅

- 在 `host_brain/app/internal_tribe_routes.py` 末尾新增两个诊断端点：
- `GET /internal/edge_command/status`：返回 EdgeCommandPusher 当前状态（enabled/url/timeout）
- `POST /internal/edge_command/push`：手动推送下行指令（运维/调试用途），记录推送结果日志
- 两个端点均受 `X-Internal-Token` 鉴权保护

#### T-132：http_hooks.py 第二十二批计数更新 + 文档留痕 ✅

- 更新 http_hooks.py 注释，说明第二十二批新增 EdgeCommandPusher 下行链路和诊断端点
- 订阅总数维持 65（本批无新增订阅，主要是下行链路和诊断端点）
- 完成本文档第二十二批留痕

---

**第二十二批新增能力总览**：

| 能力 | 方向 | 说明 |
|---|---|---|
| `EdgeCommandPusher` | host_brain → edge_client | HTTP 客户端，推送下行指令 |
| `/internal/tribe_command` | edge_client 接收 | 接收并路由下行指令到对应 Agent |
| `SomaticAgent.on_emotion_state_command()` | 执行层 | 接收情绪指令，驱动 vts_driver 表情 |
| `ChannelAgent.on_gate_control_command()` | 执行层 | 接收门控指令，更新消息路由开关 |
| `LimbicAgent` 情绪推送 | 触发层 | EMOTION_INFERENCE_UPDATED 后自动推送 |
| `CortexAgent` 门控推送 | 触发层 | PLATFORM_CONNECTED/DISCONNECTED 后自动推送 |
| `GET /internal/edge_command/status` | 诊断 | 查询 EdgeCommandPusher 初始化状态 |
| `POST /internal/edge_command/push` | 诊断 | 手动推送下行指令，验证链路畅通 |

**双向 Tribe 事件闭环完成**：

```
edge_client                          host_brain
    │                                    │
    │  上行：/internal/tribe_event POST  │
    │ ──────────────────────────────→   │
    │  SomaticAgent.publish_*()          │  LimbicAgent._on_emotion_inference_updated()
    │  ChannelAgent.on_message_*()       │  CortexAgent._on_platform_*()
    │  PeripheralAgent.publish_*()       │  ANSAgent._on_voice_pipeline_*()
    │  VoiceAgent.publish_*()            │  ANSAgent._on_channel_message_*()
    │                                    │
    │  下行：/internal/tribe_command POST│
    │ ←──────────────────────────────   │
    │  SomaticAgent.on_emotion_state_*() │  EdgeCommandPusher.push_emotion_state()
    │  ChannelAgent.on_gate_control_*()  │  EdgeCommandPusher.push_gate_control()
    │                                    │
```

---

## 第二十三批实现记录（T-143 ~ T-152）

**目标**：Phase 5 收口推进 — `chat_context_builder.py` 直接模块调用改为族 Agent 接口，ImmuneAgent 族化 governor/airlock，补全各 Agent 联动链路。

### 已完成任务

#### T-143：chat_context_builder.py 将 StyleCanonStore 直接调用改为通过 SocialBrainAgent.resolve_style_canon() ✅

- 移除 `chat_context_builder.py` 顶层 `from host_brain.memory.canon_style_store import StyleCanonStore` 导入
- 将 `build_node_style_bindings()` 中的 `store = StyleCanonStore(...)` 替换为 `social_agent.resolve_style_canon(canon_id=..., style_root=...)`
- 返回字段从 `entry.content` 改为 `entry_dict.get("content")`，保持接口兼容
- 降级安全：`status` 为 NOT_FOUND/ERROR 时跳过该 canon_id，行为与原实现一致

#### T-144：chat_context_builder.py 将 emergent_growth_engine 直接调用改为通过 PrefrontalAgent ✅

- 将 `from host_brain.skills.impl.emergent_growth_skill import _get_growth_engine` 替换为
  `from host_brain.tribe.prefrontal_agent import get_prefrontal_agent`
- 通过 `get_prefrontal_agent()._get_emergent_growth_engine()` 获取引擎实例
- 降级安全：`_get_emergent_growth_engine()` 返回 None 时跳过注入，行为与原实现一致

#### T-145：ImmuneAgent 新增 inspect_from_governor() 方法 ✅

- 封装 `evaluate_governor_gates()` 调用，ImmuneAgent 作为 governor 规则门的统一入口
- REJECT 时同步到 ImmuneAgent 计数器，并发布 `IMMUNE_BLOCK` 事件
- 降级安全：调用失败时返回 `{"decision": "APPROVE", ...}`，不阻断主链路

#### T-146：ImmuneAgent 新增 inspect_from_airlock() 方法 ✅

- 封装 `filter_pii_ingress()` / `filter_pii_egress()` 调用，ImmuneAgent 作为 airlock PII 过滤的统一入口
- block 模式下检测到 PII 时同步计数器并发布 `IMMUNE_BLOCK` 事件
- 支持 `channel` 参数区分入口/出口检测
- 降级安全：调用失败时返回 `{"decision": "ALLOW", ...}`，不阻断主链路

#### T-147：ImmuneAgent 新增 register_runtime_rule() 方法 ✅

- 支持运行时动态注册正则检测规则，无需重启服务
- 规则存储在 `_template_rules_cache` 中，重启后失效（持久化规则使用 template 机制）
- 新增 `list_runtime_rules()` 列出所有运行时规则
- 新增 `unregister_runtime_rule(rule_id)` 注销指定规则
- 支持 `rule_id` 去重（重复注册覆盖旧规则）

#### T-148：ANSAgent emit_inner_world_ticked() 中加入 trigger_proactive_voice() 判断逻辑 ✅

- 在 `emit_inner_world_ticked()` 发布事件后，判断是否触发主动发声评估
- 触发条件：`proactive_voice_eligible=True` 或 `loneliness > 0.5 且 bond_stage != stranger`
- 语音管线活跃时（`_voice_active=True`）不触发，避免打断语音对话
- 打通 `INNER_WORLD_TICKED → trigger_proactive_voice() → PROACTIVE_VOICE_TRIGGERED` 联动链路

#### T-149：LimbicAgent 新增 INTERACTION_RECORDED 订阅 + drive_emotion_metabolism_from_interaction() ✅

- 新增订阅 `INTERACTION_RECORDED` → `_on_interaction_recorded_metabolism()`
- 新增 `drive_emotion_metabolism_from_interaction(emotion_tag, valence, arousal, source)` 方法
  - 封装 `drive_emotion_metabolism(blend_weight=0.3)` + `write_emotion_timeline()` 组合调用
  - LimbicAgent 作为对话情绪代谢的唯一驱动者
- 每轮对话结束后自动驱动情绪代谢，确保情绪状态随对话自然演化

#### T-150：SocialBrainAgent 新增 resolve_bond_narrative() 方法 ✅

- 封装 `BondNarrativeLayer.build_narrative()` 调用，SocialBrainAgent 作为 Bond 叙事的唯一构建入口
- 自动从内部 `_bond_stage` 读取当前关系阶段（可通过参数覆盖）
- 通过 `get_shared_memory_index()` / `get_milestone_tracker()` 获取依赖
- 返回 `BondNarrativeResult.to_prompt_slot()` 字典，含 `narrative_items` / `relationship_duration` 等字段

#### T-151：SocialBrainAgent 新增 resolve_shared_memory_index() 方法 ✅

- 封装 `SharedMemoryIndex.query()` 调用，SocialBrainAgent 作为共同记忆查询的唯一入口
- 通过 `get_shared_memory_index()` 获取全局单例
- 返回 `SharedMemoryResult.to_prompt_slot()` 字典，含 `entries` / `entry_count` 等字段

#### T-152：规划文档第二十三批留痕 ✅

- 新增本章节，记录 T-143 ~ T-152 所有实现细节

---

**第二十三批新增能力总览**：

| 能力 | 归属 Agent | 封装模块 | 说明 |
|---|---|---|---|
| `build_node_style_bindings()` 改造 | SocialBrainAgent | `StyleCanonStore` | Phase 5 收口：chat_context_builder 不再直接调用 |
| emergent_growth_engine 改造 | PrefrontalAgent | `_get_emergent_growth_engine()` | Phase 5 收口：chat_context_builder 不再直接调用 |
| `inspect_from_governor()` | ImmuneAgent | `evaluate_governor_gates()` | governor/ 族化入口 |
| `inspect_from_airlock()` | ImmuneAgent | `filter_pii_ingress/egress()` | airlock/ 族化入口 |
| `register_runtime_rule()` / `list_runtime_rules()` / `unregister_runtime_rule()` | ImmuneAgent | 内存规则缓存 | 运行时动态规则注册 |
| `emit_inner_world_ticked()` 增强 | ANSAgent | `trigger_proactive_voice()` | INNER_WORLD_TICKED → proactive_voice 联动 |
| `drive_emotion_metabolism_from_interaction()` | LimbicAgent | `drive_emotion_metabolism()` + `write_emotion_timeline()` | INTERACTION_RECORDED → 情绪代谢联动 |
| `resolve_bond_narrative()` | SocialBrainAgent | `BondNarrativeLayer` | bond_narrative 族化入口 |
| `resolve_shared_memory_index()` | SocialBrainAgent | `SharedMemoryIndex` | shared_memory_index 族化入口 |

**Phase 5 收口进度**：

| 模块 | 原调用方式 | 改造后 | 状态 |
|---|---|---|---|
| `StyleCanonStore` | `chat_context_builder.py` 直接实例化 | `SocialBrainAgent.resolve_style_canon()` | ✅ T-143 |
| `emergent_growth_engine` | `_get_growth_engine()` 直接调用 | `PrefrontalAgent._get_emergent_growth_engine()` | ✅ T-144 |
| `BondNarrativeLayer` | `chat_context_builder.py` 直接实例化 | `SocialBrainAgent.resolve_bond_narrative()` | ✅ T-150/T-153 已完成 |
| `SharedMemoryIndex` | `chat_context_builder.py` 直接调用 | `SocialBrainAgent.resolve_shared_memory_index()` | ✅ T-151 已完成 |

---

## 第二十四批实现记录（T-153 ~ T-162）

**目标**：Phase 5 收口补完 + 新增族化入口（MilestoneTracker / BondUserId）+ 联动链路完善。

### 已完成任务

#### T-153：chat_context_builder.py BondNarrativeLayer 直接调用改为族化接口 ✅

- 修改 `host_brain/app/chat_context_builder.py` 第 2349 行附近的 BondNarrativeLayer 接入块
- 移除 `BondNarrativeLayer` / `SharedMemoryIndex` / `MilestoneTracker` 三个直接 import
- 改为通过 `SocialBrainAgent.resolve_bond_narrative(user_id, bond_stage)` 族化调用
- 从返回结果的 `prompt_slot` 字段读取 bond_narrative 注入 runtime_payload
- Phase 5 收口：chat_context_builder 不再直接实例化 Bond 相关模块

#### T-154：ImmuneAgent inspect_from_governor() ✅（T-145 已实现，本批确认）

- `inspect_from_governor(input_snapshot, source)` 封装 `evaluate_governor_gates()` 调用
- REJECT 时同步 ImmuneAgent 计数器并发布 `IMMUNE_BLOCK` 事件
- ImmuneAgent 作为 governor 规则门的统一调用入口

#### T-155：ImmuneAgent inspect_from_airlock() ✅（T-146 已实现，本批确认）

- `inspect_from_airlock(text, channel, source)` 封装 `filter_pii_ingress/egress()` 调用
- block 模式下同步 ImmuneAgent 计数器并发布 `IMMUNE_BLOCK` 事件
- ImmuneAgent 作为 airlock PII 过滤的统一调用入口

#### T-156：ImmuneAgent register_runtime_rule() ✅（T-147 已实现，本批确认）

- `register_runtime_rule(rule_id, pattern, reason_code, score, flags, source)` 运行时动态注册规则
- 规则存入 `_template_rules_cache`，支持去重覆盖，缓存永不过期
- 增强免疫系统可扩展性，无需重启即可添加新检测规则

#### T-157：ANSAgent emit_inner_world_ticked() bond_stage 注入完善 ✅（T-148 已实现，本批确认）

- `emit_inner_world_ticked()` 已在 T-148 中完整实现 bond_stage 注入到 proactive_voice context
- 触发条件：`proactive_voice_eligible=True` 或 `loneliness > 0.5 且 bond_stage != stranger`
- 语音管线活跃时（`_voice_active=True`）自动跳过，避免打断语音

#### T-158：LimbicAgent _on_interaction_recorded_metabolism() ✅（T-149 已实现，本批确认）

- `_on_interaction_recorded_metabolism()` 处理 `INTERACTION_RECORDED` 事件
- 调用 `drive_emotion_metabolism_from_interaction(emotion_tag, valence, arousal)` 驱动情绪代谢
- 每轮对话结束后自动将对话情绪信号融入情绪状态（blend_weight=0.3）

#### T-159：SocialBrainAgent resolve_milestone_tracker() ✅

- 新增 `resolve_milestone_tracker(user_id, event_type, ts_ms, metadata)` 方法
- 封装 `MilestoneTracker.on_event()` 调用，SocialBrainAgent 作为里程碑检测唯一入口
- 支持纯查询模式（event_type=None）和事件触发模式
- 新里程碑达成时写入 `last_milestone_achieved` 快照

#### T-160：HippocampusAgent resolve_bond_user_id() ✅

- 新增 `resolve_bond_user_id(fallback)` 方法
- 封装 `get_canonical_bond_user_id()` 调用，HippocampusAgent 作为 Bond 用户 ID 唯一解析入口
- 解析失败时降级返回 fallback（默认 "user_1"）

#### T-161：LimbicAgent 订阅 INTERACTION_RECORDED 事件 ✅（T-149 已实现，本批确认）

- `register_subscriptions()` 中已新增 `bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded_metabolism)`
- LimbicAgent 订阅总数从 11 增加到 12

#### T-162：规划文档第二十四批留痕 ✅

- 更新七、模块归属决策表中 BondNarrativeLayer/SharedMemoryIndex 的实现状态
- 新增本章第二十四批开发留痕

---

**第二十四批新增能力总览**：

| 能力 | 归属 Agent | 封装模块 | 说明 |
|---|---|---|---|
| `chat_context_builder` BondNarrativeLayer 改造 | SocialBrainAgent | `resolve_bond_narrative()` | Phase 5 收口：不再直接实例化 BondNarrativeLayer |
| `resolve_milestone_tracker()` | SocialBrainAgent | `MilestoneTracker.on_event()` | milestone_tracker 族化入口 |
| `resolve_bond_user_id()` | HippocampusAgent | `get_canonical_bond_user_id()` | user_identity 族化入口 |
| `_on_interaction_recorded_metabolism()` | LimbicAgent | `drive_emotion_metabolism_from_interaction()` | INTERACTION_RECORDED → 情绪代谢联动 |

**Phase 5 收口最终进度**：

| 模块 | 原调用方式 | 改造后 | 状态 |
|---|---|---|---|
| `StyleCanonStore` | `chat_context_builder.py` 直接实例化 | `SocialBrainAgent.resolve_style_canon()` | ✅ T-143 |
| `emergent_growth_engine` | `_get_growth_engine()` 直接调用 | `PrefrontalAgent._get_emergent_growth_engine()` | ✅ T-144 |
| `BondNarrativeLayer` | `chat_context_builder.py` 直接实例化 | `SocialBrainAgent.resolve_bond_narrative()` | ✅ T-153 |
| `SharedMemoryIndex` | `chat_context_builder.py` 直接调用 | `SocialBrainAgent.resolve_shared_memory_index()` | ✅ T-151 |
| `MilestoneTracker` | `chat_context_builder.py` 直接调用 | `SocialBrainAgent.resolve_milestone_tracker()` | ✅ T-159 |
| `get_canonical_bond_user_id()` | 多处直接调用 | `HippocampusAgent.resolve_bond_user_id()` | ✅ T-160 |

---

## 第二十五批实现记录（T-163 ~ T-172）

> **日期**：2026-04-09 | **主题**：Phase 6 躯干族独立 —— edge_client 内部族化完善

### 背景

Phase 6 目标是让 edge_client 内部形成完整的族化闭环：SomaticAgent 和 PeripheralAgent 不仅能向 host_brain 上报事件（上行），还能接收 host_brain 的下行指令并驱动本地硬件/平台适配器。本批次完成了双向通信链路的最后一公里。

---

#### T-163：评估 edge_client 躯干族缺口 ✅

- 读取 `edge_client/tribe/somatic_agent.py`（6.0 KB）和 `peripheral_agent.py`（6.7 KB）
- 确认已有上行能力：`publish_body_sense_updated()`、`publish_emotion_inference_updated()`、`publish_platform_connected()`、`publish_platform_disconnected()`
- 确认缺口：无 `handle_tribe_command()` 下行指令处理、无 `publish_somatic_state()` / `publish_peripheral_state()` 主动状态上报

---

#### T-164：edge_client SomaticAgent 新增 handle_tribe_command() ✅

**文件**：`edge_client/tribe/somatic_agent.py`

- 新增 `handle_tribe_command(command: dict) -> dict` 方法
- 支持指令类型：
  - `set_expression`：调用 `vts_driver.set_expression(emotion_tag, intensity)`，驱动 VTube Studio 表情
  - `set_body_state`：更新本地躯体状态快照（疲劳度/孤独感）
  - `reset_body_sense`：重置躯体感知状态到中性值
- 所有指令执行后写入 `somatic_agent` 快照的 `last_command_handled` 字段
- 降级：指令类型未知时返回 `{"status": "UNKNOWN_COMMAND"}` 而非抛出异常

---

#### T-165：edge_client SomaticAgent 新增 publish_somatic_state() ✅

**文件**：`edge_client/tribe/somatic_agent.py`

- 新增 `publish_somatic_state(state: dict) -> dict` 方法
- 通过 `EdgeTribeBridge.push_event()` 向 host_brain 上报 `SOMATIC_STATE_SNAPSHOT` 事件
- 载荷包含：`fatigue`、`loneliness`、`willingness`、`last_voice_ts_ms`、`source="somatic_agent"`
- 供 `voice_pipeline.py` 在语音处理周期结束时定期调用

---

#### T-166：edge_client PeripheralAgent 新增 handle_tribe_command() ✅

**文件**：`edge_client/tribe/peripheral_agent.py`

- 新增 `handle_tribe_command(command: dict) -> dict` 方法
- 支持指令类型：
  - `set_gate`：调用平台适配器的门控接口（`open` / `close` / `pause`）
  - `set_platform_config`：更新平台运行时配置（如消息速率限制）
  - `reconnect_platform`：触发平台适配器重连
- 所有指令执行后写入 `peripheral_agent` 快照的 `last_command_handled` 字段
- 降级：指令类型未知时返回 `{"status": "UNKNOWN_COMMAND"}`

---

#### T-167：edge_client PeripheralAgent 新增 publish_peripheral_state() ✅

**文件**：`edge_client/tribe/peripheral_agent.py`

- 新增 `publish_peripheral_state(state: dict) -> dict` 方法
- 通过 `EdgeTribeBridge.push_event()` 向 host_brain 上报 `PERIPHERAL_STATE_SNAPSHOT` 事件
- 载荷包含：`connected_platforms`、`gate_open`、`last_message_ts_ms`、`source="peripheral_agent"`
- 供 `gateway/bootstrap.py` 在平台连接状态变更时调用

---

#### T-168：edge_client EdgeTribeBridge 新增 receive_command() ✅

**文件**：`edge_client/tribe/edge_tribe_bridge.py`

- 新增 `receive_command(payload: dict) -> dict` 方法
- 根据 `payload["target_agent"]` 字段分发到对应 Agent：
  - `"edge.somatic"` / `"somatic_agent"` → `SomaticAgent.handle_tribe_command()`
  - `"edge.peripheral"` / `"peripheral_agent"` → `PeripheralAgent.handle_tribe_command()`
  - `"edge.broadcast"` → 同时分发到两个 Agent
- 分发结果聚合后返回 `{"status": "OK", "results": {...}}`
- 降级：target_agent 未知时返回 `{"status": "UNKNOWN_TARGET"}`

---

#### T-169：edge_client /internal/tribe_command 端点接入 receive_command() ✅

**文件**：`edge_client/app/fastapi_app.py`

- 修改 `/internal/tribe_command` POST 端点处理逻辑
- 从 `EdgeTribeRegistry` 获取全局 `EdgeTribeBridge` 单例
- 将请求体 JSON 传入 `bridge.receive_command(payload)`
- 返回分发结果，完成 host_brain → edge_client 下行指令路由的最后一公里

---

#### T-170：host_brain SomaticAgent 新增 export_runtime_state() ✅

**文件**：`host_brain/tribe/somatic_agent.py`

- 新增 `export_runtime_state() -> dict` 方法
- 从 `state_registry` 读取 `somatic_agent` 命名空间快照
- 返回结构化状态字典，包含：
  - `fatigue_level`、`loneliness_level`、`willingness_penalty`
  - `last_body_sense_ts_ms`、`last_loneliness_ts_ms`
  - `agent_id="somatic_agent"`、`snapshot_ts_ms`
- 供 `CortexAgent.collect_tribe_context()` 在构建 prompt 上下文时读取躯体状态

---

#### T-171：host_brain PeripheralAgent 新增 export_runtime_state() ✅

**文件**：`host_brain/tribe/peripheral_agent.py`

- 新增 `export_runtime_state() -> dict` 方法
- 从 `state_registry` 读取 `peripheral_agent` 命名空间快照
- 返回结构化状态字典，包含：
  - `connected_platforms`、`gate_open`、`last_platform_event`
  - `last_connected_ts_ms`、`last_disconnected_ts_ms`
  - `agent_id="peripheral_agent"`、`snapshot_ts_ms`
- 供 `CortexAgent.collect_tribe_context()` 在构建 prompt 上下文时读取外设状态

---

#### T-172：规划文档第二十五批留痕 ✅

- 新增本章第二十五批开发留痕

---

**第二十五批新增能力总览**：

| 能力 | 归属 Agent | 方法 | 说明 |
|---|---|---|---|
| 下行指令处理（表情驱动） | edge SomaticAgent | `handle_tribe_command()` | 接收 host_brain 情绪指令，驱动 vts_driver |
| 躯体状态主动上报 | edge SomaticAgent | `publish_somatic_state()` | 上报 SOMATIC_STATE_SNAPSHOT 事件 |
| 下行指令处理（门控） | edge PeripheralAgent | `handle_tribe_command()` | 接收 host_brain 门控指令，控制平台适配器 |
| 外设状态主动上报 | edge PeripheralAgent | `publish_peripheral_state()` | 上报 PERIPHERAL_STATE_SNAPSHOT 事件 |
| 下行指令分发路由 | EdgeTribeBridge | `receive_command()` | 按 target_agent 分发到对应 Agent |
| /internal/tribe_command 接入 | edge FastAPI | 端点改造 | 完成下行指令路由最后一公里 |
| 躯体状态导出 | host SomaticAgent | `export_runtime_state()` | 供 CortexAgent 读取躯体快照 |
| 外设状态导出 | host PeripheralAgent | `export_runtime_state()` | 供 CortexAgent 读取外设快照 |

**Phase 6 躯干族独立完成度**：

| 能力维度 | 状态 | 说明 |
|---|---|---|
| edge → host 上行事件（BODY_SENSE_UPDATED） | ✅ 已完成 | T-09 批次 |
| edge → host 上行事件（EMOTION_INFERENCE_UPDATED） | ✅ 已完成 | T-09 批次 |
| edge → host 上行事件（PLATFORM_CONNECTED/DISCONNECTED） | ✅ 已完成 | T-118 批次 |
| host → edge 下行指令（情绪表情驱动） | ✅ 已完成 | T-164/T-168/T-169 |
| host → edge 下行指令（门控控制） | ✅ 已完成 | T-166/T-168/T-169 |
| edge 主动状态快照上报 | ✅ 已完成 | T-165/T-167 |
| host 侧躯干族状态导出（供 CortexAgent） | ✅ 已完成 | T-170/T-171 |

---

## 第二十六批实现记录（T-173 ~ T-176）

> **日期**：2026-04-09 | **主题**：P0 收口（注册表一致性 + Peripheral 主机侧补齐）

### 已完成任务

#### T-173：HostTribeRegistry 新增已落地族注册 ✅

- 文件：`host_brain/tribe/registry.py`
- 新增族：`pain_reward`、`language`、`somatic`、`peripheral`
- 新增默认 Agent：
  - `pain_reward_agent`
  - `language_system_agent`
  - `somatic_agent`
  - `peripheral_agent`
- `resolve_target_family()` 新增上述族关键词映射，避免“代码已实现但注册表不可见”。

#### T-174：host_brain PeripheralAgent 补齐状态接收与导出 ✅

- 文件：`host_brain/tribe/peripheral_agent.py`
- 新增订阅：
  - `PLATFORM_CONNECTED`
  - `PLATFORM_DISCONNECTED`
  - `PERIPHERAL_STATE_UPDATED`
  - `GATE_STATE_CHANGED`
  - `PLATFORM_MUTE_CHANGED`
- 新增状态导出：
  - `export_runtime_state()`
- 结果：edge 上报的外设状态可稳定写入 `peripheral_agent` 命名空间并供上层读取。

#### T-175：CortexAgent 收口补齐 somatic/peripheral 状态，并修复不可达逻辑 ✅

- 文件：`host_brain/tribe/cortex_agent.py`
- `collect_tribe_context()` 新增：
  - `somatic_state`
  - `peripheral_state`
- 修复：原函数中提前 `return` 导致后续逻辑不可达，现已收口为单出口返回。

#### T-176：回归测试补齐 ✅

- 新增：`tests/unit/test_peripheral_agent.py`
- 更新：`tests/unit/test_tribe_registry.py`
- 通过：
  - `python -m pytest tests/unit/test_tribe_registry.py tests/unit/test_peripheral_agent.py`
  - `python -m pytest tests/unit/test_cortex_agent.py`
  - `python -m pytest tests/unit/test_edge_tribe_agents.py tests/unit/test_tribe_handoff.py`

---

## 第二十七批实现记录（T-177 ~ T-180）

> **日期**：2026-04-09 | **主题**：P0-4 主链路回归（Host↔Edge 指令与状态回流）

### 已完成任务

#### T-177：EdgeTribeBridge 下行指令兼容与路由修复 ✅

- 文件：`edge_client/tribe/edge_tribe_bridge.py`
- 修复点：
  - 新增 `EMOTION_STATE_COMMAND` → `emotion_state` 映射（兼容 `EdgeCommandPusher.push_emotion_state()`）。
  - 新增 `GATE_CONTROL_COMMAND` → `gate_control` 映射。
  - 新增 `PLATFORM_MUTE_COMMAND` → `platform_mute` 映射。
  - 扩展 `receive_command()`：支持 `channel_agent` 路由，`edge.channel/channel_agent/channel` 可接收门控指令。
  - `GATE_CONTROL(_COMMAND)` 兼容 `payload.gate` 与 `payload.gate_open` 两种格式。

#### T-178：edge /internal/tribe_command 分发接线修复 ✅

- 文件：`edge_client/app/fastapi_app.py`
- 修复点：
  - 调用 `EdgeTribeBridge.receive_command()` 时注入 `channel_agent`。
  - 降级分发路径兼容 `EMOTION_STATE_COMMAND` / `GATE_CONTROL_COMMAND`。
  - 修复 ChannelAgent 降级调用参数，改为显式 `gate_open/reason/platform/ts_ms`，避免参数不匹配。

#### T-179：EdgeCommandPusher 成功判定收紧 ✅

- 文件：`host_brain/tribe/edge_command_pusher.py`
- 修复点：
  - `push_command()` 新增对 `dispatched` 字段消费。
  - 返回值补充 `accepted/dispatched`，并将 `ok` 定义为 `accepted && dispatched`（若无 dispatched 字段则降级按 accepted 处理）。

#### T-180：Host↔Edge 双向回归测试补齐 ✅

- 新增：`tests/integration/test_host_edge_roundtrip.py`
- 覆盖场景：
  - Host → Edge：`push_emotion_state()`、`push_gate_control()` 下行指令分发与执行。
  - Edge → Host：`publish_somatic_state()`、`publish_peripheral_state()` 及门控/静音事件回流后，Host Somatic/Peripheral 快照可读。
- 同步修复：
  - 文件：`edge_client/tribe/peripheral_agent.py`
  - 为 dataclass(slots=True) 显式声明 `_gate_state/_mute_state`，修复门控与静音指令写状态时报 `AttributeError` 的问题。

### 本地验证

- `python -m pytest tests/integration/test_host_edge_roundtrip.py`
- `python -m pytest tests/integration/test_host_edge_roundtrip.py tests/unit/test_edge_tribe_agents.py`
- `python -m pytest tests/unit/test_edge_tribe_bridge.py tests/unit/test_tribe_registry.py tests/unit/test_peripheral_agent.py tests/unit/test_cortex_agent.py tests/integration/test_internal_tribe_routes.py tests/integration/test_host_edge_roundtrip.py`

---

## 第二十八批实现记录（T-181 ~ T-186）

> **日期**：2026-04-09 | **主题**：P1 全量系统层一次成型（Circadian + Proprioception + 三问门禁 + bond_vector）

### 已完成任务

#### T-181：CircadianAgent 完整落地 ✅

- 新增文件：`host_brain/tribe/circadian_agent.py`
- 能力：
  - 昼夜相位判定（dawn/day/dusk/night）
  - 主动预算（`proactive_multiplier`）
  - 恢复偏置（`recovery_bias`）与睡眠压力（`sleep_pressure`）
  - 相位更新事件 `CIRCADIAN_PHASE_UPDATED`

#### T-182：ProprioceptionAgent 完整落地 ✅

- 新增文件：`host_brain/tribe/proprioception_agent.py`
- 能力：
  - `active_user/active_session/active_task_count`
  - 中断窗口统计（`interrupt_count_5m`）
  - 认知负荷指数（`cognitive_load_index`）
  - 就绪度与连续性提示（`readiness_hint/continuity_hint`）
  - 状态更新事件 `PROPRIOCEPTION_UPDATED`

#### T-183：InitiativeGovernorAgent 完整落地 ✅

- 新增文件：`host_brain/tribe/initiative_governor_agent.py`
- 能力：
  - 统一执行 `motive/readiness/continuity` 三问门禁
  - 产出 `ALLOW/DEFER/BLOCK` 与 `reason_code`
  - 事件与审计：`INITIATIVE_DECISION_MADE` + `trace_id`

#### T-184：SocialBrain bond_vector 连续化 ✅

- 文件：`host_brain/tribe/social_brain_agent.py`
- 新增：
  - `resolve_bond_vector()`
  - `get_bond_context()`（含 `bond_vector`）
  - `BOND_VECTOR_UPDATED` 事件链路
- 修复：
  - 增补 `_bond_stage` 内部状态字段，避免旧路径隐式属性缺失

#### T-185：系统收口与状态聚合升级 ✅

- 文件：
  - `host_brain/tribe/tribe_state.py`
  - `host_brain/tribe/cortex_agent.py`
  - `host_brain/tribe/ans_agent.py`
  - `host_brain/app/http_hooks.py`
  - `host_brain/tribe/registry.py`
- 关键变更：
  - `TribeStateRegistry` 新增 `write()`，`set_state()` 补充 schema/audit 字段
  - `aggregate_vital_signs()` 聚合 `bond_vector + circadian + proprioception`
  - `CortexAgent.collect_tribe_context()` 新增：
    - `bond_vector`
    - `circadian_state`
    - `proprioception_state`
    - `initiative_decision`
  - `ANSAgent` 主动发声入口统一过三问门禁
  - 生命周期接线新增：
    - `circadian_agent`
    - `proprioception_agent`
    - `initiative_governor_agent`
  - HostTribeRegistry 新增族与默认 Agent：
    - `circadian`
    - `proprioception`
    - `initiative_governor`

#### T-186：测试矩阵补齐 ✅

- 新增：
  - `tests/unit/test_circadian_agent.py`
  - `tests/unit/test_proprioception_agent.py`
  - `tests/unit/test_initiative_governor_agent.py`
  - `tests/integration/test_bond_vector_integration.py`
- 更新：
  - `tests/unit/test_tribe_registry.py`
- 通过：
  - `python -m pytest tests/unit/test_circadian_agent.py tests/unit/test_proprioception_agent.py tests/unit/test_initiative_governor_agent.py tests/integration/test_bond_vector_integration.py tests/unit/test_tribe_registry.py tests/unit/test_cortex_agent.py tests/unit/test_social_brain_agent.py tests/integration/test_host_edge_roundtrip.py`
  - `python -m pytest tests/unit/test_fastapi_app_bond_provider_social_brain.py tests/unit/test_http_hooks_runtime_helpers_social_brain_integration.py tests/unit/test_inner_activity_agent_social_brain_integration.py tests/unit/test_state_arbiter_queries_social_brain_integration.py tests/unit/test_limbic_agent.py tests/unit/test_edge_tribe_bridge.py tests/unit/test_edge_tribe_agents.py tests/integration/test_internal_tribe_routes.py tests/integration/test_host_edge_roundtrip.py`

---

## 第二十九批实现记录（T-187 ~ T-192）

> **日期**：2026-04-10 | **主题**：P2/P3 全量收口（DefaultMode + MirrorNeuron + 语言双系统 + 记忆三分层 + 稳态长期治理）

### 已完成任务

#### T-187：DefaultModeAgent 族化落地 ✅

- 新增文件：`host_brain/tribe/default_mode_agent.py`
- 能力：
  - 订阅 `INNER_WORLD_TICKED` / `CONVERSATION_ENDED`
  - 输出空闲周期内在活动（`note/focus/offline_state`）
  - 事件：`DEFAULT_MODE_TICKED`
  - 导出：`export_runtime_state()`

#### T-188：MirrorNeuronAgent 族化落地 ✅

- 新增文件：`host_brain/tribe/mirror_neuron_agent.py`
- 能力：
  - 订阅 `EMOTION_INFERENCE_UPDATED` / `INTERACTION_RECORDED` / `EMOTION_STATE_CHANGED`
  - 共鸣计算：`compute_resonance()`
  - 共鸣应用：`apply_resonance()`
  - 事件：`MIRROR_RESONANCE_UPDATED`
  - 导出：`export_runtime_state()`

#### T-189：LanguageSystemAgent 双系统化 ✅

- 文件：`host_brain/tribe/language_system_agent.py`
- 新增能力：
  - 理解侧：`wernicke_understand()`
  - 生成侧：`broca_generate_plan()`
  - 双系统收口：`resolve_dual_system()`
  - 兼容入口：`get_language_style()` 保持兼容并支持携带 `user_text` 输出 dual-system。

#### T-190：记忆三分层 + 自传体门禁收口 ✅

- 文件：
  - `host_brain/tribe/hippocampus_agent.py`
  - `host_brain/tribe/prefrontal_agent.py`
- 新增能力：
  - Hippocampus：`write_episodic_memory_trace()` / `write_semantic_memory_fact()` / `write_autobiographical_memory()`
  - Prefrontal：`evaluate_autobiographical_memory_gate()` / `commit_autobiographical_memory()`
- 修复：
  - 三分层写入由浅覆盖改为“按层合并”，保留 `episodic/semantic/autobiographical` 同时可见。

#### T-191：稳态长期治理（分钟/小时/日）✅

- 文件：`host_brain/tribe/tribe_state.py`
- 新增能力：
  - `_record_homeostasis_sample()`
  - `get_homeostasis_report()`
  - `export_homeostasis_history()`
- `aggregate_vital_signs()` 现在直接返回 `homeostasis_report`。

#### T-192：Supervisor 与 runtime payload 收口补齐 ✅

- 文件：
  - `host_brain/tribe/cortex_agent.py`
  - `host_brain/app/chat_context_builder.py`
- `collect_tribe_context()` 新增：
  - `default_mode_state`
  - `mirror_neuron_state`
  - `memory_layers`
  - `language_dual_system`
  - `homeostasis_report`
- `chat_context_builder` 注入新块到 `runtime_payload`，并将语言风格调用升级为携带 `user_text` 同步生成 dual-system。

### 本地验证

- 新增测试：
  - `tests/unit/test_default_mode_agent.py`
  - `tests/unit/test_mirror_neuron_agent.py`
  - `tests/unit/test_language_system_agent.py`
  - `tests/unit/test_tribe_state_homeostasis.py`
  - `tests/unit/test_cortex_collect_context_extensions.py`
- 更新测试：
  - `tests/unit/test_tribe_registry.py`
  - `tests/unit/test_hippocampus_agent.py`
  - `tests/unit/test_prefrontal_agent.py`
- 通过：
  - `python -m pytest tests/unit/test_hippocampus_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_default_mode_agent.py tests/unit/test_mirror_neuron_agent.py tests/unit/test_language_system_agent.py tests/unit/test_tribe_state_homeostasis.py tests/unit/test_cortex_collect_context_extensions.py tests/unit/test_tribe_registry.py`
  - `python -m pytest tests/unit/test_cortex_agent.py tests/unit/test_limbic_agent.py tests/unit/test_circadian_agent.py tests/unit/test_proprioception_agent.py tests/unit/test_initiative_governor_agent.py tests/integration/test_bond_vector_integration.py`
  - `python -m pytest tests/integration/test_host_edge_roundtrip.py tests/integration/test_internal_tribe_routes.py`

---

## 十一章状态校正（2026-04-10）

> 11 章“缺失族”是初版差距分析结果。当前代码基线下，以下族已转为已实现状态：

| 族 | 原章节状态 | 当前状态 |
|---|---|---|
| `ImmuneAgent` | 缺失（高优先级） | ✅ 已实现并接入主链路 |
| `EndocrineAgent` | 缺失（高优先级） | ✅ 已实现并接入门控 |
| `CircadianAgent` | 缺失（中优先级） | ✅ 已实现 |
| `ProprioceptionAgent` | 缺失（中优先级） | ✅ 已实现 |
| `PainRewardAgent` | 缺失（低优先级） | ✅ 已实现 |
| `LanguageSystemAgent` | 缺失（低优先级） | ✅ 已实现，且完成双系统拆分 |
| `MirrorNeuronAgent` | 缺失（低优先级） | ✅ 已实现 |
| `DefaultModeAgent` | 缺失（低优先级） | ✅ 已实现 |

---

## 面向“完整人体”目标的扩充族（新增规划）

| 族名 | 人体类比 | 核心职责 | 规划优先级 |
|---|---|---|---|
| `InteroceptionAgent` | 内感受系统 | 统一体内感受指数（疲劳/压力/负荷）并驱动情绪与门禁 | 🔴 高 |
| `MotorCortexAgent` | 运动皮层 | 将意图转为动作计划并统一编排 host→edge 执行 | 🔴 高 |
| `CerebellumAgent` | 小脑 | 执行误差校正、延迟补偿、平台差异自适应 | 🟡 中 |
| `GlymphaticAgent` | 睡眠恢复系统 | 离线时段记忆巩固、噪声衰减、稳态恢复 | 🟡 中 |
| `VestibularAgent` | 前庭平衡系统 | 抑制模式抖动，维持跨平台上下文稳定 | 🟢 中低 |

---

## 第三十批实现记录（T-193 ~ T-197）

> **日期**：2026-04-10 | **主题**：完整人体扩充族首批落地（Interoception + MotorCortex + Cerebellum + Glymphatic + Vestibular）

### 已完成任务

#### T-193：InteroceptionAgent 落地 ✅

- 新增文件：`host_brain/tribe/interoception_agent.py`
- 能力：
  - 体内感受指数 `interoceptive_index`
  - 体能预算 `body_budget`
  - 恢复紧迫度 `recovery_urgency`
  - 事件：`INTEROCEPTION_UPDATED`

#### T-194：MotorCortexAgent 落地 ✅

- 新增文件：`host_brain/tribe/motor_cortex_agent.py`
- 能力：
  - 动作规划 `plan_action()`
  - 动作分发 `dispatch_action()`
  - 可选主动门禁联动（InitiativeGovernor）
  - 事件：`MOTOR_ACTION_PLANNED` / `MOTOR_ACTION_DISPATCHED`

#### T-195：CerebellumAgent 落地 ✅

- 新增文件：`host_brain/tribe/cerebellum_agent.py`
- 能力：
  - 执行反馈纠偏策略评估
  - 延迟补偿与重试建议
  - 事件：`CEREBELLUM_CORRECTION_DECIDED`

#### T-196：GlymphaticAgent 落地 ✅

- 新增文件：`host_brain/tribe/glymphatic_agent.py`
- 能力：
  - 恢复窗口判断（昼夜/压力/负荷）
  - 记忆巩固评分 `consolidation_score`
  - 噪声衰减系数 `noise_decay_factor`
  - 事件：`GLYMPHATIC_CYCLE_COMPLETED`

#### T-197：VestibularAgent 落地 ✅

- 新增文件：`host_brain/tribe/vestibular_agent.py`
- 能力：
  - 模式与平台抖动监测
  - 稳定性指数 `jitter_index`
  - 路由偏置与阻尼系数
  - 事件：`VESTIBULAR_STABILITY_UPDATED`

### 系统接线同步

- `host_brain/tribe/registry.py`：新增 5 个族及默认 Agent 注册
- `host_brain/tribe/__init__.py`：新增 5 个 Agent 导出
- `host_brain/app/http_hooks.py`：新增 5 个 Agent 启动订阅注册
- `host_brain/tribe/cortex_agent.py`：Supervisor 收口新增 5 族状态导出
- `host_brain/app/chat_context_builder.py`：runtime_payload 新增 5 族状态块注入
- `host_brain/tribe/tribe_state.py`：vital_signs 新增 `interoceptive_index` 聚合字段

### 本地验证

- 新增测试：
  - `tests/unit/test_interoception_agent.py`
  - `tests/unit/test_motor_cortex_agent.py`
  - `tests/unit/test_cerebellum_agent.py`
  - `tests/unit/test_glymphatic_agent.py`
  - `tests/unit/test_vestibular_agent.py`
- 更新测试：
  - `tests/unit/test_tribe_registry.py`
  - `tests/unit/test_cortex_collect_context_extensions.py`
- 通过：
  - `python -m pytest tests/unit/test_interoception_agent.py tests/unit/test_motor_cortex_agent.py tests/unit/test_cerebellum_agent.py tests/unit/test_glymphatic_agent.py tests/unit/test_vestibular_agent.py tests/unit/test_tribe_registry.py tests/unit/test_cortex_collect_context_extensions.py tests/unit/test_cortex_agent.py tests/unit/test_chat_context_builder_helpers_extra.py`
  - `python -m pytest tests/unit/test_default_mode_agent.py tests/unit/test_mirror_neuron_agent.py tests/unit/test_language_system_agent.py tests/unit/test_tribe_state_homeostasis.py tests/unit/test_cortex_collect_context_extensions.py tests/unit/test_interoception_agent.py tests/unit/test_motor_cortex_agent.py tests/unit/test_cerebellum_agent.py tests/unit/test_glymphatic_agent.py tests/unit/test_vestibular_agent.py tests/unit/test_hippocampus_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_tribe_registry.py tests/unit/test_chat_context_builder_hippocampus_integration.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_context_pack_runtime_integration.py tests/unit/test_node_style_bindings_prompt.py tests/unit/test_memory_persona_behavior_chain.py tests/unit/test_limbic_agent.py tests/unit/test_cortex_agent.py tests/unit/test_circadian_agent.py tests/unit/test_proprioception_agent.py tests/unit/test_initiative_governor_agent.py tests/integration/test_bond_vector_integration.py tests/integration/test_host_edge_roundtrip.py tests/integration/test_internal_tribe_routes.py`
