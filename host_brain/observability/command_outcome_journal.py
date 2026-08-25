"""已认证 OBS 命令的持久化追加式结果账本。

账本独立于普通审计迁移，利用 fsync 的 JSONL 校验链填补 SecureIngress claim 与
领域结果之间的崩溃窗口。身份与幂等值在进入 WAL 前会摘要化；此 API 不接受
原始 body、凭据或证明，因此不能成为秘密的旁路存储。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Iterator, Mapping


OUTCOME_JOURNAL_SCHEMA_V1 = "natsume.obs-command-outcome-journal.v1"
OUTCOME_JOURNAL_SCHEMA = "natsume.obs-command-outcome-journal.v2"
_OUTCOME_JOURNAL_ANCHOR_SCHEMA = "natsume.obs-command-outcome-journal-head.v1"
_ZERO_CHECKSUM = "0" * 64
_MAX_LINE_BYTES = 64 * 1024
_MAX_ANCHOR_BYTES = 4 * 1024
_MAX_WAL_BYTES = 128 * 1024 * 1024
_DIGEST_RE = re.compile(r"^sha256:(?:(auth|preauth):)?[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+*-]{0,254}$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_.:-]{0,254}$")
_REF_RE = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://[^\s]{1,2016}$")
_FORBIDDEN_REF_RE = re.compile(
    r"(?:raw[_-]?body|raw[_-]?proof|credential[_-]?proof|secret|password|"
    r"api[_-]?key|access[_-]?token|auth[_-]?token)",
    re.IGNORECASE,
)
_RECORD_FIELDS_V1 = frozenset({
    "schema_version",
    "sequence",
    "event_type",
    "recorded_at_ms",
    "receipt_ref",
    "state",
    "side_effect_certainty",
    "key",
    "reason_code",
    "claim_ref",
    "audit_ref",
    "domain_result_ref",
    "previous_checksum",
    "checksum",
})
_RECORD_FIELDS = frozenset(
    {*_RECORD_FIELDS_V1, "continuation_ref", "terminal_result_ref"}
)
_KEY_FIELDS = frozenset({
    "principal_digest",
    "tenant_digest",
    "idempotency_digest",
    "command_id",
    "action",
    "required_scopes",
    "body_sha256",
})
_ANCHOR_FIELDS = frozenset({
    "schema_version",
    "sequence",
    "wal_size",
    "head_checksum",
    "anchor_checksum",
})


class ObsCommandOutcomeJournalError(RuntimeError):
    pass


class ObsCommandOutcomeCorruption(ObsCommandOutcomeJournalError):
    pass


class ObsCommandOutcomeTransitionError(ObsCommandOutcomeJournalError):
    pass


class ObsCommandOutcomeState(str, Enum):
    EXECUTING = "EXECUTING"
    WAITING_REVIEW = "WAITING_REVIEW"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"
    SHADOW_EVALUATED = "SHADOW_EVALUATED"


class SideEffectCertainty(str, Enum):
    NOT_EXECUTED = "NOT_EXECUTED"
    EXECUTED = "EXECUTED"
    MAY_HAVE_EXECUTED = "MAY_HAVE_EXECUTED"


class OutcomeJournalEvent(str, Enum):
    BEGIN = "BEGIN"
    WAIT_REVIEW = "WAIT_REVIEW"
    RESOLVE_REVIEW = "RESOLVE_REVIEW"
    FINALIZE = "FINALIZE"
    RECOVER = "RECOVER"
    PROMOTE_SHADOW = "PROMOTE_SHADOW"
    PREAUTH_REJECTION = "PREAUTH_REJECTION"
    AUTHENTICATED_REJECTION = "AUTHENTICATED_REJECTION"


class OutcomeBeginDisposition(str, Enum):
    NEW = "NEW"
    EXISTING_SAME = "EXISTING_SAME"
    CONFLICT = "CONFLICT"
    PROMOTED = "PROMOTED"


def _digest(value: str, *, domain: str) -> str:
    # 认证前拒绝与认证后命令使用域分隔摘要，避免攻击者构造的声明值与已认证的
    # 幂等键落入同一条结果 lane。
    if domain not in {"auth", "preauth"}:
        raise ValueError("invalid digest domain")
    prefix = f"natsume.obs-command-outcome.digest.v1\0{domain}\0".encode("ascii")
    digest = hashlib.sha256(prefix + str(value).encode("utf-8")).hexdigest()
    return f"sha256:{domain}:{digest}"


def _digest_domain(value: str) -> str:
    match = _DIGEST_RE.fullmatch(str(value or "").strip().lower())
    if match is None:
        raise ValueError("invalid digest")
    # Untagged v1 digests remain readable and are authenticated-domain keys.
    return match.group(1) or "auth"


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _checksum(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _validate_token(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(normalized):
        raise ValueError(f"invalid {field}")
    return normalized


def _validate_reason(value: str) -> str:
    normalized = str(value or "").strip()
    if not _REASON_RE.fullmatch(normalized):
        raise ValueError("invalid reason_code")
    return normalized


def _validate_ref(value: str | None, *, field: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    normalized = str(value).strip()
    if (
        not _REF_RE.fullmatch(normalized)
        or _FORBIDDEN_REF_RE.search(normalized)
    ):
        raise ValueError(f"invalid {field}")
    return normalized


@dataclass(frozen=True, slots=True)
class ObsCommandOutcomeKey:
    """决定一次命令结果 lane 的隐私安全幂等身份。"""
    principal_digest: str
    tenant_digest: str
    idempotency_digest: str
    command_id: str
    action: str
    required_scopes: tuple[str, ...]
    body_sha256: str

    def __post_init__(self) -> None:
        digest_domains: set[str] = set()
        for field in ("principal_digest", "tenant_digest", "idempotency_digest"):
            value = str(getattr(self, field) or "").strip().lower()
            if not _DIGEST_RE.fullmatch(value):
                raise ValueError(f"invalid {field}")
            object.__setattr__(self, field, value)
            digest_domains.add(_digest_domain(value))
        if len(digest_domains) != 1:
            raise ValueError("digest domains must match")
        object.__setattr__(self, "command_id", _validate_token(self.command_id, field="command_id"))
        object.__setattr__(self, "action", _validate_token(self.action, field="action"))
        scopes = tuple(sorted({_validate_token(item, field="required_scope") for item in self.required_scopes}))
        if not scopes:
            raise ValueError("required_scopes cannot be empty")
        object.__setattr__(self, "required_scopes", scopes)
        body_sha256 = str(self.body_sha256 or "").strip().lower()
        if not _SHA256_RE.fullmatch(body_sha256):
            raise ValueError("invalid body_sha256")
        object.__setattr__(self, "body_sha256", body_sha256)

    @classmethod
    def from_values(
        cls,
        *,
        principal_id: str,
        tenant_id: str,
        idempotency_key: str,
        command_id: str,
        action: str,
        required_scopes: tuple[str, ...],
        body_sha256: str,
    ) -> "ObsCommandOutcomeKey":
        return cls(
            principal_digest=_digest(principal_id, domain="auth"),
            tenant_digest=_digest(tenant_id, domain="auth"),
            idempotency_digest=_digest(idempotency_key, domain="auth"),
            command_id=command_id,
            action=action,
            required_scopes=required_scopes,
            body_sha256=body_sha256,
        )

    @classmethod
    def _from_preauth_values(
        cls,
        *,
        principal_id: str,
        tenant_id: str,
        idempotency_key: str,
        command_id: str,
        action: str,
        required_scopes: tuple[str, ...],
        body_sha256: str,
    ) -> "ObsCommandOutcomeKey":
        return cls(
            principal_digest=_digest(principal_id, domain="preauth"),
            tenant_digest=_digest(tenant_id, domain="preauth"),
            idempotency_digest=_digest(idempotency_key, domain="preauth"),
            command_id=command_id,
            action=action,
            required_scopes=required_scopes,
            body_sha256=body_sha256,
        )

    @property
    def digest_domain(self) -> str:
        return _digest_domain(self.idempotency_digest)

    @property
    def lane(self) -> tuple[str, str, str]:
        return (
            self.principal_digest,
            self.tenant_digest,
            self.idempotency_digest,
        )

    @property
    def receipt_ref(self) -> str:
        lane = {
            "principal_digest": self.principal_digest,
            "tenant_digest": self.tenant_digest,
            "idempotency_digest": self.idempotency_digest,
        }
        return f"obs-command-outcome://{hashlib.sha256(_canonical_json(lane)).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_digest": self.principal_digest,
            "tenant_digest": self.tenant_digest,
            "idempotency_digest": self.idempotency_digest,
            "command_id": self.command_id,
            "action": self.action,
            "required_scopes": list(self.required_scopes),
            "body_sha256": self.body_sha256,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ObsCommandOutcomeKey":
        if not isinstance(value, dict) or set(value) != _KEY_FIELDS:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_KEY_SCHEMA_INVALID")
        scopes = value.get("required_scopes")
        if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_KEY_SCHEMA_INVALID")
        try:
            return cls(
                principal_digest=value["principal_digest"],
                tenant_digest=value["tenant_digest"],
                idempotency_digest=value["idempotency_digest"],
                command_id=value["command_id"],
                action=value["action"],
                required_scopes=tuple(scopes),
                body_sha256=value["body_sha256"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_KEY_SCHEMA_INVALID") from exc


@dataclass(frozen=True, slots=True)
class ObsCommandOutcomeRecord:
    schema_version: str
    sequence: int
    event_type: OutcomeJournalEvent
    recorded_at_ms: int
    receipt_ref: str
    state: ObsCommandOutcomeState
    side_effect_certainty: SideEffectCertainty
    key: ObsCommandOutcomeKey
    reason_code: str
    claim_ref: str | None
    audit_ref: str | None
    domain_result_ref: str | None
    continuation_ref: str | None
    terminal_result_ref: str | None
    previous_checksum: str
    checksum: str

    def __post_init__(self) -> None:
        if self.schema_version not in {
            OUTCOME_JOURNAL_SCHEMA_V1,
            OUTCOME_JOURNAL_SCHEMA,
        }:
            raise ValueError("invalid schema_version")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("invalid sequence")
        if type(self.recorded_at_ms) is not int or self.recorded_at_ms < 0:
            raise ValueError("invalid recorded_at_ms")
        if self.receipt_ref != self.key.receipt_ref:
            raise ValueError("receipt_ref does not match key")
        _validate_ref(self.receipt_ref, field="receipt_ref", required=True)
        _validate_reason(self.reason_code)
        _validate_ref(self.claim_ref, field="claim_ref")
        _validate_ref(self.audit_ref, field="audit_ref")
        _validate_ref(self.domain_result_ref, field="domain_result_ref")
        _validate_ref(self.continuation_ref, field="continuation_ref")
        _validate_ref(self.terminal_result_ref, field="terminal_result_ref")
        if self.schema_version == OUTCOME_JOURNAL_SCHEMA_V1 and (
            self.continuation_ref is not None
            or self.terminal_result_ref is not None
            or self.state is ObsCommandOutcomeState.WAITING_REVIEW
            or self.event_type
            in {OutcomeJournalEvent.WAIT_REVIEW, OutcomeJournalEvent.RESOLVE_REVIEW}
        ):
            raise ValueError("v1 record contains v2 Review outcome fields")
        if self.state is ObsCommandOutcomeState.WAITING_REVIEW and (
            self.domain_result_ref is None
            or self.continuation_ref is None
            or self.terminal_result_ref is not None
        ):
            raise ValueError("waiting Review record binding is incomplete")
        if self.event_type is OutcomeJournalEvent.RESOLVE_REVIEW and (
            self.domain_result_ref is None
            or self.continuation_ref is None
            or self.terminal_result_ref is None
        ):
            raise ValueError("resolved Review record binding is incomplete")
        if not _SHA256_RE.fullmatch(self.previous_checksum):
            raise ValueError("invalid previous_checksum")
        if not _SHA256_RE.fullmatch(self.checksum):
            raise ValueError("invalid checksum")
        _validate_state_certainty(self.state, self.side_effect_certainty)
        _validate_event_state(self.event_type, self.state)

    def unsigned_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "event_type": self.event_type.value,
            "recorded_at_ms": self.recorded_at_ms,
            "receipt_ref": self.receipt_ref,
            "state": self.state.value,
            "side_effect_certainty": self.side_effect_certainty.value,
            "key": self.key.to_dict(),
            "reason_code": self.reason_code,
            "claim_ref": self.claim_ref,
            "audit_ref": self.audit_ref,
            "domain_result_ref": self.domain_result_ref,
            "previous_checksum": self.previous_checksum,
        }
        if self.schema_version == OUTCOME_JOURNAL_SCHEMA:
            value.update(
                {
                    "continuation_ref": self.continuation_ref,
                    "terminal_result_ref": self.terminal_result_ref,
                }
            )
        return value

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "checksum": self.checksum}


@dataclass(frozen=True, slots=True)
class OutcomeBeginResult:
    disposition: OutcomeBeginDisposition
    record: ObsCommandOutcomeRecord


@dataclass(frozen=True, slots=True)
class ObsCommandReviewWaitRecovery:
    """Canonical Task/Review proof for one crash-left outer command claim."""

    receipt_ref: str
    claim_ref: str
    audit_ref: str
    command_id: str
    action: str
    required_scopes: tuple[str, ...]
    body_sha256: str
    domain_result_ref: str
    continuation_ref: str

    def __post_init__(self) -> None:
        _validate_ref(self.receipt_ref, field="receipt_ref", required=True)
        _validate_ref(self.claim_ref, field="claim_ref", required=True)
        _validate_ref(self.audit_ref, field="audit_ref", required=True)
        _validate_token(self.command_id, field="command_id")
        _validate_token(self.action, field="action")
        scopes = tuple(
            sorted(
                {
                    _validate_token(item, field="required_scope")
                    for item in self.required_scopes
                }
            )
        )
        if not scopes:
            raise ValueError("required_scopes cannot be empty")
        object.__setattr__(self, "required_scopes", scopes)
        body_sha256 = str(self.body_sha256 or "").strip().lower()
        if not _SHA256_RE.fullmatch(body_sha256):
            raise ValueError("invalid body_sha256")
        object.__setattr__(self, "body_sha256", body_sha256)
        _validate_ref(self.domain_result_ref, field="domain_result_ref", required=True)
        _validate_ref(self.continuation_ref, field="continuation_ref", required=True)


ReviewWaitRecoveryResolver = Callable[[ObsCommandOutcomeRecord], ObsCommandReviewWaitRecovery | None]


def _validate_state_certainty(
    state: ObsCommandOutcomeState,
    certainty: SideEffectCertainty,
) -> None:
    allowed = {
        ObsCommandOutcomeState.EXECUTING: {SideEffectCertainty.NOT_EXECUTED},
        ObsCommandOutcomeState.WAITING_REVIEW: {
            SideEffectCertainty.NOT_EXECUTED
        },
        ObsCommandOutcomeState.SHADOW_EVALUATED: {SideEffectCertainty.NOT_EXECUTED},
        ObsCommandOutcomeState.SUCCEEDED: {
            SideEffectCertainty.NOT_EXECUTED,
            SideEffectCertainty.EXECUTED,
        },
        ObsCommandOutcomeState.REJECTED: {
            SideEffectCertainty.NOT_EXECUTED,
            SideEffectCertainty.EXECUTED,
        },
        ObsCommandOutcomeState.UNCERTAIN: {
            SideEffectCertainty.MAY_HAVE_EXECUTED,
            SideEffectCertainty.EXECUTED,
        },
    }
    if certainty not in allowed[state]:
        raise ValueError("state and side-effect certainty are inconsistent")


def _validate_event_state(event: OutcomeJournalEvent, state: ObsCommandOutcomeState) -> None:
    allowed = {
        OutcomeJournalEvent.BEGIN: {
            ObsCommandOutcomeState.EXECUTING,
            ObsCommandOutcomeState.SHADOW_EVALUATED,
        },
        OutcomeJournalEvent.PROMOTE_SHADOW: {ObsCommandOutcomeState.EXECUTING},
        OutcomeJournalEvent.WAIT_REVIEW: {
            ObsCommandOutcomeState.WAITING_REVIEW
        },
        OutcomeJournalEvent.RESOLVE_REVIEW: {
            ObsCommandOutcomeState.SUCCEEDED,
            ObsCommandOutcomeState.REJECTED,
        },
        OutcomeJournalEvent.FINALIZE: {
            ObsCommandOutcomeState.SUCCEEDED,
            ObsCommandOutcomeState.REJECTED,
            ObsCommandOutcomeState.UNCERTAIN,
        },
        OutcomeJournalEvent.RECOVER: {ObsCommandOutcomeState.UNCERTAIN},
        OutcomeJournalEvent.PREAUTH_REJECTION: {ObsCommandOutcomeState.REJECTED},
        OutcomeJournalEvent.AUTHENTICATED_REJECTION: {
            ObsCommandOutcomeState.REJECTED
        },
    }
    if state not in allowed[event]:
        raise ValueError("event and state are inconsistent")


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _process_lock(path: Path, *, timeout_s: float = 30.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b", buffering=0)
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            deadline = time.monotonic() + max(0.1, float(timeout_s))
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ObsCommandOutcomeJournalError("OBS_COMMAND_OUTCOME_LOCK_TIMEOUT")
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif os.name == "posix":
            import fcntl  # type: ignore[import-not-found]  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:  # pragma: no cover - unsupported platform must never run unlocked
            raise ObsCommandOutcomeJournalError("OBS_COMMAND_OUTCOME_LOCK_UNSUPPORTED")
    finally:
        handle.close()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_DUPLICATE_FIELD")
        value[key] = child
    return value


def _reject_nonfinite(_value: str) -> None:
    raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_NONFINITE_VALUE")


def _directory_fsync_is_unsupported(exc: OSError) -> bool:
    unsupported = {
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if os.name == "nt":
        unsupported.update({errno.EACCES, errno.EPERM, errno.EBADF})
    return exc.errno in unsupported


def _fsync_directory(path: Path) -> None:
    """Fsync a directory, ignoring only an explicitly unsupported operation."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(str(path), flags)
    except (AttributeError, NotImplementedError):
        return
    except OSError as exc:
        if _directory_fsync_is_unsupported(exc):
            return
        raise ObsCommandOutcomeJournalError(
            "OBS_COMMAND_OUTCOME_DIRECTORY_FSYNC_FAILED"
        ) from exc
    try:
        try:
            os.fsync(fd)
        except (AttributeError, NotImplementedError):
            return
        except OSError as exc:
            if _directory_fsync_is_unsupported(exc):
                return
            raise ObsCommandOutcomeJournalError(
                "OBS_COMMAND_OUTCOME_DIRECTORY_FSYNC_FAILED"
            ) from exc
    finally:
        os.close(fd)


class ObsCommandOutcomeJournal:
    """带校验链的结果 WAL，支持跨进程原子 begin。"""

    def __init__(
        self,
        path: str | Path,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._path = Path(path).resolve()
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._anchor_path = self._path.with_suffix(self._path.suffix + ".head")
        self._anchor_temp_path = self._anchor_path.with_suffix(
            self._anchor_path.suffix + ".tmp"
        )
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._local_lock = _thread_lock(self._lock_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            self._load_records_locked()

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._local_lock:
            with _process_lock(self._lock_path):
                yield

    def begin(
        self,
        key: ObsCommandOutcomeKey,
        *,
        claim_ref: str,
        audit_ref: str,
        shadow: bool = False,
        promote_shadow: bool = False,
        recorded_at_ms: int | None = None,
    ) -> OutcomeBeginResult:
        # BEGIN 必须与“该 lane 是否已有结果”在同一文件锁内判定。这样并发重试
        # 获得 EXISTING_SAME 而非第二个执行许可；不同 body 的同键请求得到冲突。
        if not isinstance(key, ObsCommandOutcomeKey):
            raise TypeError("key must be ObsCommandOutcomeKey")
        if key.digest_domain != "auth":
            raise ObsCommandOutcomeTransitionError("OBS_COMMAND_PREAUTH_KEY_NOT_EXECUTABLE")
        claim = _validate_ref(claim_ref, field="claim_ref", required=True)
        audit = _validate_ref(audit_ref, field="audit_ref", required=True)
        with self._locked():
            records = self._load_records_locked()
            latest = self._latest_by_lane(records).get(key.lane)
            if latest is not None:
                if latest.key != key:
                    return OutcomeBeginResult(OutcomeBeginDisposition.CONFLICT, latest)
                if promote_shadow:
                    if latest.state is not ObsCommandOutcomeState.SHADOW_EVALUATED:
                        return OutcomeBeginResult(OutcomeBeginDisposition.CONFLICT, latest)
                    promoted = self._append_locked(
                        records,
                        event_type=OutcomeJournalEvent.PROMOTE_SHADOW,
                        key=key,
                        state=ObsCommandOutcomeState.EXECUTING,
                        certainty=SideEffectCertainty.NOT_EXECUTED,
                        reason_code="OBS_COMMAND_SHADOW_PROMOTED",
                        claim_ref=claim,
                        audit_ref=audit,
                        domain_result_ref=None,
                        recorded_at_ms=recorded_at_ms,
                    )
                    return OutcomeBeginResult(OutcomeBeginDisposition.PROMOTED, promoted)
                return OutcomeBeginResult(OutcomeBeginDisposition.EXISTING_SAME, latest)
            if promote_shadow:
                raise ObsCommandOutcomeTransitionError("OBS_COMMAND_SHADOW_RECEIPT_MISSING")
            state = (
                ObsCommandOutcomeState.SHADOW_EVALUATED
                if shadow
                else ObsCommandOutcomeState.EXECUTING
            )
            reason = (
                "OBS_COMMAND_SHADOW_EVALUATED"
                if shadow
                else "OBS_COMMAND_EXECUTION_STARTED"
            )
            record = self._append_locked(
                records,
                event_type=OutcomeJournalEvent.BEGIN,
                key=key,
                state=state,
                certainty=SideEffectCertainty.NOT_EXECUTED,
                reason_code=reason,
                claim_ref=claim,
                audit_ref=audit,
                domain_result_ref=None,
                recorded_at_ms=recorded_at_ms,
            )
            return OutcomeBeginResult(OutcomeBeginDisposition.NEW, record)

    def record_authenticated_rejection(
        self,
        key: ObsCommandOutcomeKey,
        *,
        claim_ref: str,
        audit_ref: str,
        reason_code: str,
        recorded_at_ms: int | None = None,
    ) -> OutcomeBeginResult:
        """Atomically close an authenticated command before domain execution."""

        if not isinstance(key, ObsCommandOutcomeKey):
            raise TypeError("key must be ObsCommandOutcomeKey")
        if key.digest_domain != "auth":
            raise ObsCommandOutcomeTransitionError("OBS_COMMAND_PREAUTH_KEY_NOT_EXECUTABLE")
        claim = _validate_ref(claim_ref, field="claim_ref", required=True)
        audit = _validate_ref(audit_ref, field="audit_ref", required=True)
        reason = _validate_reason(reason_code)
        with self._locked():
            records = self._load_records_locked()
            latest = self._latest_by_lane(records).get(key.lane)
            if latest is not None:
                disposition = (
                    OutcomeBeginDisposition.EXISTING_SAME
                    if latest.key == key
                    else OutcomeBeginDisposition.CONFLICT
                )
                return OutcomeBeginResult(disposition, latest)
            record = self._append_locked(
                records,
                event_type=OutcomeJournalEvent.AUTHENTICATED_REJECTION,
                key=key,
                state=ObsCommandOutcomeState.REJECTED,
                certainty=SideEffectCertainty.NOT_EXECUTED,
                reason_code=reason,
                claim_ref=claim,
                audit_ref=audit,
                domain_result_ref=None,
                recorded_at_ms=recorded_at_ms,
            )
            return OutcomeBeginResult(OutcomeBeginDisposition.NEW, record)

    def finalize(
        self,
        receipt_ref: str,
        *,
        state: ObsCommandOutcomeState,
        reason_code: str,
        side_effect_certainty: SideEffectCertainty,
        domain_result_ref: str | None = None,
        terminal_result_ref: str | None = None,
        recorded_at_ms: int | None = None,
    ) -> ObsCommandOutcomeRecord:
        # 终态不可改写；相同参数的重复 finalize 才可安全重放。特别地，等待
        # Review 的记录必须保持原领域结果与 continuation 绑定，不能直接猜测不确定。
        receipt = _validate_ref(receipt_ref, field="receipt_ref", required=True)
        if state not in {
            ObsCommandOutcomeState.SUCCEEDED,
            ObsCommandOutcomeState.REJECTED,
            ObsCommandOutcomeState.UNCERTAIN,
        }:
            raise ObsCommandOutcomeTransitionError("OBS_COMMAND_OUTCOME_FINAL_STATE_INVALID")
        reason = _validate_reason(reason_code)
        result_ref = _validate_ref(domain_result_ref, field="domain_result_ref")
        terminal_ref = _validate_ref(
            terminal_result_ref,
            field="terminal_result_ref",
        )
        if state is ObsCommandOutcomeState.SUCCEEDED and result_ref is None:
            raise ObsCommandOutcomeTransitionError("OBS_COMMAND_DOMAIN_RESULT_REF_REQUIRED")
        _validate_state_certainty(state, side_effect_certainty)
        with self._locked():
            records = self._load_records_locked()
            latest = self._latest_by_receipt(records).get(receipt)
            if latest is None:
                raise ObsCommandOutcomeTransitionError("OBS_COMMAND_OUTCOME_RECEIPT_NOT_FOUND")
            if latest.state in {
                ObsCommandOutcomeState.SUCCEEDED,
                ObsCommandOutcomeState.REJECTED,
                ObsCommandOutcomeState.UNCERTAIN,
            }:
                if (
                    latest.state is state
                    and latest.reason_code == reason
                    and latest.side_effect_certainty is side_effect_certainty
                    and latest.domain_result_ref == result_ref
                    and latest.terminal_result_ref == terminal_ref
                ):
                    return latest
                raise ObsCommandOutcomeTransitionError("OBS_COMMAND_OUTCOME_TERMINAL_IMMUTABLE")
            if latest.state not in {
                ObsCommandOutcomeState.EXECUTING,
                ObsCommandOutcomeState.WAITING_REVIEW,
            }:
                raise ObsCommandOutcomeTransitionError(
                    "OBS_COMMAND_OUTCOME_NOT_FINALIZABLE"
                )
            if (
                latest.state is ObsCommandOutcomeState.WAITING_REVIEW
                and state is ObsCommandOutcomeState.UNCERTAIN
            ):
                raise ObsCommandOutcomeTransitionError(
                    "OBS_COMMAND_REVIEW_OUTCOME_UNCERTAIN_DENIED"
                )
            if latest.state is ObsCommandOutcomeState.WAITING_REVIEW:
                if (
                    result_ref != latest.domain_result_ref
                    or not latest.continuation_ref
                    or terminal_ref is None
                ):
                    raise ObsCommandOutcomeTransitionError(
                        "OBS_COMMAND_REVIEW_TERMINAL_BINDING_INVALID"
                    )
            return self._append_locked(
                records,
                event_type=(
                    OutcomeJournalEvent.RESOLVE_REVIEW
                    if latest.state is ObsCommandOutcomeState.WAITING_REVIEW
                    else OutcomeJournalEvent.FINALIZE
                ),
                key=latest.key,
                state=state,
                certainty=side_effect_certainty,
                reason_code=reason,
                claim_ref=latest.claim_ref,
                audit_ref=latest.audit_ref,
                domain_result_ref=result_ref,
                recorded_at_ms=recorded_at_ms,
                continuation_ref=latest.continuation_ref,
                terminal_result_ref=terminal_ref,
            )

    def mark_waiting_review(
        self,
        receipt_ref: str,
        *,
        domain_result_ref: str,
        continuation_ref: str,
        recorded_at_ms: int | None = None,
    ) -> ObsCommandOutcomeRecord:
        """Persist a governed Review wait without completing Secure Ingress.

        ``continuation_ref`` is a secret-free, reversible reference to the
        immutable graph/review subject.  The central claim deliberately stays
        open until Review continuation appends a terminal outcome record.
        """

        receipt = _validate_ref(receipt_ref, field="receipt_ref", required=True)
        continuation = _validate_ref(
            continuation_ref,
            field="continuation_ref",
            required=True,
        )
        domain_result = _validate_ref(
            domain_result_ref,
            field="domain_result_ref",
            required=True,
        )
        with self._locked():
            records = self._load_records_locked()
            latest = self._latest_by_receipt(records).get(receipt)
            if latest is None:
                raise ObsCommandOutcomeTransitionError(
                    "OBS_COMMAND_OUTCOME_RECEIPT_NOT_FOUND"
                )
            if latest.state is ObsCommandOutcomeState.WAITING_REVIEW:
                if (
                    latest.domain_result_ref == domain_result
                    and latest.continuation_ref == continuation
                ):
                    return latest
                raise ObsCommandOutcomeTransitionError(
                    "OBS_COMMAND_REVIEW_CONTINUATION_CONFLICT"
                )
            if latest.state in {
                ObsCommandOutcomeState.SUCCEEDED,
                ObsCommandOutcomeState.REJECTED,
                ObsCommandOutcomeState.UNCERTAIN,
            }:
                raise ObsCommandOutcomeTransitionError(
                    "OBS_COMMAND_OUTCOME_TERMINAL_IMMUTABLE"
                )
            if latest.state is not ObsCommandOutcomeState.EXECUTING:
                raise ObsCommandOutcomeTransitionError(
                    "OBS_COMMAND_OUTCOME_NOT_EXECUTING"
                )
            return self._append_locked(
                records,
                event_type=OutcomeJournalEvent.WAIT_REVIEW,
                key=latest.key,
                state=ObsCommandOutcomeState.WAITING_REVIEW,
                certainty=SideEffectCertainty.NOT_EXECUTED,
                reason_code="OBS_COMMAND_WAITING_REVIEW",
                claim_ref=latest.claim_ref,
                audit_ref=latest.audit_ref,
                domain_result_ref=domain_result,
                recorded_at_ms=recorded_at_ms,
                continuation_ref=continuation,
            )

    def lookup(
        self,
        key_or_receipt: ObsCommandOutcomeKey | str,
    ) -> ObsCommandOutcomeRecord | None:
        with self._locked():
            records = self._load_records_locked()
            if isinstance(key_or_receipt, ObsCommandOutcomeKey):
                found = self._latest_by_lane(records).get(key_or_receipt.lane)
                return found if found is not None and found.key == key_or_receipt else None
            receipt = _validate_ref(str(key_or_receipt), field="receipt_ref", required=True)
            return self._latest_by_receipt(records).get(receipt)

    def lookup_by_claim_ref(
        self,
        claim_ref: str,
    ) -> ObsCommandOutcomeRecord | None:
        """Resolve the latest outer outcome owned by one Secure Ingress claim."""

        claim = _validate_ref(claim_ref, field="claim_ref", required=True)
        with self._locked():
            records = self._load_records_locked()
            matches = tuple(
                record
                for record in self._latest_by_lane(records).values()
                if record.claim_ref == claim
            )
            if len(matches) > 1:
                raise ObsCommandOutcomeCorruption(
                    "OBS_COMMAND_OUTCOME_CLAIM_NOT_UNIQUE"
                )
            return matches[0] if matches else None

    def lookup_waiting_review_by_claim_ref(
        self,
        claim_ref: str,
    ) -> ObsCommandOutcomeRecord | None:
        """Resolve the immutable pending Review event for exact continuation."""

        claim = _validate_ref(claim_ref, field="claim_ref", required=True)
        with self._locked():
            matches = tuple(
                record
                for record in self._load_records_locked()
                if record.claim_ref == claim
                and record.state is ObsCommandOutcomeState.WAITING_REVIEW
            )
            if len(matches) > 1:
                raise ObsCommandOutcomeCorruption(
                    "OBS_COMMAND_OUTCOME_REVIEW_BINDING_NOT_UNIQUE"
                )
            return matches[0] if matches else None

    def recover_incomplete(
        self,
        *,
        recorded_at_ms: int | None = None,
        review_wait_resolver: ReviewWaitRecoveryResolver | None = None,
        defer_review_command_ids: frozenset[str] = frozenset(),
    ) -> tuple[ObsCommandOutcomeRecord, ...]:
        """Mark crash-left EXECUTING records UNCERTAIN under the owner lock.

        Call this once after the process has acquired exclusive runtime
        ownership.  It deliberately never reopens or retries a domain port.
        """

        # 恢复从不重跑领域端口：崩溃留下的 EXECUTING 默认升级为 MAY_HAVE_EXECUTED。
        # 只有能证明它已进入 Review 等待态时，才恢复为可继续、但未完成的状态。
        if review_wait_resolver is not None and not callable(review_wait_resolver):
            raise TypeError("review_wait_resolver must be callable")
        deferred = frozenset(
            _validate_token(item, field="defer_review_command_id")
            for item in defer_review_command_ids
        )
        recovered: list[ObsCommandOutcomeRecord] = []
        with self._locked():
            records = self._load_records_locked()
            latest_records = list(self._latest_by_lane(records).values())
            for latest in latest_records:
                if latest.state is not ObsCommandOutcomeState.EXECUTING:
                    continue
                if (
                    review_wait_resolver is None
                    and latest.key.command_id in deferred
                ):
                    # Task Runtime is composed after Secure Ingress.  Keep the
                    # outer claim non-terminal until canonical HostCommand,
                    # TaskGraph and Review authorities can be reopened.
                    continue
                review_recovery = (
                    review_wait_resolver(latest)
                    if review_wait_resolver is not None
                    else None
                )
                if review_recovery is not None:
                    if not isinstance(
                        review_recovery,
                        ObsCommandReviewWaitRecovery,
                    ):
                        raise ObsCommandOutcomeCorruption(
                            "OBS_COMMAND_REVIEW_RECOVERY_PROOF_INVALID"
                        )
                    expected = (
                        review_recovery.receipt_ref == latest.receipt_ref,
                        review_recovery.claim_ref == latest.claim_ref,
                        review_recovery.audit_ref == latest.audit_ref,
                        review_recovery.command_id == latest.key.command_id,
                        review_recovery.action == latest.key.action,
                        review_recovery.required_scopes
                        == latest.key.required_scopes,
                        review_recovery.body_sha256
                        == latest.key.body_sha256,
                    )
                    if not all(expected):
                        raise ObsCommandOutcomeCorruption(
                            "OBS_COMMAND_REVIEW_RECOVERY_BINDING_DRIFT"
                        )
                    record = self._append_locked(
                        records,
                        event_type=OutcomeJournalEvent.WAIT_REVIEW,
                        key=latest.key,
                        state=ObsCommandOutcomeState.WAITING_REVIEW,
                        certainty=SideEffectCertainty.NOT_EXECUTED,
                        reason_code="OBS_COMMAND_WAITING_REVIEW_RECOVERED",
                        claim_ref=latest.claim_ref,
                        audit_ref=latest.audit_ref,
                        domain_result_ref=review_recovery.domain_result_ref,
                        recorded_at_ms=recorded_at_ms,
                        continuation_ref=review_recovery.continuation_ref,
                    )
                    records.append(record)
                    recovered.append(record)
                    continue
                record = self._append_locked(
                    records,
                    event_type=OutcomeJournalEvent.RECOVER,
                    key=latest.key,
                    state=ObsCommandOutcomeState.UNCERTAIN,
                    certainty=SideEffectCertainty.MAY_HAVE_EXECUTED,
                    reason_code="OBS_COMMAND_CRASH_RECOVERED_UNCERTAIN",
                    claim_ref=latest.claim_ref,
                    audit_ref=latest.audit_ref,
                    domain_result_ref=None,
                    recorded_at_ms=recorded_at_ms,
                )
                records.append(record)
                recovered.append(record)
        return tuple(recovered)

    def record_preauth_rejection(
        self,
        *,
        principal_claim: str | None,
        tenant_claim: str | None,
        idempotency_claim: str | None,
        command_id: str,
        action: str,
        required_scopes: tuple[str, ...],
        body_sha256: str,
        reason_code: str,
        audit_ref: str | None = None,
        recorded_at_ms: int | None = None,
    ) -> OutcomeBeginResult:
        # The digest tag is part of the persisted digest itself, so no possible
        # authenticated raw idempotency string can alias this pre-auth lane.
        preauth_idempotency = idempotency_claim or f"{body_sha256}:{reason_code}"
        key = ObsCommandOutcomeKey._from_preauth_values(
            principal_id=principal_claim or "anonymous",
            tenant_id=tenant_claim or "unknown-tenant",
            idempotency_key=preauth_idempotency,
            command_id=command_id,
            action=action,
            required_scopes=required_scopes,
            body_sha256=body_sha256,
        )
        reason = _validate_reason(reason_code)
        audit = _validate_ref(audit_ref, field="audit_ref")
        with self._locked():
            records = self._load_records_locked()
            latest = self._latest_by_lane(records).get(key.lane)
            if latest is not None:
                disposition = (
                    OutcomeBeginDisposition.EXISTING_SAME
                    if latest.key == key
                    else OutcomeBeginDisposition.CONFLICT
                )
                return OutcomeBeginResult(disposition, latest)
            record = self._append_locked(
                records,
                event_type=OutcomeJournalEvent.PREAUTH_REJECTION,
                key=key,
                state=ObsCommandOutcomeState.REJECTED,
                certainty=SideEffectCertainty.NOT_EXECUTED,
                reason_code=reason,
                claim_ref=None,
                audit_ref=audit,
                domain_result_ref=None,
                recorded_at_ms=recorded_at_ms,
            )
            return OutcomeBeginResult(OutcomeBeginDisposition.NEW, record)

    def records(self) -> tuple[ObsCommandOutcomeRecord, ...]:
        with self._locked():
            return tuple(self._load_records_locked())

    @staticmethod
    def _latest_by_lane(
        records: list[ObsCommandOutcomeRecord],
    ) -> dict[tuple[str, str, str], ObsCommandOutcomeRecord]:
        return {record.key.lane: record for record in records}

    @staticmethod
    def _latest_by_receipt(
        records: list[ObsCommandOutcomeRecord],
    ) -> dict[str, ObsCommandOutcomeRecord]:
        return {record.receipt_ref: record for record in records}

    def _append_locked(
        self,
        records: list[ObsCommandOutcomeRecord],
        *,
        event_type: OutcomeJournalEvent,
        key: ObsCommandOutcomeKey,
        state: ObsCommandOutcomeState,
        certainty: SideEffectCertainty,
        reason_code: str,
        claim_ref: str | None,
        audit_ref: str | None,
        domain_result_ref: str | None,
        recorded_at_ms: int | None,
        continuation_ref: str | None = None,
        terminal_result_ref: str | None = None,
    ) -> ObsCommandOutcomeRecord:
        # 追加记录后 fsync WAL，再用原子替换推进 head anchor；两者共同检测截断、
        # 回滚或跨进程篡改，调用者不能把内存中的 state 当作已提交事实。
        sequence = len(records) + 1
        previous = records[-1].checksum if records else _ZERO_CHECKSUM
        unsigned = {
            "schema_version": OUTCOME_JOURNAL_SCHEMA,
            "sequence": sequence,
            "event_type": event_type.value,
            "recorded_at_ms": int(self._clock_ms() if recorded_at_ms is None else recorded_at_ms),
            "receipt_ref": key.receipt_ref,
            "state": state.value,
            "side_effect_certainty": certainty.value,
            "key": key.to_dict(),
            "reason_code": _validate_reason(reason_code),
            "claim_ref": _validate_ref(claim_ref, field="claim_ref"),
            "audit_ref": _validate_ref(audit_ref, field="audit_ref"),
            "domain_result_ref": _validate_ref(domain_result_ref, field="domain_result_ref"),
            "continuation_ref": _validate_ref(
                continuation_ref,
                field="continuation_ref",
            ),
            "terminal_result_ref": _validate_ref(
                terminal_result_ref,
                field="terminal_result_ref",
            ),
            "previous_checksum": previous,
        }
        record = ObsCommandOutcomeRecord(
            schema_version=OUTCOME_JOURNAL_SCHEMA,
            sequence=sequence,
            event_type=event_type,
            recorded_at_ms=unsigned["recorded_at_ms"],
            receipt_ref=key.receipt_ref,
            state=state,
            side_effect_certainty=certainty,
            key=key,
            reason_code=unsigned["reason_code"],
            claim_ref=unsigned["claim_ref"],
            audit_ref=unsigned["audit_ref"],
            domain_result_ref=unsigned["domain_result_ref"],
            continuation_ref=unsigned["continuation_ref"],
            terminal_result_ref=unsigned["terminal_result_ref"],
            previous_checksum=previous,
            checksum=_checksum(unsigned),
        )
        line = _canonical_json(record.to_dict()) + b"\n"
        if len(line) > _MAX_LINE_BYTES:
            raise ObsCommandOutcomeJournalError("OBS_COMMAND_OUTCOME_RECORD_TOO_LARGE")
        try:
            current_size = self._path.stat().st_size if self._path.exists() else 0
        except OSError as exc:
            raise ObsCommandOutcomeJournalError(
                "OBS_COMMAND_OUTCOME_WAL_UNAVAILABLE"
            ) from exc
        new_size = current_size + len(line)
        if new_size > _MAX_WAL_BYTES:
            raise ObsCommandOutcomeJournalError("OBS_COMMAND_OUTCOME_WAL_TOO_LARGE")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        created = current_size == 0 and not self._path.exists()
        fd = os.open(str(self._path), flags, 0o600)
        try:
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short outcome journal write")
                view = view[written:]
            os.fsync(fd)
            if os.fstat(fd).st_size != new_size:
                raise ObsCommandOutcomeCorruption(
                    "OBS_COMMAND_OUTCOME_WAL_SIZE_CHANGED"
                )
        finally:
            os.close(fd)
        if created:
            _fsync_directory(self._path.parent)
        self._write_anchor_locked(record, wal_size=new_size)
        return record

    @staticmethod
    def _anchor_payload(
        record: ObsCommandOutcomeRecord,
        *,
        wal_size: int,
    ) -> dict[str, Any]:
        unsigned = {
            "schema_version": _OUTCOME_JOURNAL_ANCHOR_SCHEMA,
            "sequence": record.sequence,
            "wal_size": wal_size,
            "head_checksum": record.checksum,
        }
        return {**unsigned, "anchor_checksum": _checksum(unsigned)}

    def _write_anchor_locked(
        self,
        record: ObsCommandOutcomeRecord,
        *,
        wal_size: int,
    ) -> None:
        payload = self._anchor_payload(record, wal_size=wal_size)
        line = _canonical_json(payload) + b"\n"
        if len(line) > _MAX_ANCHOR_BYTES:
            raise ObsCommandOutcomeJournalError("OBS_COMMAND_OUTCOME_ANCHOR_TOO_LARGE")
        flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(str(self._anchor_temp_path), flags, 0o600)
        try:
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short outcome journal anchor write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(self._anchor_temp_path), str(self._anchor_path))
        _fsync_directory(self._path.parent)

    def _load_anchor_locked(self) -> dict[str, Any]:
        try:
            size = self._anchor_path.stat().st_size
            if size <= 0 or size > _MAX_ANCHOR_BYTES:
                raise ObsCommandOutcomeCorruption(
                    "OBS_COMMAND_OUTCOME_ANCHOR_SIZE_INVALID"
                )
            raw = self._anchor_path.read_bytes()
        except ObsCommandOutcomeCorruption:
            raise
        except Exception as exc:
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_ANCHOR_UNAVAILABLE"
            ) from exc
        if not raw.endswith(b"\n"):
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_ANCHOR_TRUNCATED")
        try:
            decoded = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_nonfinite,
            )
        except ObsCommandOutcomeCorruption:
            raise
        except Exception as exc:
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_ANCHOR_JSON_INVALID"
            ) from exc
        if not isinstance(decoded, dict) or set(decoded) != _ANCHOR_FIELDS:
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_ANCHOR_SCHEMA_INVALID"
            )
        if decoded.get("schema_version") != _OUTCOME_JOURNAL_ANCHOR_SCHEMA:
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_ANCHOR_SCHEMA_UNSUPPORTED"
            )
        sequence = decoded.get("sequence")
        wal_size = decoded.get("wal_size")
        head_checksum = decoded.get("head_checksum")
        anchor_checksum = decoded.get("anchor_checksum")
        if (
            type(sequence) is not int
            or sequence < 1
            or type(wal_size) is not int
            or wal_size < 1
            or not isinstance(head_checksum, str)
            or not _SHA256_RE.fullmatch(head_checksum)
            or not isinstance(anchor_checksum, str)
            or not _SHA256_RE.fullmatch(anchor_checksum)
        ):
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_ANCHOR_SCHEMA_INVALID"
            )
        unsigned = {
            "schema_version": decoded["schema_version"],
            "sequence": sequence,
            "wal_size": wal_size,
            "head_checksum": head_checksum,
        }
        if anchor_checksum != _checksum(unsigned):
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_ANCHOR_CHECKSUM_INVALID"
            )
        return decoded

    def _validate_anchor_locked(
        self,
        record: ObsCommandOutcomeRecord,
        *,
        wal_size: int,
    ) -> None:
        anchor = self._load_anchor_locked()
        if (
            anchor["sequence"] != record.sequence
            or anchor["head_checksum"] != record.checksum
            or anchor["wal_size"] != wal_size
        ):
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_HISTORY_ROLLBACK_DETECTED"
            )

    def _load_records_locked(self) -> list[ObsCommandOutcomeRecord]:
        # 读取时重验序号、前序校验和、状态机与 anchor。任一不一致都按损坏处理，
        # 因为在副作用幂等问题上“猜一个最近状态”比明确失败更危险。
        wal_exists = self._path.exists()
        anchor_exists = self._anchor_path.exists()
        if not wal_exists and not anchor_exists:
            return []
        if not wal_exists:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_WAL_MISSING")
        if not anchor_exists:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_ANCHOR_MISSING")
        try:
            size = self._path.stat().st_size
            if size > _MAX_WAL_BYTES:
                raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_WAL_TOO_LARGE")
            raw = self._path.read_bytes()
        except ObsCommandOutcomeCorruption:
            raise
        except Exception as exc:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_WAL_UNAVAILABLE") from exc
        if not raw:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_WAL_TRUNCATED")
        if not raw.endswith(b"\n"):
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_WAL_TRUNCATED")
        records: list[ObsCommandOutcomeRecord] = []
        previous = _ZERO_CHECKSUM
        latest_by_lane: dict[tuple[str, str, str], ObsCommandOutcomeRecord] = {}
        for expected_sequence, line in enumerate(raw.splitlines(), start=1):
            if not line or len(line) > _MAX_LINE_BYTES:
                raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_WAL_LINE_INVALID")
            try:
                decoded = json.loads(
                    line.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_object,
                    parse_constant=_reject_nonfinite,
                )
            except ObsCommandOutcomeCorruption:
                raise
            except Exception as exc:
                raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_WAL_JSON_INVALID") from exc
            record = self._record_from_dict(decoded)
            if record.sequence != expected_sequence:
                raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_SEQUENCE_INVALID")
            if record.previous_checksum != previous:
                raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_CHAIN_INVALID")
            if record.checksum != _checksum(record.unsigned_dict()):
                raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_CHECKSUM_INVALID")
            prior = latest_by_lane.get(record.key.lane)
            self._validate_transition(prior, record)
            latest_by_lane[record.key.lane] = record
            records.append(record)
            previous = record.checksum
        self._validate_anchor_locked(records[-1], wal_size=len(raw))
        return records

    @staticmethod
    def _record_from_dict(value: Any) -> ObsCommandOutcomeRecord:
        if not isinstance(value, dict):
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_RECORD_SCHEMA_INVALID")
        schema_version = value.get("schema_version")
        expected_fields = (
            _RECORD_FIELDS_V1
            if schema_version == OUTCOME_JOURNAL_SCHEMA_V1
            else _RECORD_FIELDS
            if schema_version == OUTCOME_JOURNAL_SCHEMA
            else None
        )
        if expected_fields is None:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_SCHEMA_UNSUPPORTED")
        if set(value) != expected_fields:
            raise ObsCommandOutcomeCorruption(
                "OBS_COMMAND_OUTCOME_RECORD_SCHEMA_INVALID"
            )
        try:
            return ObsCommandOutcomeRecord(
                schema_version=schema_version,
                sequence=value["sequence"],
                event_type=OutcomeJournalEvent(value["event_type"]),
                recorded_at_ms=value["recorded_at_ms"],
                receipt_ref=value["receipt_ref"],
                state=ObsCommandOutcomeState(value["state"]),
                side_effect_certainty=SideEffectCertainty(value["side_effect_certainty"]),
                key=ObsCommandOutcomeKey.from_dict(value["key"]),
                reason_code=value["reason_code"],
                claim_ref=value["claim_ref"],
                audit_ref=value["audit_ref"],
                domain_result_ref=value["domain_result_ref"],
                continuation_ref=value.get("continuation_ref"),
                terminal_result_ref=value.get("terminal_result_ref"),
                previous_checksum=value["previous_checksum"],
                checksum=value["checksum"],
            )
        except ObsCommandOutcomeCorruption:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_RECORD_SCHEMA_INVALID") from exc

    @staticmethod
    def _validate_transition(
        prior: ObsCommandOutcomeRecord | None,
        record: ObsCommandOutcomeRecord,
    ) -> None:
        if prior is None:
            if record.event_type not in {
                OutcomeJournalEvent.BEGIN,
                OutcomeJournalEvent.PREAUTH_REJECTION,
                OutcomeJournalEvent.AUTHENTICATED_REJECTION,
            }:
                raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_INITIAL_EVENT_INVALID")
            expected_domain = (
                "preauth"
                if record.event_type is OutcomeJournalEvent.PREAUTH_REJECTION
                else "auth"
            )
            if record.key.digest_domain != expected_domain:
                raise ObsCommandOutcomeCorruption(
                    "OBS_COMMAND_OUTCOME_DIGEST_DOMAIN_INVALID"
                )
            return
        if prior.key != record.key or prior.receipt_ref != record.receipt_ref:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_KEY_MUTATED")
        expected = {
            OutcomeJournalEvent.PROMOTE_SHADOW: ObsCommandOutcomeState.SHADOW_EVALUATED,
            OutcomeJournalEvent.FINALIZE: ObsCommandOutcomeState.EXECUTING,
            OutcomeJournalEvent.RECOVER: ObsCommandOutcomeState.EXECUTING,
            OutcomeJournalEvent.WAIT_REVIEW: ObsCommandOutcomeState.EXECUTING,
            OutcomeJournalEvent.RESOLVE_REVIEW: (
                ObsCommandOutcomeState.WAITING_REVIEW
            ),
        }.get(record.event_type)
        if expected is None or prior.state is not expected:
            raise ObsCommandOutcomeCorruption("OBS_COMMAND_OUTCOME_TRANSITION_INVALID")


__all__ = [
    "OUTCOME_JOURNAL_SCHEMA",
    "OUTCOME_JOURNAL_SCHEMA_V1",
    "ObsCommandOutcomeCorruption",
    "ObsCommandOutcomeJournal",
    "ObsCommandOutcomeJournalError",
    "ObsCommandOutcomeKey",
    "ObsCommandOutcomeRecord",
    "ObsCommandOutcomeState",
    "ObsCommandOutcomeTransitionError",
    "ObsCommandReviewWaitRecovery",
    "OutcomeBeginDisposition",
    "OutcomeBeginResult",
    "OutcomeJournalEvent",
    "SideEffectCertainty",
]
