"""Unified Message Gateway — 核心数据模型。

定义跨平台统一消息信封 UnifiedMessage 和统一回复 UnifiedReply，
以及平台枚举 Platform 和消息类型枚举 MsgType。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Platform(str, Enum):
    """支持的外部消息平台。"""
    WECHAT = "wechat"
    QQ = "qq"
    FEISHU = "feishu"
    WECOM = "wecom"
    TELEGRAM = "telegram"
    LINE = "line"


class MsgType(str, Enum):
    """统一消息类型。"""
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    FILE = "file"
    LINK = "link"
    EMOJI = "emoji"
    SYSTEM = "system"
    UNKNOWN = "unknown"
    # Phase 4: 通话动作（不是消息类型，而是通过 Gateway reply 触发的操作）
    VOICE_CALL = "voice_call"
    VIDEO_CALL = "video_call"
    CANCEL_CALL = "cancel_call"
    # Phase XV: 社交生态动作
    MOMENTS = "moments"              # 发朋友圈/动态
    STATUS = "status"                # 更新状态
    MOMENTS_COMMENT = "moments_comment"  # 朋友圈评论

    @classmethod
    def from_wx_type(cls, wx_type: int | str) -> "MsgType":
        """将微信消息类型数字映射为 MsgType。

        微信 message_*.db 中 type 字段含义：
          1   → 文本
          3   → 图片
          34  → 语音（.silk）
          43  → 视频
          47  → 表情包
          49  → 链接/小程序/文件
          10000/10002 → 系统消息
        """
        _MAP: dict[str, MsgType] = {
            "1": cls.TEXT,
            "3": cls.IMAGE,
            "34": cls.VOICE,
            "43": cls.VIDEO,
            "47": cls.EMOJI,
            "49": cls.LINK,
            "10000": cls.SYSTEM,
            "10002": cls.SYSTEM,
        }
        return _MAP.get(str(wx_type), cls.UNKNOWN)


@dataclass
class UnifiedMessage:
    """跨平台统一消息信封。

    所有平台的消息在进入 Gateway 后都转换为此格式，
    再路由到 Host Brain 处理。
    """
    # ── 必填字段 ──
    msg_id: str                          # 消息唯一 ID（平台原始 ID）
    platform: Platform                   # 来源平台
    from_id: str                         # 发送者 ID（wxid / QQ号 / open_id 等）
    to_id: str                           # 接收者 ID
    conversation_id: str                 # 会话 ID（群 ID 或私聊双方 ID 拼接）
    msg_type: MsgType                    # 消息类型
    content: str                         # 文本内容 / 媒体描述
    ts_ms: int                           # 时间戳（毫秒）

    # ── 可选字段 ──
    media_url: str | None = None         # 媒体文件本地路径或远程 URL
    media_data: bytes | None = None      # 媒体二进制数据（小文件内联）
    reply_to_msg_id: str | None = None   # 引用的消息 ID
    is_group: bool = False               # 是否群消息
    sender_name: str | None = None       # 发送者昵称（显示用）
    raw_extra: dict[str, Any] = field(default_factory=dict)  # 平台原始附加数据

    def dedup_key(self) -> str:
        """去重键：平台 + 消息 ID 组合，避免不同平台 ID 碰撞。"""
        return f"{self.platform.value}:{self.msg_id}"

    def as_report_payload(self) -> dict[str, Any]:
        """转换为上报到 Host Brain 的 payload 格式（兼容 EdgeInputReporter）。"""
        payload: dict[str, Any] = {
            "source": "GATEWAY",
            "platform": self.platform.value,
            "msg_id": self.msg_id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "conversation_id": self.conversation_id,
            "msg_type": self.msg_type.value,
            "content": self.content,
            "is_group": self.is_group,
            "ts_ms": self.ts_ms,
        }
        if self.sender_name:
            payload["sender_name"] = self.sender_name
        if self.media_url:
            payload["media_url"] = self.media_url
        if self.reply_to_msg_id:
            payload["reply_to_msg_id"] = self.reply_to_msg_id
        if self.raw_extra:
            payload["raw_extra"] = self.raw_extra
        return payload


@dataclass
class UnifiedReply:
    """跨平台统一回复。

    Host Brain 处理完消息后，通过此格式将回复交给 Outbound Dispatcher，
    由 Dispatcher 调用对应平台的发送接口。
    """
    conversation_id: str
    platform: Platform
    msg_type: MsgType = MsgType.TEXT
    content: str = ""
    media_data: bytes | None = None      # 媒体二进制（图片/语音等）
    media_path: str | None = None        # 媒体文件本地路径
    reply_to_msg_id: str | None = None   # 引用回复的原始消息 ID
    raw_extra: dict[str, Any] = field(default_factory=dict)
