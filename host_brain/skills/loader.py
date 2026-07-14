"""Single legal skill execution entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import SKILL_TOKEN_MISSING, TOKEN_INVALID, UNDO_REQUIRED
from host_brain.core.ids import new_event_id, now_ms
from host_brain.governor.tier_policy import AuditTier
from host_brain.governor.tokens import TokenService
from host_brain.observability.degradation_state import (
    evaluate_degradation_front_door,
    infer_degradation_capability,
)
from host_brain.skills.executor_registry import (
    BoundSkillExecutionAuthority,
    bind_validated_skill_authority,
    dispatch_executor_payload,
)
from host_brain.skills.registry import SkillRegistry


@dataclass(frozen=True)
class SkillExecutionResult:
    status: str
    result_code: str
    skill_name: str
    intent_id: str
    evidence_ref: str | None
    manifest_ref: str | None
    undo_token_ref: str | None
    detail: dict[str, Any]
    runtime_payload: dict[str, Any] | None = None


class SkillLoader:
    """Token-gated and auditable skill executor."""

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        token_service: TokenService,
        ledger: SQLiteLedger,
        capability_broker: Any | None = None,
    ) -> None:
        self.registry = registry
        self.token_service = token_service
        self.ledger = ledger
        self.capability_broker = capability_broker

    def execute(
        self,
        *,
        intent: dict[str, Any],
        governor_output: dict[str, Any],
        context: dict[str, Any] | None = None,
        now_ts_ms: int | None = None,
    ) -> SkillExecutionResult:
        now_value = now_ts_ms if now_ts_ms is not None else now_ms()
        request_id = str(
            intent.get("request_id")
            or governor_output.get("request_id")
            or ""
        )
        thread_id = intent.get("thread_id") or governor_output.get("thread_id")
        turn_id = intent.get("turn_id") or governor_output.get("turn_id")
        intent_id = str(intent.get("id", ""))
        session_id = str(intent.get("session_id", ""))
        seq = int(intent.get("seq", 0))
        skill_name = str(intent.get("action", ""))
        args = intent.get("args") if isinstance(intent.get("args"), dict) else {}

        meta = self.registry.require(skill_name)
        if governor_output.get("decision") != "APPROVE":
            return self._log_and_return(
                intent_id=intent_id,
                skill_name=skill_name,
                approval_token_ref=None,
                confirm_token_ref=None,
                result_status="REJECTED",
                result_code="GOVERNOR_REJECTED",
                result_digest="execution blocked by governor",
                evidence_ref=None,
                manifest_ref=None,
                undo_token_ref=None,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                ts_ms=now_value,
            )

        tokens = governor_output.get("tokens")
        approval_token_ref = None
        confirm_token_ref = None
        if isinstance(tokens, dict):
            approval_token_ref = tokens.get("approval_token_ref")
            confirm_token_ref = tokens.get("confirm_token_ref")

        if not approval_token_ref:
            return self._log_and_return(
                intent_id=intent_id,
                skill_name=skill_name,
                approval_token_ref=None,
                confirm_token_ref=confirm_token_ref,
                result_status="REJECTED",
                result_code=SKILL_TOKEN_MISSING,
                result_digest="approval token missing",
                evidence_ref=None,
                manifest_ref=None,
                undo_token_ref=None,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                ts_ms=now_value,
            )

        # Check degradation before validating/consuming one-time execution
        # tokens.  A dependency failure must neither invoke the handler nor
        # burn authority that was never exercised.
        capability_id = infer_degradation_capability(
            intent.get("capability_id"),
            skill_name,
            meta.scope,
            meta.family,
            meta.capability_tags,
            default=(
                "high_risk_execution"
                if int(meta.risk_level) >= 3
                else "general_execution"
            ),
        )
        capability_policy_decision = governor_output.get(
            "capability_policy_decision"
        )
        if not isinstance(capability_policy_decision, dict):
            capability_policy_decision = intent.get("capability_policy_decision")
        degradation_decision = evaluate_degradation_front_door(
            capability_id=capability_id,
            static_risk_level=int(meta.risk_level),
            capability_policy_decision=(
                capability_policy_decision
                if isinstance(capability_policy_decision, dict)
                else None
            ),
        )
        if not bool(
            degradation_decision.get(
                "allowed",
                degradation_decision.get("allowed_by_degradation", False),
            )
        ):
            reason_code = str(
                degradation_decision.get("reason_code")
                or "DEGRADATION_STATE_UNAVAILABLE"
            )
            return self._log_and_return(
                intent_id=intent_id,
                skill_name=skill_name,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                result_status="REJECTED",
                result_code=reason_code,
                result_digest=(
                    f"degradation gate blocked {capability_id}: {reason_code}"
                ),
                evidence_ref=None,
                manifest_ref=None,
                undo_token_ref=None,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                ts_ms=now_value,
                governor_output=governor_output,
                runtime_payload={"degradation_decision": degradation_decision},
            )

        token_result = self.token_service.validate_execution_tokens(
            intent_id=intent_id,
            skill_name=skill_name,
            session_id=session_id,
            seq=seq,
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            require_confirm=meta.require_confirm,
            now_ts_ms=now_value,
        )
        if not token_result.valid:
            return self._log_and_return(
                intent_id=intent_id,
                skill_name=skill_name,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                result_status="REJECTED",
                result_code=token_result.reason_code or TOKEN_INVALID,
                result_digest=token_result.detail or "token validation failed",
                evidence_ref=None,
                manifest_ref=None,
                undo_token_ref=None,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                ts_ms=now_value,
            )

        if self.ledger.has_skill_exec_for_intent(intent_id=intent_id):
            return self._log_and_return(
                intent_id=intent_id,
                skill_name=skill_name,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                result_status="IDEMPOTENT_REPLAY",
                result_code="NOOP_ALREADY_EXECUTED",
                result_digest="same intent_id already executed",
                evidence_ref=None,
                manifest_ref=None,
                undo_token_ref=None,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                ts_ms=now_value,
            )

        try:
            # M27-I：context 注入情感字段（OPT-37）
            exec_context = dict(context or {})
            if "emotion_tag" not in exec_context:
                exec_context["emotion_tag"] = "NEUTRAL"
            if "mood_state" not in exec_context:
                exec_context["mood_state"] = "steady"
            # 第一步：归一化层，handler 返回标准化 payload
            payload = meta.handler(args, exec_context)
            # 第二步：执行层，将 payload 路由到对应的 impl executor
            if not isinstance(payload, dict):
                raise ValueError("skill handler must return an executor payload object")
            if not str(payload.get("tool") or "").strip():
                handler_result = payload
            else:
                dispatch_authority = bind_validated_skill_authority(
                    payload=payload,
                    request_id=request_id or intent_id,
                    skill_id=skill_name,
                    principal_id=str(
                        exec_context.get("principal_id")
                        or exec_context.get("person_id")
                        or session_id
                    ),
                    target_id=skill_name,
                    intent_id=intent_id,
                    session_id=session_id,
                    seq=seq,
                    approval_token_ref=approval_token_ref,
                    # Loader context is caller-controlled. Only its token,
                    # already checked above by TokenService, may authorize.
                    governed_execution_context=None,
                    durable_ledger=self.ledger,
                    validated_by="skill_loader.token_service",
                )
                handler_result = _dispatch_executor(
                    payload,
                    authority=dispatch_authority,
                    capability_broker=self.capability_broker,
                )
        except Exception as exc:  # pragma: no cover - defensive guard
            return self._log_and_return(
                intent_id=intent_id,
                skill_name=skill_name,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                result_status="FAILED",
                result_code="SKILL_EXECUTION_ERROR",
                result_digest=str(exc),
                evidence_ref=None,
                manifest_ref=None,
                undo_token_ref=None,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                ts_ms=now_value,
            )

        evidence_ref = f"evidence::{new_event_id()}"
        manifest_ref = None
        if isinstance(handler_result, dict):
            manifest_ref = handler_result.get("manifest_ref")
            handler_status = str(handler_result.get("status") or "").strip().upper()
            if handler_status in {"REJECTED", "FAILED", "DEGRADED"}:
                mapped_status = "REJECTED" if handler_status == "REJECTED" else "FAILED"
                mapped_code = str(handler_result.get("reason_code") or ("SKILL_DEGRADED" if handler_status == "DEGRADED" else "SKILL_EXECUTION_FAILED")).strip() or "SKILL_EXECUTION_FAILED"
                mapped_digest = str(handler_result.get("detail") or handler_result)
                return self._log_and_return(
                    intent_id=intent_id,
                    skill_name=skill_name,
                    approval_token_ref=approval_token_ref,
                    confirm_token_ref=confirm_token_ref,
                    result_status=mapped_status,
                    result_code=mapped_code,
                    result_digest=mapped_digest,
                    evidence_ref=None,
                    manifest_ref=manifest_ref,
                    undo_token_ref=None,
                    request_id=request_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    ts_ms=now_value,
                    governor_output=governor_output,
                    runtime_payload=dict(handler_result),
                )

        undo_token_ref = None
        if meta.reversible:
            # 修复：先检查 manifest_ref，再签发 undo_token。
            # 原逻辑先调用 issue_undo_token() 再检查 manifest_ref，
            # 导致 manifest_ref 缺失时 undo_token 已写入 token store 但永远不会被消费（token 泄漏）。
            if not manifest_ref:
                return self._log_and_return(
                    intent_id=intent_id,
                    skill_name=skill_name,
                    approval_token_ref=approval_token_ref,
                    confirm_token_ref=confirm_token_ref,
                    result_status="REJECTED",
                    result_code=UNDO_REQUIRED,
                    result_digest="reversible execution requires manifest_ref",
                    evidence_ref=None,
                    manifest_ref=None,
                    undo_token_ref=None,
                    request_id=request_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    ts_ms=now_value,
                )
            undo_token_ref = self.token_service.issue_undo_token(
                intent_id=intent_id,
                skill_name=skill_name,
                session_id=session_id,
                seq=seq,
                now_ts_ms=now_value,
            )

        return self._log_and_return(
            intent_id=intent_id,
            skill_name=skill_name,
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            result_status="SUCCESS",
            result_code="SKILL_EXECUTED",
            result_digest=str(handler_result),
            evidence_ref=evidence_ref,
            manifest_ref=manifest_ref,
            undo_token_ref=undo_token_ref,
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            ts_ms=now_value,
            governor_output=governor_output,
            runtime_payload=dict(handler_result) if isinstance(handler_result, dict) else None,
        )

    def _log_and_return(
        self,
        *,
        intent_id: str,
        skill_name: str,
        approval_token_ref: str | None,
        confirm_token_ref: str | None,
        result_status: str,
        result_code: str,
        result_digest: str,
        evidence_ref: str | None,
        manifest_ref: str | None,
        undo_token_ref: str | None,
        request_id: str,
        thread_id: str | None,
        turn_id: str | None,
        ts_ms: int,
        governor_output: dict[str, Any] | None = None,
        runtime_payload: dict[str, Any] | None = None,
    ) -> SkillExecutionResult:
        # M42：分层审计——根据 approval_tier 选择审计级别
        audit_tier = _extract_audit_tier(governor_output)

        row: dict[str, Any] = {
            "request_id": request_id or None,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "intent_id": intent_id,
            "skill_name": skill_name,
            "result_status": result_status,
            "result_code": result_code,
            "ts_ms": ts_ms,
        }

        if audit_tier == AuditTier.LIGHT:
            # LIGHT 级：只写基础字段（不写 token/evidence/manifest 详情）
            row["approval_token_ref"] = approval_token_ref
        else:
            # STANDARD / FULL_AUDIT：写完整字段
            row["approval_token_ref"] = approval_token_ref
            row["confirm_token_ref"] = confirm_token_ref
            row["result_digest"] = result_digest
            row["evidence_ref"] = evidence_ref
            row["manifest_ref"] = manifest_ref
            row["undo_token_ref"] = undo_token_ref
            if audit_tier == AuditTier.FULL_AUDIT:
                row["audit_tier"] = "full_audit"

        self.ledger.log_skill_exec(row)
        return SkillExecutionResult(
            status=result_status,
            result_code=result_code,
            skill_name=skill_name,
            intent_id=intent_id,
            evidence_ref=evidence_ref,
            manifest_ref=manifest_ref,
            undo_token_ref=undo_token_ref,
            detail=row,
            runtime_payload=dict(runtime_payload) if isinstance(runtime_payload, dict) else None,
        )


# ---------------------------------------------------------------------------
def _dispatch_executor(
    payload: Any,
    *,
    authority: BoundSkillExecutionAuthority | None = None,
    capability_broker: Any,
) -> Any:
    """将 handler 返回的归一化 payload 路由到对应的 impl executor。

    若 payload 不是 dict 或 tool 字段未注册，则原样透传（向后兼容）。
    """
    if capability_broker is None:
        raise RuntimeError("SKILL_CAPABILITY_BROKER_NOT_COMPOSED")
    return dispatch_executor_payload(
        payload,
        authority=authority,
        capability_broker=capability_broker,
    )


def _extract_audit_tier(governor_output: dict[str, Any] | None) -> AuditTier:
    """从 governor_output 中提取审计级别（M42 分层审计）。

    读取 committed_route_token.approval_tier 字段，映射到 AuditTier 枚举。
    未提供时降级为 STANDARD（向后兼容）。
    """
    if not isinstance(governor_output, dict):
        return AuditTier.STANDARD
    committed = governor_output.get("committed_route_token")
    if not isinstance(committed, dict):
        return AuditTier.STANDARD
    tier_str = str(committed.get("approval_tier", "")).strip().lower()
    if tier_str == "cache":
        return AuditTier.LIGHT
    if tier_str == "airlock":
        return AuditTier.FULL_AUDIT
    return AuditTier.STANDARD
