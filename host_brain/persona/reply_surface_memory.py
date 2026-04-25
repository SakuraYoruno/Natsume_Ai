from __future__ import annotations

import json
import os
import random
import hashlib
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.persona.fallback_policy import (
    SURFACE_CACHE_POLICY_VERSION,
    is_forbidden_surface_text,
    is_surface_cacheable_reply,
)


_BACKEND_MEMORY = "memory"
_BACKEND_REDIS = "redis"
_ALLOWED_BACKENDS = {_BACKEND_MEMORY, _BACKEND_REDIS}
_FINAL_REPLY_CACHEABLE_TOPICS = {
    "greeting",
    "presence_ping",
    "light_ack",
    "playful_ping",
    "identity",
    "assistant_reply_followup",
}
_FOLLOWUP_CONTEXT_RE = re.compile(r"上一条助手回复前接的用户话是“([^”]{1,160})”")
_FOLLOWUP_SOURCE_RE = re.compile(r"上一条助手回复是“([^”]{1,160})”")


@dataclass(frozen=True)
class ReplySurfaceMemoryConfig:
    backend: str = _BACKEND_MEMORY
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "Natsume:replysurface:"
    redis_timeout_ms: int = 500
    ttl_ms: int = 7 * 24 * 60 * 60 * 1000
    max_entries: int = 32


def load_reply_surface_memory_config() -> ReplySurfaceMemoryConfig:
    explicit_backend = str(os.environ.get("HOST_REPLY_SURFACE_MEMORY_BACKEND") or "").strip().lower()
    discovered_redis_url = str(
        os.environ.get("HOST_REPLY_SURFACE_MEMORY_REDIS_URL")
        or os.environ.get("HOST_RATE_LIMIT_REDIS_URL")
        or ""
    ).strip()
    backend = explicit_backend or _BACKEND_REDIS
    if backend not in _ALLOWED_BACKENDS:
        backend = _BACKEND_MEMORY
    redis_url = str(
        discovered_redis_url
        or "redis://localhost:6379/0"
    ).strip() or "redis://localhost:6379/0"
    redis_key_prefix = str(
        os.environ.get("HOST_REPLY_SURFACE_MEMORY_REDIS_KEY_PREFIX")
        or "Natsume:replysurface:"
    ).strip() or "Natsume:replysurface:"
    try:
        redis_timeout_ms = int(str(os.environ.get("HOST_REPLY_SURFACE_MEMORY_REDIS_TIMEOUT_MS") or "500").strip())
    except ValueError:
        redis_timeout_ms = 500
    try:
        ttl_ms = int(str(os.environ.get("HOST_REPLY_SURFACE_MEMORY_TTL_MS") or str(7 * 24 * 60 * 60 * 1000)).strip())
    except ValueError:
        ttl_ms = 7 * 24 * 60 * 60 * 1000
    try:
        max_entries = int(str(os.environ.get("HOST_REPLY_SURFACE_MEMORY_MAX_ENTRIES") or "32").strip())
    except ValueError:
        max_entries = 32
    return ReplySurfaceMemoryConfig(
        backend=backend,
        redis_url=redis_url,
        redis_key_prefix=redis_key_prefix,
        redis_timeout_ms=max(50, min(redis_timeout_ms, 60_000)),
        ttl_ms=max(60_000, min(ttl_ms, 30 * 24 * 60 * 60 * 1000)),
        max_entries=max(1, min(max_entries, 256)),
    )


def build_reply_surface_memory_key(
    *,
    soul_id: str,
    primary_topic: str,
    tactic_family: str,
    tactic_variant: str,
    render_target_lang: str,
    response_form_reason: str = "",
    context_fingerprint: str = "",
    policy_version: str = SURFACE_CACHE_POLICY_VERSION,
) -> str:
    parts = [
        str(policy_version or "").strip() or SURFACE_CACHE_POLICY_VERSION,
        str(soul_id or "").strip() or "default-soul",
        str(primary_topic or "").strip() or "default-topic",
        str(tactic_family or "").strip() or "default-family",
        str(tactic_variant or "").strip() or "default-variant",
        str(response_form_reason or "").strip() or "default-form",
        str(render_target_lang or "").strip().lower() or "zh",
    ]
    context = re.sub(r"[^a-zA-Z0-9_-]+", "", str(context_fingerprint or "").strip())
    if context:
        parts.append(f"ctx-{context}")
    return "::".join(parts)


def build_assistant_followup_context_fingerprint(*, reply_contract: Any = "", source: Any = "", context: Any = "") -> str:
    source_text = _normalize_metadata_text(source)
    context_text = _normalize_metadata_text(context)
    if reply_contract and (not source_text or not context_text):
        contract = str(reply_contract or "")
        source_match = _FOLLOWUP_SOURCE_RE.search(contract)
        context_match = _FOLLOWUP_CONTEXT_RE.search(contract)
        if source_match:
            source_text = _normalize_metadata_text(source_match.group(1))
        if context_match:
            context_text = _normalize_metadata_text(context_match.group(1))
    if not source_text or not context_text:
        return ""
    normalized = "\n".join(
        re.sub(r"[\s\u3000。！？?!，,、~～…]+", "", item).strip().lower()
        for item in (source_text, context_text)
    )
    if not normalized.strip():
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _normalize_metadata_text(raw: Any) -> str:
    return str(raw or "").strip()


def _metadata_matches(
    metadata: Mapping[str, Any],
    *,
    soul_id: str,
    primary_topic: str,
    tactic_family: str = "",
    tactic_variant: str = "",
    response_form_reason: str = "",
) -> bool:
    if soul_id and _normalize_metadata_text(metadata.get("soul_id")) != soul_id:
        return False
    if primary_topic and _normalize_metadata_text(metadata.get("primary_topic")) != primary_topic:
        return False
    if tactic_family and _normalize_metadata_text(metadata.get("tactic_family")) != tactic_family:
        return False
    if tactic_variant and _normalize_metadata_text(metadata.get("tactic_variant")) != tactic_variant:
        return False
    if response_form_reason and _normalize_metadata_text(metadata.get("response_form_reason")) != response_form_reason:
        return False
    return True


class ReplySurfaceMemoryStore:
    def remember(self, *, key: str, text: str, ts_ms: int | None = None, request_id: str = "", session_id: str = "") -> None:
        raise NotImplementedError

    def sample(self, *, key: str, exclude: Iterable[str] | None = None) -> str:
        raise NotImplementedError


class InMemoryReplySurfaceMemoryStore(ReplySurfaceMemoryStore):
    def __init__(self, *, ttl_ms: int, max_entries: int) -> None:
        self._ttl_ms = int(ttl_ms)
        self._max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._data: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=self._max_entries))

    def remember(self, *, key: str, text: str, ts_ms: int | None = None, request_id: str = "", session_id: str = "") -> None:
        normalized = str(text or "").strip()
        if not key or not normalized or is_forbidden_surface_text(normalized):
            return
        now_ms = int(ts_ms or int(time.time() * 1000))
        entry = {
            "text": normalized,
            "ts_ms": now_ms,
            "request_id": str(request_id or "").strip(),
            "session_id": str(session_id or "").strip(),
        }
        with self._lock:
            bucket = self._data[key]
            self._prune_locked(bucket, now_ms=now_ms)
            if any(str(item.get("text") or "").strip() == normalized for item in bucket):
                return
            bucket.appendleft(entry)

    def sample(self, *, key: str, exclude: Iterable[str] | None = None) -> str:
        if not key:
            return ""
        excluded = {str(item or "").strip() for item in list(exclude or []) if str(item or "").strip()}
        now_ms = int(time.time() * 1000)
        with self._lock:
            bucket = self._data.get(key)
            if not bucket:
                return ""
            self._prune_locked(bucket, now_ms=now_ms)
            candidates = [
                str(item.get("text") or "").strip()
                for item in bucket
                if str(item.get("text") or "").strip()
                and not is_forbidden_surface_text(str(item.get("text") or "").strip())
            ]
        if not candidates:
            return ""
        non_repeated = [item for item in candidates if item not in excluded]
        pool = non_repeated or candidates
        return random.choice(pool)

    def _prune_locked(self, bucket: deque[dict[str, Any]], *, now_ms: int) -> None:
        if self._ttl_ms <= 0:
            return
        while bucket and (now_ms - int(bucket[-1].get("ts_ms") or 0)) > self._ttl_ms:
            bucket.pop()


class RedisReplySurfaceMemoryStore(ReplySurfaceMemoryStore):
    def __init__(self, *, redis_url: str, key_prefix: str, timeout_ms: int, ttl_ms: int, max_entries: int) -> None:
        self._redis_url = str(redis_url)
        self._key_prefix = str(key_prefix)
        self._timeout_ms = int(timeout_ms)
        self._ttl_ms = int(ttl_ms)
        self._max_entries = int(max_entries)
        self._client: Any = None
        self._connect()

    def _connect(self) -> None:
        import redis  # type: ignore[import-untyped]

        self._client = redis.from_url(
            self._redis_url,
            socket_timeout=self._timeout_ms / 1000.0,
            socket_connect_timeout=self._timeout_ms / 1000.0,
        )
        self._client.ping()

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    def remember(self, *, key: str, text: str, ts_ms: int | None = None, request_id: str = "", session_id: str = "") -> None:
        normalized = str(text or "").strip()
        if not key or not normalized or is_forbidden_surface_text(normalized) or self._client is None:
            return
        payload = json.dumps(
            {
                "text": normalized,
                "ts_ms": int(ts_ms or int(time.time() * 1000)),
                "request_id": str(request_id or "").strip(),
                "session_id": str(session_id or "").strip(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        full_key = self._full_key(key)
        existing = self._client.lrange(full_key, 0, self._max_entries - 1)
        for item in existing:
            try:
                parsed = json.loads(item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item))
            except Exception:
                continue
            if str(parsed.get("text") or "").strip() == normalized:
                return
        pipe = self._client.pipeline()
        pipe.lpush(full_key, payload)
        pipe.ltrim(full_key, 0, self._max_entries - 1)
        pipe.pexpire(full_key, self._ttl_ms)
        pipe.execute()

    def sample(self, *, key: str, exclude: Iterable[str] | None = None) -> str:
        if not key or self._client is None:
            return ""
        excluded = {str(item or "").strip() for item in list(exclude or []) if str(item or "").strip()}
        rows = self._client.lrange(self._full_key(key), 0, self._max_entries - 1)
        candidates: list[str] = []
        for item in rows:
            try:
                parsed = json.loads(item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item))
            except Exception:
                continue
            text = str(parsed.get("text") or "").strip()
            if text and not is_forbidden_surface_text(text):
                candidates.append(text)
        if not candidates:
            return ""
        non_repeated = [item for item in candidates if item not in excluded]
        pool = non_repeated or candidates
        return random.choice(pool)


class DegradingReplySurfaceMemoryStore(ReplySurfaceMemoryStore):
    def __init__(self, *, primary: ReplySurfaceMemoryStore | None, fallback: InMemoryReplySurfaceMemoryStore) -> None:
        self._primary = primary
        self._fallback = fallback

    def remember(self, *, key: str, text: str, ts_ms: int | None = None, request_id: str = "", session_id: str = "") -> None:
        try:
            if self._primary is not None:
                self._primary.remember(key=key, text=text, ts_ms=ts_ms, request_id=request_id, session_id=session_id)
                return
        except Exception:
            pass
        self._fallback.remember(key=key, text=text, ts_ms=ts_ms, request_id=request_id, session_id=session_id)

    def sample(self, *, key: str, exclude: Iterable[str] | None = None) -> str:
        try:
            if self._primary is not None:
                text = self._primary.sample(key=key, exclude=exclude)
                if text:
                    return text
        except Exception:
            pass
        return self._fallback.sample(key=key, exclude=exclude)


def create_reply_surface_memory_store(config: ReplySurfaceMemoryConfig | None = None) -> ReplySurfaceMemoryStore:
    cfg = config or load_reply_surface_memory_config()
    fallback = InMemoryReplySurfaceMemoryStore(ttl_ms=cfg.ttl_ms, max_entries=cfg.max_entries)
    if cfg.backend != _BACKEND_REDIS:
        return fallback
    try:
        primary = RedisReplySurfaceMemoryStore(
            redis_url=cfg.redis_url,
            key_prefix=cfg.redis_key_prefix,
            timeout_ms=cfg.redis_timeout_ms,
            ttl_ms=cfg.ttl_ms,
            max_entries=cfg.max_entries,
        )
    except Exception:
        primary = None
    return DegradingReplySurfaceMemoryStore(primary=primary, fallback=fallback)


def prime_reply_surface_memory_from_turn_history(
    *,
    memory_store: ReplySurfaceMemoryStore,
    turn_store: Any,
    limit: int = 512,
) -> int:
    try:
        turns = list(turn_store.query_recent_assistant(limit=max(1, int(limit))))
    except Exception:
        return 0
    remembered = 0
    for turn in turns:
        metadata = dict(getattr(turn, "metadata", None) or {})
        soul_id = _normalize_metadata_text(metadata.get("soul_id"))
        primary_topic = _normalize_metadata_text(metadata.get("primary_topic"))
        tactic_family = _normalize_metadata_text(metadata.get("tactic_family"))
        tactic_variant = _normalize_metadata_text(metadata.get("tactic_variant"))
        response_form_reason = _normalize_metadata_text(metadata.get("response_form_reason"))
        context_fingerprint = _normalize_metadata_text(metadata.get("reply_surface_context_fingerprint"))
        response_form = _normalize_metadata_text(metadata.get("response_form"))
        provider_name = _normalize_metadata_text(metadata.get("provider_name"))
        model_id = _normalize_metadata_text(metadata.get("model_id"))
        fallback_used = bool(metadata.get("fallback"))
        content = str(getattr(turn, "content", "") or "").strip()
        message_kind = _normalize_metadata_text(metadata.get("message_kind"))
        source = _normalize_metadata_text(metadata.get("source") or metadata.get("reply_source"))
        if (
            not soul_id
            or not primary_topic
            or primary_topic not in _FINAL_REPLY_CACHEABLE_TOPICS
            or (primary_topic == "assistant_reply_followup" and not context_fingerprint)
            or not tactic_family
            or response_form not in {"text", "text_plus_emoji", "text_plus_sticker"}
            or provider_name == "host"
            or model_id == "deterministic-plan-renderer"
            or fallback_used
            or message_kind not in {"", "final_reply"}
            or source in {"transport_ack", "quick_ack", "gateway_ack", "fallback_policy"}
            or len(content) < 4
            or not is_surface_cacheable_reply(text=content, metadata=metadata)
        ):
            continue
        memory_store.remember(
            key=build_reply_surface_memory_key(
                soul_id=soul_id,
                primary_topic=primary_topic,
                tactic_family=tactic_family,
                tactic_variant=tactic_variant,
                response_form_reason=response_form_reason,
                render_target_lang=str(getattr(turn, "lang", "") or "").strip() or "zh",
                context_fingerprint=context_fingerprint,
            ),
            text=content,
            ts_ms=int(getattr(turn, "ts_ms", 0) or 0),
            request_id=str(getattr(turn, "request_id", "") or "").strip(),
            session_id=str(getattr(turn, "session_id", "") or "").strip(),
        )
        remembered += 1
    return remembered


def sample_reply_surface_from_turn_history(
    *,
    turn_store: Any,
    soul_id: str,
    primary_topic: str,
    tactic_family: str,
    tactic_variant: str,
    response_form_reason: str,
    render_target_lang: str,
    context_fingerprint: str = "",
    exclude: Iterable[str] | None = None,
    limit: int = 256,
) -> str:
    try:
        turns = list(
            turn_store.query_recent_assistant(
                limit=max(1, int(limit)),
                lang=render_target_lang,
            )
        )
    except Exception:
        return ""
    if str(primary_topic or "").strip() not in _FINAL_REPLY_CACHEABLE_TOPICS:
        return ""
    normalized_context_fingerprint = _normalize_metadata_text(context_fingerprint)
    if str(primary_topic or "").strip() == "assistant_reply_followup" and not normalized_context_fingerprint:
        return ""
    excluded = {str(item or "").strip() for item in list(exclude or []) if str(item or "").strip()}
    candidate_groups: list[list[str]] = [[], [], [], []]
    for turn in turns:
        metadata = dict(getattr(turn, "metadata", None) or {})
        content = str(getattr(turn, "content", "") or "").strip()
        if (
            not content
            or len(content) < 4
            or bool(metadata.get("fallback"))
            or _normalize_metadata_text(metadata.get("provider_name")) == "host"
            or _normalize_metadata_text(metadata.get("model_id")) == "deterministic-plan-renderer"
            or _normalize_metadata_text(metadata.get("message_kind")) not in {"", "final_reply"}
            or not is_surface_cacheable_reply(text=content, metadata=metadata)
        ):
            continue
        if (
            str(primary_topic or "").strip() == "assistant_reply_followup"
            and _normalize_metadata_text(metadata.get("reply_surface_context_fingerprint")) != normalized_context_fingerprint
        ):
            continue
        if not _metadata_matches(
            metadata,
            soul_id=soul_id,
            primary_topic=primary_topic,
        ):
            continue
        if _metadata_matches(
            metadata,
            soul_id=soul_id,
            primary_topic=primary_topic,
            tactic_family=tactic_family,
            tactic_variant=tactic_variant,
            response_form_reason=response_form_reason,
        ):
            candidate_groups[0].append(content)
        elif _metadata_matches(
            metadata,
            soul_id=soul_id,
            primary_topic=primary_topic,
            tactic_family=tactic_family,
            response_form_reason=response_form_reason,
        ):
            candidate_groups[1].append(content)
        elif _metadata_matches(
            metadata,
            soul_id=soul_id,
            primary_topic=primary_topic,
            tactic_family=tactic_family,
        ):
            candidate_groups[2].append(content)
        else:
            candidate_groups[3].append(content)
    for group in candidate_groups:
        if not group:
            continue
        non_repeated = [item for item in group if item not in excluded]
        pool = non_repeated or group
        if pool:
            return random.choice(pool)
    return ""


__all__ = [
    "ReplySurfaceMemoryConfig",
    "ReplySurfaceMemoryStore",
    "InMemoryReplySurfaceMemoryStore",
    "RedisReplySurfaceMemoryStore",
    "DegradingReplySurfaceMemoryStore",
    "build_reply_surface_memory_key",
    "build_assistant_followup_context_fingerprint",
    "create_reply_surface_memory_store",
    "load_reply_surface_memory_config",
    "prime_reply_surface_memory_from_turn_history",
    "sample_reply_surface_from_turn_history",
    "SURFACE_CACHE_POLICY_VERSION",
]
