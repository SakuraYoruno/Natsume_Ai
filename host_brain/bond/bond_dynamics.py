"""Single-writer entry for Bond SSOT updates."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import uuid
from typing import Any, TYPE_CHECKING

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.neo4j_client import BOND_WRITER_ID, InMemoryNeo4jBondClient
from host_brain.core.errors import BOND_EVIDENCE_REQUIRED, BOND_SINGLE_WRITER_VIOLATION
from host_brain.core.ids import now_ms

if TYPE_CHECKING:
    from host_brain.bond.bond_stage_engine import BondStageEngine, StageTransitionResult

_logger = logging.getLogger("Natsume.bond_dynamics")


S_BOND_UPDATE = "S_BOND_UPDATE"

STATUS_OK = "OK"
STATUS_REJECTED = "REJECTED"


@dataclass(frozen=True)
class BondUpdateResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


class BondDynamicsWriter:
    """Only legal write path into Bond SSOT graph.

    M13-C：支持可选注入 BondStageEngine，apply_update 成功后自动触发阶段积分计算。
    """

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        neo4j_client: InMemoryNeo4jBondClient | None = None,
        stage_engine: "BondStageEngine | None" = None,
    ) -> None:
        self.ledger = ledger
        self.neo4j_client = neo4j_client or InMemoryNeo4jBondClient()
        self._stage_engine = stage_engine

    @staticmethod
    def classify_interaction(
        *,
        text: str = "",
        emotion_intensity: float = 0.5,
    ) -> str:
        """根据文本长度、情感强度、问号数量自动推断互动质量（M28-C OPT-7）。

        Returns:
            "INTERACTION_MINIMAL" | "INTERACTION_ENGAGED" | "INTERACTION_DEEP"
        """
        text_len = len(str(text).strip())
        question_count = str(text).count("?") + str(text).count("？")
        intensity = max(0.0, min(1.0, float(emotion_intensity)))

        # 深度互动：高情感强度 + 长文本
        if intensity > 0.8 and text_len > 100:
            return "INTERACTION_DEEP"
        # 普通互动：长文本或含问号
        if text_len > 50 or question_count > 0:
            return "INTERACTION_ENGAGED"
        # 简短互动：短文本且无问号
        return "INTERACTION_MINIMAL"

    def apply_update(
        self,
        *,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        user_id: str,
        topic_id: str,
        capability_hint: str,
        evidence_refs: list[str],
        payload: dict[str, Any] | None = None,
        update_kind: str = "INTERACTION",
        event_id: str | None = None,
        ts_ms: int | None = None,
        emotion_tag: str = "NEUTRAL",
        emotion_intensity: float = 0.5,
    ) -> BondUpdateResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            return self._reject(
                request_id=request_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                event_id=event_id or _new_event_id(),
                user_id=user_id,
                topic_id=topic_id,
                capability_hint=capability_hint,
                update_kind=update_kind,
                reason_code=BOND_EVIDENCE_REQUIRED,
                detail="bond update requires non-empty evidence_refs",
                evidence_refs=[],
                payload=dict(payload or {}),
                ts_ms=now_value,
            )

        event_key = str(event_id or _new_event_id()).strip()
        try:
            written = self.neo4j_client.write_bond_event(
                writer_id=BOND_WRITER_ID,
                event_id=event_key,
                user_id=str(user_id),
                session_id=str(session_id),
                topic_id=str(topic_id),
                capability_hint=str(capability_hint),
                evidence_refs=refs,
                payload=dict(payload or {}),
                ts_ms=now_value,
            )
        except PermissionError:
            return self._reject(
                request_id=request_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                event_id=event_key,
                user_id=user_id,
                topic_id=topic_id,
                capability_hint=capability_hint,
                update_kind=update_kind,
                reason_code=BOND_SINGLE_WRITER_VIOLATION,
                detail="bond update blocked by single-writer policy",
                evidence_refs=refs,
                payload=dict(payload or {}),
                ts_ms=now_value,
            )

        # M13-C：触发 BondStageEngine 积分计算（可选）
        stage_transition: "StageTransitionResult | None" = None
        if self._stage_engine is not None:
            try:
                # M27-E：情感强度加权（OPT-26）：emotion_intensity > 0.7 的互动额外加权 1.5x
                effective_update_kind = str(update_kind)
                stage_transition = self._stage_engine.apply_event(
                    user_id=str(user_id),
                    update_kind=effective_update_kind,
                    evidence_refs=refs,
                    request_id=request_id,
                    ts_ms=now_value,
                    emotion_intensity=float(emotion_intensity),
                )
                _logger.debug(
                    "BondStageEngine applied: user=%s %s (delta=%+d, total=%d)",
                    user_id,
                    stage_transition.transition_status,
                    stage_transition.score_delta,
                    stage_transition.total_score,
                    extra={"request_id": request_id},
                )
            except Exception as exc:
                _logger.warning(
                    "BondStageEngine.apply_event failed (non-fatal): %s",
                    exc,
                    extra={"request_id": request_id},
                )

        event = {
            "request_id": request_id,
            "intent_id": intent_id,
            "session_id": session_id,
            "seq": int(seq),
            "event_id": event_key,
            "user_id": str(user_id),
            "topic_id": str(topic_id),
            "capability_hint": str(capability_hint),
            "update_kind": str(update_kind),
            "evidence_refs": refs,
            "payload": dict(payload or {}),
            "neo4j_event": written,
            "stage": S_BOND_UPDATE,
            "status": STATUS_OK,
            "reason_code": None,
            "ts_ms": now_value,
            "stage_transition": (
                {
                    "from_stage": stage_transition.from_stage,
                    "to_stage": stage_transition.to_stage,
                    "transition_status": stage_transition.transition_status,
                    "score_delta": stage_transition.score_delta,
                    "total_score": stage_transition.total_score,
                }
                if stage_transition is not None
                else None
            ),
        }
        self._log_update(
            request_id=request_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            event_id=event_key,
            user_id=user_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            update_kind=update_kind,
            status=STATUS_OK,
            reason_code=None,
            evidence_refs=refs,
            payload=event["payload"],
            detail="bond update applied",
            ts_ms=now_value,
        )
        return BondUpdateResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_BOND_UPDATE,
            detail="bond update applied",
            event=event,
        )

    def _reject(
        self,
        *,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        event_id: str,
        user_id: str,
        topic_id: str,
        capability_hint: str,
        update_kind: str,
        reason_code: str,
        detail: str,
        evidence_refs: list[str],
        payload: dict[str, Any],
        ts_ms: int,
    ) -> BondUpdateResult:
        event = {
            "request_id": request_id,
            "intent_id": intent_id,
            "session_id": session_id,
            "seq": int(seq),
            "event_id": str(event_id),
            "user_id": str(user_id),
            "topic_id": str(topic_id),
            "capability_hint": str(capability_hint),
            "update_kind": str(update_kind),
            "evidence_refs": list(evidence_refs),
            "payload": dict(payload),
            "stage": S_BOND_UPDATE,
            "status": STATUS_REJECTED,
            "reason_code": reason_code,
            "ts_ms": ts_ms,
        }
        self._log_update(
            request_id=request_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            event_id=str(event_id),
            user_id=user_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            update_kind=update_kind,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            evidence_refs=list(evidence_refs),
            payload=dict(payload),
            detail=detail,
            ts_ms=ts_ms,
        )
        return BondUpdateResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=S_BOND_UPDATE,
            detail=detail,
            event=event,
        )

    def _log_update(
        self,
        *,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        event_id: str,
        user_id: str,
        topic_id: str,
        capability_hint: str,
        update_kind: str,
        status: str,
        reason_code: str | None,
        evidence_refs: list[str],
        payload: dict[str, Any],
        detail: str,
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return
        if hasattr(self.ledger, "log_bond_update"):
            self.ledger.log_bond_update(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "session_id": session_id,
                    "seq": int(seq),
                    "event_id": event_id,
                    "user_id": str(user_id),
                    "topic_id": str(topic_id),
                    "capability_hint": str(capability_hint),
                    "update_kind": str(update_kind),
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_BOND_UPDATE,
                    "evidence_refs": list(evidence_refs),
                    "payload": dict(payload),
                    "detail": detail,
                    "ts_ms": int(ts_ms),
                }
            )
            return
        if reason_code is not None and hasattr(self.ledger, "log_error"):
            self.ledger.log_error(
                request_id=request_id,
                stage=S_BOND_UPDATE,
                reason_code=reason_code,
                detail=detail,
            )


def _new_event_id() -> str:
    return f"bond_evt_{uuid.uuid4().hex}"


def _normalize_evidence_refs(value: list[str] | Any) -> list[str]:
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        ref = str(item or "").strip()
        if ref:
            refs.append(ref)
    return refs
