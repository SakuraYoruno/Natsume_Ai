from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_EMOTION
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.app.prompt_template_store import (
    resolve_prompt_template_json_merged,
    resolve_prompt_template_text,
)
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _resolve_emotion_agent_error_summary() -> str:
    return resolve_prompt_template_text("emotion_agent_handler.error_summary")


def emotion_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        user_input = str(payload.get("user_input") or payload.get("query") or "").strip()
        emotion_context = ctx.get("emotion_context") or payload.get("emotion_context") or {}
        current_emotion_tag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
        mood_state = str(emotion_context.get("mood_state") or "steady").strip()
        emotion_echo = emotion_context.get("emotion_echo") or {}
        user_profile = emotion_context.get("user_emotion_profile") or {}

        system_prompt_extra = _build_emotion_agent_system_prompt(
            current_emotion_tag=current_emotion_tag,
            mood_state=mood_state,
            emotion_echo=emotion_echo,
            user_profile=user_profile,
        )

        tools = get_tools_for_agent(AGENT_ID_EMOTION, allow_side_effects=False)
        gateway = ctx.get("gateway") or payload.get("_gateway")

        react_result = run_react_loop(
            agent_id=AGENT_ID_EMOTION,
            user_input=user_input,
            tools=tools,
            gateway=gateway,
            system_prompt_extra=system_prompt_extra,
            max_steps=int(os.environ.get("EMOTION_AGENT_REACT_MAX_STEPS") or "3"),
            allow_side_effects=False,
            context=ctx,
        )

        out["react_scratchpad"] = react_result.to_scratchpad_dict()
        emotion_analysis = _parse_emotion_analysis(react_result.final_answer)

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_EMOTION}_{int(time.time() * 1000)}"
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_EMOTION,
            artifact_ref=artifact_ref,
            summary=react_result.final_answer[:200],
            confidence=react_result.confidence,
            risk_flags=react_result.risk_flags,
        )

        out["emotion_agent_trace"] = {
            "agent_id": AGENT_ID_EMOTION,
            "mode": "react_loop",
            "artifact_ref": artifact_ref,
            "current_emotion_tag": current_emotion_tag,
            "recommended_emotion_tag": emotion_analysis.get("recommended_emotion_tag", current_emotion_tag),
            "emotion_reasoning": emotion_analysis.get("reasoning", ""),
            "total_steps": react_result.total_steps,
            "stopped_reason": react_result.stopped_reason,
            "ts_ms": int(time.time() * 1000),
        }
    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_EMOTION] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_EMOTION}_error",
            "summary": _resolve_emotion_agent_error_summary(),
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out


def _build_emotion_agent_system_prompt(
    *,
    current_emotion_tag: str,
    mood_state: str,
    emotion_echo: dict[str, Any],
    user_profile: dict[str, Any],
) -> str:
    templates = _load_template_mapping("emotion_agent_handler.prompt_templates")
    parts: list[str] = [
        _mapping_text(
            templates,
            "system_role_line",
        ),
        _mapping_text(
            templates,
            "current_emotion_tag_line",
            current_emotion_tag=current_emotion_tag,
        ),
        _mapping_text(
            templates,
            "mood_state_line",
            mood_state=mood_state,
        ),
    ]

    echo_style = str(emotion_echo.get("echo_style") or "").strip()
    if echo_style:
        parts.append(
            _mapping_text(
                templates,
                "emotion_echo_line",
                echo_style=echo_style,
            )
        )

    profile_hint = str(user_profile.get("profile_hint") or "").strip()
    if profile_hint:
        parts.append(
            _mapping_text(
                templates,
                "user_profile_line",
                profile_hint=profile_hint,
            )
        )

    parts.extend(
        [
            "",
            _mapping_text(
                templates,
                "tools_intro",
            ),
            _mapping_text(
                templates,
                "tool_emotion_history_search",
            ),
            _mapping_text(
                templates,
                "tool_emotion_timeline_query",
            ),
            _mapping_text(
                templates,
                "tool_search_knowledge",
            ),
            "",
            _mapping_text(
                templates,
                "analysis_intro",
            ),
            _mapping_text(
                templates,
                "analysis_item_1",
            ),
            _mapping_text(
                templates,
                "analysis_item_2",
            ),
            _mapping_text(
                templates,
                "analysis_item_3",
            ),
        ]
    )

    return "\n".join(parts)


def _parse_emotion_analysis(final_answer: str) -> dict[str, Any]:
    import json as _json  # noqa: PLC0415

    result: dict[str, Any] = {
        "recommended_emotion_tag": "NEUTRAL",
        "reasoning": final_answer[:200],
    }

    text = str(final_answer or "").strip()
    if not text:
        return result

    try:
        parsed = _json.loads(text)
        if isinstance(parsed, dict):
            if "recommended_emotion_tag" in parsed:
                result["recommended_emotion_tag"] = str(parsed["recommended_emotion_tag"]).strip().upper()
            elif "natsume_emotion" in parsed:
                result["recommended_emotion_tag"] = str(parsed["natsume_emotion"]).strip().upper()
            if "reasoning" in parsed:
                result["reasoning"] = str(parsed["reasoning"])[:200]
            return result
    except (_json.JSONDecodeError, TypeError, ValueError):
        pass

    from host_brain.app.emotion_llm_inference import _VALID_EMOTION_TAGS  # noqa: PLC0415

    text_upper = text.upper()
    for tag in _VALID_EMOTION_TAGS:
        if tag in text_upper and tag != "NEUTRAL":
            result["recommended_emotion_tag"] = tag
            break

    return result
