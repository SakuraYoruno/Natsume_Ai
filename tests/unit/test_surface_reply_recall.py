from __future__ import annotations

from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.isolated_surface_harness import build_isolated_surface_messages
from host_brain.persona.reply_surface_memory import build_reply_surface_memory_key
from host_brain.persona.surface_reply_recall import (
    SURFACE_REPLY_RECALL_EMPTY,
    SURFACE_REPLY_RECALL_FAILED,
    SURFACE_REPLY_RECALL_SKIPPED,
    ReplySurfaceRecallPacket,
    build_reply_surface_recall_packet,
    build_surface_input_with_reply_surface_recall,
    plan_reply_surface_recall,
    recall_reply_surface_for_context,
    reply_surface_recall_packet_from_mapping,
)


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


class _MemoryStore:
    def __init__(self, rows: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.rows = dict(rows or {})
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def sample(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("boom")
        return self.rows.get(str(kwargs.get("key") or ""), "")


def test_plan_reply_surface_recall_builds_legacy_and_current_chain_keys() -> None:
    context = ThreeLayerContinuityContext(
        current_user_message="早",
        task_mode_hint="daily_good_morning",
        response_move_hint="daily_good_morning",
    )

    plan = plan_reply_surface_recall(context, soul_id="soul", render_target_lang="zh")

    assert plan.should_recall
    assert plan.primary_topic == "greeting"
    assert plan.tactic_family == "phatic_wave"
    assert len(plan.keys) == 2
    assert plan.keys[-1] == build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="phatic_greeting",
        render_target_lang="zh",
    )


def test_reply_surface_recall_packet_compresses_sample_without_leaking_raw_reply() -> None:
    plan = plan_reply_surface_recall(
        ThreeLayerContinuityContext(current_user_message="在吗", task_mode_hint="presence"),
        soul_id="soul",
    )
    sample = "尼尼，我在呀。"

    packet = build_reply_surface_recall_packet(sample_text=sample, plan=plan, key=plan.keys[0])
    payload_text = str(packet.as_dict())

    assert isinstance(packet, ReplySurfaceRecallPacket)
    assert packet.style_anchor
    assert "短句" in "\n".join(packet.style_anchor)
    assert "称呼必须服从 Soul" in "\n".join(packet.style_anchor)
    assert "不要照抄历史表层回复" in packet.must_not_do
    assert sample not in payload_text
    assert packet.sample_digest


def test_recall_reply_surface_for_context_samples_store_and_surface_gets_only_anchors() -> None:
    context = ThreeLayerContinuityContext(
        current_user_message="早",
        task_mode_hint="daily_good_morning",
        response_move_hint="daily_good_morning",
    )
    plan = plan_reply_surface_recall(context, soul_id="soul")
    sample = "尼尼，早呀。"
    store = _MemoryStore({plan.keys[-1]: sample})

    packet = recall_reply_surface_for_context(memory_store=store, context=context, soul_id="soul")
    request = build_surface_input_with_reply_surface_recall(
        context,
        packet=packet,
        soul_md=SOUL,
        case_id="reply_surface_recall",
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert packet.style_anchor
    assert len(store.calls) == 2
    assert "表层回复记忆压缩" in request.control.style_anchor
    assert "不要照抄历史表层回复" in request.control.must_not_do
    assert "历史表层风格" in rendered
    assert sample not in rendered
    assert "尼尼，早呀" not in rendered


def test_recall_reply_surface_for_context_skips_unsupported_task_without_store_call() -> None:
    store = _MemoryStore()

    packet = recall_reply_surface_for_context(
        memory_store=store,
        context=ThreeLayerContinuityContext(
            current_user_message="这个报错怎么修",
            task_mode_hint="next_step",
        ),
        soul_id="soul",
    )

    assert packet.reason_code == SURFACE_REPLY_RECALL_SKIPPED
    assert store.calls == []


def test_recall_reply_surface_for_context_handles_empty_and_failure() -> None:
    context = ThreeLayerContinuityContext(
        current_user_message="嗯",
        task_mode_hint="short_ack",
    )
    empty = recall_reply_surface_for_context(memory_store=_MemoryStore(), context=context, soul_id="soul")
    failed = recall_reply_surface_for_context(memory_store=_MemoryStore(fail=True), context=context, soul_id="soul")

    assert empty.reason_code == SURFACE_REPLY_RECALL_EMPTY
    assert failed.reason_code == SURFACE_REPLY_RECALL_FAILED
    assert failed.degraded


def test_reply_surface_recall_packet_from_mapping_is_boundary_shape() -> None:
    packet = reply_surface_recall_packet_from_mapping(
        {
            "style_anchor": ["历史表层风格：短句。"],
            "must_not_do": ["不要照抄历史表层回复"],
            "sample_digest": "abc123",
            "confidence": 2,
            "raw_sample": "这句不应该被接收",
            "trace": {"raw_sample": "这句不应该被接收", "sample_chars": 9},
        }
    )

    assert packet.style_anchor == ("历史表层风格：短句。",)
    assert packet.confidence == 1.0
    assert "raw_sample" not in packet.as_dict()
    assert "这句不应该被接收" not in str(packet.as_dict())
    assert packet.trace == {"sample_chars": 9}
