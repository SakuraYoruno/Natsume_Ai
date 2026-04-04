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


def test_build_memory_persona_signal_summarizes_humanized_recall_metadata() -> None:
    signal = ccb._build_memory_persona_signal(
        [
            {
                "metadata": {
                    "emotion_color_tag": "warm",
                    "suppression_flag": True,
                    "distortion_level": 0.6,
                }
            },
            {"metadata": {"emotion_color_tag": "warm"}},
            {
                "metadata": {
                    "emotion_color_tag": "happy",
                    "distortion_level": 0.2,
                }
            },
            {"snippet": "plain memory without humanized metadata"},
        ]
    )

    assert signal["humanized_memory_count"] == 3
    assert signal["suppressed_memory_count"] == 1
    assert signal["distorted_memory_count"] == 2
    assert signal["avg_distortion_level"] == pytest.approx(0.4, abs=1e-4)
    assert signal["dominant_memory_emotion"] == "WARM"
    assert signal["recall_bias"] == "uncertain"
    assert signal["memory_persona_guidance"]


def test_build_memory_recall_summary_turns_signal_into_readable_hint() -> None:
    summary = ccb._build_memory_recall_summary(
        {
            "humanized_memory_count": 3,
            "suppressed_memory_count": 1,
            "distorted_memory_count": 2,
            "avg_distortion_level": 0.4,
            "dominant_memory_emotion": "WARM",
            "recall_bias": "uncertain",
            "memory_persona_guidance": "命中压制记忆时先轻触试探，不要直接掀开高压回忆",
        }
    )

    assert "3 条带人格化标记的记忆" in summary
    assert "主导回忆情绪偏向 WARM" in summary
    assert "1 条处于压制状态" in summary
    assert "平均失真 0.40" in summary
    assert "先轻触试探" in summary


def test_apply_memory_persona_signal_to_persona_expression_hint_softens_suppressed_memory() -> None:
    hint = {
        "style_hint": "respond naturally",
        "tone": "caring",
        "echo_weight_modifier": 0.12,
        "follow_up_tendency": 0.16,
    }
    memory_signal = {
        "suppressed_memory_count": 1,
        "distorted_memory_count": 1,
        "avg_distortion_level": 0.48,
        "recall_bias": "uncertain",
        "memory_persona_guidance": "keep the memory touch soft",
    }

    adjusted = ccb._apply_memory_persona_signal_to_persona_expression_hint(hint, memory_signal)

    assert adjusted["tone"] == "caring"
    assert adjusted["follow_up_tendency"] == 0.03
    assert adjusted["echo_weight_modifier"] == 0.05
    assert adjusted["pace_hint"] == "steady"
    assert "memory_guidance" in adjusted
    assert "keep the memory touch soft" in str(adjusted.get("memory_guidance") or "")


def test_build_chat_context_pack_includes_memory_persona_signal(
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
    monkeypatch.setattr(ccb, "validate_context_pack", lambda _pack: pc.PromptContextValidationResult())

    prompt_context, meta = ccb.build_chat_context_pack(
        request_id="req-memory-persona-context",
        session_id="session-memory-persona-context",
        thread_id=None,
        retrieval_summary={"status": "OK", "reason_code": "NONE"},
        retrieval_evidence_items=[
            {
                "snippet": "shared walk by the river",
                "metadata": {
                    "emotion_color_tag": "warm",
                    "suppression_flag": True,
                    "distortion_level": 0.4,
                },
            },
            {
                "snippet": "quiet afterglow",
                "metadata": {
                    "emotion_color_tag": "warm",
                },
            },
        ],
        style_bindings=None,
        emotion_context={},
        cpb_slots={"style_canon": "STYLE_SLOT", "world_canon": "WORLD_SLOT"},
    )

    memory_hint = dict(prompt_context.get("memory_emotion_hint") or {})
    recall_summary = str(prompt_context.get("memory_recall_summary") or "")
    assert meta["valid"] is True
    assert memory_hint["humanized_memory_count"] == 2
    assert memory_hint["suppressed_memory_count"] == 1
    assert memory_hint["dominant_memory_emotion"] == "WARM"
    assert memory_hint["recall_bias"] == "uncertain"
    assert recall_summary
    assert memory_hint["memory_recall_summary"] == recall_summary


def test_build_chat_prompt_runtime_payload_applies_memory_persona_signal_to_expression_hint(
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
                "memory_emotion_hint": {
                    "mood_state": "steady",
                    "emotion_tag": "HAPPY",
                    "retrieval_status": "OK",
                    "retrieval_reason_code": "NONE",
                    "humanized_memory_count": 2,
                    "suppressed_memory_count": 1,
                    "distorted_memory_count": 1,
                    "avg_distortion_level": 0.52,
                    "dominant_memory_emotion": "WARM",
                    "recall_bias": "uncertain",
                    "memory_persona_guidance": "touch the memory gently and do not over-claim details",
                },
                "memory_recall_summary": "本次召回命中了 2 条带人格化标记的记忆；其中 1 条处于压制状态；处理建议：touch the memory gently and do not over-claim details",
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
            hint = SimpleNamespace(
                style_hint="respond naturally",
                tone="gentle",
                echo_weight_modifier=0.0,
                pace_hint="steady",
            )
            setattr(hint, "追问倾向", 0.0)
            return hint

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
        request_id="req-memory-expression",
        person_id="person-memory-expression",
        session_id="session-memory-expression",
        thread_id=None,
        user_message="我想起以前的事了",
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

    memory_signal = dict(runtime_payload.get("memory_persona_signal") or {})
    hint = dict(runtime_payload.get("persona_expression_hint") or {})

    assert memory_signal["suppressed_memory_count"] == 1
    assert memory_signal["avg_distortion_level"] == pytest.approx(0.52, abs=1e-4)
    assert runtime_payload["memory_recall_summary"].startswith("本次召回命中了 2 条带人格化标记的记忆")
    assert hint["tone"] == "caring"
    assert hint["follow_up_tendency"] == 0.03
    assert hint["echo_weight_modifier"] == 0.05
    assert "motive_guidance" in hint
    assert "memory_guidance" in hint


def test_build_chat_prompt_runtime_payload_passes_runtime_mode_flags_to_motive_and_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_SYS_MODE", "focus")
    monkeypatch.setattr(
        ccb,
        "build_chat_generation_prompt_payload",
        lambda **kwargs: {
            "prompt_context": {
                "authoritative": [],
                "retrieval": [],
                "style_pack": {},
                "constraints": {"retrieval_status": "OK"},
                "hard_snapshot": {"sys_mode": "focus"},
            },
            "prompt_context_validation": {"valid": True, "reason_code": None, "violation_count": 0},
            "prompt_context_observability": {"source_stats": {}},
            "emotion_context": {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "emotion_intensity": 0.3,
                "emotion_range_hint": "calm",
            },
            "user_message": kwargs["user_message"],
        },
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(
        deps,
        "_activity_monitor_ref",
        SimpleNamespace(get_status=lambda: {"is_gaming": False, "idle_minutes": 25.0}),
        raising=False,
    )

    captured: dict[str, dict] = {}

    class _FakeAdapter:
        def adapt(self, *_args, **_kwargs):  # noqa: ANN001
            hint = SimpleNamespace(
                style_hint="respond naturally",
                tone="gentle",
                echo_weight_modifier=0.0,
                pace_hint="steady",
            )
            setattr(hint, "追问倾向", 0.0)
            return hint

    class _FakePersonaEngine:
        @staticmethod
        def get_instance():
            return SimpleNamespace(get_traits=lambda _user_id: SimpleNamespace())

    class _FakeMotiveState:
        def as_dict(self):
            return {
                "care_drive": 0.32,
                "reach_out_drive": 0.24,
                "continuity_drive": 0.26,
                "withdraw_drive": 0.22,
                "self_protect_drive": 0.18,
                "dominant_motive": "care",
            }

    class _FakePolicy:
        def as_dict(self):
            return {"allow_followup": False, "max_sentences": 2}

    def _fake_compute_motive_state(inp):  # noqa: ANN001
        captured["motive"] = dict(inp.runtime_flags or {})
        return _FakeMotiveState()

    def _fake_decide_response_policy(inp):  # noqa: ANN001
        captured["policy"] = dict(inp.runtime_flags or {})
        return _FakePolicy()

    monkeypatch.setattr("host_brain.persona.expression_adapter.PersonaExpressionAdapter", _FakeAdapter)
    monkeypatch.setattr("host_brain.persona.persona_engine.PersonaEngine", _FakePersonaEngine)
    monkeypatch.setattr("host_brain.persona.motive_engine.compute_motive_state", _fake_compute_motive_state)
    monkeypatch.setattr("host_brain.persona.response_policy.decide_response_policy", _fake_decide_response_policy)

    runtime_input = build_chat_runtime_input(
        request_id="req-runtime-mode-flags",
        person_id="person-runtime-mode-flags",
        session_id="session-runtime-mode-flags",
        thread_id=None,
        user_message="继续说吧",
        render_target_lang="zh",
        retrieval_summary={"status": "OK"},
        retrieval_evidence_items=[],
        emotion_state={"mood_state": "steady", "emotion_tag": "NEUTRAL", "emotion_intensity": 0.3},
        recall_slot_text={},
        bond_summary={},
        scene_type="CHAT",
    )

    runtime_payload = ccb.build_chat_prompt_runtime_payload(
        runtime_input=runtime_input,
        style_bindings={},
    )

    assert dict(runtime_payload.get("runtime_mode") or {})["sys_mode"] == "focus"
    assert captured["motive"]["sys_mode"] == "focus"
    assert captured["policy"]["sys_mode"] == "focus"
    assert dict(captured["policy"].get("activity_monitor") or {})["idle_minutes"] == 25.0


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



def test_build_chat_prompt_runtime_payload_aligns_self_reflection_after_motive_and_policy(
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
                "emotion_tag": "THINKING",
                "emotion_intensity": 0.55,
                "emotion_range_hint": "measured",
            },
            "user_message": kwargs["user_message"],
        },
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "host_brain.persona.self_reflection.SelfReflection.maybe_generate",
        lambda self, **kwargs: {
            "self_awareness": "原始自省",
            "mood_awareness": "情绪还算平稳",
            "growth_note": "最近更会接住对话了",
        },
    )
    monkeypatch.setattr(
        "host_brain.persona.self_model.SelfModel.build_snapshot",
        lambda self, **kwargs: {"bond_growth_perception": "稳定"},
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
            return SimpleNamespace(get_traits=lambda _user_id: SimpleNamespace(), get_change_log=lambda *_args, **_kwargs: [])

    class _FakeMotiveState:
        def as_dict(self):
            return {
                "care_drive": 0.46,
                "reach_out_drive": 0.34,
                "continuity_drive": 0.82,
                "withdraw_drive": 0.12,
                "self_protect_drive": 0.10,
                "dominant_motive": "continuity",
            }

    class _FakePolicy:
        def as_dict(self):
            return {
                "allow_followup": True,
                "max_sentences": 3,
                "should_be_brief": True,
                "should_be_withdrawn": False,
                "withdrawal_guard": False,
            }

    captured: dict[str, dict] = {}

    def _fake_compute_motive_state(inp):  # noqa: ANN001
        captured["motive_pulse"] = dict(inp.self_reflection_pulse or {})
        return _FakeMotiveState()

    def _fake_decide_response_policy(inp):  # noqa: ANN001
        captured["policy_pulse"] = dict(inp.self_reflection_pulse or {})
        return _FakePolicy()

    monkeypatch.setattr("host_brain.persona.expression_adapter.PersonaExpressionAdapter", _FakeAdapter)
    monkeypatch.setattr("host_brain.persona.persona_engine.PersonaEngine", _FakePersonaEngine)
    monkeypatch.setattr("host_brain.persona.motive_engine.compute_motive_state", _fake_compute_motive_state)
    monkeypatch.setattr("host_brain.persona.response_policy.decide_response_policy", _fake_decide_response_policy)

    runtime_input = build_chat_runtime_input(
        request_id="req-self-reflect-align",
        person_id="person-self-reflect-align",
        session_id="session-self-reflect-align",
        thread_id=None,
        user_message="继续说刚刚那件事",
        render_target_lang="zh",
        retrieval_summary={"status": "OK"},
        retrieval_evidence_items=[],
        emotion_state={"mood_state": "steady", "emotion_tag": "THINKING", "emotion_intensity": 0.55},
        recall_slot_text={},
        bond_summary={"stage": "FRIEND"},
        scene_type="CHAT",
    )

    runtime_payload = ccb.build_chat_prompt_runtime_payload(
        runtime_input=runtime_input,
        style_bindings={},
    )

    pulse = dict(runtime_payload.get("self_reflection_pulse") or {})
    assert captured["motive_pulse"]["self_awareness"] == "原始自省"
    assert captured["policy_pulse"]["self_awareness"] == "原始自省"
    assert pulse["self_awareness"] != "原始自省"
    assert "接住" in pulse["self_awareness"]
    assert "说得短一点" in pulse["self_awareness"]
    assert pulse["state_alignment"] == pulse["self_awareness"]
    assert "motive:continuity" in pulse["state_alignment_reasons"]
    assert "policy:brief" in pulse["state_alignment_reasons"]
