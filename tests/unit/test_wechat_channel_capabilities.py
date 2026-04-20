"""WeChat 通道能力声明单测（OPT-8 三期 · 首个平台灰度）.

覆盖 edge_client/channels/wechat_channel_capabilities.py 的行为：

  T-OPT8-10：WECHAT_CHANNEL_CAPABILITIES 能力矩阵符合 WeChat 官方约束。
  T-OPT8-11：adapt_outbound_for_wechat 对短文本无降级直接原样返回单元素序列。
  T-OPT8-12：adapt_outbound_for_wechat 对超长文本按 max_text_length 切分，
            拼接回来等于原文。
  T-OPT8-13：adapt_outbound_for_wechat 对 markdown=True 响应自动 strip_markdown。
  T-OPT8-14：adapt_outbound_for_wechat 不会误删受支持的 voice_url（WeChat 支持
            语音），但超长文本分片时每片仍按 base.py 约定去掉 voice 字段。
  T-OPT8-15：模块级常量是 frozen，防止运行时篡改。
"""

from __future__ import annotations

import pytest

from edge_client.channels.base import OutboundResponse
from edge_client.channels.input_reporter import InputSourceType
from edge_client.channels.wechat_channel_capabilities import (
    WECHAT_CHANNEL_CAPABILITIES,
    adapt_outbound_for_wechat,
)


# ---------------------------------------------------------------------------
# T-OPT8-10：能力矩阵
# ---------------------------------------------------------------------------


def test_t_opt8_10_capabilities_match_wechat_spec() -> None:
    caps = WECHAT_CHANNEL_CAPABILITIES
    assert caps.platform_id is InputSourceType.WECHAT
    assert caps.supports_voice is True
    assert caps.supports_image is True
    assert caps.supports_file is False
    assert caps.supports_markdown is False
    assert caps.max_text_length == 2048
    assert caps.max_voice_seconds == 60
    assert caps.text_limit() == 2048
    assert caps.voice_limit() == 60


# ---------------------------------------------------------------------------
# T-OPT8-11：短文本无降级
# ---------------------------------------------------------------------------


def test_t_opt8_11_short_text_noop() -> None:
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT,
        session_id="s1",
        text="hi, 近况如何？",
    )
    out = adapt_outbound_for_wechat(resp)
    assert out == (resp,)


# ---------------------------------------------------------------------------
# T-OPT8-12：超长文本按 2048 切分
# ---------------------------------------------------------------------------


def test_t_opt8_12_long_text_chunked_by_2048() -> None:
    long_text = "x" * (2048 * 2 + 500)  # 足以切成 3 片
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT,
        session_id="s1",
        text=long_text,
    )
    out = adapt_outbound_for_wechat(resp)
    assert len(out) == 3
    assert all(len(piece.text) <= 2048 for piece in out)
    assert "".join(piece.text for piece in out) == long_text


# ---------------------------------------------------------------------------
# T-OPT8-13：markdown 自动剥离
# ---------------------------------------------------------------------------


def test_t_opt8_13_markdown_stripped() -> None:
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT,
        session_id="s1",
        text="**bold** text",
        markdown=True,
    )
    out = adapt_outbound_for_wechat(resp)
    assert len(out) == 1
    assert out[0].markdown is False
    # 文本保留（base.py 约定：平台不支持 markdown 时不剥离原文内容，只取消标志）
    assert out[0].text == "**bold** text"


# ---------------------------------------------------------------------------
# T-OPT8-14：voice 受支持时保留；切分时按 base.py 约定去 voice
# ---------------------------------------------------------------------------


def test_t_opt8_14_voice_preserved_when_within_limit() -> None:
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT,
        session_id="s1",
        text="hi",
        voice_url="http://cdn/tts.wav",
        voice_duration_seconds=5,
    )
    out = adapt_outbound_for_wechat(resp)
    assert len(out) == 1
    assert out[0].voice_url == "http://cdn/tts.wav"
    assert out[0].voice_duration_seconds == 5


def test_t_opt8_14b_voice_dropped_when_text_chunked() -> None:
    long_text = "y" * (2048 + 10)
    resp = OutboundResponse(
        platform_id=InputSourceType.WECHAT,
        session_id="s1",
        text=long_text,
        voice_url="http://cdn/tts.wav",
        voice_duration_seconds=10,
    )
    out = adapt_outbound_for_wechat(resp)
    assert len(out) >= 2
    # base.py 约定：chunk_text 会把每片的 voice_url 清空，避免重复播放
    assert all(piece.voice_url is None for piece in out)


# ---------------------------------------------------------------------------
# T-OPT8-15：frozen 防篡改
# ---------------------------------------------------------------------------


def test_t_opt8_15_capabilities_frozen() -> None:
    with pytest.raises(Exception):
        WECHAT_CHANNEL_CAPABILITIES.max_text_length = 9999  # type: ignore[misc]
