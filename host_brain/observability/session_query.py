"""
SessionQueryService — M18-B
从 SQLite Ledger 查询会话列表，支持分页 + 过滤。
FR-OBS-009 / TASK-0189
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Natsume.obs.session")


class SessionQueryService:
    """
    从 SQLite Ledger 的 governor_log 聚合会话列表。
    支持过滤：start_time / end_time / status / channel / keyword
    支持分页：page / page_size
    """

    def __init__(self, ledger):
        self._ledger = ledger

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def query(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        channel: Optional[str] = None,
        keyword: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        time_range: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        查询会话列表。
        :param page: 页码（从 1 开始）
        :param page_size: 每页条数（最大 100）
        :param status: 'ok' | 'rejected' | 'error'（不区分大小写）
        :param channel: 通道过滤（voice/wechat/api）
        :param keyword: 模糊匹配 request_id
        :param start_time: 起始时间戳（ms）
        :param end_time: 结束时间戳（ms）
        :param time_range: '1h'|'6h'|'24h'|'7d'（与 start/end_time 互斥，优先级低）
        :return: {total, page, page_size, items}
        """
        now_ms = int(time.time() * 1000)

        # 时间范围解析
        if start_time is None and time_range:
            _range_map = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
            seconds = _range_map.get(time_range, 86400)
            start_time = now_ms - seconds * 1000
        if end_time is None:
            end_time = now_ms

        # 从 governor_log 拉取数据
        try:
            rows = self._fetch_governor_rows(start_ms=start_time or 0, end_ms=end_time)
        except Exception as exc:
            logger.warning("SessionQuery: 读取 governor_log 失败 - %s", exc)
            rows = []

        try:
            intent_rows = self._fetch_intent_rows(start_ms=start_time or 0, end_ms=end_time)
        except Exception as exc:
            logger.warning("SessionQuery: 读取 intent_log 失败 - %s", exc)
            intent_rows = []

        latest_intent_by_request: Dict[str, Dict[str, Any]] = {}
        for row in intent_rows:
            request_id = str(row.get("request_id") or "").strip()
            if not request_id:
                continue
            if request_id not in latest_intent_by_request or int(row.get("ts_ms") or 0) > int(latest_intent_by_request[request_id].get("ts_ms") or 0):
                latest_intent_by_request[request_id] = row

        # 去重（同一 request_id 取最新记录）
        seen: Dict[str, Dict] = {}
        for r in rows:
            rid = r.get("request_id", "")
            if not rid:
                continue
            intent_row = latest_intent_by_request.get(str(rid))
            if intent_row is not None:
                if not r.get("session_id") and intent_row.get("session_id"):
                    r = {**r, "session_id": intent_row.get("session_id")}
                input_preview = self._extract_intent_preview(intent_row)
                if input_preview:
                    r = {**r, "input_preview": input_preview}
            if rid not in seen or r.get("ts_ms", 0) > seen[rid].get("ts_ms", 0):
                seen[rid] = r

        items_raw = sorted(seen.values(), key=lambda x: x.get("ts_ms", 0), reverse=True)

        # 过滤
        items_raw = self._apply_filters(items_raw, status, channel, keyword)

        total = len(items_raw)
        page_size = min(max(1, page_size), 100)
        page = max(1, page)
        offset = (page - 1) * page_size
        page_items = items_raw[offset: offset + page_size]

        return {
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "items":     [self._format_item(r) for r in page_items],
            "timestamp": now_ms,
        }

    def _fetch_governor_rows(self, start_ms: int, end_ms: int) -> List[Dict]:
        """Fetch all governor rows in the time window with paging.

        SQLiteLedger.fetch_by_time_range defaults to limit=100, which can hide
        recent sessions when the window has more rows. We page through the full
        window explicitly.
        """
        page_size = 10_000
        offset = 0
        rows: List[Dict] = []
        while True:
            chunk = self._ledger.fetch_by_time_range(
                "governor_log",
                start_ms=int(start_ms),
                end_ms=int(end_ms),
                limit=page_size,
                offset=offset,
            )
            if not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size
        return rows

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _apply_filters(
        self,
        items: List[Dict],
        status: Optional[str],
        channel: Optional[str],
        keyword: Optional[str],
    ) -> List[Dict]:
        if status:
            s = status.lower()
            if s == "ok":
                items = [r for r in items if r.get("decision") == "APPROVE"]
            elif s == "rejected":
                items = [r for r in items if r.get("decision") == "REJECT"]
            elif s == "error":
                items = [r for r in items if r.get("decision") not in ("APPROVE", "REJECT")]

        if channel:
            items = [r for r in items if r.get("channel", "").lower() == channel.lower()]

        if keyword:
            kw = keyword.lower()
            items = [
                r for r in items
                if kw in r.get("request_id", "").lower()
                or kw in str(r.get("session_id", "")).lower()
                or kw in str(r.get("input_preview", "")).lower()
            ]

        return items

    @staticmethod
    def _format_item(r: Dict) -> Dict[str, Any]:
        decision = r.get("decision", "")
        if decision == "APPROVE":
            status = "ok"
        elif decision == "REJECT":
            status = "rejected"
        else:
            status = "error"

        return {
            "request_id":   r.get("request_id", ""),
            "session_id":   r.get("session_id"),
            "ts_ms":        r.get("ts_ms", 0),
            "created_at":   _fmt_ts(r.get("ts_ms")),
            "status":       status,
            "decision":     decision,
            "reason_code":  r.get("reason_code"),
            "channel":      r.get("channel"),
            "duration_ms":  r.get("duration_ms") or r.get("total_latency_ms"),
            "input_preview": r.get("input_preview"),
        }

    def _fetch_intent_rows(self, start_ms: int, end_ms: int) -> List[Dict]:
        page_size = 10_000
        offset = 0
        rows: List[Dict] = []
        while True:
            chunk = self._ledger.fetch_by_time_range(
                "intent_log",
                start_ms=int(start_ms),
                end_ms=int(end_ms),
                limit=page_size,
                offset=offset,
            )
            if not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size
        return rows

    @staticmethod
    def _extract_intent_preview(row: Dict[str, Any]) -> Optional[str]:
        normalized = row.get("normalized_json")
        raw_text = row.get("raw_text")
        if isinstance(raw_text, str) and raw_text.strip():
            return raw_text.strip()[:120]

        if isinstance(normalized, dict):
            args = normalized.get("args")
            if isinstance(args, dict):
                for key in ("text", "query", "message", "content"):
                    value = args.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()[:120]
        return None


def _fmt_ts(ts_ms: Optional[int]) -> Optional[str]:
    if ts_ms is None:
        return None
    import datetime
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
