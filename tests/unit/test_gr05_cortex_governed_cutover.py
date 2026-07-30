from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.agent_runtime.cortex_runtime import (
    _cortex_orchestrator_sse_generator_impl,
)


class _GovernedRuntime:
    def __init__(self, *, supported: bool = True) -> None:
        self.supported = supported
        self.calls: list[dict[str, Any]] = []

    def supports(self, _intent) -> bool:  # noqa: ANN001
        return self.supported

    async def execute_stream(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "request_id": kwargs["request_id"],
            "narrative": "governed result",
        }


class _RejectedGovernedChatRuntime(_GovernedRuntime):
    async def execute_stream(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        yield {
            "phase": "TERMINAL",
            "done": True,
            "decision": "REJECT",
            "reason_code": "SURFACE_REPLY_RUNTIME_FAILED",
            "governed": True,
            "execution_receipt": {
                "accepted": False,
                "status": "FAILED",
                "reason_code": "SURFACE_REPLY_RUNTIME_FAILED",
            },
        }


def _ports() -> SimpleNamespace:
    async def run_retrieval(**_kwargs):  # noqa: ANN003
        return {"status": "OK"}, [{"evidence_ref": "memory://governed/context"}]

    async def resolve_runtime(**_kwargs):  # noqa: ANN003
        # The governed route must still run and durably reject/accept through a
        # Task Attempt; Cortex cannot take its legacy LLM_UNAVAILABLE shortcut.
        return {"available": False, "provider_name": "offline", "model_id": "none"}

    def forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("legacy pre-admission writer was called")

    return SimpleNamespace(
        record_user_turn=forbidden,
        run_retrieval=run_retrieval,
        filter_retrieval_evidence_items_for_prompt=lambda **kwargs: (
            list(kwargs["retrieval_evidence_items"]),
            {"applied": False},
        ),
        build_user_preference_pack=lambda **_kwargs: {},
        audit_ledger=lambda: object(),
        resolve_host_llm_runtime_async=resolve_runtime,
    )


def _request_body() -> SimpleNamespace:
    return SimpleNamespace(
        message="请根据检索证据给出完整技术结论",
        thread_id=None,
        person_id="person-gr05-cortex",
        workspace_id="workspace-gr05-cortex",
        reply_channel="gateway",
        reply_target="device-gr05",
        render_target_lang="zh",
        input_source="GATEWAY",
        response_form="text",
        render_policy={"mode": "default"},
        attachments=[],
        multimodal_observations={},
        attachment_summary="",
    )


def _intent(route: str = "chat") -> dict[str, Any]:
    target_by_route = {
        "chat": "surface_reply",
        "skill": "memory_store",
        "delegate": "intent_agent",
        "plan": "planning_search",
    }
    return {
        "id": "intent-gr05-cortex",
        "route": route,
        "target_id": target_by_route[route],
        "task": "reply through the governed runtime",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["chat", "skill", "delegate", "plan"])
async def test_canonical_routes_freeze_context_and_skip_all_legacy_paths(
    route: str,
) -> None:
    governed = _GovernedRuntime()
    prefetch_args: dict[str, Any] = {}

    async def _prefetch(**kwargs):  # noqa: ANN003
        prefetch_args.update(kwargs)
        return {"stage": "trusted"}, None, {"dominant_motive": "help"}

    events = [
        event
        async for event in _cortex_orchestrator_sse_generator_impl(
            application_ports=_ports(),
            request_body=_request_body(),
            request_id="request-gr05-cortex",
            session_id="session-gr05-cortex",
            ts_ms=1_000,
            intent_draft=_intent(route),
            emotion_context={"emotion_tag": "NEUTRAL"},
            emotion_snapshot={"mood_state": "focused"},
            governor_result={"decision": "APPROVE", "request_id": "request-gr05-cortex"},
            intent_id="intent-gr05-cortex",
            metrics=object(),
            sse_fn=lambda value: dict(value),
            airlock_sanitize_input_fn=lambda value: (value, True),
            prefetch_retrieval_persona_inputs_fn=_prefetch,
            resolve_person_id_fn=lambda *_args, **_kwargs: "person-gr05-cortex",
            governed_command_application=governed,
            command_authority={},
        )
    ]

    assert events[-1]["narrative"] == "governed result"
    assert len(governed.calls) == 1
    call = governed.calls[0]
    assert call["bond_summary"] == {"stage": "trusted"}
    assert prefetch_args["workspace_id"] == "workspace-gr05-cortex"
    assert call["retrieval_summary"] == {"status": "OK"}
    assert call["retrieval_evidence_items"] == [
        {"evidence_ref": "memory://governed/context"}
    ]
    assert call["llm_runtime"]["available"] is False
    assert call["motive_state"] == {"dominant_motive": "help"}
    assert all(
        event.get("audit_refs", {}).get("path")
        not in {
            "cortex_orchestrator:contextual_session_direct",
            "cortex_orchestrator:contextual_style_direct",
            "cortex_orchestrator:clarification_direct",
        }
        for event in events
    )


@pytest.mark.asyncio
async def test_canonical_route_fails_closed_when_governed_runtime_cannot_support_it() -> None:
    governed = _GovernedRuntime(supported=False)
    events = [
        event
        async for event in _cortex_orchestrator_sse_generator_impl(
            application_ports=_ports(),
            request_body=_request_body(),
            request_id="request-gr05-unsupported",
            session_id="session-gr05-cortex",
            ts_ms=1_000,
            intent_draft=_intent(),
            emotion_context={},
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id="intent-gr05-cortex",
            metrics=object(),
            sse_fn=lambda value: dict(value),
            airlock_sanitize_input_fn=lambda value: (value, True),
            resolve_person_id_fn=lambda *_args, **_kwargs: "person-gr05-cortex",
            governed_command_application=governed,
        )
    ]

    assert events[-1]["decision"] == "REJECT"
    assert events[-1]["reason_code"] == "GOVERNED_ROUTE_UNSUPPORTED"
    assert governed.calls == []


@pytest.mark.asyncio
async def test_chat_projects_a_durable_terminal_rejection_to_one_public_commit() -> None:
    """Chat clients never need to understand Task Runtime's TERMINAL phase."""

    governed = _RejectedGovernedChatRuntime()
    events = [
        event
        async for event in _cortex_orchestrator_sse_generator_impl(
            application_ports=_ports(),
            request_body=_request_body(),
            request_id="request-gr05-terminal-chat",
            session_id="session-gr05-cortex",
            ts_ms=1_000,
            intent_draft=_intent("chat"),
            emotion_context={},
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id="intent-gr05-cortex",
            metrics=object(),
            sse_fn=lambda value: dict(value),
            airlock_sanitize_input_fn=lambda value: (value, True),
            resolve_person_id_fn=lambda *_args, **_kwargs: "person-gr05-cortex",
            governed_command_application=governed,
        )
    ]

    terminal = events[-1]
    assert terminal["phase"] == "COMMIT"
    assert terminal["done"] is True
    assert terminal["decision"] == "REJECT"
    assert terminal["reason_code"] == "SURFACE_REPLY_RUNTIME_FAILED"
    assert terminal["narrative"] == ""
    assert terminal["execution_receipt"]["accepted"] is False


def test_cortex_runtime_has_no_direct_chat_or_postprocess_compatibility_hooks() -> None:
    cortex_source = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "agent_runtime"
        / "cortex_runtime.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "maybe_build_contextual_memory_answer_fn",
        "maybe_build_contextual_session_answer_fn",
        "maybe_build_contextual_style_answer_fn",
        "maybe_build_previous_meaning_answer_fn",
        "async_post_process_fn",
        "cortex_orchestrator:contextual_session_direct",
        "cortex_orchestrator:contextual_style_direct",
        "cortex_orchestrator:clarification_direct",
        "CORTEX_RUNTIME_ROUTE_NOT_TERMINATED",
    ):
        assert forbidden not in cortex_source

    assert not (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "app"
        / "surface_reply_postprocessor.py"
    ).exists()
