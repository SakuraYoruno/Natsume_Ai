"""App-local ownership for Host memory, state and semantic-read runtimes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
from threading import RLock
from typing import Any
from urllib.parse import urlparse

from host_brain.core.emotion_timeline import EmotionTimeline
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import embed_text, load_chroma_runtime_config
from host_brain.persistence.emotion_timeline_query_repository import (
    EmotionTimelineQueryRepository,
)


Factory = Callable[..., Any]
ServiceProvider = Callable[[], Any]

_logger = logging.getLogger("Natsume.host_memory_runtime")

_VALENCE_BY_EMOTION = {
    "HAPPY": 0.60,
    "EXCITED": 0.80,
    "CALM": 0.20,
    "THINKING": 0.0,
    "NEUTRAL": 0.0,
    "SAD": -0.60,
    "ANGRY": -0.70,
    "ANXIOUS": -0.40,
    "FRUSTRATED": -0.50,
    "LOVING": 0.80,
    "EMBARRASSED": -0.20,
    "CONFUSED": -0.10,
    "BORED": -0.30,
    "PROUD": 0.70,
    "GRATEFUL": 0.70,
    "SURPRISED": 0.30,
}
_AROUSAL_BY_EMOTION = {
    "HAPPY": 0.40,
    "EXCITED": 0.80,
    "CALM": -0.40,
    "THINKING": 0.20,
    "NEUTRAL": 0.0,
    "SAD": -0.20,
    "ANGRY": 0.80,
    "ANXIOUS": 0.70,
    "FRUSTRATED": 0.60,
    "LOVING": 0.30,
    "EMBARRASSED": 0.50,
    "CONFUSED": 0.30,
    "BORED": -0.50,
    "PROUD": 0.50,
    "GRATEFUL": 0.20,
    "SURPRISED": 0.70,
}


@dataclass(frozen=True, slots=True)
class HostSemanticRecallSettings:
    collection_name: str
    top_k: int
    similarity_threshold: float
    use_mock: bool
    chroma_host: str
    chroma_port: int
    vector_dim: int


class HostMemoryRuntimeOwner:
    """Own one exact memory/state runtime object graph for one Host root."""

    def __init__(
        self,
        *,
        ledger_provider: ServiceProvider,
        memory_guard_factory: Factory,
        state_arbiter_factory: Factory,
        state_write_arbiter_factory: Factory,
        episodic_store_adapter_factory: Factory,
        distillation_engine_factory: Factory,
        semantic_recaller_factory: Factory,
        semantic_recaller_decorator: Callable[[Any], Any] | None = None,
        semantic_settings_provider: Callable[
            [], HostSemanticRecallSettings
        ] = lambda: load_host_semantic_recall_settings(),
        embedder: Factory = embed_text,
        emotion_timeline_factory: Factory = EmotionTimeline,
        emotion_query_repository_factory: Factory = EmotionTimelineQueryRepository,
        clock_ms: Callable[[], int] = now_ms,
    ) -> None:
        self._ledger_provider = ledger_provider
        self._memory_guard_factory = memory_guard_factory
        self._state_arbiter_factory = state_arbiter_factory
        self._state_write_arbiter_factory = state_write_arbiter_factory
        self._episodic_store_adapter_factory = episodic_store_adapter_factory
        self._distillation_engine_factory = distillation_engine_factory
        self._semantic_recaller_factory = semantic_recaller_factory
        self._semantic_recaller_decorator = semantic_recaller_decorator
        self._semantic_settings_provider = semantic_settings_provider
        self._embedder = embedder
        self._emotion_timeline_factory = emotion_timeline_factory
        self._emotion_query_repository_factory = emotion_query_repository_factory
        self._clock_ms = clock_ms
        self._lock = RLock()
        self._ledger: Any | None = None
        self._memory_guard: Any | None = None
        self._state_arbiter: Any | None = None
        self._state_write_arbiter: Any | None = None
        self._distillation_engine: Any | None = None
        self._semantic_recaller: Any | None = None
        self._semantic_settings: HostSemanticRecallSettings | None = None
        self._emotion_timeline: Any | None = None

    def require_memory_guard(self) -> Any:
        with self._lock:
            if self._memory_guard is None:
                ledger = self._require_ledger()

                def audit_writer(event: str, detail: dict[str, Any]) -> None:
                    try:
                        ledger.log_memory_op(row={"op_type": event, **detail})
                    except Exception as exc:
                        _logger.warning("MemoryGuard 审计写入失败: %s", exc)

                self._memory_guard = self._memory_guard_factory(
                    audit_writer=audit_writer
                )
            return self._memory_guard

    def require_state_arbiter(self) -> Any:
        with self._lock:
            if self._state_arbiter is None:
                ledger = self._require_ledger()

                async def audit_writer(
                    event: str,
                    detail: dict[str, Any],
                ) -> None:
                    try:
                        ledger.log_error(
                            request_id=str(
                                detail.get("request_id") or "state_arbiter"
                            ),
                            stage=event,
                            reason_code=event,
                            detail=str(detail),
                        )
                    except Exception as exc:
                        _logger.warning("StateArbiter 审计写入失败: %s", exc)

                self._state_arbiter = self._state_arbiter_factory(
                    audit_writer=audit_writer
                )
            return self._state_arbiter

    def require_state_write_arbiter(self) -> Any:
        with self._lock:
            if self._state_write_arbiter is None:
                ledger = self._require_ledger()

                def audit_writer(event: str, detail: dict[str, Any]) -> None:
                    try:
                        ledger.log_error(
                            request_id=str(
                                detail.get("request_id")
                                or "state_write_arbiter"
                            ),
                            stage=event,
                            reason_code=str(
                                detail.get("reason_code") or event
                            ),
                            detail=str(detail),
                        )
                    except Exception as exc:
                        _logger.warning(
                            "StateWriteArbiter 审计写入失败: %s",
                            exc,
                        )

                self._state_write_arbiter = self._state_write_arbiter_factory(
                    audit_writer=audit_writer
                )
            return self._state_write_arbiter

    def require_distillation_engine(self) -> Any:
        with self._lock:
            if self._distillation_engine is None:
                ledger = self._require_ledger()
                memory_guard = self.require_memory_guard()
                episodic_store = self._episodic_store_adapter_factory(
                    memory_guard=memory_guard,
                    ledger=ledger,
                )
                self._distillation_engine = self._distillation_engine_factory(
                    episodic_store=episodic_store,
                    memory_guard=memory_guard,
                    state_arbiter=self.require_state_arbiter(),
                    ledger=ledger,
                )
            return self._distillation_engine

    def require_semantic_recaller(self) -> Any:
        with self._lock:
            if self._semantic_recaller is None:
                settings = self._require_semantic_settings()

                def embed_for_runtime_collection(text: str) -> list[float]:
                    values, _model_id = self._embedder(
                        str(text or ""),
                        dim=int(settings.vector_dim),
                    )
                    return list(values or [])

                semantic_recaller = self._semantic_recaller_factory(
                    collection_name=settings.collection_name,
                    top_k=int(settings.top_k),
                    similarity_threshold=float(
                        settings.similarity_threshold
                    ),
                    use_mock=bool(settings.use_mock),
                    chroma_host=settings.chroma_host,
                    chroma_port=int(settings.chroma_port),
                    embed_fn=embed_for_runtime_collection,
                )
                self._semantic_recaller = (
                    self._semantic_recaller_decorator(semantic_recaller)
                    if self._semantic_recaller_decorator is not None
                    else semantic_recaller
                )
            return self._semantic_recaller

    def require_emotion_timeline(self) -> Any:
        with self._lock:
            if self._emotion_timeline is None:
                self._emotion_timeline = build_host_emotion_timeline(
                    ledger=self._require_ledger(),
                    timeline_factory=self._emotion_timeline_factory,
                    query_repository_factory=(
                        self._emotion_query_repository_factory
                    ),
                    clock_ms=self._clock_ms,
                )
            return self._emotion_timeline

    def _require_ledger(self) -> Any:
        if self._ledger is None:
            ledger = self._ledger_provider()
            if ledger is None:
                raise RuntimeError("HOST_MEMORY_RUNTIME_LEDGER_REQUIRED")
            self._ledger = ledger
        return self._ledger

    def _require_semantic_settings(self) -> HostSemanticRecallSettings:
        if self._semantic_settings is None:
            settings = self._semantic_settings_provider()
            if not isinstance(settings, HostSemanticRecallSettings):
                raise TypeError(
                    "semantic_settings_provider must return "
                    "HostSemanticRecallSettings"
                )
            self._semantic_settings = settings
        return self._semantic_settings


def load_host_semantic_recall_settings(
    *,
    env: Mapping[str, Any] | None = None,
) -> HostSemanticRecallSettings:
    source = dict(os.environ if env is None else env)
    cfg_result = load_chroma_runtime_config(env=source)
    cfg = cfg_result.config if cfg_result.success else None
    default_use_mock = not bool(cfg is not None and cfg.enabled)
    collection_name = str(
        source.get("SEMANTIC_RECALL_COLLECTION")
        or source.get("SEMANTIC_RECALL_COLLECTION_NAME")
        or (cfg.default_collection if cfg is not None else "")
        or "episodic"
    ).strip()
    endpoint = str(
        source.get("SEMANTIC_RECALL_CHROMA_ENDPOINT")
        or (cfg.endpoint if cfg is not None else "")
        or ""
    ).strip()
    parsed = urlparse(endpoint or "http://localhost:8000")
    chroma_host = str(
        source.get("SEMANTIC_RECALL_CHROMA_HOST")
        or parsed.hostname
        or source.get("CHROMA_HOST")
        or "localhost"
    ).strip()
    profiles = dict(getattr(cfg, "collection_profiles", {}) or {})
    default_profile = str(
        getattr(cfg, "default_collection", "") or ""
    )
    threshold_text = str(
        source.get("SEMANTIC_RECALL_THRESHOLD") or "0.70"
    ).strip()
    try:
        threshold = float(threshold_text)
    except ValueError:
        threshold = 0.70
    return HostSemanticRecallSettings(
        collection_name=collection_name,
        top_k=_parse_bounded_int(
            source.get("SEMANTIC_RECALL_TOP_K"),
            default=5,
            minimum=1,
            maximum=20,
        ),
        similarity_threshold=threshold,
        use_mock=_parse_bool(
            source.get("SEMANTIC_RECALL_USE_MOCK"),
            default=default_use_mock,
        ),
        chroma_host=chroma_host,
        chroma_port=_parse_bounded_int(
            source.get("SEMANTIC_RECALL_CHROMA_PORT"),
            default=int(parsed.port or 8000),
            minimum=1,
            maximum=65_535,
        ),
        vector_dim=_parse_bounded_int(
            source.get("SEMANTIC_RECALL_VECTOR_DIM"),
            default=int(
                profiles.get(collection_name)
                or profiles.get(default_profile)
                or 1024
            ),
            minimum=1,
            maximum=8192,
        ),
    )


def build_host_emotion_timeline(
    *,
    ledger: Any,
    timeline_factory: Factory = EmotionTimeline,
    query_repository_factory: Factory = EmotionTimelineQueryRepository,
    clock_ms: Callable[[], int] = now_ms,
) -> Any:
    timeline = timeline_factory(max_entries=200)
    rows: list[Any] = []
    try:
        canonical_db_path = str(
            getattr(ledger, "canonical_db_path", "") or ""
        ).strip()
        if canonical_db_path and canonical_db_path != ":memory:":
            rows = list(
                query_repository_factory(canonical_db_path).recent_actions(
                    since_ms=int(clock_ms()) - 24 * 3600 * 1000,
                    limit=150,
                )
            )
    except Exception as exc:
        _logger.warning(
            "EmotionTimeline 日志预热失败（空时间线降级）: %s",
            exc,
        )

    for row in rows:
        emotion = str(row.emotion_tag or "NEUTRAL").strip().upper()
        timeline.append(
            ts_ms=int(row.ts_ms),
            emotion_tag=emotion,
            valence=_VALENCE_BY_EMOTION.get(emotion, 0.0),
            arousal=_AROUSAL_BY_EMOTION.get(emotion, 0.0),
            source="restored_from_log",
        )
    _logger.info("EmotionTimeline 已从日志预热 %d 条情绪记录", len(rows))
    return timeline


def _parse_bounded_int(
    raw: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _parse_bool(raw: Any, *, default: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return bool(default)
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


__all__ = [
    "HostMemoryRuntimeOwner",
    "HostSemanticRecallSettings",
    "build_host_emotion_timeline",
    "load_host_semantic_recall_settings",
]
