from __future__ import annotations

import asyncio
import inspect
import json
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
PROACTIVE_TALK_TASK_ID = "proactive_talk_periodic"
BACKGROUND_TICK_TASK_IDS: tuple[str, ...] = (PROACTIVE_TALK_TASK_ID,)
DEFAULT_TICK_TASK_IDS: tuple[str, ...] = CORE_TICK_TASK_IDS + BACKGROUND_TICK_TASK_IDS
PROACTIVE_EPISODES_FILENAME = "proactive_episodes.jsonl"
PROACTIVE_AFTEREFFECTS_FILENAME = "proactive_aftereffects.jsonl"
PROACTIVE_OUTBOX_FILENAME = "proactive_outbox.jsonl"
PROACTIVE_DELIVERY_RECEIPTS_FILENAME = "delivery_receipts.jsonl"
PROACTIVE_INTERACTIONS_FILENAME = "proactive_interactions.jsonl"

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
    tick_task_ids: Sequence[str] = DEFAULT_TICK_TASK_IDS
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
        self._proactive_outbox_count = 0
        self._last_proactive_outbox_ts_ms = 0
        self._last_proactive_outbox_record: dict[str, Any] = {}
        self._proactive_episode_count = 0
        self._last_proactive_episode_record: dict[str, Any] = {}
        self._proactive_aftereffect_count = 0
        self._last_proactive_aftereffect_record: dict[str, Any] = {}
        self._proactive_receipt_count = 0
        self._last_proactive_receipt_record: dict[str, Any] = {}
        self._proactive_interaction_count = 0
        self._last_proactive_interaction_record: dict[str, Any] = {}
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
        selected = tuple(task_ids or self.config.tick_task_ids or DEFAULT_TICK_TASK_IDS)
        results: dict[str, Any] = {}
        for task_id in selected:
            task_key = str(task_id)
            if self.registry is not None and self.registry.get(task_key) is None:
                results[task_key] = {"status": "SKIPPED", "reason_code": "TASK_NOT_REGISTERED"}
                continue
            try:
                results[task_key] = self.scheduler.trigger_task_by_id(task_key)
            except Exception as exc:  # pragma: no cover - diagnostic path
                results[task_key] = {
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
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_started()
        request_id = str(request_id or new_request_id())
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

    def run_proactive_interaction_dry_run(
        self,
        *,
        user_reply: str = "在啊，怎么啦？",
        reply_text: str = "我在，刚才那句主动问候已经收到。",
        generate_if_missing: bool = True,
    ) -> dict[str, Any]:
        self._ensure_started()
        generation_event: dict[str, Any] = {}
        outbound = self._latest_proactive_outbox_record()
        if not outbound and generate_if_missing:
            generation_event = self.run_idle_tick(task_ids=(PROACTIVE_TALK_TASK_ID,))
            outbound = self._latest_proactive_outbox_record()
        if not outbound:
            event = {
                "status": "SKIPPED",
                "reason_code": "NO_PROACTIVE_OUTBOX_MESSAGE",
                "generation_event": _strip_large(generation_event),
                "snapshot": self.snapshot(),
            }
            self._record_event("proactive_interaction_dry_run", event)
            return event

        receipt = self._write_proactive_delivery_receipt(outbound)
        request_id = new_request_id()
        thalamus_draft = self._compile_proactive_reply_intent(
            request_id=request_id,
            user_reply=user_reply,
            outbound=outbound,
            receipt=receipt,
        )
        foreground = self.run_foreground_turn(
            user_reply,
            reply_text=reply_text,
            metadata={
                "source": "proactive_interaction_dry_run",
                "proactive_outbound": _strip_large(outbound),
                "delivery_receipt": _strip_large(receipt),
                "thalamus_intent_draft": _strip_large(thalamus_draft),
            },
            request_id=request_id,
        )
        turn = dict(foreground.get("turn") or {})
        route = str(thalamus_draft.get("route") or "")
        reason = str(thalamus_draft.get("route_reason") or thalamus_draft.get("reason_code") or "")
        receipt_status = str(receipt.get("delivery_status") or "")
        status = (
            "PASS"
            if route == "chat"
            and reason == "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION"
            and receipt_status == "DELIVERED_DRY_RUN"
            and int(turn.get("mapped_organ_count") or 0) >= 23
            else "FAILED"
        )
        interaction = {
            "status": status,
            "reason_code": None if status == "PASS" else "PROACTIVE_INTERACTION_DRY_RUN_FAILED",
            "request_id": request_id,
            "run_id": self.run_id,
            "persona_self_id": self.persona_self_id,
            "session_id": self.config.session_id,
            "outbound_message_id": str(outbound.get("message_id") or ""),
            "delivery_receipt_id": str(receipt.get("receipt_id") or ""),
            "delivery_status": receipt_status,
            "inbound_text": str(user_reply or ""),
            "thalamus_route": route,
            "thalamus_reason_code": reason,
            "thalamus_route_source": str(thalamus_draft.get("route_source") or ""),
            "foreground_turn_status": str(turn.get("status") or ""),
            "mapped_organ_count": int(turn.get("mapped_organ_count") or 0),
            "ts_ms": _now_ms(),
        }
        aftereffect = self._write_proactive_aftereffect_record(
            outbound=outbound,
            receipt=receipt,
            interaction=interaction,
            user_reply=user_reply,
            reply_text=reply_text,
            outcome="replied" if status == "PASS" else "route_failed",
        )
        interaction["aftereffect_id"] = str(aftereffect.get("aftereffect_id") or "")
        interaction["aftereffect_outcome"] = str(aftereffect.get("outcome") or "")
        self._append_proactive_jsonl(self._proactive_interactions_path(), interaction)
        self._proactive_interaction_count += 1
        self._last_proactive_interaction_record = dict(interaction)
        event = {
            "status": status,
            "generation_event": _strip_large(generation_event),
            "delivery_receipt": receipt,
            "thalamus_intent_draft": _strip_large(thalamus_draft),
            "foreground_turn": _strip_large(foreground),
            "interaction": interaction,
            "aftereffect": aftereffect,
            "snapshot": self.snapshot(),
        }
        self._record_event("proactive_interaction_dry_run", event)
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
            "proactive_emotional_episode": self._proactive_episode_snapshot(),
            "proactive_emotional_aftereffect": self._proactive_aftereffect_snapshot(),
            "proactive_outbox": self._proactive_outbox_snapshot(),
            "proactive_interaction": self._proactive_interaction_snapshot(),
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
        from host_brain.scheduler.task_registry import (  # noqa: PLC0415
            SchedulerTaskConfig,
            SchedulerTaskRegistry,
            TaskPriority,
            TriggerMode,
        )

        self.registry = SchedulerTaskRegistry()
        self.health_reporter = SchedulerHealthReporter(registry=self.registry)
        set_scheduler_providers(registry=self.registry, health_reporter=self.health_reporter)
        for config in _build_core_organ_tick_task_configs():
            if self.registry.get(config.task_id) is None:
                self.registry.register(config)
        if self.registry.get(PROACTIVE_TALK_TASK_ID) is None:
            self.registry.register(
                SchedulerTaskConfig(
                    task_id=PROACTIVE_TALK_TASK_ID,
                    trigger_mode=TriggerMode.INTERVAL,
                    trigger_config={
                        "seconds": int(os.environ.get("HOST_PROACTIVE_TALK_INTERVAL_SECONDS") or 180)
                    },
                    intent_template={
                        "skill_name": "proactive_talk",
                        "args": {
                            "idle_threshold_ms": int(
                                os.environ.get("HOST_PROACTIVE_TALK_IDLE_THRESHOLD_MS") or 2_700_000
                            )
                        },
                        "proposer_node_id": "scheduler",
                    },
                    priority=TaskPriority.LOW,
                    mode_allowlist=["normal", "sleep"],
                    enabled=True,
                    description="主动外呼：空闲阈值满足后写入安全 outbox，等待通讯网关接管。",
                )
            )
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

        intent = dict(system_intent or {})
        skill_name = str(intent.get("skill_name") or "").strip()
        if skill_name != "proactive_talk":
            return run_proactive_intent_dispatch(
                intent,
                logger=self.logger,
                get_ledger=lambda: None,
                retention_policy=None,
                ui_pusher=self._queue_proactive_outbox,
            )

        episode = self._build_emotional_proactive_episode(intent)
        episode_record = self._write_proactive_episode_record(episode, scheduler_intent=intent)
        if not bool(episode.get("send_allowed")):
            result = {
                "status": "SKIPPED",
                "reason_code": "EMOTIONAL_PROACTIVE_HELD",
                "detail": str(episode.get("felt_reason") or ""),
                "skill_name": skill_name,
                "proactive_episode": episode_record,
            }
            self._record_event("proactive_episode_held", result)
            return result

        from host_brain.persona.emotional_proactive import (  # noqa: PLC0415
            episode_to_motive_state,
            episode_to_render_policy,
        )

        motive_state = episode_to_motive_state(episode)
        render_policy = episode_to_render_policy(episode)
        result = run_proactive_intent_dispatch(
            intent,
            logger=self.logger,
            get_ledger=lambda: None,
            retention_policy=None,
            ui_pusher=self._queue_proactive_outbox,
            llm_generator_override=self._proactive_template_fallback_generator,
            last_interaction_ts_override=self._last_interaction_or_outbound_ts(),
            motive_state_override=motive_state,
            render_policy_override=render_policy,
        )
        if isinstance(result, Mapping):
            result = dict(result)
            result["proactive_episode"] = episode_record
        return result

    def _last_interaction_or_outbound_ts(self) -> int | None:
        latest = max(int(self._last_foreground_ts_ms or 0), int(self._last_proactive_outbox_ts_ms or 0))
        return latest if latest > 0 else None

    def _proactive_template_fallback_generator(self, llm_input: dict[str, Any]) -> dict[str, Any]:
        motive_state = dict((llm_input or {}).get("motive_state") or {})
        episode = dict(motive_state.get("proactive_episode") or {})
        if not episode and self._last_proactive_episode_record:
            episode = dict(self._last_proactive_episode_record.get("episode") or {})
        opening_message = str(episode.get("opening_message") or "").strip()
        return {
            "greeting": opening_message,
            "content": opening_message,
            "fallback_to_template": not bool(opening_message),
            "fallback_reason": "EMOTIONAL_PROACTIVE_EPISODE",
        }

    def _queue_proactive_outbox(self, message: dict[str, Any]) -> dict[str, Any]:
        queued_at_ms = _now_ms()
        message_payload = dict(message or {})
        if not message_payload.get("proactive_episode") and self._last_proactive_episode_record:
            message_payload["proactive_episode"] = dict(self._last_proactive_episode_record.get("episode") or {})
        message_id = str(message_payload.get("event_id") or f"{PROACTIVE_TALK_TASK_ID}_{queued_at_ms}")
        record = {
            "message_id": message_id,
            "run_id": self.run_id,
            "persona_self_id": self.persona_self_id,
            "session_id": self.config.session_id,
            "delivery_channel": "safe_outbox",
            "delivery_status": "QUEUED",
            "queued_at_ms": queued_at_ms,
            "message": message_payload,
        }
        outbox_path = self._proactive_outbox_path()
        try:
            if outbox_path is not None:
                outbox_path.parent.mkdir(parents=True, exist_ok=True)
                with outbox_path.open("a", encoding="utf-8", newline="\n") as file:
                    file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                record["outbox_path"] = str(outbox_path)
            self._proactive_outbox_count += 1
            self._last_proactive_outbox_ts_ms = queued_at_ms
            self._last_proactive_outbox_record = dict(record)
            return {
                "success": True,
                "status": "QUEUED",
                "reason_code": None,
                "delivery_mode": "safe_outbox",
                "message_id": message_id,
                "queued_at_ms": queued_at_ms,
                "outbox_path": str(outbox_path) if outbox_path is not None else "",
            }
        except Exception as exc:  # pragma: no cover - filesystem diagnostic path
            self.logger.warning("proactive outbox queue failed: %s", exc)
            return {
                "success": False,
                "status": "FAILED",
                "reason_code": type(exc).__name__,
                "detail": str(exc)[:200],
                "delivery_mode": "safe_outbox",
                "message_id": message_id,
            }

    def _proactive_outbox_path(self) -> Path | None:
        if self.out_dir is None:
            return None
        return self.out_dir / PROACTIVE_OUTBOX_FILENAME

    def _proactive_delivery_receipts_path(self) -> Path | None:
        if self.out_dir is None:
            return None
        return self.out_dir / PROACTIVE_DELIVERY_RECEIPTS_FILENAME

    def _proactive_interactions_path(self) -> Path | None:
        if self.out_dir is None:
            return None
        return self.out_dir / PROACTIVE_INTERACTIONS_FILENAME

    def _proactive_episodes_path(self) -> Path | None:
        if self.out_dir is None:
            return None
        return self.out_dir / PROACTIVE_EPISODES_FILENAME

    def _proactive_aftereffects_path(self) -> Path | None:
        if self.out_dir is None:
            return None
        return self.out_dir / PROACTIVE_AFTEREFFECTS_FILENAME

    def _append_proactive_jsonl(self, path: Path | None, record: Mapping[str, Any]) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")

    def _latest_proactive_outbox_record(self) -> dict[str, Any]:
        if self._last_proactive_outbox_record:
            return dict(self._last_proactive_outbox_record)
        return _latest_jsonl_record(self._proactive_outbox_path())

    def _build_emotional_proactive_episode(self, system_intent: Mapping[str, Any]) -> dict[str, Any]:
        from host_brain.persona.emotional_proactive import build_emotional_proactive_episode  # noqa: PLC0415

        bond_summary: dict[str, Any] = {}
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            bond_summary = dict(get_social_brain_agent().resolve_scheduler_bond_summary(session_id=self.config.session_id) or {})
        except Exception as exc:
            self.logger.debug("emotional proactive bond summary unavailable: %s", exc)

        args = dict((system_intent or {}).get("args") or {})
        scene_awareness = (
            system_intent.get("scene_awareness")
            or args.get("scene_awareness")
            or {}
        )
        inner_state = self._inner_world_state()
        try:
            from host_brain.runtime.autonomous_self import derive_inner_weather  # noqa: PLC0415

            inner_weather = derive_inner_weather(inner_state, inner_state)
        except Exception:
            inner_weather = None
        episode = build_emotional_proactive_episode(
            inner_state=inner_state,
            inner_weather=inner_weather,
            bond_summary=bond_summary,
            scene_awareness=dict(scene_awareness or {}) if isinstance(scene_awareness, Mapping) else {},
            now_ms=_now_ms(),
            last_foreground_ts_ms=int(self._last_foreground_ts_ms or 0),
            last_proactive_outbox_ts_ms=int(self._last_proactive_outbox_ts_ms or 0),
        )
        return episode.as_dict()

    def _write_proactive_episode_record(
        self,
        episode: Mapping[str, Any],
        *,
        scheduler_intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = {
            "run_id": self.run_id,
            "persona_self_id": self.persona_self_id,
            "session_id": self.config.session_id,
            "episode_id": str(episode.get("episode_id") or ""),
            "ts_ms": int(episode.get("ts_ms") or _now_ms()),
            "send_allowed": bool(episode.get("send_allowed")),
            "chosen_action": str(episode.get("chosen_action") or ""),
            "motive": str(episode.get("motive") or ""),
            "felt_reason": str(episode.get("felt_reason") or ""),
            "opening_message": str(episode.get("opening_message") or ""),
            "disturbance_risk": episode.get("disturbance_risk"),
            "emotional_drive": episode.get("emotional_drive"),
            "relationship_permission": episode.get("relationship_permission"),
            "episode": dict(episode or {}),
            "scheduler_intent": _strip_large(dict(scheduler_intent or {}), limit=1600),
        }
        self._append_proactive_jsonl(self._proactive_episodes_path(), record)
        self._proactive_episode_count += 1
        self._last_proactive_episode_record = dict(record)
        self._record_event("proactive_emotional_episode", record)
        try:
            from host_brain.persona.life_evolution_observer import observe_appraisal_event  # noqa: PLC0415

            observe_appraisal_event(episode, person_id=str(episode.get("person_id") or "default"))
        except Exception:
            # Observability must never block the proactive producer.
            pass
        return record

    def _write_proactive_delivery_receipt(self, outbound: Mapping[str, Any]) -> dict[str, Any]:
        now = _now_ms()
        message_id = str(outbound.get("message_id") or "")
        receipt = {
            "receipt_id": f"dryrun_receipt_{message_id or now}",
            "message_id": message_id,
            "run_id": self.run_id,
            "persona_self_id": self.persona_self_id,
            "session_id": self.config.session_id,
            "delivery_channel": str(outbound.get("delivery_channel") or "safe_outbox"),
            "delivery_status": "DELIVERED_DRY_RUN",
            "gateway_mode": "dry_run",
            "delivered_at_ms": now,
            "outbound_queued_at_ms": int(outbound.get("queued_at_ms") or 0),
        }
        self._append_proactive_jsonl(self._proactive_delivery_receipts_path(), receipt)
        self._proactive_receipt_count += 1
        self._last_proactive_receipt_record = dict(receipt)
        return receipt

    def _write_proactive_aftereffect_record(
        self,
        *,
        outbound: Mapping[str, Any],
        receipt: Mapping[str, Any],
        interaction: Mapping[str, Any],
        user_reply: str,
        reply_text: str,
        outcome: str,
    ) -> dict[str, Any]:
        from host_brain.persona.emotional_proactive import build_emotional_proactive_aftereffect  # noqa: PLC0415

        message = dict(outbound.get("message") or {})
        episode = dict(message.get("proactive_episode") or {})
        if not episode and self._last_proactive_episode_record:
            episode = dict(self._last_proactive_episode_record.get("episode") or {})
        aftereffect = build_emotional_proactive_aftereffect(
            episode=episode,
            outcome=outcome,
            user_reply=user_reply,
            reply_text=reply_text,
            now_ms=_now_ms(),
            delivery_status=str(receipt.get("delivery_status") or ""),
            thalamus_reason_code=str(interaction.get("thalamus_reason_code") or ""),
        ).as_dict()
        record = {
            "run_id": self.run_id,
            "persona_self_id": self.persona_self_id,
            "session_id": self.config.session_id,
            "aftereffect_id": str(aftereffect.get("aftereffect_id") or ""),
            "episode_id": str(aftereffect.get("episode_id") or ""),
            "outbound_message_id": str(outbound.get("message_id") or ""),
            "delivery_receipt_id": str(receipt.get("receipt_id") or ""),
            "interaction_request_id": str(interaction.get("request_id") or ""),
            "ts_ms": int(aftereffect.get("ts_ms") or _now_ms()),
            "outcome": str(aftereffect.get("outcome") or ""),
            "reply_mode": str(aftereffect.get("reply_mode") or ""),
            "reply_warmth": aftereffect.get("reply_warmth"),
            "loneliness_delta": aftereffect.get("loneliness_delta"),
            "anticipation_delta": aftereffect.get("anticipation_delta"),
            "emotional_resolution": str(aftereffect.get("emotional_resolution") or ""),
            "aftereffect": aftereffect,
        }
        self._append_proactive_jsonl(self._proactive_aftereffects_path(), record)
        self._proactive_aftereffect_count += 1
        self._last_proactive_aftereffect_record = dict(record)
        self._record_event("proactive_emotional_aftereffect", record)
        try:
            from host_brain.persona.life_evolution_observer import observe_appraisal_event  # noqa: PLC0415

            observe_appraisal_event(aftereffect, person_id=str(aftereffect.get("person_id") or "default"))
        except Exception:
            pass
        return record

    def _compile_proactive_reply_intent(
        self,
        *,
        request_id: str,
        user_reply: str,
        outbound: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        from host_brain.tribe.thalamus_agent import get_thalamus_agent  # noqa: PLC0415

        proactive_context = {
            "in_reply_to_proactive": True,
            "outbound_message_id": str(outbound.get("message_id") or ""),
            "delivery_receipt_id": str(receipt.get("receipt_id") or ""),
            "delivery_status": str(receipt.get("delivery_status") or ""),
            "channel": str(receipt.get("delivery_channel") or outbound.get("delivery_channel") or "safe_outbox"),
        }

        async def _compile() -> dict[str, Any]:
            return await get_thalamus_agent().classify_and_compile_intent_draft(
                request_id=request_id,
                session_id=self.config.session_id,
                user_message=str(user_reply or ""),
                person_id=self.persona_self_id,
                source="proactive_interaction_dry_run",
                input_source="PROACTIVE_DRY_RUN",
                target="host",
                ts_ms=_now_ms(),
                tribe_context={"proactive_interaction": proactive_context},
            )

        return _run_coroutine_sync(_compile())

    def _proactive_episode_snapshot(self) -> dict[str, Any]:
        episodes_path = self._proactive_episodes_path()
        file_count = _count_jsonl_rows(episodes_path)
        latest = dict(self._last_proactive_episode_record or _latest_jsonl_record(episodes_path))
        episode = dict(latest.get("episode") or {})
        return {
            "enabled": True,
            "episodes_path": str(episodes_path) if episodes_path is not None else "",
            "episode_count": max(int(self._proactive_episode_count), int(file_count)),
            "last_episode_id": str(latest.get("episode_id") or episode.get("episode_id") or ""),
            "last_motive": str(latest.get("motive") or episode.get("motive") or ""),
            "last_chosen_action": str(latest.get("chosen_action") or episode.get("chosen_action") or ""),
            "last_send_allowed": bool(latest.get("send_allowed") or episode.get("send_allowed") or False),
            "last_emotional_drive": episode.get("emotional_drive", latest.get("emotional_drive")),
            "last_disturbance_risk": episode.get("disturbance_risk", latest.get("disturbance_risk")),
            "last_relationship_permission": episode.get("relationship_permission", latest.get("relationship_permission")),
            "last_felt_reason": str(latest.get("felt_reason") or episode.get("felt_reason") or ""),
            "last_opening_preview": str(latest.get("opening_message") or episode.get("opening_message") or "")[:120],
        }

    def _proactive_aftereffect_snapshot(self) -> dict[str, Any]:
        aftereffects_path = self._proactive_aftereffects_path()
        file_count = _count_jsonl_rows(aftereffects_path)
        latest = dict(self._last_proactive_aftereffect_record or _latest_jsonl_record(aftereffects_path))
        aftereffect = dict(latest.get("aftereffect") or {})
        return {
            "enabled": True,
            "aftereffects_path": str(aftereffects_path) if aftereffects_path is not None else "",
            "aftereffect_count": max(int(self._proactive_aftereffect_count), int(file_count)),
            "last_aftereffect_id": str(latest.get("aftereffect_id") or aftereffect.get("aftereffect_id") or ""),
            "last_episode_id": str(latest.get("episode_id") or aftereffect.get("episode_id") or ""),
            "last_outcome": str(latest.get("outcome") or aftereffect.get("outcome") or ""),
            "last_reply_mode": str(latest.get("reply_mode") or aftereffect.get("reply_mode") or ""),
            "last_reply_warmth": aftereffect.get("reply_warmth", latest.get("reply_warmth")),
            "last_loneliness_delta": aftereffect.get("loneliness_delta", latest.get("loneliness_delta")),
            "last_anticipation_delta": aftereffect.get("anticipation_delta", latest.get("anticipation_delta")),
            "last_resolution": str(latest.get("emotional_resolution") or aftereffect.get("emotional_resolution") or ""),
            "last_memory_should_remember": bool(
                dict(aftereffect.get("memory_candidate") or {}).get("should_remember") or False
            ),
        }

    def _proactive_outbox_snapshot(self) -> dict[str, Any]:
        outbox_path = self._proactive_outbox_path()
        file_count = 0
        if outbox_path is not None and outbox_path.exists():
            try:
                file_count = sum(1 for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip())
            except Exception:
                file_count = 0
        latest = dict(self._last_proactive_outbox_record or {})
        message = dict(latest.get("message") or {})
        episode = dict(message.get("proactive_episode") or {})
        return {
            "enabled": True,
            "delivery_mode": "safe_outbox",
            "outbox_path": str(outbox_path) if outbox_path is not None else "",
            "queued_count": max(int(self._proactive_outbox_count), int(file_count)),
            "last_message_id": str(latest.get("message_id") or ""),
            "last_delivery_status": str(latest.get("delivery_status") or ""),
            "last_queued_at_ms": int(latest.get("queued_at_ms") or 0),
            "last_content_preview": str(message.get("content") or "")[:120],
            "last_episode_id": str(episode.get("episode_id") or ""),
            "last_episode_motive": str(episode.get("motive") or ""),
            "last_episode_action": str(episode.get("chosen_action") or ""),
        }

    def _proactive_interaction_snapshot(self) -> dict[str, Any]:
        receipts_path = self._proactive_delivery_receipts_path()
        interactions_path = self._proactive_interactions_path()
        receipt_count = _count_jsonl_rows(receipts_path)
        interaction_count = _count_jsonl_rows(interactions_path)
        last_receipt = dict(self._last_proactive_receipt_record or _latest_jsonl_record(receipts_path))
        last_interaction = dict(self._last_proactive_interaction_record or _latest_jsonl_record(interactions_path))
        return {
            "enabled": True,
            "delivery_receipts_path": str(receipts_path) if receipts_path is not None else "",
            "interactions_path": str(interactions_path) if interactions_path is not None else "",
            "delivery_receipt_count": max(int(self._proactive_receipt_count), int(receipt_count)),
            "interaction_count": max(int(self._proactive_interaction_count), int(interaction_count)),
            "last_delivery_status": str(last_receipt.get("delivery_status") or ""),
            "last_receipt_id": str(last_receipt.get("receipt_id") or ""),
            "last_status": str(last_interaction.get("status") or ""),
            "last_thalamus_route": str(last_interaction.get("thalamus_route") or ""),
            "last_thalamus_reason_code": str(last_interaction.get("thalamus_reason_code") or ""),
            "last_mapped_organ_count": int(last_interaction.get("mapped_organ_count") or 0),
        }

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
            from host_brain.app.surface_main_chain_adapter import build_surface_context_payload_from_adapter  # noqa: PLC0415
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
            surface_context = build_surface_context_payload_from_adapter(
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


def _latest_jsonl_record(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                return dict(payload)
    except Exception:
        return {}
    return {}


def _count_jsonl_rows(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except Exception:
        return 0


def _run_coroutine_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("cannot run proactive interaction dry-run from an active event loop")


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
