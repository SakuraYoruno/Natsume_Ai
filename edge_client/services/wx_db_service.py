"""微信数据库查询服务层。

基于 wx4_decrypt.py 解密后的 SQLite 数据库，提供结构化查询接口：
  - 联系人列表及详情
  - 聊天记录（按联系人/时间/关键词）
  - 增量消息轮询（配合 watchdog 实现 <100ms 实时性）
  - 朋友圈读取（Sns.db）

使用前提：
  1. 微信已启动并登录
  2. wx4_decrypt.py 已完成解密，输出目录存在 de_*.db 文件

依赖：
  pip install watchdog
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("Natsume.edge.services.wx_db_service")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class WxContact:
    """微信联系人。"""
    wxid: str
    nickname: str
    remark: str | None = None          # 备注名
    alias: str | None = None           # 微信号
    avatar_url: str | None = None
    is_group: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def display_name(self) -> str:
        """优先返回备注名，其次昵称。"""
        return self.remark or self.nickname or self.wxid


@dataclass
class WxMessage:
    """微信消息记录。"""
    msg_id: str
    from_wxid: str
    to_wxid: str
    conversation_id: str               # 私聊=对方wxid，群聊=群wxid
    content: str
    msg_type: int                      # 原始数字类型（1=文本, 3=图片, 34=语音, 等）
    ts_ms: int                         # 时间戳（毫秒）
    is_sender: bool = False            # True=自己发的
    media_url: str | None = None       # 媒体文件路径（语音/图片等）
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WxMoment:
    """朋友圈条目。"""
    moment_id: str
    wxid: str
    content: str
    ts_ms: int
    media_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# WxDbService
# ---------------------------------------------------------------------------

class WxDbService:
    """微信数据库查询服务。

    Args:
        decrypt_output_dir: wx4_decrypt.py 的解密输出目录（包含 de_*.db 文件）
        poll_watermark_file: 增量轮询水位线持久化文件路径（None 表示不持久化）
    """

    # 解密后的数据库文件名前缀
    _CONTACT_DB = "de_MicroMsg.db"
    _SNS_DB = "de_Sns.db"
    _MSG_DB_PATTERN = "de_message_"

    def __init__(
        self,
        decrypt_output_dir: str | Path,
        *,
        poll_watermark_file: str | Path | None = None,
    ) -> None:
        self._dir = Path(decrypt_output_dir)
        self._watermark_file = Path(poll_watermark_file) if poll_watermark_file else None
        # 增量轮询水位线：{db_filename: last_rowid}
        self._watermarks: dict[str, int] = {}
        self._lock = threading.Lock()
        self._load_watermarks()

    # ------------------------------------------------------------------
    # 联系人
    # ------------------------------------------------------------------

    def get_contacts(self, *, include_groups: bool = True) -> list[WxContact]:
        """获取所有联系人列表。

        Args:
            include_groups: 是否包含群聊

        Returns:
            WxContact 列表
        """
        db_path = self._dir / self._CONTACT_DB
        if not db_path.exists():
            logger.warning("WxDbService: 联系人数据库不存在: %s", db_path)
            return []

        contacts: list[WxContact] = []
        try:
            conn = self._open_db(db_path)
            # MicroMsg.db 中联系人表为 Contact
            rows = conn.execute(
                "SELECT UserName, NickName, Remark, Alias FROM Contact"
            ).fetchall()
            conn.close()

            for row in rows:
                wxid, nickname, remark, alias = row
                is_group = wxid.endswith("@chatroom")
                if not include_groups and is_group:
                    continue
                contacts.append(WxContact(
                    wxid=wxid or "",
                    nickname=nickname or "",
                    remark=remark or None,
                    alias=alias or None,
                    is_group=is_group,
                ))
        except Exception as exc:
            logger.error("WxDbService: 读取联系人失败: %s", exc)

        return contacts

    def get_contact(self, wxid: str) -> WxContact | None:
        """获取单个联系人详情。"""
        db_path = self._dir / self._CONTACT_DB
        if not db_path.exists():
            return None
        try:
            conn = self._open_db(db_path)
            row = conn.execute(
                "SELECT UserName, NickName, Remark, Alias FROM Contact WHERE UserName=?",
                (wxid,),
            ).fetchone()
            conn.close()
            if row:
                wxid_, nickname, remark, alias = row
                return WxContact(
                    wxid=wxid_ or "",
                    nickname=nickname or "",
                    remark=remark or None,
                    alias=alias or None,
                    is_group=wxid_.endswith("@chatroom"),
                )
        except Exception as exc:
            logger.error("WxDbService: 读取联系人 %s 失败: %s", wxid, exc)
        return None

    # ------------------------------------------------------------------
    # 消息查询
    # ------------------------------------------------------------------

    def get_messages(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        before_ts_ms: int | None = None,
        after_ts_ms: int | None = None,
        keyword: str | None = None,
        msg_types: list[int] | None = None,
    ) -> list[WxMessage]:
        """查询聊天记录。

        Args:
            conversation_id: 会话 ID（wxid 或群 wxid），None 表示查询所有
            limit: 最多返回条数
            before_ts_ms: 只返回此时间戳之前的消息
            after_ts_ms: 只返回此时间戳之后的消息
            keyword: 关键词搜索（仅文本消息）
            msg_types: 消息类型过滤（如 [1, 3] 表示只要文本和图片）

        Returns:
            WxMessage 列表（按时间升序）
        """
        messages: list[WxMessage] = []
        msg_dbs = self._find_message_dbs()

        for db_path in msg_dbs:
            try:
                msgs = self._query_messages_from_db(
                    db_path,
                    conversation_id=conversation_id,
                    limit=limit,
                    before_ts_ms=before_ts_ms,
                    after_ts_ms=after_ts_ms,
                    keyword=keyword,
                    msg_types=msg_types,
                )
                messages.extend(msgs)
            except Exception as exc:
                logger.warning("WxDbService: 查询 %s 失败: %s", db_path.name, exc)

        # 按时间升序排序，截取 limit 条
        messages.sort(key=lambda m: m.ts_ms)
        return messages[-limit:] if len(messages) > limit else messages

    def poll_new_messages(self) -> list[WxMessage]:
        """增量轮询新消息（基于 rowid 水位线）。

        每次调用只返回上次调用之后新增的消息，
        配合 watchdog 文件监听可实现 <100ms 实时性。

        Returns:
            新消息列表（按时间升序）
        """
        new_messages: list[WxMessage] = []
        msg_dbs = self._find_message_dbs()

        for db_path in msg_dbs:
            db_key = db_path.name
            with self._lock:
                last_rowid = self._watermarks.get(db_key, 0)

            try:
                conn = self._open_db(db_path)
                # 查询 rowid > 水位线的新消息
                rows = conn.execute(
                    "SELECT rowid, MsgSvrID, StrTalker, IsSender, Type, "
                    "StrContent, CreateTime FROM MSG "
                    "WHERE rowid > ? ORDER BY rowid ASC",
                    (last_rowid,),
                ).fetchall()
                conn.close()

                if rows:
                    max_rowid = last_rowid
                    for row in rows:
                        rowid, msg_svr_id, str_talker, is_sender, msg_type, content, create_time = row
                        max_rowid = max(max_rowid, rowid)

                        # create_time 是秒级时间戳
                        ts_ms = int(create_time) * 1000 if create_time else int(time.time() * 1000)

                        new_messages.append(WxMessage(
                            msg_id=str(msg_svr_id or rowid),
                            from_wxid="" if is_sender else str_talker,
                            to_wxid=str_talker if is_sender else "",
                            conversation_id=str_talker or "",
                            content=str(content or ""),
                            msg_type=int(msg_type or 1),
                            ts_ms=ts_ms,
                            is_sender=bool(is_sender),
                        ))

                    # 更新水位线
                    with self._lock:
                        self._watermarks[db_key] = max_rowid
                    self._save_watermarks()

            except Exception as exc:
                logger.warning("WxDbService: 增量轮询 %s 失败: %s", db_path.name, exc)

        new_messages.sort(key=lambda m: m.ts_ms)
        return new_messages

    # ------------------------------------------------------------------
    # 朋友圈
    # ------------------------------------------------------------------

    def get_moments(self, *, limit: int = 20) -> list[WxMoment]:
        """读取朋友圈（Sns.db → SnsInfo 表）。

        Args:
            limit: 最多返回条数

        Returns:
            WxMoment 列表（按时间降序）
        """
        db_path = self._dir / self._SNS_DB
        if not db_path.exists():
            logger.warning("WxDbService: 朋友圈数据库不存在: %s", db_path)
            return []

        moments: list[WxMoment] = []
        try:
            conn = self._open_db(db_path)
            rows = conn.execute(
                "SELECT FeedId, UserName, Content, CreateTime "
                "FROM SnsInfo ORDER BY CreateTime DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()

            for row in rows:
                feed_id, username, content, create_time = row
                ts_ms = int(create_time) * 1000 if create_time else 0
                moments.append(WxMoment(
                    moment_id=str(feed_id or ""),
                    wxid=str(username or ""),
                    content=str(content or ""),
                    ts_ms=ts_ms,
                ))
        except Exception as exc:
            logger.error("WxDbService: 读取朋友圈失败: %s", exc)

        return moments

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """返回数据库统计信息。"""
        msg_dbs = self._find_message_dbs()
        contact_db = self._dir / self._CONTACT_DB
        sns_db = self._dir / self._SNS_DB

        return {
            "decrypt_output_dir": str(self._dir),
            "contact_db_exists": contact_db.exists(),
            "sns_db_exists": sns_db.exists(),
            "message_db_count": len(msg_dbs),
            "message_dbs": [db.name for db in msg_dbs],
            "watermarks": dict(self._watermarks),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _find_message_dbs(self) -> list[Path]:
        """查找所有解密后的消息数据库文件。"""
        if not self._dir.exists():
            return []
        return sorted(
            p for p in self._dir.iterdir()
            if p.name.startswith(self._MSG_DB_PATTERN) and p.suffix == ".db"
        )

    @staticmethod
    def _open_db(db_path: Path) -> sqlite3.Connection:
        """以只读模式打开 SQLite 数据库。"""
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _query_messages_from_db(
        self,
        db_path: Path,
        *,
        conversation_id: str | None,
        limit: int,
        before_ts_ms: int | None,
        after_ts_ms: int | None,
        keyword: str | None,
        msg_types: list[int] | None,
    ) -> list[WxMessage]:
        """从单个消息数据库查询消息。"""
        conn = self._open_db(db_path)
        try:
            conditions: list[str] = []
            params: list[Any] = []

            if conversation_id:
                conditions.append("StrTalker = ?")
                params.append(conversation_id)
            if before_ts_ms:
                conditions.append("CreateTime < ?")
                params.append(before_ts_ms // 1000)
            if after_ts_ms:
                conditions.append("CreateTime > ?")
                params.append(after_ts_ms // 1000)
            if keyword:
                conditions.append("StrContent LIKE ?")
                params.append(f"%{keyword}%")
            if msg_types:
                placeholders = ",".join("?" * len(msg_types))
                conditions.append(f"Type IN ({placeholders})")
                params.extend(msg_types)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)

            rows = conn.execute(
                f"SELECT MsgSvrID, StrTalker, IsSender, Type, StrContent, CreateTime "
                f"FROM MSG {where} ORDER BY CreateTime DESC LIMIT ?",
                params,
            ).fetchall()

            messages: list[WxMessage] = []
            for row in rows:
                msg_svr_id, str_talker, is_sender, msg_type, content, create_time = (
                    row[0], row[1], row[2], row[3], row[4], row[5]
                )
                ts_ms = int(create_time) * 1000 if create_time else 0
                messages.append(WxMessage(
                    msg_id=str(msg_svr_id or ""),
                    from_wxid="" if is_sender else str_talker,
                    to_wxid=str_talker if is_sender else "",
                    conversation_id=str_talker or "",
                    content=str(content or ""),
                    msg_type=int(msg_type or 1),
                    ts_ms=ts_ms,
                    is_sender=bool(is_sender),
                ))
            return messages
        finally:
            conn.close()

    def _load_watermarks(self) -> None:
        """从文件加载水位线（持久化支持）。"""
        if self._watermark_file is None or not self._watermark_file.exists():
            return
        try:
            import json
            with open(self._watermark_file, "r", encoding="utf-8") as f:
                self._watermarks = json.load(f)
        except Exception as exc:
            logger.warning("WxDbService: 加载水位线失败: %s", exc)

    def _save_watermarks(self) -> None:
        """将水位线持久化到文件。"""
        if self._watermark_file is None:
            return
        try:
            import json
            with self._lock:
                data = dict(self._watermarks)
            with open(self._watermark_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as exc:
            logger.warning("WxDbService: 保存水位线失败: %s", exc)
