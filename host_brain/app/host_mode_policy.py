"""Host 自动模式策略的应用局部 owner，支持带 revision 的持久化提交。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from threading import Lock
from typing import Any

from host_brain.contracts.host_mode_policy import (
    DEFAULT_HOST_MODE_AUTO_RULES,
    HOST_MODE_AUTO_RULES,
    HostModePolicyCommitV1,
    HostModePolicyEventV1,
    HostModePolicyJournalPort,
    HostModePolicySnapshotV1,
)


class HostModePolicyOwnerError(RuntimeError):
    """应用局部模式策略无法证明其规范状态时抛出的失败关闭错误。"""


def _rules(value: Mapping[str, Any]) -> dict[str, bool]:
    """校验完整规则面，拒绝缺项/多项，避免持久快照语义被悄然扩展。"""
    candidate = dict(value or {})
    if set(candidate) != set(HOST_MODE_AUTO_RULES) or any(
        type(candidate[key]) is not bool for key in HOST_MODE_AUTO_RULES
    ):
        raise HostModePolicyOwnerError("HOST_MODE_POLICY_RULES_INVALID")
    return {key: bool(candidate[key]) for key in sorted(HOST_MODE_AUTO_RULES)}


def _updates(value: Mapping[str, Any]) -> dict[str, bool]:
    """校验局部规则更新；空更新不能制造无意义的 revision。"""
    candidate = dict(value or {})
    if (
        not candidate
        or not set(candidate).issubset(HOST_MODE_AUTO_RULES)
        or any(type(item) is not bool for item in candidate.values())
    ):
        raise HostModePolicyOwnerError("HOST_MODE_POLICY_RULE_UPDATE_INVALID")
    return {key: bool(candidate[key]) for key in sorted(candidate)}


def _operation_id(value: Any) -> str:
    """规范化可审计幂等键，并拒绝控制字符或过长标识。"""
    text = str(value or "").strip()
    if (
        not text
        or len(text.encode("utf-8")) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise HostModePolicyOwnerError("HOST_MODE_POLICY_OPERATION_ID_INVALID")
    return text


def _revision(value: Any) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 1:
        raise HostModePolicyOwnerError(
            "HOST_MODE_POLICY_EXPECTED_REVISION_INVALID"
        )
    return value


def _timestamp(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise HostModePolicyOwnerError(f"HOST_MODE_POLICY_{field.upper()}_INVALID")
    return value


def _input_digest(value: Mapping[str, Any]) -> str:
    """对稳定 JSON 输入求摘要，使同一 operation 的重放能检测语义漂移。"""
    try:
        canonical = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HostModePolicyOwnerError(
            "HOST_MODE_POLICY_INPUT_INVALID"
        ) from exc
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class HostModePolicyOwner:
    """一个运行时围栏 journal 的线性化应用局部门面。

    operation_id 用于幂等重开，expected revision 拒绝陈旧写入；bootstrap 的 transient
    快照只服务于启动前组合，要求 durable 时必须先绑定 journal，不能把内存状态当成
    可恢复的模式决策权威。
    """

    _snapshot: HostModePolicySnapshotV1
    _require_durable: bool = field(default=False, repr=False)
    _journal: HostModePolicyJournalPort | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _transient_events: dict[str, HostModePolicyEventV1] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def bootstrap(
        cls,
        *,
        initial_last_interaction_ts_ms: int,
        initial_auto_rules: Mapping[str, bool] = DEFAULT_HOST_MODE_AUTO_RULES,
        require_durable: bool = False,
    ) -> "HostModePolicyOwner":
        """创建未绑定的初始快照；后续持久化所有权由 ``bind_durable_journal`` 接管。"""
        interaction = _timestamp(
            initial_last_interaction_ts_ms,
            field="initial_last_interaction_ts_ms",
        )
        return cls(
            _snapshot=HostModePolicySnapshotV1(
                runtime_scope_id="transient",
                revision=1,
                auto_rules=_rules(initial_auto_rules),
                last_interaction_ts_ms=interaction,
                operation_id="transient-initialization",
                event_digest="",
                updated_at_ms=interaction,
            ),
            _require_durable=bool(require_durable),
        )

    def bind_durable_journal(
        self,
        journal: HostModePolicyJournalPort,
        *,
        initialization_operation_id: str = "host-mode-policy-initialized",
    ) -> HostModePolicySnapshotV1:
        """一次性绑定 journal 并以其已存状态或初始化提交替换 transient 快照。"""
        if journal is None:
            raise HostModePolicyOwnerError("HOST_MODE_POLICY_JOURNAL_REQUIRED")
        with self._lock:
            if self._journal is not None:
                raise HostModePolicyOwnerError(
                    "HOST_MODE_POLICY_JOURNAL_ALREADY_BOUND"
                )
            current = self._snapshot
            commit = journal.load_or_initialize(
                initial_auto_rules=current.auto_rules,
                initial_last_interaction_ts_ms=(
                    current.last_interaction_ts_ms
                ),
                operation_id=_operation_id(initialization_operation_id),
                observed_at_ms=current.updated_at_ms,
            )
            self._snapshot = commit.snapshot
            self._journal = journal
            self._transient_events.clear()
            return self._snapshot

    def unbind_durable_journal(self) -> None:
        """在 lifespan 关闭时断开 journal；之后要求耐久的读取/写入将失败关闭。"""
        with self._lock:
            self._journal = None

    def snapshot(self) -> HostModePolicySnapshotV1:
        """返回防御性快照，调用方无法通过可变映射改写 owner 内部规则。"""
        with self._lock:
            self._require_bound()
            current = self._snapshot
            return HostModePolicySnapshotV1(
                runtime_scope_id=current.runtime_scope_id,
                revision=current.revision,
                auto_rules=dict(current.auto_rules),
                last_interaction_ts_ms=current.last_interaction_ts_ms,
                operation_id=current.operation_id,
                event_digest=current.event_digest,
                updated_at_ms=current.updated_at_ms,
            )

    def get_operation(
        self,
        operation_id: str,
    ) -> HostModePolicyEventV1 | None:
        """按 operation ID 重开已记录事件，用于幂等请求而非模糊历史查询。"""
        operation = _operation_id(operation_id)
        with self._lock:
            self._require_bound()
            journal = self._journal
            if journal is None:
                return self._transient_events.get(operation)
            return journal.get_operation(operation)

    def update_auto_rules(
        self,
        rules: Mapping[str, bool],
        *,
        operation_id: str,
        expected_revision: int,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1:
        """以 expected revision 提交自动规则变更，先重开同 ID 的精确操作。"""
        changes = _updates(rules)
        operation = _operation_id(operation_id)
        expected = _revision(expected_revision)
        observed = _timestamp(observed_at_ms, field="observed_at_ms")
        with self._lock:
            self._require_bound()
            existing = self._reopen_operation(
                operation_id=operation,
                event_kind="AUTO_RULES_UPDATED",
                input_payload={"rules": changes},
                event_payload={"rules": changes},
            )
            if existing is not None:
                return existing
            current = self._snapshot
            next_rules = {**dict(current.auto_rules), **changes}
            return self._commit(
                operation_id=operation,
                expected_revision=expected,
                event_kind="AUTO_RULES_UPDATED",
                input_payload={"rules": changes},
                event_payload={"rules": changes},
                auto_rules=next_rules,
                last_interaction_ts_ms=current.last_interaction_ts_ms,
                observed_at_ms=observed,
            )

    def record_event(
        self,
        *,
        operation_id: str,
        expected_revision: int,
        event_kind: str,
        input_payload: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        last_interaction_ts_ms: int,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1:
        """记录策略相关事件，并用输入摘要保证重试载荷与原请求一致。"""
        operation = _operation_id(operation_id)
        expected = _revision(expected_revision)
        kind = str(event_kind or "").strip().upper()
        if kind not in {"EVENT_SUBMITTED", "INTERACTION_RECORDED"}:
            raise HostModePolicyOwnerError(
                "HOST_MODE_POLICY_EVENT_KIND_INVALID"
            )
        interaction = _timestamp(
            last_interaction_ts_ms,
            field="last_interaction_ts_ms",
        )
        observed = _timestamp(observed_at_ms, field="observed_at_ms")
        with self._lock:
            self._require_bound()
            existing = self._reopen_operation(
                operation_id=operation,
                event_kind=kind,
                input_payload=dict(input_payload or {}),
                event_payload=dict(event_payload or {}),
                last_interaction_ts_ms=interaction,
            )
            if existing is not None:
                return existing
            return self._commit(
                operation_id=operation,
                expected_revision=expected,
                event_kind=kind,
                input_payload=dict(input_payload or {}),
                event_payload=dict(event_payload or {}),
                auto_rules=self._snapshot.auto_rules,
                last_interaction_ts_ms=interaction,
                observed_at_ms=observed,
            )

    def _commit(
        self,
        *,
        operation_id: str,
        expected_revision: int,
        event_kind: str,
        input_payload: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        auto_rules: Mapping[str, bool],
        last_interaction_ts_ms: int,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1:
        """提交或暂存事件；耐久 journal 优先，transient 仅用于启动前组合。"""
        journal = self._journal
        current = self._snapshot
        if journal is not None:
            commit = journal.append(
                expected_revision=expected_revision,
                operation_id=operation_id,
                event_kind=event_kind,
                input_payload=input_payload,
                event_payload=event_payload,
                auto_rules=auto_rules,
                last_interaction_ts_ms=last_interaction_ts_ms,
                observed_at_ms=observed_at_ms,
            )
            self._snapshot = commit.snapshot
            return commit
        existing = self._transient_events.get(operation_id)
        if existing is not None:
            if (
                existing.event_kind != event_kind
                or dict(existing.event_payload) != dict(event_payload)
                or dict(existing.auto_rules) != dict(auto_rules)
                or existing.last_interaction_ts_ms != last_interaction_ts_ms
            ):
                raise HostModePolicyOwnerError(
                    "HOST_MODE_POLICY_IDEMPOTENCY_DRIFT"
                )
            return HostModePolicyCommitV1(
                snapshot=current,
                event=existing,
                exact_replay=True,
            )
        if current.revision != expected_revision:
            raise HostModePolicyOwnerError(
                "HOST_MODE_POLICY_EXPECTED_HEAD_CONFLICT"
            )
        next_revision = current.revision + 1
        event = HostModePolicyEventV1(
            runtime_scope_id=current.runtime_scope_id,
            revision=next_revision,
            operation_id=operation_id,
            event_kind=event_kind,
            input_digest="transient",
            event_payload=dict(event_payload),
            auto_rules=_rules(auto_rules),
            last_interaction_ts_ms=last_interaction_ts_ms,
            previous_event_digest=current.event_digest,
            event_digest="",
            runtime_owner_id="transient",
            runtime_lease_id="transient",
            runtime_fencing_token=1,
            runtime_issued_at_ms=0,
            runtime_expires_at_ms=2**63 - 1,
            observed_at_ms=observed_at_ms,
            created_at_ms=observed_at_ms,
        )
        self._snapshot = HostModePolicySnapshotV1(
            runtime_scope_id=current.runtime_scope_id,
            revision=next_revision,
            auto_rules=dict(event.auto_rules),
            last_interaction_ts_ms=last_interaction_ts_ms,
            operation_id=operation_id,
            event_digest="",
            updated_at_ms=observed_at_ms,
        )
        self._transient_events[operation_id] = event
        return HostModePolicyCommitV1(
            snapshot=self._snapshot,
            event=event,
            exact_replay=False,
        )

    def _reopen_operation(
        self,
        *,
        operation_id: str,
        event_kind: str,
        input_payload: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        last_interaction_ts_ms: int | None = None,
    ) -> HostModePolicyCommitV1 | None:
        """验证既有操作的类型、摘要和载荷后精确重开，漂移则拒绝重放。"""
        journal = self._journal
        existing = (
            journal.get_operation(operation_id)
            if journal is not None
            else self._transient_events.get(operation_id)
        )
        if existing is None:
            return None
        if (
            existing.event_kind != event_kind
            or existing.input_digest != (
                _input_digest(input_payload) if journal is not None else "transient"
            )
            or dict(existing.event_payload) != dict(event_payload)
            or (
                last_interaction_ts_ms is not None
                and existing.last_interaction_ts_ms != last_interaction_ts_ms
            )
        ):
            raise HostModePolicyOwnerError(
                "HOST_MODE_POLICY_IDEMPOTENCY_DRIFT"
            )
        if journal is not None:
            current = journal.load()
            if current is None:
                raise HostModePolicyOwnerError(
                    "HOST_MODE_POLICY_CANONICAL_HEAD_MISSING"
                )
            self._snapshot = current
        return HostModePolicyCommitV1(
            snapshot=self._snapshot,
            event=existing,
            exact_replay=True,
        )

    def _require_bound(self) -> None:
        """在部署要求耐久 authority 时禁止以 transient 内存快照继续工作。"""
        if self._require_durable and self._journal is None:
            raise HostModePolicyOwnerError(
                "HOST_MODE_POLICY_DURABLE_AUTHORITY_NOT_BOUND"
            )


__all__ = [
    "HostModePolicyOwner",
    "HostModePolicyOwnerError",
]
