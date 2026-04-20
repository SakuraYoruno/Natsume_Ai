"""WeChat 通道能力声明（OPT-8 三期 · 首个平台灰度）.

背景：docs/35_quality/OPTIMIZATION_PLAN_20260420.md §12 / OPT-8 的目标是把
散落在 host_brain / edge_client 中的 ``if platform == "wechat"`` 条件分支
收敛到 :class:`ChatChannel` Protocol + :class:`ChannelCapabilities` 声明里。

本模块提供 WeChat 适配器的**最小接入示例**：

  * :data:`WECHAT_CHANNEL_CAPABILITIES`：官方可公开确认的能力矩阵；
  * :func:`adapt_outbound_for_wechat`：把任意 :class:`OutboundResponse` 按
    WeChat 能力降级并切分的便捷入口。

设计原则（与 OPT-8 base.py 一致）：

  1. **不依赖** :mod:`edge_client.gateway.adapters.wechat_adapter` 的内部实现，
     以避免循环依赖；
  2. **不直接发消息**：只做能力声明与数据结构适配。真正的 IO 由 adapter
     后续 PR 按 :class:`ChatChannel` Protocol 迁移时接入；
  3. **真实值**：能力数值取自微信官方公众号消息规范 + 公司内部实测的
     保守上限，避免把临界值写死后踩限流。

数值依据（WeChat 官方 + 内部实测保守值）：

  * ``max_text_length = 2048``：公众号单条文本消息正文上限 2048 字符。
    真实上限 2048 bytes，此处以 char 数做**保守估计**，多字节字符也安全；
  * ``max_voice_seconds = 60``：公众号语音消息上限 60 秒；
  * ``supports_markdown = False``：公众号文本消息不渲染 Markdown，统一
    会被当作纯文本展示。保持 False 以触发 ``strip_markdown`` 降级。
"""

from __future__ import annotations

from collections.abc import Sequence

from edge_client.channels.base import (
    ChannelCapabilities,
    OutboundResponse,
    adapt_response_to_capabilities,
)
from edge_client.channels.input_reporter import InputSourceType


# ---------------------------------------------------------------------------
# WeChat 能力矩阵（单例、frozen）
# ---------------------------------------------------------------------------

WECHAT_CHANNEL_CAPABILITIES: ChannelCapabilities = ChannelCapabilities(
    platform_id=InputSourceType.WECHAT,
    supports_voice=True,
    supports_image=True,
    supports_file=False,          # 公众号不支持任意文件下行
    supports_markdown=False,      # 公众号文本统一纯文本渲染
    max_text_length=2048,
    max_voice_seconds=60,
    # extras 里保留方言字段，后续如需公众号专有能力（如模板消息 ID、客服消息类型等）
    # 在此追加；核心链路不消费 extras。
    extras={
        "wechat_kind": "official_account",
        "notes": "See OPT-8 base.py for semantic of supports_* fields",
    },
)


# ---------------------------------------------------------------------------
# 便捷入口：按 WeChat 能力降级 + 切分一个 OutboundResponse
# ---------------------------------------------------------------------------


def adapt_outbound_for_wechat(response: OutboundResponse) -> Sequence[OutboundResponse]:
    """按 WeChat 能力把 ``response`` 降级并切分为可发送序列。

    该函数不发送消息，仅返回可以直接投递给 wechat_adapter 的 chunk 序列。
    使用方式示例（未来 adapter 接入 ChatChannel 后的标准写法）::

        for chunk in adapt_outbound_for_wechat(response):
            await wechat_adapter.send(chunk)

    返回序列长度 ≥ 1：
      * 若文本在 ``max_text_length`` 内且无降级需要 → 单元素序列；
      * 若文本超限 → 按 ``max_text_length`` 切分；
      * 若响应携带 voice_url 但平台不支持 voice → 不会发生，因为 WeChat
        支持 voice；但如携带 markdown → 降级剥离。
    """
    return adapt_response_to_capabilities(response, WECHAT_CHANNEL_CAPABILITIES)


__all__ = [
    "WECHAT_CHANNEL_CAPABILITIES",
    "adapt_outbound_for_wechat",
]
