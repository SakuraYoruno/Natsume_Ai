"""Gateway 拟人化发送公共模块。

将 AI 回复拆分为多段，模拟真人打字节奏逐段发送，
适用于所有 Gateway 平台（微信/QQ/Telegram/飞书/企微）。

核心能力：
  1. 智能分段：按句子边界拆分，每段不超过 max_length
  2. 打字延迟：根据文本长度模拟打字耗时，加随机抖动
  3. 段间停顿：多段之间自然停顿（模拟"想了一下再继续说"）
  4. 配置灵活：可通过环境变量或参数覆盖所有行为

环境变量：
  EDGE_HUMANIZE_ENABLED          启用拟人化发送（默认 False）
  EDGE_HUMANIZE_TYPING_SPEED_CPS 打字速度（字符/秒，默认 12）
  EDGE_HUMANIZE_BASE_DELAY_S     基础延迟（秒，默认 0.3）
  EDGE_HUMANIZE_MAX_DELAY_S      单段最大延迟（秒，默认 8.0）
  EDGE_HUMANIZE_SEGMENT_GAP_MIN  段间最小间隔（秒，默认 0.5）
  EDGE_HUMANIZE_SEGMENT_GAP_MAX  段间最大间隔（秒，默认 2.5）
  EDGE_HUMANIZE_MAX_SEGMENTS     最大分段数（默认 5）
  EDGE_HUMANIZE_SEGMENT_MAX_LEN  单段最大字符数（默认 200）
  EDGE_HUMANIZE_JITTER           延迟抖动幅度（0.0-1.0，默认 0.2）
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("Natsume.edge.gateway.humanize")

# ---------------------------------------------------------------------------
# 句子边界标点（中文 + 日文 + 英文）
# ---------------------------------------------------------------------------

_SENTENCE_END_CHARS = frozenset("。！？…\n」）.!?")
_PARAGRAPH_SEP = "\n\n"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class HumanizeSendConfig:
    """拟人化发送配置。

    可通过 HumanizeSendConfig.from_env() 从环境变量加载，
    也可直接构造覆盖特定参数。
    """
    enabled: bool = False
    typing_speed_cps: float = 12.0       # 字符/秒（characters per second）
    base_delay_s: float = 0.3            # 基础延迟（模拟"看到消息→开始打字"的反应时间）
    max_delay_s: float = 8.0             # 单段最大延迟
    segment_gap_min_s: float = 0.5       # 段间最小间隔
    segment_gap_max_s: float = 2.5       # 段间最大间隔
    max_segments: int = 5                # 最大分段数
    segment_max_len: int = 200           # 单段最大字符数
    jitter: float = 0.2                  # 延迟随机抖动幅度（±jitter）
    short_text_bypass_len: int = 12      # 短文本直接发送，不模拟打字

    @classmethod
    def from_env(cls) -> HumanizeSendConfig:
        """从环境变量加载配置。"""
        return cls(
            enabled=_env_bool("EDGE_HUMANIZE_ENABLED", False),
            typing_speed_cps=max(1.0, _env_float("EDGE_HUMANIZE_TYPING_SPEED_CPS", 12.0)),
            base_delay_s=max(0.0, _env_float("EDGE_HUMANIZE_BASE_DELAY_S", 0.3)),
            max_delay_s=max(0.5, _env_float("EDGE_HUMANIZE_MAX_DELAY_S", 8.0)),
            segment_gap_min_s=max(0.0, _env_float("EDGE_HUMANIZE_SEGMENT_GAP_MIN", 0.5)),
            segment_gap_max_s=max(0.1, _env_float("EDGE_HUMANIZE_SEGMENT_GAP_MAX", 2.5)),
            max_segments=max(1, _env_int("EDGE_HUMANIZE_MAX_SEGMENTS", 5)),
            segment_max_len=max(50, _env_int("EDGE_HUMANIZE_SEGMENT_MAX_LEN", 200)),
            jitter=max(0.0, min(1.0, _env_float("EDGE_HUMANIZE_JITTER", 0.2))),
            short_text_bypass_len=max(0, _env_int("EDGE_HUMANIZE_SHORT_TEXT_BYPASS_LEN", 12)),
        )


# ---------------------------------------------------------------------------
# 智能分段
# ---------------------------------------------------------------------------

def split_message_humanize(text: str, *, max_length: int = 200, max_segments: int = 5) -> list[str]:
    """将长消息按句子/段落边界拆分为多段。

    规则：
      1. 短消息（≤ max_length）不拆分
      2. 优先按段落（双换行）拆分
      3. 段落内按句子边界（。！？…）拆分
      4. 超长句子按 max_length 硬切
      5. 最多 max_segments 段，多余合并到最后一段

    Args:
        text: 原始消息文本
        max_length: 单段最大字符数
        max_segments: 最大分段数

    Returns:
        分段后的文本列表
    """
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    segments: list[str] = []

    # Step 1: 按段落拆分
    paragraphs = text.split(_PARAGRAPH_SEP)

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 段落本身短，尝试合并到上一段
        if segments and len(segments[-1]) + len(para) + 2 <= max_length:
            segments[-1] = f"{segments[-1]}\n\n{para}"
            continue

        # 段落长度可接受，直接作为一段
        if len(para) <= max_length:
            segments.append(para)
            continue

        # 段落太长：按句子边界拆分
        _split_by_sentence(para, max_length=max_length, out=segments)

    # 清理空段
    segments = [s.strip() for s in segments if s.strip()]

    # 限制段数
    if len(segments) > max_segments:
        overflow = segments[max_segments - 1:]
        segments = segments[: max_segments - 1]
        segments.append("\n".join(overflow))

    return segments


def _split_by_sentence(text: str, *, max_length: int, out: list[str]) -> None:
    """按句子边界拆分长文本，结果追加到 out。"""
    current = ""
    i = 0

    while i < len(text):
        ch = text[i]

        if ch in _SENTENCE_END_CHARS:
            # 消费连续边界字符
            j = i + 1
            while j < len(text) and text[j] in _SENTENCE_END_CHARS:
                j += 1
            current += text[i:j]
            i = j

            # 句子完成，检查是否需要切段
            if len(current) >= max_length * 0.6:
                out.append(current.strip())
                current = ""
        else:
            current += ch
            i += 1

            # 硬切：当前段已超过 max_length 且还没遇到句子边界
            if len(current) >= max_length:
                # 尝试在最后一个标点处切
                cut_pos = -1
                for cp in range(len(current) - 1, max(0, len(current) - 50), -1):
                    if current[cp] in _SENTENCE_END_CHARS or current[cp] in "，、；：,;:":
                        cut_pos = cp + 1
                        break
                if cut_pos > 0:
                    out.append(current[:cut_pos].strip())
                    current = current[cut_pos:]
                else:
                    out.append(current.strip())
                    current = ""

    if current.strip():
        out.append(current.strip())


# ---------------------------------------------------------------------------
# 打字延迟计算
# ---------------------------------------------------------------------------

def compute_typing_delay(
    text: str,
    *,
    config: HumanizeSendConfig,
    is_first_segment: bool = False,
) -> float:
    """计算某段文本的模拟打字延迟。

    公式：delay = base_delay + len(text) / typing_speed_cps + jitter

    Args:
        text: 要发送的文本段
        config: 拟人化配置
        is_first_segment: 是否为第一段（第一段有额外的"反应时间"）

    Returns:
        延迟秒数
    """
    char_count = len(str(text or ""))
    typing_time = char_count / max(1.0, config.typing_speed_cps)

    delay = typing_time
    if is_first_segment:
        delay += config.base_delay_s

    # 随机抖动 ±jitter
    if config.jitter > 0:
        jitter_range = delay * config.jitter
        delay += random.uniform(-jitter_range, jitter_range)

    return max(0.1, min(delay, config.max_delay_s))


def compute_segment_gap(config: HumanizeSendConfig) -> float:
    """计算段间停顿时间。

    Returns:
        停顿秒数
    """
    return random.uniform(config.segment_gap_min_s, config.segment_gap_max_s)


# ---------------------------------------------------------------------------
# 发送结果
# ---------------------------------------------------------------------------

@dataclass
class HumanizeSendResult:
    """拟人化发送结果。"""
    success: bool = True
    total_segments: int = 0
    sent_segments: int = 0
    total_delay_ms: int = 0
    sleep_delay_ms: int = 0
    send_elapsed_ms: int = 0
    failed_segment_index: int = -1
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "total_segments": self.total_segments,
            "sent_segments": self.sent_segments,
            "total_delay_ms": self.total_delay_ms,
            "sleep_delay_ms": self.sleep_delay_ms,
            "send_elapsed_ms": self.send_elapsed_ms,
            "failed_segment_index": self.failed_segment_index,
            "error": self.error[:200] if self.error else "",
        }


# ---------------------------------------------------------------------------
# 核心：拟人化发送（异步）
# ---------------------------------------------------------------------------

async def humanize_send_async(
    text: str,
    *,
    send_fn: Callable[[str], Awaitable[bool] | bool],
    config: HumanizeSendConfig | None = None,
) -> HumanizeSendResult:
    """拟人化分段发送（异步版本）。

    将文本拆分为多段，逐段发送，段前模拟打字延迟，段间自然停顿。

    Args:
        text: 完整回复文本
        send_fn: 发送单段文本的回调函数，签名为 (segment: str) -> bool
                 可以是同步或异步函数
        config: 拟人化配置，不传则从环境变量加载

    Returns:
        HumanizeSendResult

    用法示例::

        # 微信适配器
        result = await humanize_send_async(
            text=reply.content,
            send_fn=lambda seg: self._send_text_segment(seg, target=reply.conversation_id),
            config=HumanizeSendConfig.from_env(),
        )

        # Telegram 适配器
        result = await humanize_send_async(
            text=reply.content,
            send_fn=lambda seg: self._send_text_to_chat(chat_id, seg),
        )
    """
    cfg = config or HumanizeSendConfig.from_env()
    result = HumanizeSendResult()

    text = str(text or "").strip()
    if not text:
        result.success = True
        return result

    # 未启用拟人化：直接一次性发送
    if not cfg.enabled:
        try:
            send_result = send_fn(text)
            if asyncio.iscoroutine(send_result) or asyncio.isfuture(send_result):
                send_result = await send_result
            result.total_segments = 1
            result.sent_segments = 1 if send_result else 0
            result.success = bool(send_result)
        except Exception as exc:
            result.success = False
            result.error = str(exc)
        return result

    if cfg.short_text_bypass_len > 0 and len(text) <= int(cfg.short_text_bypass_len):
        try:
            send_started = time.monotonic()
            send_result = send_fn(text)
            if asyncio.iscoroutine(send_result) or asyncio.isfuture(send_result):
                send_result = await send_result
            result.total_segments = 1
            result.sent_segments = 1 if send_result else 0
            result.success = bool(send_result)
            result.total_delay_ms = int((time.monotonic() - send_started) * 1000)
            result.send_elapsed_ms = result.total_delay_ms
        except Exception as exc:
            result.success = False
            result.error = str(exc)
        return result

    # 分段
    segments = split_message_humanize(
        text,
        max_length=cfg.segment_max_len,
        max_segments=cfg.max_segments,
    )

    if not segments:
        segments = [text]

    result.total_segments = len(segments)
    start_ts = time.monotonic()
    sleep_delay_ms = 0
    send_elapsed_ms = 0

    for idx, segment in enumerate(segments):
        if not segment.strip():
            continue

        # 打字延迟
        delay = compute_typing_delay(
            segment,
            config=cfg,
            is_first_segment=(idx == 0),
        )
        await asyncio.sleep(delay)
        sleep_delay_ms += int(delay * 1000)

        # 发送
        try:
            send_started = time.monotonic()
            send_result = send_fn(segment)
            if asyncio.iscoroutine(send_result) or asyncio.isfuture(send_result):
                send_result = await send_result
            send_elapsed_ms += int((time.monotonic() - send_started) * 1000)
            if not send_result:
                result.success = False
                result.failed_segment_index = idx
                result.error = f"send_fn returned False for segment {idx}"
                break
            result.sent_segments += 1
        except Exception as exc:
            result.success = False
            result.failed_segment_index = idx
            result.error = str(exc)
            logger.warning(
                "humanize_send_async: segment %d/%d 发送失败: %s",
                idx + 1, len(segments), exc,
            )
            break

        # 段间停顿（非最后一段）
        if idx < len(segments) - 1:
            gap = compute_segment_gap(cfg)
            await asyncio.sleep(gap)
            sleep_delay_ms += int(gap * 1000)

    result.total_delay_ms = int((time.monotonic() - start_ts) * 1000)
    result.sleep_delay_ms = sleep_delay_ms
    result.send_elapsed_ms = send_elapsed_ms

    if result.success:
        logger.info(
            "humanize_send: segments=%d total=%dms sleep=%dms send=%dms",
            result.sent_segments,
            result.total_delay_ms,
            result.sleep_delay_ms,
            result.send_elapsed_ms,
        )
    return result


def humanize_send_sync(
    text: str,
    *,
    send_fn: Callable[[str], bool],
    config: HumanizeSendConfig | None = None,
) -> HumanizeSendResult:
    """拟人化分段发送（同步版本）。

    与 humanize_send_async 逻辑一致，但使用 time.sleep 替代 asyncio.sleep，
    适用于同步适配器（如微信 bridge 队列模式）。

    Args:
        text: 完整回复文本
        send_fn: 同步发送单段文本的回调，签名为 (segment: str) -> bool
        config: 拟人化配置

    Returns:
        HumanizeSendResult
    """
    cfg = config or HumanizeSendConfig.from_env()
    result = HumanizeSendResult()

    text = str(text or "").strip()
    if not text:
        result.success = True
        return result

    if not cfg.enabled:
        try:
            ok = send_fn(text)
            result.total_segments = 1
            result.sent_segments = 1 if ok else 0
            result.success = bool(ok)
        except Exception as exc:
            result.success = False
            result.error = str(exc)
        return result

    if cfg.short_text_bypass_len > 0 and len(text) <= int(cfg.short_text_bypass_len):
        try:
            send_started = time.monotonic()
            ok = send_fn(text)
            result.total_segments = 1
            result.sent_segments = 1 if ok else 0
            result.success = bool(ok)
            result.total_delay_ms = int((time.monotonic() - send_started) * 1000)
            result.send_elapsed_ms = result.total_delay_ms
        except Exception as exc:
            result.success = False
            result.error = str(exc)
        return result

    segments = split_message_humanize(
        text,
        max_length=cfg.segment_max_len,
        max_segments=cfg.max_segments,
    )
    if not segments:
        segments = [text]

    result.total_segments = len(segments)
    start_ts = time.monotonic()
    sleep_delay_ms = 0
    send_elapsed_ms = 0

    for idx, segment in enumerate(segments):
        if not segment.strip():
            continue

        delay = compute_typing_delay(segment, config=cfg, is_first_segment=(idx == 0))
        time.sleep(delay)
        sleep_delay_ms += int(delay * 1000)

        try:
            send_started = time.monotonic()
            ok = send_fn(segment)
            send_elapsed_ms += int((time.monotonic() - send_started) * 1000)
            if not ok:
                result.success = False
                result.failed_segment_index = idx
                result.error = f"send_fn returned False for segment {idx}"
                break
            result.sent_segments += 1
        except Exception as exc:
            result.success = False
            result.failed_segment_index = idx
            result.error = str(exc)
            break

        if idx < len(segments) - 1:
            gap = compute_segment_gap(cfg)
            time.sleep(gap)
            sleep_delay_ms += int(gap * 1000)

    result.total_delay_ms = int((time.monotonic() - start_ts) * 1000)
    result.sleep_delay_ms = sleep_delay_ms
    result.send_elapsed_ms = send_elapsed_ms
    return result
