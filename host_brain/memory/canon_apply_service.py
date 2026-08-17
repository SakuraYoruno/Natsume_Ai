"""WorldCanon 在生产环境中的唯一变更边界。

SQLite 与 canon JSON 文件不能参加同一个原子事务。本服务因此先把严格绑定的
bearer token 销毁并写入 ``PREPARED`` journal，再 fsync/原子替换文件，最后
更新 SQLite 索引。journal 显式记录每个结果不明窗口并支持恢复，但绝不伪称
跨资源原子性；向量投影则在真实 canon 成功后才以 best-effort 方式触发。
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable, Mapping
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.tokens import (
    CANON_OPERATION_APPLY,
    CANON_OPERATION_ROLLBACK,
    CANON_SUBJECT_PENTEST_PROMOTION,
    CANON_SUBJECT_PROPOSAL,
    TOKEN_TYPE_CANON_APPLY,
    TOKEN_TYPE_CANON_ROLLBACK,
    _durable_db_path,
    _exact_sqlite_int,
    canon_evidence_digest,
    canon_token_digest,
    normalize_canon_evidence_refs,
    validate_canon_binding_text,
)
from host_brain.memory.canon_vector_auto_sync import trigger_canon_vector_auto_sync
from host_brain.memory.canon_world_store import (
    WorldCanonStore,
    _CANON_APPLY_PERMIT,
    _normalize_doc_id,
    _normalize_tags,
    compute_world_canon_hash,
)
from host_brain.runtime.degradation_authority import (
    DegradationCapabilityPort,
    evaluate_degradation_capability,
)
from host_brain.persistence.canon_apply_repository import (
    CanonApplyRepository,
    CanonApplyRepositoryError,
    CanonApplyUnitOfWork,
)


JOURNAL_PREPARED = "PREPARED"
JOURNAL_APPLIED = "APPLIED"
JOURNAL_FAILED = "FAILED"
JOURNAL_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"

_SAFE_APPLY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,239}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_APPLIED_LINEAGE_DEPTH = 32
_MAX_APPLIED_LINEAGE_CANDIDATES = 64
_JOURNAL_REQUIRED_INTEGER_FIELDS = (
    "subject_version",
    "base_version",
    "before_version",
    "after_version",
    "fence_version",
    "file_replaced",
    "recovery_not_before_ts_ms",
    "lease_expiry_ts_ms",
    "prepared_ts_ms",
    "updated_ts_ms",
)
_PENTEST_ENTRY_SNAPSHOT_KEYS = frozenset(
    {
        "entry_id",
        "schema_version",
        "knowledge_type",
        "title",
        "summary",
        "payload",
        "source_ref",
        "source_hash",
        "evidence_refs",
        "tags",
        "status",
        "created_ts_ms",
        "updated_ts_ms",
    }
)


class CanonApplyError(RuntimeError):
    """治理 apply 边界的稳定、可机器读取失败，不以底层异常文本作为协议。"""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(f"{self.reason_code}{': ' + self.detail if self.detail else ''}")


def _required_exact_int(value: Any, *, field_name: str) -> int:
    result = _exact_sqlite_int(value, field_name=field_name)
    if result is None:  # pragma: no cover - required helper never permits None
        raise ValueError(f"CANON_APPLY_INTEGER_INVALID:{field_name}")
    return result


def _integrity_int(
    value: Any,
    *,
    field_name: str,
    optional: bool = False,
) -> int | None:
    try:
        return _exact_sqlite_int(
            value,
            field_name=field_name,
            optional=optional,
        )
    except ValueError as exc:
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
            f"{field_name} is not exact INTEGER",
        ) from exc


def _subject_int(value: Any, *, field_name: str) -> int:
    try:
        return _required_exact_int(value, field_name=field_name)
    except ValueError as exc:
        raise CanonApplyError(
            "CANON_APPLY_SUBJECT_INTEGER_INVALID",
            field_name,
        ) from exc


def _reason_int(value: Any, *, field_name: str, reason_code: str) -> int:
    try:
        return _required_exact_int(value, field_name=field_name)
    except ValueError as exc:
        raise CanonApplyError(
            reason_code,
            f"{field_name} is not exact INTEGER",
        ) from exc


def _validated_journal_integers(row: Mapping[str, Any]) -> dict[str, int | None]:
    values: dict[str, int | None] = {
        field_name: _integrity_int(
            row[field_name],
            field_name=f"journal.{field_name}",
        )
        for field_name in _JOURNAL_REQUIRED_INTEGER_FIELDS
    }
    values["completed_ts_ms"] = _integrity_int(
        row["completed_ts_ms"],
        field_name="journal.completed_ts_ms",
        optional=True,
    )
    required = {key: value for key, value in values.items() if key != "completed_ts_ms"}
    if any(value is None for value in required.values()):  # pragma: no cover - helper invariant
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
            "required journal INTEGER missing",
        )
    exact = {key: value for key, value in values.items()}
    if (
        any(
            exact[field_name] is None or exact[field_name] < 0
            for field_name in (
                "subject_version",
                "base_version",
                "before_version",
                "after_version",
                "recovery_not_before_ts_ms",
                "lease_expiry_ts_ms",
                "prepared_ts_ms",
                "updated_ts_ms",
            )
        )
        or exact["fence_version"] is None
        or exact["fence_version"] < 1
        or exact["file_replaced"] not in {0, 1}
        or (exact["completed_ts_ms"] is not None and exact["completed_ts_ms"] < 0)
    ):
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
            "journal INTEGER coordinates invalid",
        )
    return values


class CanonApplyCrash(BaseException):
    """故障注入信号：模拟进程丢失，刻意跳过正常清理以验证恢复窗口。"""


@dataclass(frozen=True, slots=True)
class CanonApplyCommand:
    """内容无关的变更命令；完整授权坐标在进入副作用边界前即冻结。"""

    apply_id: str
    operation: str
    subject_type: str
    subject_id: str
    subject_version: int
    subject_hash: str
    target_doc_id: str
    base_version: int
    approval_token_ref: str = field(repr=False)
    actor_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # 命令在进入副作用边界前冻结全部授权坐标：目标、版本、摘要、身份和证据
        # 都会参与令牌绑定，后续任何替换字段的尝试都应在这里失败。
        apply_id = validate_canon_binding_text("apply_id", self.apply_id)
        operation = str(self.operation or "").strip().upper()
        subject_type = str(self.subject_type or "").strip().upper()
        subject_id = validate_canon_binding_text("subject_id", self.subject_id)
        subject_hash = str(self.subject_hash or "").strip().lower()
        target_doc_id = _normalize_doc_id(
            validate_canon_binding_text("target_doc_id", self.target_doc_id, max_length=160)
        )
        actor_id = validate_canon_binding_text("actor_id", self.actor_id)
        session_id = validate_canon_binding_text("session_id", self.session_id)
        trace_id = validate_canon_binding_text("trace_id", self.trace_id)
        token_ref = str(self.approval_token_ref or "")
        refs = normalize_canon_evidence_refs(self.evidence_refs)

        if not _SAFE_APPLY_ID.fullmatch(apply_id):
            raise ValueError("CANON_APPLY_ID_INVALID")
        if operation not in {CANON_OPERATION_APPLY, CANON_OPERATION_ROLLBACK}:
            raise ValueError("CANON_APPLY_OPERATION_INVALID")
        if subject_type not in {CANON_SUBJECT_PROPOSAL, CANON_SUBJECT_PENTEST_PROMOTION}:
            raise ValueError("CANON_APPLY_SUBJECT_TYPE_INVALID")
        if operation == CANON_OPERATION_ROLLBACK and subject_type != CANON_SUBJECT_PROPOSAL:
            raise ValueError("CANON_APPLY_ROLLBACK_SUBJECT_INVALID")
        if not _HEX_64.fullmatch(subject_hash):
            raise ValueError("CANON_APPLY_SUBJECT_HASH_INVALID")
        subject_version = _required_exact_int(
            self.subject_version,
            field_name="subject_version",
        )
        base_version = _required_exact_int(
            self.base_version,
            field_name="base_version",
        )
        if subject_version < 0 or base_version < 0:
            raise ValueError("CANON_APPLY_VERSION_INVALID")
        if not token_ref or len(token_ref) > 256 or any(ord(ch) < 33 or ord(ch) == 127 for ch in token_ref):
            raise ValueError("CANON_APPLY_TOKEN_MISSING")

        object.__setattr__(self, "apply_id", apply_id)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "subject_type", subject_type)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(self, "subject_version", subject_version)
        object.__setattr__(self, "subject_hash", subject_hash)
        object.__setattr__(self, "target_doc_id", target_doc_id)
        object.__setattr__(self, "base_version", base_version)
        object.__setattr__(self, "approval_token_ref", token_ref)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "trace_id", trace_id)
        object.__setattr__(self, "evidence_refs", refs)

    def binding_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "subject_version": self.subject_version,
            "subject_hash": self.subject_hash,
            "target_doc_id": self.target_doc_id,
            "base_version": self.base_version,
            "actor_id": self.actor_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class CanonApplyResult:
    """一次 apply/recovery 的公开结果，携带版本演进而非批准 bearer。"""
    apply_id: str
    operation: str
    subject_type: str
    subject_id: str
    target_doc_id: str
    state: str
    before_version: int
    after_version: int
    after_hash: str | None
    recovered: bool = False


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def compute_promotion_subject_hash(candidate: Mapping[str, Any]) -> str:
    """哈希完整审核候选，而非仅哈希 Pentest lineage key，防止审批后内容替换。"""

    payload = _parse_json_object(
        candidate["payload_json"], reason_code="CANON_APPLY_PROMOTION_PAYLOAD_INVALID"
    )
    try:
        evidence_refs = json.loads(str(candidate["evidence_refs_json"] or "[]"))
    except json.JSONDecodeError as exc:
        raise CanonApplyError("CANON_APPLY_PROMOTION_PAYLOAD_INVALID", str(exc)) from exc
    if not isinstance(evidence_refs, list):
        raise CanonApplyError("CANON_APPLY_PROMOTION_PAYLOAD_INVALID", "evidence refs must be a list")
    try:
        created_ts_ms = _required_exact_int(
            candidate["created_ts_ms"],
            field_name="promotion.created_ts_ms",
        )
    except (KeyError, ValueError) as exc:
        raise CanonApplyError(
            "CANON_APPLY_PROMOTION_PAYLOAD_INVALID",
            "created_ts_ms is not exact INTEGER",
        ) from exc
    return _sha256_json(
        {
            "candidate_id": str(candidate["candidate_id"]),
            "entry_id": str(candidate["entry_id"]),
            "source_hash": str(candidate["source_hash"]),
            "candidate_hash": str(candidate["candidate_hash"]),
            "payload": payload,
            "evidence_refs": evidence_refs,
            "reason": str(candidate["reason"]),
            "status": str(candidate["status"]),
            "created_ts_ms": created_ts_ms,
        }
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def default_world_canon_root() -> Path:
    return (Path(__file__).resolve().parents[1] / "canon" / "world").resolve()


def resolve_world_canon_root(value: str | Path | None = None) -> Path:
    if value is None or not str(value).strip():
        return default_world_canon_root()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError("CANON_APPLY_WORLD_ROOT_MUST_BE_ABSOLUTE")
    return candidate.resolve()


def _normalize_trusted_approval_keys(
    supplied: Mapping[str, Any] | None,
) -> dict[str, Ed25519PublicKey]:
    source: Mapping[str, Any]
    if supplied is None:
        raw = os.environ.get("NATSUME_CANON_APPROVAL_PUBLIC_KEYS_JSON", "").strip()
        if not raw:
            source = {}
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("CANON_APPLY_APPROVAL_KEYRING_INVALID") from exc
            if not isinstance(parsed, dict):
                raise ValueError("CANON_APPLY_APPROVAL_KEYRING_INVALID")
            source = parsed
    else:
        source = supplied
    normalized: dict[str, Ed25519PublicKey] = {}
    for raw_key_id, raw_key in source.items():
        key_id = validate_canon_binding_text(
            "authority_key_id", str(raw_key_id), max_length=128
        )
        if isinstance(raw_key, Ed25519PublicKey):
            public_key = raw_key
        else:
            try:
                encoded = (
                    base64.b64decode(raw_key, validate=True)
                    if isinstance(raw_key, str)
                    else bytes(raw_key)
                )
                public_key = Ed25519PublicKey.from_public_bytes(encoded)
            except (binascii.Error, TypeError, ValueError) as exc:
                raise ValueError("CANON_APPLY_APPROVAL_KEYRING_INVALID") from exc
        normalized[key_id] = public_key
    return normalized


def _parse_json_object(raw: Any, *, reason_code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise CanonApplyError(reason_code, str(exc)) from exc
    if not isinstance(value, dict):
        raise CanonApplyError(reason_code, "expected JSON object")
    return value


def _parse_tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CanonApplyError("CANON_APPLY_PROPOSAL_PAYLOAD_INVALID", str(exc)) from exc
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise CanonApplyError("CANON_APPLY_PROPOSAL_PAYLOAD_INVALID", "tags must be a list")
    return _normalize_tags([str(value) for value in raw])


_CANON_SNAPSHOT_KEYS = {
    "doc_id",
    "version",
    "status",
    "title",
    "tags",
    "body",
    "updated_by",
    "ts_ms",
    "hash",
}
_CANON_SNAPSHOT_STRING_KEYS = (
    "doc_id",
    "status",
    "title",
    "body",
    "updated_by",
    "hash",
)


def _validated_canon_snapshot(
    raw: Any,
    *,
    target_doc_id: str,
    expected_version: int,
    expected_hash: str | None,
) -> dict[str, Any] | None:
    expected_version_value = _integrity_int(
        expected_version,
        field_name="snapshot.expected_version",
    )
    if raw is None:
        if expected_version_value != 0 or expected_hash is not None:
            raise CanonApplyError("CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "missing snapshot")
        return None
    if not isinstance(raw, str):
        raise CanonApplyError("CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "snapshot is not text")
    payload = _parse_json_object(raw, reason_code="CANON_APPLY_JOURNAL_INTEGRITY_FAILED")
    if set(payload) != _CANON_SNAPSHOT_KEYS or _canonical_json(payload) != raw:
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "snapshot is not canonical"
        )
    try:
        if (
            type(payload["version"]) is not int
            or type(payload["ts_ms"]) is not int
            or any(type(payload[key]) is not str for key in _CANON_SNAPSHOT_STRING_KEYS)
            or type(payload["tags"]) is not list
            or any(type(tag) is not str for tag in payload["tags"])
        ):
            raise ValueError("snapshot field type")
        identity: dict[str, Any] = {
            "doc_id": _normalize_doc_id(payload["doc_id"]),
            "version": payload["version"],
            "status": payload["status"],
            "title": payload["title"],
            "tags": list(payload["tags"]),
            "body": payload["body"],
            "updated_by": payload["updated_by"],
            "ts_ms": payload["ts_ms"],
        }
        if identity["version"] <= 0 or identity["ts_ms"] < 0:
            raise ValueError("snapshot field value")
    except (KeyError, TypeError, ValueError) as exc:
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "snapshot field invalid"
        ) from exc
    computed_hash = compute_world_canon_hash(identity)
    normalized = {**identity, "hash": computed_hash}
    if (
        payload != normalized
        or identity["doc_id"] != target_doc_id
        or identity["version"] != expected_version_value
        or computed_hash != str(expected_hash or "")
    ):
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "snapshot coordinates mismatch"
        )
    return normalized


def _same_complete_payload(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _canonical_json(left) == _canonical_json(right)


class CanonApplyService:
    """带耐久授权和恢复协议的唯一 WorldCanon mutation service。

    service 同时约束 token/journal 数据库、canon 文件根和可选降级 authority 的
    组合关系。它不允许调用者绕过 subject version、base version 或证据绑定去
    直接写文件；失败后的权威入口是 journal recovery，而不是重新签发同一操作。
    """

    def __init__(
        self,
        *,
        world_root: str | Path | None = None,
        ledger: SQLiteLedger | None = None,
        db_path: str | Path | None = None,
        promotion_db_path: str | Path | None = None,
        fault_injector: Callable[[str, str], None] | None = None,
        clock: Callable[[], int] = _now_ms,
        recovery_grace_ms: int = 120_000,
        recovery_lease_ms: int = 120_000,
        trusted_approval_keys: Mapping[str, Any] | None = None,
        repository: CanonApplyRepository | None = None,
        degradation_capability_port: DegradationCapabilityPort | None = None,
    ) -> None:
        selected_db = (
            db_path
            if db_path is not None
            else getattr(ledger, "canonical_db_path", getattr(ledger, "db_path", ""))
        )
        self.db_path = _durable_db_path(selected_db)
        self.promotion_db_path = (
            _durable_db_path(promotion_db_path) if promotion_db_path is not None else self.db_path
        )
        self.world_root = resolve_world_canon_root(world_root)
        self.world_root.mkdir(parents=True, exist_ok=True)
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self.recovery_grace_ms = _required_exact_int(
            recovery_grace_ms,
            field_name="recovery_grace_ms",
        )
        self.recovery_lease_ms = _required_exact_int(
            recovery_lease_ms,
            field_name="recovery_lease_ms",
        )
        if self.recovery_grace_ms <= 0 or self.recovery_lease_ms <= 0:
            raise ValueError("CANON_APPLY_RECOVERY_LEASE_INVALID")
        self.instance_id = f"canon-apply-{uuid.uuid4().hex}"
        self._trusted_approval_keys = _normalize_trusted_approval_keys(
            trusted_approval_keys
        )
        self.repository = repository or CanonApplyRepository(self.db_path)
        self.degradation_capability_port = degradation_capability_port
        if self.repository.db_path != self.db_path:
            raise ValueError("CANON_APPLY_REPOSITORY_DB_MISMATCH")
        self.ledger = ledger or SQLiteLedger(db_path=self.db_path, check_same_thread=False)
        ledger_canonical_path = getattr(self.ledger, "canonical_db_path", self.ledger.db_path)
        if _durable_db_path(ledger_canonical_path) != self.db_path:
            raise ValueError("CANON_APPLY_LEDGER_DB_MISMATCH")
        self.store = WorldCanonStore(world_root=self.world_root, ledger=self.ledger)
        self._fault_injector = fault_injector
        self.repository.assert_ready()

    def _clock_ms(self) -> int:
        try:
            value = _required_exact_int(self._clock(), field_name="clock_ts_ms")
        except ValueError as exc:
            raise CanonApplyError("CANON_APPLY_CLOCK_INVALID") from exc
        if value < 0:
            raise CanonApplyError("CANON_APPLY_CLOCK_INVALID")
        return value

    def apply(self, command: CanonApplyCommand) -> CanonApplyResult:
        """按 prepare→原子文件替换→finalize 的顺序执行一次受控 canon 变更。

    降级 authority 在任何时钟、token 消耗或文件写入前拒绝不可用能力。文件
    替换之后即使 SQLite finalize 失败，也只记录可恢复 journal 状态；不能回滚
    猜测文件或重放 token。成功后才异步触发向量索引，索引失败不改变 SSOT 成功。
    """
        if not isinstance(command, CanonApplyCommand):
            raise TypeError("command must be CanonApplyCommand")
        degradation = evaluate_degradation_capability(
            self.degradation_capability_port,
            capability_id="canon_apply",
            static_risk_level=5,
        )
        if not bool(degradation.get("allowed")):
            # Preserve Canon's stable machine-readable boundary contract while
            # ensuring no clock, journal, token burn, or file mutation happens.
            raise CanonApplyError(
                str(
                    degradation.get("reason_code")
                    or "DEGRADATION_STATE_UNAVAILABLE"
                ),
                str(
                    degradation.get("detail")
                    or "degradation authority blocked Canon apply"
                ),
            )
        # 顺序不可交换：先持久化 PREPARED 与令牌消耗，再原子替换文件，最后提交
        # SQLite 投影。跨资源没有伪原子事务，journal 负责暴露并恢复中间窗口。
        now_value = self._clock_ms()
        prepared = self._prepare(command, now_value)
        self._fault("after_prepare", command.apply_id)
        try:
            self._renew_lease(command.apply_id, prepared["fence_version"])
            self._write_target(prepared)
            self._mark_file_replaced(
                command.apply_id, prepared["fence_version"], self._clock_ms()
            )
            self._fault("after_file_replace", command.apply_id)
            self._fault("before_finalize", command.apply_id)
            result = self._finalize(
                command.apply_id, prepared["fence_version"], self._clock_ms()
            )
        except CanonApplyCrash:
            raise
        except Exception as exc:
            state = self._classify_failure(prepared)
            self._record_failure(
                command.apply_id,
                prepared["fence_version"],
                state=state,
                reason_code=getattr(exc, "reason_code", "CANON_APPLY_EXECUTION_FAILED"),
                detail=str(exc),
                now_ts_ms=self._clock_ms(),
            )
            if isinstance(exc, CanonApplyError):
                raise
            raise CanonApplyError("CANON_APPLY_EXECUTION_FAILED", str(exc)) from exc
        self._sync_vectors(result)
        return result

    def recover_pending(self, *, limit: int = 100) -> list[CanonApplyResult]:
        """取得带 fence 租约的可恢复 journal，并只推进可精确判定的文件状态。

    多进程竞争时仅当前 fence holder 能检查/替换/最终提交同一 apply；遇到文件
    内容既非旧快照也非目标快照则记录 recovery required，避免覆盖人工修复或
    并发写入。恢复成功同样只在 finalize 后触发可选向量投影。
    """
        now_value = self._clock_ms()
        apply_ids = self.repository.list_recoverable_apply_ids(
            now_ms=now_value,
            limit=max(
                1,
                min(_required_exact_int(limit, field_name="recovery_limit"), 1000),
            ),
        )
        results: list[CanonApplyResult] = []
        for apply_id in apply_ids:
            # 恢复先争抢带 fence 的租约；只有持有当前 fence 的实例能检查文件状态、
            # 重放缺失替换或最终提交，防止多个进程同时把同一 journal 推进两次。
            prepared = self._claim_recovery(apply_id, now_value)
            if prepared is None:
                continue
            try:
                self._renew_lease(apply_id, prepared["fence_version"])
                disposition = self._file_disposition(prepared)
                if disposition == "BEFORE":
                    self._write_target(prepared)
                    self._mark_file_replaced(
                        apply_id, prepared["fence_version"], self._clock_ms()
                    )
                elif disposition != "TARGET":
                    raise CanonApplyError("CANON_APPLY_RECOVERY_DRIFT", disposition)
                result = self._finalize(
                    apply_id,
                    prepared["fence_version"],
                    self._clock_ms(),
                    recovered=True,
                )
            except Exception as exc:
                self._record_failure(
                    apply_id,
                    prepared["fence_version"],
                    state=JOURNAL_RECOVERY_REQUIRED,
                    reason_code=getattr(exc, "reason_code", "CANON_APPLY_RECOVERY_FAILED"),
                    detail=str(exc),
                    now_ts_ms=self._clock_ms(),
                )
                continue
            self._sync_vectors(result)
            results.append(result)
        return results

    def get_journal(self, apply_id: str) -> dict[str, Any] | None:
        return self.repository.get_journal(apply_id)

    def get_promotion_subject_hash(self, candidate_id: str) -> str:
        """返回签发 token 前必须绑定的完整候选哈希，拒绝跨数据库的伪原子提升。"""

        if self.promotion_db_path != self.db_path:
            raise CanonApplyError("CANON_APPLY_CROSS_DB_PROMOTION_UNSUPPORTED")
        normalized_id = validate_canon_binding_text("subject_id", candidate_id)
        row = self.repository.get_promotion_candidate(normalized_id)
        if row is None:
            raise CanonApplyError("CANON_APPLY_SUBJECT_NOT_FOUND")
        if str(row["status"]) != "PENDING_REVIEW":
            raise CanonApplyError("CANON_APPLY_SUBJECT_STATUS_INVALID")
        return compute_promotion_subject_hash(row)

    def _fault(self, stage: str, apply_id: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage, apply_id)

    def _prepare(self, command: CanonApplyCommand, now_value: int) -> dict[str, Any]:
        if (
            command.subject_type == CANON_SUBJECT_PENTEST_PROMOTION
            and self.promotion_db_path != self.db_path
        ):
            raise CanonApplyError(
                "CANON_APPLY_CROSS_DB_PROMOTION_UNSUPPORTED",
                "promotion candidate and Canon journal must share one SQLite transaction",
            )
        token_digest = canon_token_digest(command.approval_token_ref)
        # journal 只保存令牌/证据摘要而非 bearer 原文；摘要仍与完整 command 哈希
        # 绑定，使重放、换目标或删改证据都无法复用一次授权。
        evidence_digest = canon_evidence_digest(command.evidence_refs)
        command_hash = _sha256_json(
            {
                "apply_id": command.apply_id,
                **command.binding_dict(),
                "token_digest": token_digest,
            }
        )
        try:
            with self.repository.transaction() as uow:
                token = uow.get_token(token_digest)
                self._validate_token(token, command, evidence_digest, now_value)
                existing = uow.get_journal(command.apply_id)
                if existing is not None:
                    raise CanonApplyError("CANON_APPLY_REPLAY")
                active_target = uow.find_active_target(command.target_doc_id)
                if active_target is not None:
                    raise CanonApplyError("CANON_APPLY_TARGET_BUSY")
                prepared = self._load_subject(uow, command, now_value)

                if not uow.cas_subject_status(
                    table=prepared["subject_table"],
                    subject_id=command.subject_id,
                    expected_status=prepared["subject_previous_status"],
                    new_status=prepared["subject_prepared_status"],
                ):
                    raise CanonApplyError("CANON_APPLY_SUBJECT_CAS_FAILED")

                expected_type = (
                    TOKEN_TYPE_CANON_APPLY
                    if command.operation == CANON_OPERATION_APPLY
                    else TOKEN_TYPE_CANON_ROLLBACK
                )
                if not uow.burn_token(
                    {
                        "apply_id": command.apply_id,
                        "burned_ts_ms": now_value,
                        "token_digest": token_digest,
                        "token_type": expected_type,
                        "operation": command.operation,
                        "subject_type": command.subject_type,
                        "subject_id": command.subject_id,
                        "subject_version": command.subject_version,
                        "subject_hash": command.subject_hash,
                        "target_doc_id": command.target_doc_id,
                        "base_version": command.base_version,
                        "actor_id": command.actor_id,
                        "session_id": command.session_id,
                        "trace_id": command.trace_id,
                        "evidence_digest": evidence_digest,
                        "now_ts_ms": now_value,
                    }
                ):
                    raise CanonApplyError("CANON_APPLY_TOKEN_BURN_CAS_FAILED")

                uow.insert_journal(
                    {
                        "apply_id": command.apply_id,
                        "command_hash": command_hash,
                        "operation": command.operation,
                        "subject_type": command.subject_type,
                        "subject_id": command.subject_id,
                        "subject_version": command.subject_version,
                        "subject_hash": command.subject_hash,
                        "target_doc_id": command.target_doc_id,
                        "base_version": command.base_version,
                        "token_digest": token_digest,
                        "actor_id": command.actor_id,
                        "session_id": command.session_id,
                        "trace_id": command.trace_id,
                        "evidence_refs_json": _canonical_json(list(command.evidence_refs)),
                        "evidence_digest": evidence_digest,
                        "authority_key_id": token["authority_key_id"],
                        "approval_attestation_digest": token[
                            "approval_attestation_digest"
                        ],
                        "subject_previous_status": prepared["subject_previous_status"],
                        "subject_prepared_status": prepared["subject_prepared_status"],
                        "subject_final_status": prepared["subject_final_status"],
                        "before_payload_json": _canonical_json(prepared["before_payload"])
                        if prepared["before_payload"] is not None
                        else None,
                        "target_payload_json": _canonical_json(prepared["target_payload"])
                        if prepared["target_payload"] is not None
                        else None,
                        "before_version": prepared["before_version"],
                        "before_hash": prepared["before_hash"],
                        "after_version": prepared["after_version"],
                        "after_hash": prepared["after_hash"],
                        "recovery_not_before_ts_ms": now_value + self.recovery_grace_ms,
                        "lease_owner": self.instance_id,
                        "lease_expiry_ts_ms": now_value + self.recovery_lease_ms,
                        "prepared_ts_ms": now_value,
                        "updated_ts_ms": now_value,
                    }
                )
        except CanonApplyRepositoryError as exc:
            raise CanonApplyError(exc.reason_code, exc.detail) from exc
        return {
            **prepared,
            "apply_id": command.apply_id,
            "target_doc_id": command.target_doc_id,
            "fence_version": 1,
        }

    def _validate_token(
        self,
        token: Mapping[str, Any] | None,
        command: CanonApplyCommand,
        evidence_digest: str,
        now_value: int,
    ) -> None:
        if token is None:
            raise CanonApplyError("CANON_APPLY_TOKEN_INVALID", "not found")
        # Verify the signed envelope and every immutable token coordinate before
        # consulting replay/expiry state.  Otherwise a forged signature can be
        # used as an oracle for durable token state or command bindings.
        attestation = self._verify_approval_attestation(
            token,
            expected_binding=None,
        )
        if attestation["binding"] != command.binding_dict():
            raise CanonApplyError("CANON_APPLY_TOKEN_BINDING_MISMATCH", "command")
        expected_type = (
            TOKEN_TYPE_CANON_APPLY
            if command.operation == CANON_OPERATION_APPLY
            else TOKEN_TYPE_CANON_ROLLBACK
        )
        if str(token["token_type"]) != expected_type:
            raise CanonApplyError("CANON_APPLY_TOKEN_BINDING_MISMATCH", "token_type")
        issued = _required_exact_int(
            attestation["token_issued_ts_ms"],
            field_name="attestation.token_issued_ts_ms",
        )
        expiry = _required_exact_int(
            attestation["token_expiry_ts_ms"],
            field_name="attestation.token_expiry_ts_ms",
        )
        if issued > now_value:
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_FROM_FUTURE")
        if expiry <= now_value:
            raise CanonApplyError("CANON_APPLY_TOKEN_EXPIRED")
        if str(token["state"]) != "AVAILABLE":
            raise CanonApplyError("CANON_APPLY_TOKEN_REPLAY")
        if token["burned_ts_ms"] is not None:
            raise CanonApplyError("CANON_APPLY_TOKEN_INVALID", "available token is burned")
        if str(token["evidence_digest"]) != evidence_digest:
            raise CanonApplyError("CANON_APPLY_TOKEN_BINDING_MISMATCH", "evidence_digest")

    def _validate_approval_attestation(
        self,
        token: Mapping[str, Any],
        command: CanonApplyCommand,
        now_value: int,
    ) -> None:
        attestation = self._verify_approval_attestation(
            token,
            expected_binding=command.binding_dict(),
        )
        issued = _required_exact_int(
            attestation["token_issued_ts_ms"],
            field_name="attestation.token_issued_ts_ms",
        )
        expiry = _required_exact_int(
            attestation["token_expiry_ts_ms"],
            field_name="attestation.token_expiry_ts_ms",
        )
        if issued > now_value:
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_FROM_FUTURE")
        if expiry <= now_value:
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_EXPIRED")

    def _verify_approval_attestation(
        self,
        token: Mapping[str, Any],
        *,
        expected_binding: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        key_id = str(token["authority_key_id"] or "")
        public_key = self._trusted_approval_keys.get(key_id)
        if public_key is None:
            reason = (
                "CANON_APPLY_APPROVAL_AUTHORITY_UNCONFIGURED"
                if not self._trusted_approval_keys
                else "CANON_APPLY_APPROVAL_AUTHORITY_UNTRUSTED"
            )
            raise CanonApplyError(reason)
        raw = str(token["approval_attestation_json"] or "")
        try:
            attestation = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_INVALID") from exc
        if not isinstance(attestation, dict) or _canonical_json(attestation) != raw:
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_INVALID")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if digest != str(token["approval_attestation_digest"] or ""):
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_DIGEST_MISMATCH")
        try:
            signature = base64.b64decode(
                str(token["approval_signature"] or ""), validate=True
            )
            public_key.verify(signature, raw.encode("utf-8"))
        except (binascii.Error, TypeError, ValueError, InvalidSignature) as exc:
            raise CanonApplyError("CANON_APPLY_APPROVAL_SIGNATURE_INVALID") from exc
        expected_attestation_keys = {
            "schema_version",
            "authority_key_id",
            "reviewer_id",
            "reviewer_role",
            "decision",
            "approval_nonce",
            "token_digest",
            "token_type",
            "token_issued_ts_ms",
            "token_expiry_ts_ms",
            "binding",
        }
        binding = attestation.get("binding")
        expected_binding_keys = {
            "operation",
            "subject_type",
            "subject_id",
            "subject_version",
            "subject_hash",
            "target_doc_id",
            "base_version",
            "actor_id",
            "session_id",
            "trace_id",
            "evidence_refs",
        }
        if (
            set(attestation) != expected_attestation_keys
            or not isinstance(binding, dict)
            or set(binding) != expected_binding_keys
        ):
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_BINDING_MISMATCH")
        try:
            evidence_refs = normalize_canon_evidence_refs(binding["evidence_refs"])
            issued = _required_exact_int(
                attestation["token_issued_ts_ms"],
                field_name="attestation.token_issued_ts_ms",
            )
            expiry = _required_exact_int(
                attestation["token_expiry_ts_ms"],
                field_name="attestation.token_expiry_ts_ms",
            )
            binding_subject_version = _required_exact_int(
                binding["subject_version"],
                field_name="attestation.binding.subject_version",
            )
            binding_base_version = _required_exact_int(
                binding["base_version"],
                field_name="attestation.binding.base_version",
            )
            token_subject_version = _required_exact_int(
                token["subject_version"],
                field_name="token.subject_version",
            )
            token_base_version = _required_exact_int(
                token["base_version"],
                field_name="token.base_version",
            )
            token_issued_ts_ms = _required_exact_int(
                token["issued_ts_ms"],
                field_name="token.issued_ts_ms",
            )
            token_expiry_ts_ms = _required_exact_int(
                token["expiry_ts_ms"],
                field_name="token.expiry_ts_ms",
            )
            _exact_sqlite_int(
                token["burned_ts_ms"],
                field_name="token.burned_ts_ms",
                optional=True,
            )
            token_binding = {
                "operation": str(token["operation"]),
                "subject_type": str(token["subject_type"]),
                "subject_id": str(token["subject_id"]),
                "subject_version": token_subject_version,
                "subject_hash": str(token["subject_hash"]),
                "target_doc_id": str(token["target_doc_id"]),
                "base_version": token_base_version,
                "actor_id": str(token["actor_id"]),
                "session_id": str(token["session_id"]),
                "trace_id": str(token["trace_id"]),
                "evidence_refs": list(evidence_refs),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise CanonApplyError(
                "CANON_APPLY_APPROVAL_ATTESTATION_BINDING_MISMATCH"
            ) from exc
        if (
            attestation.get("schema_version")
            != "Natsume.canon-approval-attestation.v1"
            or attestation.get("authority_key_id") != key_id
            or not str(attestation.get("reviewer_id") or "").strip()
            or attestation.get("decision") != "APPROVE"
            or attestation.get("reviewer_role") not in {"MASTER", "GOVERNOR"}
            or attestation.get("approval_nonce") != token["approval_nonce"]
            or attestation.get("token_digest") != token["token_digest"]
            or attestation.get("token_type") != token["token_type"]
            or issued != token_issued_ts_ms
            or expiry != token_expiry_ts_ms
            or expiry <= issued
            or binding_subject_version != token_subject_version
            or binding_base_version != token_base_version
            or binding != token_binding
            or canon_evidence_digest(evidence_refs) != str(token["evidence_digest"])
            or (expected_binding is not None and binding != dict(expected_binding))
        ):
            raise CanonApplyError("CANON_APPLY_APPROVAL_ATTESTATION_BINDING_MISMATCH")
        return attestation

    def _load_subject(
        self,
        uow: CanonApplyUnitOfWork,
        command: CanonApplyCommand,
        now_value: int,
    ) -> dict[str, Any]:
        try:
            before_payload = self.store._load_apply_snapshot(command.target_doc_id)
        except Exception as exc:
            raise CanonApplyError(
                "CANON_APPLY_EXISTING_PAYLOAD_INVALID",
                "strict Canon snapshot required",
            ) from exc
        before_version = before_payload["version"] if before_payload is not None else 0
        if before_version != command.base_version:
            raise CanonApplyError(
                "CANON_APPLY_BASE_VERSION_DRIFT",
                f"expected={command.base_version},actual={before_version}",
            )

        if command.operation == CANON_OPERATION_ROLLBACK:
            return self._load_rollback(uow, command, before_payload)
        if command.subject_type == CANON_SUBJECT_PROPOSAL:
            return self._load_proposal(uow, command, before_payload)
        return self._load_promotion(uow, command, before_payload, now_value)

    def _load_proposal(
        self,
        uow: CanonApplyUnitOfWork,
        command: CanonApplyCommand,
        before_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        row = uow.get_proposal(command.subject_id)
        if row is None:
            raise CanonApplyError("CANON_APPLY_SUBJECT_NOT_FOUND")
        if str(row["status"]).lower() != "pending":
            raise CanonApplyError("CANON_APPLY_SUBJECT_STATUS_INVALID", str(row["status"]))
        if str(row["canon_kind"]) != "world":
            raise CanonApplyError("CANON_APPLY_PROPOSAL_DOMAIN_MISMATCH")
        title = str(row["proposed_title"] or "").strip()
        body = str(row["proposed_body"] or "").strip()
        if not title or not body:
            raise CanonApplyError("CANON_APPLY_PROPOSAL_PAYLOAD_MISSING")
        target_id = _normalize_doc_id(str(row["target_id"] or ""))
        base_version = _subject_int(
            row["base_version"],
            field_name="proposal.base_version",
        )
        proposed_version = _subject_int(
            row["proposed_version"],
            field_name="proposal.proposed_version",
        )
        proposal_ts_ms = _subject_int(
            row["ts_ms"],
            field_name="proposal.ts_ms",
        )
        payload: dict[str, Any] = {
            "doc_id": target_id,
            "version": proposed_version,
            "status": "active",
            "title": title,
            "tags": _parse_tags(row["proposed_tags"]),
            "body": body,
            "updated_by": command.subject_id,
            "ts_ms": proposal_ts_ms,
        }
        payload["hash"] = compute_world_canon_hash(payload)
        if payload["hash"] != str(row["proposed_hash"] or "").lower():
            raise CanonApplyError("CANON_APPLY_PROPOSAL_HASH_DRIFT")
        if (
            target_id != command.target_doc_id
            or base_version != command.base_version
            or proposed_version != command.subject_version
            or payload["hash"] != command.subject_hash
        ):
            raise CanonApplyError("CANON_APPLY_SUBJECT_BINDING_MISMATCH")
        return {
            "subject_table": "canon_proposal_log",
            "subject_key": "proposal_id",
            "subject_previous_status": str(row["status"]),
            "subject_prepared_status": "APPLY_PREPARED",
            "subject_final_status": "APPLIED",
            "before_payload": before_payload,
            "target_payload": payload,
            "before_version": command.base_version,
            "before_hash": before_payload["hash"] if before_payload else None,
            "after_version": proposed_version,
            "after_hash": payload["hash"],
        }

    def _load_promotion(
        self,
        uow: CanonApplyUnitOfWork,
        command: CanonApplyCommand,
        before_payload: dict[str, Any] | None,
        now_value: int,
    ) -> dict[str, Any]:
        row = uow.get_promotion_candidate(command.subject_id)
        if row is None:
            raise CanonApplyError("CANON_APPLY_SUBJECT_NOT_FOUND")
        if str(row["status"]) != "PENDING_REVIEW":
            raise CanonApplyError("CANON_APPLY_SUBJECT_STATUS_INVALID", str(row["status"]))
        authorization_hash = compute_promotion_subject_hash(row)
        if command.subject_version != 1 or command.subject_hash != authorization_hash:
            raise CanonApplyError("CANON_APPLY_SUBJECT_BINDING_MISMATCH")
        payload, evidence_refs = self._validate_promotion_lineage(
            uow,
            row,
            reason_code="CANON_APPLY_PROMOTION_SOURCE_DRIFT",
        )
        candidate_hash = _sha256_json(
            {
                "entry_id": str(row["entry_id"]),
                "source_hash": str(row["source_hash"]),
                "evidence_refs": evidence_refs,
                "reason": str(row["reason"]),
            }
        )
        if (
            candidate_hash != str(row["candidate_hash"])
            or str(payload.get("source_hash") or "") != str(row["source_hash"])
        ):
            raise CanonApplyError("CANON_APPLY_PROMOTION_SOURCE_DRIFT")
        target = self._promotion_target_payload(
            row,
            target_doc_id=command.target_doc_id,
            base_version=command.base_version,
        )
        return {
            "subject_table": "pentest_knowledge_promotion_candidate",
            "subject_key": "candidate_id",
            "subject_previous_status": "PENDING_REVIEW",
            "subject_prepared_status": "APPROVED",
            "subject_final_status": "APPLIED",
            "before_payload": before_payload,
            "target_payload": target,
            "before_version": command.base_version,
            "before_hash": before_payload["hash"] if before_payload else None,
            "after_version": target["version"],
            "after_hash": target["hash"],
        }

    def _load_rollback(
        self,
        uow: CanonApplyUnitOfWork,
        command: CanonApplyCommand,
        before_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if before_payload is None:
            raise CanonApplyError("CANON_APPLY_ROLLBACK_TARGET_MISSING")
        if (
            before_payload["version"] != command.subject_version
            or str(before_payload["hash"]) != command.subject_hash
        ):
            raise CanonApplyError("CANON_APPLY_SUBJECT_BINDING_MISMATCH")
        proposal = uow.get_proposal(command.subject_id)
        if proposal is None:
            raise CanonApplyError("CANON_APPLY_SUBJECT_NOT_FOUND")
        if str(proposal["status"]) != "APPLIED":
            raise CanonApplyError("CANON_APPLY_SUBJECT_STATUS_INVALID", str(proposal["status"]))
        applied = uow.latest_applied_subject(command.subject_id)
        if applied is None:
            raise CanonApplyError("CANON_APPLY_ROLLBACK_SNAPSHOT_MISSING")
        source_context = self._validated_historical_apply_chain(uow, applied)
        if (
            source_context["after_version"] != before_payload["version"]
            or str(source_context["after_hash"] or "") != str(before_payload["hash"])
            or _normalize_doc_id(str(proposal["target_id"] or "")) != command.target_doc_id
            or not _same_complete_payload(source_context["target_payload"], before_payload)
        ):
            raise CanonApplyError("CANON_APPLY_ROLLBACK_LINEAGE_DRIFT")
        target_payload = source_context["before_payload"]
        return {
            "subject_table": "canon_proposal_log",
            "subject_key": "proposal_id",
            "subject_previous_status": "APPLIED",
            "subject_prepared_status": "ROLLBACK_PREPARED",
            "subject_final_status": "ROLLED_BACK",
            "before_payload": before_payload,
            "target_payload": target_payload,
            "before_version": before_payload["version"],
            "before_hash": str(before_payload["hash"]),
            "after_version": source_context["before_version"],
            "after_hash": source_context["before_hash"],
        }

    def _write_target(self, prepared: Mapping[str, Any]) -> None:
        apply_id = str(prepared["apply_id"])
        fence = _integrity_int(
            prepared["fence_version"],
            field_name="prepared.fence_version",
        )
        if fence is None:  # pragma: no cover - required helper rejects None
            raise CanonApplyError("CANON_APPLY_JOURNAL_INTEGRITY_FAILED")
        with self.repository.transaction() as uow:
                row = uow.get_journal(apply_id)
                row_fence = (
                    _integrity_int(
                        row["fence_version"],
                        field_name="journal.fence_version",
                    )
                    if row is not None
                    else None
                )
                if (
                    row is None
                    or row_fence != fence
                    or str(row["lease_owner"] or "") != self.instance_id
                    or str(row["state"])
                    not in {JOURNAL_PREPARED, JOURNAL_RECOVERY_REQUIRED}
                ):
                    raise CanonApplyError("CANON_APPLY_STALE_FENCE")
                # 文件替换前重新从 journal 校验准备上下文；内存中的 prepared 不能
                # 被信任为最新状态，避免租约过期或记录漂移时写入错误版本。
                validated = self._journal_context(row, uow=uow)
                if not self._same_prepared_context(prepared, validated):
                    raise CanonApplyError(
                        "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "prepared context drift"
                    )
                target = validated["target_payload"]
                if target is None:
                    self.store._delete_authorized_document(
                        validated["target_doc_id"],
                        expected_version=validated["before_version"],
                        expected_hash=validated["before_hash"],
                        apply_id=apply_id,
                        permit=_CANON_APPLY_PERMIT,
                    )
                else:
                    self.store._replace_authorized_payload(
                        dict(target),
                        expected_version=validated["before_version"],
                        expected_hash=validated["before_hash"],
                        apply_id=apply_id,
                        permit=_CANON_APPLY_PERMIT,
                    )

    @staticmethod
    def _same_prepared_context(
        left: Mapping[str, Any], right: Mapping[str, Any]
    ) -> bool:
        return (
            str(left["apply_id"]) == str(right["apply_id"])
            and str(left["target_doc_id"]) == str(right["target_doc_id"])
            and _integrity_int(
                left["before_version"], field_name="prepared.before_version"
            )
            == _integrity_int(
                right["before_version"], field_name="journal.before_version"
            )
            and (str(left["before_hash"]) if left["before_hash"] else None)
            == (str(right["before_hash"]) if right["before_hash"] else None)
            and _integrity_int(
                left["after_version"], field_name="prepared.after_version"
            )
            == _integrity_int(
                right["after_version"], field_name="journal.after_version"
            )
            and (str(left["after_hash"]) if left["after_hash"] else None)
            == (str(right["after_hash"]) if right["after_hash"] else None)
            and _same_complete_payload(left["before_payload"], right["before_payload"])
            and _same_complete_payload(left["target_payload"], right["target_payload"])
        )

    def _mark_file_replaced(self, apply_id: str, fence: int, now_value: int) -> None:
        fence = _required_exact_int(fence, field_name="fence_version")
        now_value = _required_exact_int(now_value, field_name="updated_ts_ms")
        if not self.repository.mark_file_replaced(
            apply_id=apply_id,
            fence=fence,
            lease_owner=self.instance_id,
            now_ms=now_value,
        ):
            raise CanonApplyError("CANON_APPLY_STALE_FENCE")

    def _renew_lease(self, apply_id: str, fence: int) -> None:
        fence = _required_exact_int(fence, field_name="fence_version")
        now_value = self._clock_ms()
        if not self.repository.renew_lease(
            apply_id=apply_id,
            fence=fence,
            lease_owner=self.instance_id,
            lease_expiry_ms=now_value + self.recovery_lease_ms,
            now_ms=now_value,
        ):
            raise CanonApplyError("CANON_APPLY_STALE_FENCE")

    def _finalize(
        self,
        apply_id: str,
        fence: int,
        now_value: int,
        *,
        recovered: bool = False,
    ) -> CanonApplyResult:
        fence = _required_exact_int(fence, field_name="fence_version")
        now_value = _required_exact_int(now_value, field_name="completed_ts_ms")
        with self.repository.transaction() as uow:
                row = uow.get_journal(apply_id)
                row_fence = (
                    _integrity_int(
                        row["fence_version"],
                        field_name="journal.fence_version",
                    )
                    if row is not None
                    else None
                )
                if row is None or row_fence != fence:
                    raise CanonApplyError("CANON_APPLY_STALE_FENCE")
                if str(row["lease_owner"] or "") != self.instance_id:
                    raise CanonApplyError("CANON_APPLY_STALE_FENCE")
                if str(row["state"]) not in {JOURNAL_PREPARED, JOURNAL_RECOVERY_REQUIRED}:
                    raise CanonApplyError("CANON_APPLY_FINALIZE_STATE_INVALID", str(row["state"]))
                prepared = self._journal_context(row, uow=uow)
                if self._file_disposition(prepared) != "TARGET":
                    raise CanonApplyError("CANON_APPLY_FINALIZE_FILE_MISMATCH")
                # 最终状态推进以“文件已是目标内容”为前置条件；若进程在替换前崩溃
                # 就保留可恢复 journal，而不会把数据库投影伪造成已经落盘。
                if not uow.cas_subject_status(
                    table=prepared["subject_table"],
                    subject_id=str(row["subject_id"]),
                    expected_status=str(row["subject_prepared_status"]),
                    new_status=str(row["subject_final_status"]),
                ):
                    raise CanonApplyError("CANON_APPLY_SUBJECT_FINALIZE_CAS_FAILED")
                change_id = f"canon_apply::{apply_id}"
                source_path = str(self.store._doc_path(str(row["target_doc_id"])))
                uow.insert_change_log(
                    {
                        "change_id": change_id,
                        "proposal_id": row["subject_id"],
                        "target_id": row["target_doc_id"],
                        "before_version": prepared["before_version"] or None,
                        "after_version": prepared["after_version"],
                        "before_hash": row["before_hash"],
                        "after_hash": row["after_hash"],
                        "before_body": prepared["before_payload"].get("body")
                        if prepared["before_payload"]
                        else None,
                        "after_body": prepared["target_payload"].get("body")
                        if prepared["target_payload"]
                        else None,
                        "source_path": source_path,
                        "ts_ms": now_value,
                    }
                )
                uow.project_world_document(
                    prepared["target_payload"],
                    doc_id=str(row["target_doc_id"]),
                    source_path=source_path,
                    now_ms=now_value,
                )
                if not uow.finalize_journal(
                    apply_id=apply_id,
                    fence=fence,
                    lease_owner=self.instance_id,
                    now_ms=now_value,
                ):
                    raise CanonApplyError("CANON_APPLY_STALE_FENCE")
        return CanonApplyResult(
            apply_id=apply_id,
            operation=str(row["operation"]),
            subject_type=str(row["subject_type"]),
            subject_id=str(row["subject_id"]),
            target_doc_id=str(row["target_doc_id"]),
            state=JOURNAL_APPLIED,
            before_version=prepared["before_version"],
            after_version=prepared["after_version"],
            after_hash=str(row["after_hash"]) if row["after_hash"] else None,
            recovered=recovered,
        )

    def _record_failure(
        self,
        apply_id: str,
        fence: int,
        *,
        state: str,
        reason_code: str,
        detail: str,
        now_ts_ms: int,
    ) -> None:
        if state not in {JOURNAL_FAILED, JOURNAL_RECOVERY_REQUIRED}:
            raise ValueError("invalid failure state")
        fence = _required_exact_int(fence, field_name="fence_version")
        now_ts_ms = _required_exact_int(now_ts_ms, field_name="updated_ts_ms")
        with self.repository.transaction() as uow:
                row = uow.get_journal(apply_id)
                row_fence = (
                    _integrity_int(
                        row["fence_version"],
                        field_name="journal.fence_version",
                    )
                    if row is not None
                    else None
                )
                if row is None or row_fence != fence:
                    return
                changed = uow.record_failure(
                    apply_id=apply_id,
                    fence=fence,
                    lease_owner=self.instance_id,
                    state=state,
                    reason_code=str(reason_code),
                    detail=str(detail),
                    now_ms=now_ts_ms,
                )
                if changed and state == JOURNAL_FAILED:
                    subject = self._subject_coordinates(row)
                    uow.cas_subject_status(
                        table=subject[0],
                        subject_id=str(row["subject_id"]),
                        expected_status=str(row["subject_prepared_status"]),
                        new_status=str(row["subject_previous_status"]),
                    )

    def _classify_failure(self, prepared: Mapping[str, Any]) -> str:
        disposition = self._file_disposition(prepared)
        return JOURNAL_FAILED if disposition == "BEFORE" else JOURNAL_RECOVERY_REQUIRED

    def _file_disposition(self, prepared: Mapping[str, Any]) -> str:
        try:
            current_payload = self.store._load_apply_snapshot(
                str(prepared["target_doc_id"])
            )
        except Exception:
            return "CORRUPT"
        target = prepared["target_payload"]
        before = prepared["before_payload"]
        if _same_complete_payload(current_payload, target):
            return "TARGET"
        if _same_complete_payload(current_payload, before):
            return "BEFORE"
        return "DRIFT"

    def _claim_recovery(self, apply_id: str, now_value: int) -> dict[str, Any] | None:
        now_value = _required_exact_int(now_value, field_name="recovery_now_ts_ms")
        with self.repository.transaction() as uow:
                row = uow.get_journal(apply_id)
                if row is None or str(row["state"]) not in {
                    JOURNAL_PREPARED,
                    JOURNAL_RECOVERY_REQUIRED,
                }:
                    return None
                try:
                    recovery_not_before_ts_ms = _integrity_int(
                        row["recovery_not_before_ts_ms"],
                        field_name="journal.recovery_not_before_ts_ms",
                    )
                    lease_expiry_ts_ms = _integrity_int(
                        row["lease_expiry_ts_ms"],
                        field_name="journal.lease_expiry_ts_ms",
                    )
                    old_fence = _integrity_int(
                        row["fence_version"],
                        field_name="journal.fence_version",
                    )
                    if (
                        recovery_not_before_ts_ms is None
                        or lease_expiry_ts_ms is None
                        or old_fence is None
                    ):  # pragma: no cover - required helpers reject None
                        raise CanonApplyError("CANON_APPLY_JOURNAL_INTEGRITY_FAILED")
                except CanonApplyError as exc:
                    uow.quarantine_recovery(
                        apply_id=apply_id,
                        detail=str(exc),
                        recovery_not_before_ms=now_value + self.recovery_grace_ms,
                        now_ms=now_value,
                    )
                    return None
                if (
                    recovery_not_before_ts_ms > now_value
                    or lease_expiry_ts_ms > now_value
                ):
                    return None
                try:
                    context = self._journal_context(row, uow=uow)
                except CanonApplyError as exc:
                    uow.quarantine_recovery(
                        apply_id=apply_id,
                        detail=str(exc),
                        recovery_not_before_ms=now_value + self.recovery_grace_ms,
                        now_ms=now_value,
                        fence=old_fence,
                    )
                    return None
                if not uow.claim_recovery(
                    apply_id=apply_id,
                    old_fence=old_fence,
                    lease_owner=self.instance_id,
                    lease_expiry_ms=now_value + self.recovery_lease_ms,
                    now_ms=now_value,
                ):
                    return None
                context["fence_version"] = old_fence + 1
        return context

    def _journal_context(
        self,
        row: Mapping[str, Any],
        *,
        uow: CanonApplyUnitOfWork,
        validate_subject: bool = True,
    ) -> dict[str, Any]:
        try:
            integer_fields = _validated_journal_integers(row)
            apply_id = validate_canon_binding_text("apply_id", str(row["apply_id"]))
            target_doc_id = _normalize_doc_id(str(row["target_doc_id"]))
            before_version = integer_fields["before_version"]
            after_version = integer_fields["after_version"]
            if before_version is None or after_version is None:  # pragma: no cover
                raise ValueError("journal version missing")
            before_hash = str(row["before_hash"]) if row["before_hash"] else None
            after_hash = str(row["after_hash"]) if row["after_hash"] else None
            before = _validated_canon_snapshot(
                row["before_payload_json"],
                target_doc_id=target_doc_id,
                expected_version=before_version,
                expected_hash=before_hash,
            )
            target = _validated_canon_snapshot(
                row["target_payload_json"],
                target_doc_id=target_doc_id,
                expected_version=after_version,
                expected_hash=after_hash,
            )
            binding = self._journal_binding(row)
            token_digest = str(row["token_digest"] or "")
            if not _HEX_64.fullmatch(token_digest):
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "token digest invalid"
                )
            expected_command_hash = _sha256_json(
                {"apply_id": apply_id, **binding, "token_digest": token_digest}
            )
            if str(row["command_hash"] or "") != expected_command_hash:
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "command hash mismatch"
                )
            token = uow.get_token(token_digest)
            if token is None:
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "token missing"
                )
            self._verify_approval_attestation(token, expected_binding=binding)
            burned_ts = _integrity_int(
                token["burned_ts_ms"],
                field_name="token.burned_ts_ms",
            )
            token_issued_ts_ms = _integrity_int(
                token["issued_ts_ms"],
                field_name="token.issued_ts_ms",
            )
            token_expiry_ts_ms = _integrity_int(
                token["expiry_ts_ms"],
                field_name="token.expiry_ts_ms",
            )
            prepared_ts = integer_fields["prepared_ts_ms"]
            if (
                burned_ts is None
                or token_issued_ts_ms is None
                or token_expiry_ts_ms is None
                or prepared_ts is None
            ):  # pragma: no cover - required helpers reject None
                raise ValueError("required token/journal INTEGER missing")
            if (
                str(token["state"]) != "BURNED"
                or str(token["burned_apply_id"] or "") != apply_id
                or burned_ts != prepared_ts
                or burned_ts < token_issued_ts_ms
                or burned_ts >= token_expiry_ts_ms
                or str(row["authority_key_id"]) != str(token["authority_key_id"])
                or str(row["approval_attestation_digest"])
                != str(token["approval_attestation_digest"])
                or str(row["evidence_digest"]) != str(token["evidence_digest"])
            ):
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "token coordinates mismatch"
                )
            table, key = self._subject_coordinates(row)
            self._validate_journal_operation_coordinates(row, before, target)
            if validate_subject:
                self._validate_journal_subject(uow, row, before, target)
            return {
                "apply_id": apply_id,
                "target_doc_id": target_doc_id,
                "before_payload": before,
                "target_payload": target,
                "before_version": before_version,
                "before_hash": before_hash,
                "after_version": after_version,
                "after_hash": after_hash,
                "subject_table": table,
                "subject_key": key,
                "fence_version": integer_fields["fence_version"],
            }
        except CanonApplyError as exc:
            if exc.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED":
                raise
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", exc.reason_code
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", str(exc)
            ) from exc

    def _journal_binding(self, row: Mapping[str, Any]) -> dict[str, Any]:
        evidence_raw = str(row["evidence_refs_json"] or "")
        try:
            evidence_value = json.loads(evidence_raw)
            if not isinstance(evidence_value, list):
                raise ValueError("evidence refs are not a list")
            evidence_refs = normalize_canon_evidence_refs(evidence_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "evidence refs invalid"
            ) from exc
        if evidence_raw != _canonical_json(list(evidence_refs)):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "evidence refs are not canonical"
            )
        try:
            command = CanonApplyCommand(
                apply_id=str(row["apply_id"]),
                operation=str(row["operation"]),
                subject_type=str(row["subject_type"]),
                subject_id=str(row["subject_id"]),
                subject_version=row["subject_version"],
                subject_hash=str(row["subject_hash"]),
                target_doc_id=str(row["target_doc_id"]),
                base_version=row["base_version"],
                approval_token_ref="journal-integrity-check",
                actor_id=str(row["actor_id"]),
                session_id=str(row["session_id"]),
                trace_id=str(row["trace_id"]),
                evidence_refs=evidence_refs,
            )
        except (TypeError, ValueError) as exc:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "command coordinates invalid"
            ) from exc
        binding = command.binding_dict()
        base_version = _integrity_int(
            row["base_version"],
            field_name="journal.base_version",
        )
        before_version = _integrity_int(
            row["before_version"],
            field_name="journal.before_version",
        )
        if (
            str(row["evidence_digest"]) != canon_evidence_digest(evidence_refs)
            or base_version != before_version
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "command coordinates mismatch"
            )
        return binding

    @staticmethod
    def _validate_journal_operation_coordinates(
        row: Mapping[str, Any],
        before: dict[str, Any] | None,
        target: dict[str, Any] | None,
    ) -> None:
        operation = str(row["operation"])
        subject_type = str(row["subject_type"])
        subject_version = _integrity_int(
            row["subject_version"],
            field_name="journal.subject_version",
        )
        base_version = _integrity_int(
            row["base_version"],
            field_name="journal.base_version",
        )
        before_version = _integrity_int(
            row["before_version"],
            field_name="journal.before_version",
        )
        after_version = _integrity_int(
            row["after_version"],
            field_name="journal.after_version",
        )
        if operation == CANON_OPERATION_APPLY:
            if target is None or after_version != base_version + 1:
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "apply target invalid"
                )
            if subject_type == CANON_SUBJECT_PROPOSAL and (
                subject_version != after_version
                or str(row["subject_hash"]) != str(row["after_hash"])
            ):
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "proposal target is not signed"
                )
            if subject_type == CANON_SUBJECT_PENTEST_PROMOTION and subject_version != 1:
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "promotion version invalid"
                )
            return
        if operation != CANON_OPERATION_ROLLBACK or subject_type != CANON_SUBJECT_PROPOSAL:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "operation invalid"
            )
        if before is None or (
            subject_version != before_version
            or str(row["subject_hash"]) != str(row["before_hash"])
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "rollback source is not signed"
            )
        if target is not None and after_version >= before_version:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "rollback target version invalid"
            )

    def _validate_journal_subject(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        before: dict[str, Any] | None,
        target: dict[str, Any] | None,
    ) -> None:
        operation = str(row["operation"])
        subject_type = str(row["subject_type"])
        if operation == CANON_OPERATION_APPLY and subject_type == CANON_SUBJECT_PROPOSAL:
            self._validate_prepared_proposal(uow, row, target)
            return
        if operation == CANON_OPERATION_APPLY and subject_type == CANON_SUBJECT_PENTEST_PROMOTION:
            self._validate_prepared_promotion(uow, row, target)
            return
        self._validate_prepared_rollback(uow, row, before, target)

    def _validate_prepared_proposal(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        target: dict[str, Any] | None,
    ) -> None:
        if (
            str(row["subject_previous_status"]).lower() != "pending"
            or str(row["subject_prepared_status"]) != "APPLY_PREPARED"
            or str(row["subject_final_status"]) != "APPLIED"
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "proposal status coordinates invalid"
            )
        proposal = uow.get_proposal(str(row["subject_id"]))
        if proposal is None or str(proposal["status"]) != "APPLY_PREPARED":
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "prepared proposal missing"
            )
        if str(proposal["canon_kind"]) != "world":
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "prepared proposal domain drift",
            )
        expected = self._proposal_target_payload(proposal)
        proposal_base_version = _integrity_int(
            proposal["base_version"],
            field_name="proposal.base_version",
        )
        proposal_version = _integrity_int(
            proposal["proposed_version"],
            field_name="proposal.proposed_version",
        )
        journal_base_version = _integrity_int(
            row["base_version"],
            field_name="journal.base_version",
        )
        journal_subject_version = _integrity_int(
            row["subject_version"],
            field_name="journal.subject_version",
        )
        if (
            str(proposal["proposed_hash"] or "").lower() != str(row["subject_hash"])
            or _normalize_doc_id(str(proposal["target_id"])) != str(row["target_doc_id"])
            or proposal_base_version != journal_base_version
            or proposal_version != journal_subject_version
            or not _same_complete_payload(expected, target)
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "prepared proposal drift"
            )

    @staticmethod
    def _proposal_target_payload(proposal: Mapping[str, Any]) -> dict[str, Any]:
        title = str(proposal["proposed_title"] or "").strip()
        body = str(proposal["proposed_body"] or "").strip()
        if not title or not body:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "proposal payload missing"
            )
        payload: dict[str, Any] = {
            "doc_id": _normalize_doc_id(str(proposal["target_id"] or "")),
            "version": _integrity_int(
                proposal["proposed_version"],
                field_name="proposal.proposed_version",
            ),
            "status": "active",
            "title": title,
            "tags": _parse_tags(proposal["proposed_tags"]),
            "body": body,
            "updated_by": str(proposal["proposal_id"]),
            "ts_ms": _integrity_int(
                proposal["ts_ms"],
                field_name="proposal.ts_ms",
            ),
        }
        payload["hash"] = compute_world_canon_hash(payload)
        return payload

    def _validate_prepared_promotion(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        target: dict[str, Any] | None,
    ) -> None:
        if (
            str(row["subject_previous_status"]) != "PENDING_REVIEW"
            or str(row["subject_prepared_status"]) != "APPROVED"
            or str(row["subject_final_status"]) != "APPLIED"
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "promotion status coordinates invalid"
            )
        candidate = uow.get_promotion_candidate(str(row["subject_id"]))
        if candidate is None or str(candidate["status"]) != "APPROVED":
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "prepared promotion missing"
            )
        original = dict(candidate)
        original["status"] = "PENDING_REVIEW"
        if compute_promotion_subject_hash(original) != str(row["subject_hash"]):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "prepared promotion drift"
            )
        self._validate_promotion_lineage(
            uow,
            candidate,
            reason_code="CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
        )
        expected = self._promotion_target_payload(
            candidate,
            target_doc_id=str(row["target_doc_id"]),
            base_version=_required_exact_int(
                _integrity_int(
                    row["base_version"],
                    field_name="journal.base_version",
                ),
                field_name="journal.base_version",
            ),
        )
        if not _same_complete_payload(expected, target):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "promotion target drift"
            )

    @staticmethod
    def _validate_promotion_lineage(
        uow: CanonApplyUnitOfWork,
        candidate: Mapping[str, Any],
        *,
        reason_code: str,
    ) -> tuple[dict[str, Any], list[str]]:
        payload = _parse_json_object(candidate["payload_json"], reason_code=reason_code)
        try:
            evidence_refs = json.loads(str(candidate["evidence_refs_json"] or "[]"))
        except json.JSONDecodeError as exc:
            raise CanonApplyError(reason_code, "promotion evidence invalid") from exc
        entry = uow.get_pentest_entry(str(candidate["entry_id"]))
        if entry is None:
            raise CanonApplyError(reason_code, "promotion source entry missing")
        try:
            source_payload = json.loads(str(entry["payload_json"]))
            source_evidence_refs = json.loads(str(entry["evidence_refs_json"]))
            source_tags = json.loads(str(entry["tags_json"]))
        except json.JSONDecodeError as exc:
            raise CanonApplyError(reason_code, "promotion source JSON invalid") from exc
        if (
            set(payload) != _PENTEST_ENTRY_SNAPSHOT_KEYS
            or not isinstance(source_payload, dict)
            or not isinstance(evidence_refs, list)
            or not isinstance(source_evidence_refs, list)
            or not isinstance(source_tags, list)
            or any(not isinstance(value, str) for value in evidence_refs)
            or any(not isinstance(value, str) for value in source_evidence_refs)
            or any(not isinstance(value, str) for value in source_tags)
        ):
            raise CanonApplyError(reason_code, "promotion source schema invalid")
        source_created_ts_ms = _reason_int(
            entry["created_ts_ms"],
            field_name="promotion_entry.created_ts_ms",
            reason_code=reason_code,
        )
        source_updated_ts_ms = _reason_int(
            entry["updated_ts_ms"],
            field_name="promotion_entry.updated_ts_ms",
            reason_code=reason_code,
        )
        _reason_int(
            payload["created_ts_ms"],
            field_name="promotion_payload.created_ts_ms",
            reason_code=reason_code,
        )
        _reason_int(
            payload["updated_ts_ms"],
            field_name="promotion_payload.updated_ts_ms",
            reason_code=reason_code,
        )
        source_snapshot: dict[str, Any] = {
            "entry_id": str(entry["entry_id"]),
            "schema_version": str(entry["schema_version"]),
            "knowledge_type": str(entry["knowledge_type"]),
            "title": str(entry["title"]),
            "summary": str(entry["summary"]),
            "payload": source_payload,
            "source_ref": str(entry["source_ref"]),
            "source_hash": str(entry["source_hash"]),
            "evidence_refs": source_evidence_refs,
            "tags": source_tags,
            "status": str(entry["status"]),
            "created_ts_ms": source_created_ts_ms,
            "updated_ts_ms": source_updated_ts_ms,
        }
        computed_source_hash = _sha256_json(
            {
                "evidence_refs": source_evidence_refs,
                "knowledge_type": source_snapshot["knowledge_type"],
                "payload": source_payload,
                "source_ref": source_snapshot["source_ref"],
                "status": source_snapshot["status"],
                "summary": source_snapshot["summary"],
                "tags": source_tags,
                "title": source_snapshot["title"],
            }
        )
        if (
            str(candidate["entry_id"]) != source_snapshot["entry_id"]
            or str(candidate["source_hash"]) != source_snapshot["source_hash"]
            or computed_source_hash != source_snapshot["source_hash"]
            or evidence_refs != source_evidence_refs
            or not _same_complete_payload(payload, source_snapshot)
        ):
            raise CanonApplyError(reason_code, "promotion source lineage drift")
        return payload, evidence_refs

    @staticmethod
    def _promotion_target_payload(
        candidate: Mapping[str, Any],
        *,
        target_doc_id: str,
        base_version: int,
    ) -> dict[str, Any]:
        payload = _parse_json_object(
            candidate["payload_json"], reason_code="CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
        )
        try:
            evidence_refs = json.loads(str(candidate["evidence_refs_json"] or "[]"))
        except json.JSONDecodeError as exc:
            raise CanonApplyError("CANON_APPLY_JOURNAL_INTEGRITY_FAILED", str(exc)) from exc
        if not isinstance(evidence_refs, list):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "promotion evidence invalid"
            )
        title = str(
            payload.get("title") or payload.get("summary") or candidate["entry_id"]
        ).strip()
        body = _canonical_json(
            {
                "entry_id": candidate["entry_id"],
                "knowledge_type": payload.get("knowledge_type"),
                "summary": payload.get("summary"),
                "payload": payload.get("payload"),
                "source_ref": payload.get("source_ref"),
                "source_hash": candidate["source_hash"],
                "evidence_refs": evidence_refs,
            }
        )
        target: dict[str, Any] = {
            "doc_id": target_doc_id,
            "version": _required_exact_int(
                base_version,
                field_name="promotion.base_version",
            )
            + 1,
            "status": "active",
            "title": title,
            "tags": _normalize_tags(
                [
                    "pentest",
                    str(payload.get("knowledge_type") or "knowledge"),
                    *payload.get("tags", []),
                ]
            ),
            "body": body,
            "updated_by": str(candidate["candidate_id"]),
            "ts_ms": _required_exact_int(
                _integrity_int(
                    candidate["created_ts_ms"],
                    field_name="promotion.created_ts_ms",
                ),
                field_name="promotion.created_ts_ms",
            ),
        }
        target["hash"] = compute_world_canon_hash(target)
        return target

    def _validated_historical_apply_chain(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        *,
        path: frozenset[str] | None = None,
        depth: int = 0,
    ) -> dict[str, Any]:
        if depth >= _MAX_APPLIED_LINEAGE_DEPTH:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical apply lineage depth exceeded",
            )
        apply_id = str(row["apply_id"] or "")
        current_path = frozenset() if path is None else path
        if not apply_id or apply_id in current_path:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical apply lineage cycle",
            )
        try:
            integer_fields = _validated_journal_integers(row)
            completed_ts_ms = integer_fields["completed_ts_ms"]
            prepared_ts_ms = integer_fields["prepared_ts_ms"]
            if (
                str(row["state"]) != JOURNAL_APPLIED
                or str(row["operation"]) != CANON_OPERATION_APPLY
                or integer_fields["file_replaced"] != 1
                or completed_ts_ms is None
                or prepared_ts_ms is None
                or completed_ts_ms < prepared_ts_ms
                or row["lease_owner"] is not None
            ):
                raise ValueError("historical apply is not finalized")
        except (KeyError, TypeError, ValueError) as exc:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical apply final state invalid",
            ) from exc

        context = self._journal_context(row, uow=uow, validate_subject=False)
        self._validate_historical_apply_subject(
            uow,
            row,
            context["target_payload"],
        )
        before = context["before_payload"]
        before_version = context["before_version"]
        if before_version == 0:
            if before is not None or context["before_hash"] is not None:
                raise CanonApplyError(
                    "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                    "base-zero historical apply has a before snapshot",
                )
            return context

        next_path = current_path | {apply_id}
        predecessors = uow.list_apply_predecessors(
            target_doc_id=context["target_doc_id"],
            after_version=before_version,
            after_hash=context["before_hash"],
            exclude_apply_id=apply_id,
            completed_before_ms=prepared_ts_ms,
            limit=_MAX_APPLIED_LINEAGE_CANDIDATES,
        )
        for predecessor in predecessors:
            predecessor_id = str(predecessor["apply_id"] or "")
            if not predecessor_id or predecessor_id in next_path:
                continue
            try:
                predecessor_context = self._validated_historical_apply_chain(
                    uow,
                    predecessor,
                    path=next_path,
                    depth=depth + 1,
                )
            except CanonApplyError:
                continue
            if _same_complete_payload(predecessor_context["target_payload"], before):
                return context
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
            "historical apply predecessor untrusted",
        )

    def _validate_historical_apply_subject(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        target: dict[str, Any] | None,
    ) -> None:
        subject_type = str(row["subject_type"])
        if subject_type == CANON_SUBJECT_PROPOSAL:
            self._validate_historical_proposal(uow, row, target)
            return
        if subject_type == CANON_SUBJECT_PENTEST_PROMOTION:
            self._validate_historical_promotion(uow, row, target)
            return
        raise CanonApplyError(
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
            "historical apply subject invalid",
        )

    def _validate_historical_proposal(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        target: dict[str, Any] | None,
    ) -> None:
        if (
            str(row["subject_previous_status"]).lower() != "pending"
            or str(row["subject_prepared_status"]) != "APPLY_PREPARED"
            or str(row["subject_final_status"]) != "APPLIED"
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical proposal status coordinates invalid",
            )
        proposal = uow.get_proposal(str(row["subject_id"]))
        if (
            proposal is None
            or str(proposal["status"]) not in {"APPLIED", "ROLLBACK_PREPARED"}
            or str(proposal["canon_kind"]) != "world"
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical proposal missing",
            )
        try:
            expected = self._proposal_target_payload(proposal)
        except CanonApplyError as exc:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical proposal payload invalid",
            ) from exc
        proposal_base_version = _integrity_int(
            proposal["base_version"],
            field_name="proposal.base_version",
        )
        proposal_version = _integrity_int(
            proposal["proposed_version"],
            field_name="proposal.proposed_version",
        )
        journal_base_version = _integrity_int(
            row["base_version"],
            field_name="journal.base_version",
        )
        journal_subject_version = _integrity_int(
            row["subject_version"],
            field_name="journal.subject_version",
        )
        if (
            str(proposal["proposed_hash"] or "").lower() != str(row["subject_hash"])
            or _normalize_doc_id(str(proposal["target_id"])) != str(row["target_doc_id"])
            or proposal_base_version != journal_base_version
            or proposal_version != journal_subject_version
            or not _same_complete_payload(expected, target)
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical proposal drift",
            )

    def _validate_historical_promotion(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        target: dict[str, Any] | None,
    ) -> None:
        if (
            str(row["subject_previous_status"]) != "PENDING_REVIEW"
            or str(row["subject_prepared_status"]) != "APPROVED"
            or str(row["subject_final_status"]) != "APPLIED"
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical promotion status coordinates invalid",
            )
        candidate = uow.get_promotion_candidate(str(row["subject_id"]))
        if candidate is None or str(candidate["status"]) != "APPLIED":
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical promotion missing",
            )
        original = dict(candidate)
        original["status"] = "PENDING_REVIEW"
        try:
            authorization_hash = compute_promotion_subject_hash(original)
            _payload, evidence_refs = self._validate_promotion_lineage(
                uow,
                candidate,
                reason_code="CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
            )
            expected = self._promotion_target_payload(
                candidate,
                target_doc_id=str(row["target_doc_id"]),
                base_version=_required_exact_int(
                    _integrity_int(
                        row["base_version"],
                        field_name="journal.base_version",
                    ),
                    field_name="journal.base_version",
                ),
            )
        except CanonApplyError as exc:
            if exc.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED":
                raise
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical promotion payload invalid",
            ) from exc
        candidate_hash = _sha256_json(
            {
                "entry_id": str(candidate["entry_id"]),
                "source_hash": str(candidate["source_hash"]),
                "evidence_refs": evidence_refs,
                "reason": str(candidate["reason"]),
            }
        )
        if (
            authorization_hash != str(row["subject_hash"])
            or candidate_hash != str(candidate["candidate_hash"])
            or not _same_complete_payload(expected, target)
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED",
                "historical promotion drift",
            )

    def _validate_prepared_rollback(
        self,
        uow: CanonApplyUnitOfWork,
        row: Mapping[str, Any],
        before: dict[str, Any] | None,
        target: dict[str, Any] | None,
    ) -> None:
        if (
            str(row["subject_previous_status"]) != "APPLIED"
            or str(row["subject_prepared_status"]) != "ROLLBACK_PREPARED"
            or str(row["subject_final_status"]) != "ROLLED_BACK"
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "rollback status coordinates invalid"
            )
        proposal = uow.get_proposal(str(row["subject_id"]))
        if (
            proposal is None
            or str(proposal["status"]) != "ROLLBACK_PREPARED"
            or str(proposal["canon_kind"]) != "world"
            or _normalize_doc_id(str(proposal["target_id"])) != str(row["target_doc_id"])
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "prepared rollback missing"
            )
        source = uow.applied_rollback_source(
            subject_id=str(row["subject_id"]),
            target_doc_id=str(row["target_doc_id"]),
            after_version=int(row["before_version"]),
            after_hash=str(row["before_hash"]),
        )
        if source is None:
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "rollback lineage missing"
            )
        source_context = self._validated_historical_apply_chain(uow, source)
        if (
            not _same_complete_payload(source_context["target_payload"], before)
            or not _same_complete_payload(source_context["before_payload"], target)
        ):
            raise CanonApplyError(
                "CANON_APPLY_JOURNAL_INTEGRITY_FAILED", "rollback lineage drift"
            )

    @staticmethod
    def _subject_coordinates(row: Mapping[str, Any]) -> tuple[str, str]:
        if str(row["subject_type"]) == CANON_SUBJECT_PROPOSAL:
            return "canon_proposal_log", "proposal_id"
        if str(row["subject_type"]) == CANON_SUBJECT_PENTEST_PROMOTION:
            return "pentest_knowledge_promotion_candidate", "candidate_id"
        raise CanonApplyError("CANON_APPLY_JOURNAL_SUBJECT_INVALID")

    def _sync_vectors(self, result: CanonApplyResult) -> None:
        try:
            trigger_canon_vector_auto_sync(
                include_world=True,
                world_root=self.world_root,
                reason=f"canon_apply:{result.apply_id}:{result.state}",
            )
        except Exception:
            # The SSOT and journal are already durable.  Vector projection is a
            # rebuildable derivative and must never rewrite apply truth.
            return


__all__ = [
    "CANON_OPERATION_APPLY",
    "CANON_OPERATION_ROLLBACK",
    "CANON_SUBJECT_PENTEST_PROMOTION",
    "CANON_SUBJECT_PROPOSAL",
    "CanonApplyCommand",
    "CanonApplyCrash",
    "CanonApplyError",
    "CanonApplyResult",
    "CanonApplyService",
    "compute_promotion_subject_hash",
    "default_world_canon_root",
    "resolve_world_canon_root",
    "JOURNAL_APPLIED",
    "JOURNAL_FAILED",
    "JOURNAL_PREPARED",
    "JOURNAL_RECOVERY_REQUIRED",
]
