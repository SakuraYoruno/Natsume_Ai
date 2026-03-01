"""Evidence pack fusion contract with replayable references."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import RETR_EPACK_INPUT_INVALID
from host_brain.core.ids import now_ms


S_RET_EVIDENCE_PACK = "S_RET_EVIDENCE_PACK"
RET_OP_EVIDENCE_PACK = "EVIDENCE_PACK"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

SERIALIZATION_VERSION = "evidence_pack_v1"


@dataclass(frozen=True)
class EvidencePackResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class EvidencePackServiceContract:
    """Build deterministic evidence packs from retrieval candidates."""

    def __init__(self, *, ledger: SQLiteLedger | None = None) -> None:
        self.ledger = ledger

    def build_pack(
        self,
        *,
        request_id: str,
        query: str | None = None,
        topk: list[dict[str, Any]] | None = None,
        rerank_output: dict[str, Any] | None = None,
        intent_id: str | None = None,
        degraded: bool | None = None,
        reason_code: str | None = None,
        ts_ms: int | None = None,
    ) -> EvidencePackResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        merged_query = str(query if query is not None else rerank_output.get("query", "") if isinstance(rerank_output, dict) else "").strip()
        upstream_topk = rerank_output.get("topk") if isinstance(rerank_output, dict) else None
        merged_topk = topk if topk is not None else upstream_topk

        upstream_degraded = bool(rerank_output.get("degraded")) if isinstance(rerank_output, dict) else False
        upstream_reason = str(rerank_output.get("reason_code")) if isinstance(rerank_output, dict) and rerank_output.get("reason_code") else None

        normalized_topk = _normalize_topk(merged_topk)
        final_degraded = bool(degraded) or upstream_degraded
        final_reason = reason_code or upstream_reason

        if not merged_query or not normalized_topk:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                query=merged_query,
                topk=normalized_topk,
                reason_code=RETR_EPACK_INPUT_INVALID,
                detail="evidence pack input invalid: query and topk are required",
                ts_ms=now_value,
            )

        if final_reason:
            final_degraded = True

        evidence_refs = [str(item["evidence_ref"]) for item in normalized_topk]
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "query": merged_query,
            "topk": normalized_topk,
            "evidence_refs": evidence_refs,
            "pack_id": _compute_pack_id(
                request_id=request_id,
                intent_id=intent_id,
                query=merged_query,
                topk=normalized_topk,
            ),
            "serialization_version": SERIALIZATION_VERSION,
            "degraded": final_degraded,
            "stage": S_RET_EVIDENCE_PACK,
            "reason_code": final_reason if final_degraded else None,
            "status": STATUS_DEGRADED if final_degraded else STATUS_OK,
            "ts_ms": now_value,
        }
        detail = "evidence pack built"
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=output["status"],
            reason_code=output["reason_code"],
            detail=detail,
            payload={
                "query_length": len(merged_query),
                "topk_count": len(normalized_topk),
                "evidence_refs_count": len(evidence_refs),
                "pack_id": output["pack_id"],
                "serialization_version": SERIALIZATION_VERSION,
                "output_status": output["status"],
            },
            ts_ms=now_value,
        )
        return EvidencePackResult(
            success=not final_degraded,
            status=output["status"],
            reason_code=output["reason_code"],
            stage=S_RET_EVIDENCE_PACK,
            detail=detail if not final_degraded else "evidence pack built with degraded upstream",
            output=output,
        )

    def serialize_pack(self, pack: dict[str, Any]) -> str:
        return _stable_json(pack)

    def _degrade(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        query: str,
        topk: list[dict[str, Any]],
        reason_code: str,
        detail: str,
        ts_ms: int,
    ) -> EvidencePackResult:
        evidence_refs = [str(item["evidence_ref"]) for item in topk]
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "query": query,
            "topk": topk,
            "evidence_refs": evidence_refs,
            "pack_id": _compute_pack_id(
                request_id=request_id,
                intent_id=intent_id,
                query=query,
                topk=topk,
            ),
            "serialization_version": SERIALIZATION_VERSION,
            "degraded": True,
            "stage": S_RET_EVIDENCE_PACK,
            "reason_code": reason_code,
            "status": STATUS_DEGRADED,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            detail=detail,
            payload={
                "query_length": len(query),
                "topk_count": len(topk),
                "evidence_refs_count": len(evidence_refs),
                "pack_id": output["pack_id"],
                "serialization_version": SERIALIZATION_VERSION,
                "output_status": STATUS_DEGRADED,
            },
            ts_ms=ts_ms,
        )
        return EvidencePackResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=S_RET_EVIDENCE_PACK,
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
                    "op_kind": RET_OP_EVIDENCE_PACK,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_RET_EVIDENCE_PACK,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_RET_EVIDENCE_PACK,
                reason_code=reason_code,
                detail=detail,
            )


def _normalize_topk(raw_topk: list[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(raw_topk, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_topk):
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("doc_id") or f"doc_{index + 1:03d}")
        source_ref = str(item.get("source_ref") or f"source://doc/{index + 1}")
        snippet = str(item.get("snippet") or "")
        score = _coerce_float(item.get("score"), 0.0)
        confidence = _coerce_float(item.get("confidence"), max(0.0, min(1.0, score)))
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
        evidence_ref = str(item.get("evidence_ref") or f"evd://{doc_id}/{index + 1}")
        normalized.append(
            {
                "doc_id": doc_id,
                "source_ref": source_ref,
                "snippet": snippet,
                "score": score,
                "confidence": confidence,
                "metadata": metadata,
                "evidence_ref": evidence_ref,
            }
        )
    return normalized


def _coerce_float(value: Any, default: float) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return round(float(default), 6)


def _compute_pack_id(
    *,
    request_id: str,
    intent_id: str | None,
    query: str,
    topk: list[dict[str, Any]],
) -> str:
    digest_input = {
        "request_id": request_id,
        "intent_id": intent_id,
        "query": query,
        "topk": [
            {
                "doc_id": item["doc_id"],
                "source_ref": item["source_ref"],
                "snippet": item["snippet"],
                "score": item["score"],
                "confidence": item["confidence"],
                "evidence_ref": item["evidence_ref"],
            }
            for item in topk
        ],
        "serialization_version": SERIALIZATION_VERSION,
    }
    digest = hashlib.sha256(_stable_json(digest_input).encode("utf-8")).hexdigest()[:16]
    return f"epk_{digest}"


def _stable_json(value: dict[str, Any] | list[Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
