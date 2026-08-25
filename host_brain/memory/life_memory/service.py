"""Governed single-writer service for chat LifeMemory admission."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import time
from typing import Any, Protocol
import uuid

from host_brain.memory.life_memory.constants import (
    DECAY_FAST,
    EVIDENCE_ASSISTANT_REPLY,
    EVIDENCE_ATTACHMENT,
    EVIDENCE_GATEWAY_MESSAGE,
    EVIDENCE_USER_TEXT,
    EVIDENCE_VISION_OBSERVATION,
    EPISODE_CHAT,
    RETENTION_COLD,
    STATUS_ACTIVE,
    TRUTH_CANDIDATE,
    TRUTH_NARRATIVE_ONLY,
    TRUTH_VERIFIED,
)
from host_brain.memory.life_memory.contracts import (
    ChatMemoryCandidate,
    ChatMemoryWritePlan,
    LifeMemoryEvidenceWrite,
    MemoryAdmissionClaim,
    MemoryAdmissionDecision,
    MemoryAdmissionKind,
    MemoryWriteReceipt,
)
from host_brain.memory.life_memory.utils import (
    _classify_life_memory_policy,
    _compute_memory_strength,
    _decay_profile_for_episode,
    _retention_state_for_strength,
    _with_memory_policy_metadata,
    _attachment_looks_visual,
    _chat_salience,
    derive_episode_id,
    derive_evidence_id,
)
from host_brain.persistence.life_memory_repository import (
    LifeMemoryRepository,
    LifeMemoryRepositoryError,
)


MEMORY_WORTH_SKIP = "MEMORY_WORTH_SKIP"
MEMORY_WORTH_LIGHT_ONLY = "MEMORY_WORTH_LIGHT_ONLY"
MEMORY_ADMISSION_ACCEPTED = "MEMORY_ADMISSION_ACCEPTED"
MEMORY_GUARD_LIGHT_ONLY = "MEMORY_GUARD_LIGHT_ONLY"
MEMORY_ADMISSION_BUSY = "MEMORY_ADMISSION_BUSY"


class MemoryWorthEvaluatorPort(Protocol):
    def evaluate(
        self,
        user_message: str,
        ai_response: str,
        *,
        emotion_tag: str = "",
        turn_count: int = 1,
    ) -> Any: ...


class ChatMemoryGuardPort(Protocol):
    def validate(self, candidate: ChatMemoryCandidate, *, tags: Sequence[str]) -> None: ...


class MemoryProjectionDispatcherPort(Protocol):
    def project_outbox(self, outbox_id: str) -> str: ...


class ChatMemoryGuardRejected(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or "MEMORY_GUARD_REJECTED")
        self.detail = str(detail or self.reason)
        super().__init__(self.detail)


class LifeMemoryAdmissionPolicy:
    """Evaluate MemoryWorth and MemoryGuard exactly once on first admission."""

    def __init__(
        self,
        *,
        worth_evaluator: MemoryWorthEvaluatorPort,
        guard: ChatMemoryGuardPort,
    ) -> None:
        self._worth_evaluator = worth_evaluator
        self._guard = guard

    def decide(self, candidate: ChatMemoryCandidate) -> MemoryAdmissionDecision:
        result = self._worth_evaluator.evaluate(
            candidate.user_message,
            candidate.assistant_reply,
            emotion_tag=candidate.emotion_tag,
            turn_count=candidate.turn_count,
        )
        raw_decision = str(getattr(result, "decision", "") or "").strip().lower()
        worth_score = _bounded_score(getattr(result, "worth_score", 0.0))
        tags = tuple(
            dict.fromkeys(
                str(tag or "").strip()
                for tag in list(getattr(result, "tags", ()) or ())
                if str(tag or "").strip()
            )
        )
        if raw_decision == "skip":
            return MemoryAdmissionDecision(
                kind=MemoryAdmissionKind.SKIP,
                reason_code=MEMORY_WORTH_SKIP,
                worth_score=worth_score,
                tags=tags,
            )
        if raw_decision == "write_light":
            return MemoryAdmissionDecision(
                kind=MemoryAdmissionKind.WRITE_LIGHT,
                reason_code=MEMORY_WORTH_LIGHT_ONLY,
                worth_score=worth_score,
                tags=tags,
            )
        if raw_decision != "write":
            raise ValueError(f"MEMORY_WORTH_DECISION_INVALID:{raw_decision or 'empty'}")
        try:
            self._guard.validate(candidate, tags=tags)
        except ChatMemoryGuardRejected as exc:
            # A guard rejection blocks the derived/vector projection, while a
            # short-retention SQLite episode preserves the admitted evidence.
            guard_reason = str(getattr(exc, "reason", None) or type(exc).__name__).strip()
            return MemoryAdmissionDecision(
                kind=MemoryAdmissionKind.WRITE_LIGHT,
                reason_code=f"{MEMORY_GUARD_LIGHT_ONLY}:{guard_reason}"[:240],
                worth_score=worth_score,
                tags=tags,
            )
        return MemoryAdmissionDecision(
            kind=MemoryAdmissionKind.WRITE,
            reason_code=MEMORY_ADMISSION_ACCEPTED,
            worth_score=worth_score,
            tags=tags,
        )


class LifeMemoryService:
    """The only production writer for committed chat LifeMemory."""

    def __init__(
        self,
        *,
        repository: LifeMemoryRepository,
        policy: LifeMemoryAdmissionPolicy,
        projection_dispatcher: MemoryProjectionDispatcherPort | None = None,
        claim_lease_ms: int = 60_000,
        claim_wait_timeout_ms: int = 65_000,
        claim_poll_interval_ms: int = 10,
    ) -> None:
        self.repository = repository
        self.policy = policy
        self.projection_dispatcher = projection_dispatcher
        self.claim_lease_ms = max(1, int(claim_lease_ms))
        self.claim_wait_timeout_ms = max(1, int(claim_wait_timeout_ms))
        self.claim_poll_interval_ms = max(1, int(claim_poll_interval_ms))

    def admit_chat(self, candidate: ChatMemoryCandidate) -> MemoryWriteReceipt:
        if not isinstance(candidate, ChatMemoryCandidate):
            raise TypeError("candidate must be ChatMemoryCandidate")

        owner_token = f"life-memory-admission-{uuid.uuid4().hex}"
        deadline = time.monotonic() + (self.claim_wait_timeout_ms / 1000.0)
        while True:
            outcome = self.repository.try_claim_chat_admission(
                candidate,
                owner_token=owner_token,
                lease_ms=self.claim_lease_ms,
            )
            if isinstance(outcome, MemoryWriteReceipt):
                return self._dispatch_projection(outcome)
            if isinstance(outcome, MemoryAdmissionClaim):
                try:
                    decision = self.policy.decide(candidate)
                    plan = (
                        build_chat_memory_write_plan(candidate, decision)
                        if decision.writes_episode
                        else None
                    )
                    receipt = self.repository.commit_chat_admission(
                        candidate=candidate,
                        claim=outcome,
                        decision=decision,
                        plan=plan,
                    )
                except Exception:
                    # A handled failure should not strand peers until lease
                    # expiry.  Process death still recovers durably by expiry.
                    try:
                        self.repository.release_chat_admission_claim(outcome)
                    except LifeMemoryRepositoryError:
                        pass
                    raise
                return self._dispatch_projection(receipt)
            if time.monotonic() >= deadline:
                raise LifeMemoryRepositoryError(
                    MEMORY_ADMISSION_BUSY,
                    "timed out waiting for durable admission owner",
                )
            time.sleep(self.claim_poll_interval_ms / 1000.0)

    def _dispatch_projection(self, receipt: MemoryWriteReceipt) -> MemoryWriteReceipt:
        if not receipt.outbox_id or self.projection_dispatcher is None:
            return receipt
        try:
            status = self.projection_dispatcher.project_outbox(receipt.outbox_id)
        except Exception:
            status = self.repository.projection_status(receipt.outbox_id) or "PENDING"
        return receipt.with_projection_status(status)


def build_chat_memory_write_plan(
    candidate: ChatMemoryCandidate,
    decision: MemoryAdmissionDecision,
) -> ChatMemoryWritePlan:
    if decision.kind is MemoryAdmissionKind.SKIP:
        raise ValueError("skip admission cannot build a write plan")
    created = candidate.ts_ms
    refs = _chat_evidence_refs(candidate)
    episode_id = derive_episode_id(
        refs,
        fallback_seed=(
            f"{EPISODE_CHAT}:{candidate.session_id}:{candidate.person_id}:"
            f"chat://request/{candidate.request_id}"
        ),
    )
    primary_ref = f"chat://request/{candidate.request_id}"
    perception_summary = _compact_text(candidate.attachment_summary, limit=500)
    summary_parts = [
        f"User: {_compact_text(candidate.user_message, limit=500)}",
        f"Assistant: {_compact_text(candidate.assistant_reply, limit=500)}",
    ]
    if perception_summary:
        summary_parts.append(f"Perception: {perception_summary}")
    summary = "\n".join(part for part in summary_parts if part).strip()
    raw_salience = _chat_salience(
        user_message=candidate.user_message,
        assistant_reply=candidate.assistant_reply,
        has_perception=bool(
            candidate.attachments
            or candidate.multimodal_observations
            or perception_summary
        ),
    )
    salience = min(raw_salience, 0.25) if decision.kind is MemoryAdmissionKind.WRITE_LIGHT else raw_salience
    metadata: dict[str, Any] = {
        **dict(candidate.metadata),
        "intent_id": candidate.intent_id,
        "emotion_tag": candidate.emotion_tag,
        "emotion_valence": candidate.emotion_valence,
        "emotion_arousal": candidate.emotion_arousal,
        "memory_layer": "life_episode",
        "memory_admission": decision.as_dict(),
        "evidence_refs": list(refs),
        "projection_authority": "life_memory_projection_outbox",
    }
    policy = _classify_life_memory_policy(
        episode_kind=EPISODE_CHAT,
        action="chat_turn",
        result="assistant_reply",
        summary=summary,
        metadata=metadata,
        evidence_refs=refs,
        truth_state=TRUTH_CANDIDATE,
    )
    explicit_decay = DECAY_FAST if decision.kind is MemoryAdmissionKind.WRITE_LIGHT else None
    metadata = _with_memory_policy_metadata(
        metadata,
        policy=policy,
        explicit_decay_profile_id=explicit_decay,
    )
    decay_metadata = dict(metadata)
    if explicit_decay:
        decay_metadata["decay_profile_id"] = explicit_decay
    decay_profile = _decay_profile_for_episode(
        episode_kind=EPISODE_CHAT,
        salience=salience,
        metadata=decay_metadata,
    )
    memory_strength = _compute_memory_strength(
        salience=salience,
        episode_kind=EPISODE_CHAT,
        summary=summary,
        metadata=metadata,
    )
    retention_state = (
        RETENTION_COLD
        if decision.kind is MemoryAdmissionKind.WRITE_LIGHT
        else _retention_state_for_strength(memory_strength, truth_state=TRUTH_CANDIDATE)
    )
    evidence = _build_evidence(candidate, episode_id=episode_id, perception_summary=perception_summary)
    return ChatMemoryWritePlan(
        episode_id=episode_id,
        episode_kind=EPISODE_CHAT,
        thread_id=f"chat://session/{candidate.session_id}",
        person_id=candidate.person_id,
        session_id=candidate.session_id,
        actor_id="natsume",
        intent=candidate.intent_id,
        action="chat_turn",
        result="assistant_reply",
        primary_ref=primary_ref,
        summary=summary,
        salience=salience,
        memory_strength=memory_strength,
        decay_profile_id=decay_profile,
        retention_state=retention_state,
        truth_state=TRUTH_CANDIDATE,
        status=STATUS_ACTIVE,
        created_ts_ms=created,
        metadata=metadata,
        evidence_refs=refs,
        evidence=evidence,
    )


def _chat_evidence_refs(candidate: ChatMemoryCandidate) -> tuple[str, ...]:
    refs = [
        f"chat://request/{candidate.request_id}",
        f"chat://session/{candidate.session_id}",
        f"chat://person/{candidate.person_id}",
    ]
    if candidate.intent_id:
        refs.append(f"chat://intent/{candidate.intent_id}")
    gateway_message_id = _gateway_message_id(candidate)
    if gateway_message_id:
        refs.append(f"gateway://message/{gateway_message_id}")
    return tuple(dict.fromkeys(refs))


def _build_evidence(
    candidate: ChatMemoryCandidate,
    *,
    episode_id: str,
    perception_summary: str,
) -> tuple[LifeMemoryEvidenceWrite, ...]:
    rows: list[LifeMemoryEvidenceWrite] = []

    def append(
        *,
        kind: str,
        source_ref: str,
        content: str,
        payload: Mapping[str, Any],
        trust_level: str,
        ts_ms: int,
    ) -> None:
        clean_content = _compact_text(content, limit=4000)
        evidence_id = derive_evidence_id(
            episode_id=episode_id,
            evidence_kind=kind,
            source_ref=source_ref,
            content=clean_content,
            payload=payload,
        )
        rows.append(
            LifeMemoryEvidenceWrite(
                evidence_id=evidence_id,
                evidence_kind=kind,
                source_ref=source_ref,
                request_id=candidate.request_id,
                content=clean_content,
                payload=dict(payload),
                trust_level=trust_level,
                created_ts_ms=ts_ms,
            )
        )

    if candidate.user_message:
        append(
            kind=EVIDENCE_USER_TEXT,
            source_ref=f"chat://request/{candidate.request_id}#user",
            content=candidate.user_message,
            payload={"person_id": candidate.person_id, "session_id": candidate.session_id},
            trust_level=TRUTH_CANDIDATE,
            ts_ms=candidate.ts_ms,
        )
    if candidate.assistant_reply:
        append(
            kind=EVIDENCE_ASSISTANT_REPLY,
            source_ref=f"chat://request/{candidate.request_id}#assistant",
            content=candidate.assistant_reply,
            payload={"person_id": candidate.person_id, "session_id": candidate.session_id},
            trust_level=TRUTH_NARRATIVE_ONLY,
            ts_ms=candidate.ts_ms + 1,
        )

    attachments = [dict(item) for item in candidate.attachments]
    observations = dict(candidate.multimodal_observations)
    gateway_message_id = _gateway_message_id(candidate)
    if gateway_message_id:
        append(
            kind=EVIDENCE_GATEWAY_MESSAGE,
            source_ref=f"gateway://message/{gateway_message_id}",
            content=candidate.user_message,
            payload={"attachments": attachments, "observations": observations},
            trust_level=TRUTH_VERIFIED,
            ts_ms=candidate.ts_ms,
        )

    if attachments:
        for index, attachment in enumerate(attachments):
            source_ref = str(attachment.get("source_ref") or attachment.get("url") or "").strip()
            if not source_ref:
                source_ref = f"chat://request/{candidate.request_id}#attachment/{index}"
            append(
                kind=(
                    EVIDENCE_VISION_OBSERVATION
                    if _attachment_looks_visual(attachment)
                    else EVIDENCE_ATTACHMENT
                ),
                source_ref=source_ref,
                content=perception_summary,
                payload={"attachment": attachment, "observations": observations},
                trust_level=(
                    TRUTH_VERIFIED if observations or perception_summary else TRUTH_CANDIDATE
                ),
                ts_ms=candidate.ts_ms + 2 + index,
            )
    elif observations or perception_summary:
        append(
            kind=EVIDENCE_VISION_OBSERVATION,
            source_ref=f"vision://observation/{candidate.request_id}",
            content=perception_summary,
            payload={"observations": observations},
            trust_level=TRUTH_VERIFIED if observations else TRUTH_CANDIDATE,
            ts_ms=candidate.ts_ms + 2,
        )
    return tuple(rows)


def _gateway_message_id(candidate: ChatMemoryCandidate) -> str | None:
    metadata = dict(candidate.metadata)
    value = str(metadata.get("gateway_message_id") or "").strip()
    reply_channel = str(metadata.get("reply_channel") or "").strip().lower()
    if not value and (
        reply_channel in {"gateway", "qq", "wechat"}
        or candidate.session_id.lower().startswith(("qq_", "qq:", "wechat_", "wechat:"))
    ):
        value = candidate.request_id
    return value or None


def _compact_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _bounded_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("MEMORY_WORTH_SCORE_INVALID") from exc
    if score < 0.0 or score > 1.0:
        raise ValueError("MEMORY_WORTH_SCORE_INVALID")
    return score


__all__ = [
    "ChatMemoryGuardPort",
    "ChatMemoryGuardRejected",
    "LifeMemoryAdmissionPolicy",
    "LifeMemoryService",
    "MemoryProjectionDispatcherPort",
    "MemoryWorthEvaluatorPort",
    "build_chat_memory_write_plan",
]
