from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from host_brain.app.emotion_context import (
    HostEmotionRuntime,
    normalize_chat_emotion_context,
    resolve_chat_emotion_context,
)
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.reply_form_decider import MsgType, ResponseFormDecider


ROOT = Path(__file__).resolve().parents[2]


def _request(message: str) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        emotion_context={},
        mood_state=None,
        emotion_tag=None,
        emotion_range_hint=None,
        emotion_intensity=None,
    )


def test_host_emotion_runtime_isolates_overlapping_app_roots() -> None:
    first = HostEmotionRuntime(clock_ms=lambda: 1000, hour_provider=lambda: 12)
    second = HostEmotionRuntime(clock_ms=lambda: 1000, hour_provider=lambda: 12)

    first_result = resolve_chat_emotion_context(
        request_body=_request("今天真的很开心"),
        runtime=first,
    )
    second_result = resolve_chat_emotion_context(
        request_body=_request("今天真的很难受"),
        runtime=second,
    )

    assert first_result["emotion_tag"] == "HAPPY"
    assert second_result["emotion_tag"] == "SAD"
    assert first.user_emotion_profile() is not second.user_emotion_profile()
    assert first.user_emotion_profile().to_summary()["top_emotions"][0]["tag"] == "HAPPY"
    assert second.user_emotion_profile().to_summary()["top_emotions"][0]["tag"] == "SAD"


def test_replay_normalization_does_not_advance_host_affect_state() -> None:
    runtime = HostEmotionRuntime(clock_ms=lambda: 1000, hour_provider=lambda: 12)
    accepted = resolve_chat_emotion_context(
        request_body=_request("我有点焦虑"),
        runtime=runtime,
    )
    before = runtime.user_emotion_profile().to_summary()

    for _ in range(3):
        replayed = normalize_chat_emotion_context(accepted)
        assert replayed["emotion_tag"] == accepted["emotion_tag"]

    assert runtime.user_emotion_profile().to_summary() == before


def test_reply_form_decider_is_owned_by_each_input_composition() -> None:
    first_ports = build_input_runtime_application_ports()
    second_ports = build_input_runtime_application_ports()

    assert first_ports.get_reply_form_decider() is first_ports.get_reply_form_decider()
    assert first_ports.get_reply_form_decider() is not second_ports.get_reply_form_decider()


def test_reply_form_voice_budget_is_atomic_under_concurrent_requests() -> None:
    decider = ResponseFormDecider(
        voice_enabled=True,
        probability_scale=1.0,
        daily_voice_limit=100,
        min_voice_interval_ms=0,
    )

    def decide_once(_index: int) -> None:
        decision = decider.decide(
            user_id="same-user",
            user_msg_type=MsgType.TEXT,
            narrative_zh="抱抱，我在呢。",
            emotion_tag="COMFORT",
            bond_stage="CLOSE_FRIEND",
            user_text="今天有点累",
        )
        assert decision.secondary is MsgType.VOICE

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(decide_once, range(50)))

    assert decider.get_voice_stats("same-user")["count"] == 50


def test_production_affect_modules_have_no_mutable_singleton_authority() -> None:
    emotion_source = (
        ROOT / "host_brain" / "app" / "emotion_context.py"
    ).read_text(encoding="utf-8-sig")
    reply_source = (
        ROOT / "host_brain" / "app" / "reply_form_decider.py"
    ).read_text(encoding="utf-8-sig")
    host_root_source = (
        ROOT / "host_brain" / "composition" / "host_application.py"
    ).read_text(encoding="utf-8-sig")

    for symbol in (
        "_global_emotion_timeline",
        "_global_emotion_vector",
        "_global_prev_emotion_tag",
        "_user_emotion_profile",
        "get_user_emotion_profile",
        "update_user_emotion_profile",
    ):
        assert symbol not in emotion_source
    assert "\n_decider" not in reply_source
    assert "\ndef get_reply_form_decider" not in reply_source
    assert "\ndef get_response_form_decider" not in reply_source
    assert "host_brain.app.emotion_context" not in host_root_source
    assert "emotion_runtime_factory" not in host_root_source
    assert "HostEmotionRuntime" not in host_root_source
    assert "user_emotion_profile" not in host_root_source
