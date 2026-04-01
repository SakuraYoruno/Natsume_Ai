"""
SessionQueryService — M18-B
从 SQLite Ledger 查询会话列表，支持分页与过滤。
"""

from __future__ import annotations

import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Natsume.obs.session")


class SessionQueryService:
    """从 governor/intention/voice 审计表聚合会话列表。"""

    def __init__(self, ledger):
        self._ledger = ledger

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
        now_ms = int(time.time() * 1000)

        if start_time is None and time_range:
            range_map = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
            seconds = range_map.get(time_range, 86400)
            start_time = now_ms - seconds * 1000
        if end_time is None:
            end_time = now_ms

        try:
            rows = self._fetch_rows("governor_log", start_ms=start_time or 0, end_ms=end_time)
        except Exception as exc:
            logger.warning("SessionQuery: 读取 governor_log 失败 - %s", exc)
            rows = []

        try:
            intent_rows = self._fetch_rows("intent_log", start_ms=start_time or 0, end_ms=end_time)
        except Exception as exc:
            logger.warning("SessionQuery: 读取 intent_log 失败 - %s", exc)
            intent_rows = []

        try:
            voice_rows = self._fetch_rows("voice_event_log", start_ms=start_time or 0, end_ms=end_time)
        except Exception as exc:
            logger.warning("SessionQuery: 读取 voice_event_log 失败 - %s", exc)
            voice_rows = []

        try:
            bond_rows = self._fetch_rows("bond_update_log", start_ms=start_time or 0, end_ms=end_time)
        except Exception as exc:
            logger.warning("SessionQuery: 读取 bond_update_log 失败 - %s", exc)
            bond_rows = []

        latest_intent_by_request: Dict[str, Dict[str, Any]] = {}
        for row in intent_rows:
            request_id = str(row.get("request_id") or "").strip()
            if not request_id:
                continue
            if (
                request_id not in latest_intent_by_request
                or int(row.get("ts_ms") or 0) > int(latest_intent_by_request[request_id].get("ts_ms") or 0)
            ):
                latest_intent_by_request[request_id] = row

        latest_voice_preview_by_request: Dict[str, str] = {}
        latest_voice_session_by_request: Dict[str, str] = {}
        for row in voice_rows:
            request_id = str(row.get("request_id") or "").strip()
            if not request_id:
                continue
            payload = row.get("payload")
            payload_dict = dict(payload) if isinstance(payload, dict) else {}
            preview = self._extract_voice_preview(row, payload_dict)
            if preview:
                latest_voice_preview_by_request[request_id] = preview
            session_value = str(row.get("session_id") or payload_dict.get("conversation_id") or "").strip()
            if session_value:
                latest_voice_session_by_request[request_id] = session_value

        latest_bond_topic_by_request: Dict[str, str] = {}
        for row in bond_rows:
            request_id = str(row.get("request_id") or "").strip()
            if not request_id:
                continue
            topic_preview = self._extract_bond_topic_preview(row)
            if topic_preview:
                latest_bond_topic_by_request[request_id] = topic_preview

        seen: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            request_id = str(row.get("request_id") or "").strip()
            if not request_id:
                continue

            current = dict(row)
            intent_row = latest_intent_by_request.get(request_id)
            if intent_row is not None:
                if not current.get("session_id") and intent_row.get("session_id"):
                    current["session_id"] = intent_row.get("session_id")
                input_preview = self._extract_intent_preview(intent_row)
                if input_preview:
                    current["input_preview"] = input_preview

            if not current.get("session_id") and latest_voice_session_by_request.get(request_id):
                current["session_id"] = latest_voice_session_by_request[request_id]
            if not current.get("input_preview") and latest_voice_preview_by_request.get(request_id):
                current["input_preview"] = latest_voice_preview_by_request[request_id]
            if not current.get("input_preview") and latest_bond_topic_by_request.get(request_id):
                current["input_preview"] = latest_bond_topic_by_request[request_id]

            if request_id not in seen or current.get("ts_ms", 0) > seen[request_id].get("ts_ms", 0):
                seen[request_id] = current

        items_raw = sorted(seen.values(), key=lambda item: item.get("ts_ms", 0), reverse=True)
        items_raw = self._apply_filters(items_raw, status=status, channel=channel, keyword=keyword)

        total = len(items_raw)
        page_size = min(max(1, page_size), 100)
        page = max(1, page)
        offset = (page - 1) * page_size
        page_items = items_raw[offset: offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [self._format_item(item) for item in page_items],
            "timestamp": now_ms,
        }

    def _fetch_rows(self, table: str, *, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
        page_size = 10_000
        offset = 0
        rows: List[Dict[str, Any]] = []
        while True:
            chunk = self._ledger.fetch_by_time_range(
                table,
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

    def _apply_filters(
        self,
        items: List[Dict[str, Any]],
        *,
        status: Optional[str],
        channel: Optional[str],
        keyword: Optional[str],
    ) -> List[Dict[str, Any]]:
        filtered = list(items)
        if status:
            normalized_status = status.lower()
            if normalized_status == "ok":
                filtered = [row for row in filtered if row.get("decision") == "APPROVE"]
            elif normalized_status == "rejected":
                filtered = [row for row in filtered if row.get("decision") == "REJECT"]
            elif normalized_status == "error":
                filtered = [row for row in filtered if row.get("decision") not in ("APPROVE", "REJECT")]

        if channel:
            filtered = [row for row in filtered if str(row.get("channel") or "").lower() == channel.lower()]

        if keyword:
            keyword_lower = keyword.lower()
            filtered = [
                row for row in filtered
                if keyword_lower in str(row.get("request_id") or "").lower()
                or keyword_lower in str(row.get("session_id") or "").lower()
                or keyword_lower in str(row.get("input_preview") or "").lower()
            ]

        return filtered

    @staticmethod
    def _format_item(row: Dict[str, Any]) -> Dict[str, Any]:
        decision = row.get("decision", "")
        if decision == "APPROVE":
            status = "ok"
        elif decision == "REJECT":
            status = "rejected"
        else:
            status = "error"

        return {
            "request_id": row.get("request_id", ""),
            "session_id": row.get("session_id"),
            "ts_ms": row.get("ts_ms", 0),
            "created_at": _fmt_ts(row.get("ts_ms")),
            "status": status,
            "decision": decision,
            "reason_code": row.get("reason_code"),
            "channel": row.get("channel"),
            "duration_ms": row.get("duration_ms") or row.get("total_latency_ms"),
            "input_preview": row.get("input_preview"),
        }

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

    @staticmethod
    def _extract_voice_preview(row: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
        event_type = str(row.get("event_type") or "").strip().upper()
        if not event_type.startswith("INPUT_"):
            return None
        for key in ("input_preview", "content_preview", "text_preview"):
            value = payload.get(key)
            if isinstance(value, str):
                preview = value.strip()
                if preview and not SessionQueryService._is_placeholder_preview(preview):
                    return preview[:120]
        return None

    @staticmethod
    def _extract_bond_topic_preview(row: Dict[str, Any]) -> Optional[str]:
        topic_id = row.get("topic_id")
        if not isinstance(topic_id, str):
            return None
        preview = topic_id.strip()
        if not preview or SessionQueryService._is_placeholder_preview(preview):
            return None
        return preview[:120]

    @staticmethod
    def _is_placeholder_preview(value: str) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return True
        return all(ch in {"?", "？"} for ch in normalized)


def _fmt_ts(ts_ms: Optional[int]) -> Optional[str]:
    if ts_ms is None:
        return None
    import datetime

    dt = datetime.datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
