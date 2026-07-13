"""Pure review-resume token contract shared by application and persistence.

The contract deliberately has no dependency on Governor runtime services.  A
review repository may issue and digest its one-time bearer token without
importing the Governor application module, while callers that historically
imported these names from ``governor.tokens`` receive the same objects through
that module's re-export.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import time
from typing import Any, Callable
import uuid


def _now_ms() -> int:
    return int(time.time() * 1000)


TOKEN_TYPE_REVIEW_RESUME = "review_resume"
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_PATTERN = re.compile(
    r"(?:-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----|\bBearer\s+|"
    r"\bsk-(?:live-|test-|proj-)?[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)\s*[=:])",
    re.IGNORECASE,
)


def _exact_int(value: Any, *, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"CANON_APPLY_INTEGER_INVALID:{field_name}")
    return value


def _binding_text(field_name: str, value: str, *, max_length: int = 240) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length or _CONTROL_PATTERN.search(normalized):
        raise ValueError(f"CANON_APPLY_{field_name.upper()}_INVALID")
    if _SECRET_PATTERN.search(normalized):
        raise ValueError(f"CANON_APPLY_{field_name.upper()}_SECRET_REJECTED")
    return normalized


@dataclass(frozen=True)
class ReviewResumeToken:
    token_ref: str
    token_ref_public: str
    token_digest: str
    token_type: str
    review_id: str
    subject_ref: str
    subject_version: int
    resume_phase: str
    checkpoint_id: str
    issued_ts_ms: int
    expires_ts_ms: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "token_ref_public": self.token_ref_public,
            "token_digest": self.token_digest,
            "token_type": self.token_type,
            "review_id": self.review_id,
            "subject_ref": self.subject_ref,
            "subject_version": self.subject_version,
            "resume_phase": self.resume_phase,
            "checkpoint_id": self.checkpoint_id,
            "issued_ts_ms": self.issued_ts_ms,
            "expires_ts_ms": self.expires_ts_ms,
        }


def review_resume_token_digest(token_ref: str) -> str:
    value = str(token_ref or "").strip()
    if not value or not value.startswith("review_resume::") or len(value) > 240:
        raise ValueError("REVIEW_TOKEN_INVALID")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ReviewTokenService:
    """Issue review tokens whose durable one-time state lives in ReviewStore."""

    def __init__(
        self,
        *,
        default_ttl_ms: int = 120_000,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self.default_ttl_ms = _exact_int(default_ttl_ms, field_name="default_ttl_ms")
        if self.default_ttl_ms <= 0:
            raise ValueError("REVIEW_TOKEN_TTL_INVALID")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock

    def issue_resume_token(
        self,
        *,
        review_id: str,
        subject_ref: str,
        subject_version: int,
        resume_phase: str,
        checkpoint_id: str,
        ttl_ms: int | None = None,
    ) -> ReviewResumeToken:
        review_ref = _binding_text("review_id", review_id)
        subject = _binding_text("subject_ref", subject_ref, max_length=512)
        checkpoint = _binding_text("checkpoint_id", checkpoint_id, max_length=512)
        phase = str(resume_phase or "").strip().upper()
        if phase not in {"PRE_EXECUTION", "POST_EXECUTION_ACCEPTANCE"}:
            raise ValueError("REVIEW_TOKEN_RESUME_PHASE_INVALID")
        version = _exact_int(subject_version, field_name="subject_version")
        if version < 0:
            raise ValueError("REVIEW_TOKEN_SUBJECT_VERSION_INVALID")
        issued = _exact_int(self._clock(), field_name="issued_ts_ms")
        ttl = _exact_int(
            self.default_ttl_ms if ttl_ms is None else ttl_ms,
            field_name="ttl_ms",
        )
        if issued < 0 or ttl <= 0:
            raise ValueError("REVIEW_TOKEN_TTL_INVALID")
        token_ref = f"review_resume::{uuid.uuid4().hex}::{uuid.uuid4().hex}"
        digest = review_resume_token_digest(token_ref)
        return ReviewResumeToken(
            token_ref=token_ref,
            token_ref_public=f"review-token://{digest}",
            token_digest=digest,
            token_type=TOKEN_TYPE_REVIEW_RESUME,
            review_id=review_ref,
            subject_ref=subject,
            subject_version=version,
            resume_phase=phase,
            checkpoint_id=checkpoint,
            issued_ts_ms=issued,
            expires_ts_ms=issued + ttl,
        )


__all__ = [
    "ReviewResumeToken",
    "ReviewTokenService",
    "TOKEN_TYPE_REVIEW_RESUME",
    "review_resume_token_digest",
]
