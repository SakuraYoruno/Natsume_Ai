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
    path = root / f"{canon_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    path = root / f"{doc_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_build_node_style_bindings_loads_cn_and_ja(monkeypatch, tmp_path: Path) -> None:
    style_root = tmp_path / "style"
    _write_style_entry(style_root, canon_id="natsume_ai_cn", content="CN_STYLE_HINT")
    _write_style_entry(style_root, canon_id="natsume_ai_ja", content="JA_STYLE_HINT")

    monkeypatch.setenv("Natsume_STYLE_CANON_ROOT", str(style_root))
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_STYLE_CORE_CANON_ID", "natsume_ai_cn")
    monkeypatch.setenv("HOST_STYLE_RENDER_CANON_ID", "natsume_ai_ja")

    import host_brain.app.dependencies as dep
    dep._ledger = None
    from host_brain.app.chat_context_builder import build_node_style_bindings as _build_node_style_bindings
    bindings = _build_node_style_bindings()

    assert bindings["core_reasoner_primary"]["canon_id"] == "natsume_ai_cn"
    assert bindings["core_reasoner_secondary"]["canon_id"] == "natsume_ai_cn"
    assert bindings["chat_direct_ja"]["canon_id"] == "natsume_ai_ja"
    assert bindings["renderer_ja_c_style"]["canon_id"] == "natsume_ai_ja"
    assert "CN_STYLE_HINT" in str(bindings["core_reasoner_primary"]["style_instruction"])
    assert "JA_STYLE_HINT" in str(bindings["chat_direct_ja"]["style_instruction"])
    assert "JA_STYLE_HINT" in str(bindings["renderer_ja_c_style"]["style_instruction"])


def test_render_node_prompt_uses_node_specific_style() -> None:
    from host_brain.app.prompt_builder import render_node_generation_prompt as _render_node_generation_prompt

    runtime_payload = {
        "user_message": "测试风格绑定",
        "retrieval": {
            "status": "OK",
            "reason_code": "NONE",
            "evidence_items": [],
        },
        "style_bindings": {
            "core_reasoner_primary": {
                "canon_id": "natsume_ai_cn",
                "citation": "style_canon:natsume_ai_cn@v1:aaaa",
                "tone_only": True,
                "unsafe_as_fact": True,
                "style_instruction": "CN_STYLE_HINT",
            },
            "renderer_ja_c_style": {
                "canon_id": "natsume_ai_ja",
                "citation": "style_canon:natsume_ai_ja@v1:bbbb",
                "tone_only": True,
                "unsafe_as_fact": True,
                "style_instruction": "JA_STYLE_HINT",
            },
            "chat_direct_ja": {
                "canon_id": "natsume_ai_ja",
                "citation": "style_canon:natsume_ai_ja@v1:cccc",
                "tone_only": True,
                "unsafe_as_fact": True,
                "style_instruction": "JA_STYLE_HINT_DIRECT",
            },
        },
        "core_reasoner_primary": {
            "payload": {
                "narrative": {"text": "这是中文语义草案"},
                "intent_draft": {"skill_name": "narrative_only", "args": {}},
            }
        },
        "planner_constraints": {"payload": {"narrative": {"text": "planner"}}},
        "safety_verifier": {"payload": {"narrative": {"text": "safe"}}},
    }

    core_prompt = _render_node_generation_prompt(
        node_id="core_reasoner_primary",
        runtime_payload=runtime_payload,
    )
    render_prompt = _render_node_generation_prompt(
        node_id="renderer_ja_c_style",
        runtime_payload=runtime_payload,
    )
    direct_prompt = _render_node_generation_prompt(
        node_id="chat_direct_ja",
        runtime_payload=runtime_payload,
    )

    assert "STYLE_CANON_BINDING" in core_prompt
    assert "canon_id: natsume_ai_cn" in core_prompt
    assert "CN_STYLE_HINT" in core_prompt
    assert "canon_id: natsume_ai_ja" not in core_prompt

    assert "STYLE_CANON_BINDING" in render_prompt
    assert "canon_id: natsume_ai_ja" in render_prompt
    assert "JA_STYLE_HINT" in render_prompt
    assert "canon_id: natsume_ai_cn" not in render_prompt

    assert "STYLE_CANON_BINDING" in direct_prompt
    assert "canon_id: natsume_ai_ja" in direct_prompt
    assert "JA_STYLE_HINT_DIRECT" in direct_prompt
    assert "Role: chat_direct_ja" in direct_prompt


def test_build_chat_generation_prompt_payload_includes_valid_context_pack(
    monkeypatch,
    tmp_path: Path,
) -> None:
    style_root = tmp_path / "style"
    world_root = tmp_path / "world"
    _write_style_entry(style_root, canon_id="natsume_ai_cn", content="CN_STYLE_HINT")
    _write_style_entry(style_root, canon_id="natsume_ai_ja", content="JA_STYLE_HINT")
    _write_world_doc(
        world_root,
        doc_id="wc-003-persona-core",
        title="人格核心设定",
        body="夏目保持温柔与边界感，只能给出草案，不宣称执行。",
    )

    monkeypatch.setenv("Natsume_STYLE_CANON_ROOT", str(style_root))
    monkeypatch.setenv("Natsume_WORLD_CANON_ROOT", str(world_root))
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))

    import host_brain.app.dependencies as dep
    dep._ledger = None
    from host_brain.app.chat_context_builder import (
        build_node_style_bindings as _build_node_style_bindings,
        build_chat_generation_prompt_payload as _build_chat_generation_prompt_payload,
    )
    style_bindings = _build_node_style_bindings()
    payload = _build_chat_generation_prompt_payload(
        request_id="req_ctx_pack",
        session_id="session_ctx_pack",
        thread_id="thread_ctx_pack",
        user_message="测试 context pack",
        emotion_context={
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_range_hint": "可以表达积极情感",
        },
        retrieval_summary={
            "status": "OK",
            "reason_code": "NONE",
            "route_count": 1,
            "candidate_count": 1,
            "rerank_topk": 1,
            "degraded": False,
        },
        retrieval_evidence_items=[
            {
                "evidence_ref": "evd://episodic/test",
                "source_ref": "memory://episodic/test",
                "snippet": "这是检索摘要",
                "score": 0.9,
                "confidence": 0.9,
            }
        ],
        style_bindings=style_bindings,
    )

    context_pack = dict(payload.get("context_pack") or {})
    context_pack_validation = dict(payload.get("context_pack_validation") or {})
    assert context_pack
    assert context_pack_validation.get("valid") is True
    authoritative = list(context_pack.get("authoritative") or [])
    assert any(str(row.get("kind") or "") == "world_canon" for row in authoritative)
    style_pack = dict(context_pack.get("style_pack") or {})
    node_bindings = dict(style_pack.get("node_bindings") or {})
    assert "core_reasoner_primary" in node_bindings
    assert "chat_direct_ja" in node_bindings
    assert "renderer_ja_c_style" in node_bindings
    assert dict(payload.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"
    assert dict(context_pack.get("emotion_pulse") or {}).get("emotion_tag") == "HAPPY"
    assert dict(context_pack.get("memory_emotion_hint") or {}).get("mood_state") == "engaged"
    assert dict(context_pack.get("bond_texture") or {}).get("emotion_tag") == "HAPPY"


def test_render_node_prompt_prefers_context_pack_slots() -> None:
    from host_brain.app.prompt_builder import render_node_generation_prompt as _render_node_generation_prompt

    runtime_payload = {
        "user_message": "测试严格分槽",
        "retrieval": {
            "status": "DEGRADED",
            "reason_code": "RETR_PARTIAL",
            "evidence_items": [],
        },
        "context_pack": {
            "hard_snapshot": {"session_id": "s1"},
            "bond_summary": {},
            "authoritative": [
                {
                    "slot": "authoritative",
                    "kind": "world_canon",
                    "citation": "world_canon:wc-003-persona-core@v1:abcdef",
                    "content": "夏目不会宣称已执行动作。",
                }
            ],
            "retrieval": [
                {
                    "slot": "retrieval",
                    "kind": "episodic",
                    "evidence_ref": "evd://episodic/1",
                    "content": "用户喜欢简洁回答。",
                }
            ],
            "style_pack": {
                "node_bindings": {
                    "renderer_ja_c_style": {
                        "canon_id": "natsume_ai_ja",
                        "citation": "style_canon:natsume_ai_ja@v1:bbbb",
                        "tone_only": True,
                        "unsafe_as_fact": True,
                        "style_instruction": "JA_STYLE_HINT_FROM_CONTEXT_PACK",
                    }
                }
            },
            "constraints": {
                "strict_json_output": True,
                "no_false_execution_claim": True,
            },
            "tool_meta_pack": [],
            "emotion_pulse": {
                "emotion_tag": "HAPPY",
                "mood_state": "engaged",
                "style_hint": "情绪积极",
                "emotion_range_hint": "可以表达积极情感",
            },
            "memory_emotion_hint": {
                "emotion_tag": "HAPPY",
                "mood_state": "engaged",
                "retrieval_status": "DEGRADED",
            },
            "bond_texture": {
                "emotion_tag": "HAPPY",
                "mood_state": "engaged",
                "tone_hint": "保持温柔边界",
            },
        },
        "context_pack_validation": {
            "valid": True,
            "reason_code": None,
            "violation_count": 0,
        },
        "emotion_context": {
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_range_hint": "可以表达积极情感",
        },
        "core_reasoner_primary": {
            "payload": {
                "narrative": {"text": "这是中文语义草案"},
                "intent_draft": {"skill_name": "narrative_only", "args": {}},
            }
        },
        "planner_constraints": {"payload": {"narrative": {"text": "planner"}}},
        "safety_verifier": {"payload": {"narrative": {"text": "safe"}}},
    }

    prompt = _render_node_generation_prompt(
        node_id="renderer_ja_c_style",
        runtime_payload=runtime_payload,
    )

    assert "CONTEXT_PACK_VALIDATION" in prompt
    assert "valid: True" in prompt
    assert "WORLD_CANON_BINDING" in prompt
    assert "world_canon:wc-003-persona-core" in prompt
    assert "JA_STYLE_HINT_FROM_CONTEXT_PACK" in prompt
    assert "用户喜欢简洁回答" in prompt
    assert "EMOTION_BINDING" in prompt
    assert "emotion_tag: HAPPY" in prompt
    assert "mood_state: engaged" in prompt


def test_render_node_prompt_contains_node_role_binding_for_all_known_nodes() -> None:
    from host_brain.app.prompt_builder import render_node_generation_prompt as _render_node_generation_prompt

    base_runtime_payload = {
        "user_message": "测试节点提示词",
        "retrieval": {"status": "OK", "reason_code": "NONE", "evidence_items": []},
        "context_pack": {
            "hard_snapshot": {"session_id": "s1"},
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
        "context_pack_validation": {"valid": True, "reason_code": None, "violation_count": 0},
        "emotion_context": {"emotion_tag": "NEUTRAL", "mood_state": "steady"},
        "core_reasoner_primary": {"payload": {"narrative": {"text": "核心草案"}, "intent_draft": {}}},
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
        prompt = _render_node_generation_prompt(
            node_id=node_id,
            runtime_payload=base_runtime_payload,
        )
        assert "NODE_ROLE_BINDING" in prompt
        assert marker in prompt
