from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.fastpaths import (
    _build_canonical_retrieval_relationship_summary,
    _prefetch_retrieval_persona_inputs,
)


def _canonical_context(
    *,
    workspace_id: str = "workspace-dl06",
    person_id: str = "person-dl06",
    session_id: str = "session-dl06",
    repair_required: bool = False,
    rupture_load: float = 0.0,
) -> dict[str, object]:
    return {
        "_affective_life_internal": True,
        "affective_life": {
            "schema_version": "affective_turn_context.v1",
            "self_id": "natsume_ai",
            "workspace_id": workspace_id,
            "relationship_subject_id": person_id,
            "session_id": session_id,
            "relationship_life": {
                "schema_version": "relationship_turn_context.v1",
                "workspace_id": workspace_id,
                "relationship_subject_id": person_id,
                "state": {
                    "schema_version": "relationship_state.v2",
                    "self_id": "natsume_ai",
                    "workspace_id": workspace_id,
                    "relationship_subject_id": person_id,
                    "revision": 4,
                    "trust": 0.96,
                    "safety": 0.90,
                    "attunement": 0.88,
                    "rupture_load": rupture_load,
                    "repair_required": repair_required,
                    "proactive_permission": "ALLOWED",
                },
            },
        },
    }


def test_canonical_retrieval_projection_never_carries_legacy_intimacy_score() -> None:
    summary = _build_canonical_retrieval_relationship_summary(
        emotion_context=_canonical_context(),
        workspace_id="workspace-dl06",
        person_id="person-dl06",
        session_id="session-dl06",
    )

    assert summary == {
        "source_authority": "CANONICAL_RELATIONSHIP_LIFE",
        "stage": "FRIEND",
        "bond_stage": "FRIEND",
        "relationship_phase": "stepsibling",
        "relationship_distance": "familiar",
        "trust": 0.96,
        "safety": 0.90,
        "attunement": 0.88,
    }
    assert "score" not in summary
    assert "proactive_permission" not in summary


def test_invalid_claimed_canonical_context_becomes_guarded_without_legacy_fallback() -> None:
    summary = _build_canonical_retrieval_relationship_summary(
        emotion_context=_canonical_context(workspace_id="other-workspace"),
        workspace_id="workspace-dl06",
        person_id="person-dl06",
        session_id="session-dl06",
    )

    assert summary == {
        "source_authority": "CANONICAL_RELATIONSHIP_REJECTED",
        "stage": "FRIEND",
        "bond_stage": "FRIEND",
        "relationship_phase": "stepsibling",
        "relationship_distance": "guarded",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("emotion_context", "expected_authority", "expected_distance"),
    [
        (_canonical_context(), "CANONICAL_RELATIONSHIP_LIFE", "familiar"),
        (
            _canonical_context(workspace_id="other-workspace"),
            "CANONICAL_RELATIONSHIP_REJECTED",
            "guarded",
        ),
    ],
)
async def test_retrieval_prefetch_skips_legacy_bond_fetch_for_claimed_life_context(
    monkeypatch: pytest.MonkeyPatch,
    emotion_context: dict[str, object],
    expected_authority: str,
    expected_distance: str,
) -> None:
    calls: list[object] = []

    async def _forbidden_legacy_fetch(**_kwargs):  # noqa: ANN003
        calls.append("legacy")
        raise AssertionError("canonical Life context must not prefetch legacy Bond")

    def _motive(**kwargs):  # noqa: ANN003
        return {"received_bond": dict(kwargs["bond_summary"])}

    import host_brain.persona.motive_runtime as motive_runtime

    monkeypatch.setattr(motive_runtime, "compute_runtime_motive_state", _motive)
    summary, evidence, motive = await _prefetch_retrieval_persona_inputs(
        application_ports=SimpleNamespace(
            resolve_state_arbiter_bond_fact=_forbidden_legacy_fetch,
        ),
        request_id="request-dl06",
        session_id="session-dl06",
        person_id="person-dl06",
        workspace_id="workspace-dl06",
        user_message="记得上次的安排吗？",
        emotion_context=emotion_context,
        mood_state="calm",
        governor_decision={"sys_mode": "normal"},
    )

    assert calls == []
    assert evidence is None
    assert summary["source_authority"] == expected_authority
    assert summary["relationship_distance"] == expected_distance
    assert motive["received_bond"]["source_authority"] == expected_authority
