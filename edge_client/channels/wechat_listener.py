"""微信小号消息监听器。

监听微信小号（Edge 端本地运行的微信）接收到的消息，
将消息上报给 Host Brain 处理。

支持两种接入方式：
  1. HTTP 回调模式（推荐）：微信 Hook 工具（如 WeChatFerry/wcf）将消息 POST 到本地端口
  2. 轮询模式（备用）：定期调用 wcf SDK 拉取新消息

设计原则：
  - 与 EdgeInputReporter 解耦，只负责接收和转发
  - 支持消息去重（基于 msg_id）
  - 密码框/敏感内容自动脱敏后再上报
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("Natsume.edge.channels.wechat_listener")


# ---------------------------------------------------------------------------
# 消息类型常量
# ---------------------------------------------------------------------------

MSG_TYPE_TEXT = "TEXT"
MSG_TYPE_IMAGE = "IMAGE"
MSG_TYPE_VOICE = "VOICE"
MSG_TYPE_VIDEO = "VIDEO"
MSG_TYPE_FILE = "FILE"
MSG_TYPE_LINK = "LINK"
MSG_TYPE_SYSTEM = "SYSTEM"

# 需要脱敏的关键词模式（密码/验证码等）
_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(验证码|密码|口令)\s*[:：]\s*\S+"),
    re.compile(r"\b\d{6}\b"),  # 6位数字验证码（谨慎：可能误杀）
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class WeChatMessage:
    """微信消息结构。"""
    msg_id: str
    from_wxid: str
    to_wxid: str
    content: str
    msg_type: str = MSG_TYPE_TEXT
    room_wxid: str | None = None   # 群聊时的群 ID
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    raw_extra: dict[str, Any] = field(default_factory=dict)

    def is_group_msg(self) -> bool:
        """是否为群消息。"""
        return bool(self.room_wxid)

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "msg_id": self.msg_id,
            "from_wxid": self.from_wxid,
            "to_wxid": self.to_wxid,
            "content": self.content,
            "msg_type": self.msg_type,
            "ts_ms": self.ts_ms,
        }
        if self.room_wxid:
            d["room_wxid"] = self.room_wxid
        if self.raw_extra:
            d["raw_extra"] = self.raw_extra
        return d


@dataclass(frozen=True)
class WeChatListenerConfig:
    """微信监听器配置。"""
    # 监听的微信小号 wxid（用于过滤，None 表示监听所有）
    self_wxid: str | None = None
    # 是否只处理私聊消息（忽略群消息）
    private_only: bool = False
    # 是否只处理文本消息
    text_only: bool = True
    # 消息去重窗口（秒），0 表示不去重
    dedup_window_s: float = 60.0
    # 最大去重缓存条数
    dedup_max_size: int = 1000
    # 是否启用内容脱敏
    enable_desensitize: bool = True


MessageCallback = Callable[[WeChatMessage], None]


# ---------------------------------------------------------------------------
# 消息去重器
# ---------------------------------------------------------------------------

class _MessageDeduplicator:
    """基于 msg_id 的消息去重器。"""

    def __init__(self, window_s: float = 60.0, max_size: int = 1000) -> None:
        self._window_s = window_s
        self._max_size = max_size
        self._seen: dict[str, float] = {}  # msg_id → 首次见到的时间
        self._lock = threading.Lock()

    def is_duplicate(self, msg_id: str) -> bool:
        """检查是否重复，同时记录新消息。"""
        if self._window_s <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            self._evict_expired(now)
            if msg_id in self._seen:
                return True
            # 超出上限时丢弃最旧的
            if len(self._seen) >= self._max_size:
                oldest_key = min(self._seen, key=lambda k: self._seen[k])
                del self._seen[oldest_key]
            self._seen[msg_id] = now
            return False

    def _evict_expired(self, now: float) -> None:
        expired = [k for k, t in self._seen.items() if (now - t) > self._window_s]
        for k in expired:
            del self._seen[k]


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
# 微信监听器核心
# ---------------------------------------------------------------------------

class WeChatListener:
    """微信小号消息监听器。

    接收微信消息后，经过过滤、去重、脱敏，调用 on_message 回调。
    回调通常是 EdgeInputReporter.report_wechat。

    使用方式：
        listener = WeChatListener(config=cfg, on_message=reporter.report_wechat_msg)
        listener.start()
        # ... 运行中 ...
        listener.stop()
    """

    def __init__(
        self,
        *,
        config: WeChatListenerConfig | None = None,
        on_message: MessageCallback | None = None,
    ) -> None:
        self._config = config or WeChatListenerConfig()
        self._on_message = on_message
        self._dedup = _MessageDeduplicator(
            window_s=self._config.dedup_window_s,
            max_size=self._config.dedup_max_size,
        )
        self._running = False
        self._lock = threading.Lock()
        self._stats = {
            "received": 0,
            "filtered": 0,
            "deduplicated": 0,
            "dispatched": 0,
            "errors": 0,
        }

    @property
    def running(self) -> bool:
        return self._running

    def set_callback(self, callback: MessageCallback) -> None:
        """设置消息回调（可在运行时替换）。"""
        self._on_message = callback

    def start(self) -> None:
        """启动监听器（标记为运行中，等待外部推送消息）。"""
        with self._lock:
            self._running = True
        logger.info("WeChatListener: 已启动，等待消息推送")

    def stop(self) -> None:
        """停止监听器。"""
        with self._lock:
            self._running = False
        logger.info("WeChatListener: 已停止")

    def on_raw_message(self, raw: dict[str, Any]) -> bool:
        """处理原始消息（由 HTTP 回调或轮询调用）。

        Args:
            raw: 原始消息字典，字段格式参考 WeChatFerry/wcf 协议

        Returns:
            True 表示消息已分发，False 表示被过滤/去重
        """
        if not self._running:
            return False

        try:
            msg = self._parse_raw(raw)
            if msg is None:
                return False
            return self._process(msg)
        except Exception as exc:
            self._stats["errors"] += 1
            logger.warning("WeChatListener: 处理消息异常: %s", exc)
            return False

    def _parse_raw(self, raw: dict[str, Any]) -> WeChatMessage | None:
        """将原始字典解析为 WeChatMessage。"""
        # 兼容 WeChatFerry (wcf) 和常见 Hook 工具的字段格式
        msg_id = str(
            raw.get("id") or raw.get("msg_id") or raw.get("msgId") or ""
        ).strip()
        from_wxid = str(
            raw.get("sender") or raw.get("from_wxid") or raw.get("fromUser") or ""
        ).strip()
        to_wxid = str(
            raw.get("receiver") or raw.get("to_wxid") or raw.get("toUser") or ""
        ).strip()
        content = str(
            raw.get("content") or raw.get("text") or raw.get("msg") or ""
        ).strip()
        room_wxid = str(
            raw.get("roomid") or raw.get("room_wxid") or raw.get("chatRoom") or ""
        ).strip() or None

        # 消息类型映射
        raw_type = str(raw.get("type") or raw.get("msg_type") or "1").strip()
        msg_type = self._map_msg_type(raw_type)

        ts_raw = raw.get("ts") or raw.get("ts_ms") or raw.get("timestamp")
        if ts_raw:
            ts_ms = int(float(ts_raw))
            # 如果是秒级时间戳，转换为毫秒
            if ts_ms < 10_000_000_000:
                ts_ms *= 1000
        else:
            ts_ms = int(time.time() * 1000)

        if not from_wxid or not content:
            return None

        # 自动生成 msg_id（如果没有）
        if not msg_id:
            msg_id = f"{from_wxid}_{ts_ms}"

        raw_extra_payload = {
            k: v for k, v in raw.items() if k not in (
                "id", "msg_id", "sender", "receiver", "content", "roomid", "type", "ts"
            )
        }
        nested_extra = raw.get("raw_extra")
        if isinstance(nested_extra, dict):
            for extra_key, extra_value in nested_extra.items():
                if extra_key not in raw_extra_payload:
                    raw_extra_payload[extra_key] = extra_value

        return WeChatMessage(
            msg_id=msg_id,
            from_wxid=from_wxid,
            to_wxid=to_wxid,
            content=content,
            msg_type=msg_type,
            room_wxid=room_wxid,
            ts_ms=ts_ms,
            raw_extra=raw_extra_payload,
        )

    def _process(self, msg: WeChatMessage) -> bool:
        """过滤、去重、脱敏，然后分发。"""
        self._stats["received"] += 1

        # ── 过滤 ──
        if not self._should_process(msg):
            self._stats["filtered"] += 1
            return False

        # ── 去重 ──
        if self._dedup.is_duplicate(msg.msg_id):
            self._stats["deduplicated"] += 1
            logger.debug("WeChatListener: 重复消息已跳过 msg_id=%s", msg.msg_id)
            return False

        # ── 脱敏 ──
        if self._config.enable_desensitize:
            msg = WeChatMessage(
                msg_id=msg.msg_id,
                from_wxid=msg.from_wxid,
                to_wxid=msg.to_wxid,
                content=desensitize_content(msg.content),
                msg_type=msg.msg_type,
                room_wxid=msg.room_wxid,
                ts_ms=msg.ts_ms,
                raw_extra=msg.raw_extra,
            )

        # ── 分发 ──
        if self._on_message is not None:
            try:
                self._on_message(msg)
                self._stats["dispatched"] += 1
                return True
            except Exception as exc:
                self._stats["errors"] += 1
                logger.warning("WeChatListener: 回调异常: %s", exc)
                return False

        self._stats["dispatched"] += 1
        return True

    def _should_process(self, msg: WeChatMessage) -> bool:
        """判断消息是否需要处理。"""
        # 过滤系统消息
        if msg.msg_type == MSG_TYPE_SYSTEM:
            return False
        # 只处理文本
        if self._config.text_only and msg.msg_type != MSG_TYPE_TEXT:
            return False
        # 只处理私聊
        if self._config.private_only and msg.is_group_msg():
            return False
        # 过滤自己发的消息（小号发出的消息不上报）
        if self._config.self_wxid and msg.from_wxid == self._config.self_wxid:
            return False
        return True

    @staticmethod
    def _map_msg_type(raw_type: str) -> str:
        """将 wcf 数字类型映射为字符串类型。"""
        _TYPE_MAP = {
            "1": MSG_TYPE_TEXT,
            "3": MSG_TYPE_IMAGE,
            "34": MSG_TYPE_VOICE,
            "43": MSG_TYPE_VIDEO,
            "49": MSG_TYPE_LINK,
            "10000": MSG_TYPE_SYSTEM,
            "10002": MSG_TYPE_SYSTEM,
        }
        return _TYPE_MAP.get(raw_type, MSG_TYPE_TEXT)

    def health(self) -> dict[str, Any]:
        """返回监听器健康快照。"""
        return {
            "running": self._running,
            "config": {
                "self_wxid": self._config.self_wxid,
                "private_only": self._config.private_only,
                "text_only": self._config.text_only,
                "dedup_window_s": self._config.dedup_window_s,
                "enable_desensitize": self._config.enable_desensitize,
            },
            "stats": dict(self._stats),
        }
