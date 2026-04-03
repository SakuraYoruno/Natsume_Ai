from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app import chat_context_builder as ccb
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.core import prompt_context as pc


def test_normalize_and_load_v5_style_instruction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    style_root = tmp_path / "style"
    style_root.mkdir(parents=True, exist_ok=True)
    (style_root / "style_profile_v5.json").write_text(
        json.dumps(
            {
                "instruction_cn": "短文本",
                "instruction_ja": "j" * 120,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NATSUME_STYLE_V5_PATH", str(style_root))

    normalized = ccb.normalize_node_style_bindings(
        {
            "top_agent": {
                "canon_id": "canon-a",
                "version": 1,
                "hash": "abc",
                "tone_only": False,
                "unsafe_as_fact": False,
                "style_instruction": " warm ",
                "citation": " style://x ",
            },
            "ignored": "bad",
        }
    )

    assert normalized["top_agent"]["style_instruction"] == "warm"
    assert ccb._load_v5_style_instruction("ja") == "j" * 120
    assert ccb._load_v5_style_instruction("zh") == ""


def test_build_node_style_bindings_prefers_v5_then_falls_back_to_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccb, "_load_v5_style_instruction", lambda render_lang: "v" * 120)
    v5_bindings = ccb.build_node_style_bindings(render_target_lang="ja")
    assert v5_bindings["top_agent"]["citation"] == "style_canon:v5_distilled:ja"
    assert ccb.CHAT_DIRECT_NODE_ID in v5_bindings

    monkeypatch.setattr(ccb, "_load_v5_style_instruction", lambda render_lang: "")
    monkeypatch.setattr(ccb, "get_ledger", lambda: "LEDGER")

    class _FakeStore:
        def __init__(self, style_root, ledger) -> None:
            self.style_root = style_root
            self.ledger = ledger

        def get(self, canon_id: str):
            mapping = {
                "core": SimpleNamespace(
                    canon_id="core",
                    version=1,
                    hash="a" * 64,
                    tone_only=True,
                    unsafe_as_fact=True,
                    content="CORE_STYLE",
                ),
                "secondary": SimpleNamespace(
                    canon_id="secondary",
                    version=2,
                    hash="b" * 64,
                    tone_only=True,
                    unsafe_as_fact=True,
                    content="SECONDARY_STYLE",
                ),
                "render_cn": SimpleNamespace(
                    canon_id="render_cn",
                    version=3,
                    hash="c" * 64,
                    tone_only=True,
                    unsafe_as_fact=True,
                    content="RENDER_STYLE",
                ),
            }
            if canon_id == "broken":
                raise RuntimeError("boom")
            return mapping.get(canon_id)

    monkeypatch.setattr(ccb, "StyleCanonStore", _FakeStore)
    monkeypatch.setenv("HOST_STYLE_CORE_CANON_ID", "core")
    monkeypatch.setenv("HOST_STYLE_SECONDARY_CANON_ID", "secondary")
    monkeypatch.setenv("HOST_STYLE_RENDER_CN_CANON_ID", "render_cn")
    fallback = ccb.build_node_style_bindings(render_target_lang="zh")

    assert fallback["core_reasoner_primary"]["style_instruction"] == "CORE_STYLE"
    assert fallback["core_reasoner_secondary"]["style_instruction"] == "SECONDARY_STYLE"
    assert fallback[ccb.CHAT_DIRECT_NODE_ID]["style_instruction"] == "RENDER_STYLE"


def test_chat_context_helper_branches_and_observability() -> None:
    assert ccb._coerce_non_negative_int("bad", default=3) == 3
    assert ccb._coerce_non_negative_int(-5, default=1) == 0
    assert ccb._coerce_optional_float("0.25") == 0.25
    assert ccb._coerce_optional_float("bad") is None
    assert ccb._extract_query_plan_intent({"query_plan": {"intent": "Affection"}}) == "affection"
    assert ccb._extract_query_plan_text_len({"query_plan": {"original_query": "  hi "}}) == 2
    assert ccb._should_drop_persona_row(
        store_id="persona_utterance",
        score=0.1,
        confidence=None,
        query_plan_intent="chat",
        query_plan_text_len=4,
    ) is True
    assert ccb._should_drop_persona_row(
        store_id="persona_utterance",
        score=0.9,
        confidence=0.9,
        query_plan_intent="phatic",
        query_plan_text_len=20,
    ) is True
    assert ccb._should_drop_persona_row(
        store_id="episodic_vector",
        score=0.1,
        confidence=0.1,
        query_plan_intent="chat",
        query_plan_text_len=4,
    ) is False
    assert ccb._count_kind([{"kind": "episodic"}, {"kind": "episodic"}, {"kind": "persona_utterance"}, "bad"]) == {
        "episodic": 2,
        "persona_utterance": 1,
    }
    assert ccb._extract_route_ids([{"route_id": "r1"}, {"store_id": "r2"}, "r1", "r3"]) == ["r1", "r2", "r3"]
    assert ccb._classify_retrieval_path_for_obs(
        retrieval_summary={"status": "OK", "degraded": False, "route_count": 6, "candidate_count": 2, "rerank_topk": 2},
        retrieval_evidence_items=[{"snippet": "x"}],
    ) == "success"
    assert ccb._classify_retrieval_path_for_obs(
        retrieval_summary={"status": "DEGRADED", "degraded": True, "route_count": 1, "candidate_count": 0, "rerank_topk": 0},
        retrieval_evidence_items=[],
    ) == "partial_failure"
    assert ccb._classify_retrieval_path_for_obs(
        retrieval_summary={"status": "FAILED", "degraded": True, "route_count": 0, "candidate_count": 0, "rerank_topk": 0},
        retrieval_evidence_items=[],
    ) == "all_failure"

    assert ccb._select_context_degrade_tier(
        retrieval_summary={"status": "OK", "degraded": False, "route_count": 6},
        prompt_context_validation={"reason_code": None},
        slot_item_counts={"retrieval": 2, "authoritative": 1, "style_canon": 1, "tool_meta_pack": 0},
    )[0] == "six_route"
    assert ccb._select_context_degrade_tier(
        retrieval_summary={"status": "OK", "degraded": False, "route_count": 3},
        prompt_context_validation={"reason_code": None},
        slot_item_counts={"retrieval": 1},
    )[0] == "core_three"
    assert ccb._select_context_degrade_tier(
        retrieval_summary={"status": "FAILED", "degraded": True, "route_count": 0},
        prompt_context_validation={"reason_code": "PROMPT_CONTEXT_DEGRADED"},
        slot_item_counts={"authoritative": 1},
    )[0] == "minimal_context"
    assert ccb._select_context_degrade_tier(
        retrieval_summary={"status": "FAILED", "degraded": True, "route_count": 0},
        prompt_context_validation={"reason_code": "PROMPT_CONTEXT_MINIMAL_FALLBACK"},
        slot_item_counts={},
    )[0] == "bare_llm"

    observability = ccb.build_prompt_context_observability(
        prompt_context={
            "authoritative": [{"kind": "world_canon"}],
            "retrieval": [{"kind": "episodic"}],
            "style_pack": {
                "style_canon": [{"kind": "style_canon"}],
                "node_bindings": {"top_agent": {"canon_id": "x"}},
            },
            "tool_meta_pack": [{"id": "memory_recall"}],
            "constraints": {"strict_json_output": True},
            "emotion_pulse": {"emotion_tag": "HAPPY"},
            "emotion_recall": "Current affective layer",
        },
        prompt_context_validation={"valid": True, "reason_code": None},
        retrieval_summary={
            "status": "DEGRADED",
            "reason_code": "RETR_PARTIAL",
            "degraded": True,
            "route_count": 2,
            "candidate_count": 3,
            "rerank_topk": 2,
            "routes": [{"route_id": "r1"}, {"store_id": "r2"}, "r1"],
            "cache_hit": True,
            "cache_ttl_ms": 1000,
            "cache_age_ms": 5,
        },
        retrieval_evidence_items=[{"snippet": "a"}, {"snippet": "b"}, {"snippet": "c"}],
    )

    assert observability["retrieval_metrics"]["route_ids"] == ["r1", "r2"]
    assert observability["retrieval_metrics"]["path"] == "partial_failure"
    assert observability["source_stats"]["retrieval_trimmed_items"] == 2
    assert observability["kind_breakdown"]["retrieval"]["episodic"] == 1

    graph_output = {"payload": {"top_agent": {"payload": {"text": "ok"}}}}
    assert ccb.extract_chat_graph_node_entry(graph_output, node_id="top_agent")["payload"]["text"] == "ok"
    node_entry, gateway_payload = ccb.extract_chat_graph_gateway_payload(graph_output, node_id="top_agent")
    assert node_entry["payload"]["text"] == "ok"
    assert gateway_payload == {"text": "ok"}


def test_build_chat_context_pack_degraded_and_minimal_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import host_brain.app.dependencies as deps
    import host_brain.app.emotion_context as emotion_context

    normalized_emotion = {
        "mood_state": "engaged",
        "emotion_tag": "HAPPY",
        "emotion_range_hint": "gentle",
        "allowed_emotion_tags": ["HAPPY", "NEUTRAL"],
        "emotion_intensity": 0.7,
    }
    monkeypatch.setattr(emotion_context, "normalize_chat_emotion_context", lambda raw: normalized_emotion)
    monkeypatch.setattr(deps, "get_bond_stage_engine", lambda: None)
    monkeypatch.setattr(ccb, "get_ledger", lambda: "LEDGER")
    violations_logged: list[dict[str, object]] = []
    monkeypatch.setattr(ccb, "check_wcanon_slot_violation", lambda **kwargs: violations_logged.append(kwargs))

    invalid = pc.PromptContextValidationResult()
    invalid.add("BROKEN", "authoritative broken", slot="authoritative")
    valid = pc.PromptContextValidationResult()
    responses = [invalid, valid]
    monkeypatch.setattr(ccb, "validate_context_pack", lambda _pack: responses.pop(0))

    degraded_pack, degraded_meta = ccb.build_chat_context_pack(
        request_id="req-1",
        session_id="session-1",
        thread_id=None,
        retrieval_summary={"status": "OK", "reason_code": "NONE"},
        retrieval_evidence_items=[{"snippet": "retrieval", "score": 0.9, "confidence": 0.9}],
        style_bindings=None,
        emotion_context={},
        cpb_slots={
            "style_canon": "STYLE_SLOT",
            "world_canon": "WORLD_SLOT",
            "emotion_recall": "EMOTION_RECALL_BINDING",
        },
    )

    assert degraded_meta["degraded"] is True
    assert degraded_meta["reason_code"] == "PROMPT_CONTEXT_DEGRADED"
    assert degraded_pack["authoritative"] == []
    assert violations_logged

    invalid_again = pc.PromptContextValidationResult()
    invalid_again.add("BROKEN", "authoritative broken", slot="authoritative")
    invalid_final = pc.PromptContextValidationResult()
    invalid_final.add("BROKEN2", "still broken", slot="style_pack")
    responses = [invalid_again, invalid_final]
    monkeypatch.setattr(ccb, "validate_context_pack", lambda _pack: responses.pop(0))

    minimal_pack, minimal_meta = ccb.build_chat_context_pack(
        request_id="req-2",
        session_id="session-2",
        thread_id=None,
        retrieval_summary={"status": "FAILED", "reason_code": "ERR"},
        retrieval_evidence_items=[],
        style_bindings=None,
        emotion_context={},
        cpb_slots={"style_canon": "STYLE_SLOT"},
    )

    assert minimal_meta["reason_code"] == "PROMPT_CONTEXT_MINIMAL_FALLBACK"
    assert minimal_pack["authoritative"] == []
    assert minimal_pack["style_pack"] == {}


def test_build_chat_prompt_runtime_payload_carries_episodic_vector_recall_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ccb,
        "build_chat_generation_prompt_payload",
        lambda **kwargs: {
            "prompt_context": {
                "authoritative": [],
                "retrieval": [],
                "style_pack": {},
                "constraints": {"retrieval_status": "OK"},
            },
            "prompt_context_validation": {"valid": True, "reason_code": None, "violation_count": 0},
            "prompt_context_observability": {"source_stats": {}},
            "emotion_context": {
                "mood_state": "steady",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.7,
                "emotion_range_hint": "warm",
            },
            "user_message": kwargs["user_message"],
        },
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )

    runtime_input = build_chat_runtime_input(
        request_id="req-episodic-slot",
        person_id="person-episodic-slot",
        session_id="session-episodic-slot",
        thread_id=None,
        user_message="hello",
        render_target_lang="zh",
        retrieval_summary={"status": "OK"},
        retrieval_evidence_items=[],
        emotion_state={"mood_state": "steady", "emotion_tag": "HAPPY", "emotion_intensity": 0.7},
        recall_slot_text={"episodic_vector": "shared memory came back through recall pack"},
        bond_summary={},
        scene_type="CHAT",
    )

    runtime_payload = ccb.build_chat_prompt_runtime_payload(
        runtime_input=runtime_input,
        style_bindings={},
    )

    assert runtime_payload["prompt_context"]["payload"]["episodic_vector"] == (
        "shared memory came back through recall pack"
    )


def test_apply_motive_state_to_persona_expression_hint_promotes_caring_tone() -> None:
    hint = {
        "style_hint": "自然回应",
        "tone": "gentle",
        "echo_weight_modifier": 0.0,
        "follow_up_tendency": 0.0,
        "pace_hint": "steady",
    }
    motive_state = {
        "care_drive": 0.78,
        "reach_out_drive": 0.45,
        "continuity_drive": 0.41,
        "withdraw_drive": 0.12,
        "self_protect_drive": 0.15,
    }

    adjusted = ccb._apply_motive_state_to_persona_expression_hint(hint, motive_state)

    assert adjusted["tone"] == "caring"
    assert adjusted["follow_up_tendency"] >= 0.08
    assert adjusted["echo_weight_modifier"] >= 0.08
    assert "先接住对方情绪" in adjusted["style_hint"]
    assert "motive_guidance" in adjusted


def test_apply_motive_state_to_persona_expression_hint_suppresses_followup_when_retreat_high() -> None:
    hint = {
        "style_hint": "自然回应",
        "tone": "caring",
        "echo_weight_modifier": 0.12,
        "follow_up_tendency": 0.16,
        "pace_hint": "steady",
    }
    motive_state = {
        "care_drive": 0.18,
        "reach_out_drive": 0.14,
        "continuity_drive": 0.2,
        "withdraw_drive": 0.82,
        "self_protect_drive": 0.87,
    }

    adjusted = ccb._apply_motive_state_to_persona_expression_hint(hint, motive_state)

    assert adjusted["tone"] == "gentle"
    assert adjusted["follow_up_tendency"] == 0.0
    assert adjusted["echo_weight_modifier"] == 0.02
    assert "收缩和自我保护" in adjusted["style_hint"]


def test_build_chat_prompt_runtime_payload_applies_motive_to_expression_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ccb,
        "build_chat_generation_prompt_payload",
        lambda **kwargs: {
            "prompt_context": {
                "authoritative": [],
                "retrieval": [],
                "style_pack": {},
                "constraints": {"retrieval_status": "OK"},
            },
            "prompt_context_validation": {"valid": True, "reason_code": None, "violation_count": 0},
            "prompt_context_observability": {"source_stats": {}},
            "emotion_context": {
                "mood_state": "steady",
                "emotion_tag": "SAD",
                "emotion_intensity": 0.8,
                "emotion_range_hint": "needs support",
            },
            "user_message": kwargs["user_message"],
        },
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )

    class _FakeAdapter:
        def adapt(self, *_args, **_kwargs):  # noqa: ANN001
            return SimpleNamespace(
                style_hint="自然回应",
                tone="gentle",
                echo_weight_modifier=0.0,
                追问倾向=0.0,
                pace_hint="steady",
            )

    class _FakePersonaEngine:
        @staticmethod
        def get_instance():
            return SimpleNamespace(get_traits=lambda _user_id: SimpleNamespace())

    class _FakeMotiveState:
        def as_dict(self):
            return {
                "care_drive": 0.81,
                "reach_out_drive": 0.42,
                "continuity_drive": 0.38,
                "withdraw_drive": 0.14,
                "self_protect_drive": 0.1,
                "dominant_motive": "care",
            }

    class _FakePolicy:
        def as_dict(self):
            return {"allow_followup": True, "max_sentences": 3}

    monkeypatch.setattr("host_brain.persona.expression_adapter.PersonaExpressionAdapter", _FakeAdapter)
    monkeypatch.setattr("host_brain.persona.persona_engine.PersonaEngine", _FakePersonaEngine)
    monkeypatch.setattr("host_brain.persona.motive_engine.compute_motive_state", lambda _inp: _FakeMotiveState())
    monkeypatch.setattr("host_brain.persona.response_policy.decide_response_policy", lambda _inp: _FakePolicy())

    runtime_input = build_chat_runtime_input(
        request_id="req-motive-expression",
        person_id="person-motive-expression",
        session_id="session-motive-expression",
        thread_id=None,
        user_message="我有点难受",
        render_target_lang="zh",
        retrieval_summary={"status": "OK"},
        retrieval_evidence_items=[],
        emotion_state={"mood_state": "steady", "emotion_tag": "SAD", "emotion_intensity": 0.8},
        recall_slot_text={},
        bond_summary={},
        scene_type="CHAT",
    )

    runtime_payload = ccb.build_chat_prompt_runtime_payload(
        runtime_input=runtime_input,
        style_bindings={},
    )

    hint = dict(runtime_payload.get("persona_expression_hint") or {})
    assert hint["tone"] == "caring"
    assert hint["follow_up_tendency"] >= 0.08
    assert "先接住对方情绪" in str(hint.get("style_hint") or "")
