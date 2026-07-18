"""Read-only Life evolution views for World/Appraisal/Bond/Scene/Arbiter."""

from __future__ import annotations

import inspect
import time
from collections.abc import Mapping
from typing import Any

from fastapi import Body, Depends, HTTPException, Query, Request

from host_brain.app.obs_route_ports import (
    require_obs_query_projection_store,
    require_obs_route_ports,
)
from host_brain.app.obs_routes_core import router
from host_brain.app.settings.core import get_core_settings
from host_brain.persona.life_evolution_view import build_life_evolution_view


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


async def _get_life_evolution_snapshot(
    projection_store: Any = Depends(require_obs_query_projection_store),
    person_id: str = Query("default"),
    session_id: str = Query(""),
) -> dict[str, Any]:
    _require_life_views_enabled()
    del session_id
    from host_brain.app.observability_runtime.domain_state_projection import (
        LIFE_EVOLUTION_KIND,
        LIFE_MAX_AGE_MS,
        read_domain_projection_from_store,
    )

    key = str(person_id or "default").strip() or "default"
    payload, projection = read_domain_projection_from_store(
        projection_store,
        kind=LIFE_EVOLUTION_KIND,
        key=key,
        max_age_ms=LIFE_MAX_AGE_MS,
    )
    if payload is None:
        now = int(time.time() * 1000)
        payload = build_life_evolution_view(
            person_id=key,
            world_snapshot={},
            appraisal_records=(),
            bond_projection={},
            scene_decision={},
            action_arbiter_decision={},
            action_preview={},
            now_ms=now,
        )
    payload["projection"] = projection
    return payload


@router.get("/life-evolution/snapshot")
async def get_life_evolution_snapshot(
    snapshot: dict[str, Any] = Depends(_get_life_evolution_snapshot),
) -> dict[str, Any]:
    return snapshot


@router.post("/life-evolution/action-preview")
async def post_life_evolution_action_preview(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Compute a governed DRY_RUN; recording and executors are intentionally unavailable."""
    _require_life_views_enabled()
    from host_brain.governor.action_preview import preview_action

    trusted: dict[str, Any] = {}
    route_ports = require_obs_route_ports(request)
    provider = route_ports.life_action_context_provider
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
        from host_brain.governor.tokens import TOKEN_TYPE_APPROVAL

        governor = dict(trusted.get("governor_decision") or {})
        intent = dict(trusted.get("intent_envelope") or {})
        tokens = dict(governor.get("tokens") or {}) if isinstance(governor.get("tokens"), Mapping) else {}
        token_ref = tokens.get("approval_token_ref")
        try:
            seq = int(intent.get("seq"))
        except (TypeError, ValueError):
            seq = -1
        validator = route_ports.approval_token_validator
        token_check = None
        if validator is not None:
            try:
                token_check = await _resolve(
                    validator(
                        token_ref if isinstance(token_ref, str) else None,
                        expected_type=TOKEN_TYPE_APPROVAL,
                        intent_id=str(
                            intent.get("id") or intent.get("intent_id") or ""
                        ),
                        skill_name=str(
                            proposed_action.get("action_kind")
                            or proposed_action.get("action")
                            or ""
                        ),
                        session_id=str(intent.get("session_id") or ""),
                        seq=seq,
                        now_ts_ms=now_ms,
                        consume=False,
                    )
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="OBS_APPROVAL_TOKEN_VALIDATOR_UNAVAILABLE",
                ) from exc
        if token_check is not None and bool(getattr(token_check, "valid", False)):
            authority_context_status = "SERVER_TRUSTED_TOKEN_VALID"
            authority_validation_reason = "APPROVAL_TOKEN_VALID_NOT_CONSUMED"
        else:
            authority_context_status = "SERVER_CONTEXT_TOKEN_INVALID"
            authority_validation_reason = (
                str(
                    getattr(token_check, "reason_code", None)
                    or getattr(token_check, "detail", None)
                    or "TOKEN_INVALID"
                )
                if token_check is not None
                else "APPROVAL_TOKEN_VALIDATOR_UNAVAILABLE"
            )
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
]
