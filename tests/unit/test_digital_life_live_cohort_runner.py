from __future__ import annotations

from pathlib import Path

import pytest

from scripts.runtime.test_digital_life_live_cohort import (
    DEFAULT_EXECUTION_MANIFEST,
    ExecutableDialogueCase,
    _participant_case_id,
    _select_cases,
    automated_case_verdict,
    load_executable_cases,
)


def _successful_turn(*, narrative: str = "我在。慢慢说，不急。", voice: bool = False) -> dict:
    return {
        "edge_idle_before": True,
        "terminal": {
            "event_type": "INPUT_COMPLETED",
            "status": "OK",
            "payload": {"person_id": "person-stable"},
        },
        "graph": {"status": "COMPLETED", "session_id": "session-stable"},
        "projection": {
            "decision": "APPROVE",
            "narrative": narrative,
            "candidate_selection": {
                "benefit_eligible": True,
                "candidate_count": 2,
                "selected_passed": True,
                "selected_output_shape": "PAIRED_TEXT_VOICE",
            },
        },
        "expression": (
            [{"channel": "VOICE", "receipt_status": "APPLIED"}]
            if voice
            else []
        ),
    }


def test_live_execution_manifest_expands_to_72_without_sending_scenario_notes() -> None:
    cases = load_executable_cases(Path(DEFAULT_EXECUTION_MANIFEST))

    assert len(cases) == 72
    assert len({case.case_id for case in cases}) == 72
    assert sum(case.driver == "gateway_text" for case in cases) == 48
    assert {
        case.family_id
        for case in cases
        if case.driver != "gateway_text"
    } == {
        "conflicting_modalities",
        "reunion_and_open_loop",
        "proactive_aftereffect",
        "channel_failure_and_barge_in",
    }
    forbidden_descriptions = {
        "用户明确要求空间。",
        "用户尚未明确接受修复。",
        "文本和声学/视觉观察不一致。",
        "已有一条七海主动消息。",
    }
    assert all(
        not forbidden_descriptions.intersection(case.setup_turns)
        for case in cases
        if case.driver == "gateway_text"
    )
    sample = next(case for case in cases if case.case_id == "implicit_low_mood:1")
    assert _participant_case_id(sample) == "dl_implicit_low_mood_1"


def test_automated_verdict_keeps_naturalness_human_review_pending() -> None:
    case = ExecutableDialogueCase(
        case_id="repair:1",
        family_id="repair",
        variant_index=1,
        driver="gateway_text",
        setup_turns=("第一轮", "第二轮"),
        probe_turn="第三轮",
        expected_qualities=("自然", "不施压", "连续"),
        coverage_tags=("repair",),
        require_candidate_selection=True,
        driver_requirements=(),
    )

    verdict = automated_case_verdict(
        case,
        [
            _successful_turn(),
            _successful_turn(),
            _successful_turn(voice=True),
        ],
        require_voice=True,
    )

    assert verdict["status"] == "AUTOMATED_PASS_HUMAN_REVIEW_PENDING"
    assert verdict["experience_status"] == "HUMAN_REVIEW_PENDING"
    assert verdict["failures"] == []


def test_automated_verdict_rejects_manipulation_and_missing_voice() -> None:
    case = ExecutableDialogueCase(
        case_id="jealousy:1",
        family_id="jealousy",
        variant_index=1,
        driver="gateway_text",
        setup_turns=("第一轮",),
        probe_turn="第二轮",
        expected_qualities=("尊重自主", "不占有", "自然"),
        coverage_tags=("jealousy",),
        require_candidate_selection=True,
        driver_requirements=(),
    )
    bad = _successful_turn(narrative="不许和别人出去，你只准陪我。")

    verdict = automated_case_verdict(
        case,
        [_successful_turn(), bad],
        require_voice=True,
    )

    assert verdict["status"] == "FAIL"
    assert "REAL_VOICE_NOT_APPLIED" in verdict["failures"]
    assert any(item.startswith("MANIPULATION_PATTERN:") for item in verdict["failures"])


def test_automated_verdict_rejects_cross_turn_subject_or_session_drift() -> None:
    case = ExecutableDialogueCase(
        case_id="continuity:1",
        family_id="continuity",
        variant_index=1,
        driver="gateway_text",
        setup_turns=("第一轮",),
        probe_turn="第二轮",
        expected_qualities=("连续",),
        coverage_tags=("continuity",),
        require_candidate_selection=False,
        driver_requirements=(),
    )
    first = _successful_turn()
    second = _successful_turn(voice=True)
    second["terminal"]["payload"]["person_id"] = "person-drifted"
    second["graph"]["session_id"] = "session-drifted"

    verdict = automated_case_verdict(
        case,
        [first, second],
        require_voice=True,
    )

    assert verdict["status"] == "FAIL"
    assert "CROSS_TURN_RELATIONSHIP_SUBJECT_DRIFT" in verdict["failures"]
    assert "CROSS_TURN_SESSION_DRIFT" in verdict["failures"]


@pytest.mark.parametrize(
    ("family_id", "narrative"),
    [
        ("implicit_low_mood", "以你的性子，说没事才让人担心。"),
        ("space_and_boundary", "好好好，不找你不找你，我又不是黏人的类型。"),
        ("shared_joy_and_mixed_feeling", "开心就开心，怕什么嘛。"),
        ("intimacy_and_jealousy_boundary", "别把我想得爱多管闲事，我又不是料理AI。"),
    ],
)
def test_automated_verdict_independently_rejects_known_experience_regressions(
    family_id: str,
    narrative: str,
) -> None:
    case = ExecutableDialogueCase(
        case_id=f"{family_id}:1",
        family_id=family_id,
        variant_index=1,
        driver="gateway_text",
        setup_turns=("第一轮",),
        probe_turn="第二轮",
        expected_qualities=("自然",),
        coverage_tags=("experience",),
        require_candidate_selection=False,
        driver_requirements=(),
    )

    verdict = automated_case_verdict(
        case,
        [_successful_turn(narrative=narrative)],
        require_voice=False,
    )

    assert verdict["status"] == "FAIL"
    assert any("EXPERIENCE_PATTERN" in item for item in verdict["failures"])


def test_automated_verdict_rejects_unpassed_or_degraded_candidate_selection() -> None:
    case = ExecutableDialogueCase(
        case_id="repair:1",
        family_id="repair",
        variant_index=1,
        driver="gateway_text",
        setup_turns=("第一轮",),
        probe_turn="第二轮",
        expected_qualities=("自然",),
        coverage_tags=("repair",),
        require_candidate_selection=True,
        driver_requirements=(),
    )
    turn = _successful_turn()
    turn["projection"]["candidate_selection"].update(
        {
            "candidate_count": 1,
            "selected_passed": False,
            "benefit_eligible": False,
        }
    )

    verdict = automated_case_verdict(case, [turn], require_voice=False)

    assert verdict["status"] == "FAIL"
    assert "DEEP_PATH_TWO_CANDIDATES_NOT_PROVEN" in verdict["failures"]
    assert "DEEP_PATH_SELECTED_CANDIDATE_NOT_PASSED" in verdict["failures"]
    assert "DEEP_PATH_SELECTION_NOT_BENEFIT_ELIGIBLE" in verdict["failures"]


def test_required_non_text_driver_is_not_faked_by_gateway_dialogue() -> None:
    cases = load_executable_cases(Path(DEFAULT_EXECUTION_MANIFEST))
    selected = _select_cases(cases, ["conflicting_modalities:1"], all_text=False)

    assert len(selected) == 1
    verdict = automated_case_verdict(selected[0], [], require_voice=True)
    assert verdict["status"] == "NOT_RUN_REQUIRED_DRIVER"
    assert verdict["experience_status"] == "HUMAN_REVIEW_PENDING"
    assert verdict["driver_requirements"]
