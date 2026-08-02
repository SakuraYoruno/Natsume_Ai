"""ChatChannel Protocol 与能力声明（OPT-8）.

历史背景：docs/_history/quality/OPTIMIZATION_PLAN_20260420.md §12 / OPT-8 指出，
当前六平台（微信/QQ/Telegram/飞书/企微/LINE）的能力差异（语音、图片、长文本上限）
分散在 host_brain / edge_client 的多个 ``if platform == "wechat"`` 分支中，
新增平台接入成本高、易遗漏降级路径。

本模块提供一个**最小侵入**的协议抽象：

  * :class:`ChannelCapabilities` — 平台能力声明（是否支持语音/图片/文件/
    markdown，以及文本与语音长度上限）。frozen dataclass，天然可哈希、可比较。
  * :class:`InboundEvent` — 入站消息事件的统一包装。与 ``EdgeInputReporter``
    的 snake_case JSON 输出兼容。
  * :class:`OutboundResponse` — 出站响应，附带主动降级方法 :meth:`degrade_voice_to_text`
    / :meth:`chunk_text`，让 ``MotorCortexAgent.dispatch_speak()`` 能按能力自适应。
  * :class:`ChannelHealth` — 通道健康快照（供 ``/health/*`` 聚合）。
  * :class:`ChatChannel` — ``@runtime_checkable`` Protocol，定义 inbound /
    outbound / capabilities / health 四个方法。

设计原则：
  1. **只定义契约，不提供具体实现**：具体的 WeChat/QQ/Telegram adapter 由
     后续 PR 按此 Protocol 迁移。
  2. **不依赖** host_brain 代码：避免边-主脑双向依赖。
  3. **平台枚举复用** :class:`edge_client.channels.input_reporter.InputSourceType`，
     不再重造一套平台 ID。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from edge_client.channels.input_reporter import InputSourceType


# ---------------------------------------------------------------------------
# 平台能力声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelCapabilities:
    """平台能力快照（frozen，可作为字典键/集合元素）.

    Attributes
    ----------
    platform_id
        归一化平台 ID，复用 :class:`InputSourceType`。
    supports_voice
        是否支持语音/音频消息发送。
    supports_image
        是否支持图片消息发送。
    supports_file
        是否支持任意文件附件发送。
    supports_markdown
        是否支持 Markdown 渲染（决定是否需要降级为纯文本）。
    max_text_length
        单条文本最长字符数；超过时 ``OutboundResponse.chunk_text`` 会分段。
        0 或负数表示无限制。
    max_voice_seconds
        单条语音最长时长（秒）；超过时需要分段或降级为文本。
    extras
        平台方言字段。允许 adapter 返回平台专有能力（如 wechat_can_at），
        但不应被 host_brain 核心链路直接消费。
    """

    platform_id: InputSourceType
    supports_voice: bool = False
    supports_image: bool = False
    supports_file: bool = False
    supports_markdown: bool = False
    max_text_length: int = 0
    max_voice_seconds: int = 0
    extras: dict[str, Any] = field(default_factory=dict)

    def text_limit(self) -> int | None:
        """返回文本长度上限；0/负数表示无限制。"""
        return self.max_text_length if self.max_text_length > 0 else None

    def voice_limit(self) -> int | None:
        """返回语音时长上限（秒）；0/负数表示无限制。"""
        return self.max_voice_seconds if self.max_voice_seconds > 0 else None


# ---------------------------------------------------------------------------
# 入站 / 出站事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboundEvent:
    """入站消息事件（来自平台 SDK → Host Brain）.

    保持与现有 ``EdgeInputReporter`` payload 的 snake_case 字段约定一致。
    """

    platform_id: InputSourceType
    session_id: str
    text: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
    received_ts_ms: int = 0
    sender_id: str | None = None
    thread_key: str | None = None


@dataclass(frozen=True)
class OutboundResponse:
    """出站响应（Host Brain → 平台）.

    ``MotorCortexAgent.dispatch_speak`` 可在发送前调用
    :meth:`degrade_voice_to_text` / :meth:`chunk_text` 做能力自适应，
    避免散落的 ``if platform == "wechat"`` 分支。
    """

    platform_id: InputSourceType
    session_id: str
    text: str = ""
    voice_url: str | None = None
    voice_duration_seconds: int = 0
    image_url: str | None = None
    markdown: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def has_voice(self) -> bool:
        return bool(self.voice_url)

    @property
    def has_image(self) -> bool:
        return bool(self.image_url)

    def degrade_voice_to_text(self) -> "OutboundResponse":
        """能力不足时把语音响应降级为纯文本；无语音则原样返回。"""
        if not self.has_voice:
            return self
        return replace(self, voice_url=None, voice_duration_seconds=0)

    def strip_markdown(self) -> "OutboundResponse":
        """平台不支持 markdown 时去掉标志；正文不做剥离（由 adapter 决定）。"""
        if not self.markdown:
            return self
        return replace(self, markdown=False)

    def chunk_text(self, limit: int) -> Sequence["OutboundResponse"]:
        """按 ``limit`` 切分长文本；limit <= 0 时返回原响应单元素序列。"""
        if limit <= 0 or len(self.text) <= limit:
            return (self,)
        pieces: list[OutboundResponse] = []
        for start in range(0, len(self.text), limit):
            chunk = self.text[start : start + limit]
            pieces.append(replace(self, text=chunk, voice_url=None, voice_duration_seconds=0))
        return tuple(pieces)


@dataclass(frozen=True)
class ChannelHealth:
    """通道健康快照."""

    platform_id: InputSourceType
    ok: bool
    last_heartbeat_ms: int = 0
    consecutive_failures: int = 0
    degraded_reason: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ChatChannel(Protocol):
    """统一平台通道契约（OPT-8）.

    具体的 WeChat / QQ / Telegram / Feishu / WeCom / LINE adapter 实现本
    Protocol 即可对接 ``MotorCortexAgent.dispatch_speak``。

    注意：Protocol 方法都是 async——入站是 ``AsyncIterator``，出站/健康检查
    是协程；用 ``@runtime_checkable`` 可在测试中用 ``isinstance`` 做结构检测。
    """

    def capabilities(self) -> ChannelCapabilities:
        """返回平台能力快照（应当是纯函数，每次返回一致值）."""
        ...

    def inbound(self) -> AsyncIterator[InboundEvent]:
        """订阅入站事件流；调用方可通过 ``async for`` 迭代."""
        ...

    async def outbound(self, response: OutboundResponse) -> None:
        """把响应发送到平台；应由 adapter 负责重试与失败上报."""
        ...

    async def health(self) -> ChannelHealth:
        """返回通道当前健康状态（同步探测 or 读取最近心跳）."""
        ...


# ---------------------------------------------------------------------------
# 能力自适应工具（给 MotorCortexAgent 复用）
# ---------------------------------------------------------------------------


def adapt_response_to_capabilities(
    response: OutboundResponse,
    capabilities: ChannelCapabilities,
) -> Sequence[OutboundResponse]:
    """把单个 ``response`` 按 ``capabilities`` 降级/切分为可发送序列.

    调用约定：
      * 语音不支持 → :meth:`OutboundResponse.degrade_voice_to_text`
      * markdown 不支持 → :meth:`OutboundResponse.strip_markdown`
      * 文本超限 → :meth:`OutboundResponse.chunk_text`

    返回至少长度 1 的序列，保证调用方可以简单 ``for chunk in ...: channel.outbound(chunk)``。
    """
    out = response
    if out.has_voice and not capabilities.supports_voice:
        out = out.degrade_voice_to_text()
    if out.markdown and not capabilities.supports_markdown:
        out = out.strip_markdown()
    limit = capabilities.text_limit()
    if limit is not None and len(out.text) > limit:
        return out.chunk_text(limit)
    return (out,)


__all__ = [
    "ChannelCapabilities",
    "ChannelHealth",
    "ChatChannel",
    "InboundEvent",
    "OutboundResponse",
    "adapt_response_to_capabilities",
]
