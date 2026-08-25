"""Shared model-budget and fallback helpers for the Surface main chain."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.digital_life.companion_voice import japanese_companion_voice_enabled
from host_brain.persona.surface_main_chain import SurfaceMainChainInput
from host_brain.surface_runtime.common import _coerce_int, _safe_mapping
from host_brain.surface_runtime.memory_grounding import _surface_memory_packet_for_output_guard
from host_brain.surface_runtime.quality_guards import (
    _memory_packet_payload_for_guard,
    _raw_memory_packet_payload_from_agent_input,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_id,
    _surface_sub_agent_result_text,
)


_ACTIVE_EXPRESSION_PREFERENCE_PREFIX = (
    "已激活的长期表达偏好（本轮执行）："
)
_ACTIVE_KNOWLEDGE_FACT_PREFIX = "已激活知识："
_ACTIVE_KNOWLEDGE_EVIDENCE_PREFIX = "evolution-knowledge-entry://sha256/"


def _surface_active_knowledge_set_digest(entry_digests: Sequence[Any]) -> str:
    """Return a stable digest for a bounded set of public Knowledge digests."""

    raw = tuple(str(value or "").strip().lower() for value in entry_digests)
    if (
        not raw
        or len(raw) > 4
        or any(
            len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in raw
        )
    ):
        return ""
    normalized = sorted(set(raw))
    if len(normalized) != len(raw):
        return ""
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _surface_active_knowledge_prompt_evidence(
    *,
    messages: Sequence[Mapping[str, Any]],
    memory_packet: Any,
) -> dict[str, Any]:
    """Prove active Knowledge reached the final prompt without storing content."""

    packet = _memory_packet_payload_for_guard(memory_packet)
    active_facts = tuple(
        str(value or "").strip()
        for value in list(packet.get("facts") or [])
        if str(value or "").strip().startswith(_ACTIVE_KNOWLEDGE_FACT_PREFIX)
    )
    entry_digests = tuple(
        str(value or "").strip()[len(_ACTIVE_KNOWLEDGE_EVIDENCE_PREFIX) :]
        for value in list(packet.get("evidence_refs") or [])
        if str(value or "").strip().startswith(
            _ACTIVE_KNOWLEDGE_EVIDENCE_PREFIX
        )
    )
    if (
        not active_facts
        or len(active_facts) > 4
        or len(entry_digests) != len(active_facts)
    ):
        return {}
    rendered = "\n".join(
        str(message.get("content") or "") for message in messages
    )
    if any(fact not in rendered for fact in active_facts):
        return {}
    set_digest = _surface_active_knowledge_set_digest(entry_digests)
    if not set_digest:
        return {}
    return {
        "surface_active_knowledge_set_digest": set_digest,
        "surface_active_knowledge_count": len(active_facts),
    }


def _surface_expression_prompt_evidence(
    *,
    messages: Sequence[Mapping[str, Any]],
    style_anchors: Sequence[Any],
) -> dict[str, str]:
    """Return secret-free evidence only when the active directive is rendered.

    The actual registered prompt already has a durable digest.  This optional
    digest identifies the one bounded expression-version directive inside the
    final message list, without storing the prompt or adding another ordinary
    chat write.
    """

    directive = next(
        (
            str(item or "").strip()
            for item in style_anchors
            if str(item or "").strip().startswith(
                _ACTIVE_EXPRESSION_PREFERENCE_PREFIX
            )
        ),
        "",
    )
    if not directive:
        return {}
    rendered = "\n".join(
        str(message.get("content") or "") for message in messages
    )
    if directive not in rendered:
        return {}
    return {
        "surface_expression_directive_digest": hashlib.sha256(
            directive.encode("utf-8")
        ).hexdigest()
    }


def _surface_main_chain_response_format() -> dict[str, str] | None:
    if japanese_companion_voice_enabled():
        return {"type": "json_object"}
    mode = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_OUTPUT_CONTRACT") or "text").strip().lower()
    if mode in {"json", "json_object", "structured"}:
        return {"type": "json_object"}
    return None


def _surface_model_deadline_ms(governed_model_budget: Any | None) -> int:
    configured = max(
        1_000,
        min(
            120_000,
            _coerce_int(
                os.environ.get("HOST_SURFACE_MODEL_ATTEMPT_DEADLINE_MS"),
                default=60_000,
            ),
        ),
    )
    if governed_model_budget is None:
        return configured
    return min(int(governed_model_budget.deadline_ms), configured)


def _surface_model_request_profile(
    trace_stage_meta: Mapping[str, Any] | None,
) -> tuple[str, str, str]:
    """Select the server-owned model lane at the actual ModelAttempt boundary.

    Route projection alone is insufficient because the Surface main chain
    creates the canonical ModelAttemptRequest itself.  The server-owned
    ``runtime_path`` determines whether this is ordinary companionship or a
    quality-first execution route.  A two-candidate wording pass may stay
    buffered without silently upgrading an ordinary companion turn from the
    latency-bounded B lane to the A-lane model.
    """

    runtime_path = str(
        _safe_mapping(trace_stage_meta).get("runtime_path") or ""
    ).strip().lower()
    if runtime_path == "realtime_companion":
        return "CHAT_SURFACE_REALTIME", "B", "realtime_companion"
    return "CHAT_SURFACE", "A", "surface_reply"


def _final_stream_usage_is_attested(result: Any) -> bool:
    """Return whether a final stream result carries all canonical budget facts.

    A final provider response can be useful audit evidence even when the
    provider omits token or cost fields.  It must not be relabeled as usage
    attestation in that case.  Governed calls enforce these fields inside
    ``ModelAttemptService``; this small read-side classifier only keeps the
    direct native Surface trace honest.
    """

    if result is None or bool(getattr(result, "cached", False)):
        return False
    attempts = tuple(getattr(result, "attempts", ()) or ())
    if not attempts:
        return False
    measurements = _safe_mapping(
        _safe_mapping(getattr(attempts[-1], "metadata", None)).get(
            "provider_budget_measurements"
        )
    )
    required = ("token_count", "completion_tokens", "model_cost_micros")
    return all(
        isinstance(measurements.get(key), int)
        and not isinstance(measurements.get(key), bool)
        and int(measurements[key]) >= 0
        for key in required
    )


def _record_surface_memory_guard_packet(
    *,
    agent_input: Any,
    surface_input: SurfaceMainChainInput,
    trace_stage_meta: dict[str, Any] | None,
) -> None:
    if not isinstance(trace_stage_meta, dict):
        return
    guard_packet = (
        _raw_memory_packet_payload_from_agent_input(agent_input)
        or _surface_memory_packet_for_output_guard(
            agent_input=agent_input,
            trace_stage_meta=trace_stage_meta,
        )
        or _memory_packet_payload_for_guard(surface_input.memory_packet)
    )
    if guard_packet and not _safe_mapping(trace_stage_meta.get("surface_memory_recall_packet")):
        trace_stage_meta["surface_memory_recall_packet"] = guard_packet


def _governed_task_delivery_authority(agent_input: Any) -> Any | None:
    """Return only the app-local Task visibility authority for frozen Chat.

    This rejects an arbitrary object smuggled through an ordinary Surface
    input.  The authority is constructed by ``FrozenChatRouteExecutor`` from
    the live node attempt/lease/grant tuple and never comes from HTTP input.
    """

    authority = getattr(agent_input, "governed_delivery_authority", None)
    if authority is None:
        return None
    from host_brain.task_runtime.chat_delivery_authority import (  # noqa: PLC0415
        TaskBoundChatDeliveryAuthority,
    )

    if (
        not isinstance(authority, TaskBoundChatDeliveryAuthority)
        or not bool(getattr(agent_input, "defer_effects_to_task_runtime", False))
    ):
        raise RuntimeError("TASK_CHAT_DELIVERY_AUTHORITY_INVALID")
    return authority


def _governed_model_budget_guard(
    trace_stage_meta: Mapping[str, Any] | None,
):
    """Decode only the immutable guard inserted by the governed route worker."""

    raw = _safe_mapping(trace_stage_meta).get("governed_model_budget")
    if not raw:
        return None
    if not isinstance(raw, Mapping):
        raise RuntimeError("MODEL_BUDGET_GUARD_INVALID")
    from host_brain.model_runtime.contracts import ModelBudgetGuard  # noqa: PLC0415

    try:
        return ModelBudgetGuard.from_mapping(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MODEL_BUDGET_GUARD_INVALID") from exc


def _has_governed_model_budget(trace_stage_meta: Mapping[str, Any] | None) -> bool:
    return bool(_safe_mapping(trace_stage_meta).get("governed_model_budget"))


def _governed_model_budget_failure_reason(exc: BaseException) -> str:
    candidate = str(exc).split(":", 1)[0].strip().upper()
    if candidate.startswith("MODEL_"):
        return candidate
    if candidate == "ATTEMPT_BUDGET_EXHAUSTED":
        return "MODEL_SEMANTIC_RETRY_EXHAUSTED"
    if candidate.startswith(("AFFECTIVE_", "JAPANESE_SPEECH_", "SURFACE_")):
        return candidate
    return "MODEL_BUDGET_GUARD_REJECTED"


def _native_stream_failure_reason(exc: BaseException) -> str:
    """Return a public-safe, machine-stable cause for an unseen stream failure.

    The native sentence path is allowed to surface the cause only before a
    sentence becomes visible.  Do not forward provider prose, URLs or Python
    trace text into the client or immutable task receipt.
    """

    candidate = str(getattr(exc, "code", "") or "").strip().upper()
    if not candidate:
        candidate = str(exc).split(":", 1)[0].strip().upper()
    compact = candidate.replace("_", "")
    if (
        3 <= len(candidate) <= 160
        and compact.isalnum()
        and candidate[:1] in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        and all(char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in candidate)
    ):
        return candidate
    return "SURFACE_STREAM_INTERRUPTED"


def _surface_failure_fallback_text(agent_input: Any, *, exc: BaseException) -> str:
    _ = exc
    try:
        from host_brain.app.surface_reply_preprocessor import _build_deterministic_sub_agent_reply  # noqa: PLC0415

        text = _build_deterministic_sub_agent_reply(
            getattr(agent_input, "sub_agent_context", None),
            render_target_lang=str(getattr(agent_input, "render_target_lang", "zh") or "zh"),
            user_message=str(getattr(agent_input, "user_message", "") or ""),
        )
        if text:
            return str(text).strip()
    except Exception:
        pass
    result_text = _surface_sub_agent_result_text(agent_input)
    if _surface_sub_agent_id(agent_input) and result_text:
        return result_text[:4000].strip()
    return ""
