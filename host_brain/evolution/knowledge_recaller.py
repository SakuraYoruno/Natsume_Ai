"""Active-only Knowledge OS semantic recall with legacy shadow comparison."""

from __future__ import annotations

import hashlib
import inspect
import time
from typing import Any, Callable, Mapping

from host_brain.contracts.semantic_recall import (
    SemanticHit,
    SemanticRecallResult,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)


ScopeProvider = Callable[[], tuple[str, str]]
ReadModeProvider = Callable[[], str]


class KnowledgeOSSemanticRecaller:
    """Return only activated V2 knowledge; legacy results are audit input."""

    def __init__(
        self,
        *,
        repository: SQLiteEvolutionActivationV2Repository,
        legacy_recaller: Any | None,
        scope_provider: ScopeProvider,
        read_mode_provider: ReadModeProvider | None = None,
    ) -> None:
        if not isinstance(
            repository,
            SQLiteEvolutionActivationV2Repository,
        ):
            raise TypeError(
                "repository must be SQLiteEvolutionActivationV2Repository"
            )
        if not callable(scope_provider):
            raise TypeError("scope_provider must be callable")
        self._repository = repository
        self._legacy_recaller = legacy_recaller
        self._scope_provider = scope_provider
        self._read_mode_provider = (
            read_mode_provider or (lambda: "SHADOW")
        )

    async def recall(
        self,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
        filter_metadata: Mapping[str, Any] | None = None,
    ) -> SemanticRecallResult:
        started = time.monotonic()
        metadata = dict(filter_metadata or {})
        legacy_filter_metadata = {
            key: metadata[key]
            for key in ("session_id", "memory_layer")
            if key in metadata
        }
        default_workspace, default_owner = self._scope_provider()
        workspace_id = str(
            metadata.pop("workspace_id", default_workspace) or ""
        ).strip()
        owner_id = str(
            metadata.pop("owner_id", default_owner) or ""
        ).strip()
        namespace = str(
            metadata.pop("knowledge_namespace", "") or ""
        ).strip()
        if not namespace:
            namespace = f"life-memory://person/{owner_id}"
        metadata.pop("session_id", None)
        metadata.pop("memory_layer", None)
        allowed = {
            f"life-memory://person/{owner_id}",
            f"project-knowledge://workspace/{workspace_id}",
        }
        if namespace not in allowed:
            raise ValueError("KNOWLEDGE_OS_NAMESPACE_SCOPE_DRIFT")
        if metadata:
            raise ValueError(
                "KNOWLEDGE_OS_FILTER_METADATA_UNSUPPORTED:"
                + ",".join(sorted(metadata))
            )

        resolved_top_k = max(1, min(int(top_k or 5), 100))
        resolved_threshold = float(
            0.0 if threshold is None else threshold
        )
        rows = self._repository.search_active_knowledge(
            workspace_id=workspace_id,
            namespace=namespace,
            query=str(query or ""),
            top_k=resolved_top_k,
            threshold=resolved_threshold,
        )
        hits = [
            SemanticHit(
                doc_id=str(row["doc_id"]),
                content=str(row["content"]),
                score=float(row["score"]),
                metadata=dict(row["metadata"]),
                source=str(row["source"]),
            )
            for row in rows
        ]
        legacy_hits, degraded_reason = await self._shadow_compare(
            workspace_id=workspace_id,
            namespace=namespace,
            query=str(query or ""),
            top_k=resolved_top_k,
            threshold=resolved_threshold,
            active=hits,
            legacy_filter_metadata=legacy_filter_metadata,
        )
        mode = _read_mode(self._read_mode_provider())
        selected = hits
        if self._legacy_recaller is not None and (
            mode == "SHADOW"
            or (mode == "CANARY" and not hits)
        ):
            selected = legacy_hits
        return SemanticRecallResult(
            hits=selected,
            query=str(query or ""),
            top_k=resolved_top_k,
            threshold=resolved_threshold,
            total_found=len(selected),
            filtered_count=len(selected),
            degraded=bool(degraded_reason),
            degraded_reason=degraded_reason or None,
            latency_ms=max(0, int((time.monotonic() - started) * 1_000)),
        )

    def add_mock_document(self, *_args: Any, **_kwargs: Any) -> None:
        """Reject the legacy in-memory writer on the production wrapper."""

        raise RuntimeError("KNOWLEDGE_OS_LEGACY_WRITE_RETIRED")

    async def add_document(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        """Require proposal/activation instead of direct Chroma mutation."""

        raise RuntimeError("KNOWLEDGE_OS_LEGACY_WRITE_RETIRED")

    async def add_documents(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        """Require proposal/activation instead of direct Chroma mutation."""

        raise RuntimeError("KNOWLEDGE_OS_LEGACY_WRITE_RETIRED")

    def public_runtime_stats(
        self,
        *,
        observed_at_ms: int,
    ) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        reader = getattr(
            self._legacy_recaller,
            "public_runtime_stats",
            None,
        )
        if callable(reader):
            stats = dict(reader(observed_at_ms=observed_at_ms) or {})
        mode = _read_mode(self._read_mode_provider())
        workspace_id, _owner_id = self._scope_provider()
        evolution = self._repository.public_runtime_summary(
            workspace_id=workspace_id,
        )
        return {
            **stats,
            "evolution_activation": evolution,
            "knowledge_os_read_mode": mode,
            "knowledge_os_read_authority": (
                "V2_ACTIVE_PROJECTION"
                if mode == "ENFORCE"
                else "CANARY_ACTIVE_WITH_LEGACY_FALLBACK"
                if mode == "CANARY"
                else "LEGACY_SHADOW"
            ),
            "legacy_read_authority": (
                "SHADOW_COMPARE_ONLY"
                if mode == "ENFORCE"
                else "READ_AND_SHADOW_COMPARE"
            ),
            "timestamp": int(observed_at_ms),
        }

    def __getattr__(self, name: str) -> Any:
        legacy = object.__getattribute__(self, "_legacy_recaller")
        if legacy is None:
            raise AttributeError(name)
        return getattr(legacy, name)

    async def _shadow_compare(
        self,
        *,
        workspace_id: str,
        namespace: str,
        query: str,
        top_k: int,
        threshold: float,
        active: list[SemanticHit],
        legacy_filter_metadata: Mapping[str, Any],
    ) -> tuple[list[SemanticHit], str]:
        legacy = self._legacy_recaller
        if legacy is None:
            return [], ""
        try:
            value = legacy.recall(
                query,
                top_k=top_k,
                threshold=threshold,
                filter_metadata=(
                    dict(legacy_filter_metadata)
                    if legacy_filter_metadata
                    else None
                ),
            )
            result = await value if inspect.isawaitable(value) else value
            legacy_hits = list(getattr(result, "hits", []) or [])
            self._repository.record_knowledge_shadow_compare(
                workspace_id=workspace_id,
                namespace=namespace,
                query=query,
                active_results=[
                    _shadow_identity(hit)
                    for hit in active
                ],
                legacy_results=[
                    _shadow_identity(hit)
                    for hit in legacy_hits
                ],
            )
            return legacy_hits, ""
        except Exception as exc:
            self._repository.record_failure(
                workspace_id=workspace_id,
                subject_kind="KNOWLEDGE",
                subject_id=namespace,
                operation="LEGACY_SHADOW_COMPARE",
                reason_code="KNOWLEDGE_OS_SHADOW_COMPARE_FAILED",
                detail=f"{type(exc).__name__}:{exc}",
            )
            return [], "knowledge_os_legacy_shadow_compare_failed"


def _read_mode(value: Any) -> str:
    mode = str(value or "").strip().upper()
    if mode not in {"SHADOW", "CANARY", "ENFORCE"}:
        raise ValueError("KNOWLEDGE_OS_READ_MODE_INVALID")
    return mode


def _shadow_identity(hit: Any) -> dict[str, Any]:
    content = str(getattr(hit, "content", "") or "")
    return {
        "doc_id_digest": hashlib.sha256(
            str(getattr(hit, "doc_id", "") or "").encode("utf-8")
        ).hexdigest(),
        "content_digest": hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest(),
        "score": round(float(getattr(hit, "score", 0.0) or 0.0), 6),
        "source": str(getattr(hit, "source", "") or "")[:64],
    }


__all__ = [
    "KnowledgeOSSemanticRecaller",
    "ReadModeProvider",
    "ScopeProvider",
]
