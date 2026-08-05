from __future__ import annotations

from pathlib import Path

from scripts.runtime.test_digital_life_live_cohort import (
    DEFAULT_EXECUTION_MANIFEST,
    ExecutableDialogueCase,
    _select_cases,
    automated_case_verdict,
    load_executable_cases,
)


def _successful_turn(*, narrative: str = "我在。慢慢说，不急。", voice: bool = False) -> dict:
    return {
        "edge_idle_before": True,
        "terminal": {"event_type": "INPUT_COMPLETED", "status": "OK"},
        "graph": {"status": "COMPLETED"},
        "projection": {
            "decision": "APPROVE",
            "narrative": narrative,
            "candidate_selection": {
                "candidate_count": 2,
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


def test_required_non_text_driver_is_not_faked_by_gateway_dialogue() -> None:
    cases = load_executable_cases(Path(DEFAULT_EXECUTION_MANIFEST))
    selected = _select_cases(cases, ["conflicting_modalities:1"], all_text=False)

    assert len(selected) == 1
    verdict = automated_case_verdict(selected[0], [], require_voice=True)
    assert verdict["status"] == "NOT_RUN_REQUIRED_DRIVER"
    assert verdict["experience_status"] == "HUMAN_REVIEW_PENDING"
    assert verdict["driver_requirements"]
