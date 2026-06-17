from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

LIGHTNOVEL_DOMAIN = "lightnovel_recommendation"
GENERAL_DOMAIN = "general"
PROFILE_SCHEMA_VERSION = "Natsume.user_preference_profile.v1"
DELIVERY_SCHEMA_VERSION = "Natsume.recommendation_delivery.v1"

_SPACE_RE = re.compile(r"\s+")
_LIGHTNOVEL_QUERY_RE = re.compile(r"(小说|轻小说|书|推荐|详情|详细|内容|简介|介绍|lightnovel|epub|奇幻|后宫|异世界|恋爱|校园)", re.IGNORECASE)
_PREFERENCE_QUERY_RE = re.compile(r"(我喜欢什么|我的偏好|我的喜好|我偏好|喜欢哪些|喜欢哪类|口味|taste|preference)", re.IGNORECASE)
_RECOMMENDATION_CONTEXT_QUERY_RE = re.compile(
    r"("
    r"第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)?"
    r"|(?:这本|这部|这个|那本|那部|刚才那本|刚刚那本|刚才推荐|刚刚推荐)"
    r"|(?:哪本|先看哪|先读哪|好看吗|好不好看|怎么样|如何|值不值得|推不推荐|值得看)"
    r"|(?:详情|详细内容|详细|内容|简介|介绍|讲什么|说什么)"
    r"|(?:epub|电子书|封装|打包|制作|生成|下载|购买|买下来)"
    r")",
    re.IGNORECASE,
)
_RECOMMENDATION_INDEX_RE = re.compile(r"(?:第\s*)?([一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)")
_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _enabled(name: str, *, default: bool = True) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _clean_text(value: Any, *, limit: int = 240) -> str:
    text = _SPACE_RE.sub(" ", str(value or "").strip())
    if len(text) <= max(1, int(limit)):
        return text
    return text[: max(1, int(limit)) - 1].rstrip() + "..."


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("|".join(_clean_text(part, limit=400) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _event_id() -> str:
    return f"prefevt_{uuid.uuid4().hex}"


def _delivery_id() -> str:
    return f"recodel_{uuid.uuid4().hex}"


def _normalize_domain(domain: Any) -> str:
    value = str(domain or "").strip().lower()
    return value or GENERAL_DOMAIN


def _get_ledger(ledger: Any | None = None) -> Any:
    if ledger is not None:
        return ledger
    from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

    return get_ledger()


def _conn_and_lock(ledger: Any) -> tuple[Any | None, Any | None]:
    return getattr(ledger, "_conn", None), getattr(ledger, "_write_lock", None)


@dataclass(slots=True)
class UserPreferenceEvent:
    person_id: str
    session_id: str
    domain: str
    source_kind: str
    polarity: str
    target_kind: str
    target_id: str
    target_label: str
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at_ms: int = field(default_factory=_now_ms)
    event_id: str = field(default_factory=_event_id)
    active: bool = True

    def normalized(self) -> "UserPreferenceEvent":
        polarity = str(self.polarity or "").strip().lower()
        if polarity not in {"positive", "negative"}:
            polarity = "positive"
        confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        return UserPreferenceEvent(
            event_id=str(self.event_id or _event_id()),
            person_id=_clean_text(self.person_id, limit=120) or "unknown",
            session_id=_clean_text(self.session_id, limit=180),
            domain=_normalize_domain(self.domain),
            source_kind=_clean_text(self.source_kind, limit=80) or "unknown",
            polarity=polarity,
            target_kind=_clean_text(self.target_kind, limit=80) or "trait",
            target_id=_clean_text(self.target_id, limit=220) or _stable_id("target", self.target_label),
            target_label=_clean_text(self.target_label, limit=240),
            attributes=dict(self.attributes or {}),
            evidence_refs=[_clean_text(item, limit=300) for item in list(self.evidence_refs or []) if _clean_text(item)],
            confidence=confidence,
            created_at_ms=int(self.created_at_ms or _now_ms()),
            active=bool(self.active),
        )

    def as_row(self) -> tuple[Any, ...]:
        item = self.normalized()
        return (
            item.event_id,
            item.person_id,
            item.session_id,
            item.domain,
            item.source_kind,
            item.polarity,
            item.target_kind,
            item.target_id,
            item.target_label,
            _json_dumps(item.attributes),
            _json_dumps(item.evidence_refs),
            item.confidence,
            item.created_at_ms,
            1 if item.active else 0,
        )


class UserPreferenceStore:
    def __init__(self, *, ledger: Any | None = None) -> None:
        self.ledger = _get_ledger(ledger)
        self.conn, self.write_lock = _conn_and_lock(self.ledger)
        self.ensure_schema()

    @property
    def available(self) -> bool:
        return self.conn is not None and self.write_lock is not None

    def ensure_schema(self) -> None:
        if self.conn is None:
            return
        self.conn.executescript(USER_PREFERENCE_SCHEMA_SQL)
        columns = {
            str(row[1])
            for row in self.conn.execute("PRAGMA table_info(recommendation_delivery_log)").fetchall()
        }
        if "items_json" not in columns:
            self.conn.execute("ALTER TABLE recommendation_delivery_log ADD COLUMN items_json TEXT NOT NULL DEFAULT '[]'")
        if "message_payload_json" not in columns:
            self.conn.execute("ALTER TABLE recommendation_delivery_log ADD COLUMN message_payload_json TEXT NOT NULL DEFAULT '{}'")
        if "active" not in columns:
            self.conn.execute("ALTER TABLE recommendation_delivery_log ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        self.conn.commit()

    def append_events(self, events: Sequence[UserPreferenceEvent]) -> list[dict[str, Any]]:
        if not _enabled("HOST_USER_PREFERENCE_ENABLED", default=True) or not self.available:
            return []
        normalized = [event.normalized() for event in list(events or [])]
        if not normalized:
            return []
        touched: set[tuple[str, str]] = set()
        rows: list[dict[str, Any]] = []
        with self.write_lock:
            for event in normalized:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO user_preference_event (
                        event_id, person_id, session_id, domain, source_kind, polarity,
                        target_kind, target_id, target_label, attributes_json,
                        evidence_refs_json, confidence, created_at_ms, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    event.as_row(),
                )
                touched.add((event.person_id, event.domain))
                rows.append(self._event_to_dict(event))
            for person_id, domain in sorted(touched):
                self._recompute_profile_locked(person_id=person_id, domain=domain)
            self.conn.commit()
        return rows

    def append_event(self, event: UserPreferenceEvent) -> dict[str, Any] | None:
        rows = self.append_events([event])
        return rows[0] if rows else None

    def get_profile(self, *, person_id: str, domain: str = LIGHTNOVEL_DOMAIN) -> dict[str, Any] | None:
        if not self.available:
            return None
        row = self.conn.execute(
            """
            SELECT person_id, domain, version, profile_json, updated_at_ms
            FROM user_preference_profile
            WHERE person_id = ? AND domain = ?
            LIMIT 1
            """,
            (_clean_text(person_id, limit=120) or "unknown", _normalize_domain(domain)),
        ).fetchone()
        if row is None:
            return None
        profile = _json_loads(row["profile_json"], {})
        if not isinstance(profile, dict):
            profile = {}
        profile.setdefault("person_id", str(row["person_id"] or ""))
        profile.setdefault("domain", str(row["domain"] or ""))
        profile.setdefault("version", int(row["version"] or 1))
        profile.setdefault("updated_at_ms", int(row["updated_at_ms"] or 0))
        return profile

    def get_latest_delivery(
        self,
        *,
        person_id: str | None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        domain: str = LIGHTNOVEL_DOMAIN,
        created_before_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        params: list[Any] = [_normalize_domain(domain)]
        where = ["domain = ?", "active = 1"]
        clean_person = _clean_text(person_id, limit=120)
        clean_session = _clean_text(session_id, limit=180)
        clean_conversation = _clean_text(conversation_id, limit=180)
        identity_parts: list[str] = []
        if clean_person:
            identity_parts.append("person_id = ?")
            params.append(clean_person)
        if clean_session:
            identity_parts.append("session_id = ?")
            params.append(clean_session)
        if clean_conversation:
            identity_parts.append("conversation_id = ?")
            params.append(clean_conversation)
        if identity_parts:
            where.append("(" + " OR ".join(identity_parts) + ")")
        if created_before_ms is not None and int(created_before_ms or 0) > 0:
            where.append("created_at_ms <= ?")
            params.append(int(created_before_ms))
        row = self.conn.execute(
            """
            SELECT *
            FROM recommendation_delivery_log
            WHERE """
            + " AND ".join(where)
            + """
            ORDER BY created_at_ms DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return self._delivery_row_to_dict(row) if row is not None else None

    def record_delivery(
        self,
        *,
        person_id: str | None,
        session_id: str | None,
        domain: str,
        platform: str,
        conversation_id: str,
        request_id: str,
        job_id: str,
        recommendations_ref: str,
        ranked_recommendations_ref: str,
        items: Sequence[Mapping[str, Any]],
        message_payload: Mapping[str, Any] | None = None,
        created_at_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if not _enabled("HOST_RECOMMENDATION_FEEDBACK_ENABLED", default=True) or not self.available:
            return None
        normalized_items = [
            normalize_recommendation_item(item, rank=index)
            for index, item in enumerate(list(items or []), start=1)
            if isinstance(item, Mapping)
        ]
        if not normalized_items:
            return None
        delivery = {
            "delivery_id": _delivery_id(),
            "person_id": _clean_text(person_id, limit=120) or "default",
            "session_id": _clean_text(session_id, limit=180),
            "domain": _normalize_domain(domain),
            "platform": _clean_text(platform, limit=40),
            "conversation_id": _clean_text(conversation_id, limit=180),
            "request_id": _clean_text(request_id, limit=160),
            "job_id": _clean_text(job_id, limit=160),
            "recommendations_ref": _clean_text(recommendations_ref, limit=400),
            "ranked_recommendations_ref": _clean_text(ranked_recommendations_ref, limit=400),
            "items": normalized_items,
            "message_payload": dict(message_payload or {}),
            "created_at_ms": int(created_at_ms or _now_ms()),
            "active": True,
        }
        with self.write_lock:
            columns = {
                str(row[1])
                for row in self.conn.execute("PRAGMA table_info(recommendation_delivery_log)").fetchall()
            }
            insert_columns = [
                "delivery_id",
                "person_id",
                "session_id",
                "domain",
                "platform",
                "conversation_id",
                "request_id",
                "job_id",
                "recommendations_ref",
                "ranked_recommendations_ref",
                "items_json",
                "message_payload_json",
                "created_at_ms",
                "active",
            ]
            values: list[Any] = [
                delivery["delivery_id"],
                delivery["person_id"],
                delivery["session_id"],
                delivery["domain"],
                delivery["platform"],
                delivery["conversation_id"],
                delivery["request_id"],
                delivery["job_id"],
                delivery["recommendations_ref"],
                delivery["ranked_recommendations_ref"],
                _json_dumps(normalized_items),
                _json_dumps(delivery["message_payload"]),
                delivery["created_at_ms"],
                1,
            ]
            if "visible_items_json" in columns and "visible_items_json" not in insert_columns:
                insert_columns.insert(11, "visible_items_json")
                values.insert(11, _json_dumps(normalized_items))
            placeholders = ", ".join("?" for _ in insert_columns)
            self.conn.execute(
                f"""
                INSERT INTO recommendation_delivery_log ({", ".join(insert_columns)})
                VALUES ({placeholders})
                """,
                tuple(values),
            )
            self.conn.commit()
        return delivery

    def _event_to_dict(self, event: UserPreferenceEvent) -> dict[str, Any]:
        item = event.normalized()
        return {
            "event_id": item.event_id,
            "person_id": item.person_id,
            "session_id": item.session_id,
            "domain": item.domain,
            "source_kind": item.source_kind,
            "polarity": item.polarity,
            "target_kind": item.target_kind,
            "target_id": item.target_id,
            "target_label": item.target_label,
            "attributes": dict(item.attributes or {}),
            "evidence_refs": list(item.evidence_refs or []),
            "confidence": item.confidence,
            "created_at_ms": item.created_at_ms,
            "active": item.active,
        }

    def _delivery_row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "delivery_id": str(row["delivery_id"] or ""),
            "person_id": str(row["person_id"] or ""),
            "session_id": str(row["session_id"] or ""),
            "domain": str(row["domain"] or ""),
            "platform": str(row["platform"] or ""),
            "conversation_id": str(row["conversation_id"] or ""),
            "request_id": str(row["request_id"] or ""),
            "job_id": str(row["job_id"] or ""),
            "recommendations_ref": str(row["recommendations_ref"] or ""),
            "ranked_recommendations_ref": str(row["ranked_recommendations_ref"] or ""),
            "items": _json_loads(
                row["items_json"] if "items_json" in row.keys() else row["visible_items_json"],
                [],
            ),
            "message_payload": _json_loads(row["message_payload_json"], {}),
            "created_at_ms": int(row["created_at_ms"] or 0),
            "active": bool(row["active"]),
        }

    def _recompute_profile_locked(self, *, person_id: str, domain: str) -> None:
        rows = self.conn.execute(
            """
            SELECT *
            FROM user_preference_event
            WHERE person_id = ? AND domain = ? AND active = 1
            ORDER BY created_at_ms ASC
            """,
            (person_id, domain),
        ).fetchall()
        events = [self._row_to_event_dict(row) for row in list(rows or [])]
        profile = build_profile_from_events(person_id=person_id, domain=domain, events=events)
        current = self.conn.execute(
            "SELECT version FROM user_preference_profile WHERE person_id = ? AND domain = ?",
            (person_id, domain),
        ).fetchone()
        next_version = int(current["version"] or 0) + 1 if current is not None else 1
        profile["version"] = next_version
        updated_at_ms = _now_ms()
        profile["updated_at_ms"] = updated_at_ms
        self.conn.execute(
            """
            INSERT INTO user_preference_profile (person_id, domain, version, profile_json, updated_at_ms)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(person_id, domain) DO UPDATE SET
                version = excluded.version,
                profile_json = excluded.profile_json,
                updated_at_ms = excluded.updated_at_ms
            """,
            (person_id, domain, next_version, _json_dumps(profile), updated_at_ms),
        )

    def _row_to_event_dict(self, row: Any) -> dict[str, Any]:
        return {
            "event_id": str(row["event_id"] or ""),
            "person_id": str(row["person_id"] or ""),
            "session_id": str(row["session_id"] or ""),
            "domain": str(row["domain"] or ""),
            "source_kind": str(row["source_kind"] or ""),
            "polarity": str(row["polarity"] or ""),
            "target_kind": str(row["target_kind"] or ""),
            "target_id": str(row["target_id"] or ""),
            "target_label": str(row["target_label"] or ""),
            "attributes": _json_loads(row["attributes_json"], {}),
            "evidence_refs": _json_loads(row["evidence_refs_json"], []),
            "confidence": float(row["confidence"] or 0.0),
            "created_at_ms": int(row["created_at_ms"] or 0),
            "active": bool(row["active"]),
        }


def build_profile_from_events(*, person_id: str, domain: str, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    likes: dict[str, dict[str, Any]] = {}
    dislikes: dict[str, dict[str, Any]] = {}
    liked_items: dict[str, dict[str, Any]] = {}
    disliked_items: dict[str, dict[str, Any]] = {}
    evidence_summary: list[dict[str, Any]] = []

    for event in list(events or []):
        polarity = str(event.get("polarity") or "").strip().lower()
        target_kind = str(event.get("target_kind") or "trait").strip().lower()
        target_id = str(event.get("target_id") or "").strip()
        label = _clean_text(event.get("target_label"), limit=160)
        confidence = max(0.0, min(1.0, float(event.get("confidence") or 0.0)))
        if not target_id and label:
            target_id = _stable_id(target_kind, label)
        if not target_id:
            continue
        bucket = liked_items if polarity == "positive" and target_kind == "recommendation_item" else None
        if polarity == "negative" and target_kind == "recommendation_item":
            bucket = disliked_items
        if bucket is None:
            bucket = likes if polarity == "positive" else dislikes
        item = bucket.setdefault(
            target_id,
            {
                "id": target_id,
                "label": label or target_id,
                "confidence": 0.0,
                "evidence_count": 0,
                "latest_at_ms": 0,
                "keywords": [],
                "source_kinds": [],
            },
        )
        item["confidence"] = round(max(float(item.get("confidence") or 0.0), confidence), 4)
        item["evidence_count"] = int(item.get("evidence_count") or 0) + 1
        item["latest_at_ms"] = max(int(item.get("latest_at_ms") or 0), int(event.get("created_at_ms") or 0))
        attrs = dict(event.get("attributes") or {})
        for keyword in list(attrs.get("keywords") or []):
            keyword_text = _clean_text(keyword, limit=60)
            if keyword_text and keyword_text not in item["keywords"]:
                item["keywords"].append(keyword_text)
        source_kind = _clean_text(event.get("source_kind"), limit=80)
        if source_kind and source_kind not in item["source_kinds"]:
            item["source_kinds"].append(source_kind)
        evidence_summary.append(
            {
                "event_id": str(event.get("event_id") or ""),
                "polarity": polarity,
                "target_kind": target_kind,
                "target_label": label or target_id,
                "confidence": confidence,
                "created_at_ms": int(event.get("created_at_ms") or 0),
            }
        )

    def _ordered(bucket: Mapping[str, Mapping[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in sorted(
                bucket.values(),
                key=lambda row: (float(row.get("confidence") or 0.0), int(row.get("latest_at_ms") or 0)),
                reverse=True,
            )[:limit]
        ]

    inferred_traits = _ordered(likes, limit=16)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "person_id": person_id,
        "domain": domain,
        "likes": _ordered(likes, limit=20),
        "dislikes": _ordered(dislikes, limit=20),
        "liked_items": _ordered(liked_items, limit=20),
        "disliked_items": _ordered(disliked_items, limit=20),
        "inferred_traits": inferred_traits,
        "evidence_summary": sorted(evidence_summary, key=lambda row: int(row.get("created_at_ms") or 0), reverse=True)[:30],
    }


def normalize_recommendation_item(item: Mapping[str, Any], *, rank: int) -> dict[str, Any]:
    title = _clean_text(item.get("title") or item.get("title_display") or item.get("canonical_title") or "未命名作品", limit=180)
    detail_url = _clean_text(item.get("detail_url") or item.get("url") or item.get("source_url"), limit=320)
    cover_url = _clean_text(item.get("cover_url") or item.get("cover") or item.get("image_url"), limit=500)
    series_key = _clean_text(item.get("series_key"), limit=220)
    item_id = series_key or _clean_text(item.get("key"), limit=220) or _stable_id("recitem", title, detail_url)
    preference_matches = [dict(match) for match in list(item.get("preference_matches") or []) if isinstance(match, Mapping)]
    traits = []
    for match in preference_matches[:8]:
        label = _clean_text(match.get("label") or match.get("group_id"), limit=120)
        if not label:
            continue
        traits.append(
            {
                "trait_id": _clean_text(match.get("group_id"), limit=120) or _stable_id("trait", label),
                "label": label,
                "keywords": [_clean_text(keyword, limit=60) for keyword in list(match.get("keywords") or []) if _clean_text(keyword)],
                "score": float(match.get("score") or 0.0),
            }
        )
    if not traits:
        bucket = _clean_text(item.get("diversity_bucket"), limit=120)
        if bucket and bucket != "other":
            traits.append({"trait_id": bucket, "label": bucket, "keywords": [bucket], "score": 0.0})
    return {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "rank": int(item.get("recommendation_rank") or rank),
        "target_id": item_id,
        "title": title,
        "detail_url": detail_url,
        "cover_url": cover_url,
        "series_key": series_key,
        "source_key": _clean_text(item.get("source_key"), limit=120),
        "selection_reason": _clean_text(item.get("selection_reason") or item.get("rationale") or item.get("reason"), limit=220),
        "intro_snippet": _clean_text(item.get("intro_snippet") or item.get("intro") or item.get("description") or item.get("summary"), limit=260),
        "score": float(item.get("recommendation_score") or item.get("recommendation_adjusted_score") or 0.0),
        "traits": traits,
        "message_ids": [_clean_text(value, limit=120) for value in list(item.get("message_ids") or []) if _clean_text(value)],
        "raw": {
            "content_access_state": _clean_text(item.get("content_access_state"), limit=60),
            "dedupe_state": _clean_text(item.get("dedupe_state"), limit=80),
            "ranking_profile_version": _clean_text(item.get("ranking_profile_version"), limit=120),
        },
    }


def _parse_recommendation_reference_index(query: str) -> int | None:
    match = _RECOMMENDATION_INDEX_RE.search(str(query or ""))
    if not match:
        return None
    raw = str(match.group(1) or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _CN_NUMBERS.get(raw)


def _infer_recent_recommendation_reference_index(
    *,
    store: UserPreferenceStore,
    session_id: str,
    conversation_id: str,
    current_query: str,
) -> int | None:
    conn = store.conn
    if conn is None:
        return None
    clean_session = _clean_text(session_id, limit=180)
    clean_conversation = _clean_text(conversation_id, limit=180) or clean_session
    if not clean_session and not clean_conversation:
        return None
    try:
        rows = conn.execute(
            """
            SELECT role, content, ts_ms, turn_index
            FROM conversation_turn
            WHERE session_id = ? OR session_id = ?
            ORDER BY ts_ms DESC, turn_index DESC
            LIMIT 16
            """,
            (clean_session, clean_conversation),
        ).fetchall()
    except Exception:
        return None
    current = _clean_text(current_query, limit=500)
    for row in list(rows or []):
        try:
            content = _clean_text(row["content"], limit=500)
        except Exception:
            content = _clean_text(row[1] if len(row) > 1 else "", limit=500)
        if not content or content == current:
            continue
        index = _parse_recommendation_reference_index(content)
        if index is not None:
            return index
    return None


def _compact_recommendation_context_item(item: Mapping[str, Any]) -> dict[str, Any]:
    traits = []
    for trait in list(item.get("traits") or [])[:4]:
        if not isinstance(trait, Mapping):
            continue
        label = _clean_text(trait.get("label"), limit=80)
        if label:
            traits.append(label)
    return {
        "rank": int(item.get("rank") or 0),
        "title": _clean_text(item.get("title"), limit=180),
        "detail_url": _clean_text(item.get("detail_url"), limit=320),
        "selection_reason": _clean_text(item.get("selection_reason"), limit=220),
        "intro_snippet": _clean_text(item.get("intro_snippet"), limit=260),
        "traits": traits,
        "score": item.get("score"),
    }


def _build_recent_recommendation_context(
    *,
    store: UserPreferenceStore,
    person_id: str,
    session_id: str | None,
    conversation_id: str | None,
    query_text: str,
) -> dict[str, Any]:
    query = _clean_text(query_text, limit=500)
    if not query or not _RECOMMENDATION_CONTEXT_QUERY_RE.search(query):
        return {}
    clean_session = _clean_text(session_id, limit=180)
    clean_conversation = _clean_text(conversation_id, limit=180) or clean_session
    delivery = store.get_latest_delivery(
        person_id=person_id,
        session_id=clean_session,
        conversation_id=clean_conversation,
        domain=LIGHTNOVEL_DOMAIN,
    )
    if not delivery:
        return {}
    items = [
        _compact_recommendation_context_item(item)
        for item in list(delivery.get("items") or [])
        if isinstance(item, Mapping)
    ]
    if not items:
        return {}
    reference_index = _parse_recommendation_reference_index(query)
    if reference_index is None:
        reference_index = _infer_recent_recommendation_reference_index(
            store=store,
            session_id=clean_session,
            conversation_id=clean_conversation,
            current_query=query,
        )
    referenced_item = None
    if reference_index is not None:
        referenced_item = next((item for item in items if int(item.get("rank") or 0) == reference_index), None)
        if referenced_item is None and 1 <= reference_index <= len(items):
            referenced_item = items[reference_index - 1]
    return {
        "schema_version": "Natsume.recent_recommendation_context.v1",
        "domain": LIGHTNOVEL_DOMAIN,
        "delivery_id": _clean_text(delivery.get("delivery_id"), limit=160),
        "created_at_ms": int(delivery.get("created_at_ms") or 0),
        "conversation_id": _clean_text(delivery.get("conversation_id"), limit=180),
        "items": items[:8],
        "reference_index": reference_index,
        "referenced_item": referenced_item or {},
        "answer_guidance": {
            "generate_with_model": True,
            "treat_as_followup_question": True,
            "do_not_record_as_preference": True,
            "if_referenced_item_present": "Answer about that exact ranked item from the recent recommendation set.",
            "if_no_referenced_item": "Use the recent recommendation list as context; ask a short clarification only if the reference is still ambiguous.",
        },
    }


def record_recommendation_delivery(
    *,
    person_id: str | None,
    session_id: str | None,
    domain: str,
    platform: str,
    conversation_id: str,
    request_id: str,
    job_id: str,
    recommendations_ref: str,
    ranked_recommendations_ref: str,
    items: Sequence[Mapping[str, Any]],
    message_payload: Mapping[str, Any] | None = None,
    ledger: Any | None = None,
) -> dict[str, Any] | None:
    return UserPreferenceStore(ledger=ledger).record_delivery(
        person_id=person_id,
        session_id=session_id,
        domain=domain,
        platform=platform,
        conversation_id=conversation_id,
        request_id=request_id,
        job_id=job_id,
        recommendations_ref=recommendations_ref,
        ranked_recommendations_ref=ranked_recommendations_ref,
        items=items,
        message_payload=message_payload,
    )


def build_user_preference_pack(
    *,
    person_id: str | None,
    query_text: str,
    session_id: str | None = None,
    conversation_id: str | None = None,
    ledger: Any | None = None,
) -> dict[str, Any]:
    if not _enabled("HOST_USER_PREFERENCE_ENABLED", default=True):
        return {}
    clean_person = _clean_text(person_id, limit=120) or "default"
    query = _clean_text(query_text, limit=500)
    store = UserPreferenceStore(ledger=ledger)
    recent_recommendation_context = _build_recent_recommendation_context(
        store=store,
        person_id=clean_person,
        session_id=session_id,
        conversation_id=conversation_id,
        query_text=query,
    )
    query_relevant = bool(
        _PREFERENCE_QUERY_RE.search(query)
        or _LIGHTNOVEL_QUERY_RE.search(query)
        or recent_recommendation_context
    )
    domain = LIGHTNOVEL_DOMAIN if (
        _LIGHTNOVEL_QUERY_RE.search(query)
        or _PREFERENCE_QUERY_RE.search(query)
        or recent_recommendation_context
    ) else GENERAL_DOMAIN
    profile = store.get_profile(person_id=clean_person, domain=domain)
    if not profile and domain != LIGHTNOVEL_DOMAIN and query_relevant:
        profile = store.get_profile(person_id=clean_person, domain=LIGHTNOVEL_DOMAIN)
        domain = LIGHTNOVEL_DOMAIN if profile else domain
    if not profile and not query_relevant:
        return {}
    pack = {
        "schema_version": "Natsume.user_preference_pack.v1",
        "person_id": clean_person,
        "domain": domain,
        "query_relevant": query_relevant,
        "has_profile": bool(profile),
        "profile": profile or {},
        "answer_guidance": {
            "generate_with_model": True,
            "do_not_claim_unproven_preferences": True,
            "separate_confirmed_from_inferred": True,
            "if_empty": "Say naturally that there is not enough explicit preference evidence yet; ask one small calibration question or propose a tentative direction.",
        },
    }
    if recent_recommendation_context:
        pack["recent_recommendation_context"] = recent_recommendation_context
    return pack


def build_lightnovel_dynamic_taste_profile(
    base_profile: Mapping[str, Any] | None,
    *,
    person_id: str | None,
    ledger: Any | None = None,
) -> dict[str, Any]:
    profile = dict(base_profile or {})
    if not _enabled("HOST_USER_PREFERENCE_ENABLED", default=True):
        return profile
    clean_person = _clean_text(person_id, limit=120)
    if not clean_person:
        return profile
    store = UserPreferenceStore(ledger=ledger)
    user_profile = store.get_profile(person_id=clean_person, domain=LIGHTNOVEL_DOMAIN)
    if not user_profile:
        return profile

    def _coerce_groups(value: Any) -> dict[str, dict[str, Any]]:
        if isinstance(value, Mapping):
            return {
                _clean_text(key, limit=120) or _stable_id("group", index, group): dict(group)
                if isinstance(group, Mapping)
                else {"label": _clean_text(key, limit=80), "weight": 1.0, "keywords": [_clean_text(group, limit=60)]}
                for index, (key, group) in enumerate(value.items())
            }
        groups: dict[str, dict[str, Any]] = {}
        for index, group in enumerate(list(value or []), start=1):
            if isinstance(group, Mapping):
                label = _clean_text(group.get("label") or group.get("group_id") or f"base_group_{index}", limit=80)
                keywords = [_clean_text(keyword, limit=60) for keyword in list(group.get("keywords") or []) if _clean_text(keyword)]
                groups[_clean_text(group.get("group_id"), limit=120) or f"base_group_{index}"] = {
                    **dict(group),
                    "label": label,
                    "keywords": keywords,
                }
                continue
            if isinstance(group, (str, bytes)):
                keywords = [_clean_text(group, limit=60)]
            else:
                keywords = [_clean_text(keyword, limit=60) for keyword in list(group or []) if _clean_text(keyword)]
            keywords = [keyword for keyword in keywords if keyword]
            if keywords:
                groups[f"base_group_{index}"] = {"label": keywords[0], "weight": 1.0, "keywords": keywords}
        return groups

    positive_groups = _coerce_groups(profile.get("positive_keyword_groups"))
    negative_groups = _coerce_groups(profile.get("negative_keyword_groups"))

    def _keywords(entry: Mapping[str, Any]) -> list[str]:
        values = [_clean_text(item, limit=60) for item in list(entry.get("keywords") or []) if _clean_text(item)]
        label = _clean_text(entry.get("label"), limit=80)
        if label and label not in values:
            values.append(label)
        return values[:8]

    for entry in list(user_profile.get("likes") or [])[:12]:
        if not isinstance(entry, Mapping):
            continue
        keywords = _keywords(entry)
        if not keywords:
            continue
        confidence = float(entry.get("confidence") or 0.0)
        group_id = "user_like_" + hashlib.sha1(str(entry.get("id") or entry.get("label") or "").encode("utf-8")).hexdigest()[:10]
        positive_groups[group_id] = {
            "label": f"用户偏好：{_clean_text(entry.get('label'), limit=40)}",
            "weight": round(6.0 + confidence * 10.0, 2),
            "keywords": keywords,
        }
    for entry in list(user_profile.get("liked_items") or [])[:6]:
        if not isinstance(entry, Mapping):
            continue
        label = _clean_text(entry.get("label"), limit=80)
        if not label:
            continue
        group_id = "user_liked_item_" + hashlib.sha1(str(entry.get("id") or label).encode("utf-8")).hexdigest()[:10]
        positive_groups[group_id] = {
            "label": f"用户刚喜欢过：{label[:32]}",
            "weight": 5.0,
            "keywords": [label],
        }
    for entry in list(user_profile.get("dislikes") or [])[:12]:
        if not isinstance(entry, Mapping):
            continue
        keywords = _keywords(entry)
        if not keywords:
            continue
        confidence = float(entry.get("confidence") or 0.0)
        group_id = "user_dislike_" + hashlib.sha1(str(entry.get("id") or entry.get("label") or "").encode("utf-8")).hexdigest()[:10]
        negative_groups[group_id] = {
            "label": f"用户不喜欢：{_clean_text(entry.get('label'), limit=40)}",
            "weight": round(-8.0 - confidence * 14.0, 2),
            "keywords": keywords,
        }

    profile["positive_keyword_groups"] = positive_groups
    profile["negative_keyword_groups"] = negative_groups
    profile["version"] = f"{profile.get('version') or 'custom'}+userpref-v{user_profile.get('version') or 1}"
    profile["user_preference_profile"] = {
        "person_id": clean_person,
        "domain": LIGHTNOVEL_DOMAIN,
        "version": user_profile.get("version"),
        "likes_count": len(list(user_profile.get("likes") or [])),
        "dislikes_count": len(list(user_profile.get("dislikes") or [])),
    }
    return profile


USER_PREFERENCE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_preference_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    session_id TEXT,
    domain TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    polarity TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_label TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_preference_event_id ON user_preference_event(event_id);
CREATE INDEX IF NOT EXISTS idx_user_preference_event_person_domain ON user_preference_event(person_id, domain, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_user_preference_event_target ON user_preference_event(domain, target_kind, target_id);

CREATE TABLE IF NOT EXISTS user_preference_profile (
    person_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    profile_json TEXT NOT NULL DEFAULT '{}',
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY(person_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_user_preference_profile_updated ON user_preference_profile(updated_at_ms);

CREATE TABLE IF NOT EXISTS recommendation_delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    session_id TEXT,
    domain TEXT NOT NULL,
    platform TEXT,
    conversation_id TEXT,
    request_id TEXT,
    job_id TEXT,
    recommendations_ref TEXT,
    ranked_recommendations_ref TEXT,
    items_json TEXT NOT NULL DEFAULT '[]',
    message_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_recommendation_delivery_id ON recommendation_delivery_log(delivery_id);
CREATE INDEX IF NOT EXISTS idx_recommendation_delivery_person_domain ON recommendation_delivery_log(person_id, domain, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_recommendation_delivery_session ON recommendation_delivery_log(session_id, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_recommendation_delivery_conversation ON recommendation_delivery_log(conversation_id, created_at_ms);
"""


__all__ = [
    "LIGHTNOVEL_DOMAIN",
    "UserPreferenceEvent",
    "UserPreferenceStore",
    "build_lightnovel_dynamic_taste_profile",
    "build_user_preference_pack",
    "record_recommendation_delivery",
]
