from __future__ import annotations

import inspect
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_brain.core.ids import new_request_id
from host_brain.organ_ops.organ_registry import build_default_organ_registry
from host_brain.persona.identity import get_canonical_persona_self_id, resolve_persona_self_id


CORE_TICK_TASK_IDS: tuple[str, ...] = (
    "inner_world_numeric_tick",
    "inner_world_monologue_tick",
    "silence_tick",
    "body_sense_tick",
    "emotion_decay_tick",
)

ForegroundTurnRunner = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(slots=True)
class DigitalLifeRuntimeConfig:
    mode: str = "fixture"
    run_id: str = ""
    persona_self_id: str = ""
    session_id: str = "digital-life-host-runtime"
    out_dir: str | Path | None = None
    start_scheduler_threads: bool = False
    manage_environment: bool = True
    reset_persona_state_for_run: bool = True
    tick_task_ids: Sequence[str] = CORE_TICK_TASK_IDS
    foreground_runner: ForegroundTurnRunner | None = None


@dataclass(slots=True)
class ForegroundTurnResult:
    request_id: str
    user_message: str
    reply_text: str = ""
    status: str = "RECORDED"
    reason_code: str = ""
    mapped_organ_count: int = 0
    user_emotion_tag: str = ""
    natsume_emotion_tag: str = ""
    vts_expression_id: str = ""
    reply_control_packet: dict[str, Any] = field(default_factory=dict)
    organ_trace: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_message": self.user_message,
            "reply_text": self.reply_text,
            "status": self.status,
            "reason_code": self.reason_code,
            "mapped_organ_count": self.mapped_organ_count,
            "user_emotion_tag": self.user_emotion_tag,
            "natsume_emotion_tag": self.natsume_emotion_tag,
            "vts_expression_id": self.vts_expression_id,
            "reply_control_packet": dict(self.reply_control_packet),
            "organ_trace": dict(self.organ_trace),
            "raw": dict(self.raw),
        }


class DigitalLifeSupervisor:
    """Host-side supervisor for keeping the digital-life loop alive.

    The supervisor owns the core background tick scheduler and exposes a small
    foreground-turn boundary. The foreground boundary accepts a pluggable
    runner so the real chat adapter can be attached without making the runtime
    package depend on validation scripts or transport-specific code.
    """

    def __init__(self, config: DigitalLifeRuntimeConfig | None = None) -> None:
        self.config = config or DigitalLifeRuntimeConfig()
        self.logger = logging.getLogger("Natsume.digital_life_supervisor")
        self.run_id = str(self.config.run_id or f"host-runtime-{int(time.time())}")
        self.mode = _normalize_mode(self.config.mode)
        self.persona_self_id = resolve_persona_self_id(
            self.config.persona_self_id or get_canonical_persona_self_id()
        )
        self.out_dir = Path(self.config.out_dir) if self.config.out_dir else None
        self._started_at_ms = 0
        self._last_tick_ts_ms = 0
        self._last_foreground_ts_ms = 0
        self._tick_count = 0
        self._foreground_turn_count = 0
        self._idle_tick_count = 0
        self._last_user_message = ""
        self._last_reply_text = ""
        self._last_turn: ForegroundTurnResult | None = None
        self._last_tick_results: dict[str, Any] = {}
        self._events: list[dict[str, Any]] = []
        self._old_env: dict[str, str | None] = {}
        self._old_inner_world: Any = None
        self._old_inner_world_dir: Any = None
        self._old_self_dir: str | None = None
        self._old_default_agent: Any = None
        self._old_limbic_agent: Any = None
        self._old_scheduler_registry: Any = None
        self._old_scheduler_health: Any = None
        self.registry: Any = None
        self.health_reporter: Any = None
        self.scheduler: Any = None
        self.alive = False

    def __enter__(self) -> "DigitalLifeSupervisor":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()

    @property
    def events(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._events]

    def start(self) -> dict[str, Any]:
        if self.alive:
            return self.snapshot()
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        self._started_at_ms = _now_ms()
        self._prepare_runtime_environment()
        self._prepare_persona_state()
        self._prepare_scheduler()
        if bool(self.config.start_scheduler_threads) and self.scheduler is not None:
            self.scheduler.start()
        self.alive = True
        self._record_event("runtime_started", self.snapshot())
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        if self.scheduler is not None:
            try:
                self.scheduler.stop()
            except Exception as exc:  # pragma: no cover - defensive shutdown
                self.logger.debug("digital life scheduler stop degraded: %s", exc)
        snapshot = self.snapshot()
        if self.alive:
            self._record_event("runtime_stopped", snapshot)
        self.alive = False
        self._restore_runtime_environment()
        return snapshot

    def run_idle_tick(self, *, task_ids: Sequence[str] | None = None) -> dict[str, Any]:
        self._ensure_started()
        selected = tuple(task_ids or self.config.tick_task_ids or CORE_TICK_TASK_IDS)
        results: dict[str, Any] = {}
        for task_id in selected:
            if task_id not in CORE_TICK_TASK_IDS:
                results[str(task_id)] = {"status": "SKIPPED", "reason_code": "NOT_A_CORE_TICK"}
                continue
            try:
                results[str(task_id)] = self.scheduler.trigger_task_by_id(str(task_id))
            except Exception as exc:  # pragma: no cover - diagnostic path
                results[str(task_id)] = {
                    "status": "ERROR",
                    "reason_code": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
        self._tick_count += 1
        self._idle_tick_count += 1
        self._last_tick_ts_ms = _now_ms()
        self._last_tick_results = results
        event = {
            "tick_index": self._idle_tick_count,
            "task_results": _strip_large(results),
            "snapshot": self.snapshot(),
        }
        self._record_event("idle_tick", event)
        return event

    def run_foreground_turn(
        self,
        user_message: str,
        *,
        reply_text: str = "",
        task_mode: str = "daily_chat",
        response_move: str = "reply_current_turn",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_started()
        request_id = new_request_id()
        self._mark_user_awaken()
        runner = self.config.foreground_runner or self._default_foreground_runner
        context = {
            "request_id": request_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "persona_self_id": self.persona_self_id,
            "session_id": self.config.session_id,
            "user_message": str(user_message or ""),
            "reply_text": str(reply_text or ""),
            "task_mode": str(task_mode or "daily_chat"),
            "response_move": str(response_move or "reply_current_turn"),
            "metadata": dict(metadata or {}),
            "snapshot_before": self.snapshot(),
        }
        raw_result = _call_runner(runner, context)
        result = _coerce_foreground_result(
            raw_result,
            request_id=request_id,
            user_message=str(user_message or ""),
            reply_text=str(reply_text or ""),
        )
        self._foreground_turn_count += 1
        self._last_foreground_ts_ms = _now_ms()
        self._last_user_message = result.user_message
        self._last_reply_text = result.reply_text
        self._last_turn = result
        self._mark_session_end(topic=result.user_message)
        event = {
            "turn_index": self._foreground_turn_count,
            "turn": result.as_dict(),
            "snapshot": self.snapshot(),
        }
        self._record_event("foreground_turn", event)
        return event

    def snapshot(self) -> dict[str, Any]:
        now = _now_ms()
        scheduler_snapshot = self._scheduler_snapshot()
        inner_state = self._inner_world_state()
        background_runtime = self._background_runtime_snapshot(
            now_ms=now,
            scheduler_snapshot=scheduler_snapshot,
            inner_state=inner_state,
        )
        latest_private = _latest_inner_event(inner_state)
        self_packet = self._self_directive_packet()
        organ_registry_rows = build_default_organ_registry().list_organs()
        return {
            "alive": bool(self.alive),
            "mode": self.mode,
            "run_id": self.run_id,
            "persona_self_id": self.persona_self_id,
            "session_id": self.config.session_id,
            "started_at_ms": self._started_at_ms,
            "uptime_ms": max(0, now - self._started_at_ms) if self._started_at_ms else 0,
            "foreground_turn_count": self._foreground_turn_count,
            "idle_tick_count": self._idle_tick_count,
            "tick_count": self._tick_count,
            "last_tick_ts_ms": self._last_tick_ts_ms,
            "last_foreground_ts_ms": self._last_foreground_ts_ms,
            "last_user_message": self._last_user_message,
            "last_reply_text": self._last_reply_text,
            "last_turn": self._last_turn.as_dict() if self._last_turn is not None else {},
            "current_emotion": {
                "user_emotion_tag": self._last_turn.user_emotion_tag if self._last_turn else "",
                "natsume_emotion_tag": self._last_turn.natsume_emotion_tag if self._last_turn else "",
                "vts_expression_id": self._last_turn.vts_expression_id if self._last_turn else "",
            },
            "latest_private_thought": {
                "private_text": str(latest_private.get("private_text") or latest_private.get("note") or ""),
                "focus": str(latest_private.get("focus") or ""),
                "source": str(latest_private.get("source") or ""),
                "salience": latest_private.get("salience"),
            },
            "self_model": self_packet,
            "organ_runtime": {
                "registered_organ_count": len(organ_registry_rows),
                "surface_expected_organ_count": 23,
                "last_mapped_organ_count": self._last_turn.mapped_organ_count if self._last_turn else 0,
                "last_organ_trace": dict(self._last_turn.organ_trace) if self._last_turn else {},
            },
            "scheduler": scheduler_snapshot,
            "background_runtime": background_runtime,
            "last_tick_results": _strip_large(self._last_tick_results),
            "external_services": {
                "chroma": {"status": "not_probed_by_supervisor"},
                "neo4j": {"status": "not_probed_by_supervisor"},
                "sqlite": {"status": "managed_by_runtime_components"},
            },
        }

    def _prepare_runtime_environment(self) -> None:
        if not self.config.manage_environment:
            return
        keys = (
            "HOST_BACKGROUND_TASKS_ENABLED",
            "HOST_CONTINUOUS_ORGAN_TICKS_ENABLED",
            "HOST_CORE_INLINE_DEFAULT_MODE_TICK_ENABLED",
            "NATSUME_PERSONA_SELF_ID",
            "NATSUME_INNER_WORLD_DIR",
            "NATSUME_SELF_MODEL_DIR",
            "NATSUME_INNER_STREAM_MIN_INTERVAL_S",
            "NATSUME_INNER_STREAM_CALM_INTERVAL_S",
        )
        self._old_env = {key: os.environ.get(key) for key in keys}
        os.environ["HOST_BACKGROUND_TASKS_ENABLED"] = "false"
        os.environ["HOST_CONTINUOUS_ORGAN_TICKS_ENABLED"] = "true"
        os.environ["HOST_CORE_INLINE_DEFAULT_MODE_TICK_ENABLED"] = "true"
        os.environ["NATSUME_PERSONA_SELF_ID"] = self.persona_self_id
        if self.out_dir is not None:
            os.environ["NATSUME_INNER_WORLD_DIR"] = str(self.out_dir / "inner_world_state")
            os.environ["NATSUME_SELF_MODEL_DIR"] = str(self.out_dir / "self_model_state")
        os.environ["NATSUME_INNER_STREAM_MIN_INTERVAL_S"] = "0"
        os.environ["NATSUME_INNER_STREAM_CALM_INTERVAL_S"] = "0"

    def _restore_runtime_environment(self) -> None:
        self._restore_persona_state()
        if not self.config.manage_environment:
            return
        for key, value in dict(self._old_env).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _prepare_persona_state(self) -> None:
        if not self.config.reset_persona_state_for_run:
            return
        try:
            import host_brain.persona.inner_world as inner_world  # noqa: PLC0415
            import host_brain.persona.self_model as self_model  # noqa: PLC0415
            import host_brain.scheduler.scheduler_routes as scheduler_routes  # noqa: PLC0415
            import host_brain.tribe.default_mode_agent as default_mode_agent  # noqa: PLC0415
            import host_brain.tribe.limbic_agent as limbic_agent  # noqa: PLC0415
            from host_brain.persona.inner_world import OfflineInnerWorld  # noqa: PLC0415
            from host_brain.tribe.default_mode_agent import DefaultModeAgent  # noqa: PLC0415
            from host_brain.tribe.limbic_agent import LimbicAgent  # noqa: PLC0415
            from host_brain.tribe.tribe_state import TribeStateRegistry  # noqa: PLC0415

            self._old_inner_world = getattr(inner_world, "_global_inner_world", None)
            self._old_inner_world_dir = getattr(inner_world, "_DEFAULT_STATE_DIR", None)
            self._old_self_dir = str(getattr(self_model, "_DEFAULT_PERSISTENT_SELF_DIR", ""))
            self._old_default_agent = getattr(default_mode_agent, "_DEFAULT_MODE_AGENT", None)
            self._old_limbic_agent = getattr(limbic_agent, "_LIMBIC_AGENT", None)
            self._old_scheduler_registry = getattr(scheduler_routes, "_registry", None)
            self._old_scheduler_health = getattr(scheduler_routes, "_health_reporter", None)

            if self.out_dir is not None:
                inner_dir = str(self.out_dir / "inner_world_state")
                self_dir = str(self.out_dir / "self_model_state")
                inner_world._DEFAULT_STATE_DIR = inner_dir
                inner_world._global_inner_world = OfflineInnerWorld(state_dir=inner_dir)
                self_model._DEFAULT_PERSISTENT_SELF_DIR = self_dir
                self_model._PERSISTENT_SELF_CACHE.clear()

            registry = TribeStateRegistry()
            limbic_agent.set_limbic_agent(LimbicAgent(state_registry=registry))
            default_mode_agent.set_default_mode_agent(DefaultModeAgent(state_registry=registry))
            default_mode_agent._FOREGROUND_INTERACTION_CACHE["ts_ms"] = 0
        except Exception as exc:  # pragma: no cover - defensive setup
            self.logger.debug("persona state isolation degraded: %s", exc)

    def _restore_persona_state(self) -> None:
        if not self.config.reset_persona_state_for_run:
            return
        try:
            import host_brain.persona.inner_world as inner_world  # noqa: PLC0415
            import host_brain.persona.self_model as self_model  # noqa: PLC0415
            import host_brain.scheduler.scheduler_routes as scheduler_routes  # noqa: PLC0415
            import host_brain.tribe.default_mode_agent as default_mode_agent  # noqa: PLC0415
            import host_brain.tribe.limbic_agent as limbic_agent  # noqa: PLC0415

            inner_world._global_inner_world = self._old_inner_world
            if self._old_inner_world_dir is not None:
                inner_world._DEFAULT_STATE_DIR = self._old_inner_world_dir
            default_mode_agent.set_default_mode_agent(self._old_default_agent)
            limbic_agent.set_limbic_agent(self._old_limbic_agent)
            self_model._DEFAULT_PERSISTENT_SELF_DIR = self._old_self_dir or self_model._DEFAULT_PERSISTENT_SELF_DIR
            self_model._PERSISTENT_SELF_CACHE.clear()
            scheduler_routes._registry = self._old_scheduler_registry
            scheduler_routes._health_reporter = self._old_scheduler_health
        except Exception as exc:  # pragma: no cover - defensive restore
            self.logger.debug("persona state restore degraded: %s", exc)

    def _prepare_scheduler(self) -> None:
        from host_brain.app.http_hooks import _build_core_organ_tick_task_configs  # noqa: PLC0415
        from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415
        from host_brain.scheduler.scheduler import NatsumeScheduler  # noqa: PLC0415
        from host_brain.scheduler.scheduler_routes import set_scheduler_providers  # noqa: PLC0415
        from host_brain.scheduler.task_registry import SchedulerTaskRegistry  # noqa: PLC0415

        self.registry = SchedulerTaskRegistry()
        self.health_reporter = SchedulerHealthReporter(registry=self.registry)
        set_scheduler_providers(registry=self.registry, health_reporter=self.health_reporter)
        for config in _build_core_organ_tick_task_configs():
            if self.registry.get(config.task_id) is None:
                self.registry.register(config)
        self.scheduler = NatsumeScheduler(
            registry=self.registry,
            health_reporter=self.health_reporter,
            intent_dispatcher=self._dispatch_scheduler_intent,
            get_emotion_state=self._scheduler_emotion_state,
            get_immune_gate_state=lambda: {},
            persist_tasks=False,
        )

    def _dispatch_scheduler_intent(self, system_intent: dict[str, Any]) -> dict[str, Any]:
        from host_brain.app.http_hooks_runtime_helpers import run_proactive_intent_dispatch  # noqa: PLC0415

        def _ui_pusher(_message: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": False,
                "status": "SKIPPED",
                "detail": "digital life host runtime does not push UI messages directly",
            }

        return run_proactive_intent_dispatch(
            dict(system_intent or {}),
            logger=self.logger,
            get_ledger=lambda: None,
            retention_policy=None,
            ui_pusher=_ui_pusher,
        )

    def _scheduler_emotion_state(self) -> dict[str, Any]:
        tag = ""
        if self._last_turn is not None:
            tag = self._last_turn.natsume_emotion_tag or self._last_turn.user_emotion_tag
        return {
            "emotion_tag": tag or "NEUTRAL",
            "mood_state": "steady",
            "endocrine_state": {
                "gate_state": "open",
                "cortisol_level": 0.08,
                "energy_reserve": 0.82,
            },
        }

    def _default_foreground_runner(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id = str(context.get("request_id") or new_request_id())
        user_message = str(context.get("user_message") or "")
        reply_text = str(context.get("reply_text") or "")
        surface_context: dict[str, Any] = {}
        reply_control: dict[str, Any] = {}
        raw_context_key_count = 0
        status = "RECORDED"
        reason_code = ""
        try:
            from host_brain.app.surface_context_packet import build_surface_context_packet  # noqa: PLC0415
            from host_brain.persona.surface_packet_compiler import compile_reply_control_packet  # noqa: PLC0415
            from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

            raw_context = get_cortex_agent().collect_tribe_context(
                request_body={
                    "request_id": request_id,
                    "person_id": self.persona_self_id,
                    "session_id": self.config.session_id,
                    "user_message": user_message,
                },
                session_id=self.config.session_id,
                user_id=self.persona_self_id,
            )
            raw_context = dict(raw_context or {}) if isinstance(raw_context, Mapping) else {}
            raw_context_key_count = len(raw_context)
            surface_context = build_surface_context_packet(
                user_message=user_message,
                request_id=request_id,
                session_id=self.config.session_id,
                person_id=self.persona_self_id,
                organ_context=raw_context,
                surface_task_control={
                    "task_mode": str(context.get("task_mode") or "daily_chat"),
                    "response_move": str(context.get("response_move") or "reply_current_turn"),
                    "question_budget": 0,
                },
            )
            compiled = compile_reply_control_packet(
                user_message=user_message,
                task_control=surface_context.get("reply_plan"),
                memory_packet=surface_context.get("surface_memory_recall_packet"),
                affect_packet=surface_context.get("surface_affect_packet"),
                influence_packet=surface_context.get("surface_influence_packet"),
            )
            reply_control = dict(compiled.packet.model_dump())
            status = "SURFACE_PACKET_COMPILED"
        except Exception as exc:
            status = "DEGRADED"
            reason_code = type(exc).__name__
            surface_context = {"error": str(exc)[:200]}

        influence = dict(surface_context.get("surface_influence_packet") or {})
        organ_trace = dict(influence.get("organ_trace") or {})
        return {
            "request_id": request_id,
            "user_message": user_message,
            "reply_text": reply_text,
            "status": status,
            "reason_code": reason_code,
            "reply_control_packet": reply_control,
            "organ_trace": organ_trace,
            "mapped_organ_count": int(
                organ_trace.get("mapped_organ_count")
                or len(list(organ_trace.get("mapped_organs") or []))
                or 0
            ),
            "raw": {
                "raw_context_key_count": raw_context_key_count,
                "surface_context_meta": dict(surface_context.get("surface_organ_context_meta") or {}),
            },
        }

    def _mark_user_awaken(self) -> None:
        try:
            from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

            get_inner_world().on_user_awaken(user_id=self.persona_self_id)
        except Exception as exc:
            self.logger.debug("inner world awaken marker degraded: %s", exc)

    def _mark_session_end(self, *, topic: str) -> None:
        try:
            from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

            get_inner_world().on_session_end(
                user_id=self.persona_self_id,
                valence=0.0,
                arousal=0.3,
                pending_topics=[str(topic or "")[:80]] if topic else [],
            )
        except Exception as exc:
            self.logger.debug("inner world session end marker degraded: %s", exc)

    def _scheduler_snapshot(self) -> dict[str, Any]:
        try:
            from host_brain.scheduler.scheduler_routes import get_scheduler_health_snapshot_safe  # noqa: PLC0415

            return dict(get_scheduler_health_snapshot_safe() or {})
        except Exception as exc:
            return {
                "scheduler_status": "UNAVAILABLE",
                "total_tasks": 0,
                "enabled_tasks": 0,
                "suspended_tasks": 0,
                "error_tasks": 0,
                "tasks": [],
                "error": str(exc)[:160],
            }

    def _inner_world_state(self) -> dict[str, Any]:
        try:
            from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

            return dict(get_inner_world().get_state(user_id=self.persona_self_id) or {})
        except Exception as exc:
            return {"user_id": self.persona_self_id, "error": str(exc)[:160]}

    def _self_directive_packet(self) -> dict[str, Any]:
        try:
            from host_brain.persona.self_model import build_self_directive_packet, load_persistent_self_state  # noqa: PLC0415

            return dict(build_self_directive_packet(load_persistent_self_state(user_id=self.persona_self_id)) or {})
        except Exception as exc:
            return {"self_update_count": 0, "error": str(exc)[:160]}

    def _background_runtime_snapshot(
        self,
        *,
        now_ms: int,
        scheduler_snapshot: Mapping[str, Any],
        inner_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            from host_brain.app.http_hooks_runtime_helpers import _build_background_runtime_snapshot  # noqa: PLC0415

            return dict(
                _build_background_runtime_snapshot(
                    now_ms=now_ms,
                    persona_user_id=self.persona_self_id,
                    inner_world_state=dict(inner_state or {}),
                    scheduler_snapshot_override=dict(scheduler_snapshot or {}),
                )
            )
        except Exception as exc:
            return {"is_advancing": False, "tick_health": "unknown", "error": str(exc)[:160]}

    def _record_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self._events.append(
            {
                "event_type": str(event_type),
                "ts_ms": _now_ms(),
                "run_id": self.run_id,
                "payload": _strip_large(dict(payload or {})),
            }
        )

    def _ensure_started(self) -> None:
        if not self.alive:
            raise RuntimeError("digital life supervisor is not started")


def _call_runner(runner: ForegroundTurnRunner, context: Mapping[str, Any]) -> Mapping[str, Any]:
    result = runner(context)
    if inspect.isawaitable(result):
        raise RuntimeError("async foreground runners must be wrapped by the caller before supervisor use")
    return dict(result or {}) if isinstance(result, Mapping) else {}


def _coerce_foreground_result(
    raw: Mapping[str, Any],
    *,
    request_id: str,
    user_message: str,
    reply_text: str,
) -> ForegroundTurnResult:
    data = dict(raw or {})
    organ_trace = dict(data.get("organ_trace") or {})
    mapped = int(
        data.get("mapped_organ_count")
        or organ_trace.get("mapped_organ_count")
        or len(list(organ_trace.get("mapped_organs") or []))
        or 0
    )
    return ForegroundTurnResult(
        request_id=str(data.get("request_id") or request_id),
        user_message=str(data.get("user_message") or user_message),
        reply_text=str(data.get("reply_text") or reply_text),
        status=str(data.get("status") or "RECORDED"),
        reason_code=str(data.get("reason_code") or ""),
        mapped_organ_count=mapped,
        user_emotion_tag=str(data.get("user_emotion_tag") or ""),
        natsume_emotion_tag=str(data.get("natsume_emotion_tag") or ""),
        vts_expression_id=str(data.get("vts_expression_id") or ""),
        reply_control_packet=dict(data.get("reply_control_packet") or {}),
        organ_trace=organ_trace,
        raw=dict(data.get("raw") or {}),
    )


def _latest_inner_event(inner_state: Mapping[str, Any]) -> dict[str, Any]:
    stream = [dict(item) for item in list(inner_state.get("inner_stream") or []) if isinstance(item, Mapping)]
    return stream[-1] if stream else {}


def _strip_large(value: Any, *, limit: int = 6000) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _strip_large(child, limit=limit) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_strip_large(item, limit=limit) for item in list(value)[:80]]
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1].rstrip() + "..."
    return value


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "fixture").strip().lower()
    return normalized if normalized in {"fixture", "live"} else "fixture"


__all__ = [
    "CORE_TICK_TASK_IDS",
    "DigitalLifeRuntimeConfig",
    "DigitalLifeSupervisor",
    "ForegroundTurnResult",
]
