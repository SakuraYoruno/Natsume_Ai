from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from host_brain.core import prompt_context as pc


def _valid_tool_meta() -> dict[str, object]:
    return {
        "id": "memory_recall",
        "name": "memory_recall",
        "desc": "recall recent context",
        "schema_hash": "sha256:" + ("a" * 64),
        "side_effect_level": "READ",
        "auth_level": "NONE",
        "domain_tags": ["memory"],
        "version": "v1",
    }


def _valid_context() -> dict[str, object]:
    return {
        "hard_snapshot": {},
        "bond_summary": {},
        "authoritative": [{"slot": "authoritative", "kind": "world_canon", "content": "soft note"}],
        "retrieval": [{"slot": "retrieval", "kind": "episodic", "content": "memory"}],
        "constraints": {},
        "tool_meta_pack": [_valid_tool_meta()],
    }


@pytest.fixture(autouse=True)
def _clear_dynamic_slots() -> None:
    pc._DYNAMIC_SLOT_REGISTRY.clear()
    yield
    pc._DYNAMIC_SLOT_REGISTRY.clear()


def test_validate_prompt_context_handles_non_dict_missing_unknown_and_dynamic_slots() -> None:
    non_dict = pc.validate_prompt_context("bad")  # type: ignore[arg-type]
    assert non_dict.valid is False
    assert non_dict.violations[0].code == pc.PC_SLOT_TYPE_INVALID

    broken = _valid_context()
    broken.pop("hard_snapshot")
    broken["mystery"] = 1
    broken["emotion_pulse"] = "bad"
    broken_result = pc.validate_prompt_context(broken)
    codes = {item.code for item in broken_result.violations}
    assert pc.PC_MISSING_SLOT in codes
    assert pc.PC_UNKNOWN_SLOT in codes
    assert pc.PC_SLOT_TYPE_INVALID in codes

    pc.register_slot("custom_slot", lambda _ctx: {"ok": True})
    dynamic = _valid_context()
    dynamic["custom_slot"] = {"ok": True}
    assert pc.validate_prompt_context(dynamic).valid is True


def test_validate_prompt_context_catches_slot_mixing_tool_meta_and_semantic_errors() -> None:
    payload = _valid_context()
    payload["authoritative"] = [{"slot": "retrieval", "kind": "world_canon", "content": "soft", "style_instruction": "oops"}]
    payload["retrieval"] = [{"slot": "authoritative", "kind": "episodic", "content": "memory", "tone": "warm"}]
    payload["tool_meta_pack"] = [
        {**_valid_tool_meta(), "command": "rm -rf /"},
        {**_valid_tool_meta(), "extra": "bad"},
    ]
    payload["semantic_slot"] = ["bad", {"semantic_id": "s1"}, {"summary": "only summary"}]

    result = pc.validate_prompt_context(payload)
    codes = [item.code for item in result.violations]

    assert codes.count(pc.CP_SLOT_MIXED) >= 2
    assert pc.CP_TOOL_META_FORBIDDEN_FIELD in codes
    assert pc.CP_TOOL_META_INVALID in codes
    assert pc.PC_SLOT_TYPE_INVALID in codes


def test_bond_summary_helpers_and_semantic_builder_cover_edge_cases() -> None:
    result = pc.PromptContextValidationResult()
    pc._validate_bond_summary_slot({"event_id": {"bad": True}}, result)
    assert any(item.code == pc.CP_BOND_SUMMARY_FIELD_INVALID for item in result.violations)

    result = pc.PromptContextValidationResult()
    pc._validate_bond_summary_slot({"evidence_refs": ["", "ok"]}, result)
    assert any(item.code == pc.CP_BOND_SUMMARY_FIELD_INVALID for item in result.violations)

    result = pc.PromptContextValidationResult()
    pc._check_slot_tag({}, expected_slot="authoritative", result=result)
    assert result.valid is True
    pc._check_slot_tag({"slot": "retrieval"}, expected_slot="authoritative", result=result)
    assert result.valid is False

    assert pc._contains_execution_claim("saved successfully") is True
    assert pc._contains_execution_claim("soft note only") is False
    assert list(pc._iter_values({"a": [1, {"b": "x"}], "c": True})) == [1, "x", True]

    ledger = SimpleNamespace(
        list_semantic_memories=lambda *, limit, offset: (
            [
                {
                    "semantic_id": "sem-1",
                    "summary": "summary",
                    "distill_strategy": "merge",
                    "confidence": 0.9,
                    "tags": ["persona"],
                    "source_memory_ids": ["m1", "m2"],
                    "ts_ms": 123,
                }
            ],
            1,
        )
    )
    semantic_slot = pc.build_semantic_slot(ledger=ledger, top_k=1)
    assert semantic_slot[0]["source_count"] == 2
    assert semantic_slot[0]["slot"] == "semantic_slot"


def test_inject_bond_summary_and_build_world_canon_slot() -> None:
    docs = [
        SimpleNamespace(doc_id="wc-1", version=1, hash="a" * 64, status="active", title="A", tags=["x"], body="body-a", ts_ms=10),
        SimpleNamespace(doc_id="wc-2", version=2, hash="b" * 64, status="draft", title="B", tags=["x"], body="body-b", ts_ms=20),
        SimpleNamespace(doc_id="wc-3", version=3, hash="c" * 64, status="active", title="C", tags=["x"], body="body-c", ts_ms=30),
    ]

    class _FakeStore:
        def __init__(self, world_root, ledger) -> None:
            self.world_root = world_root
            self.ledger = ledger

        def scan_documents(self):
            return docs, []

    with patch(
        "host_brain.memory.canon_world_store.WorldCanonStore",
        _FakeStore,
    ), patch(
        "host_brain.bond.summarizer.normalize_bond_summary",
        return_value={"stage": "关系好的兄妹"},
    ) as normalize_mock, patch(
        "host_brain.bond.summarizer.derive_bond_summary",
        return_value={"stage": "亲近兄妹"},
    ) as derive_mock:
        entries = pc.build_world_canon_slot(world_root="tmp/world", top_k=2, status_filter="active")
        explicit = pc.inject_bond_summary(_valid_context(), bond_summary={"stage": "关系好的兄妹"})
        derived = pc.inject_bond_summary(_valid_context(), bond_event={"type": "chat"})

    assert [item["doc_id"] for item in entries] == ["wc-3", "wc-1"]
    assert explicit["bond_summary"] == {"stage": "关系好的兄妹"}
    assert derived["bond_summary"] == {"stage": "亲近兄妹"}
    normalize_mock.assert_called_once()
    derive_mock.assert_called_once()

