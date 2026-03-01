"""Token issuance and validation for approval/confirm/undo flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from host_brain.core.errors import CONFIRM_REQUIRED, TOKEN_INVALID
from host_brain.core.ids import now_ms


TOKEN_TYPE_APPROVAL = "approval"
TOKEN_TYPE_CONFIRM = "confirm"
TOKEN_TYPE_UNDO = "undo"


@dataclass(frozen=True)
class TokenRecord:
    token_ref: str
    token_type: str
    intent_id: str
    skill_name: str
    session_id: str
    seq: int
    issued_ts_ms: int
    expiry_ts_ms: int
    nonce: str
    used: bool = False


@dataclass(frozen=True)
class TokenValidationResult:
    valid: bool
    reason_code: str | None = None
    token: TokenRecord | None = None
    detail: str | None = None


class TokenService:
    """In-memory token registry used by governor/skills contracts."""

    def __init__(
        self,
        *,
        approval_ttl_ms: int = 120_000,
        confirm_ttl_ms: int = 120_000,
        undo_ttl_ms: int = 600_000,
    ) -> None:
        self.approval_ttl_ms = approval_ttl_ms
        self.confirm_ttl_ms = confirm_ttl_ms
        self.undo_ttl_ms = undo_ttl_ms
        self._tokens: dict[str, TokenRecord] = {}

    def issue_approval_token(
        self,
        *,
        intent_id: str,
        skill_name: str,
        session_id: str,
        seq: int,
        now_ts_ms: int | None = None,
    ) -> str:
        return self._issue(
            token_type=TOKEN_TYPE_APPROVAL,
            intent_id=intent_id,
            skill_name=skill_name,
            session_id=session_id,
            seq=seq,
            ttl_ms=self.approval_ttl_ms,
            now_ts_ms=now_ts_ms,
        )

    def issue_confirm_token(
        self,
        *,
        intent_id: str,
        skill_name: str,
        session_id: str,
        seq: int,
        now_ts_ms: int | None = None,
    ) -> str:
        return self._issue(
            token_type=TOKEN_TYPE_CONFIRM,
            intent_id=intent_id,
            skill_name=skill_name,
            session_id=session_id,
            seq=seq,
            ttl_ms=self.confirm_ttl_ms,
            now_ts_ms=now_ts_ms,
        )

    def issue_undo_token(
        self,
        *,
        intent_id: str,
        skill_name: str,
        session_id: str,
        seq: int,
        now_ts_ms: int | None = None,
    ) -> str:
        return self._issue(
            token_type=TOKEN_TYPE_UNDO,
            intent_id=intent_id,
            skill_name=skill_name,
            session_id=session_id,
            seq=seq,
            ttl_ms=self.undo_ttl_ms,
            now_ts_ms=now_ts_ms,
        )

    def validate_bound_token(
        self,
        token_ref: str | None,
        *,
        expected_type: str,
        intent_id: str,
        skill_name: str,
        session_id: str,
        seq: int,
        now_ts_ms: int | None = None,
        consume: bool = False,
    ) -> TokenValidationResult:
        if not token_ref:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="token missing")

        token = self._tokens.get(token_ref)
        if token is None:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="token not found")

        if token.token_type != expected_type:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="token type mismatch")

        if token.used:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="token already used")

        now_value = now_ts_ms if now_ts_ms is not None else now_ms()
        if token.expiry_ts_ms < now_value:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="token expired")

        if token.intent_id != intent_id:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="intent mismatch")
        if token.skill_name != skill_name:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="skill mismatch")
        if token.session_id != session_id:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="session mismatch")
        if token.seq != seq:
            return TokenValidationResult(valid=False, reason_code=TOKEN_INVALID, detail="seq mismatch")

        if consume:
            self._tokens[token_ref] = TokenRecord(
                token_ref=token.token_ref,
                token_type=token.token_type,
                intent_id=token.intent_id,
                skill_name=token.skill_name,
                session_id=token.session_id,
                seq=token.seq,
                issued_ts_ms=token.issued_ts_ms,
                expiry_ts_ms=token.expiry_ts_ms,
                nonce=token.nonce,
                used=True,
            )
        return TokenValidationResult(valid=True, token=token)

    def validate_execution_tokens(
        self,
        *,
        intent_id: str,
        skill_name: str,
        session_id: str,
        seq: int,
        approval_token_ref: str | None,
        confirm_token_ref: str | None,
        require_confirm: bool,
        now_ts_ms: int | None = None,
    ) -> TokenValidationResult:
        approval_result = self.validate_bound_token(
            approval_token_ref,
            expected_type=TOKEN_TYPE_APPROVAL,
            intent_id=intent_id,
            skill_name=skill_name,
            session_id=session_id,
            seq=seq,
            now_ts_ms=now_ts_ms,
            consume=True,
        )
        if not approval_result.valid:
            return approval_result

        if require_confirm:
            if not confirm_token_ref:
                return TokenValidationResult(
                    valid=False,
                    reason_code=CONFIRM_REQUIRED,
                    detail="confirm token required",
                )
            confirm_result = self.validate_bound_token(
                confirm_token_ref,
                expected_type=TOKEN_TYPE_CONFIRM,
                intent_id=intent_id,
                skill_name=skill_name,
                session_id=session_id,
                seq=seq,
                now_ts_ms=now_ts_ms,
                consume=True,
            )
            if not confirm_result.valid:
                return confirm_result

        return TokenValidationResult(valid=True, token=approval_result.token)

    def _issue(
        self,
        *,
        token_type: str,
        intent_id: str,
        skill_name: str,
        session_id: str,
        seq: int,
        ttl_ms: int,
        now_ts_ms: int | None,
    ) -> str:
        issued = now_ts_ms if now_ts_ms is not None else now_ms()
        token_ref = f"{token_type}::{uuid.uuid4().hex}"
        token = TokenRecord(
            token_ref=token_ref,
            token_type=token_type,
            intent_id=intent_id,
            skill_name=skill_name,
            session_id=session_id,
            seq=seq,
            issued_ts_ms=issued,
            expiry_ts_ms=issued + ttl_ms,
            nonce=f"nonce::{uuid.uuid4().hex}",
        )
        self._tokens[token_ref] = token
        return token_ref

    def export_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            token_ref: {
                "token_type": t.token_type,
                "intent_id": t.intent_id,
                "skill_name": t.skill_name,
                "session_id": t.session_id,
                "seq": t.seq,
                "issued_ts_ms": t.issued_ts_ms,
                "expiry_ts_ms": t.expiry_ts_ms,
                "used": t.used,
            }
            for token_ref, t in self._tokens.items()
        }
