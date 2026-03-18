from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_style_entry(root: Path, *, canon_id: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "canon_id": canon_id,
        "version": 1,
        "hash": ("a" * 64),
        "tone_only": True,
        "unsafe_as_fact": True,
        "content": content,
        "tags": ["style_canon", "tone_only"],
        "updated_by": "test",
        "ts_ms": 0,
    }
    (root / f"{canon_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )


def _write_world_doc(root: Path, *, doc_id: str, title: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "doc_id": doc_id,
        "version": 1,
        "status": "active",
        "title": title,
        "tags": ["persona", "invariant"],
        "body": body,
        "updated_by": "test",
        "ts_ms": 1,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    (root / f"{doc_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )


def _make_runtime_payload(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    retrieval_summary: dict,
    retrieval_evidence_items: list[dict],
    recall_slot_text: dict[str, str] | None = None,
    emotion_state: dict | None = None,
):
    from host_brain.app.chat_context_builder import build_chat_runtime_payload
    from host_brain.app.chat_runtime_input import build_chat_runtime_input

    runtime_input = build_chat_runtime_input(
        request_id=request_id,
        session_id=session_id,
        thread_id=None,
        user_message=user_message,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        recall_slot_text=recall_slot_text or {},
        emotion_state=emotion_state or {},
        render_target_lang="ja",
    )
    payload = build_chat_runtime_payload(runtime_input=runtime_input)
    payload.update(
        {
            "core_reasoner_primary": {
                "payload": {
                    "narrative": {"text": "core narrative"},
                    "intent_draft": {"skill_name": "narrative_only", "args": {}},
                }
            },
            "planner_constraints": {"payload": {"narrative": {"text": "planner"}}},
            "safety_verifier": {"payload": {"narrative": {"text": "safe"}}},
        }
    )
    return payload


def test_build_chat_runtime_payload_uses_recall_slots_for_background() -> None:
    payload = _make_runtime_payload(
        request_id="req_recall_slots",
        session_id="session_recall_slots",
        user_message="test prompt context",
        retrieval_summary={
            "status": "OK",
            "reason_code": "NONE",
            "route_count": 7,
            "candidate_count": 2,
            "rerank_topk": 2,
            "degraded": False,
        },
        retrieval_evidence_items=[],
        recall_slot_text={
            "style_canon": "JA_STYLE_HINT",
            "world_canon": "Natsume never claims completed execution.",
        },
    )

    assert "style" not in payload
    prompt_context = dict(dict(payload.get("prompt_context") or {}).get("payload") or {})
    style_pack = dict(prompt_context.get("style_pack") or {})
    style_rows = list(style_pack.get("style_canon") or [])
    authoritative = list(prompt_context.get("authoritative") or [])
    assert len(style_rows) == 1
    assert style_rows[0].get("source") == "recall_pack_builder"
    assert style_rows[0].get("instruction") == "JA_STYLE_HINT"
    assert len(authoritative) == 1
    assert authoritative[0].get("citation") == "recall_pack_builder://world_canon"


def test_build_chat_runtime_payload_includes_valid_prompt_context() -> None:
    payload = _make_runtime_payload(
        request_id="req_prompt_context",
        session_id="session_prompt_context",
        user_message="test prompt context",
        retrieval_summary={
            "status": "OK",
            "reason_code": "NONE",
            "route_count": 3,
            "candidate_count": 1,
            "rerank_topk": 1,
            "degraded": False,
        },
        retrieval_evidence_items=[
            {
                "evidence_ref": "evd://episodic/test",
                "source_ref": "memory://episodic/test",
                "snippet": "retrieval snippet",
                "score": 0.9,
                "confidence": 0.9,
            }
        ],
        recall_slot_text={
            "style_canon": "JA_STYLE_HINT",
            "world_canon": "Natsume keeps a soft boundary and never claims completed execution.",
        },
        emotion_state={
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_range_hint": "positive expression is allowed",
        },
    )

    prompt_context_section = dict(payload.get("prompt_context") or {})
    prompt_context = dict(prompt_context_section.get("payload") or {})
    prompt_context_validation = dict(prompt_context_section.get("validation") or {})
    prompt_context_observability = dict(prompt_context_section.get("observability") or {})

    assert prompt_context
    assert prompt_context_validation.get("valid") is True
    assert prompt_context_observability.get("version") == "prompt_context_obs.v1"
    assert prompt_context_observability.get("selected_tier") == "core_three"
    retrieval_metrics = dict(prompt_context_observability.get("retrieval_metrics") or {})
    source_stats = dict(prompt_context_observability.get("source_stats") or {})
    assert retrieval_metrics.get("path") == "success"
    assert source_stats.get("retrieval_used_items") == 1
    assert source_stats.get("style_prompt_rows") == 1

    authoritative = list(prompt_context.get("authoritative") or [])
    assert any(str(row.get("kind") or "") == "world_canon" for row in authoritative)

    style_pack = dict(prompt_context.get("style_pack") or {})
    style_rows = list(style_pack.get("style_canon") or [])
    assert len(style_rows) == 1
    assert style_rows[0].get("source") == "recall_pack_builder"

    emotion_section = dict(payload.get("emotion") or {})
    emotion_state = dict(emotion_section.get("state") or {})
    emotion_snapshot = dict(emotion_section.get("snapshot") or {})
    assert emotion_state.get("emotion_tag") == "HAPPY"
    assert emotion_snapshot.get("version") == "chat_emotion_snapshot.v1"
    assert dict(prompt_context.get("emotion_pulse") or {}).get("emotion_tag") == "HAPPY"
    assert dict(prompt_context.get("memory_emotion_hint") or {}).get("mood_state") == "engaged"
    assert dict(prompt_context.get("bond_texture") or {}).get("emotion_tag") == "HAPPY"


def test_build_chat_runtime_payload_marks_persona_utterance_rows() -> None:
    payload = _make_runtime_payload(
        request_id="req_prompt_context_persona",
        session_id="session_prompt_context_persona",
        user_message="need a gentle answer",
        retrieval_summary={
            "status": "OK",
            "reason_code": "NONE",
            "route_count": 2,
            "candidate_count": 2,
            "rerank_topk": 2,
            "degraded": False,
        },
        retrieval_evidence_items=[
            {
                "evidence_ref": "evd://persona_utterance/natsume_ai_ja/ja/01",
                "source_ref": "persona_utterance://natsume_ai_ja/ja/01",
                "snippet": "example line",
                "score": 0.91,
                "confidence": 0.91,
                "metadata": {
                    "store_id": "persona_utterance",
                    "canon_id": "natsume_ai_ja",
                    "lang": "ja",
                    "speaker": "Natsume",
                    "file_rel_path": "story/ch01.ast",
                    "block_id": "block_01",
                    "line_index": 3,
                },
            }
        ],
        recall_slot_text={"style_canon": "JA_STYLE_HINT"},
    )

    prompt_context = dict(dict(payload.get("prompt_context") or {}).get("payload") or {})
    retrieval_rows = list(prompt_context.get("retrieval") or [])
    assert retrieval_rows
    top = dict(retrieval_rows[0])
    assert top.get("kind") == "persona_utterance"
    assert top.get("store_id") == "persona_utterance"
    assert top.get("lang") == "ja"
    assert top.get("speaker") == "Natsume"


def test_prompt_context_observability_marks_bare_llm_when_minimal_fallback() -> None:
    from host_brain.app.chat_context_builder import build_prompt_context_observability

    observability = build_prompt_context_observability(
        prompt_context={
            "hard_snapshot": {},
            "bond_summary": {},
            "authoritative": [],
            "retrieval": [],
            "style_pack": {},
            "constraints": {},
            "tool_meta_pack": [],
        },
        prompt_context_validation={
            "valid": True,
            "degraded": True,
            "reason_code": "PROMPT_CONTEXT_MINIMAL_FALLBACK",
            "violation_count": 2,
            "violations": [],
        },
        retrieval_summary={
            "status": "DEGRADED",
            "reason_code": "RETRIEVAL_FAILED",
            "route_count": 0,
            "candidate_count": 0,
            "rerank_topk": 0,
            "degraded": True,
        },
        retrieval_evidence_items=[],
    )

    assert observability.get("selected_tier") == "bare_llm"
    assert observability.get("selected_reason") == "prompt_context_minimal_fallback_and_empty_slots"
    retrieval_metrics = dict(observability.get("retrieval_metrics") or {})
    assert retrieval_metrics.get("path") == "all_failure"


def test_render_node_prompt_prefers_prompt_context_slots() -> None:
    from host_brain.app.prompt_builder import render_node_generation_prompt

    runtime_payload = {
        "request": {
            "request_id": "req-render",
            "session_id": "session-render",
            "thread_id": None,
            "user_message": "render prompt context",
        },
        "render": {"target_lang": "ja"},
        "retrieval": {
            "status": "DEGRADED",
            "reason_code": "RETR_PARTIAL",
            "evidence_items": [],
        },
        "emotion": {
            "state": {
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_range_hint": "positive expression is allowed",
                "emotion_intensity": 0.76,
            },
            "snapshot": {
                "version": "chat_emotion_snapshot.v1",
                "source": "test_case",
                "captured_ts_ms": 1234567890,
                "emotion_ts_ms": 1234567000,
                "age_ms": 890,
                "emotion_context": {
                    "mood_state": "engaged",
                    "emotion_tag": "HAPPY",
                    "emotion_range_hint": "positive expression is allowed",
                    "emotion_intensity": 0.76,
                },
            },
        },
        "prompt_context": {
            "payload": {
                "hard_snapshot": {"session_id": "session-render"},
                "bond_summary": {},
                "authoritative": [
                    {
                        "slot": "authoritative",
                        "kind": "world_canon",
                        "citation": "world_canon:wc-003-persona-core@v1:abcdef",
                        "content": "Natsume never claims completed execution.",
                    }
                ],
                "retrieval": [
                    {
                        "slot": "retrieval",
                        "kind": "episodic",
                        "evidence_ref": "evd://episodic/1",
                        "content": "user prefers concise replies",
                    }
                ],
                "style_pack": {
                    "style_canon": [
                        {
                            "slot": "style_pack",
                            "kind": "style_canon",
                            "node_id": "renderer_ja_c_style",
                            "canon_id": "natsume_ai_ja",
                            "citation": "style_canon:natsume_ai_ja@v1:bbbb",
                            "tone_only": True,
                            "unsafe_as_fact": True,
                            "instruction": "JA_STYLE_HINT_FROM_PROMPT_CONTEXT",
                            "source": "recall_pack_builder",
                        }
                    ]
                },
                "constraints": {
                    "strict_json_output": True,
                    "no_false_execution_claim": True,
                },
                "tool_meta_pack": [],
                "emotion_pulse": {
                    "emotion_tag": "HAPPY",
                    "mood_state": "engaged",
                    "style_hint": "energetic but gentle",
                    "emotion_range_hint": "positive expression is allowed",
                },
                "memory_emotion_hint": {
                    "emotion_tag": "HAPPY",
                    "mood_state": "engaged",
                    "retrieval_status": "DEGRADED",
                },
                "bond_texture": {
                    "emotion_tag": "HAPPY",
                    "mood_state": "engaged",
                    "tone_hint": "keep a warm boundary",
                },
            },
            "validation": {
                "valid": True,
                "reason_code": None,
                "violation_count": 0,
            },
            "observability": {},
        },
        "core_reasoner_primary": {
            "payload": {
                "narrative": {"text": "core narrative"},
                "intent_draft": {"skill_name": "narrative_only", "args": {}},
            }
        },
        "planner_constraints": {"payload": {"narrative": {"text": "planner"}}},
        "safety_verifier": {"payload": {"narrative": {"text": "safe"}}},
    }

    prompt = render_node_generation_prompt(
        node_id="renderer_ja_c_style",
        runtime_payload=runtime_payload,
    )

    assert "PROMPT_CONTEXT_VALIDATION" in prompt
    assert "valid: True" in prompt
    assert "WORLD_CANON_BINDING" in prompt
    assert "world_canon:wc-003-persona-core" in prompt
    assert "JA_STYLE_HINT_FROM_PROMPT_CONTEXT" in prompt
    assert "user prefers concise replies" in prompt
    assert "EMOTION_BINDING" in prompt
    assert "emotion_tag: HAPPY" in prompt
    assert "mood_state: engaged" in prompt
    assert "emotion_intensity: 0.76" in prompt
    assert "snapshot_source: test_case" in prompt


def test_render_node_prompt_contains_node_role_binding_for_all_known_nodes() -> None:
    from host_brain.app.prompt_builder import render_node_generation_prompt

    base_runtime_payload = {
        "request": {
            "request_id": "req-role",
            "session_id": "session-role",
            "thread_id": None,
            "user_message": "test node role",
        },
        "render": {"target_lang": "ja"},
        "retrieval": {"status": "OK", "reason_code": "NONE", "evidence_items": []},
        "style": {"node_bindings": {}},
        "emotion": {
            "state": {"emotion_tag": "NEUTRAL", "mood_state": "steady"},
            "snapshot": {"emotion_context": {"emotion_tag": "NEUTRAL", "mood_state": "steady"}},
        },
        "prompt_context": {
            "payload": {
                "hard_snapshot": {"session_id": "session-role"},
                "bond_summary": {},
                "authoritative": [],
                "retrieval": [],
                "style_pack": {"node_bindings": {}},
                "constraints": {},
                "tool_meta_pack": [],
                "emotion_pulse": {"emotion_tag": "NEUTRAL", "mood_state": "steady"},
                "memory_emotion_hint": {},
                "bond_texture": {},
            },
            "validation": {"valid": True, "reason_code": None, "violation_count": 0},
            "observability": {},
        },
        "core_reasoner_primary": {"payload": {"narrative": {"text": "core"}, "intent_draft": {}}},
        "planner_constraints": {"payload": {"narrative": {"text": "constraints"}}},
        "safety_verifier": {"payload": {"narrative": {"text": "safety"}}},
    }
    expected_role_markers = {
        "chat_direct_ja": "Role: chat_direct_ja",
        "core_reasoner_primary": "Role: core_reasoner_primary",
        "core_reasoner_secondary": "Role: core_reasoner_secondary",
        "planner_constraints": "Role: planner_constraints",
        "tool_planner": "Role: tool_planner",
        "safety_verifier": "Role: safety_verifier",
        "consistency_verifier": "Role: consistency_verifier",
        "workbench_translate": "Role: workbench_translate",
        "workbench_code_assist": "Role: workbench_code_assist",
        "renderer_ja_c_style": "Role: renderer_ja_c_style",
    }

    for node_id, marker in expected_role_markers.items():
        prompt = render_node_generation_prompt(
            node_id=node_id,
            runtime_payload=base_runtime_payload,
        )
        assert "NODE_ROLE_BINDING" in prompt
        assert marker in prompt
