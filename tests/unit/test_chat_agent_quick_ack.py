from __future__ import annotations

from host_brain.app.chat_agent import (
    _should_schedule_commit_tts_fallback,
    _should_buffer_stream_output,
    render_quick_gateway_ack_text,
    render_quick_gateway_progress_text,
    render_quick_gateway_realign_text,
)
from tests.text_quality_assertions import assert_natsume_style


def test_render_quick_gateway_ack_prefers_close_stage_template(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_agent._load_template_mapping",
        lambda _template_id: {
            "quick_ack_close": "好，我知道了，马上给你处理好。",
            "quick_ack_default": "好的，稍等下，我马上处理。",
        },
    )

    text = render_quick_gateway_ack_text(
        user_message="请帮我查一下今天的安排",
        bond_stage="INTIMATE",
        emotion_tag="HAPPY",
        sender_name="测试用户",
    )

    assert text == "好，我知道了，马上给你处理好。"


def test_render_quick_gateway_ack_low_energy_has_higher_priority(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_agent._load_template_mapping",
        lambda _template_id: {
            "quick_ack_low_energy": "我在，给我一点点时间，我马上处理。",
            "quick_ack_close": "close-template",
            "quick_ack_default": "default-template",
        },
    )

    text = render_quick_gateway_ack_text(
        user_message="请执行这个任务",
        bond_stage="INTIMATE",
        emotion_tag="SAD",
        sender_name="测试用户",
    )

    assert text == "我在，给我一点点时间，我马上处理。"


def test_render_quick_gateway_ack_falls_back_and_clamps_length(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_agent._load_template_mapping",
        lambda _template_id: {
            "quick_ack_default": (
                "这是一个非常非常长的默认回复文本，用于测试长度限制是否能够生效并自动进行截断处理，"
                "避免发送过长消息，并确保在超出限制时追加省略号。"
            ),
        },
    )

    text = render_quick_gateway_ack_text(
        user_message="请帮我执行",
        bond_stage="FRIEND",
        emotion_tag="NEUTRAL",
        sender_name="测试用户",
    )

    assert len(text) <= 60
    assert text.endswith("...")


def test_render_quick_gateway_ack_prefers_open_app_template(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_agent._load_template_mapping",
        lambda _template_id: {
            "quick_ack_open_app": "好，我替你打开。",
            "quick_ack_default": "默认回复。",
        },
    )

    text = render_quick_gateway_ack_text(
        user_message="可以帮我打开本地的酷狗音乐吗",
        bond_stage="FRIEND",
        emotion_tag="NEUTRAL",
    )

    assert text == "好，我替你打开。"


def test_render_quick_gateway_progress_running_template(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_agent._load_template_mapping",
        lambda _template_id: {
            "quick_progress_running": "正在处理 {target_id}，已进行 {elapsed}。当前进展：{progress_message}",
            "default_step_target": "任务",
        },
    )

    text = render_quick_gateway_progress_text(
        status="RUNNING",
        target_id="session_summary",
        progress_message="已完成检索，正在组织结果",
        elapsed_ms=12_300,
    )

    assert "session_summary" in text
    assert "已完成检索" in text
    assert "12s" in text


def test_render_quick_gateway_progress_missing_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_agent._load_template_mapping",
        lambda _template_id: {
            "quick_progress_missing": "我这边暂时没有可汇报的任务进度。",
        },
    )

    text = render_quick_gateway_progress_text(status="NONE")

    assert text == "我这边暂时没有可汇报的任务进度。"


def test_render_quick_gateway_realign_notice(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_agent._load_template_mapping",
        lambda _template_id: {
            "quick_realign_notice": "我看到了，刚才执行方向可能有点偏，我马上重新对齐你的需求再给你结果。",
        },
    )

    text = render_quick_gateway_realign_text(sender_name="测试用户")

    assert "执行方向可能有点偏" in text


def test_render_quick_gateway_ack_does_not_overpromise_for_non_app_prefix() -> None:
    assert render_quick_gateway_ack_text(user_message="运行测试") != "好，我替你打开。"
    assert render_quick_gateway_ack_text(user_message="打开空调") != "好，我替你打开。"


def test_should_buffer_stream_output_respects_social_validation_policy() -> None:
    assert _should_buffer_stream_output(
        emotion_context={"emotion_tag": "CALM", "turn_count": 1},
        bond_summary={"stage": "FRIEND", "intent": "chat"},
        render_policy={
            "primary_topic": "assistant_reply_followup",
            "requires_validation": True,
            "force_buffering": False,
        },
    ) is True
