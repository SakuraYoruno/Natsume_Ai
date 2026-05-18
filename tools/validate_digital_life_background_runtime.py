from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_inner_world_self_loop import (  # noqa: E402
    FixtureInnerActivityModel,
    _run_circuit_breaker_probe,
    _rewind_inner_world_for_idle_tick,
)


DEFAULT_OUT = ROOT / "artifacts" / "digital_life_background_runtime"
CORE_TASK_IDS = (
    "inner_world_numeric_tick",
    "inner_world_monologue_tick",
    "silence_tick",
    "body_sense_tick",
    "emotion_decay_tick",
)
HEAVY_BACKGROUND_TASK_IDS = (
    "proactive_talk_periodic",
    "long_term_memory_distillation",
    "gaming_companion_tick",
    "emergent_growth_weekly",
    "evolution_cycle_daily",
)
PRIVATE_THOUGHT_TEMPLATE_MARKERS = (
    "tracking topic:",
    "fallback",
    "template",
    "prompt",
    "model",
    "score",
    "ReplyControlPacket",
    "self_model",
    "inner_monologue",
    "自我连续性",
    "连续性修正",
    "文本自然度",
    "模板感",
    "模板",
    "生成",
    "回复",
    "控制包",
    "模型",
)


@dataclass
class RuntimePatchContext:
    out_dir: Path
    inner_model: FixtureInnerActivityModel | None = None
    old_env: dict[str, str | None] | None = None
    old_world: Any | None = None
    old_limbic_agent: Any | None = None
    old_default_agent: Any | None = None
    old_scheduler_registry: Any | None = None
    old_scheduler_health: Any | None = None
    old_generate_inner_activity_note: Any | None = None
    old_self_dir: str | None = None
    old_inner_world_constants: dict[str, Any] | None = None

    def __enter__(self) -> "RuntimePatchContext":
        keys = (
            "HOST_BACKGROUND_TASKS_ENABLED",
            "HOST_CONTINUOUS_ORGAN_TICKS_ENABLED",
            "HOST_CORE_INLINE_DEFAULT_MODE_TICK_ENABLED",
            "NATSUME_INNER_WORLD_LLM_ENABLED",
            "HOST_INNER_ACTIVITY_LLM_ENABLED",
            "NATSUME_INNER_WORLD_DIR",
            "NATSUME_SELF_MODEL_DIR",
            "NATSUME_INNER_STREAM_MIN_INTERVAL_S",
            "NATSUME_INNER_STREAM_CALM_INTERVAL_S",
            "NATSUME_INNER_WORLD_OFFLINE_THRESHOLD_S",
            "HOST_BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN_MS",
            "HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS",
        )
        self.old_env = {key: os.environ.get(key) for key in keys}
        os.environ["HOST_BACKGROUND_TASKS_ENABLED"] = "false"
        os.environ["HOST_CONTINUOUS_ORGAN_TICKS_ENABLED"] = "true"
        os.environ["HOST_CORE_INLINE_DEFAULT_MODE_TICK_ENABLED"] = "true"
        os.environ["NATSUME_INNER_WORLD_LLM_ENABLED"] = "true"
        os.environ["HOST_INNER_ACTIVITY_LLM_ENABLED"] = "true"
        os.environ["NATSUME_INNER_WORLD_DIR"] = str(self.out_dir / "inner_world_state")
        os.environ["NATSUME_SELF_MODEL_DIR"] = str(self.out_dir / "self_model_state")
        os.environ["NATSUME_INNER_STREAM_MIN_INTERVAL_S"] = "0"
        os.environ["NATSUME_INNER_STREAM_CALM_INTERVAL_S"] = "0"
        os.environ["NATSUME_INNER_WORLD_OFFLINE_THRESHOLD_S"] = "0"
        os.environ["HOST_BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN_MS"] = "0"
        os.environ["HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS"] = "0"

        import host_brain.persona.inner_activity_agent as inner_activity_agent  # noqa: PLC0415
        import host_brain.persona.inner_world as inner_world  # noqa: PLC0415
        import host_brain.persona.self_model as self_model  # noqa: PLC0415
        import host_brain.scheduler.scheduler_routes as scheduler_routes  # noqa: PLC0415
        import host_brain.tribe.default_mode_agent as default_mode_agent  # noqa: PLC0415
        import host_brain.tribe.limbic_agent as limbic_agent  # noqa: PLC0415
        from host_brain.persona.inner_world import OfflineInnerWorld  # noqa: PLC0415
        from host_brain.tribe.default_mode_agent import DefaultModeAgent  # noqa: PLC0415
        from host_brain.tribe.limbic_agent import LimbicAgent  # noqa: PLC0415
        from host_brain.tribe.tribe_state import TribeStateRegistry  # noqa: PLC0415

        self.old_world = getattr(inner_world, "_global_inner_world", None)
        self.old_limbic_agent = getattr(limbic_agent, "_LIMBIC_AGENT", None)
        self.old_default_agent = getattr(default_mode_agent, "_DEFAULT_MODE_AGENT", None)
        self.old_scheduler_registry = getattr(scheduler_routes, "_registry", None)
        self.old_scheduler_health = getattr(scheduler_routes, "_health_reporter", None)
        self.old_self_dir = str(getattr(self_model, "_DEFAULT_PERSISTENT_SELF_DIR", ""))
        self.old_inner_world_constants = {
            "_DEFAULT_STATE_DIR": getattr(inner_world, "_DEFAULT_STATE_DIR", None),
            "_OFFLINE_IDLE_THRESHOLD_MS": getattr(inner_world, "_OFFLINE_IDLE_THRESHOLD_MS", None),
            "_INNER_STREAM_MIN_INTERVAL_MS": getattr(inner_world, "_INNER_STREAM_MIN_INTERVAL_MS", None),
            "_INNER_STREAM_CALM_INTERVAL_MS": getattr(inner_world, "_INNER_STREAM_CALM_INTERVAL_MS", None),
            "_INNER_WORLD_LLM_ENABLED": getattr(inner_world, "_INNER_WORLD_LLM_ENABLED", None),
        }

        if self.inner_model is not None:
            self.old_generate_inner_activity_note = inner_activity_agent.generate_inner_activity_note
            inner_activity_agent.generate_inner_activity_note = self.inner_model.generate_inner_activity_note

        inner_world._DEFAULT_STATE_DIR = os.environ["NATSUME_INNER_WORLD_DIR"]
        inner_world._OFFLINE_IDLE_THRESHOLD_MS = 0
        inner_world._INNER_STREAM_MIN_INTERVAL_MS = 0
        inner_world._INNER_STREAM_CALM_INTERVAL_MS = 0
        inner_world._INNER_WORLD_LLM_ENABLED = True
        inner_world._reset_inner_world_llm_circuit()
        inner_world._global_inner_world = OfflineInnerWorld(state_dir=os.environ["NATSUME_INNER_WORLD_DIR"])

        self_model._DEFAULT_PERSISTENT_SELF_DIR = os.environ["NATSUME_SELF_MODEL_DIR"]
        self_model._PERSISTENT_SELF_CACHE.clear()

        registry = TribeStateRegistry()
        limbic_agent.set_limbic_agent(LimbicAgent(state_registry=registry))
        default_mode_agent.set_default_mode_agent(DefaultModeAgent(state_registry=registry))
        default_mode_agent._FOREGROUND_INTERACTION_CACHE["ts_ms"] = 0
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        import host_brain.persona.inner_activity_agent as inner_activity_agent  # noqa: PLC0415
        import host_brain.persona.inner_world as inner_world  # noqa: PLC0415
        import host_brain.persona.self_model as self_model  # noqa: PLC0415
        import host_brain.scheduler.scheduler_routes as scheduler_routes  # noqa: PLC0415
        import host_brain.tribe.default_mode_agent as default_mode_agent  # noqa: PLC0415
        import host_brain.tribe.limbic_agent as limbic_agent  # noqa: PLC0415

        if self.old_generate_inner_activity_note is not None:
            inner_activity_agent.generate_inner_activity_note = self.old_generate_inner_activity_note
        inner_world._global_inner_world = self.old_world
        for key, value in dict(self.old_inner_world_constants or {}).items():
            setattr(inner_world, key, value)
        inner_world._reset_inner_world_llm_circuit()

        limbic_agent.set_limbic_agent(self.old_limbic_agent)
        default_mode_agent.set_default_mode_agent(self.old_default_agent)
        default_mode_agent._FOREGROUND_INTERACTION_CACHE["ts_ms"] = 0

        self_model._DEFAULT_PERSISTENT_SELF_DIR = self.old_self_dir or self_model._DEFAULT_PERSISTENT_SELF_DIR
        self_model._PERSISTENT_SELF_CACHE.clear()

        scheduler_routes._registry = self.old_scheduler_registry
        scheduler_routes._health_reporter = self.old_scheduler_health

        for key, value in dict(self.old_env or {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_background_runtime_validation(args: argparse.Namespace) -> dict[str, Any]:
    mode = _resolve_mode(args)
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    inner_model = FixtureInnerActivityModel() if mode == "fixture" else None
    try:
        with RuntimePatchContext(out_dir=out_dir, inner_model=inner_model):
            return _run_validation(args=args, out_dir=out_dir, mode=mode, inner_model=inner_model)
    except Exception as exc:
        return _write_failure_result(out_dir=out_dir, mode=mode, exc=exc)


def _run_validation(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    mode: str,
    inner_model: FixtureInnerActivityModel | None,
) -> dict[str, Any]:
    from host_brain.app.http_hooks import _build_core_organ_tick_task_configs  # noqa: PLC0415
    from host_brain.app.http_hooks_runtime_helpers import (  # noqa: PLC0415
        _build_background_runtime_snapshot,
        run_proactive_intent_dispatch,
    )
    from host_brain.persona.inner_world import _inner_world_llm_circuit_snapshot, get_inner_world  # noqa: PLC0415
    from host_brain.persona.self_model import build_self_directive_packet, load_persistent_self_state  # noqa: PLC0415
    from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415
    from host_brain.scheduler.scheduler import NatsumeScheduler  # noqa: PLC0415
    from host_brain.scheduler.scheduler_routes import (  # noqa: PLC0415
        get_scheduler_health_snapshot_safe,
        set_scheduler_providers,
    )
    from host_brain.scheduler.task_registry import SchedulerTaskRegistry  # noqa: PLC0415

    logger = logging.getLogger("Natsume.validation.background_runtime")
    run_id = str(getattr(args, "run_id", "") or uuid.uuid4().hex[:8])
    user_id = str(getattr(args, "user_id", "") or "user_1").strip() or "user_1"

    registry = SchedulerTaskRegistry()
    health_reporter = SchedulerHealthReporter(registry=registry)
    set_scheduler_providers(registry=registry, health_reporter=health_reporter)
    for config in _build_core_organ_tick_task_configs():
        if registry.get(config.task_id) is None:
            registry.register(config)

    def _ui_pusher(_message: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": False,
            "status": "SKIPPED",
            "detail": "background runtime validation does not push UI messages",
        }

    def _intent_dispatcher(system_intent: dict[str, Any]) -> dict[str, Any]:
        return run_proactive_intent_dispatch(
            system_intent,
            logger=logger,
            get_ledger=lambda: None,
            retention_policy=None,
            ui_pusher=_ui_pusher,
        )

    scheduler = NatsumeScheduler(
        registry=registry,
        health_reporter=health_reporter,
        intent_dispatcher=_intent_dispatcher,
        get_emotion_state=_steady_scheduler_emotion_state,
        get_immune_gate_state=lambda: {},
        persist_tasks=False,
    )

    world = get_inner_world()
    world.on_session_end(
        user_id=user_id,
        valence=-0.18,
        arousal=0.48,
        pending_topics=["后台生命节律", "自我连续性"],
    )
    _rewind_inner_world_for_idle_tick(
        world,
        user_id=user_id,
        minutes=int(getattr(args, "rewind_minutes", 30) or 30),
        topics=["后台生命节律", "自我连续性"],
    )

    trigger_results: dict[str, Any] = {}
    for task_id in CORE_TASK_IDS:
        if task_id == "inner_world_monologue_tick":
            _rewind_inner_world_for_idle_tick(
                world,
                user_id=user_id,
                minutes=int(getattr(args, "rewind_minutes", 30) or 30),
                topics=["后台生命节律", "自我连续性"],
            )
        trigger_results[task_id] = scheduler.trigger_task_by_id(task_id)

    now_ms = int(time.time() * 1000)
    scheduler_snapshot = get_scheduler_health_snapshot_safe()
    inner_state = dict(world.get_state(user_id=user_id) or {})
    background_runtime = _build_background_runtime_snapshot(
        now_ms=now_ms,
        persona_user_id=user_id,
        inner_world_state=inner_state,
        scheduler_snapshot_override=scheduler_snapshot,
    )
    latest_private_thought = _latest_inner_event(inner_state)
    monologue_result = dict(dict(trigger_results.get("inner_world_monologue_tick") or {}).get("result") or {})
    default_mode_tick = dict(monologue_result.get("default_mode_tick") or {})
    self_directive = build_self_directive_packet(load_persistent_self_state(user_id=user_id))
    breaker_probe = _run_circuit_breaker_probe(out_dir=out_dir, user_id=f"{user_id}-breaker")

    details = {
        "mode": mode,
        "run_id": run_id,
        "user_id": user_id,
        "env_contract": {
            "HOST_BACKGROUND_TASKS_ENABLED": os.environ.get("HOST_BACKGROUND_TASKS_ENABLED"),
            "HOST_CONTINUOUS_ORGAN_TICKS_ENABLED": os.environ.get("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED"),
            "HOST_CORE_INLINE_DEFAULT_MODE_TICK_ENABLED": os.environ.get("HOST_CORE_INLINE_DEFAULT_MODE_TICK_ENABLED"),
        },
        "registered_task_ids": [task.task_id for task in registry.all_tasks()],
        "heavy_background_task_ids": list(HEAVY_BACKGROUND_TASK_IDS),
        "trigger_results": trigger_results,
        "scheduler_snapshot": scheduler_snapshot,
        "background_runtime": background_runtime,
        "inner_world": {
            "state": _project_inner_state(inner_state),
            "latest_private_thought": latest_private_thought,
            "llm_circuit": _inner_world_llm_circuit_snapshot(),
        },
        "default_mode_tick": default_mode_tick,
        "self_model": self_directive,
        "inner_model_calls": list(getattr(inner_model, "calls", []) if inner_model is not None else []),
        "breaker_probe": breaker_probe,
    }
    checks = _checks(details)
    summary = {
        "mode": mode,
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "passed": all(checks.values()),
        "run_id": run_id,
        "user_id": user_id,
        "core_task_count": len(CORE_TASK_IDS),
        "registered_task_count": len(details["registered_task_ids"]),
        "private_thought_count": int(inner_state.get("spontaneous_event_count") or 0),
        "self_update_count": int(self_directive.get("self_update_count") or 0),
        "tick_health": background_runtime.get("tick_health"),
        "is_advancing": bool(background_runtime.get("is_advancing")),
        "latest_private_thought": str(
            latest_private_thought.get("private_text")
            or latest_private_thought.get("note")
            or ""
        ),
        "checks": checks,
        "output_dir": str(out_dir),
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {
        "summary": summary,
        "details": details,
        "output_dir": str(out_dir),
    }


def _steady_scheduler_emotion_state() -> dict[str, Any]:
    return {
        "emotion_tag": "NEUTRAL",
        "mood_state": "steady",
        "endocrine_state": {
            "gate_state": "open",
            "cortisol_level": 0.08,
            "energy_reserve": 0.82,
        },
    }


def _latest_inner_event(inner_state: Mapping[str, Any]) -> dict[str, Any]:
    stream = [dict(item) for item in list(inner_state.get("inner_stream") or []) if isinstance(item, Mapping)]
    return stream[-1] if stream else {}


def _project_inner_state(inner_state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "user_id": inner_state.get("user_id"),
        "loneliness": inner_state.get("loneliness"),
        "anticipation": inner_state.get("anticipation"),
        "anticipation_topics": list(inner_state.get("anticipation_topics") or []),
        "continuity_score": inner_state.get("continuity_score"),
        "spontaneous_event_count": inner_state.get("spontaneous_event_count"),
        "last_spontaneous_ts_ms": inner_state.get("last_spontaneous_ts_ms"),
        "last_spontaneous_source": inner_state.get("last_spontaneous_source"),
        "inner_stream_size": len(list(inner_state.get("inner_stream") or [])),
    }


def _checks(details: Mapping[str, Any]) -> dict[str, bool]:
    registered = {str(item) for item in list(details.get("registered_task_ids") or [])}
    trigger_results = {
        str(key): dict(value or {})
        for key, value in dict(details.get("trigger_results") or {}).items()
        if isinstance(value, Mapping)
    }
    scheduler_snapshot = dict(details.get("scheduler_snapshot") or {})
    tasks = [dict(item) for item in list(scheduler_snapshot.get("tasks") or []) if isinstance(item, Mapping)]
    task_health = {str(item.get("task_id") or ""): item for item in tasks}
    background_runtime = dict(details.get("background_runtime") or {})
    inner_world = dict(details.get("inner_world") or {})
    latest = dict(inner_world.get("latest_private_thought") or {})
    latest_text = str(latest.get("private_text") or latest.get("note") or "").strip()
    default_mode_tick = dict(details.get("default_mode_tick") or {})
    self_model = dict(details.get("self_model") or {})
    env_contract = dict(details.get("env_contract") or {})
    heavy_tasks = set(HEAVY_BACKGROUND_TASK_IDS)
    core_tasks = set(CORE_TASK_IDS)
    return {
        "heavy_background_disabled": str(env_contract.get("HOST_BACKGROUND_TASKS_ENABLED") or "").lower() == "false",
        "continuous_core_ticks_enabled": str(env_contract.get("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED") or "").lower() == "true",
        "core_tick_tasks_registered": core_tasks.issubset(registered),
        "core_only_scheduler_registry": registered == core_tasks,
        "heavy_background_tasks_absent": not bool(registered & heavy_tasks),
        "manual_core_ticks_executed": all(
            str(trigger_results.get(task_id, {}).get("status") or "").upper() not in {"ERROR", "FAILED", "SUSPENDED"}
            for task_id in CORE_TASK_IDS
        ),
        "scheduler_health_has_no_core_errors": all(
            str(task_health.get(task_id, {}).get("status") or "").upper() not in {"ERROR", "SUSPENDED"}
            for task_id in CORE_TASK_IDS
        ),
        "background_runtime_is_advancing": bool(background_runtime.get("is_advancing")),
        "background_tick_health_ok": str(background_runtime.get("tick_health") or "") in {"fresh", "delayed"},
        "monologue_generated_private_thought": bool(latest_text),
        "private_thought_source_is_llm": str(latest.get("source") or "").lower() == "llm",
        "private_thought_not_template_fallback": bool(latest_text)
        and not any(marker in latest_text for marker in PRIVATE_THOUGHT_TEMPLATE_MARKERS),
        "fixture_inner_model_called": str(details.get("mode") or "") == "live"
        or len(list(details.get("inner_model_calls") or [])) >= 1,
        "default_mode_inline_tick_ran": str(default_mode_tick.get("status") or "").upper() == "OK",
        "default_mode_suppressed_background_side_effects": bool(
            default_mode_tick.get("background_side_effects_suppressed")
        ),
        "self_model_updated_by_background_tick": int(self_model.get("self_update_count") or 0) >= 1,
        "breaker_opens_when_model_unavailable": bool(dict(details.get("breaker_probe") or {}).get("passed")),
    }


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    latest = dict((details.get("inner_world") or {}).get("latest_private_thought") or {})
    lines = [
        "# Digital Life Background Runtime Validation",
        "",
        "This is a contract validation, not text quality scoring.",
        "",
        f"- mode: `{summary.get('mode')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- core_task_count: `{summary.get('core_task_count')}`",
        f"- registered_task_count: `{summary.get('registered_task_count')}`",
        f"- tick_health: `{summary.get('tick_health')}`",
        f"- is_advancing: `{summary.get('is_advancing')}`",
        f"- private_thought_count: `{summary.get('private_thought_count')}`",
        f"- self_update_count: `{summary.get('self_update_count')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in dict(summary.get("checks") or {}).items():
        lines.append(f"- [{'x' if value else ' '}] `{key}`")
    lines.extend(
        [
            "",
            "## Core Tasks",
            "",
            "```json",
            json.dumps(details.get("registered_task_ids") or [], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Latest Private Thought",
            "",
            f"- source: `{latest.get('source')}` focus: `{latest.get('focus')}`",
            f"- text: {latest.get('private_text') or latest.get('note') or ''}",
            "",
            "## Default Mode Inline Tick",
            "",
            "```json",
            json.dumps(details.get("default_mode_tick") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Background Runtime",
            "",
            "```json",
            json.dumps(details.get("background_runtime") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Trigger Results",
            "",
            "```json",
            json.dumps(details.get("trigger_results") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Breaker Probe",
            "",
            "```json",
            json.dumps(details.get("breaker_probe") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_failure_result(*, out_dir: Path, mode: str, exc: BaseException) -> dict[str, Any]:
    payload = {
        "summary": {
            "mode": mode,
            "evaluation_mode": "contract_validation_no_text_quality_scoring",
            "passed": False,
            "error": str(exc),
            "output_dir": str(out_dir),
        },
        "details": {
            "error": str(exc),
            "traceback": traceback.format_exc(),
        },
        "output_dir": str(out_dir),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "summary.json", payload["summary"])
    _write_json(out_dir / "details.json", payload["details"])
    (out_dir / "report.md").write_text(
        "# Digital Life Background Runtime Validation\n\nValidation failed before checks completed.\n",
        encoding="utf-8",
    )
    return payload


def _resolve_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "mode", "fixture") or "fixture").strip().lower()
    return "live" if mode == "live" else "fixture"


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate core digital-life background runtime without enabling heavy background tasks.",
    )
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--user-id", default="user_1")
    parser.add_argument("--rewind-minutes", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_background_runtime_validation(args)
    summary = dict(result.get("summary") or {})
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(summary.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
