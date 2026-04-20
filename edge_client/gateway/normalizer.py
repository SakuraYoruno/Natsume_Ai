"""Unified Message Gateway — 消息标准化层。

负责：
  1. 消息去重（基于 platform + msg_id 组合键）
  2. 内容脱敏（密码/验证码等敏感信息替换）
  3. 基础校验（必填字段非空）

复用旧微信入口链路中提炼出的去重和脱敏逻辑，上移到 Gateway 公共层，
使所有平台适配器共享同一套去重/脱敏机制。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from edge_client.gateway.models import GatewayAttachment, MsgType, UnifiedMessage

logger = logging.getLogger("Natsume.edge.gateway.normalizer")


# ---------------------------------------------------------------------------
# 敏感内容模式（沿用旧微信入口链路规则，统一在此维护）
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(验证码|密码|口令)\s*[:：]\s*\S+"),
    re.compile(r"\b\d{6}\b"),  # 6 位数字验证码（谨慎：可能误杀普通数字）
]

_PLACEHOLDER_CONVERSATION_IDS: set[str] = {
    "active_chat",
    "active chat",
    "current_chat",
    "current chat",
    "current_session",
    "default",
    "unknown",
    "none",
    "null",
    "-",
    "_",
    "na",
    "n/a",
    "当前聊天",
    "当前会话",
}


# ---------------------------------------------------------------------------
# 消息去重器（跨平台，基于 platform:msg_id 组合键）
# ---------------------------------------------------------------------------

class MessageDeduplicator:
    """跨平台消息去重器。

    去重键为 ``platform:msg_id``，避免不同平台消息 ID 碰撞。
    """

    def __init__(self, window_s: float = 60.0, max_size: int = 2000) -> None:
        self._window_s = window_s
        self._max_size = max_size
        self._seen: dict[str, float] = {}   # dedup_key → 首次见到的 monotonic 时间
        self._lock = threading.Lock()

    def is_duplicate(self, dedup_key: str) -> bool:
        """检查是否重复，同时记录新消息。线程安全。"""
        if self._window_s <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            self._evict_expired(now)
            if dedup_key in self._seen:
                return True
            if len(self._seen) >= self._max_size:
                oldest = min(self._seen, key=lambda k: self._seen[k])
                del self._seen[oldest]
            self._seen[dedup_key] = now
            return False

    def _evict_expired(self, now: float) -> None:
        expired = [k for k, t in self._seen.items() if (now - t) > self._window_s]
        for k in expired:
            del self._seen[k]

    def size(self) -> int:
        with self._lock:
            return len(self._seen)


# ---------------------------------------------------------------------------
# 内容脱敏
# ---------------------------------------------------------------------------

def desensitize_content(text: str) -> str:
    """对消息内容进行脱敏处理（替换密码/验证码等敏感信息）。"""
    result = text
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


# ---------------------------------------------------------------------------
# 消息标准化器
# ---------------------------------------------------------------------------

class MessageNormalizer:
    """消息标准化器：去重 + 脱敏 + 校验。

    所有平台适配器在将消息推入路由前，都应先经过此标准化器。

    Args:
        dedup_window_s: 去重时间窗口（秒），0 表示不去重
        dedup_max_size: 去重缓存最大条数
        enable_desensitize: 是否启用内容脱敏
        skip_system_msgs: 是否跳过系统消息
    """

    def __init__(
        self,
        *,
        dedup_window_s: float = 60.0,
        dedup_max_size: int = 2000,
        enable_desensitize: bool = True,
        skip_system_msgs: bool = True,
    ) -> None:
        self._dedup = MessageDeduplicator(
            window_s=dedup_window_s,
            max_size=dedup_max_size,
        )
        self._enable_desensitize = enable_desensitize
        self._skip_system_msgs = skip_system_msgs
        self._stats: dict[str, int] = {
            "received": 0,
            "invalid": 0,
            "invalid_conversation": 0,
            "system_skipped": 0,
            "deduplicated": 0,
            "desensitized": 0,
            "passed": 0,
        }

    def normalize(self, msg: UnifiedMessage) -> UnifiedMessage | None:
        """标准化消息。

        Returns:
            标准化后的 UnifiedMessage，或 None（表示消息应被丢弃）。
        """
        self._stats["received"] += 1

        # ── 基础校验 ──
        if not msg.from_id or not msg.conversation_id:
            self._stats["invalid"] += 1
            logger.debug(
                "Normalizer: 丢弃无效消息 platform=%s msg_id=%s（from_id 或 conversation_id 为空）",
                msg.platform.value, msg.msg_id,
            )
            return None
        if self._is_placeholder_conversation_id(msg.conversation_id):
            self._stats["invalid_conversation"] += 1
            logger.warning(
                "Normalizer: 丢弃占位会话消息 platform=%s msg_id=%s conversation_id=%s",
                msg.platform.value,
                msg.msg_id,
                msg.conversation_id,
            )
            return None

        # ── 跳过系统消息 ──
        if self._skip_system_msgs and msg.msg_type == MsgType.SYSTEM:
            self._stats["system_skipped"] += 1
            return None

        # ── 附件兼容回填（旧链路只传 media_url） ──
        self._backfill_attachments(msg)

        # ── 去重 ──
        if self._dedup.is_duplicate(msg.dedup_key()):
            self._stats["deduplicated"] += 1
            logger.debug(
                "Normalizer: 重复消息已跳过 %s", msg.dedup_key()
            )
            return None

        # ── 脱敏（仅文本消息） ──
        if self._enable_desensitize and msg.msg_type == MsgType.TEXT and msg.content:
            desensitized = desensitize_content(msg.content)
            if desensitized != msg.content:
                self._stats["desensitized"] += 1
                # dataclass 不可变字段需要重建（UnifiedMessage 是普通 dataclass，可直接赋值）
                msg.content = desensitized

        self._stats["passed"] += 1
        return msg

    def stats(self) -> dict[str, Any]:
        """返回标准化统计快照。"""
        return {
            **self._stats,
            "dedup_cache_size": self._dedup.size(),
        }

    @staticmethod
    def _is_placeholder_conversation_id(value: str) -> bool:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return True
        return normalized in _PLACEHOLDER_CONVERSATION_IDS

    @staticmethod
    def _backfill_attachments(msg: UnifiedMessage) -> None:
        if list(msg.attachments or []):
            return
        media_url = str(msg.media_url or "").strip()
        if not media_url:
            return

        attachment_type_map: dict[MsgType, str] = {
            MsgType.IMAGE: "image",
            MsgType.EMOJI: "emoji",
            MsgType.VOICE: "voice",
            MsgType.VIDEO: "video",
            MsgType.FILE: "file",
        }
        attachment_type = attachment_type_map.get(msg.msg_type)
        if not attachment_type:
            return

        is_remote = media_url.startswith(("http://", "https://"))
        attachment_id = str(msg.msg_id or media_url)
        msg.attachments = [
            GatewayAttachment(
                attachment_id=f"{attachment_id}:0",
                type=attachment_type,
                local_path=(None if is_remote else media_url),
                remote_url=(media_url if is_remote else None),
                caption=str(msg.content or "").strip() or None,
            )
        ]
