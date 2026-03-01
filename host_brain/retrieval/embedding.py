"""Embedding service contract with deterministic degrade semantics."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    RETR_EMBED_INPUT_INVALID,
    RETR_EMBED_TIMEOUT,
    RETR_EMBED_UNAVAILABLE,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import EMBEDDING_MODEL_ID, embed_text


S_RET_EMBEDDING = "S_RET_EMBEDDING"
RET_OP_EMBEDDING = "EMBEDDING"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

EmbeddingProvider = Callable[[str], tuple[list[float], str]]


@dataclass(frozen=True)
class EmbeddingResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class EmbeddingServiceContract:
    """Host embedding service contract with auditable degrade mapping."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: EmbeddingProvider | None = None,
        available: bool = True,
        timeout_ms: int = 500,
    ) -> None:
        self.ledger = ledger
        self.provider = provider or embed_text
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)

    def embed(
        self,
        *,
        request_id: str,
        text: str,
        intent_id: str | None = None,
        timeout_ms: int | None = None,
        ts_ms: int | None = None,
    ) -> EmbeddingResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        timeout_value = int(timeout_ms if timeout_ms is not None else self.timeout_ms)
        normalized = str(text or "").strip()

        if not normalized:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_INPUT_INVALID,
                detail="embedding input text is empty",
                latency_ms=0,
                model_id=EMBEDDING_MODEL_ID,
                vector=[],
                text=normalized,
                ts_ms=now_value,
            )

        if not self.available:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_UNAVAILABLE,
                detail="embedding service unavailable; retrieval degraded",
                latency_ms=0,
                model_id=EMBEDDING_MODEL_ID,
                vector=[],
                text=normalized,
                ts_ms=now_value,
            )

        start = perf_counter()
        try:
            vector, model_id = self.provider(normalized)
        except TimeoutError:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_TIMEOUT,
                detail=f"embedding provider timeout>{timeout_value}ms",
                latency_ms=latency_ms,
                model_id=EMBEDDING_MODEL_ID,
                vector=[],
                text=normalized,
                ts_ms=now_value,
            )
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_UNAVAILABLE,
                detail=f"embedding provider unavailable: {exc}",
                latency_ms=latency_ms,
                model_id=EMBEDDING_MODEL_ID,
                vector=[],
                text=normalized,
                ts_ms=now_value,
            )

        latency_ms = int(round((perf_counter() - start) * 1000))
        if latency_ms > timeout_value:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_TIMEOUT,
                detail=f"embedding provider slow: {latency_ms}ms>{timeout_value}ms",
                latency_ms=latency_ms,
                model_id=str(model_id or EMBEDDING_MODEL_ID),
                vector=[],
                text=normalized,
                ts_ms=now_value,
            )

        normalized_vector = _normalize_vector(vector)
        output = {
            "embedding_vector": normalized_vector,
            "embedding_model_id": str(model_id or EMBEDDING_MODEL_ID),
            "latency_ms": latency_ms,
            "status": STATUS_OK,
            "stage": S_RET_EMBEDDING,
            "reason_code": None,
            "degraded": False,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_OK,
            reason_code=None,
            detail="embedding generated",
            payload={
                "text_length": len(normalized),
                "embedding_dim": len(normalized_vector),
                "latency_ms": latency_ms,
                "output_status": STATUS_OK,
            },
            ts_ms=now_value,
        )
        return EmbeddingResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_RET_EMBEDDING,
            detail="embedding generated",
            output=output,
        )

    def _degrade(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        reason_code: str,
        detail: str,
        latency_ms: int,
        model_id: str,
        vector: list[float],
        text: str,
        ts_ms: int,
    ) -> EmbeddingResult:
        output = {
            "embedding_vector": list(vector),
            "embedding_model_id": str(model_id),
            "latency_ms": int(latency_ms),
            "status": STATUS_DEGRADED,
            "stage": S_RET_EMBEDDING,
            "reason_code": reason_code,
            "degraded": True,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            detail=detail,
            payload={
                "text_length": len(text),
                "embedding_dim": len(vector),
                "latency_ms": int(latency_ms),
                "output_status": STATUS_DEGRADED,
            },
            ts_ms=ts_ms,
        )
        return EmbeddingResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=S_RET_EMBEDDING,
            detail=detail,
            output=output,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        status: str,
        reason_code: str | None,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.ledger is not None and hasattr(self.ledger, "log_retrieval_event"):
            self.ledger.log_retrieval_event(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "op_kind": RET_OP_EMBEDDING,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_RET_EMBEDDING,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_RET_EMBEDDING,
                reason_code=reason_code,
                detail=detail,
            )


def _normalize_vector(raw: list[float] | tuple[float, ...] | Any) -> list[float]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [float(value) for value in raw]
