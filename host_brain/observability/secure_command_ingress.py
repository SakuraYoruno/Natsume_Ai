"""OBS 命令面的失败关闭式 SecureIngress 适配层。

它把不可信 HTTP 原始请求收敛为一次带权威绑定、可去重且可恢复的领域命令；
任一安全依赖或持久化收据不可用时都拒绝执行，而不是降级为直连写入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hmac
import json
import threading
import time
from typing import Any, Callable, Mapping

from natsume_contracts.identity import CredentialProof, IngressIdentityEnvelope
from host_brain.secure_ingress import (
    IngressReasonCode,
    IngressSurface,
    M48B_P0_SURFACE_POLICIES,
    SecureIngressAdapter,
    SecureIngressMode,
    SecureIngressRequest,
    SecureIngressService,
    build_surface_request,
)

from .command_ports import (
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandContractError,
    ObsCommandEnvelope,
    ObsCommandPortRegistry,
    ObsCommandSpec,
    SecureIngressClaimResultStore,
    bind_server_authority,
    governed_review_continuation_ref,
)
from .command_outcome_journal import (
    ObsCommandOutcomeJournal,
    ObsCommandOutcomeKey,
    ObsCommandOutcomeRecord,
    ObsCommandOutcomeState,
    OutcomeBeginDisposition,
    SideEffectCertainty,
)
from .command_router import ObsCommandRouter, ObsCommandRoutingError


MAX_OBS_COMMAND_BODY_BYTES = 1_048_576
_ENVELOPE_FIELDS = frozenset({"schema_version", "command_id", "payload"})


@dataclass(frozen=True, slots=True)
class ObsCommandIngressRequest:
    command_id: str
    raw_body: bytes
    identity: IngressIdentityEnvelope | None
    credential: CredentialProof | None
    idempotency_key: str
    nonce: str
    received_at_epoch_ms: int
    transport_key: str
    method: str = "POST"
    normalized_path: str = ""
    correlation_id: str | None = None
    contract_error_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ObsCommandIngressOutcome:
    allowed: bool
    executed: bool
    duplicate: bool
    reason_code: str
    command_id: str | None = None
    principal_id: str | None = None
    claim_ref: str | None = None
    audit_ref: str | None = None
    result_ref: str | None = None
    result: Mapping[str, Any] | None = None
    terminal_state: str | None = None
    certainty: str | None = None


def build_obs_command_secure_request(
    request: ObsCommandIngressRequest,
    spec: ObsCommandSpec,
) -> SecureIngressRequest:
    """将原始字节绑定到 OBS 表面，并用服务端清单收紧 action/scopes。

    路径决定命令身份，JSON body 只在认证后作为该命令的载荷，避免调用方借
    body 中的 command_id 选择更高权限的策略。
    """

    command_id = str(request.command_id).strip()
    expected_path = f"/v1/host/obs-commands/{command_id}"
    if command_id != spec.command_id or request.normalized_path != expected_path:
        raise ObsCommandContractError("OBS_COMMAND_PATH_BINDING_INVALID")
    surface_request = build_surface_request(
        surface=IngressSurface.OBS,
        identity=request.identity,
        credential=request.credential,
        method=str(request.method).upper(),
        normalized_path=str(request.normalized_path),
        idempotency_key=str(request.idempotency_key),
        nonce=str(request.nonce),
        raw_body=bytes(request.raw_body),
        received_at_epoch_ms=request.received_at_epoch_ms,
        transport_key=str(request.transport_key),
        correlation_id=request.correlation_id,
    )
    # Generic OBS policy alone grants only obs:write.  Every command must pass
    # its manifest-owned action and domain scope through TASK-0487 as well.
    return replace(
        surface_request,
        action=spec.action,
        required_scopes=tuple(sorted(set(spec.required_scopes))),
    )


class SecureObsCommandIngress:
    """认证、授权并路由恰好一个类型化 OBS 命令。"""

    def __init__(
        self,
        *,
        ingress_adapter: SecureIngressAdapter | None,
        ingress_service: SecureIngressService | None,
        claim_result_store: SecureIngressClaimResultStore | None,
        outcome_journal: ObsCommandOutcomeJournal | None,
        registry: ObsCommandPortRegistry,
        router: ObsCommandRouter | None,
        clock_ms: Callable[[], int] | None = None,
        admission_open: bool = True,
    ) -> None:
        self._adapter = ingress_adapter
        self._service = ingress_service
        self._claim_results = claim_result_store
        self._outcomes = outcome_journal
        self._registry = registry
        self._router = router
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._admission = threading.Condition()
        self._admission_open = bool(admission_open)
        self._recovery_admission_pending = not bool(admission_open)
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        with self._admission:
            return self._in_flight

    def close_admission(self) -> None:
        """Atomically reject new requests without interrupting admitted work."""

        with self._admission:
            self._admission_open = False
            if self._in_flight == 0:
                self._admission.notify_all()

    def open_admission_after_recovery(self) -> None:
        """Open initial admission exactly once after the durable recovery gate."""

        with self._admission:
            if (
                not self._recovery_admission_pending
                or self._admission_open
                or self._in_flight != 0
            ):
                raise RuntimeError("OBS_COMMAND_RECOVERY_ADMISSION_STATE_INVALID")
            self._admission_open = True
            self._recovery_admission_pending = False

    async def close_admission_and_drain(self) -> None:
        """Close admission and asynchronously wait for admitted work to leave."""

        self.close_admission()
        await asyncio.to_thread(self._wait_until_drained)

    async def execute(self, request: ObsCommandIngressRequest) -> ObsCommandIngressOutcome:
        # 准入计数把关停与正在执行的副作用隔开：关闭后不再接收新命令，但已
        # 领取的命令必须自行落到确定或不确定收据，不能被中途强行取消。
        if not self._try_admit():
            return self._unavailable("OBS_COMMAND_INGRESS_CLOSED")
        try:
            return await self._execute_admitted(request)
        finally:
            self._leave_admission()

    async def _execute_admitted(
        self,
        request: ObsCommandIngressRequest,
    ) -> ObsCommandIngressOutcome:
        # 顺序不可交换：先验证 SecureIngress 对原始字节的结论，再解析 JSON，
        # 最后才绑定服务器权威并调用端口。这样解析器和领域实现永不接触未认证
        # 载荷，也不会把客户端提供的 principal 当作授权事实。
        if not isinstance(request, ObsCommandIngressRequest):
            return self._unavailable("OBS_COMMAND_REQUEST_INVALID")
        if self._outcomes is None:
            return self._unavailable("OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE")
        if (
            self._adapter is None
            or self._service is None
            or self._claim_results is None
            or self._router is None
        ):
            return self._unavailable("OBS_COMMAND_SECURE_DEPENDENCY_UNAVAILABLE")

        # command_id is selected by the server-owned URL path.  Resolve its
        # policy without looking at the attacker-controlled JSON body.
        command_id = str(request.command_id or "").strip()
        spec = self._registry.resolve_spec(command_id)
        if spec is None:
            return self._reject_unparsed(request, "OBS_COMMAND_NOT_ALLOWED")
        if str(request.contract_error_reason or "").strip():
            return self._reject_unparsed(
                request,
                str(request.contract_error_reason).strip(),
            )
        try:
            secure_request = build_obs_command_secure_request(request, spec)
        except ObsCommandContractError as exc:
            return self._reject_unparsed(request, exc.reason_code)
        except Exception:
            return self._reject_unparsed(request, "OBS_COMMAND_PATH_BINDING_INVALID")

        try:
            ingress = self._adapter.evaluate(secure_request)
        except Exception:
            return self._unavailable(
                "OBS_COMMAND_SECURE_INGRESS_UNAVAILABLE",
                command_id=command_id,
            )
        decision = ingress.decision
        if decision is None:
            return self._unavailable(
                "OBS_COMMAND_SECURE_INGRESS_DISABLED",
                command_id=command_id,
            )
        principal_id = secure_request.principal_id
        secure_duplicate = (
            decision.reason_code is IngressReasonCode.IDEMPOTENCY_DUPLICATE
        )
        if not secure_duplicate and not decision.allowed:
            return ObsCommandIngressOutcome(
                allowed=False,
                executed=False,
                duplicate=False,
                reason_code=decision.reason_code.value,
                command_id=command_id,
                principal_id=principal_id,
                claim_ref=decision.claim_ref,
                audit_ref=decision.audit_ref,
                result_ref=decision.existing_result_ref,
            )
        identity = secure_request.identity
        if identity is None or not decision.claim_ref or not decision.audit_ref:
            return self._unavailable(
                "OBS_COMMAND_AUTHORITY_UNAVAILABLE",
                command_id=command_id,
            )

        # Only authenticated raw bytes are parsed.  Keep the parse failure until
        # after the durable key/begin step so definite payload rejection also
        # receives an immutable outcome receipt.
        payload: dict[str, Any] | None = None
        payload_error: str | None = None
        try:
            payload = self._parse_authenticated_envelope(
                request.raw_body,
                command_id=command_id,
                spec=spec,
            )
        except ObsCommandContractError as exc:
            payload_error = exc.reason_code
        except Exception:
            payload_error = "OBS_COMMAND_ENVELOPE_INVALID"

        try:
            outcome_key = ObsCommandOutcomeKey.from_values(
                principal_id=identity.principal.principal_id,
                tenant_id=identity.principal.tenant_id,
                idempotency_key=secure_request.idempotency_key,
                command_id=command_id,
                action=spec.action,
                required_scopes=spec.required_scopes,
                body_sha256=secure_request.body_sha256,
            )
        except Exception:
            return self._unavailable(
                "OBS_COMMAND_OUTCOME_KEY_INVALID",
                command_id=command_id,
            )

        try:
            existing = self._outcomes.lookup(outcome_key)
        except Exception:
            return self._unavailable(
                "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
                command_id=command_id,
            )

        mode = self._adapter.mode
        if (
            secure_duplicate
            and existing is None
            and decision.existing_result_ref is not None
        ):
            return self._unavailable(
                "OBS_COMMAND_OUTCOME_RECEIPT_MISSING",
                command_id=command_id,
            )

        if mode is SecureIngressMode.SHADOW:
            # 影子模式只记录“若强制执行会怎样”的稳定收据，绝不能穿透到领域端口；
            # 同一幂等键之后转 ENFORCE 时再以显式 PROMOTE 事件继续。
            if existing is not None:
                return self._outcome_from_record(existing, duplicate=True)
            if payload_error is not None:
                try:
                    rejected = self._outcomes.record_authenticated_rejection(
                        outcome_key,
                        claim_ref=decision.claim_ref,
                        audit_ref=decision.audit_ref,
                        reason_code=payload_error,
                        recorded_at_ms=int(self._clock_ms()),
                    )
                except Exception:
                    return self._unavailable(
                        "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
                        command_id=command_id,
                    )
                if rejected.disposition is OutcomeBeginDisposition.CONFLICT:
                    return self._outcome_conflict(command_id, principal_id)
                return self._outcome_from_record(
                    rejected.record,
                    duplicate=(
                        rejected.disposition
                        is OutcomeBeginDisposition.EXISTING_SAME
                    ),
                )
            try:
                begin = self._outcomes.begin(
                    outcome_key,
                    claim_ref=decision.claim_ref,
                    audit_ref=decision.audit_ref,
                    shadow=True,
                    recorded_at_ms=int(self._clock_ms()),
                )
            except Exception:
                return self._unavailable(
                    "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
                    command_id=command_id,
                )
            if begin.disposition is OutcomeBeginDisposition.CONFLICT:
                return self._outcome_conflict(command_id, principal_id)
            return self._outcome_from_record(
                begin.record,
                duplicate=begin.disposition is not OutcomeBeginDisposition.NEW,
            )

        if mode is not SecureIngressMode.ENFORCE or (
            not secure_duplicate and not ingress.permit_execution
        ):
            return self._unavailable(
                "OBS_COMMAND_SECURE_INGRESS_DISABLED",
                command_id=command_id,
            )

        begin_record: ObsCommandOutcomeRecord
        recovering_missing_receipt = secure_duplicate and existing is None
        if existing is not None:
            # 收据优先于重试：已有终态永远重放，影子收据只能通过 journal 的受控
            # promote 转为执行中，防止切换模式时同一命令出现两次副作用。
            if existing.state is not ObsCommandOutcomeState.SHADOW_EVALUATED:
                return self._replay_existing_outcome(
                    existing,
                    claim_ref=decision.claim_ref,
                    principal_id=principal_id,
                    central_result_ref=decision.existing_result_ref,
                )
            try:
                promoted = self._outcomes.begin(
                    outcome_key,
                    claim_ref=decision.claim_ref,
                    audit_ref=decision.audit_ref,
                    promote_shadow=True,
                    recorded_at_ms=int(self._clock_ms()),
                )
            except Exception:
                return self._unavailable(
                    "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
                    command_id=command_id,
                )
            if promoted.disposition is OutcomeBeginDisposition.CONFLICT:
                if promoted.record.key == outcome_key:
                    return self._replay_existing_outcome(
                        promoted.record,
                        claim_ref=decision.claim_ref,
                        principal_id=principal_id,
                        central_result_ref=decision.existing_result_ref,
                    )
                return self._outcome_conflict(command_id, principal_id)
            if promoted.disposition is not OutcomeBeginDisposition.PROMOTED:
                return self._replay_existing_outcome(
                    promoted.record,
                    claim_ref=decision.claim_ref,
                    principal_id=principal_id,
                    central_result_ref=decision.existing_result_ref,
                )
            begin_record = promoted.record
        else:
            try:
                begun = self._outcomes.begin(
                    outcome_key,
                    claim_ref=decision.claim_ref,
                    audit_ref=decision.audit_ref,
                    recorded_at_ms=int(self._clock_ms()),
                )
            except Exception:
                return self._unavailable(
                    "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
                    command_id=command_id,
                )
            if begun.disposition is OutcomeBeginDisposition.CONFLICT:
                if begun.record.key == outcome_key:
                    return self._replay_existing_outcome(
                        begun.record,
                        claim_ref=decision.claim_ref,
                        principal_id=principal_id,
                        central_result_ref=decision.existing_result_ref,
                    )
                return self._outcome_conflict(command_id, principal_id)
            if begun.disposition is not OutcomeBeginDisposition.NEW:
                return self._replay_existing_outcome(
                    begun.record,
                    claim_ref=decision.claim_ref,
                    principal_id=principal_id,
                    central_result_ref=decision.existing_result_ref,
                )
            begin_record = begun.record

        if recovering_missing_receipt:
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.UNCERTAIN,
                reason_code="OBS_COMMAND_CLAIM_RECEIPT_RECOVERED_UNCERTAIN",
                certainty=SideEffectCertainty.MAY_HAVE_EXECUTED,
                principal_id=principal_id,
            )

        if payload_error is not None or payload is None:
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.REJECTED,
                reason_code=payload_error or "OBS_COMMAND_ENVELOPE_INVALID",
                certainty=SideEffectCertainty.NOT_EXECUTED,
                principal_id=principal_id,
            )

        try:
            authority = ObsCommandAuthority.bind(
                principal_id=identity.principal.principal_id,
                tenant_id=identity.principal.tenant_id,
                request_id=identity.request_id,
            )
            typed_envelope = ObsCommandEnvelope(
                schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
                command_id=command_id,
                action=spec.action,
                required_scopes=spec.required_scopes,
                payload=bind_server_authority(payload, spec=spec, authority=authority),
                authority=authority,
                claim_ref=decision.claim_ref,
                audit_ref=decision.audit_ref,
                body_sha256=secure_request.body_sha256,
                correlation_id=request.correlation_id,
            )
        except Exception:
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.REJECTED,
                reason_code="OBS_COMMAND_BOUND_PAYLOAD_INVALID",
                certainty=SideEffectCertainty.NOT_EXECUTED,
                principal_id=principal_id,
            )

        try:
            execution = await self._router.route(typed_envelope)
        except ObsCommandRoutingError as exc:
            if (
                not exc.side_effects_may_have_run
                and exc.reason_code != "OBS_COMMAND_RESULT_INVALID"
            ):
                return self._finalize_outcome(
                    begin_record,
                    state=ObsCommandOutcomeState.REJECTED,
                    reason_code=exc.reason_code,
                    certainty=SideEffectCertainty.NOT_EXECUTED,
                    principal_id=authority.principal_id,
                )
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.UNCERTAIN,
                reason_code=exc.reason_code,
                certainty=SideEffectCertainty.MAY_HAVE_EXECUTED,
                principal_id=authority.principal_id,
            )
        except Exception:
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.UNCERTAIN,
                reason_code="OBS_COMMAND_EXECUTION_UNCERTAIN",
                certainty=SideEffectCertainty.MAY_HAVE_EXECUTED,
                principal_id=authority.principal_id,
            )

        try:
            continuation_ref = governed_review_continuation_ref(execution)
        except ObsCommandContractError as exc:
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.UNCERTAIN,
                reason_code=exc.reason_code,
                certainty=SideEffectCertainty.MAY_HAVE_EXECUTED,
                principal_id=authority.principal_id,
            )
        if continuation_ref is not None:
            return self._mark_waiting_review(
                begin_record,
                domain_result_ref=execution.result_ref,
                continuation_ref=continuation_ref,
                principal_id=authority.principal_id,
            )

        if execution.success:
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.SUCCEEDED,
                reason_code="OBS_COMMAND_COMPLETED",
                certainty=(
                    SideEffectCertainty.EXECUTED
                    if execution.side_effects_executed
                    else SideEffectCertainty.NOT_EXECUTED
                ),
                principal_id=authority.principal_id,
                domain_result_ref=execution.result_ref,
            )
        if execution.side_effects_executed:
            return self._finalize_outcome(
                begin_record,
                state=ObsCommandOutcomeState.REJECTED,
                reason_code="OBS_COMMAND_DOMAIN_REJECTED",
                certainty=SideEffectCertainty.EXECUTED,
                principal_id=authority.principal_id,
                domain_result_ref=execution.result_ref,
            )
        return self._finalize_outcome(
            begin_record,
            state=ObsCommandOutcomeState.REJECTED,
            reason_code="OBS_COMMAND_DOMAIN_REJECTED",
            certainty=SideEffectCertainty.NOT_EXECUTED,
            principal_id=authority.principal_id,
        )

    def finalize_review_outcome(
        self,
        *,
        claim_ref: str,
        state: str,
        reason_code: str,
        side_effects_executed: bool,
        terminal_result_ref: str,
        continuation_ref: str,
    ) -> ObsCommandIngressOutcome:
        """追加 Review 终态后再绑定中央 claim。

        The canonical DomainCommand result remains ``domain_result_ref``;
        Task/Review settlement is retained separately as ``terminal_result_ref``.
        Secure Ingress always receives the stable outer
        ``obs-command-outcome://`` receipt, including an exact retry after a
        crash between the WAL append and central claim completion.
        """

        if self._outcomes is None or self._claim_results is None:
            raise RuntimeError("OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE")
        normalized_state = str(state or "").strip().upper()
        if normalized_state not in {"SUCCEEDED", "REJECTED"}:
            raise ValueError("OBS_COMMAND_REVIEW_FINAL_STATE_INVALID")
        if type(side_effects_executed) is not bool:
            raise ValueError("OBS_COMMAND_REVIEW_CERTAINTY_INVALID")
        record = self._outcomes.lookup_by_claim_ref(str(claim_ref or "").strip())
        if record is None:
            raise RuntimeError("OBS_COMMAND_REVIEW_OUTCOME_NOT_FOUND")
        waiting = self._outcomes.lookup_waiting_review_by_claim_ref(
            str(claim_ref or "").strip()
        )
        expected_continuation = str(continuation_ref or "").strip()
        if (
            waiting is None
            or waiting.receipt_ref != record.receipt_ref
            or waiting.continuation_ref is None
            or not hmac.compare_digest(
                waiting.continuation_ref,
                expected_continuation,
            )
        ):
            raise RuntimeError("OBS_COMMAND_REVIEW_CONTINUATION_BINDING_DRIFT")
        terminal = self._outcomes.finalize(
            record.receipt_ref,
            state=ObsCommandOutcomeState(normalized_state),
            reason_code=str(reason_code or "").strip().upper(),
            side_effect_certainty=(
                SideEffectCertainty.EXECUTED
                if side_effects_executed
                else SideEffectCertainty.NOT_EXECUTED
            ),
            domain_result_ref=waiting.domain_result_ref,
            terminal_result_ref=terminal_result_ref,
            recorded_at_ms=int(self._clock_ms()),
        )
        if terminal.claim_ref != str(claim_ref or "").strip():
            raise RuntimeError("OBS_COMMAND_REVIEW_CLAIM_BINDING_DRIFT")
        self._claim_results.complete_claim(
            claim_ref=terminal.claim_ref,
            result_ref=terminal.receipt_ref,
            at_ms=int(self._clock_ms()),
        )
        return self._outcome_from_record(terminal, duplicate=False)

    def _parse_authenticated_envelope(
        self,
        raw_body: bytes,
        *,
        command_id: str,
        spec: ObsCommandSpec,
    ) -> dict[str, Any]:
        # raw_body 的完整性已由 SecureIngress 覆盖；这里仍采用严格 JSON 与字段集
        # 校验，拒绝重复键、NaN 和路径/body 命令漂移，确保签名的字节语义唯一。
        if (
            not isinstance(raw_body, bytes)
            or not raw_body
            or len(raw_body) > MAX_OBS_COMMAND_BODY_BYTES
        ):
            raise ObsCommandContractError("OBS_COMMAND_BODY_INVALID")
        try:
            decoded = json.loads(
                raw_body.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_nonfinite,
            )
        except ObsCommandContractError:
            raise
        except Exception as exc:
            raise ObsCommandContractError("OBS_COMMAND_ENVELOPE_INVALID") from exc
        if not isinstance(decoded, dict) or set(decoded) != _ENVELOPE_FIELDS:
            raise ObsCommandContractError("OBS_COMMAND_ENVELOPE_INVALID")
        if decoded.get("schema_version") != OBS_COMMAND_ENVELOPE_SCHEMA:
            raise ObsCommandContractError("OBS_COMMAND_SCHEMA_UNSUPPORTED")
        body_command_id = str(decoded.get("command_id") or "").strip()
        if body_command_id != command_id or spec.command_id != command_id:
            raise ObsCommandContractError("OBS_COMMAND_PATH_BODY_MISMATCH")
        payload = decoded.get("payload")
        if not isinstance(payload, dict):
            raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_INVALID")
        return spec.payload_schema.validate(payload)

    def _replay_existing_outcome(
        self,
        record: ObsCommandOutcomeRecord,
        *,
        claim_ref: str,
        principal_id: str | None,
        central_result_ref: str | None,
    ) -> ObsCommandIngressOutcome:
        terminal_states = {
            ObsCommandOutcomeState.SUCCEEDED,
            ObsCommandOutcomeState.REJECTED,
            ObsCommandOutcomeState.UNCERTAIN,
        }
        if record.state in terminal_states:
            if self._claim_results is None:
                return self._unavailable(
                    "OBS_COMMAND_RESULT_COMMIT_FAILED",
                    command_id=record.key.command_id,
                )
            try:
                if central_result_ref is None:
                    self._claim_results.complete_claim(
                        claim_ref=claim_ref,
                        result_ref=record.receipt_ref,
                        at_ms=int(self._clock_ms()),
                    )
                elif central_result_ref != record.receipt_ref:
                    raise RuntimeError("central result conflicts with outcome receipt")
            except Exception:
                return ObsCommandIngressOutcome(
                    allowed=False,
                    executed=self._certainty_executed(record.side_effect_certainty),
                    duplicate=True,
                    reason_code="OBS_COMMAND_RESULT_COMMIT_FAILED",
                    command_id=record.key.command_id,
                    principal_id=principal_id,
                    claim_ref=claim_ref,
                    audit_ref=record.audit_ref,
                    result_ref=record.receipt_ref,
                    terminal_state=record.state.value,
                    certainty=record.side_effect_certainty.value,
                )
        return replace(
            self._outcome_from_record(record, duplicate=True),
            principal_id=principal_id,
            claim_ref=claim_ref,
        )

    def _finalize_outcome(
        self,
        begin_record: ObsCommandOutcomeRecord,
        *,
        state: ObsCommandOutcomeState,
        reason_code: str,
        certainty: SideEffectCertainty,
        principal_id: str | None,
        domain_result_ref: str | None = None,
    ) -> ObsCommandIngressOutcome:
        # 先让 append-only 收据成为重试的事实来源，再尝试补齐中央 claim。后一步
        # 崩溃只会得到可恢复的“收据已存在”，不能重新路由领域副作用。
        if self._outcomes is None or self._claim_results is None:
            return self._unavailable(
                "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
                command_id=begin_record.key.command_id,
            )
        try:
            final_record = self._outcomes.finalize(
                begin_record.receipt_ref,
                state=state,
                reason_code=reason_code,
                side_effect_certainty=certainty,
                domain_result_ref=domain_result_ref,
                recorded_at_ms=int(self._clock_ms()),
            )
        except Exception:
            return ObsCommandIngressOutcome(
                allowed=False,
                executed=True,
                duplicate=False,
                reason_code="OBS_COMMAND_OUTCOME_FINALIZE_FAILED",
                command_id=begin_record.key.command_id,
                principal_id=principal_id,
                claim_ref=begin_record.claim_ref,
                audit_ref=begin_record.audit_ref,
                result_ref=begin_record.receipt_ref,
                terminal_state=ObsCommandOutcomeState.UNCERTAIN.value,
                certainty=SideEffectCertainty.MAY_HAVE_EXECUTED.value,
            )
        if not final_record.claim_ref:
            return self._unavailable(
                "OBS_COMMAND_RESULT_COMMIT_FAILED",
                command_id=final_record.key.command_id,
            )
        try:
            self._claim_results.complete_claim(
                claim_ref=final_record.claim_ref,
                result_ref=final_record.receipt_ref,
                at_ms=int(self._clock_ms()),
            )
        except Exception:
            return ObsCommandIngressOutcome(
                allowed=False,
                executed=self._certainty_executed(final_record.side_effect_certainty),
                duplicate=False,
                reason_code="OBS_COMMAND_RESULT_COMMIT_FAILED",
                command_id=final_record.key.command_id,
                principal_id=principal_id,
                claim_ref=final_record.claim_ref,
                audit_ref=final_record.audit_ref,
                result_ref=final_record.receipt_ref,
                terminal_state=final_record.state.value,
                certainty=final_record.side_effect_certainty.value,
            )
        return self._outcome_from_record(final_record, duplicate=False)

    def _mark_waiting_review(
        self,
        begin_record: ObsCommandOutcomeRecord,
        *,
        domain_result_ref: str,
        continuation_ref: str,
        principal_id: str | None,
    ) -> ObsCommandIngressOutcome:
        # Review 等待态保留外层幂等键和 continuation 绑定，但刻意不完成中央 claim；
        # 只有后续复核得到终态，调用方才可把这次请求视为真正完成。
        if self._outcomes is None:
            return self._unavailable(
                "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
                command_id=begin_record.key.command_id,
            )
        try:
            waiting = self._outcomes.mark_waiting_review(
                begin_record.receipt_ref,
                domain_result_ref=domain_result_ref,
                continuation_ref=continuation_ref,
                recorded_at_ms=int(self._clock_ms()),
            )
        except Exception:
            # The graph is durably waiting and no public effect ran, but the
            # outer WAL did not commit its pending transition.  Keep the claim
            # open and surface an executing/uncertain transport result; never
            # manufacture a terminal claim receipt.
            return ObsCommandIngressOutcome(
                allowed=False,
                executed=False,
                duplicate=False,
                reason_code="OBS_COMMAND_OUTCOME_FINALIZE_FAILED",
                command_id=begin_record.key.command_id,
                principal_id=principal_id,
                claim_ref=begin_record.claim_ref,
                audit_ref=begin_record.audit_ref,
                result_ref=begin_record.receipt_ref,
                terminal_state=ObsCommandOutcomeState.EXECUTING.value,
                certainty=SideEffectCertainty.NOT_EXECUTED.value,
            )
        return replace(
            self._outcome_from_record(waiting, duplicate=False),
            principal_id=principal_id,
        )

    def _outcome_from_record(
        self,
        record: ObsCommandOutcomeRecord,
        *,
        duplicate: bool,
    ) -> ObsCommandIngressOutcome:
        state = record.state
        certainty = record.side_effect_certainty
        reason_code = record.reason_code
        terminal_state = state
        if state is ObsCommandOutcomeState.EXECUTING:
            reason_code = "OBS_COMMAND_EXECUTION_UNCERTAIN"
            terminal_state = ObsCommandOutcomeState.UNCERTAIN
            certainty = SideEffectCertainty.MAY_HAVE_EXECUTED
        return ObsCommandIngressOutcome(
            allowed=terminal_state
            in {
                ObsCommandOutcomeState.SUCCEEDED,
                ObsCommandOutcomeState.WAITING_REVIEW,
            },
            executed=(False if duplicate else self._certainty_executed(certainty)),
            duplicate=bool(duplicate),
            reason_code=reason_code,
            command_id=record.key.command_id,
            claim_ref=record.claim_ref,
            audit_ref=record.audit_ref,
            result_ref=record.receipt_ref,
            result=(
                {"continuation_ref": record.continuation_ref}
                if state is ObsCommandOutcomeState.WAITING_REVIEW
                and record.continuation_ref is not None
                else None
            ),
            terminal_state=terminal_state.value,
            certainty=certainty.value,
        )

    @staticmethod
    def _certainty_executed(certainty: SideEffectCertainty) -> bool:
        return certainty in {
            SideEffectCertainty.EXECUTED,
            SideEffectCertainty.MAY_HAVE_EXECUTED,
        }

    @staticmethod
    def _outcome_conflict(
        command_id: str,
        principal_id: str | None,
    ) -> ObsCommandIngressOutcome:
        return ObsCommandIngressOutcome(
            allowed=False,
            executed=False,
            duplicate=False,
            reason_code="OBS_COMMAND_OUTCOME_CONFLICT",
            command_id=command_id,
            principal_id=principal_id,
            terminal_state=ObsCommandOutcomeState.REJECTED.value,
            certainty=SideEffectCertainty.NOT_EXECUTED.value,
        )

    def _reject_unparsed(
        self,
        request: ObsCommandIngressRequest,
        fallback_reason: str,
    ) -> ObsCommandIngressOutcome:
        if self._service is None:
            return self._unavailable("OBS_COMMAND_SECURE_INGRESS_UNAVAILABLE")
        policy = M48B_P0_SURFACE_POLICIES[IngressSurface.OBS]
        try:
            decision = self._service.reject_unparsed_attempt(
                transport_key=request.transport_key,
                surface=policy.surface_id,
                method=str(request.method).upper(),
                normalized_path=request.normalized_path,
                action=policy.action,
                required_scopes=policy.required_scopes,
                received_at_epoch_ms=request.received_at_epoch_ms,
                correlation_id=request.correlation_id,
                claimed_principal_id=(
                    request.identity.principal.principal_id
                    if request.identity is not None
                    else None
                ),
                key_id=(
                    request.identity.credential_proof.key_id
                    if request.identity is not None
                    else None
                ),
            )
        except Exception:
            return self._unavailable("OBS_COMMAND_SECURE_INGRESS_UNAVAILABLE")
        identity = request.identity
        return ObsCommandIngressOutcome(
            allowed=False,
            executed=False,
            duplicate=False,
            reason_code=(
                fallback_reason
                if decision.reason_code is IngressReasonCode.SCHEMA_INVALID
                else decision.reason_code.value
            ),
            command_id=str(request.command_id or "").strip() or None,
            principal_id=(
                identity.principal.principal_id if identity is not None else None
            ),
            audit_ref=decision.audit_ref,
        )

    def _try_admit(self) -> bool:
        with self._admission:
            if not self._admission_open:
                return False
            self._in_flight += 1
            return True

    def _leave_admission(self) -> None:
        with self._admission:
            self._in_flight -= 1
            if self._in_flight == 0:
                self._admission.notify_all()

    def _wait_until_drained(self) -> None:
        with self._admission:
            while self._in_flight:
                self._admission.wait()

    @staticmethod
    def _unavailable(
        reason_code: str,
        *,
        command_id: str | None = None,
    ) -> ObsCommandIngressOutcome:
        return ObsCommandIngressOutcome(
            allowed=False,
            executed=False,
            duplicate=False,
            reason_code=reason_code,
            command_id=command_id,
        )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        normalized = str(key)
        if normalized in result:
            raise ObsCommandContractError("OBS_COMMAND_DUPLICATE_FIELD")
        result[normalized] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ObsCommandContractError("OBS_COMMAND_NONFINITE_VALUE")


__all__ = [
    "MAX_OBS_COMMAND_BODY_BYTES",
    "ObsCommandIngressOutcome",
    "ObsCommandIngressRequest",
    "SecureObsCommandIngress",
    "build_obs_command_secure_request",
]
