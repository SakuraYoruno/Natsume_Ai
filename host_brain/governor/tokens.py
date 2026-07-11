"""Token issuance and validation for approval/confirm/undo flows."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
import threading
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.core.errors import CONFIRM_REQUIRED, TOKEN_INVALID
from host_brain.core.ids import now_ms


TOKEN_TYPE_APPROVAL = "approval"
TOKEN_TYPE_CONFIRM = "confirm"
TOKEN_TYPE_UNDO = "undo"
TOKEN_TYPE_CANON_APPLY = "canon_apply"
TOKEN_TYPE_CANON_ROLLBACK = "canon_rollback"
TOKEN_TYPE_REVIEW_RESUME = "review_resume"

CANON_OPERATION_APPLY = "APPLY"
CANON_OPERATION_ROLLBACK = "ROLLBACK"
CANON_SUBJECT_PROPOSAL = "CANON_PROPOSAL"
CANON_SUBJECT_PENTEST_PROMOTION = "PENTEST_PROMOTION"
CANON_APPLY_MIGRATION_ASSET = "0053_canon_apply.sql"
CANON_APPLY_MIGRATION_SHA256 = (
    "5c712b40f7184f5f47bcc50be3e3516f59d4a5ec6342e9a4f07c64f13f54e54b"
)
# Frozen semantic manifest of the two tables, every index (including SQLite
# autoindexes), columns/defaults/PK flags and foreign keys created by 0053.
# Runtime readiness is pure read-only verification; it never executes DDL.
CANON_APPLY_SCHEMA_MANIFEST_SHA256 = (
    "dfa8f618f721ec87edc7af5097702279577d30b0b06eb1ee8b6879b92b160c09"
)
CANON_EVIDENCE_MAX_COUNT = 32
CANON_EVIDENCE_MAX_ITEM_LENGTH = 512
CANON_EVIDENCE_MAX_TOTAL_LENGTH = 4096
CANON_BINDING_MAX_LENGTH = 240
_CANON_EVIDENCE_REF_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,479}$"
)
_CANON_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_CANON_SECRET_PATTERN = re.compile(
    r"(?:-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----|\bBearer\s+|"
    r"\bsk-(?:live-|test-|proj-)?[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)\s*[=:])",
    re.IGNORECASE,
)


def _exact_sqlite_int(
    value: Any,
    *,
    field_name: str,
    optional: bool = False,
) -> int | None:
    """Return only Python ints, matching sqlite3's exact INTEGER storage class."""

    if value is None and optional:
        return None
    if type(value) is not int:
        raise ValueError(f"CANON_APPLY_INTEGER_INVALID:{field_name}")
    return value


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

    # 每隔多少次 issue 操作触发一次过期清理（避免每次都扫描）
    _CLEANUP_INTERVAL = 50

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
        self._issue_count: int = 0
        self._lock = threading.RLock()

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
        with self._lock:
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
            # The expiry instant itself is outside the validity interval.
            if token.expiry_ts_ms <= now_value:
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
        # 修复（M28-L）：先验证 approval_token（不消费），再验证 confirm_token（不消费），
        # 两者均通过后才依次消费，防止 approval_token 被消费后 confirm 验证失败导致 intent 无法重试。
        # RLock makes validation + one-time consumption one atomic registry action.
        with self._lock:
            approval_result = self.validate_bound_token(
                approval_token_ref,
                expected_type=TOKEN_TYPE_APPROVAL,
                intent_id=intent_id,
                skill_name=skill_name,
                session_id=session_id,
                seq=seq,
                now_ts_ms=now_ts_ms,
                consume=False,
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
                    consume=False,
                )
                if not confirm_result.valid:
                    return confirm_result
                self.validate_bound_token(
                    approval_token_ref,
                    expected_type=TOKEN_TYPE_APPROVAL,
                    intent_id=intent_id,
                    skill_name=skill_name,
                    session_id=session_id,
                    seq=seq,
                    now_ts_ms=now_ts_ms,
                    consume=True,
                )
                self.validate_bound_token(
                    confirm_token_ref,
                    expected_type=TOKEN_TYPE_CONFIRM,
                    intent_id=intent_id,
                    skill_name=skill_name,
                    session_id=session_id,
                    seq=seq,
                    now_ts_ms=now_ts_ms,
                    consume=True,
                )
            else:
                self.validate_bound_token(
                    approval_token_ref,
                    expected_type=TOKEN_TYPE_APPROVAL,
                    intent_id=intent_id,
                    skill_name=skill_name,
                    session_id=session_id,
                    seq=seq,
                    now_ts_ms=now_ts_ms,
                    consume=True,
                )

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
        with self._lock:
            self._tokens[token_ref] = token
            # 定期清理过期/已使用的 token，防止内存无限增长
            self._issue_count += 1
            if self._issue_count % self._CLEANUP_INTERVAL == 0:
                self._purge_expired_tokens_locked(issued)
        return token_ref

    def _purge_expired_tokens(self, now_ts_ms: int | None = None) -> int:
        """清理所有已过期或已使用的 token，返回清理数量。"""
        with self._lock:
            return self._purge_expired_tokens_locked(now_ts_ms)

    def _purge_expired_tokens_locked(self, now_ts_ms: int | None = None) -> int:
        """清理所有已过期或已使用的 token（调用方必须持有 self._lock）。"""
        now_value = now_ts_ms if now_ts_ms is not None else now_ms()
        expired_refs = [
            ref for ref, t in self._tokens.items()
            if t.used or t.expiry_ts_ms <= now_value
        ]
        for ref in expired_refs:
            del self._tokens[ref]
        return len(expired_refs)

    def export_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canon_evidence_digest(evidence_refs: Iterable[str]) -> str:
    refs = list(normalize_canon_evidence_refs(evidence_refs))
    return hashlib.sha256(_canonical_json(refs).encode("utf-8")).hexdigest()


def normalize_canon_evidence_refs(evidence_refs: Iterable[str]) -> tuple[str, ...]:
    refs = tuple(str(ref).strip() for ref in evidence_refs)
    if not refs:
        raise ValueError("CANON_APPLY_EVIDENCE_REQUIRED")
    if len(refs) > CANON_EVIDENCE_MAX_COUNT:
        raise ValueError("CANON_APPLY_EVIDENCE_COUNT_EXCEEDED")
    if sum(len(ref) for ref in refs) > CANON_EVIDENCE_MAX_TOTAL_LENGTH:
        raise ValueError("CANON_APPLY_EVIDENCE_TOTAL_SIZE_EXCEEDED")
    for ref in refs:
        if (
            not ref
            or len(ref) > CANON_EVIDENCE_MAX_ITEM_LENGTH
            or _CANON_CONTROL_PATTERN.search(ref)
            or not _CANON_EVIDENCE_REF_PATTERN.fullmatch(ref)
        ):
            raise ValueError("CANON_APPLY_EVIDENCE_REF_INVALID")
        if _CANON_SECRET_PATTERN.search(ref):
            raise ValueError("CANON_APPLY_EVIDENCE_SECRET_REJECTED")
    return refs


def validate_canon_binding_text(
    field_name: str,
    value: str,
    *,
    max_length: int = CANON_BINDING_MAX_LENGTH,
) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > int(max_length)
        or _CANON_CONTROL_PATTERN.search(normalized)
    ):
        raise ValueError(f"CANON_APPLY_{str(field_name).upper()}_INVALID")
    if _CANON_SECRET_PATTERN.search(normalized):
        raise ValueError(f"CANON_APPLY_{str(field_name).upper()}_SECRET_REJECTED")
    return normalized


def canon_token_digest(token_ref: str) -> str:
    value = str(token_ref or "")
    if not value:
        raise ValueError("CANON_APPLY_TOKEN_MISSING")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    """Issue bearer resume tokens whose durable binding is stored by ReviewStore."""

    def __init__(self, *, default_ttl_ms: int = 120_000, clock: Any = now_ms) -> None:
        self.default_ttl_ms = _exact_sqlite_int(default_ttl_ms, field_name="default_ttl_ms")
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
        review_ref = validate_canon_binding_text("review_id", review_id)
        subject = validate_canon_binding_text("subject_ref", subject_ref, max_length=512)
        checkpoint = validate_canon_binding_text("checkpoint_id", checkpoint_id, max_length=512)
        phase = str(resume_phase or "").strip().upper()
        if phase not in {"PRE_EXECUTION", "POST_EXECUTION_ACCEPTANCE"}:
            raise ValueError("REVIEW_TOKEN_RESUME_PHASE_INVALID")
        version = _exact_sqlite_int(subject_version, field_name="subject_version")
        if version < 0:
            raise ValueError("REVIEW_TOKEN_SUBJECT_VERSION_INVALID")
        issued = _exact_sqlite_int(self._clock(), field_name="issued_ts_ms")
        ttl = _exact_sqlite_int(self.default_ttl_ms if ttl_ms is None else ttl_ms, field_name="ttl_ms")
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


def _canon_apply_migration_path() -> Path:
    return Path(__file__).resolve().parents[1] / "audit" / "migrations" / CANON_APPLY_MIGRATION_ASSET


def _ensure_canon_apply_schema(conn: sqlite3.Connection) -> None:
    asset = _canon_apply_migration_path()
    if not asset.is_file():
        raise RuntimeError(f"CANON_APPLY_SCHEMA_MISSING:{asset}")
    sql = asset.read_text(encoding="utf-8-sig")
    if "canon_apply_token" not in sql or "canon_apply_journal" not in sql:
        raise RuntimeError("CANON_APPLY_SCHEMA_INVALID")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if checksum != CANON_APPLY_MIGRATION_SHA256:
        raise RuntimeError("CANON_APPLY_SCHEMA_INVALID:asset_checksum")
    try:
        migration_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if migration_table is None:
            raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:central_migration_missing")
        migrations = conn.execute(
            "SELECT name, checksum FROM schema_migrations WHERE migration_id = 53"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:central_migration_invalid") from exc
    if (
        len(migrations) != 1
        or str(migrations[0][0]) != "canon_apply"
        or str(migrations[0][1]) != CANON_APPLY_MIGRATION_SHA256
    ):
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:central_migration_mismatch")
    if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:foreign_keys_disabled")

    try:
        actual_manifest = _canon_apply_schema_manifest(conn)
        referenced_objects = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view')"
        ).fetchall()
        referenced_objects.extend(
            conn.execute(
                "SELECT type, name, sql FROM sqlite_temp_master WHERE type IN ('trigger', 'view')"
            ).fetchall()
        )
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:schema_introspection") from exc
    for object_type, name, object_sql in referenced_objects:
        normalized = str(object_sql or "").lower()
        if "canon_apply_token" in normalized or "canon_apply_journal" in normalized:
            raise RuntimeError(
                "CANON_APPLY_SCHEMA_NOT_READY:unexpected_schema_object:"
                f"{object_type}:{name}"
            )
    manifest_digest = hashlib.sha256(
        _canonical_json(actual_manifest).encode("utf-8")
    ).hexdigest()
    if manifest_digest != CANON_APPLY_SCHEMA_MANIFEST_SHA256:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:schema_fingerprint")


def _canon_apply_schema_manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = ("canon_apply_token", "canon_apply_journal")
    master_rows = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE tbl_name IN ('canon_apply_token', 'canon_apply_journal')
          AND type IN ('table', 'index', 'trigger')
        ORDER BY type, name
        """
    ).fetchall()
    master = [
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            " ".join(str(row[3] or "").split()),
        )
        for row in master_rows
    ]
    table_info: dict[str, list[tuple[Any, ...]]] = {}
    foreign_keys: dict[str, list[tuple[Any, ...]]] = {}
    indexes: dict[str, list[tuple[Any, ...]]] = {}
    index_columns: dict[str, list[tuple[Any, ...]]] = {}
    for table in tables:
        table_info[table] = [
            tuple(row) for row in conn.execute(f"PRAGMA table_xinfo({table})").fetchall()
        ]
        foreign_keys[table] = sorted(
            (tuple(row) for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()),
            key=repr,
        )
        index_rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
        indexes[table] = sorted(
            (
                (str(row[1]), int(row[2]), str(row[3]), int(row[4]))
                for row in index_rows
            ),
            key=repr,
        )
        for row in index_rows:
            name = str(row[1])
            index_columns[name] = [
                tuple(value)
                for value in conn.execute(f"PRAGMA index_xinfo({name})").fetchall()
            ]
    return {
        "master": master,
        "table_info": table_info,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
        "index_columns": index_columns,
    }


def _durable_db_path(db_path: str | Path) -> str:
    raw = str(db_path or "").strip()
    if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
        raise ValueError("CANON_APPLY_DURABLE_DB_REQUIRED")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("CANON_APPLY_DURABLE_DB_ABSOLUTE_PATH_REQUIRED")
    return str(candidate.resolve())


class CanonApprovalTokenIssuer:
    """Offline Governor/MASTER signer; never compose this into the runtime app."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        authority_key_id: str,
        private_key: Ed25519PrivateKey,
        reviewer_id: str,
        reviewer_role: str = "MASTER",
        default_ttl_ms: int = 120_000,
        clock: Any = now_ms,
    ) -> None:
        self.db_path = _durable_db_path(db_path)
        self.authority_key_id = validate_canon_binding_text(
            "authority_key_id", authority_key_id, max_length=128
        )
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("private_key must be Ed25519PrivateKey")
        self.__private_key = private_key
        self.reviewer_id = validate_canon_binding_text("reviewer_id", reviewer_id)
        self.reviewer_role = str(reviewer_role or "").strip().upper()
        if self.reviewer_role not in {"MASTER", "GOVERNOR"}:
            raise ValueError("CANON_APPLY_REVIEWER_ROLE_INVALID")
        self.default_ttl_ms = _exact_sqlite_int(
            default_ttl_ms,
            field_name="default_ttl_ms",
        )
        if self.default_ttl_ms <= 0:
            raise ValueError("CANON_APPLY_TOKEN_TTL_INVALID")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        with self._connect() as conn:
            _ensure_canon_apply_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @staticmethod
    def migration_asset_path() -> Path:
        return _canon_apply_migration_path()

    @classmethod
    def migration_asset_checksum(cls) -> str:
        text = cls.migration_asset_path().read_text(encoding="utf-8-sig")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def issue_approved_token(
        self,
        *,
        operation: str,
        subject_type: str,
        subject_id: str,
        subject_version: int,
        subject_hash: str,
        target_doc_id: str,
        base_version: int,
        actor_id: str,
        session_id: str,
        trace_id: str,
        evidence_refs: Iterable[str],
        token_type: str | None = None,
        ttl_ms: int | None = None,
    ) -> str:
        operation_value = str(operation or "").strip().upper()
        if operation_value not in {CANON_OPERATION_APPLY, CANON_OPERATION_ROLLBACK}:
            raise ValueError("CANON_APPLY_OPERATION_INVALID")
        subject_type_value = str(subject_type or "").strip().upper()
        if subject_type_value not in {CANON_SUBJECT_PROPOSAL, CANON_SUBJECT_PENTEST_PROMOTION}:
            raise ValueError("CANON_APPLY_SUBJECT_TYPE_INVALID")
        expected_type = (
            TOKEN_TYPE_CANON_APPLY
            if operation_value == CANON_OPERATION_APPLY
            else TOKEN_TYPE_CANON_ROLLBACK
        )
        token_type_value = str(token_type or expected_type).strip().lower()
        if token_type_value not in {TOKEN_TYPE_CANON_APPLY, TOKEN_TYPE_CANON_ROLLBACK}:
            raise ValueError("CANON_APPLY_TOKEN_TYPE_INVALID")
        fields = {
            "subject_id": validate_canon_binding_text("subject_id", subject_id),
            "target_doc_id": validate_canon_binding_text(
                "target_doc_id", target_doc_id, max_length=160
            ),
            "actor_id": validate_canon_binding_text("actor_id", actor_id),
            "session_id": validate_canon_binding_text("session_id", session_id),
            "trace_id": validate_canon_binding_text("trace_id", trace_id),
        }
        subject_hash_value = str(subject_hash or "").strip().lower()
        if len(subject_hash_value) != 64 or any(ch not in "0123456789abcdef" for ch in subject_hash_value):
            raise ValueError("CANON_APPLY_SUBJECT_HASH_INVALID")
        subject_version_value = _exact_sqlite_int(
            subject_version,
            field_name="subject_version",
        )
        base_version_value = _exact_sqlite_int(
            base_version,
            field_name="base_version",
        )
        if subject_version_value < 0 or base_version_value < 0:
            raise ValueError("CANON_APPLY_VERSION_INVALID")
        normalized_evidence = normalize_canon_evidence_refs(evidence_refs)
        evidence_digest = canon_evidence_digest(normalized_evidence)
        issued = _exact_sqlite_int(
            self._clock(),
            field_name="issued_ts_ms",
        )
        ttl = _exact_sqlite_int(
            self.default_ttl_ms if ttl_ms is None else ttl_ms,
            field_name="ttl_ms",
        )
        if issued < 0 or ttl <= 0:
            raise ValueError("CANON_APPLY_TOKEN_TTL_INVALID")
        token_ref = f"canon::{uuid.uuid4().hex}::{uuid.uuid4().hex}"
        digest = canon_token_digest(token_ref)
        expiry = issued + ttl
        approval_nonce = uuid.uuid4().hex
        attestation = {
            "schema_version": "Natsume.canon-approval-attestation.v1",
            "authority_key_id": self.authority_key_id,
            "reviewer_id": self.reviewer_id,
            "reviewer_role": self.reviewer_role,
            "decision": "APPROVE",
            "approval_nonce": approval_nonce,
            "token_digest": digest,
            "token_type": token_type_value,
            "token_issued_ts_ms": issued,
            "token_expiry_ts_ms": expiry,
            "binding": {
                "operation": operation_value,
                "subject_type": subject_type_value,
                "subject_id": fields["subject_id"],
                "subject_version": subject_version_value,
                "subject_hash": subject_hash_value,
                "target_doc_id": fields["target_doc_id"],
                "base_version": base_version_value,
                "actor_id": fields["actor_id"],
                "session_id": fields["session_id"],
                "trace_id": fields["trace_id"],
                "evidence_refs": list(normalized_evidence),
            },
        }
        attestation_json = _canonical_json(attestation)
        attestation_digest = hashlib.sha256(attestation_json.encode("utf-8")).hexdigest()
        signature = base64.b64encode(
            self.__private_key.sign(attestation_json.encode("utf-8"))
        ).decode("ascii")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO canon_apply_token (
                        token_digest, token_type, operation, subject_type, subject_id,
                        subject_version, subject_hash, target_doc_id, base_version,
                        actor_id, session_id, trace_id, evidence_digest,
                        authority_key_id, approval_attestation_json,
                        approval_attestation_digest, approval_signature, approval_nonce,
                        issued_ts_ms, expiry_ts_ms, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE')
                    """,
                    (
                        digest,
                        token_type_value,
                        operation_value,
                        subject_type_value,
                        fields["subject_id"],
                        subject_version_value,
                        subject_hash_value,
                        fields["target_doc_id"],
                        base_version_value,
                        fields["actor_id"],
                        fields["session_id"],
                        fields["trace_id"],
                        evidence_digest,
                        self.authority_key_id,
                        attestation_json,
                        attestation_digest,
                        signature,
                        approval_nonce,
                        issued,
                        expiry,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return token_ref

    def get_token_metadata(self, token_ref: str) -> dict[str, Any] | None:
        """Return non-secret metadata; the bearer value is never recoverable."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM canon_apply_token WHERE token_digest = ?",
                (canon_token_digest(token_ref),),
            ).fetchone()
        return dict(row) if row is not None else None
