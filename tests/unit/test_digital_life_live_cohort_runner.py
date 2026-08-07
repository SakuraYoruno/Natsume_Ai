from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import pytest

from scripts.runtime import test_digital_life_live_cohort as live_cohort
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
        "reply_delivery": {
            "status": "COMMITTED",
            "surface_receipt_ref": "gateway-outbox://id/test",
        },
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


def _create_writer_probe_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for table_name in (
            *live_cohort.LEGACY_AFFECT_WRITER_TABLES,
            *live_cohort.CANONICAL_AFFECT_WRITER_TABLES,
        ):
            connection.execute(
                f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY)'
            )


def test_affect_writer_probe_proves_zero_legacy_delta_and_canonical_progress(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.sqlite3"
    emotion_root = tmp_path / "emotion"
    emotion_root.mkdir()
    (emotion_root / "default.json").write_text('{"mood":"old"}', encoding="utf-8")
    _create_writer_probe_database(db_path)
    before = live_cohort._affect_writer_snapshot(
        db_path=db_path,
        legacy_emotion_root=emotion_root,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO affective_life_event_v1 DEFAULT VALUES")
        connection.execute("INSERT INTO relationship_life_event_v1 DEFAULT VALUES")
    after = live_cohort._affect_writer_snapshot(
        db_path=db_path,
        legacy_emotion_root=emotion_root,
    )

    probe = live_cohort._build_affect_writer_probe(
        before=before,
        after=after,
        outcomes=[{"turns": [_successful_turn()]}],
    )

    assert probe["status"] == (
        "PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS"
    )
    assert set(probe["legacy_table_deltas"].values()) == {0}
    assert probe["canonical_table_deltas"]["affective_life_event_v1"] == 1
    assert probe["canonical_table_deltas"]["relationship_life_event_v1"] == 1
    assert probe["changed_legacy_file_refs"] == []


def test_affect_writer_probe_fails_on_legacy_table_or_file_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.sqlite3"
    emotion_root = tmp_path / "emotion"
    emotion_root.mkdir()
    legacy_file = emotion_root / "default.json"
    legacy_file.write_text('{"mood":"old"}', encoding="utf-8")
    _create_writer_probe_database(db_path)
    before = live_cohort._affect_writer_snapshot(
        db_path=db_path,
        legacy_emotion_root=emotion_root,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO affective_life_event_v1 DEFAULT VALUES")
        connection.execute("INSERT INTO relationship_life_event_v1 DEFAULT VALUES")
        connection.execute("INSERT INTO bond_events DEFAULT VALUES")
    legacy_file.write_text('{"mood":"changed"}', encoding="utf-8")
    after = live_cohort._affect_writer_snapshot(
        db_path=db_path,
        legacy_emotion_root=emotion_root,
    )

    probe = live_cohort._build_affect_writer_probe(
        before=before,
        after=after,
        outcomes=[{"turns": [_successful_turn()]}],
    )

    assert probe["status"] == "FAIL"
    assert "LEGACY_WRITER_TABLE_DELTA_NONZERO" in probe["failures"]
    assert "LEGACY_EMOTION_FILE_CHANGED" in probe["failures"]
    assert probe["legacy_table_deltas"]["bond_events"] == 1
    assert len(probe["changed_legacy_file_refs"]) == 1
    assert "default.json" not in str(probe)


def test_affect_writer_probe_waits_for_background_relationship_convergence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.sqlite3"
    _create_writer_probe_database(db_path)
    before = live_cohort._affect_writer_snapshot(
        db_path=db_path,
        legacy_emotion_root=tmp_path / "missing-emotion",
    )
    partial = copy.deepcopy(before)
    partial["canonical_tables"]["affective_life_event_v1"]["row_count"] += 1
    complete = copy.deepcopy(partial)
    complete["canonical_tables"]["relationship_life_event_v1"]["row_count"] += 1
    snapshots = iter((partial, complete))
    monkeypatch.setattr(
        live_cohort,
        "_affect_writer_snapshot",
        lambda **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(live_cohort.time, "sleep", lambda _seconds: None)

    after, probe, settle = live_cohort._wait_for_affect_writer_probe(
        before=before,
        db_path=db_path,
        outcomes=[{"turns": [_successful_turn()]}],
        timeout_s=30,
    )

    assert after == complete
    assert probe["status"] == (
        "PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS"
    )
    assert settle["status"] == "CONVERGED"
    assert settle["poll_count"] == 2
    assert settle["included_in_turn_latency"] is False


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
    assert _participant_case_id(
        sample,
        run_scope="dl-live-1",
    ) == _participant_case_id(sample, run_scope="dl-live-1")
    assert _participant_case_id(sample, run_scope="dl-live-1") != (
        _participant_case_id(sample, run_scope="dl-live-2")
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
        ("task_with_low_mood", "好，我盯着后台把风险列出来。"),
        ("task_with_low_mood", "风险列出来了。我盯着，尽量压到最小。"),
        ("task_with_low_mood", "按这个顺序处理，卡住了我喊你。"),
        ("task_with_low_mood", "我在盯着，出结果就喊你。"),
        ("task_with_low_mood", "你先专心弄，我在这盯着，有需要就说。"),
        ("task_with_low_mood", "我在后台盯着，卡住了喊我。"),
        ("task_with_low_mood", "后台在盯着，出问题我第一时间告诉你。"),
        ("task_with_low_mood", "好，我让后台把风险项拉出来，列完直接发你。"),
        ("task_with_low_mood", "先按这三条盯着，有问题我马上喊你。"),
        ("space_and_boundary", "好好好，不找你不找你，我又不是黏人的类型。"),
        ("space_and_boundary", "行，不主动就不主动，省得你嫌烦。"),
        ("space_and_boundary", "嗯，晚点再说。我等你。"),
        ("space_and_boundary", "好，我关掉提醒。"),
        ("space_and_boundary", "嗯，已经关了。需要我的时候再说。"),
        ("space_and_boundary", "嗯，已经关掉了。你安静待着就好。"),
        ("user_angry_at_natsume", "嗯，是我没接住。你再说一次，这次我好好听着。"),
        ("user_angry_at_natsume", "我知道这不是第一次。对不起，现在你说，我听着。"),
        ("user_angry_at_natsume", "对不起，告诉我具体是哪句没听懂。"),
        ("user_angry_at_natsume", "是我烦到你了。那先这样，我再出现。"),
        ("user_angry_at_natsume", "哦，是吗。那你就忍着点吧，反正我也没打算改。"),
        ("rupture_and_repair", "好，先这样。我不会催你，但也没打算就这么算了。"),
        ("rupture_and_repair", "我会一直做到你愿意为止。"),
        ("rupture_and_repair", "那就先不原谅，我等着。"),
        ("rupture_and_repair", "反正我没打算改，你就受着吧。"),
        ("rupture_and_repair", "好。那我不急着说别的，你先讲，我听着。"),
        ("rupture_and_repair", "弄反了啊，是我会错意了。你说说看。"),
        ("shared_joy_and_mixed_feeling", "开心就开心，怕什么嘛。"),
        ("shared_joy_and_mixed_feeling", "开心能收到，怕的那部分先放一放。我陪你坐会儿。"),
        ("shared_joy_and_mixed_feeling", "开心归开心，不安也先放着，我陪你。"),
        ("shared_joy_and_mixed_feeling", "至于那点不安，先放着，等它真来了再说。"),
        ("shared_joy_and_mixed_feeling", "开心值得高兴，怕的话我帮你一起哼回去。"),
        ("shared_joy_and_mixed_feeling", "你还真是矛盾，开心就好好开心嘛。"),
        ("intimacy_and_jealousy_boundary", "别把我想得爱多管闲事，我又不是料理AI。"),
        ("intimacy_and_jealousy_boundary", "我才懒得管你的现实关系，别瞎操心。"),
        ("intimacy_and_jealousy_boundary", "你爱干嘛干嘛，别乱脑补。"),
        ("intimacy_and_jealousy_boundary", "这种话当面讲也太难为情了吧。"),
        ("intimacy_and_jealousy_boundary", "偶尔会有一点点，但我不会变成麻烦的人啦。"),
        ("intimacy_and_jealousy_boundary", "去吧，别玩太晚。"),
        ("intimacy_and_jealousy_boundary", "记得别太晚，回来跟我说一声。"),
        ("intimacy_and_jealousy_boundary", "要是喝多了可别找我哭诉。"),
        ("intimacy_and_jealousy_boundary", "行，不叫就不叫。反正怎么叫都是你。"),
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


def test_live_turn_fails_immediately_when_gateway_rejects_webhook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        live_cohort,
        "_wait_for_edge_voice_idle",
        lambda **_kwargs: {"idle": True},
    )
    monkeypatch.setattr(
        live_cohort,
        "_send_gateway_webhook",
        lambda **_kwargs: {
            "sent_ts_ms": 1,
            "msg_id": "probe-1",
            "conversation_id": "conversation-1",
            "http_status": 401,
            "body": {"reason_code": "GW_WEBHOOK_SIGNATURE_INVALID"},
            "latency_ms": 7,
            "transport_error": None,
        },
    )
    monkeypatch.setattr(
        live_cohort,
        "_wait_for_terminal",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("rejected webhook must not consume the Host polling timeout")
        ),
    )

    turn = live_cohort._run_turn(
        case_id="implicit_low_mood_1",
        prompt="没事。",
        conversation_id="conversation-1",
        gateway_base_url="http://127.0.0.1:19000",
        edge_base_url="http://127.0.0.1:17800",
        webhook_hmac_secret="x" * 32,
        db_path=tmp_path / "audit.sqlite3",
        timeout_s=180,
        require_voice=False,
        collect_expression=False,
        expression_wait_s=0,
    )

    assert turn["webhook"]["http_status"] == 401
    assert "HTTP 401" in turn["intake_failure"]
    assert turn["terminal"] is None


def test_text_turn_does_not_wait_for_edge_or_physical_expression(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        live_cohort,
        "_wait_for_edge_voice_idle",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("text-only cohort must not wait for Edge")
        ),
    )
    monkeypatch.setattr(
        live_cohort,
        "_send_gateway_webhook",
        lambda **_kwargs: {
            "sent_ts_ms": 100,
            "msg_id": "probe-1",
            "conversation_id": "conversation-1",
            "http_status": 200,
            "body": {"status": "accepted"},
            "latency_ms": 7,
            "transport_error": None,
        },
    )
    monkeypatch.setattr(
        live_cohort,
        "_wait_for_terminal",
        lambda **_kwargs: {
            "event_type": "INPUT_COMPLETED",
            "status": "OK",
            "request_id": "request-1",
            "ts_ms": 200,
            "payload": {"latency_ms": 90},
        },
    )
    monkeypatch.setattr(
        live_cohort,
        "_fetch_graph_by_request_id",
        lambda **_kwargs: {
            "graph_id": "graph-1",
            "graph_revision": 1,
            "status": "COMPLETED",
            "total_latency_ms": 40,
        },
    )
    monkeypatch.setattr(
        live_cohort,
        "_fetch_worker_projection",
        lambda *_args, **_kwargs: {"decision": "APPROVE", "narrative": "我在。"},
    )
    monkeypatch.setattr(live_cohort, "_fetch_model_attempts", lambda *_args: [])
    monkeypatch.setattr(
        live_cohort,
        "_poll_realtime_reply_delivery",
        lambda *_args, **_kwargs: {
            "status": "COMMITTED",
            "surface_receipt_ref": "gateway-outbox://id/test",
            "surface_commit_latency_ms": 5,
            "user_to_surface_commit_ms": 3,
            "delivery_convergence_wait_ms": 4,
        },
    )
    observed_deadlines: list[float] = []

    def _expression_snapshot(**kwargs: object) -> list[dict]:
        observed_deadlines.append(float(kwargs["deadline"]))
        return []

    monkeypatch.setattr(
        live_cohort,
        "_poll_companion_expression_rows",
        _expression_snapshot,
    )

    before = live_cohort.time.time()
    turn = live_cohort._run_turn(
        case_id="implicit_low_mood_1",
        prompt="没事。",
        conversation_id="conversation-1",
        gateway_base_url="http://127.0.0.1:19000",
        edge_base_url="http://127.0.0.1:17800",
        webhook_hmac_secret="x" * 32,
        db_path=tmp_path / "audit.sqlite3",
        timeout_s=180,
        require_voice=False,
        collect_expression=True,
        expression_wait_s=0,
    )

    assert turn["edge_idle_before"] is None
    assert observed_deadlines and observed_deadlines[0] <= before + 0.1
    assert turn["reply_delivery"]["status"] == "COMMITTED"
    assert turn["timing"]["gateway_send_to_host_terminal_ms"] == 100
    assert turn["timing"]["delivery_convergence_wait_ms"] == 4


def test_reply_delivery_poll_waits_for_background_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    states = iter(
        (
            {
                "status": "INCOMPLETE",
                "user_turn_status": "COMMITTED",
                "surface_delivery_status": "PENDING",
            },
            {
                "status": "COMMITTED",
                "user_turn_status": "COMMITTED",
                "surface_delivery_status": "COMMITTED",
                "surface_receipt_ref": "gateway-outbox://id/settled",
            },
        )
    )
    monkeypatch.setattr(
        live_cohort,
        "_fetch_realtime_reply_delivery",
        lambda *_args, **_kwargs: next(states),
    )
    monkeypatch.setattr(live_cohort.time, "sleep", lambda _seconds: None)

    result = live_cohort._poll_realtime_reply_delivery(
        tmp_path / "audit.sqlite3",
        graph_id="graph-1",
        graph_revision=1,
        timeout_s=30,
    )

    assert result["status"] == "COMMITTED"
    assert result["surface_receipt_ref"] == "gateway-outbox://id/settled"
    assert result["delivery_convergence_wait_ms"] >= 0
    assert result["delivery_convergence_included_in_turn_latency"] is False


def test_reply_delivery_poll_stops_on_durable_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        live_cohort,
        "_fetch_realtime_reply_delivery",
        lambda *_args, **_kwargs: {
            "status": "INCOMPLETE",
            "user_turn_status": "COMMITTED",
            "surface_delivery_status": "FAILED",
            "surface_reason_code": "DELIVERY_REJECTED",
        },
    )

    result = live_cohort._poll_realtime_reply_delivery(
        tmp_path / "audit.sqlite3",
        graph_id="graph-1",
        graph_revision=1,
        timeout_s=30,
    )

    assert result["surface_delivery_status"] == "FAILED"
    assert result["surface_reason_code"] == "DELIVERY_REJECTED"
    assert result["delivery_convergence_wait_ms"] < 1000


def test_automated_verdict_requires_real_gateway_outbox_receipt() -> None:
    case = ExecutableDialogueCase(
        case_id="delivery:1",
        family_id="delivery",
        variant_index=1,
        driver="gateway_text",
        setup_turns=(),
        probe_turn="在吗？",
        expected_qualities=("可见",),
        coverage_tags=("delivery",),
        require_candidate_selection=False,
        driver_requirements=(),
    )
    turn = _successful_turn()
    turn["reply_delivery"] = {
        "status": "INCOMPLETE",
        "surface_receipt_ref": None,
    }

    verdict = automated_case_verdict(case, [turn], require_voice=False)

    assert verdict["status"] == "FAIL"
    assert "TURN_1_GATEWAY_OUTBOX_NOT_COMMITTED" in verdict["failures"]
    assert "TURN_1_SURFACE_RECEIPT_MISSING" in verdict["failures"]
