from __future__ import annotations

from host_brain.app.reply_envelope import (
    FINAL_REPLY,
    NO_REPLY,
    ReplyEnvelope,
    can_cache,
    can_persist,
    can_send,
    envelope_from_commit,
)


def test_final_reply_envelope_is_the_only_sendable_persistable_cacheable_kind() -> None:
    final = ReplyEnvelope.final_reply(text="哥哥，先接住你这一下。", source="surface")
    no_reply = ReplyEnvelope.no_reply(reason_code="LLM_UNAVAILABLE")
    ack = ReplyEnvelope(kind="transport_ack", text="收到", sendable=True, persistable=True, cacheable=True)
    degraded_final = ReplyEnvelope(kind="final_reply", text="兜底", sendable=True, persistable=True, cacheable=True, degraded=True)

    assert final.kind == FINAL_REPLY
    assert can_send(final) and can_persist(final) and can_cache(final)
    assert no_reply.kind == NO_REPLY
    assert not can_send(no_reply) and not can_persist(no_reply) and not can_cache(no_reply)
    assert not can_send(ack) and not can_persist(ack) and not can_cache(ack)
    assert not can_send(degraded_final) and not can_persist(degraded_final) and not can_cache(degraded_final)


def test_envelope_from_commit_rejects_empty_or_degraded_approved_text() -> None:
    approved = envelope_from_commit(decision="APPROVE", narrative="接到了。", source="full_model")
    empty = envelope_from_commit(decision="APPROVE", narrative="", reason_code="EMPTY")
    rejected = envelope_from_commit(decision="REJECT", narrative="不该发", reason_code="BLOCKED")
    degraded = envelope_from_commit(decision="APPROVE", narrative="兜底", degraded=True, reason_code="FALLBACK")

    assert approved.kind == FINAL_REPLY and can_send(approved)
    assert empty.kind == NO_REPLY and not can_send(empty)
    assert rejected.kind == NO_REPLY and not can_send(rejected)
    assert degraded.kind == NO_REPLY and not can_send(degraded)
