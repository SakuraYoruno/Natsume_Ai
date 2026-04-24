from __future__ import annotations

from host_brain.app.agent_runtime_shared import _resolve_persisted_fallback_flag


def test_resolve_persisted_fallback_flag_prefers_explicit_reply_flag() -> None:
    assert _resolve_persisted_fallback_flag(
        trace_stage_meta={"reply_used_fallback": False, "fallback_used": True, "degrade_code": "SOMETHING"},
        gov_decision={"reason_code": "FALLBACK_ROUTE"},
        narrative="正常回复",
        provider_name="gemini",
        model_id="gemini-2.5-pro",
    ) is False

    assert _resolve_persisted_fallback_flag(
        trace_stage_meta={"reply_used_fallback": True},
        gov_decision={},
        narrative="保命回复",
        provider_name="host",
        model_id="fallback",
    ) is True


def test_resolve_persisted_fallback_flag_uses_explicit_trace_or_detects_degrade() -> None:
    assert _resolve_persisted_fallback_flag(
        trace_stage_meta={"fallback_used": False},
        gov_decision={"reason_code": "FALLBACK_ROUTE"},
        narrative="正常回复",
        provider_name="gemini",
        model_id="gemini-2.5-pro",
    ) is False

    assert _resolve_persisted_fallback_flag(
        trace_stage_meta={"degrade_code": "chat_fallback"},
        gov_decision={},
        narrative="普通文本",
        provider_name="gemini",
        model_id="gemini-2.5-pro",
    ) is True
