"""Read-only Life evolution views for World/Appraisal/Bond/Scene/Arbiter."""

from __future__ import annotations

import inspect
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, HTTPException, Query

from host_brain.app.obs_routes_core import router
from host_brain.app.settings.core import get_core_settings
from host_brain.persona.life_evolution_view import build_life_evolution_view


SnapshotProvider = Callable[..., Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
_snapshot_provider: SnapshotProvider | None = None
ActionContextProvider = Callable[..., Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
_action_context_provider: ActionContextProvider | None = None


def set_life_evolution_snapshot_provider(provider: SnapshotProvider | None) -> None:
    """Install a test/composition provider without adding a write authority."""
    global _snapshot_provider
    _snapshot_provider = provider


def set_life_evolution_action_context_provider(provider: ActionContextProvider | None) -> None:
    """Install a server-side provider for already committed Governor context.

    Browser payloads are never authority.  Without this internal provider a
    preview remains a useful fail-closed BLOCK and cannot claim executable
    approval merely because a caller supplied self-consistent token hashes.
    """
    global _action_context_provider
    _action_context_provider = provider


def _require_life_views_enabled() -> None:
    if not get_core_settings().nle_life_views_enabled:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "NLE_LIFE_VIEWS_DISABLED",
                "detail": "set HOST_NLE_LIFE_VIEWS_ENABLED=true to expose Life projections",
            },
        )


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _projection_enabled() -> bool:
    from host_brain.persona.scene_policy import life_projection_enabled

    return life_projection_enabled()


async def _default_snapshot(
    *,
    person_id: str,
    session_id: str,
    fact_keys: list[str],
    now_ms: int,
) -> dict[str, Any]:
    from host_brain.app.dependencies import get_bond_stage_engine, get_ledger, get_state_arbiter
    from host_brain.bond.bond_projection import build_bond_projection
    from host_brain.governor.action_preview import preview_action
    from host_brain.persona.life_evolution_observer import (
        read_life_evolution_observations,
        read_supervisor_appraisal_events,
    )
    from host_brain.runtime.world_model import build_world_snapshot
    from host_brain.tribe.social_brain_agent import get_social_brain_agent

    ledger = get_ledger()
    enabled = _projection_enabled()
    world = await build_world_snapshot(
        ledger=ledger,
        person_id=person_id,
        state_arbiter=get_state_arbiter(),
        fact_keys=fact_keys,
        session_id=session_id,
        now_ms=now_ms,
        enabled=enabled,
    )
    observations = read_life_evolution_observations(person_id=person_id)
    appraisals = list(observations.get("appraisals") or [])
    configured_dir = str(os.environ.get("HOST_NLE_LIFE_APPRAISAL_DIR") or "").strip()
    if not configured_dir:
        candidate = Path(__file__).resolve().parents[2] / "artifacts" / "digital_life_host_runtime" / "state"
        configured_dir = str(candidate) if candidate.is_dir() else ""
    if configured_dir:
        appraisals.extend(read_supervisor_appraisal_events(configured_dir, limit=20))
    appraisals = list(
        {
            str(item.get("appraisal_id") or f"missing-{index}"): item
            for index, item in enumerate(appraisals)
            if isinstance(item, Mapping)
        }.values()
    )
    bond = build_bond_projection(
        user_id=person_id,
        stage_engine=get_bond_stage_engine(),
        social_brain=get_social_brain_agent(),
        appraisal_records=appraisals,
        now_ms=now_ms,
        enabled=enabled,
    )
    scene = dict(observations.get("scene_decision") or {})
    action_arbiter = dict(observations.get("action_arbiter_decision") or {})
    preview = preview_action(
        governor_decision={},
        proposed_action={
            "action_kind": "proactive_expression",
            "scope": "conversation",
            "risk_level": 5,
            "requires_token": True,
            "is_proactive": True,
        },
        scene_decision=scene,
        user_intent={"explicit": False},
        evidence_refs=tuple(scene.get("source_refs") or ()),
        now_ms=now_ms,
        runtime_event_store=None,
        record_runtime_event=False,
    )
    preview["authority_context_status"] = "UNAVAILABLE"
    preview["authority_validation_reason"] = "CURRENT_GOVERNOR_AUTHORITY_NOT_EXPOSED"
    preview["arbiter_eligible"] = False
    preview["would_execute"] = False
    preview["finality"] = "ARBITER_ELIGIBILITY_ONLY"
    preview["execution_precheck_status"] = "NOT_RUN"
    return build_life_evolution_view(
        person_id=person_id,
        world_snapshot=world,
        appraisal_records=appraisals,
        bond_projection=bond,
        scene_decision=scene,
        action_arbiter_decision=action_arbiter,
        action_preview=preview,
        now_ms=now_ms,
    )


async def _get_life_evolution_snapshot(
    person_id: str = Query("default"),
    session_id: str = Query(""),
) -> dict[str, Any]:
    _require_life_views_enabled()
    now = int(time.time() * 1000)
    provider = _snapshot_provider or _default_snapshot
    try:
        payload = await _resolve(
            provider(
                person_id=str(person_id or "default"),
                session_id=str(session_id or ""),
                # Public UI never accepts arbitrary StateArbiter fact keys.
                fact_keys=[],
                now_ms=now,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "LIFE_EVOLUTION_READ_MODEL_UNAVAILABLE",
                "detail": str(exc)[:400],
            },
        ) from exc
    return dict(payload or {})


@router.get("/life-evolution/snapshot")
async def get_life_evolution_snapshot(
    snapshot: dict[str, Any] = Depends(_get_life_evolution_snapshot),
) -> dict[str, Any]:
    return snapshot


@router.post("/life-evolution/action-preview")
async def post_life_evolution_action_preview(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Compute a governed DRY_RUN; recording and executors are intentionally unavailable."""
    _require_life_views_enabled()
    from host_brain.governor.action_preview import preview_action

    trusted: dict[str, Any] = {}
    provider = _action_context_provider
    if provider is not None:
        try:
            preview_subject_ref = payload.get("preview_subject_ref")
            person_id = payload.get("person_id")
            resolved = await _resolve(
                provider(
                    preview_subject_ref=(
                        preview_subject_ref.strip()[:512]
                        if isinstance(preview_subject_ref, str)
                        else ""
                    ),
                    person_id=(
                        person_id.strip()[:200]
                        if isinstance(person_id, str) and person_id.strip()
                        else "default"
                    ),
                )
            )
            candidate = dict(resolved or {})
            authority_source_ref = candidate.get("authority_source_ref")
            if (
                candidate.get("authority_context_trusted") is True
                and isinstance(authority_source_ref, str)
                and authority_source_ref.strip()
            ):
                trusted = candidate
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "reason_code": "ACTION_PREVIEW_AUTHORITY_CONTEXT_UNAVAILABLE",
                    "detail": str(exc)[:400],
                },
            ) from exc

    # Only the non-authoritative proposal shape may come from the browser.  All
    # authority, intent, scene and evidence fields must originate server-side.
    proposed_action = (
        dict(trusted.get("proposed_action") or {})
        if trusted
        else dict(payload.get("proposed_action") or {})
    )
    now_ms = int(time.time() * 1000)
    authority_context_status = "UNAVAILABLE"
    authority_validation_reason = "TRUSTED_CONTEXT_PROVIDER_UNAVAILABLE"
    if trusted:
        from host_brain.app.dependencies import get_token_service
        from host_brain.governor.tokens import TOKEN_TYPE_APPROVAL

        governor = dict(trusted.get("governor_decision") or {})
        intent = dict(trusted.get("intent_envelope") or {})
        tokens = dict(governor.get("tokens") or {}) if isinstance(governor.get("tokens"), Mapping) else {}
        token_ref = tokens.get("approval_token_ref")
        try:
            seq = int(intent.get("seq"))
        except (TypeError, ValueError):
            seq = -1
        token_check = get_token_service().validate_bound_token(
            token_ref if isinstance(token_ref, str) else None,
            expected_type=TOKEN_TYPE_APPROVAL,
            intent_id=str(intent.get("id") or intent.get("intent_id") or ""),
            skill_name=str(proposed_action.get("action_kind") or proposed_action.get("action") or ""),
            session_id=str(intent.get("session_id") or ""),
            seq=seq,
            now_ts_ms=now_ms,
            consume=False,
        )
        if token_check.valid:
            authority_context_status = "SERVER_TRUSTED_TOKEN_VALID"
            authority_validation_reason = "APPROVAL_TOKEN_VALID_NOT_CONSUMED"
        else:
            authority_context_status = "SERVER_CONTEXT_TOKEN_INVALID"
            authority_validation_reason = str(token_check.reason_code or token_check.detail or "TOKEN_INVALID")
            trusted = {}
    try:
        result = preview_action(
            governor_decision=dict(trusted.get("governor_decision") or {}),
            proposed_action=proposed_action,
            intent_envelope=dict(trusted.get("intent_envelope") or {}),
            scene_decision=dict(trusted.get("scene_decision") or {}),
            user_intent=dict(trusted.get("user_intent") or {}),
            evidence_refs=tuple(
                ref.strip()[:512]
                for ref in list(trusted.get("evidence_refs") or ())[:32]
                if isinstance(ref, str) and ref.strip()
            ),
            now_ms=now_ms,
            runtime_event_store=None,
            record_runtime_event=False,
        )
        if not trusted and result.get("would_execute"):
            raise RuntimeError("untrusted preview context cannot be executable")
        arbiter_eligible = bool(result.get("would_execute"))
        # This endpoint intentionally has no handler, registry precheck or
        # executor.  Even a token-valid ALLOW is eligibility evidence only.
        result["arbiter_eligible"] = arbiter_eligible
        result["would_execute"] = False
        result["finality"] = "ARBITER_ELIGIBILITY_ONLY"
        result["execution_precheck_status"] = "NOT_RUN"
        result["authority_context_status"] = authority_context_status
        result["authority_validation_reason"] = authority_validation_reason
        result["authority_source_ref"] = (
            trusted.get("authority_source_ref", "").strip()[:512] if trusted else ""
        )
        return result
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"reason_code": "ACTION_PREVIEW_INVALID", "detail": str(exc)[:400]},
        ) from exc


__all__ = [
    "get_life_evolution_snapshot",
    "post_life_evolution_action_preview",
    "set_life_evolution_action_context_provider",
    "set_life_evolution_snapshot_provider",
]
