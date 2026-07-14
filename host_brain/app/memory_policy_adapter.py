"""App-layer adapter from the legacy MemoryGuard to LifeMemory policy port."""

from __future__ import annotations

from host_brain.memory.life_memory.contracts import ChatMemoryCandidate
from host_brain.memory.life_memory.service import ChatMemoryGuardRejected


class ExistingMemoryGuardAdapter:
    def __init__(self, guard) -> None:
        self._guard = guard

    def validate(
        self,
        candidate: ChatMemoryCandidate,
        *,
        tags: tuple[str, ...],
    ) -> None:
        from host_brain.app.memory.memory_guard import MemoryGuardError  # noqa: PLC0415
        from host_brain.app.schemas.memory_types import (  # noqa: PLC0415
            EpisodicRecord,
            EvidenceLevel,
        )

        text = "\n".join(
            part
            for part in (
                f"User: {candidate.user_message}" if candidate.user_message else "",
                f"Assistant: {candidate.assistant_reply}"
                if candidate.assistant_reply
                else "",
            )
            if part
        )
        record = EpisodicRecord(
            session_id=candidate.session_id,
            content=text,
            turn_count=max(2, candidate.turn_count),
            emotion_tags=[candidate.emotion_tag] if candidate.emotion_tag else [],
            fact_tags=list(tags),
            preference_tags=[],
            evidence_level=EvidenceLevel.CANDIDATE,
        )
        try:
            self._guard.check_episodic_write(record)
        except MemoryGuardError as exc:
            raise ChatMemoryGuardRejected(exc.reason, str(exc)) from exc


__all__ = ["ExistingMemoryGuardAdapter"]
