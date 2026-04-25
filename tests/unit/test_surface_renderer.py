from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from host_brain.persona import surface_renderer as sr
from host_brain.persona.surface_renderer import SurfaceRenderer, SurfaceRequest


def _plan(**kwargs):
    payload = {
        "primary_topic": "",
        "response_form_reason": "",
        "tactic_family": "",
        "tactic_variant": "",
        "reply_contract": "",
        "multimodal": {},
    }
    payload.update(kwargs)
    return SimpleNamespace(**payload)


def _validation(*, safe: bool = True, valid: bool = True, missing=(), reasons=(), quality: float = 1.0):
    return SimpleNamespace(
        safe_to_emit=safe,
        is_valid=valid,
        missing_actions=tuple(missing),
        reason_codes=tuple(reasons),
        quality_score=quality,
        as_dict=lambda: {
            "safe_to_emit": safe,
            "is_valid": valid,
            "missing_actions": list(missing),
            "reason_codes": list(reasons),
            "quality_score": quality,
        },
    )


def _request(plan, *, trace=None, validate=None, finalize=None):
    return SurfaceRequest(
        request_id="req-surface",
        session_id="sess-surface",
        user_message="?",
        plan=plan,
        previous_assistant_text="唔。",
        provider_name="gemini",
        model_id="gemini-2.5-flash-lite",
        trace_meta=trace if trace is not None else {},
        validate_reply=validate or (lambda text: _validation()),
        finalize_text=finalize or (lambda text: text.strip()),
    )


def test_surface_renderer_private_helpers_cover_candidate_and_validation_edges(monkeypatch) -> None:
    assert sr._trace_text_sample("x" * 300).endswith("...")
    assert sr._extract_candidates("not-a-map") == []
    assert sr._extract_candidates({"candidates": ["甲", "甲", "", "乙"], "reply": "乙"}) == ["甲", "乙"]

    assert sr._assistant_followup_semantic_seed(
        _plan(reply_contract="上一条助手回复前接的用户话是“刚才讲了什么”。上一条助手回复是“唔。”。")
    ).startswith("上一条只是短语气词")
    assert "自然接回前文" in sr._assistant_followup_semantic_seed(
        _plan(reply_contract="上一条助手回复前接的用户话是“这个是什么意思”。上一条助手回复是“唔。”。")
    )
    assert "自然补清" in sr._assistant_followup_semantic_seed(
        _plan(reply_contract="上一条助手回复前接的用户话是“继续”。上一条助手回复是“知道。”。")
    )

    assert not sr._validation_is_acceptable(None)
    assert not sr._validation_is_acceptable(_validation(safe=False))
    assert not sr._validation_is_acceptable(_validation(missing=("x",)))
    assert sr._validation_is_acceptable(_validation(valid=True))
    assert sr._validation_is_acceptable(_validation(valid=False, reasons=("meta_contract_echo",), quality=0.95))
    assert not sr._validation_is_acceptable(_validation(valid=False, reasons=("other",), quality=0.95))

    assert not sr._surface_validation_is_acceptable(
        _validation(safe=False),
        candidate_text="",
        followup_context="知道哪个秘密",
        needs_specific_reference=True,
        is_repeat_request=False,
    )
    assert not sr._surface_validation_is_acceptable(
        _validation(valid=False, missing=("other",), reasons=("previous_reference_missing",)),
        candidate_text="哪个秘密",
        followup_context="知道哪个秘密",
        needs_specific_reference=True,
        is_repeat_request=False,
    )
    assert not sr._surface_validation_is_acceptable(
        _validation(valid=False, missing=("previous_reference",), reasons=("other",)),
        candidate_text="哪个秘密",
        followup_context="知道哪个秘密",
        needs_specific_reference=True,
        is_repeat_request=False,
    )
    assert sr._surface_validation_is_acceptable(
        _validation(valid=False, missing=("previous_reference",), reasons=("previous_reference_missing",)),
        candidate_text="哥哥，你说哪个秘密？",
        followup_context="知道哪个秘密",
        needs_specific_reference=True,
        is_repeat_request=False,
    )
    assert sr._surface_validation_is_acceptable(
        _validation(valid=False, missing=("previous_reference",), reasons=("previous_reference_missing",)),
        candidate_text="刚才只是唔了一下。",
        followup_context="刚才说了什么",
        needs_specific_reference=False,
        is_repeat_request=True,
    )
    assert sr._surface_validation_is_acceptable(
        _validation(valid=False, missing=("previous_reference",), reasons=("previous_reference_missing",)),
        candidate_text="接上了。",
        followup_context="继续",
        needs_specific_reference=False,
        is_repeat_request=False,
    )

    monkeypatch.setattr(
        "host_brain.app.context.soul_runtime.get_compiled_soul",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert "Soul.md" in sr._compiled_soul_text()
    assert sr._compiled_identity_names() == ("新海天",)


def test_surface_renderer_candidate_acceptance_guards() -> None:
    renderer = SurfaceRenderer()

    assert renderer._accept_assistant_candidate(
        "哥哥，你问这个是什么意思呀？",
        _validation(),
        followup_context="这个是什么意思",
        followup_source="唔。",
        needs_specific_reference=False,
        is_repeat_request=False,
    )[0] is False
    assert renderer._accept_assistant_candidate(
        "哥哥，具体指哪件？",
        _validation(),
        followup_context="知道哪个新秘密",
        followup_source="唔。",
        needs_specific_reference=True,
        is_repeat_request=False,
    )[1] == "SURFACE_UNDERSPECIFIED_CONTEXT_NOT_RESOLVED"
    assert renderer._accept_assistant_candidate(
        "哥哥，那边啦。",
        _validation(),
        followup_context="这个是什么意思",
        followup_source="唔。",
        needs_specific_reference=False,
        is_repeat_request=False,
    )[1] == "SURFACE_FILLER_MEANING_NOT_RESOLVED"
    assert renderer._accept_assistant_candidate(
        "刚才只是唔了一下。",
        _validation(safe=False),
        followup_context="刚才说了什么",
        followup_source="唔。",
        needs_specific_reference=False,
        is_repeat_request=True,
    )[1] == "SURFACE_VALIDATION_FAILED"
    assert renderer._accept_short_social_candidate("哥哥，你真正想问什么？", _validation(), topic="greeting")[1] == "SHORT_SURFACE_TEMPLATE_SHAPE"
    assert renderer._accept_short_social_candidate("哥哥，来啦。", _validation(safe=False), topic="greeting")[1] == "SHORT_SURFACE_VALIDATION_UNSAFE"
    assert renderer._accept_image_candidate("哥哥今天怎么这么安静。", _validation(), multimodal={"summary_for_chat": "猫耳女孩"})[1] == "IMAGE_SURFACE_EMOTION_NOT_USED"
    assert renderer._accept_image_candidate("哥哥，你看我这小猫耳朵是不是超可爱呀！", _validation(), multimodal={"summary_for_chat": "猫耳女孩"})[1] == "IMAGE_SURFACE_TEMPLATE_SHAPE"
    assert renderer._accept_image_candidate("哥哥，你是不是觉得这张图里的猫猫很可爱呀？", _validation(), multimodal={"summary_for_chat": "猫耳女孩"})[1] == "IMAGE_SURFACE_TEMPLATE_SHAPE"
    assert renderer._accept_image_candidate("被你这么一晃，我有点招架不住。", _validation(safe=False), multimodal={"summary_for_chat": "猫耳女孩"})[1] == "IMAGE_SURFACE_VALIDATION_FAILED"
    assert renderer._accept_image_candidate(
        "被你这么一晃，我有点招架不住。",
        _validation(valid=False, reasons=("image_social_overplayed",), quality=0.25),
        multimodal={"summary_for_chat": "猫耳女孩"},
    )[1] == "IMAGE_SURFACE_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_surface_renderer_skips_unknown_topic() -> None:
    result = await SurfaceRenderer().render(_request(_plan(primary_topic="literal_description")))

    assert result.text == ""
    assert result.skipped_reason == "topic_not_surface"


@pytest.mark.asyncio
async def test_surface_renderer_assistant_followup_tactic_skip() -> None:
    result = await SurfaceRenderer().render(
        _request(_plan(primary_topic="assistant_reply_followup", tactic_family="other"))
    )

    assert result.text == ""
    assert result.skipped_reason == "assistant_followup_tactic_not_surface"


@pytest.mark.asyncio
async def test_surface_renderer_call_surface_model_uses_pool_defaults(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_pool(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return SimpleNamespace(content='{"candidates":["好"]}', final_provider="gemini", final_model="gemini-2.5-flash-lite")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_pool)

    result = await SurfaceRenderer()._call_surface_model(
        _request(_plan()),
        system_prompt="sys",
        user_prompt="user",
        timeout_ms=1234,
        tier="D",
        temperature=0.6,
    )

    assert result.content
    assert captured["scene_type"] == "CHAT_SURFACE"
    assert captured["model_id"] == "gemini-2.5-flash-lite"
    assert captured["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]


@pytest.mark.asyncio
async def test_surface_renderer_assistant_followup_renders_repeat_request(monkeypatch) -> None:
    async def _fake_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        assert "上一条助手短回复" in kwargs["user_prompt"]
        assert "刚才到底说了什么" in kwargs["user_prompt"]
        return SimpleNamespace(
            content=json.dumps({"candidates": ["刚才只唔了一下啦，哥哥，你说的是哪句？"]}, ensure_ascii=False),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=123,
        )

    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _fake_call)
    trace: dict[str, object] = {}

    result = await SurfaceRenderer().render(
        _request(
            _plan(
                primary_topic="assistant_reply_followup",
                tactic_family="explain_previous_reply",
                reply_contract="上一条助手回复前接的用户话是“刚才说了什么”。上一条助手回复是“唔。”。",
            ),
            trace=trace,
        )
    )

    assert result.text == "刚才只唔了一下啦，哥哥，你说的是哪句？"
    assert dict(trace["assistant_followup_surface_render"])["status"] == "OK"


@pytest.mark.asyncio
async def test_surface_renderer_identity_rejects_missing_name_then_accepts(monkeypatch) -> None:
    async def _fake_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        assert "低风险短聊天" in kwargs["system_prompt"]
        return SimpleNamespace(
            content=json.dumps({"candidates": ["哥哥，又问这个呀。", "哥哥，我是新海天呀。"]}, ensure_ascii=False),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=321,
        )

    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _fake_call)
    trace: dict[str, object] = {}

    result = await SurfaceRenderer().render(
        _request(_plan(primary_topic="identity", reply_contract="认下身份。"), trace=trace)
    )

    assert result.text == "哥哥，我是新海天呀。"
    assert result.degraded is False
    meta = dict(trace["identity_surface_render"])  # type: ignore[index]
    assert meta["status"] == "OK"
    assert meta["rejected_attempts"][0]["reason_code"] == "IDENTITY_SURFACE_NAME_MISSING"


@pytest.mark.asyncio
async def test_surface_renderer_image_observation_empty_falls_through() -> None:
    trace: dict[str, object] = {}

    result = await SurfaceRenderer().render(
        _request(
            _plan(primary_topic="emoji_social_reply", response_form_reason="image_social_reply", multimodal={}),
            trace=trace,
        )
    )

    assert result.text == ""
    assert result.degraded is False
    assert result.skipped_reason == "image_social_observation_empty"
    meta = dict(trace["image_social_surface_render"])
    assert meta["affected_output"] is False
    assert meta["reason_code"] == "image_social_observation_empty"
    assert "surface_render_fallback" not in trace


@pytest.mark.asyncio
async def test_surface_renderer_image_rejects_template_and_accepts_emotion_response(monkeypatch) -> None:
    async def _fake_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        assert "表情包观察" in kwargs["user_prompt"]
        return SimpleNamespace(
            content=json.dumps(
                {"candidates": ["哥哥，突然这么可爱，是想让我哄你吗？", "好啦好啦，别拿这种眼神晃我，我会心软的。"]},
                ensure_ascii=False,
            ),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=222,
        )

    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _fake_call)
    trace: dict[str, object] = {}

    result = await SurfaceRenderer().render(
        _request(
            _plan(
                primary_topic="emoji_social_reply",
                response_form_reason="image_social_reply",
                tactic_family="cute_image_ack",
                multimodal={"summary_for_chat": "可爱、卖萌；猫耳女孩特写。", "core_observation": "猫耳女孩。"},
            ),
            trace=trace,
        )
    )

    assert result.text == "好啦好啦，别拿这种眼神晃我，我会心软的。"
    assert dict(trace["image_social_surface_render"])["rejected_attempts"][0]["reason_code"] == "IMAGE_SURFACE_TEMPLATE_SHAPE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "expected_hint", "candidate"),
    [
        ("soft_emotion_ack", "有点蔫或委屈", "好啦，别用这种委屈劲晃我，我会心软的。"),
        ("tease_image_ack", "小表情来戳你", "哥哥又拿这股劲逗我，我有点招架不住。"),
    ],
)
async def test_surface_renderer_image_prompt_uses_emotion_action_hints(monkeypatch, family, expected_hint, candidate) -> None:
    async def _fake_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        assert expected_hint in kwargs["user_prompt"]
        assert "不要描述图片本身" in kwargs["user_prompt"]
        return SimpleNamespace(
            content=json.dumps({"candidates": [candidate]}, ensure_ascii=False),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=222,
        )

    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _fake_call)

    result = await SurfaceRenderer().render(
        _request(
            _plan(
                primary_topic="emoji_social_reply",
                response_form_reason="image_social_reply",
                tactic_family=family,
                multimodal={"summary_for_chat": "表情包表达情绪。", "core_observation": "表情特写。"},
            )
        )
    )

    assert result.text == candidate


@pytest.mark.asyncio
async def test_surface_renderer_image_rejected_candidates_fall_through_to_full_model(monkeypatch) -> None:
    async def _fake_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        return SimpleNamespace(
            content=json.dumps(
                {
                    "candidates": [
                        "哥哥，人家今天也很可爱哦！",
                        "哥哥，今天我也想和你一起玩！",
                    ]
                },
                ensure_ascii=False,
            ),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=222,
        )

    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _fake_call)
    trace: dict[str, object] = {}

    result = await SurfaceRenderer().render(
        _request(
            _plan(
                primary_topic="emoji_social_reply",
                response_form_reason="image_social_reply",
                tactic_family="cute_image_ack",
                multimodal={"summary_for_chat": "可爱、卖萌；猫耳女孩特写。", "core_observation": "猫耳女孩。"},
            ),
            trace=trace,
        )
    )

    assert result.text == ""
    assert result.skipped_reason == "image_social_surface_unavailable"
    assert result.degraded is False
    meta = dict(trace["image_social_surface_render"])
    assert meta["status"] == "REJECTED"
    assert meta["affected_output"] is False
    assert [item["reason_code"] for item in meta["rejected_attempts"]] == [
        "IMAGE_SURFACE_TEMPLATE_SHAPE",
        "IMAGE_SURFACE_EMOTION_NOT_USED",
    ]
    assert "image_social_surface_render_fallback" not in trace


@pytest.mark.asyncio
async def test_surface_renderer_image_timeout_falls_through_without_fallback_text(monkeypatch) -> None:
    async def _slow_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        await asyncio.sleep(2)
        return SimpleNamespace(content='{"candidates":["迟到了"]}', final_provider="gemini", final_model="slow", total_latency_ms=2000)

    monkeypatch.setenv("HOST_IMAGE_SOCIAL_SURFACE_TIMEOUT_MS", "1")
    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _slow_call)
    trace: dict[str, object] = {}

    result = await SurfaceRenderer().render(
        _request(
            _plan(
                primary_topic="emoji_social_reply",
                response_form_reason="image_social_reply",
                tactic_family="cute_image_ack",
                multimodal={"summary_for_chat": "可爱、卖萌；猫耳女孩特写。"},
            ),
            trace=trace,
        )
    )

    assert result.text == ""
    assert result.degraded is False
    assert result.skipped_reason == "image_social_surface_unavailable"
    assert dict(trace["image_social_surface_render"])["reason_code"] == "SURFACE_HARD_TIMEOUT"
    assert "image_social_surface_render_fallback" not in trace


@pytest.mark.asyncio
async def test_surface_renderer_json_invalid_and_empty_candidates_fallback(monkeypatch) -> None:
    calls = 0

    async def _fake_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(content="{", final_provider="gemini", final_model="m", total_latency_ms=1)
        return SimpleNamespace(content=json.dumps({"candidates": []}), final_provider="gemini", final_model="m", total_latency_ms=2)

    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _fake_call)
    first_trace: dict[str, object] = {}
    second_trace: dict[str, object] = {}
    plan = _plan(primary_topic="greeting")

    first = await SurfaceRenderer().render(_request(plan, trace=first_trace))
    second = await SurfaceRenderer().render(_request(plan, trace=second_trace))

    assert first.degraded is False
    assert second.degraded is False
    assert dict(first_trace["phatic_surface_render"])["reason_code"] == "SURFACE_JSON_INVALID"
    assert dict(second_trace["phatic_surface_render"])["reason_code"] == "SURFACE_EMPTY"


@pytest.mark.asyncio
async def test_surface_renderer_timeout_and_exception_fallback(monkeypatch) -> None:
    async def _slow_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        await asyncio.sleep(2)
        return SimpleNamespace(content="{}", final_provider="gemini", final_model="m", total_latency_ms=50)

    monkeypatch.setenv("HOST_SHORT_SOCIAL_SURFACE_TIMEOUT_MS", "1")
    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _slow_call)
    timeout_trace: dict[str, object] = {}
    timeout_result = await SurfaceRenderer().render(_request(_plan(primary_topic="greeting"), trace=timeout_trace))

    async def _broken_call(self, request, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr(SurfaceRenderer, "_call_surface_model", _broken_call)
    failed_trace: dict[str, object] = {}
    failed_result = await SurfaceRenderer().render(_request(_plan(primary_topic="greeting"), trace=failed_trace))

    assert timeout_result.degraded is False
    assert failed_result.degraded is False
    assert dict(timeout_trace["phatic_surface_render"])["status"] == "TIMEOUT"
    assert dict(failed_trace["phatic_surface_render"])["status"] == "FAILED"
