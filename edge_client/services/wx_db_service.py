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
import hashlib
import os
import re
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
    # 这是对“已由外部工具解密”的本地库的只读查询面，不持有解密密钥也不直接
    # 发送微信消息。其输出进入 Gateway 前仍要经过适配器净化与持久投递边界。
    """微信数据库查询服务。

    Args:
        decrypt_output_dir: wx4_decrypt.py 的解密输出目录（包含 de_*.db 文件）
        poll_watermark_file: 增量轮询水位线持久化文件路径（None 表示不持久化）
    """

    # 解密后的数据库文件名前缀
    _CONTACT_DB_CANDIDATES = ("de_MicroMsg.db", "de_contact.db")
    _SNS_DB_CANDIDATES = ("de_Sns.db", "de_sns.db")
    _SESSION_DB_CANDIDATES = ("de_session.db",)
    _MSG_DB_PATTERN = "de_message_"
    _MSG_DB_REGEX = re.compile(r"^de_message_\d+\.db$", re.IGNORECASE)

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
        db_path = self._resolve_existing_db(self._CONTACT_DB_CANDIDATES)
        if db_path is None:
            logger.warning("WxDbService: 联系人数据库不存在: %s", db_path)
            return []

        contacts: list[WxContact] = []
        try:
            conn = self._open_db(db_path)
            table_name = self._resolve_contact_table(conn)
            if not table_name:
                logger.warning("WxDbService: 联系人表不存在（Contact/contact）")
                conn.close()
                return []
            columns = self._load_table_columns(conn, table_name)
            username_col = self._pick_first_column(columns, "UserName", "username", "user_name")
            nickname_col = self._pick_first_column(columns, "NickName", "nick_name", "nickname")
            remark_col = self._pick_first_column(columns, "Remark", "remark")
            alias_col = self._pick_first_column(columns, "Alias", "alias")
            if not username_col:
                logger.warning("WxDbService: 联系人表缺少 username 列 table=%s", table_name)
                conn.close()
                return []
            select_sql = (
                f"SELECT {self._quote_ident(username_col)} AS username, "
                f"{self._column_or_empty_expr(nickname_col)} AS nickname, "
                f"{self._column_or_empty_expr(remark_col)} AS remark, "
                f"{self._column_or_empty_expr(alias_col)} AS alias "
                f"FROM {self._quote_ident(table_name)}"
            )
            rows = conn.execute(select_sql).fetchall()
            conn.close()

            for row in rows:
                wxid = row[0]
                nickname = row[1]
                remark = row[2]
                alias = row[3]
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
        db_path = self._resolve_existing_db(self._CONTACT_DB_CANDIDATES)
        if db_path is None:
            return None
        try:
            conn = self._open_db(db_path)
            table_name = self._resolve_contact_table(conn)
            if not table_name:
                conn.close()
                return None
            columns = self._load_table_columns(conn, table_name)
            username_col = self._pick_first_column(columns, "UserName", "username", "user_name")
            nickname_col = self._pick_first_column(columns, "NickName", "nick_name", "nickname")
            remark_col = self._pick_first_column(columns, "Remark", "remark")
            alias_col = self._pick_first_column(columns, "Alias", "alias")
            if not username_col:
                conn.close()
                return None
            select_sql = (
                f"SELECT {self._quote_ident(username_col)} AS username, "
                f"{self._column_or_empty_expr(nickname_col)} AS nickname, "
                f"{self._column_or_empty_expr(remark_col)} AS remark, "
                f"{self._column_or_empty_expr(alias_col)} AS alias "
                f"FROM {self._quote_ident(table_name)} "
                f"WHERE {self._quote_ident(username_col)}=?"
            )
            row = conn.execute(select_sql, (wxid,)).fetchone()
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

    def get_contact_detail(self, wxid: str) -> dict[str, Any] | None:
        """兼容接口：返回联系人详情字典。"""
        contact = self.get_contact(wxid)
        if contact is None:
            return None
        return {
            "wxid": contact.wxid,
            "nickname": contact.nickname,
            "remark": contact.remark,
            "alias": contact.alias,
            "avatar_url": contact.avatar_url,
            "is_group": contact.is_group,
        }

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
        # 分片库可能在刷新时短暂不可读；逐库隔离失败后合并可用结果，避免一个
        # 损坏分片阻塞全部历史查询。limit 在全局排序后裁剪，维持时间语义。
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
        table_conv_map = self._load_msg_table_conversation_map()
        sender_name_map = self._load_name2id_map()

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
                    table_conv_map=table_conv_map,
                    sender_name_map=sender_name_map,
                )
                messages.extend(msgs)
            except Exception as exc:
                logger.warning("WxDbService: 查询 %s 失败: %s", db_path.name, exc)

        # 按时间升序排序，截取 limit 条
        messages.sort(key=lambda m: m.ts_ms)
        return messages[-limit:] if len(messages) > limit else messages

    def search_messages(self, *, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """兼容接口：按关键词搜索消息。"""
        rows = self.get_messages(keyword=keyword, limit=limit)
        return [self._wx_message_to_dict(row) for row in rows]

    def is_top_session(self, conversation_id: str) -> bool:
        """判断目标会话是否位于最近会话首位。"""
        target = str(conversation_id or "").strip()
        if not target:
            return False
        session_db = self._resolve_existing_db(self._SESSION_DB_CANDIDATES)
        if session_db is None:
            return False
        try:
            conn = self._open_db(session_db)
            row = conn.execute(
                "SELECT username FROM SessionTable WHERE is_hidden=0 "
                "ORDER BY sort_timestamp DESC LIMIT 1"
            ).fetchone()
            conn.close()
        except Exception as exc:
            logger.debug("WxDbService: is_top_session failed conversation=%s err=%s", target, exc)
            return False
        if not row:
            return False
        return str(row[0] or "").strip() == target

    def refresh(self) -> dict[str, Any]:
        """兼容接口：refresh 由外层调度器触发。"""
        return {
            "success": False,
            "reason": "NOT_CONFIGURED",
            "detail": "refresh is managed by WxDecryptRefresher or /v1/edge/wx/refresh",
        }

    def poll_new_messages(self) -> list[WxMessage]:
        # 水位线是“已观察到”的恢复位置，不是 Host 已接收确认。首次启动只推进
        # 到当前末尾而不回放历史，重启后则从持久水位继续，避免大量旧消息触发。
        """增量轮询新消息（基于 rowid 水位线）。

        每次调用只返回上次调用之后新增的消息，
        配合 watchdog 文件监听可实现 <100ms 实时性。

        Returns:
            新消息列表（按时间升序）
        """
        new_messages: list[WxMessage] = []
        msg_dbs = self._find_message_dbs()
        table_conv_map = self._load_msg_table_conversation_map()
        sender_name_map = self._load_name2id_map()
        self_wxid = self._infer_self_wxid()

        for db_path in msg_dbs:
            try:
                conn = self._open_db(db_path)
                table_names = self._list_message_tables(conn)
                is_legacy = "MSG" in table_names

                if is_legacy:
                    db_key = db_path.name
                    with self._lock:
                        last_rowid = self._watermarks.get(db_key, 0)
                    bootstrap_mode = int(last_rowid or 0) <= 0
                    rows = conn.execute(
                        "SELECT rowid, MsgSvrID, StrTalker, IsSender, Type, "
                        "StrContent, CreateTime FROM MSG "
                        "WHERE rowid > ? ORDER BY rowid ASC",
                        (last_rowid,),
                    ).fetchall()
                    if rows:
                        max_rowid = last_rowid
                        for row in rows:
                            rowid, msg_svr_id, str_talker, is_sender, msg_type, content, create_time = row
                            max_rowid = max(max_rowid, int(rowid or 0))
                            if not bootstrap_mode:
                                ts_ms = int(create_time) * 1000 if create_time else int(time.time() * 1000)
                                new_messages.append(WxMessage(
                                    msg_id=str(msg_svr_id or rowid),
                                    from_wxid="" if is_sender else str(str_talker or ""),
                                    to_wxid=str(str_talker or "") if is_sender else "",
                                    conversation_id=str(str_talker or ""),
                                    content=str(content or ""),
                                    msg_type=int(msg_type or 1),
                                    ts_ms=ts_ms,
                                    is_sender=bool(is_sender),
                                ))
                        with self._lock:
                            # 更新与保存只在完整扫描该表后发生，确保中途查询异常
                            # 不会把尚未处理的尾部行永久跳过。
                            self._watermarks[db_key] = max_rowid
                        self._save_watermarks()
                else:
                    shard_tables = [name for name in table_names if name.startswith("Msg_")]
                    for table_name in shard_tables:
                        watermark_key = f"{db_path.name}::{table_name}"
                        with self._lock:
                            last_local_id = int(self._watermarks.get(watermark_key, 0) or 0)
                        bootstrap_mode = last_local_id <= 0
                        rows = conn.execute(
                            f"SELECT local_id, server_id, local_type, create_time, message_content, real_sender_id "
                            f"FROM '{table_name}' WHERE local_id > ? ORDER BY local_id ASC",
                            (last_local_id,),
                        ).fetchall()
                        if not rows:
                            continue

                        conversation_id = table_conv_map.get(table_name, "")
                        if not conversation_id:
                            # 无法将匿名分表映射到稳定会话时只推进水位，不把消息
                            # 送入默认/错误会话造成误路由。
                            logger.debug(
                                "WxDbService: skip table without conversation mapping db=%s table=%s rows=%d",
                                db_path.name,
                                table_name,
                                len(rows),
                            )
                            max_local_id = max(int(last_local_id), max(int(row[0] or 0) for row in rows))
                            with self._lock:
                                self._watermarks[watermark_key] = max_local_id
                            self._save_watermarks()
                            continue

                        max_local_id = last_local_id
                        for row in rows:
                            local_id, server_id, local_type, create_time, message_content, real_sender_id = row
                            local_id_int = int(local_id or 0)
                            max_local_id = max(max_local_id, local_id_int)
                            sender_name = sender_name_map.get(int(real_sender_id or 0), "")
                            if not bootstrap_mode:
                                ts_ms = int(create_time) * 1000 if create_time else int(time.time() * 1000)
                                text = self._normalize_message_content(message_content)
                                is_sender = bool(self_wxid and sender_name and sender_name == self_wxid)
                                new_messages.append(WxMessage(
                                    msg_id=str(server_id or f"{table_name}:{local_id_int}"),
                                    from_wxid=sender_name,
                                    to_wxid=conversation_id if is_sender else "",
                                    conversation_id=conversation_id,
                                    content=text,
                                    msg_type=int(local_type or 1),
                                    ts_ms=ts_ms,
                                    is_sender=is_sender,
                                ))

                        with self._lock:
                            self._watermarks[watermark_key] = max_local_id
                        self._save_watermarks()
                conn.close()
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
        db_path = self._resolve_existing_db(self._SNS_DB_CANDIDATES)
        if db_path is None:
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
        contact_db = self._resolve_existing_db(self._CONTACT_DB_CANDIDATES)
        sns_db = self._resolve_existing_db(self._SNS_DB_CANDIDATES)

        return {
            "decrypt_output_dir": str(self._dir),
            "contact_db_exists": contact_db is not None,
            "sns_db_exists": sns_db is not None,
            "message_db_count": len(msg_dbs),
            "message_dbs": [db.name for db in msg_dbs],
            "watermarks": dict(self._watermarks),
        }

    @staticmethod
    def _wx_message_to_dict(msg: WxMessage) -> dict[str, Any]:
        return {
            "msg_id": msg.msg_id,
            "from_wxid": msg.from_wxid,
            "to_wxid": msg.to_wxid,
            "conversation_id": msg.conversation_id,
            "content": msg.content,
            "msg_type": msg.msg_type,
            "ts_ms": msg.ts_ms,
            "is_sender": msg.is_sender,
            "media_url": msg.media_url,
            "raw": msg.raw,
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
            if p.suffix == ".db" and bool(self._MSG_DB_REGEX.match(p.name))
        )

    def _resolve_existing_db(self, candidates: tuple[str, ...]) -> Path | None:
        for name in candidates:
            path = self._dir / name
            if path.exists():
                return path
        return None

    @staticmethod
    def _open_db(db_path: Path) -> sqlite3.Connection:
        # 使用 SQLite URI 的只读模式，服务层不能改写正在被微信或解密刷新器使用
        # 的源库；所有可恢复状态仅写入独立水位文件。
        """以只读模式打开 SQLite 数据库。"""
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _list_message_tables(conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _quote_ident(name: str) -> str:
        return '"' + str(name or "").replace('"', '""') + '"'

    @staticmethod
    def _pick_first_column(columns: set[str], *candidates: str) -> str | None:
        lowered = {c.lower() for c in columns}
        for candidate in candidates:
            if candidate.lower() in lowered:
                for raw in columns:
                    if raw.lower() == candidate.lower():
                        return raw
        return None

    @staticmethod
    def _column_or_empty_expr(column: str | None) -> str:
        if not column:
            return "''"
        return WxDbService._quote_ident(column)

    @staticmethod
    def _load_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(
            f"PRAGMA table_info({WxDbService._quote_ident(table_name)})"
        ).fetchall()
        result: set[str] = set()
        for row in rows:
            if len(row) > 1:
                result.add(str(row[1]))
        return result

    @staticmethod
    def _resolve_contact_table(conn: sqlite3.Connection) -> str | None:
        tables = WxDbService._list_message_tables(conn)
        if "Contact" in tables:
            return "Contact"
        if "contact" in tables:
            return "contact"
        return None

    def _load_msg_table_conversation_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        session_db = self._resolve_existing_db(self._SESSION_DB_CANDIDATES)
        if session_db is None:
            return mapping
        try:
            conn = self._open_db(session_db)
            rows = conn.execute(
                "SELECT username FROM SessionTable WHERE username IS NOT NULL AND username != ''"
            ).fetchall()
            conn.close()
        except Exception:
            return mapping
        for row in rows:
            username = str(row[0] or "").strip()
            if not username:
                continue
            digest = hashlib.md5(username.encode("utf-8")).hexdigest()
            mapping[f"Msg_{digest}"] = username
        return mapping

    def _load_name2id_map(self) -> dict[int, str]:
        mapping: dict[int, str] = {}
        session_db = self._resolve_existing_db(self._SESSION_DB_CANDIDATES)
        if session_db is None:
            return mapping
        try:
            conn = self._open_db(session_db)
            rows = conn.execute(
                "SELECT rowid, user_name FROM Name2Id"
            ).fetchall()
            conn.close()
        except Exception:
            return mapping
        for row in rows:
            try:
                key = int(row[0])
            except Exception:
                continue
            mapping[key] = str(row[1] or "")
        return mapping

    @staticmethod
    def _normalize_message_content(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, bytes):
            for enc in ("utf-8", "utf-16-le", "gb18030", "latin1"):
                try:
                    text = raw.decode(enc, errors="ignore").strip()
                    if text:
                        return text
                except Exception:
                    continue
            return ""
        return str(raw).strip()

    def _infer_self_wxid(self) -> str | None:
        folder_name = self._dir.parent.name
        if folder_name.startswith("wxid_"):
            return folder_name.split("_e", 1)[0]
        return None

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
        table_conv_map: dict[str, str],
        sender_name_map: dict[int, str],
    ) -> list[WxMessage]:
        """从单个消息数据库查询消息。"""
        conn = self._open_db(db_path)
        try:
            table_names = self._list_message_tables(conn)
            self_wxid = self._infer_self_wxid()

            if "MSG" in table_names:
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
                        from_wxid="" if is_sender else str(str_talker or ""),
                        to_wxid=str(str_talker or "") if is_sender else "",
                        conversation_id=str(str_talker or ""),
                        content=str(content or ""),
                        msg_type=int(msg_type or 1),
                        ts_ms=ts_ms,
                        is_sender=bool(is_sender),
                    ))
                return messages

            messages: list[WxMessage] = []
            shard_tables = [name for name in table_names if name.startswith("Msg_")]
            for table_name in shard_tables:
                conv_id = table_conv_map.get(table_name, "")
                if conversation_id and conv_id != conversation_id:
                    continue

                conditions: list[str] = []
                params: list[Any] = []
                if before_ts_ms:
                    conditions.append("create_time < ?")
                    params.append(before_ts_ms // 1000)
                if after_ts_ms:
                    conditions.append("create_time > ?")
                    params.append(after_ts_ms // 1000)
                if keyword:
                    conditions.append("message_content LIKE ?")
                    params.append(f"%{keyword}%")
                if msg_types:
                    placeholders = ",".join("?" * len(msg_types))
                    conditions.append(f"local_type IN ({placeholders})")
                    params.extend(msg_types)
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                params.append(limit)

                rows = conn.execute(
                    f"SELECT local_id, server_id, local_type, create_time, message_content, real_sender_id "
                    f"FROM '{table_name}' {where} ORDER BY create_time DESC LIMIT ?",
                    params,
                ).fetchall()
                for row in rows:
                    local_id, server_id, local_type, create_time, message_content, real_sender_id = row
                    sender = sender_name_map.get(int(real_sender_id or 0), "")
                    is_sender = bool(self_wxid and sender and sender == self_wxid)
                    ts_ms = int(create_time) * 1000 if create_time else 0
                    messages.append(WxMessage(
                        msg_id=str(server_id or f"{table_name}:{int(local_id or 0)}"),
                        from_wxid=sender,
                        to_wxid=conv_id if is_sender else "",
                        conversation_id=conv_id,
                        content=self._normalize_message_content(message_content),
                        msg_type=int(local_type or 1),
                        ts_ms=ts_ms,
                        is_sender=is_sender,
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
