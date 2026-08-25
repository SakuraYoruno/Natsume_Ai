"""Lifecycle runtime responsibility extracted from the HTTP adapter."""

from typing import Any, Callable, Mapping

from ._shared import *  # noqa: F403
from host_brain.core.ids import now_ms as _now_ms
from .life_jobs import (
    _run_emotion_decay_tick,
    _run_evolution_cycle,
    run_life_memory_sleep_replay_handler,
    run_long_term_distillation_handler,
)
from .ports import LifecycleRuntimeApplicationPorts
from .scheduler_handlers import (
    _run_codex_production_scheduler_skill,
    _run_personality_scheduler_skill,
)


LEGACY_PROACTIVE_TALK_RETIRED = "LEGACY_PROACTIVE_TALK_RETIRED"


def run_proactive_intent_dispatch(
    system_intent: dict[str, Any],
    *,
    ports: LifecycleRuntimeApplicationPorts,
    logger: Any,
    get_ledger: Callable[[], Any],
    retention_policy: Any,
    ui_pusher: Callable[..., dict[str, Any]],
    life_snapshot_reader: Callable[[], Any] | None = None,
    llm_generator_override: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    last_interaction_ts_override: int | None = None,
    motive_state_override: Mapping[str, Any] | None = None,
    render_policy_override: Mapping[str, Any] | None = None,
    execution_context: Mapping[str, Any] | None = None,
    runtime_context_application: Any | None = None,
    action_preview_service: Any | None = None,
) -> dict[str, Any]:
    skill_name = str(system_intent.get("skill_name", "") or "").strip()
    args = dict(system_intent.get("args") or {})
    if skill_name == "audit_retention":
        try:
            from host_brain.audit.retention import build_retention_policy_from_env, run_retention_policy  # noqa: PLC0415
            from host_brain.core.ids import new_request_id as _new_id  # noqa: PLC0415

            request_id = str(system_intent.get("request_id") or _new_id())
            now_value = _now_ms()
            policy = retention_policy if retention_policy is not None else build_retention_policy_from_env(now_ts_ms=now_value)
            result = run_retention_policy(
                ledger=get_ledger(),
                policy=policy,
                request_id=request_id,
                now_ts_ms=now_value,
            )
            payload = result.as_dict()
            payload["status"] = "OK" if result.success else "FAILED"
            return payload
        except Exception as retention_exc:
            logger.warning("audit_retention 调度执行失败: %s", retention_exc)
            return {
                "status": "FAILED",
                "reason_code": "AUDIT_RETENTION_FAILED",
                "detail": str(retention_exc),
            }

    if skill_name == "long_term_memory_distillation":
        return run_long_term_distillation_handler(args, {}, ports=ports)

    if skill_name == "life_memory_sleep_replay":
        return run_life_memory_sleep_replay_handler(args, {}, ports=ports)

    if skill_name == "evolution_cycle":
        return _run_evolution_cycle(system_intent=system_intent, logger=logger)

    if skill_name == "emotion_decay_tick":
        return _run_emotion_decay_tick(logger=logger)

    codex_production_result = _run_codex_production_scheduler_skill(
        system_intent=system_intent,
        logger=logger,
    )
    if codex_production_result is not None:
        return codex_production_result

    # Wrap any remaining legacy personality UI effect before its handler is
    # selected.  The retired proactive_talk path below never invokes it.
    preview_context = dict(execution_context or {}).get(
        "_action_preview_context"
    )
    raw_ui_pusher = ui_pusher

    def previewed_ui_pusher(message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(preview_context, Mapping) or action_preview_service is None:
            raise RuntimeError(
                "ACTION_PREVIEW_INVALID: personality UI effect has no app-local authority"
            )
        from host_brain.runtime.action_preview_authority import (  # noqa: PLC0415
            require_scheduler_derived_action,
        )

        concrete = dict(message or {})
        scope = str(
            concrete.get("delivery_id")
            or concrete.get("request_id")
            or system_intent.get("request_id")
            or "proactive.gateway"
        ).strip()
        request_id = str(concrete.get("request_id") or "").strip()
        if request_id and request_id != scope:
            raise RuntimeError(
                "ACTION_PREVIEW_DRIFT: proactive request_id differs from action scope"
            )
        concrete["request_id"] = scope
        receipt = require_scheduler_derived_action(
            service=action_preview_service,
            preview_context=preview_context,
            action_kind="proactive.ui_push",
            action_scope=scope,
            risk_level=3,
            action_args=concrete,
            guardian_capability_id="proactive_ui_push",
        )
        from host_brain.runtime.action_preview_proof import (  # noqa: PLC0415
            ActionPreviewExecutionProof,
        )

        proof = ActionPreviewExecutionProof(
            service=action_preview_service,
            event_id=str(receipt.get("event_id") or ""),
            boundary=dict(receipt.get("_terminal_execution_boundary") or {}),
        )
        result = dict(raw_ui_pusher(concrete, preview_proof=proof) or {})
        result["action_preview_receipt_ref"] = str(
            receipt.get("receipt_ref") or ""
        )
        result["action_preview_event_id"] = str(
            receipt.get("event_id") or ""
        )
        return result

    ui_pusher = previewed_ui_pusher

    if skill_name != "proactive_talk":
        personality_result = _run_personality_scheduler_skill(
            ports=ports,
            system_intent=system_intent,
            logger=logger,
            get_ledger=get_ledger,
            ui_pusher=ui_pusher,
            llm_generator_override=llm_generator_override,
        )
        if personality_result is not None:
            return personality_result
        return {"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL", "skill_name": skill_name}

    # The canonical Life tick owns proactive companionship.  This branch keeps
    # the legacy Scheduler API fail-closed for an already-admitted stale
    # occurrence, but never reopens Bond, EmotionTimeline, Limbic or template
    # logic to plan a second proactive message.
    try:
        frozen_runtime_context, _scene_projection = _require_proactive_runtime_context(
            execution_context=execution_context,
            runtime_context_application=runtime_context_application,
        )
    except Exception as exc:
        return {
            "status": "REJECTED",
            "reason_code": str(
                getattr(exc, "reason_code", None)
                or "PROACTIVE_SCENE_CONTEXT_INVALID"
            ),
            "detail": str(exc)[:300],
            "skill_name": skill_name,
        }
    scene_identity = dict(frozen_runtime_context["scene_identity_v2"])
    return {
        "status": "SKIPPED",
        "reason_code": LEGACY_PROACTIVE_TALK_RETIRED,
        "detail": (
            "legacy proactive_talk is retired; canonical Life tick and "
            "CompanionProactiveDecision own proactive delivery"
        ),
        "skill_name": skill_name,
        "scene_identity_v2": scene_identity,
        "scene_decision_digest": str(scene_identity.get("decision_digest") or ""),
    }


class _ProactiveContextError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "PROACTIVE_SCENE_CONTEXT_INVALID")
        super().__init__(detail)


def _require_proactive_runtime_context(
    *,
    execution_context: Mapping[str, Any] | None,
    runtime_context_application: Any | None,
) -> tuple[dict[str, Any], Any]:
    """Verify Task Runtime authority and reopen the exact frozen Scene."""

    context = dict(execution_context or {})
    verified_grant = context.get("verified_node_grant")
    required_execution_fields = (
        "work_order_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "lease_id",
        "fencing_token",
    )
    if not isinstance(verified_grant, Mapping) or any(
        context.get(key) in (None, "") for key in required_execution_fields
    ):
        raise _ProactiveContextError(
            "PROACTIVE_NODE_AUTHORITY_REQUIRED",
            "proactive execution requires a verified governed Scheduler node",
        )
    if any(
        str(verified_grant.get(key) or "").strip()
        != str(context.get(context_key) or "").strip()
        for key, context_key in (
            ("graph_id", "graph_id"),
            ("graph_revision", "graph_revision"),
            ("node_id", "node_id"),
            ("grant_ref", "node_grant_ref"),
        )
    ):
        raise _ProactiveContextError(
            "PROACTIVE_NODE_AUTHORITY_DRIFT",
            "verified proactive node authority differs from execution lineage",
        )
    frozen_input = context.get("frozen_input_pack")
    if not isinstance(frozen_input, Mapping):
        raise _ProactiveContextError(
            "PROACTIVE_SCENE_CONTEXT_REQUIRED",
            "proactive WorkOrder lacks a frozen runtime context",
        )
    require_frozen = getattr(runtime_context_application, "require_frozen", None)
    if not callable(require_frozen):
        raise _ProactiveContextError(
            "PROACTIVE_SCENE_AUTHORITY_REQUIRED",
            "proactive Scene requires an app-local runtime context authority",
        )
    try:
        frozen = require_frozen(dict(frozen_input)).to_dict()
        from host_brain.runtime.scene_consumer_projection import (  # noqa: PLC0415
            project_scene_decision_v2,
        )

        projection = project_scene_decision_v2(
            frozen.get("scene_decision_v2"),
            expected_identity=frozen.get("scene_identity_v2"),
        )
    except Exception as exc:
        raise _ProactiveContextError(
            str(getattr(exc, "reason_code", None) or "PROACTIVE_SCENE_CONTEXT_INVALID"),
            "proactive frozen Scene is missing, tampered, or no longer canonical",
        ) from exc
    return frozen, projection
