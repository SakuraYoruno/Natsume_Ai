"""Central registry for tool executors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import threading
import time
from types import MappingProxyType
from typing import Any, Callable

from host_brain.capability_plane.broker import CapabilityBroker, CapabilityDescriptor
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.policies import issue_grant

from host_brain.skills.impl.browser_executor import execute_browser_skill
from host_brain.agent_runtime.executor import execute_agent_runtime_payload
from host_brain.domain_packs.production_runtime import (
    GOVERNED_PRODUCTION_EXECUTION_MODE,
    execute_agent_production_payload,
)
from host_brain.skills.impl.desktop_executor import execute_desktop_skill
from host_brain.skills.impl.desktop_note import execute_desktop_note
from host_brain.skills.impl.diary_writer import execute_diary_writer
from host_brain.skills.impl.dream_generator import execute_dream_generator
from host_brain.skills.impl.emotion_executor import execute_emotion_skill
from host_brain.skills.impl.file_executor import execute_file_skill
from host_brain.skills.impl.focus_mode import execute_focus_mode
from host_brain.skills.impl.frontend_executor import execute_frontend_skill
from host_brain.skills.impl.ide_executor import execute_ide_skill
from host_brain.skills.impl.memory_executor import execute_memory_skill
from host_brain.skills.impl.notification_executor import execute_notification_skill
from host_brain.skills.impl.proactive_talk import execute_proactive_talk
from host_brain.skills.impl.repo_executor import execute_repo_skill
from host_brain.skills.impl.scheduler_executor import execute_scheduler_skill
from host_brain.skills.impl.shell_executor import execute_shell_skill
from host_brain.skills.impl.system_executor import execute_system_skill
from host_brain.skills.impl.web_executor import execute_web_skill


ExecutorFn = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolExecutorSpec:
    tool_name: str
    handler: ExecutorFn
    aliases: tuple[str, ...] = field(default_factory=tuple)


class ToolExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, ExecutorFn] = {}

    def register(self, spec: ToolExecutorSpec) -> None:
        names = (spec.tool_name, *spec.aliases)
        for name in names:
            normalized = str(name).strip().lower()
            if not normalized:
                raise ValueError("tool executor name must be non-empty")
            if normalized in self._executors:
                raise ValueError(f"executor already registered: {normalized}")
            self._executors[normalized] = spec.handler

    def _get_unchecked(self, tool_name: str) -> ExecutorFn | None:
        return self._executors.get(str(tool_name or "").strip().lower())

    def _dispatch_unchecked(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        executor = self._get_unchecked(str(payload.get("tool") or ""))
        if executor is None:
            raise LookupError(f"skill executor is not registered: {payload.get('tool')}")
        return executor(payload)

    def _executor_ids_unchecked(self) -> tuple[str, ...]:
        return tuple(sorted(self._executors))


def _with_context(handler: Callable[[dict[str, Any], dict[str, Any] | None], Any]) -> ExecutorFn:
    return lambda payload: handler(payload, payload.get("_context"))


def _focus_mode_with_mode_changer(payload: dict[str, Any]) -> Any:
    """focus_mode 的受控上下文包装器。

    focus_mode skill 期望 context 中有 mode_changer 回调来实际切换 sys_mode。
    调用方必须显式注入 Host app-local owner 的回调；本层不回退到
    process-global 模式写入器。
    """
    ctx = dict(payload.get("_context") or {})
    return execute_focus_mode(payload, ctx)


def _execute_agent_production_with_context(payload: dict[str, Any]) -> Any:
    """Select terminal execution only for an official governed attempt."""

    context = payload.get("_context")
    governed_context = (
        context.get("_governed_execution_context")
        if isinstance(context, dict)
        else None
    )
    if not isinstance(governed_context, dict):
        governed_context = None
    platform = context if isinstance(context, dict) else {}
    capability_broker = platform.get("capability_broker")
    model_attempt_service = platform.get("model_attempt_service")
    authority_verifier = platform.get("production_authority_verifier")
    if capability_broker is None or model_attempt_service is None or not callable(authority_verifier):
        raise RuntimeError("PRODUCTION_EXECUTION_PORTS_NOT_COMPOSED")
    return execute_agent_production_payload(
        payload,
        governed_execution_context=governed_context,
        capability_broker=capability_broker,
        model_attempt_service=model_attempt_service,
        authority_verifier=authority_verifier,
    )


def _execute_agent_runtime_with_context(payload: dict[str, Any]) -> Any:
    """Bridge composed model ports into the governed ``codex_runtime`` worker.

    The runtime payload itself cannot carry service objects.  This adapter is
    reached only after ``_broker_executor_adapter`` has revalidated a sealed
    capability invocation and attached the internal authority snapshot.
    """

    context = payload.get("_context")
    if not isinstance(context, Mapping):
        raise RuntimeError("AGENT_RUNTIME_PORTS_NOT_COMPOSED")
    sealed = context.get("_skill_execution_authority")
    governed = context.get("_governed_execution_context")
    model_attempt_service = context.get("model_attempt_service")
    artifact_store = context.get("agent_artifact_store")
    workspace_runtime = context.get("agent_workspace_runtime")
    # ``_execute_skill`` composes only the ModelAttemptService into the
    # governed context.  Its prompt registry is part of that same immutable
    # model-runtime composition; never trust a separately caller-shaped
    # ``_context.prompt_registry`` value.
    prompt_registry = getattr(model_attempt_service, "prompt_registry", None)
    if (
        not isinstance(sealed, Mapping)
        or not str(sealed.get("authority_id") or "").strip()
        or not isinstance(governed, Mapping)
        or str(governed.get("execution_mode") or "").strip()
        != GOVERNED_PRODUCTION_EXECUTION_MODE
        or model_attempt_service is None
        or prompt_registry is None
    ):
        raise RuntimeError("AGENT_RUNTIME_PORTS_NOT_COMPOSED")
    return execute_agent_runtime_payload(
        payload,
        model_attempt_service=model_attempt_service,
        prompt_registry=prompt_registry,
        artifact_store=artifact_store,
        workspace_runtime=workspace_runtime,
        governed_execution_context=governed,
    )


def _execute_skill_result_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a pure Skill result through the same governed Broker path."""

    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("skill result projection requires an object result")
    return dict(result)


def build_builtin_tool_executor_registry() -> ToolExecutorRegistry:
    registry = ToolExecutorRegistry()
    specs = (
        ToolExecutorSpec("file", execute_file_skill),
        ToolExecutorSpec("notification", execute_notification_skill),
        ToolExecutorSpec("shell", execute_shell_skill),
        ToolExecutorSpec("browser", execute_browser_skill),
        ToolExecutorSpec("cursor", execute_ide_skill),
        ToolExecutorSpec("pycharm", execute_ide_skill),
        ToolExecutorSpec("codex_runtime", _execute_agent_runtime_with_context),
        ToolExecutorSpec("codex_production", _execute_agent_production_with_context),
        ToolExecutorSpec("skill_result", _execute_skill_result_projection),
        ToolExecutorSpec("open_file_in_editor", execute_ide_skill),
        ToolExecutorSpec("diary_writer", _with_context(execute_diary_writer)),
        ToolExecutorSpec("dream_generator", _with_context(execute_dream_generator)),
        ToolExecutorSpec("desktop_note", _with_context(execute_desktop_note)),
        ToolExecutorSpec("focus_mode", _focus_mode_with_mode_changer),
        ToolExecutorSpec("proactive_talk", _with_context(execute_proactive_talk)),
        ToolExecutorSpec("desktop", _with_context(execute_desktop_skill)),
        ToolExecutorSpec("memory", _with_context(execute_memory_skill)),
        ToolExecutorSpec("emotion", _with_context(execute_emotion_skill)),
        ToolExecutorSpec("repo", _with_context(execute_repo_skill)),
        ToolExecutorSpec("frontend", _with_context(execute_frontend_skill)),
        ToolExecutorSpec("scheduler", _with_context(execute_scheduler_skill)),
        ToolExecutorSpec("system", _with_context(execute_system_skill)),
        ToolExecutorSpec("web", _with_context(execute_web_skill)),
    )
    for spec in specs:
        registry.register(spec)
    return registry


_BUILTIN_EXECUTOR_REGISTRY = build_builtin_tool_executor_registry()


_AUTHORITY_SEAL = object()
_UNTRUSTED_AUTHORITY_KEYS = frozenset({
    "_capability_authority",
    "_governed_execution_context",
    "_skill_execution_authority",
    "approval_token_ref",
    "authority",
    "confirm_token_ref",
    "governed_execution_context",
    "governor_grant",
    "governor_grant_ref",
})


class BoundSkillExecutionAuthority:
    """Sealed attestation issued only after an outer Governor/Task validation."""

    __slots__ = (
        "authority_id", "authority_kind", "authority_ref", "executor_id",
        "skill_id", "request_id", "principal_id", "target_id", "command_target_id", "command_id",
        "work_order_id", "graph_id", "graph_revision", "node_id",
        "attempt_id", "lease_id", "fencing_token", "intent_id", "session_id", "seq",
        "reservation_id", "node_grant_ref", "parent_grant_ref", "lease_owner_id",
        "business_scope", "operation_fingerprint", "node_risk_level",
        "pre_execution_review_ref",
        "payload_digest", "expires_at_ms", "validated_by", "durable_ledger",
        "durable_db_path", "_seal",
    )

    def __new__(cls, *_args: Any, **_kwargs: Any) -> "BoundSkillExecutionAuthority":
        raise TypeError("BoundSkillExecutionAuthority is issued by bind_validated_skill_authority")

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise TypeError("BoundSkillExecutionAuthority is immutable")


def _canonical_authority_ledger(candidate: Any | None) -> Any:
    if candidate is None:
        raise ValueError("skill authority requires an explicit durable ledger")
    return candidate


def _verify_durable_token_authority(
    *,
    ledger: Any,
    approval_token_ref: str,
    request_id: str,
    principal_id: str,
    target_id: str,
    skill_id: str,
    intent_id: str,
    session_id: str,
    seq: int,
) -> tuple[str, int]:
    """Resolve a token to an exact, unexpired durable Governor binding."""

    from host_brain.orchestration import DurableGovernorGrantResolver  # noqa: PLC0415
    from host_brain.orchestration.governor_grant import (  # noqa: PLC0415
        durable_governor_binding,
        governor_grant_ref,
    )

    trace = ledger.trace_request(request_id)
    rows = list(trace.get("governor_log") or []) if isinstance(trace, Mapping) else []
    expected_token_digest = "sha256:" + hashlib.sha256(
        approval_token_ref.encode("utf-8")
    ).hexdigest()
    now_value = int(time.time() * 1000)
    for raw in sorted(
        (dict(row) for row in rows if isinstance(row, Mapping)),
        key=lambda row: int(row.get("ts_ms") or 0),
        reverse=True,
    ):
        if str(raw.get("decision") or "").strip().upper() != "APPROVE":
            continue
        binding = durable_governor_binding(raw)
        try:
            expires_at_ms = int(binding.get("expires_at_ms") or 0)
            bound_seq = int(binding.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if (
            str(binding.get("approval_token_digest") or "") != expected_token_digest
            or str(binding.get("intent_id") or "") != intent_id
            or str(binding.get("session_id") or "") != session_id
            or bound_seq != int(seq)
            or str(binding.get("action_kind") or "") != skill_id
            or str(binding.get("scope") or "").strip().lower()
            != str(target_id or "").strip().lower()
            or str(binding.get("principal_id") or "") != principal_id
            or expires_at_ms <= now_value
        ):
            continue
        grant_ref = governor_grant_ref(request_id, raw)
        DurableGovernorGrantResolver(ledger).require(
            request_id=request_id,
            expected_ref=grant_ref,
        )
        return grant_ref, expires_at_ms
    raise ValueError("approval token has no exact durable Governor authority binding")


def bind_validated_skill_authority(
    *,
    payload: dict[str, Any],
    request_id: str,
    skill_id: str,
    principal_id: str,
    target_id: str,
    intent_id: str = "",
    session_id: str = "",
    seq: int = 0,
    approval_token_ref: str | None = None,
    governed_execution_context: dict[str, Any] | None = None,
    durable_ledger: Any | None = None,
    validated_by: str,
    ttl_ms: int = 5 * 60 * 1000,
) -> BoundSkillExecutionAuthority:
    """Seal the minimum already-validated outer authority for one payload."""

    executor_id = str(payload.get("tool") or "").strip().lower()
    if not executor_id:
        raise ValueError("validated skill authority requires payload.tool")
    request_id = str(request_id or "").strip()
    skill_id = str(skill_id or "").strip()
    principal_id = str(principal_id or "").strip()
    target_id = str(target_id or "").strip()
    validated_by = str(validated_by or "").strip()
    if not request_id or not skill_id or not principal_id or not target_id or not validated_by:
        raise ValueError("validated skill authority binding is incomplete")
    governed = dict(governed_execution_context or {})
    command_id = str(governed.get("command_id") or "").strip()
    attempt_id = str(governed.get("attempt_id") or "").strip()
    execution_mode = str(governed.get("execution_mode") or "").strip()
    token_ref = str(approval_token_ref or "").strip()
    work_order_id = ""
    command_target_id = ""
    graph_id = ""
    graph_revision = 0
    node_id = ""
    lease_id = ""
    fencing_token = 0
    reservation_id = ""
    node_grant_ref = ""
    parent_grant_ref = ""
    lease_owner_id = ""
    business_scope = ""
    operation_fingerprint = ""
    node_risk_level = 0
    pre_execution_review_ref = ""
    authority_expires_at_ms = int(time.time() * 1000) + max(1, int(ttl_ms))
    canonical_ledger = _canonical_authority_ledger(durable_ledger)
    if governed_execution_context is not None:
        governed_request_id = str(governed.get("request_id") or "").strip()
        governed_principal_id = str(governed.get("principal_id") or "").strip()
        governed_target_id = str(governed.get("target_id") or "").strip()
        command_target_id = str(governed.get("command_target_id") or "").strip()
        work_order_id = str(governed.get("work_order_id") or "").strip()
        graph_id = str(governed.get("graph_id") or "").strip()
        node_id = str(governed.get("node_id") or "").strip()
        lease_id = str(governed.get("lease_id") or "").strip()
        reservation_id = str(governed.get("reservation_id") or "").strip()
        node_grant_ref = str(governed.get("node_grant_ref") or "").strip()
        parent_grant_ref = str(governed.get("parent_grant_ref") or "").strip()
        lease_owner_id = str(governed.get("lease_owner_id") or "").strip()
        business_scope = str(governed.get("business_scope") or "").strip()
        operation_fingerprint = str(governed.get("operation_fingerprint") or "").strip()
        pre_execution_review_ref = str(
            governed.get("pre_execution_review_ref") or ""
        ).strip()
        try:
            graph_revision = int(governed.get("graph_revision") or 0)
            fencing_token = int(governed.get("fencing_token") or 0)
            # Older read-only governed callers did not persist this field.
            # Keep them least-privileged at risk 1; write-capable Agent
            # binding later requires the explicit admitted risk to be >= 4.
            node_risk_level = int(governed.get("node_risk_level") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("governed Task authority graph/fencing identity is invalid") from exc
        raw_work_order = payload.get("work_order")
        payload_work_order_id = (
            str(raw_work_order.get("work_order_id") or "").strip()
            if isinstance(raw_work_order, Mapping)
            else work_order_id
        )
        if (
            execution_mode != GOVERNED_PRODUCTION_EXECUTION_MODE
            or not command_id
            or not attempt_id
            or governed_request_id != request_id
            or governed_principal_id != principal_id
            or governed_target_id != target_id
            or governed_target_id != skill_id
            or not command_target_id
            or not work_order_id
            or payload_work_order_id != work_order_id
            or not graph_id
            or graph_revision < 1
            or not node_id
            or not lease_id
            or fencing_token < 1
            or not reservation_id
            or not node_grant_ref
            or not parent_grant_ref
            or node_grant_ref == parent_grant_ref
            or not lease_owner_id
            or not business_scope
            or not operation_fingerprint
            or not 1 <= node_risk_level <= 5
            or not str(session_id or "").strip()
        ):
            raise ValueError("governed Task authority identity does not match the dispatch binding")
        authority_kind = "governed_task_attempt"
        authority_ref = f"{command_id}#{attempt_id}"
    elif token_ref:
        authority_ref, grant_expires_at_ms = _verify_durable_token_authority(
            ledger=canonical_ledger,
            approval_token_ref=token_ref,
            request_id=request_id,
            principal_id=principal_id,
            target_id=target_id,
            skill_id=skill_id,
            intent_id=str(intent_id or "").strip(),
            session_id=str(session_id or "").strip(),
            seq=int(seq),
        )
        authority_kind = "governor_durable_grant"
        authority_expires_at_ms = min(authority_expires_at_ms, grant_expires_at_ms)
    else:
        raise ValueError("validated skill authority requires Governor token or governed Task attempt")
    payload_digest = _payload_digest(_sanitize_executor_payload(payload))
    identity = {
        "authority_kind": authority_kind,
        "authority_ref": authority_ref,
        "executor_id": executor_id,
        "skill_id": skill_id,
        "request_id": request_id,
        "principal_id": principal_id,
        "target_id": target_id,
        "command_target_id": command_target_id,
        "command_id": command_id,
        "work_order_id": work_order_id,
        "graph_id": graph_id,
        "graph_revision": graph_revision,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "lease_id": lease_id,
        "fencing_token": fencing_token,
        "reservation_id": reservation_id,
        "node_grant_ref": node_grant_ref,
        "parent_grant_ref": parent_grant_ref,
        "lease_owner_id": lease_owner_id,
        "business_scope": business_scope,
        "operation_fingerprint": operation_fingerprint,
        "node_risk_level": node_risk_level,
        "pre_execution_review_ref": pre_execution_review_ref,
        "intent_id": str(intent_id or ""),
        "session_id": str(session_id or ""),
        "seq": int(seq),
        "payload_digest": payload_digest,
        "validated_by": validated_by,
        "durable_db_path": str(
            getattr(canonical_ledger, "canonical_db_path", "") or ""
        ),
    }
    authority = object.__new__(BoundSkillExecutionAuthority)
    object.__setattr__(authority, "authority_id", "skill-authority://sha256/" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest())
    object.__setattr__(authority, "authority_kind", authority_kind)
    object.__setattr__(authority, "authority_ref", authority_ref)
    object.__setattr__(authority, "executor_id", executor_id)
    object.__setattr__(authority, "skill_id", skill_id)
    object.__setattr__(authority, "request_id", request_id)
    object.__setattr__(authority, "principal_id", principal_id)
    object.__setattr__(authority, "target_id", target_id)
    object.__setattr__(authority, "command_target_id", command_target_id)
    object.__setattr__(authority, "command_id", command_id)
    object.__setattr__(authority, "work_order_id", work_order_id)
    object.__setattr__(authority, "graph_id", graph_id)
    object.__setattr__(authority, "graph_revision", graph_revision)
    object.__setattr__(authority, "node_id", node_id)
    object.__setattr__(authority, "attempt_id", attempt_id)
    object.__setattr__(authority, "lease_id", lease_id)
    object.__setattr__(authority, "fencing_token", fencing_token)
    object.__setattr__(authority, "reservation_id", reservation_id)
    object.__setattr__(authority, "node_grant_ref", node_grant_ref)
    object.__setattr__(authority, "parent_grant_ref", parent_grant_ref)
    object.__setattr__(authority, "lease_owner_id", lease_owner_id)
    object.__setattr__(authority, "business_scope", business_scope)
    object.__setattr__(authority, "operation_fingerprint", operation_fingerprint)
    object.__setattr__(authority, "node_risk_level", node_risk_level)
    object.__setattr__(
        authority,
        "pre_execution_review_ref",
        pre_execution_review_ref,
    )
    object.__setattr__(authority, "intent_id", str(intent_id or ""))
    object.__setattr__(authority, "session_id", str(session_id or ""))
    object.__setattr__(authority, "seq", int(seq))
    object.__setattr__(authority, "payload_digest", payload_digest)
    object.__setattr__(authority, "expires_at_ms", authority_expires_at_ms)
    object.__setattr__(authority, "validated_by", validated_by)
    object.__setattr__(authority, "durable_ledger", canonical_ledger)
    object.__setattr__(
        authority,
        "durable_db_path",
        str(getattr(canonical_ledger, "canonical_db_path", "") or ""),
    )
    object.__setattr__(authority, "_seal", _AUTHORITY_SEAL)
    return authority


@dataclass(frozen=True, slots=True)
class _SkillInvocationEnvelope:
    payload: dict[str, Any]
    authority: BoundSkillExecutionAuthority | None


_INVOCATIONS_LOCK = threading.RLock()
_INVOCATIONS: dict[str, _SkillInvocationEnvelope] = {}
# Unit tests may replace this with a repository-backed fixture. Production
# leaves it unset and always performs the canonical durable lookup below.
_GOVERNED_AUTHORITY_VERIFIER_OVERRIDE: Callable[[BoundSkillExecutionAuthority], str | None] | None = None


class _SkillExecutionPolicy:
    def authorize(self, request: CapabilityRequest, descriptor: CapabilityDescriptor, *, now_ms: int):  # noqa: ANN201
        del descriptor
        with _INVOCATIONS_LOCK:
            envelope = _INVOCATIONS.get(request.attempt_id)
        authority = envelope.authority if envelope is not None else None
        reason = _authority_error(authority, request, envelope, now_ms=now_ms)
        return issue_grant(
            request,
            now_ms=now_ms,
            approved=reason is None,
            reason_code=reason or "SKILL_CAPABILITY_APPROVED",
            authority_ref=authority.authority_ref if reason is None and authority is not None else "",
        )


_SKILL_EXECUTION_POLICY = _SkillExecutionPolicy()


def _authority_error(
    authority: BoundSkillExecutionAuthority | None,
    request: CapabilityRequest,
    envelope: _SkillInvocationEnvelope | None,
    *,
    now_ms: int,
) -> str | None:
    if type(authority) is not BoundSkillExecutionAuthority or getattr(authority, "_seal", None) is not _AUTHORITY_SEAL:
        return "SKILL_AUTHORITY_MISSING"
    if envelope is None:
        return "SKILL_AUTHORITY_MISSING"
    if authority.expires_at_ms <= now_ms:
        return "SKILL_AUTHORITY_EXPIRED"
    if authority.executor_id != request.capability_id:
        return "SKILL_AUTHORITY_EXECUTOR_MISMATCH"
    if authority.request_id != request.request_id:
        return "SKILL_AUTHORITY_REQUEST_MISMATCH"
    if authority.principal_id != str(request.context.get("principal_id") or ""):
        return "SKILL_AUTHORITY_PRINCIPAL_MISMATCH"
    if authority.target_id != str(request.context.get("target_id") or ""):
        return "SKILL_AUTHORITY_TARGET_MISMATCH"
    if authority.command_target_id != str(request.context.get("command_target_id") or ""):
        return "SKILL_AUTHORITY_COMMAND_TARGET_MISMATCH"
    if authority.command_id != str(request.context.get("command_id") or ""):
        return "SKILL_AUTHORITY_COMMAND_MISMATCH"
    if authority.work_order_id != str(request.context.get("work_order_id") or ""):
        return "SKILL_AUTHORITY_WORK_ORDER_MISMATCH"
    if authority.graph_id != str(request.context.get("graph_id") or ""):
        return "SKILL_AUTHORITY_GRAPH_MISMATCH"
    if authority.graph_revision != int(request.context.get("graph_revision") or 0):
        return "SKILL_AUTHORITY_GRAPH_MISMATCH"
    if authority.node_id != str(request.context.get("node_id") or ""):
        return "SKILL_AUTHORITY_NODE_MISMATCH"
    if authority.attempt_id != str(request.context.get("governed_attempt_id") or ""):
        return "SKILL_AUTHORITY_ATTEMPT_MISMATCH"
    if authority.lease_id != str(request.context.get("lease_id") or ""):
        return "SKILL_AUTHORITY_LEASE_MISMATCH"
    if authority.fencing_token != int(request.context.get("fencing_token") or 0):
        return "SKILL_AUTHORITY_FENCING_MISMATCH"
    if authority.reservation_id != str(request.context.get("reservation_id") or ""):
        return "SKILL_AUTHORITY_RESERVATION_MISMATCH"
    if authority.node_grant_ref != str(request.context.get("node_grant_ref") or ""):
        return "SKILL_AUTHORITY_NODE_GRANT_MISMATCH"
    if authority.parent_grant_ref != str(request.context.get("parent_grant_ref") or ""):
        return "SKILL_AUTHORITY_PARENT_GRANT_MISMATCH"
    if authority.lease_owner_id != str(request.context.get("lease_owner_id") or ""):
        return "SKILL_AUTHORITY_LEASE_OWNER_MISMATCH"
    if authority.business_scope != str(request.context.get("business_scope") or ""):
        return "SKILL_AUTHORITY_BUSINESS_SCOPE_MISMATCH"
    if authority.operation_fingerprint != str(
        request.context.get("operation_fingerprint") or ""
    ):
        return "SKILL_AUTHORITY_OPERATION_FINGERPRINT_MISMATCH"
    if authority.payload_digest != _payload_digest(envelope.payload):
        return "SKILL_AUTHORITY_PAYLOAD_MISMATCH"
    if authority.authority_kind not in {"governor_durable_grant", "governed_task_attempt"}:
        return "SKILL_AUTHORITY_KIND_INVALID"
    if authority.authority_kind == "governed_task_attempt":
        verifier = _GOVERNED_AUTHORITY_VERIFIER_OVERRIDE or _verify_durable_governed_authority
        durable_error = verifier(authority)
        if durable_error is not None:
            return durable_error
    elif _verify_durable_skill_grant_authority(authority) is not None:
        return "SKILL_DURABLE_GRANT_INVALID"
    return None


def _verify_durable_governed_authority(
    authority: BoundSkillExecutionAuthority,
) -> str | None:
    """Re-resolve the exact command, node grant and leased operation claim."""

    try:
        from host_brain.orchestration.node_grants import (  # noqa: PLC0415
            DurableNodeGrantVerifier,
        )
        from host_brain.persistence.host_command_repository import HostCommandRepository  # noqa: PLC0415
        from host_brain.persistence.task_repository import DurableTaskStore  # noqa: PLC0415

        ledger = authority.durable_ledger
        if ledger is None:
            return "SKILL_DURABLE_AUTHORITY_UNAVAILABLE"
        db_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not db_path or db_path == ":memory:":
            return "SKILL_DURABLE_AUTHORITY_UNAVAILABLE"
        command = HostCommandRepository(db_path).get(authority.command_id)
        if command is None:
            return "SKILL_DURABLE_COMMAND_MISSING"
        if (
            str(command.get("request_id") or "") != authority.request_id
            or str(command.get("session_id") or "") != authority.session_id
            or str(command.get("principal_id") or "") != authority.principal_id
            or str(command.get("target_id") or "") != authority.command_target_id
            or str(command.get("root_work_order_id") or "") != authority.work_order_id
            or str(command.get("graph_id") or "") != authority.graph_id
            or int(command.get("graph_revision") or 0) != authority.graph_revision
            or str(command.get("business_scope") or "") != authority.business_scope
            or str(command.get("governor_grant_ref") or "") != authority.parent_grant_ref
        ):
            return "SKILL_DURABLE_COMMAND_BINDING_MISMATCH"
        task_store = DurableTaskStore(db_path)
        attempt = task_store.get_attempt(authority.attempt_id)
        if (
            str(attempt.get("graph_id") or "") != authority.graph_id
            or int(attempt.get("graph_revision") or 0) != authority.graph_revision
            or str(attempt.get("node_id") or "") != authority.node_id
            or str(attempt.get("status") or "").strip().upper() != "RUNNING"
            or str(attempt.get("operation_scope") or "") != authority.business_scope
            or str(attempt.get("operation_fingerprint") or "")
            != authority.operation_fingerprint
        ):
            return "SKILL_DURABLE_ATTEMPT_BINDING_MISMATCH"
        node = task_store.get_node(
            authority.graph_id,
            authority.graph_revision,
            authority.node_id,
        )
        if (
            str(node.get("graph_id") or "") != authority.graph_id
            or int(node.get("graph_revision") or 0) != authority.graph_revision
            or str(node.get("node_id") or "") != authority.node_id
            or str(node.get("status") or "").strip().upper() != "RUNNING"
        ):
            return "SKILL_DURABLE_NODE_BINDING_MISMATCH"
        lease = task_store.get_active_lease(authority.attempt_id)
        now_value = int(time.time() * 1000)
        if (
            not isinstance(lease, Mapping)
            or str(lease.get("lease_id") or "") != authority.lease_id
            or str(lease.get("attempt_id") or "") != authority.attempt_id
            or str(lease.get("owner_id") or "") != authority.lease_owner_id
            or int(lease.get("fencing_token") or 0) != authority.fencing_token
            or str(lease.get("status") or "").strip().upper() != "ACTIVE"
            or int(lease.get("expires_ts_ms") or 0) <= now_value
        ):
            return "SKILL_DURABLE_LEASE_BINDING_MISMATCH"
        reservation = task_store.get_operation_reservation(authority.reservation_id)
        if (
            str(reservation.get("reservation_id") or "") != authority.reservation_id
            or str(reservation.get("status") or "").strip().upper() != "RESERVED"
            or int(reservation.get("expires_ts_ms") or 0) <= now_value
            or str(reservation.get("attempt_id") or "") != authority.attempt_id
            or str(reservation.get("lease_id") or "") != authority.lease_id
            or str(reservation.get("owner_id") or "") != authority.lease_owner_id
            or int(reservation.get("fencing_token") or 0) != authority.fencing_token
            or str(reservation.get("business_scope") or "") != authority.business_scope
            or str(reservation.get("operation_fingerprint") or "")
            != authority.operation_fingerprint
        ):
            return "SKILL_DURABLE_OPERATION_BINDING_MISMATCH"
        try:
            verified = DurableNodeGrantVerifier(ledger=ledger).verify(
                grant_ref=authority.node_grant_ref,
                request_id=authority.request_id,
                session_id=authority.session_id,
                principal_id=authority.principal_id,
                graph_id=authority.graph_id,
                root_work_order_id=authority.work_order_id,
                graph_revision=authority.graph_revision,
                node_id=authority.node_id,
                route="skill",
                target_id=authority.target_id,
                parent_grant_ref=authority.parent_grant_ref,
            )
        except Exception:
            return "SKILL_DURABLE_NODE_GRANT_INVALID"
        if (
            str(verified.grant_ref or "") != authority.node_grant_ref
            or str(verified.parent_grant_ref or "") != authority.parent_grant_ref
            or str(verified.route or "").strip().lower() != "skill"
            or str(verified.target_id or "") != authority.target_id
        ):
            return "SKILL_DURABLE_NODE_GRANT_BINDING_MISMATCH"
    except Exception:
        return "SKILL_DURABLE_AUTHORITY_INVALID"
    return None


def _verify_durable_skill_grant_authority(
    authority: BoundSkillExecutionAuthority,
) -> str | None:
    try:
        from host_brain.orchestration import DurableGovernorGrantResolver  # noqa: PLC0415
        from host_brain.orchestration.governor_grant import durable_governor_binding  # noqa: PLC0415

        ledger = authority.durable_ledger
        decision = DurableGovernorGrantResolver(ledger).require(
            request_id=authority.request_id,
            expected_ref=authority.authority_ref,
        )
        binding = durable_governor_binding(decision)
        if (
            str(binding.get("intent_id") or "") != authority.intent_id
            or str(binding.get("session_id") or "") != authority.session_id
            or int(binding.get("seq") or 0) != authority.seq
            or str(binding.get("action_kind") or "") != authority.skill_id
            or str(binding.get("scope") or "").strip().lower()
            != authority.target_id.strip().lower()
            or str(binding.get("principal_id") or "") != authority.principal_id
            or int(binding.get("expires_at_ms") or 0) <= int(time.time() * 1000)
        ):
            return "SKILL_DURABLE_GRANT_BINDING_MISMATCH"
    except Exception:
        return "SKILL_DURABLE_GRANT_INVALID"
    return None


def _broker_executor_adapter(request: CapabilityRequest, grant: Any) -> Any:
    """The only production caller allowed to cross the unchecked registry."""

    with _INVOCATIONS_LOCK:
        envelope = _INVOCATIONS.get(request.attempt_id)
    if envelope is None:
        raise RuntimeError("SKILL_INVOCATION_ENVELOPE_MISSING")
    payload = dict(envelope.payload)
    authority = envelope.authority
    if type(authority) is BoundSkillExecutionAuthority:
        context = dict(payload.get("_context") or {})
        # This snapshot is created only after _SkillExecutionPolicy has verified
        # the sealed durable authority and issued the request-bound Capability
        # grant. Caller supplied values with this key were already stripped by
        # _sanitize_executor_payload, so physical-effect adapters can consume it
        # without accepting an authority claim from Skill arguments.
        context["_skill_execution_authority"] = MappingProxyType({
            "request_id": authority.request_id,
            "authority_id": authority.authority_id,
            "authority_kind": authority.authority_kind,
            "authority_ref": authority.authority_ref,
            "grant_ref": str(getattr(grant, "grant_id", "") or ""),
            "capability_attempt_id": request.attempt_id,
            "principal_id": authority.principal_id,
            "target_id": authority.target_id,
            "command_target_id": authority.command_target_id,
            "session_id": authority.session_id,
            "lease_id": authority.lease_id,
            "fencing_token": authority.fencing_token,
            "reservation_id": authority.reservation_id,
            "node_grant_ref": authority.node_grant_ref,
            "parent_grant_ref": authority.parent_grant_ref,
            "lease_owner_id": authority.lease_owner_id,
            "business_scope": authority.business_scope,
            "operation_fingerprint": authority.operation_fingerprint,
            "node_risk_level": authority.node_risk_level,
            "pre_execution_review_ref": authority.pre_execution_review_ref,
            "expires_at_ms": min(
                authority.expires_at_ms,
                int(getattr(grant, "expires_at_ms", authority.expires_at_ms)),
            ),
        })
        if authority.authority_kind == "governed_task_attempt":
            context["_governed_execution_context"] = {
                "execution_mode": GOVERNED_PRODUCTION_EXECUTION_MODE,
                "command_id": authority.command_id,
                "attempt_id": authority.attempt_id,
                "request_id": authority.request_id,
                "principal_id": authority.principal_id,
                "target_id": authority.target_id,
                "command_target_id": authority.command_target_id,
                "work_order_id": authority.work_order_id,
                "graph_id": authority.graph_id,
                "graph_revision": authority.graph_revision,
                "node_id": authority.node_id,
                "lease_id": authority.lease_id,
                "lease_owner_id": authority.lease_owner_id,
                "fencing_token": authority.fencing_token,
                "reservation_id": authority.reservation_id,
                "node_grant_ref": authority.node_grant_ref,
                "parent_grant_ref": authority.parent_grant_ref,
                "business_scope": authority.business_scope,
                "operation_fingerprint": authority.operation_fingerprint,
                "node_risk_level": authority.node_risk_level,
                "pre_execution_review_ref": (
                    authority.pre_execution_review_ref
                ),
            }
        payload["_context"] = context
    return _BUILTIN_EXECUTOR_REGISTRY._dispatch_unchecked(payload)


def _bind_skill_capabilities(capability_broker: CapabilityBroker) -> None:
    """Attach built-in Skill adapters to the composed process broker."""

    for executor_id in _BUILTIN_EXECUTOR_REGISTRY._executor_ids_unchecked():
        capability_broker.register(
            CapabilityDescriptor(
                capability_id=executor_id,
                kind=CapabilityKind.SKILL,
                version="1",
                risk_level=5,
                adapter=_broker_executor_adapter,
                authorization_policy=_SKILL_EXECUTION_POLICY,
            ),
            replace_existing=True,
        )


def get_builtin_tool_executor_registry() -> ToolExecutorRegistry:
    """Metadata/testing access only; registry has no public execution method."""

    return _BUILTIN_EXECUTOR_REGISTRY


def dispatch_executor_payload(
    payload: Any = None,
    *,
    capability_broker: CapabilityBroker,
    skill_id: str | None = None,
    context: dict[str, Any] | None = None,
    authority: BoundSkillExecutionAuthority | None = None,
    deadline_ms: int = 5 * 60 * 1000,
) -> Any:
    """Public governed executor dispatch; missing authority always fails closed."""

    if capability_broker is None:
        raise ValueError("skill dispatch requires the composed CapabilityBroker")
    _bind_skill_capabilities(capability_broker)

    if skill_id is not None:
        normalized = dict(payload or {}) if isinstance(payload, dict) else {}
        normalized["tool"] = str(skill_id or "").strip()
        if context is not None:
            normalized["_context"] = dict(context)
        payload = normalized
    if not isinstance(payload, dict):
        return _dispatch_failure("SKILL_EXECUTOR_PAYLOAD_INVALID", "executor payload must be an object")
    payload = _sanitize_executor_payload(payload)
    executor_id = str(payload.get("tool") or "").strip().lower()
    if not executor_id:
        return _dispatch_failure("SKILL_EXECUTOR_ID_MISSING", "payload.tool is required")
    payload_digest = _payload_digest(payload)
    now_ms = int(time.time() * 1000)
    authority_id = (
        authority.authority_id
        if type(authority) is BoundSkillExecutionAuthority
        else "missing-authority://" + hashlib.sha256(
            f"{executor_id}:{payload_digest}".encode()
        ).hexdigest()
    )
    attempt_id = f"skill-attempt::{authority_id}"
    envelope = _SkillInvocationEnvelope(payload=dict(payload), authority=authority)
    with _INVOCATIONS_LOCK:
        _INVOCATIONS[attempt_id] = envelope
    try:
        receipt = capability_broker.execute(CapabilityRequest(
            request_id=(authority.request_id if type(authority) is BoundSkillExecutionAuthority else str(payload.get("request_id") or "unbound-skill-request")),
            attempt_id=attempt_id,
            idempotency_key=f"skill-dispatch::{authority_id}::{payload_digest}",
            capability_id=executor_id,
            kind=CapabilityKind.SKILL,
            args={"executor_id": executor_id, "payload_digest": payload_digest},
            context={
                "authority_id": authority_id,
                "authority_kind": getattr(authority, "authority_kind", "missing"),
                "principal_id": getattr(authority, "principal_id", ""),
                "target_id": getattr(authority, "target_id", ""),
                "command_target_id": getattr(authority, "command_target_id", ""),
                "command_id": getattr(authority, "command_id", ""),
                "work_order_id": getattr(authority, "work_order_id", ""),
                "graph_id": getattr(authority, "graph_id", ""),
                "graph_revision": getattr(authority, "graph_revision", 0),
                "node_id": getattr(authority, "node_id", ""),
                "governed_attempt_id": getattr(authority, "attempt_id", ""),
                "lease_id": getattr(authority, "lease_id", ""),
                "fencing_token": getattr(authority, "fencing_token", 0),
                "reservation_id": getattr(authority, "reservation_id", ""),
                "node_grant_ref": getattr(authority, "node_grant_ref", ""),
                "parent_grant_ref": getattr(authority, "parent_grant_ref", ""),
                "lease_owner_id": getattr(authority, "lease_owner_id", ""),
                "business_scope": getattr(authority, "business_scope", ""),
                "operation_fingerprint": getattr(
                    authority,
                    "operation_fingerprint",
                    "",
                ),
            },
            created_at_ms=now_ms,
            deadline_at_ms=now_ms + max(1, int(deadline_ms)),
        ))
    finally:
        with _INVOCATIONS_LOCK:
            _INVOCATIONS.pop(attempt_id, None)
    receipt_payload = receipt.to_dict()
    receipt_payload.pop("output", None)
    if not receipt.success:
        return _dispatch_failure(
            str(receipt.reason_code or "SKILL_CAPABILITY_REJECTED"),
            receipt.detail,
            capability_receipt=receipt_payload,
        )
    if isinstance(receipt.output, dict):
        result = dict(receipt.output)
        result["capability_receipt"] = receipt_payload
        return result
    return {"status": "OK", "result": receipt.output, "capability_receipt": receipt_payload}


def _dispatch_failure(reason_code: str, detail: str, *, capability_receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "REJECTED",
        "reason_code": str(reason_code),
        "detail": str(detail),
        "capability_receipt": dict(capability_receipt or {}),
    }


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_stable_projection, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _sanitize_executor_payload(value: Any) -> Any:
    """Remove every caller-controlled authority claim before broker admission."""

    if isinstance(value, dict):
        return {
            key: _sanitize_executor_payload(item)
            for key, item in value.items()
            if str(key).strip().lower() not in _UNTRUSTED_AUTHORITY_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_executor_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_executor_payload(item) for item in value)
    return value


def _stable_projection(value: Any) -> str:
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


__all__ = [
    "BoundSkillExecutionAuthority",
    "ToolExecutorRegistry",
    "ToolExecutorSpec",
    "bind_validated_skill_authority",
    "build_builtin_tool_executor_registry",
    "dispatch_executor_payload",
    "get_builtin_tool_executor_registry",
]
