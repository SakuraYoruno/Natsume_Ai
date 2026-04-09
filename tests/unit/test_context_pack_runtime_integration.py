from __future__ import annotations

import pytest

from host_brain.app import chat_context_builder as ccb
from host_brain.app.chat_runtime_input import build_chat_runtime_input


def _make_runtime_input() -> object:
    return build_chat_runtime_input(
        request_id="req-context-pack",
        person_id="person-context-pack",
        session_id="session-context-pack",
        thread_id="thread-context-pack",
        user_message="hello context pack",
        render_target_lang="zh",
        retrieval_summary={"status": "OK", "top_k": 3},
        retrieval_evidence_items=[{"id": "e1", "score": 0.8}],
        emotion_state={"mood_state": "steady", "emotion_tag": "CALM", "emotion_intensity": 0.3},
        recall_slot_text={"episodic_vector": "memory slot"},
        bond_summary={},
        scene_type="CHAT",
    )


def _stub_generation_payload(**kwargs) -> dict:
    return {
        "prompt_context": {
            "authoritative": [],
            "retrieval": [],
            "style_pack": {},
            "constraints": {"retrieval_status": "OK"},
            "memory_recall_summary": "summary-from-prompt-context",
        },
        "prompt_context_validation": {"valid": True, "reason_code": None, "violation_count": 0},
        "prompt_context_observability": {"source_stats": {}},
        "emotion_context": {
            "mood_state": "steady",
            "emotion_tag": "CALM",
            "emotion_intensity": 0.3,
            "endocrine_state": {
                "dopamine_level": 0.66,
                "serotonin_level": 0.61,
                "cortisol_level": 0.22,
                "energy_reserve": 0.65,
                "gate_state": "open",
            },
        },
        "user_message": kwargs["user_message"],
    }


def test_build_chat_prompt_runtime_payload_includes_context_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccb, "build_chat_generation_prompt_payload", _stub_generation_payload)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.build_prompt_intelligence", lambda **_kwargs: {})

    runtime_payload = ccb.build_chat_prompt_runtime_payload(
        runtime_input=_make_runtime_input(),
        style_bindings={},
    )

    assert "context_pack" in runtime_payload
    context_pack = runtime_payload["context_pack"]
    assert context_pack["schema_version"] == "1.0"
    assert context_pack["request_meta"]["request_id"] == "req-context-pack"
    assert context_pack["request_meta"]["person_id"] == "person-context-pack"
    assert context_pack["request_meta"]["session_id"] == "session-context-pack"
    assert context_pack["request_meta"]["thread_id"] == "thread-context-pack"
    assert context_pack["request_meta"]["target_lang"] == "zh"
    assert "request:req-context-pack" in context_pack["trace_refs"]
    assert context_pack["memory_state"]["prompt_context_payload"]["constraints"]["retrieval_status"] == "OK"
    assert context_pack["runtime_state"]["endocrine_state"]["gate_state"] == "open"
    assert context_pack["extensions"]["endocrine_gate"] == "open"


def test_build_chat_prompt_runtime_payload_propagates_motive_and_policy_to_context_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MotiveState:
        def as_dict(self) -> dict:
            return {"state": "approach", "score": 0.91}

    class _PolicyDecision:
        def as_dict(self) -> dict:
            return {"reply_mode": "normal", "risk_level": "low"}

    monkeypatch.setattr(ccb, "build_chat_generation_prompt_payload", _stub_generation_payload)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.build_prompt_intelligence", lambda **_kwargs: {})
    monkeypatch.setattr("host_brain.persona.motive_engine.compute_motive_state", lambda _inp: _MotiveState())
    monkeypatch.setattr("host_brain.persona.response_policy.decide_response_policy", lambda _inp: _PolicyDecision())

    runtime_payload = ccb.build_chat_prompt_runtime_payload(
        runtime_input=_make_runtime_input(),
        style_bindings={},
    )

    assert runtime_payload["motive_state"]["state"] == "approach"
    assert runtime_payload["response_policy"]["reply_mode"] == "normal"
    assert runtime_payload["context_pack"]["motive"]["state"] == "approach"
    assert runtime_payload["context_pack"]["response_policy"]["reply_mode"] == "normal"
