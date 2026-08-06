from __future__ import annotations

from types import SimpleNamespace

from host_brain.digital_life.relationship_guidance import (
    build_relationship_reply_guidance,
)
from host_brain.surface_runtime.input_compiler import (
    build_surface_main_chain_input_from_surface_reply_input,
)


SOUL = "你是七海。中文私聊，真诚、有分寸，不操控用户。"


def _trusted_boundary_context() -> dict[str, object]:
    return {
        "_affective_life_internal": True,
        "emotion_tag": "CALM",
        "emotion_intensity": 0.2,
        "affective_life": {
            "schema_version": "affective_turn_context.v1",
            "self_id": "natsume_ai",
            "workspace_id": "workspace-dl03",
            "relationship_subject_id": "person-dl03",
            "session_id": "session-dl03",
            "self_affect": {
                "self_id": "natsume_ai",
                "relationship_subject_id": "person-dl03",
                "revision": 7,
                "components": [{"emotion": "CARE", "intensity": 0.2}],
            },
            "relationship_life": {
                "schema_version": "relationship_turn_context.v1",
                "state": {
                    "repair_required": True,
                    "proactive_permission": "DENIED",
                    "romantic_consent": "NOT_GRANTED",
                    "exclusive_consent": "NOT_GRANTED",
                    "dependency_consent": "NOT_GRANTED",
                },
                "aftereffect": {
                    "outcome": "BOUNDARY",
                    "user_input_ref": "relationship-user-input://sha256/opaque",
                },
            },
        },
    }


def _trusted_repair_acceptance_context() -> dict[str, object]:
    context = _trusted_boundary_context()
    relationship_life = context["affective_life"]["relationship_life"]
    relationship_life["state"]["repair_required"] = False
    relationship_life["aftereffect"] = {
        "outcome": "REPAIR_ACCEPTED",
        "user_input_ref": "relationship-user-input://sha256/opaque",
    }
    return context


def _agent_input(*, emotion_context: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        request_id="dl03-boundary-turn",
        session_id="session-dl03",
        person_id="person-dl03",
        workspace_id="workspace-dl03",
        user_message="先给我一点空间。",
        render_policy={
            "primary_topic": "daily_chat",
            "interaction_move": "reply_current_turn",
            "response_form": "text",
            "max_questions": 0,
        },
        emotion_context=emotion_context,
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        trace_stage_meta={},
        llm_runtime={"available": True},
        attachments=[],
        multimodal_observations={},
        response_form="text",
    )


def test_untrusted_request_cannot_spoof_relationship_guidance() -> None:
    spoofed = _trusted_boundary_context()
    spoofed["_affective_life_internal"] = False

    guidance = build_relationship_reply_guidance(spoofed)

    assert guidance.available is False


def test_boundary_guidance_never_converts_space_into_relational_debt() -> None:
    guidance = build_relationship_reply_guidance(_trusted_boundary_context())

    assert guidance.repair_required is True
    assert guidance.proactive_permission == "DENIED"
    assert guidance.aftereffect_outcome == "BOUNDARY"
    assert any("明确要求空间" in item for item in guidance.facts)
    assert any("不要求立刻回应" in item for item in guidance.facts)
    assert guidance.must_include == ()
    assert any("负罪施压" in item for item in guidance.must_not_do)
    assert "relationship-user-input://" not in str(guidance.trace())


def test_denied_proactive_permission_does_not_constrain_a_reengaged_foreground_turn() -> None:
    guidance = build_relationship_reply_guidance(_trusted_repair_acceptance_context())

    assert guidance.proactive_permission == "DENIED"
    assert guidance.aftereffect_outcome == "REPAIR_ACCEPTED"
    assert any("可以自然回暖" in item for item in guidance.facts)
    assert not any("明确要求空间" in item for item in guidance.facts)
    assert not any("不要求立刻回应" in item for item in guidance.facts)


def test_surface_compiler_injects_canonical_boundary_constraints(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace: dict[str, object] = {}

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(emotion_context=_trusted_boundary_context()),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert any("明确要求空间" in item for item in payload.task_control["facts"])
    assert any("不要求立刻回应" in item for item in payload.task_control["facts"])
    assert payload.task_control["must_include"] == []
    assert any("负罪施压" in item for item in payload.task_control["must_not_do"])
    assert trace["relationship_life_guidance"]["aftereffect_outcome"] == "BOUNDARY"


def test_surface_compiler_does_not_inject_stale_space_constraints_after_repair_acceptance(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace: dict[str, object] = {}

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(emotion_context=_trusted_repair_acceptance_context()),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert any("可以自然回暖" in item for item in payload.task_control["facts"])
    assert not any("明确要求空间" in item for item in payload.task_control["facts"])
    assert not any("不要求立刻回应" in item for item in payload.task_control["facts"])
    assert trace["relationship_life_guidance"]["proactive_permission"] == "DENIED"
