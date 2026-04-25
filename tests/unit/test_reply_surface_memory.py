from __future__ import annotations

import sys
import time
from types import SimpleNamespace

from host_brain.persona.reply_surface_memory import (
    DegradingReplySurfaceMemoryStore,
    InMemoryReplySurfaceMemoryStore,
    RedisReplySurfaceMemoryStore,
    SURFACE_CACHE_POLICY_VERSION,
    build_assistant_followup_context_fingerprint,
    build_reply_surface_memory_key,
    create_reply_surface_memory_store,
    load_reply_surface_memory_config,
    prime_reply_surface_memory_from_turn_history,
    sample_reply_surface_from_turn_history,
)


def test_inmemory_reply_surface_memory_store_remember_and_sample() -> None:
    store = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)
    now_ms = int(time.time() * 1000)
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="emoji_social_followup",
        tactic_family="soft_emotion_ack",
        tactic_variant="backoff_hold",
        response_form_reason="image_social_followup",
        render_target_lang="zh",
    )

    store.remember(key=key, text="哥哥，猫耳那张我接住了。", ts_ms=now_ms)

    sampled = store.sample(key=key)

    assert sampled == "哥哥，猫耳那张我接住了。"


def test_reply_surface_memory_key_is_policy_versioned() -> None:
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="phatic_greeting",
        render_target_lang="zh",
    )

    assert key.startswith(SURFACE_CACHE_POLICY_VERSION + "::")
    assert build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="phatic_greeting",
        render_target_lang="zh",
        policy_version="surface-old",
    ).startswith("surface-old::")


def test_inmemory_reply_surface_memory_store_rejects_forbidden_text() -> None:
    store = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="phatic_greeting",
        render_target_lang="zh",
    )

    store.remember(key=key, text="哥哥，你真正想问什么？", ts_ms=int(time.time() * 1000))

    assert store.sample(key=key) == ""


def test_assistant_followup_context_fingerprint_is_stable() -> None:
    contract = "上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。"

    from_contract = build_assistant_followup_context_fingerprint(reply_contract=contract)
    from_parts = build_assistant_followup_context_fingerprint(source="唔。", context="知道什么")

    assert from_contract == from_parts
    assert len(from_contract) == 16


def test_inmemory_reply_surface_memory_store_ignores_duplicate_text() -> None:
    store = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)
    now_ms = int(time.time() * 1000)
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="identity",
        tactic_family="ack_repeat_then_light_probe",
        tactic_variant="confirm_real_question",
        response_form_reason="default-form",
        render_target_lang="zh",
    )

    store.remember(key=key, text="哥哥，怎么又问起这个啦？我是新海天呀。", ts_ms=now_ms)
    store.remember(key=key, text="哥哥，怎么又问起这个啦？我是新海天呀。", ts_ms=now_ms + 1)

    assert store.sample(key=key) == "哥哥，怎么又问起这个啦？我是新海天呀。"


def test_inmemory_reply_surface_memory_store_exclude_prefers_other_candidate() -> None:
    store = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)
    now_ms = int(time.time() * 1000)
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="emoji_social_followup",
        tactic_family="tease_image_ack",
        tactic_variant="deny_nudge",
        response_form_reason="image_social_followup",
        render_target_lang="zh",
    )

    store.remember(key=key, text="哥哥，猫耳那张我接住了。", ts_ms=now_ms)
    store.remember(key=key, text="哥哥，这张小表情我看见了。", ts_ms=now_ms + 1)

    sampled = store.sample(
        key=key,
        exclude=["哥哥，这张小表情我看见了。"],
    )

    assert sampled == "哥哥，猫耳那张我接住了。"


def test_load_reply_surface_memory_config_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_BACKEND", "redis")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_REDIS_URL", "redis://example/2")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_REDIS_KEY_PREFIX", "test:reply:")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_REDIS_TIMEOUT_MS", "1200")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_TTL_MS", "180000")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_MAX_ENTRIES", "12")

    config = load_reply_surface_memory_config()

    assert config.backend == "redis"
    assert config.redis_url == "redis://example/2"
    assert config.redis_key_prefix == "test:reply:"
    assert config.redis_timeout_ms == 1200
    assert config.ttl_ms == 180000
    assert config.max_entries == 12


def test_load_reply_surface_memory_config_falls_back_on_invalid_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_BACKEND", "weird")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_REDIS_TIMEOUT_MS", "oops")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_TTL_MS", "oops")
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_MAX_ENTRIES", "oops")

    config = load_reply_surface_memory_config()

    assert config.backend == "memory"
    assert config.redis_timeout_ms == 500
    assert config.ttl_ms == 7 * 24 * 60 * 60 * 1000
    assert config.max_entries == 32


def test_load_reply_surface_memory_config_auto_enables_redis_from_url(monkeypatch) -> None:
    monkeypatch.delenv("HOST_REPLY_SURFACE_MEMORY_BACKEND", raising=False)
    monkeypatch.setenv("HOST_RATE_LIMIT_REDIS_URL", "redis://127.0.0.1:6379/0")

    config = load_reply_surface_memory_config()

    assert config.backend == "redis"
    assert config.redis_url == "redis://127.0.0.1:6379/0"


def test_reply_surface_memory_store_base_methods_raise() -> None:
    from host_brain.persona.reply_surface_memory import ReplySurfaceMemoryStore

    store = ReplySurfaceMemoryStore()

    try:
        store.remember(key="k", text="x")
    except NotImplementedError:
        pass
    else:  # pragma: no cover
        raise AssertionError("remember should raise")

    try:
        store.sample(key="k")
    except NotImplementedError:
        pass
    else:  # pragma: no cover
        raise AssertionError("sample should raise")


def test_create_reply_surface_memory_store_uses_memory_backend(monkeypatch) -> None:
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_BACKEND", "memory")

    store = create_reply_surface_memory_store()

    assert isinstance(store, InMemoryReplySurfaceMemoryStore)


def test_create_reply_surface_memory_store_degrades_when_redis_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_BACKEND", "redis")
    monkeypatch.setattr(
        "host_brain.persona.reply_surface_memory.RedisReplySurfaceMemoryStore",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
    )

    store = create_reply_surface_memory_store()

    assert isinstance(store, DegradingReplySurfaceMemoryStore)


def test_prime_reply_surface_memory_from_turn_history_seeds_store() -> None:
    capture = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)
    now_ms = int(time.time() * 1000)
    turn = SimpleNamespace(
        content="哥哥，我在呀。",
        ts_ms=now_ms,
        request_id="req-1",
        session_id="sess-1",
        lang="zh",
        metadata={
            "soul_id": "soul",
            "primary_topic": "greeting",
            "tactic_family": "phatic_wave",
            "tactic_variant": "greeting",
            "response_form": "text_plus_emoji",
            "response_form_reason": "phatic_greeting",
            "provider_name": "gemini",
            "model_id": "gemini-2.5-pro",
            "fallback": False,
        },
    )
    turn_store = SimpleNamespace(query_recent_assistant=lambda **kwargs: [turn])

    remembered = prime_reply_surface_memory_from_turn_history(
        memory_store=capture,
        turn_store=turn_store,
        limit=32,
    )

    assert remembered == 1
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="phatic_greeting",
        render_target_lang="zh",
    )
    assert capture.sample(key=key) == "哥哥，我在呀。"


def test_prime_reply_surface_memory_from_turn_history_seeds_assistant_followup_with_context_key() -> None:
    capture = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)
    now_ms = int(time.time() * 1000)
    fingerprint = build_assistant_followup_context_fingerprint(source="唔。", context="知道什么")
    turn = SimpleNamespace(
        content="哥哥，刚才那句唔我没接好，是在顺着你问的知道什么往下说。",
        ts_ms=now_ms,
        request_id="req-followup-1",
        session_id="sess-followup-1",
        lang="zh",
        metadata={
            "soul_id": "soul",
            "primary_topic": "assistant_reply_followup",
            "tactic_family": "explain_previous_reply",
            "tactic_variant": "clarify_meaning",
            "response_form": "text",
            "response_form_reason": "default_text",
            "reply_surface_context_fingerprint": fingerprint,
            "provider_name": "gemini",
            "model_id": "gemini-2.5-pro",
            "fallback": False,
        },
    )
    turn_store = SimpleNamespace(query_recent_assistant=lambda **kwargs: [turn])

    remembered = prime_reply_surface_memory_from_turn_history(
        memory_store=capture,
        turn_store=turn_store,
        limit=32,
    )

    assert remembered == 1
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="assistant_reply_followup",
        tactic_family="explain_previous_reply",
        tactic_variant="clarify_meaning",
        response_form_reason="default_text",
        render_target_lang="zh",
        context_fingerprint=fingerprint,
    )
    assert "没接好" in capture.sample(key=key)


def test_sample_reply_surface_from_turn_history_prefers_exact_then_relaxed() -> None:
    exact_turn = SimpleNamespace(
        content="哥哥，我在呀。",
        metadata={
            "soul_id": "soul",
            "primary_topic": "greeting",
            "tactic_family": "phatic_wave",
            "tactic_variant": "greeting",
            "response_form_reason": "phatic_greeting",
            "provider_name": "gemini",
            "model_id": "gemini-2.5-pro",
            "fallback": False,
        },
        lang="zh",
    )
    relaxed_turn = SimpleNamespace(
        content="哥哥，原来你在找我呀。",
        metadata={
            "soul_id": "soul",
            "primary_topic": "greeting",
            "tactic_family": "phatic_wave",
            "tactic_variant": "presence",
            "response_form_reason": "phatic_presence",
            "provider_name": "gemini",
            "model_id": "gemini-2.5-pro",
            "fallback": False,
        },
        lang="zh",
    )
    turn_store = SimpleNamespace(query_recent_assistant=lambda **kwargs: [relaxed_turn, exact_turn])

    sampled = sample_reply_surface_from_turn_history(
        turn_store=turn_store,
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="phatic_greeting",
        render_target_lang="zh",
    )
    assert sampled == "哥哥，我在呀。"

    sampled_relaxed = sample_reply_surface_from_turn_history(
        turn_store=turn_store,
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="missing",
        response_form_reason="missing",
        render_target_lang="zh",
    )
    assert sampled_relaxed in {"哥哥，我在呀。", "哥哥，原来你在找我呀。"}


def test_sample_reply_surface_from_turn_history_skips_context_heavy_topics() -> None:
    image_turn = SimpleNamespace(
        content="哥哥，好嘛，那我就当自己刚才多想了一点。",
        metadata={
            "soul_id": "soul",
            "primary_topic": "emoji_social_followup",
            "tactic_family": "soft_emotion_ack",
            "tactic_variant": "backoff_hold",
            "response_form_reason": "image_social_followup",
            "provider_name": "gemini",
            "model_id": "gemini-2.5-pro",
            "fallback": False,
        },
        lang="zh",
    )
    turn_store = SimpleNamespace(query_recent_assistant=lambda **kwargs: [image_turn])

    sampled = sample_reply_surface_from_turn_history(
        turn_store=turn_store,
        soul_id="soul",
        primary_topic="emoji_social_followup",
        tactic_family="soft_emotion_ack",
        tactic_variant="backoff_hold",
        response_form_reason="image_social_followup",
        render_target_lang="zh",
    )

    assert sampled == ""


def test_inmemory_reply_surface_memory_store_handles_empty_and_prunes_old_entries() -> None:
    store = InMemoryReplySurfaceMemoryStore(ttl_ms=1, max_entries=4)

    store.remember(key="", text="ignored")
    assert store.sample(key="") == ""

    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="default_text",
        render_target_lang="zh",
    )
    old_ts = int(time.time() * 1000) - 10_000
    store.remember(key=key, text="哦！你好呀。", ts_ms=old_ts)

    assert store.sample(key=key) == ""


class _FakePipeline:
    def __init__(self, client) -> None:  # noqa: ANN001
        self._client = client
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def lpush(self, key: str, payload: str) -> "_FakePipeline":
        self._ops.append(("lpush", (key, payload)))
        return self

    def ltrim(self, key: str, start: int, end: int) -> "_FakePipeline":
        self._ops.append(("ltrim", (key, start, end)))
        return self

    def pexpire(self, key: str, ttl_ms: int) -> "_FakePipeline":
        self._ops.append(("pexpire", (key, ttl_ms)))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for op, args in self._ops:
            results.append(getattr(self._client, op)(*args))
        self._ops.clear()
        return results


class _FakeRedisClient:
    def __init__(self) -> None:
        self.rows: dict[str, list[str]] = {}
        self.expiry: dict[str, int] = {}

    def ping(self) -> bool:
        return True

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        rows = list(self.rows.get(key, []))
        if end >= 0:
            rows = rows[start : end + 1]
        else:
            rows = rows[start:]
        return [item.encode("utf-8") for item in rows]

    def lpush(self, key: str, payload: str) -> int:
        bucket = self.rows.setdefault(key, [])
        bucket.insert(0, payload)
        return len(bucket)

    def ltrim(self, key: str, start: int, end: int) -> bool:
        rows = self.rows.setdefault(key, [])
        if end >= 0:
            self.rows[key] = rows[start : end + 1]
        else:
            self.rows[key] = rows[start:]
        return True

    def pexpire(self, key: str, ttl_ms: int) -> bool:
        self.expiry[key] = ttl_ms
        return True


def test_redis_reply_surface_memory_store_remember_and_sample(monkeypatch) -> None:
    fake_client = _FakeRedisClient()
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: fake_client),
    )

    store = RedisReplySurfaceMemoryStore(
        redis_url="redis://localhost:6379/0",
        key_prefix="test:",
        timeout_ms=500,
        ttl_ms=60_000,
        max_entries=4,
    )
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="identity",
        tactic_family="ack_repeat_then_light_probe",
        tactic_variant="confirm_real_question",
        response_form_reason="default_text",
        render_target_lang="zh",
    )
    now_ms = int(time.time() * 1000)

    store.remember(key=key, text="哥哥，怎么又问起这个啦？我是新海天呀。", ts_ms=now_ms)
    store.remember(key=key, text="哥哥，我是新海天呀。你怎么还确认这个。", ts_ms=now_ms + 1)

    sampled = store.sample(key=key, exclude=["哥哥，我是新海天呀。你怎么还确认这个。"])

    assert sampled == "哥哥，怎么又问起这个啦？我是新海天呀。"
    assert fake_client.expiry["test:" + key] == 60_000


def test_redis_reply_surface_memory_store_handles_invalid_existing_rows(monkeypatch) -> None:
    fake_client = _FakeRedisClient()
    key = "broken"
    fake_client.rows[key] = ["not-json", "{\"text\":\"哥哥，我在呀。\"}"]
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: fake_client),
    )

    store = RedisReplySurfaceMemoryStore(
        redis_url="redis://localhost:6379/0",
        key_prefix="",
        timeout_ms=500,
        ttl_ms=60_000,
        max_entries=4,
    )

    store.remember(key="broken", text="哥哥，在呀在呀，怎么啦？", ts_ms=int(time.time() * 1000))
    sampled = store.sample(key="broken", exclude=["哥哥，在呀在呀，怎么啦？"])

    assert sampled == "哥哥，我在呀。"


def test_redis_reply_surface_memory_store_handles_missing_client(monkeypatch) -> None:
    fake_client = _FakeRedisClient()
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: fake_client),
    )
    store = RedisReplySurfaceMemoryStore(
        redis_url="redis://localhost:6379/0",
        key_prefix="",
        timeout_ms=500,
        ttl_ms=60_000,
        max_entries=4,
    )
    store._client = None  # type: ignore[attr-defined]

    store.remember(key="k", text="x")
    assert store.sample(key="k") == ""


def test_degrading_reply_surface_memory_store_falls_back_to_memory() -> None:
    fallback = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)

    class _BrokenPrimary:
        def remember(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("broken")

        def sample(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("broken")

    store = DegradingReplySurfaceMemoryStore(primary=_BrokenPrimary(), fallback=fallback)
    key = build_reply_surface_memory_key(
        soul_id="soul",
        primary_topic="greeting",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        response_form_reason="default_text",
        render_target_lang="zh",
    )
    now_ms = int(time.time() * 1000)

    store.remember(key=key, text="哦！你好呀。", ts_ms=now_ms)

    assert store.sample(key=key) == "哦！你好呀。"


def test_degrading_reply_surface_memory_store_prefers_primary_when_available() -> None:
    fallback = InMemoryReplySurfaceMemoryStore(ttl_ms=60_000, max_entries=8)

    class _Primary:
        def remember(self, **kwargs):  # noqa: ANN003
            self.last = dict(kwargs)

        def sample(self, **kwargs):  # noqa: ANN003
            _ = kwargs
            return "哥哥，我在呀。"

    primary = _Primary()
    store = DegradingReplySurfaceMemoryStore(primary=primary, fallback=fallback)

    store.remember(key="k", text="哥哥，我在呀。", ts_ms=int(time.time() * 1000))

    assert getattr(primary, "last")["text"] == "哥哥，我在呀。"
    assert store.sample(key="k") == "哥哥，我在呀。"
