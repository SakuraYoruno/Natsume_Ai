"""治理令牌的签发与验证：把批准、确认和撤销绑定到一次具体意图。

令牌不是通用凭据：验证时必须同时匹配类型、意图、技能、会话和序号，并在需要时原子地
一次性消费。文件后半还提供离线 Canon 批准令牌签发器，私钥绝不进入在线运行时组合。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
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

CANON_OPERATION_APPLY = "APPLY"
CANON_OPERATION_ROLLBACK = "ROLLBACK"
CANON_SUBJECT_PROPOSAL = "CANON_PROPOSAL"
CANON_SUBJECT_PENTEST_PROMOTION = "PENTEST_PROMOTION"
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
    """仅接受 Python ``int``，与 sqlite3 精确的 INTEGER 存储类别保持一致。

    ``bool`` 虽是 ``int`` 子类却不代表版本或时间戳；以 ``type(value) is int`` 拒绝它，
    防止宽松转换让签名绑定的持久化字段与后续读取语义不一致。
    """

    if value is None and optional:
        return None
    if type(value) is not int:
        raise ValueError(f"CANON_APPLY_INTEGER_INVALID:{field_name}")
    return value


@dataclass(frozen=True)
class TokenRecord:
    """内存令牌的最小绑定坐标及一次性消费状态。"""

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
    """令牌验证结果；失败只暴露稳定原因码，不回显令牌内容。"""

    valid: bool
    reason_code: str | None = None
    token: TokenRecord | None = None
    detail: str | None = None


class TokenService:
    """供 Governor/技能契约使用的进程内令牌登记表。

    ``RLock`` 将检查和标记已用保持在同一临界区，避免并发请求双重消费同一令牌。此实现
    不跨进程持久化，进程重启会自然失效，适用于短生命周期的在线批准流。
    """

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
        ttl_ms: int | None = None,
    ) -> str:
        effective_ttl_ms = self.approval_ttl_ms if ttl_ms is None else ttl_ms
        if isinstance(effective_ttl_ms, bool) or not isinstance(effective_ttl_ms, int):
            raise TypeError("approval token ttl_ms must be an integer")
        if effective_ttl_ms <= 0:
            raise ValueError("approval token ttl_ms must be positive")
        return self._issue(
            token_type=TOKEN_TYPE_APPROVAL,
            intent_id=intent_id,
            skill_name=skill_name,
            session_id=session_id,
            seq=seq,
            ttl_ms=effective_ttl_ms,
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
        """校验令牌是否仍精确绑定当前调用，并可选地原子标为已使用。"""
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
            # 到期时刻本身不在有效区间内，避免边界请求因时钟粒度获得额外执行窗口。
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
        """先完整预检再消费执行令牌，保证确认失败不会烧掉批准令牌。"""
        # 修复（M28-L）：先验证 approval_token（不消费），再验证 confirm_token（不消费），
        # 两者均通过后才依次消费，防止 approval_token 被消费后 confirm 验证失败导致 intent 无法重试。
        # RLock 使预检与一次性消费成为同一个登记表原子操作。
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
    """序列化为稳定 JSON，供签名和摘要在重启或不同调用处保持可复算。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canon_evidence_digest(evidence_refs: Iterable[str]) -> str:
    refs = list(normalize_canon_evidence_refs(evidence_refs))
    return hashlib.sha256(_canonical_json(refs).encode("utf-8")).hexdigest()


def normalize_canon_evidence_refs(evidence_refs: Iterable[str]) -> tuple[str, ...]:
    """验证 Canon 证据引用的数量、形态与长度，并拒绝疑似秘密文本。

    证据引用会进入持久化审批证明，因此这里比一般展示字段更严格：不接受控制字符、
    非 URI 形态或令牌/密钥特征，避免把可用凭据写入长期审计面。
    """
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
    """规范化 Canon 审批绑定字段，并在签名前拒绝空值、控制符及秘密特征。"""
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


def _canon_apply_migration_path() -> Path:
    from host_brain.persistence.canon_apply_repository import (  # noqa: PLC0415
        canon_apply_migration_path,
    )

    return canon_apply_migration_path()


def _durable_db_path(db_path: str | Path) -> str:
    raw = str(db_path or "").strip()
    if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
        raise ValueError("CANON_APPLY_DURABLE_DB_REQUIRED")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("CANON_APPLY_DURABLE_DB_ABSOLUTE_PATH_REQUIRED")
    return str(candidate.resolve())


class CanonApprovalTokenIssuer:
    """离线 Governor/MASTER 签发器；绝不可组合进在线运行时应用。

    它用 Ed25519 对精确绑定的 Canon 变更证明签名，再把令牌摘要及证明写入耐久仓储。
    私钥只保留在该离线对象中，任何查询接口均只能返回不可逆的元数据摘要。
    """

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
        # 仅在构造离线签发器时导入仓储：若在 governor.tokens 初始化期间加载持久化包，
        # 旧包导出会回环引用 TokenService，破坏模块初始化顺序。
        from host_brain.persistence.canon_approval_repository import (
            CanonApprovalTokenRepository,
        )

        self._repository = CanonApprovalTokenRepository(
            self.db_path,
        )
        self._repository.assert_ready()

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
        """为一项指定 Canon APPLY/ROLLBACK 变更签发短时、可审计的批准令牌。

        待签名证明覆盖目标版本、主体哈希、操作者、追踪链和证据引用；仓储只保存令牌
        摘要，调用方得到的 bearer 值无法由元数据接口反推出。
        """
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
        self._repository.insert_available_token(
            {
                "token_digest": digest,
                "token_type": token_type_value,
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
                "evidence_digest": evidence_digest,
                "authority_key_id": self.authority_key_id,
                "approval_attestation_json": attestation_json,
                "approval_attestation_digest": attestation_digest,
                "approval_signature": signature,
                "approval_nonce": approval_nonce,
                "issued_ts_ms": issued,
                "expiry_ts_ms": expiry,
            }
        )
        return token_ref

    def get_token_metadata(self, token_ref: str) -> dict[str, Any] | None:
        """返回非秘密元数据；bearer 值始终不可由仓储或该接口恢复。"""

        return self._repository.get_token_metadata(canon_token_digest(token_ref))
