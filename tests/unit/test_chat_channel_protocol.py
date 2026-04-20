"""ChatChannel Protocol 契约单测（OPT-8）.

覆盖：
  * T-OPT8-01：``ChannelCapabilities`` 能构造且 ``text_limit/voice_limit`` 对 0/负数返回 None。
  * T-OPT8-02：``OutboundResponse.degrade_voice_to_text`` 正确脱离语音。
  * T-OPT8-03：``OutboundResponse.chunk_text`` 按 limit 切分；limit<=0 返回单元素。
  * T-OPT8-04：``adapt_response_to_capabilities`` 能按 capability 降级 + 切分组合。
  * T-OPT8-05：一个最小结构体（非继承）可通过 ``isinstance`` 满足 Protocol。

测试不依赖任何具体平台 adapter，完全静态验证契约。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from edge_client.channels.base import (
    ChannelCapabilities,
    ChannelHealth,
    ChatChannel,
    InboundEvent,
    OutboundResponse,
    adapt_response_to_capabilities,
)
from edge_client.channels.input_reporter import InputSourceType


def _caps(**overrides: object) -> ChannelCapabilities:
    base = {
        "platform_id": InputSourceType.WECHAT,
        "supports_voice": False,
        "supports_image": False,
        "supports_file": False,
        "supports_markdown": False,
        "max_text_length": 0,
        "max_voice_seconds": 0,
    }
    base.update(overrides)
    return ChannelCapabilities(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T-OPT8-01：ChannelCapabilities 基础行为
# ---------------------------------------------------------------------------


def test_t_opt8_01_capabilities_limits_none_when_zero_or_negative() -> None:
    caps = _caps(max_text_length=0, max_voice_seconds=-1)
    assert caps.text_limit() is None
    assert caps.voice_limit() is None

    caps_positive = _caps(max_text_length=100, max_voice_seconds=60)
    assert caps_positive.text_limit() == 100
    assert caps_positive.voice_limit() == 60


def test_t_opt8_01_capabilities_frozen() -> None:
    caps = _caps()
    with pytest.raises(Exception):
        caps.platform_id = InputSourceType.WECHAT  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T-OPT8-02：voice → text 降级
# ---------------------------------------------------------------------------


def test_t_opt8_02_degrade_voice_to_text() -> None:
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT,
        session_id="s1",
        text="hi",
        voice_url="http://tts/1.wav",
        voice_duration_seconds=3,
    )
    assert resp.has_voice is True
    degraded = resp.degrade_voice_to_text()
    assert degraded.has_voice is False
    assert degraded.voice_url is None
    assert degraded.voice_duration_seconds == 0
    assert degraded.text == "hi"  # 文本保留


def test_t_opt8_02_degrade_when_no_voice_is_noop() -> None:
    resp = OutboundResponse(platform_id=InputSourceType.WECHAT, session_id="s1", text="hi")
    assert resp.degrade_voice_to_text() is resp  # 原样返回


# ---------------------------------------------------------------------------
# T-OPT8-03：chunk_text 切分
# ---------------------------------------------------------------------------


def test_t_opt8_03_chunk_text_splits_by_limit() -> None:
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT, session_id="s1", text="abcdefghij"
    )
    pieces = resp.chunk_text(3)
    assert [p.text for p in pieces] == ["abc", "def", "ghi", "j"]
    # 切分出的每片都不应携带语音字段
    assert all(p.voice_url is None for p in pieces)


def test_t_opt8_03_chunk_text_noop_when_limit_zero_or_short() -> None:
    resp = OutboundResponse(platform_id=InputSourceType.WECHAT, session_id="s1", text="abc")
    assert resp.chunk_text(0) == (resp,)
    assert resp.chunk_text(10) == (resp,)


# ---------------------------------------------------------------------------
# T-OPT8-04：adapt_response_to_capabilities 组合降级
# ---------------------------------------------------------------------------


def test_t_opt8_04_adapt_drops_voice_and_markdown_and_chunks() -> None:
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT,
        session_id="s1",
        text="a" * 25,
        voice_url="http://x/y.wav",
        voice_duration_seconds=10,
        markdown=True,
    )
    caps = _caps(
        supports_voice=False,
        supports_markdown=False,
        max_text_length=10,
    )
    pieces = adapt_response_to_capabilities(resp, caps)
    # 长度 25 / 10 = 3 块
    assert len(pieces) == 3
    # 全部降级：无 voice、无 markdown
    for piece in pieces:
        assert piece.voice_url is None
        assert piece.markdown is False
    # 文本拼回来等于原文
    assert "".join(p.text for p in pieces) == resp.text


def test_t_opt8_04_adapt_noop_when_all_supported() -> None:
    resp = OutboundResponse(
        platform_id=InputSourceType.TELEGRAM
        if hasattr(InputSourceType, "TELEGRAM")
        else InputSourceType.WECHAT,
        session_id="s1",
        text="short",
        voice_url="http://x/y.wav",
        markdown=True,
    )
    caps = _caps(
        supports_voice=True,
        supports_markdown=True,
        max_text_length=1000,
    )
    pieces = adapt_response_to_capabilities(resp, caps)
    assert pieces == (resp,)


# ---------------------------------------------------------------------------
# T-OPT8-05：Protocol 结构化一致性（非继承即可 isinstance）
# ---------------------------------------------------------------------------


class _MinimalChannel:
    """一个不继承 ChatChannel 但方法签名兼容的实现，用于验证 runtime_checkable。"""

    def __init__(self) -> None:
        self._caps = ChannelCapabilities(platform_id=InputSourceType.WECHAT)

    def capabilities(self) -> ChannelCapabilities:
        return self._caps

    def inbound(self) -> AsyncIterator[InboundEvent]:
        async def _gen() -> AsyncIterator[InboundEvent]:
            if False:  # pragma: no cover - generator skeleton
                yield InboundEvent(platform_id=InputSourceType.WECHAT, session_id="x")
        return _gen()

    async def outbound(self, response: OutboundResponse) -> None:
        return None

    async def health(self) -> ChannelHealth:
        return ChannelHealth(platform_id=InputSourceType.WECHAT, ok=True)


def test_t_opt8_05_minimal_channel_is_runtime_chat_channel() -> None:
    ch = _MinimalChannel()
    assert isinstance(ch, ChatChannel)


def test_t_opt8_05_outbound_is_coroutine() -> None:
    ch = _MinimalChannel()
    resp = OutboundResponse(platform_id=InputSourceType.WECHAT, session_id="s1", text="ok")
    # 运行一次，确保 async 调用链不抛异常
    asyncio.run(ch.outbound(resp))
