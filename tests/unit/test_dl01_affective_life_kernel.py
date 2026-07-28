from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.digital_life.domain_reducer import reduce_life_domains
from natsume_contracts.life import (
    EmotionComponentV1,
    EmotionEpisodeV1,
    EventAppraisalV1,
    RegulationDecisionV1,
    UserAffectObservationV1,
)


NOW = 1_900_000_000_000
PERSON_ID = "person-dl01"


def _reduce(
    before: dict,
    *,
    command_type: str,
    payload: dict,
    command_id: str,
    now_ms: int,
) -> dict:
    return reduce_life_domains(
        before,
        person_id=PERSON_ID,
        command_id=command_id,
        command_type=command_type,
        payload=payload,
        created_at_ms=now_ms,
    )


def _observation(*, observed_at_ms: int = NOW) -> UserAffectObservationV1:
    return UserAffectObservationV1(
        observation_id="observation-1",
        workspace_id="workspace-1",
        relationship_subject_id=PERSON_ID,
        session_id="session-1",
        modality="TEXT",
        emotion_distribution={"SAD": 0.7, "CALM": 0.2},
        valence=-0.55,
        arousal=0.32,
        confidence=0.44,
        observed_at_ms=observed_at_ms,
        expires_at_ms=observed_at_ms + 30_000,
        evidence_refs=("observation://sha256/one",),
    )


def _appraisal(*, suffix: str = "1") -> EventAppraisalV1:
    return EventAppraisalV1(
        appraisal_id=f"appraisal-{suffix}",
        workspace_id="workspace-1",
        relationship_subject_id=PERSON_ID,
        session_id="session-1",
        event_kind="boundary_misunderstanding",
        target_ref=f"event://sha256/{suffix}",
        goal_congruence=-0.68,
        responsibility=0.42,
        controllability=0.34,
        relationship_significance=0.86,
        boundary_relevance=0.64,
        risk=0.31,
        uncertainty=0.18,
        evidence_refs=(f"appraisal://sha256/{suffix}",),
        observation_refs=("observation-1",),
    )


def _episode(
    *,
    suffix: str = "1",
    opened_at_ms: int = NOW,
    intensity: float = 0.72,
) -> EmotionEpisodeV1:
    return EmotionEpisodeV1(
        episode_id=f"episode-{suffix}",
        appraisal_id=f"appraisal-{suffix}",
        relationship_subject_id=PERSON_ID,
        trigger_ref=f"event://sha256/{suffix}",
        components=(
            EmotionComponentV1(
                emotion="HURT",
                intensity=intensity,
                valence=-0.78,
                arousal=0.52,
                action_tendency="repair",
            ),
            EmotionComponentV1(
                emotion="CARE",
                intensity=0.43,
                valence=0.54,
                arousal=0.31,
                action_tendency="comfort",
            ),
        ),
        opened_at_ms=opened_at_ms,
        decay_half_life_ms=60_000,
        evidence_refs=(f"episode://sha256/{suffix}",),
    )


def _appraise(
    before: dict,
    *,
    suffix: str = "1",
    now_ms: int = NOW,
    intensity: float = 0.72,
) -> dict:
    appraisal = _appraisal(suffix=suffix)
    episode = _episode(
        suffix=suffix,
        opened_at_ms=now_ms,
        intensity=intensity,
    )
    return _reduce(
        before,
        command_type="affective_event_appraised",
        command_id=f"command-appraisal-{suffix}",
        payload={"appraisal": appraisal.as_dict(), "episode": episode.as_dict()},
        now_ms=now_ms,
    )


def test_user_affect_observation_is_separate_from_self_affect() -> None:
    before = {"person_id": PERSON_ID, "version": 0}
    frozen = deepcopy(before)

    after = _reduce(
        before,
        command_type="affective_observation_recorded",
        command_id="command-observation",
        payload={"observation": _observation().as_dict()},
        now_ms=NOW,
    )

    assert before == frozen
    assert after["identity"] == {
        "self_id": "natsume_ai",
        "relationship_subject_id": PERSON_ID,
    }
    assert after["observations"]["last_user_affect"]["emotion_distribution"] == {
        "CALM": 0.2,
        "SAD": 0.7,
    }
    assert after["affective"]["revision"] == 0
    assert after["affective"]["components"] == []
    assert after["affective"]["mood"]["label"] == "calm"
    assert "user_message" not in after["affective"]["last_observation"]


def test_only_appraisal_creates_mixed_self_affect_and_time_decays_it() -> None:
    appraised = _appraise({"person_id": PERSON_ID, "version": 0})
    components = appraised["affective"]["components"]

    assert [component["emotion"] for component in components] == ["HURT", "CARE"]
    assert appraised["affective"]["revision"] == 1
    assert appraised["affective"]["mood"]["label"] == "hurt"
    assert appraised["affective"]["last_appraisal"]["appraisal_id"] == "appraisal-1"
    assert appraised["affect"]["valence"] == 0.18

    decayed = _reduce(
        appraised,
        command_type="affective_time_advanced",
        command_id="command-decay",
        payload={},
        now_ms=NOW + 60_000,
    )

    by_emotion = {
        row["emotion"]: row["intensity"]
        for row in decayed["affective"]["components"]
    }
    assert by_emotion["HURT"] == 0.36
    assert by_emotion["CARE"] == 0.215
    assert decayed["affective"]["revision"] == 2


def test_episode_head_is_bounded_and_regulation_resolution_are_causal() -> None:
    state: dict = {"person_id": PERSON_ID, "version": 0}
    for index in range(5):
        suffix = str(index)
        state = _appraise(
            state,
            suffix=suffix,
            now_ms=NOW + index,
            intensity=0.2 + index * 0.1,
        )

    episodes = state["affective"]["active_episodes"]
    assert len(episodes) == 4
    assert "episode-0" not in {episode["episode_id"] for episode in episodes}

    decision = RegulationDecisionV1(
        regulation_id="regulation-1",
        relationship_subject_id=PERSON_ID,
        mode="SEEK_REPAIR",
        reason_refs=("appraisal://sha256/4",),
        decided_at_ms=NOW + 10,
        episode_id="episode-4",
    )
    regulated = _reduce(
        state,
        command_type="affective_regulation_decided",
        command_id="command-regulation",
        payload={"regulation": decision.as_dict()},
        now_ms=NOW + 10,
    )
    assert regulated["affective"]["last_regulation"]["mode"] == "SEEK_REPAIR"

    resolved = _reduce(
        regulated,
        command_type="affective_episode_resolved",
        command_id="command-resolution",
        payload={
            "episode_id": "episode-4",
            "resolution_ref": "aftereffect://sha256/accepted",
        },
        now_ms=NOW + 11,
    )
    assert "episode-4" not in {
        episode["episode_id"]
        for episode in resolved["affective"]["active_episodes"]
    }
    assert resolved["affective"]["last_resolution"] == {
        "episode_id": "episode-4",
        "resolution_ref": "aftereffect://sha256/accepted",
        "resolved_at_ms": NOW + 11,
    }


def test_canonical_affective_domains_reject_state_patch_and_raw_material() -> None:
    with pytest.raises(ValueError, match="canonical affective domains"):
        _reduce(
            {"person_id": PERSON_ID, "version": 0},
            command_type="tick",
            command_id="command-forbidden-patch",
            payload={"state_patch": {"affective": {"revision": 999}}},
            now_ms=NOW,
        )

    with pytest.raises(ValueError, match="cannot contain user_message"):
        _reduce(
            {"person_id": PERSON_ID, "version": 0},
            command_type="affective_observation_recorded",
            command_id="command-raw-material",
            payload={
                "observation": _observation().as_dict(),
                "user_message": "private content must never enter the affect journal",
            },
            now_ms=NOW,
        )


def test_contracts_fail_closed_for_identity_and_episode_bounds() -> None:
    with pytest.raises(ValueError, match="self_id must be natsume_ai"):
        EventAppraisalV1(
            appraisal_id="appraisal-invalid",
            workspace_id="workspace-1",
            relationship_subject_id=PERSON_ID,
            session_id="session-1",
            event_kind="test",
            target_ref="event://one",
            goal_congruence=0.0,
            responsibility=0.0,
            controllability=0.0,
            relationship_significance=0.0,
            boundary_relevance=0.0,
            risk=0.0,
            uncertainty=0.0,
            self_id="user",
        )

    with pytest.raises(ValueError, match="components must contain 1 to 8"):
        EmotionEpisodeV1(
            episode_id="episode-overflow",
            appraisal_id="appraisal-overflow",
            relationship_subject_id=PERSON_ID,
            trigger_ref="event://overflow",
            components=tuple(
                EmotionComponentV1(
                    emotion=f"EMOTION_{index}",
                    intensity=0.2,
                    valence=0.0,
                    arousal=0.2,
                )
                for index in range(9)
            ),
            opened_at_ms=NOW,
            decay_half_life_ms=60_000,
        )
