"""Fail-closed inner dispatch for governed delegate TaskNodes.

The outer Task Runtime already owns durable graph admission, attempts, leases
and node grants.  This module is the *inner* delegate boundary: it receives
only an immutable projection of that authority, verifies it again immediately
before dispatch, and invokes an explicitly registered, read-only adapter.  A
target may either be completely port-free or receive the one canonical model
port under the frozen Task budget; neither profile receives tools or effects.

It deliberately does not call the legacy ``SubAgentDispatcher``.  That
dispatcher owns a global handler registry, creates runtime gateways and writes
an independent ``skill_exec_log`` before Task Runtime verification/acceptance.
Those behaviours are incompatible with a frozen node execution boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import re
from typing import Any, Protocol

from host_brain.core.trace_redaction import (
    public_output_text,
    read_decision_summary,
    read_public_agent_output,
)
from host_brain.contracts.agent_runtime.delegate_usage_attestation import (
    READONLY_DELEGATE_EXECUTION_MODE,
    build_readonly_delegate_usage_attestation,
)
from host_brain.contracts.agent_runtime.delegate_target_policy import (
    GOVERNED_DELEGATE_BUDGET_MODE,
    GOVERNED_DELEGATE_EFFECT_MODE,
    GOVERNED_DELEGATE_POLICY_SCHEMA_VERSION,
    GOVERNED_DELEGATE_REVIEW_MODE,
    GOVERNED_MODEL_DELEGATE_BUDGET_MODE,
    GOVERNED_MODEL_DELEGATE_EXECUTION_MODE,
    GOVERNED_MODEL_DELEGATE_POLICY_SCHEMA_VERSION,
    GOVERNED_MODEL_DELEGATE_REVIEW_MODE,
    freeze_governed_delegate_target_policy,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput


GOVERNED_DELEGATE_INVOCATION_INVALID = "GOVERNED_DELEGATE_INVOCATION_INVALID"
GOVERNED_DELEGATE_AUTHORITY_INVALID = "GOVERNED_DELEGATE_AUTHORITY_INVALID"
GOVERNED_DELEGATE_TARGET_UNAVAILABLE = "GOVERNED_DELEGATE_TARGET_UNAVAILABLE"
GOVERNED_DELEGATE_OUTPUT_INVALID = "GOVERNED_DELEGATE_OUTPUT_INVALID"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_FORBIDDEN_CONTEXT_KEYS = frozenset(
    {
        "approval_token",
        "approval_token_ref",
        "authorization",
        "capability",
        "delivery_authorization",
        "governor_result",
        "grant",
        "node_grant_ref",
        "parent_grant_ref",
        "planner",
        "planning",
        "replan",
        "route",
        "route_type",
        "target_id",
        "token",
        "dispatch_sub_agent",
        "canonical_sub_agent_request",
    }
)


class GovernedDelegateDispatchError(RuntimeError):
    """A rejectable inner-delegate failure with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_DELEGATE_OUTPUT_INVALID)
        self.detail = str(detail or "governed delegate dispatch failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class NodeGrantVerifierPort(Protocol):
    def verify(self, **kwargs: Any) -> Any: ...


ReadOnlyDelegateHandler = Callable[[dict[str, Any], dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class GovernedDelegateInvocation:
    """Secret-free immutable authority projection for one delegate node."""

    command_id: str
    request_id: str
    session_id: str
    principal_id: str
    work_order_id: str
    root_work_order_id: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    lease_id: str
    lease_owner_id: str
    fencing_token: int
    parent_grant_ref: str
    node_grant_ref: str
    target_id: str
    objective: str
    user_message: str
    input_refs: tuple[str, ...]
    _budget_json: str = field(repr=False)
    _context_json: str = field(repr=False)

    @classmethod
    def from_frozen(
        cls,
        *,
        frozen: FrozenNodeInput,
        user_message: str,
        context: Mapping[str, Any] | None,
    ) -> "GovernedDelegateInvocation":
        if not isinstance(frozen, FrozenNodeInput):
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_INVOCATION_INVALID,
                "delegate invocation requires a FrozenNodeInput",
            )
        if str(frozen.route or "").strip().lower() != "delegate":
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_INVOCATION_INVALID,
                "frozen node route must be delegate",
            )
        safe_context = _safe_context(context)
        invocation = cls(
            command_id=_required_identifier(frozen.command_id, "command_id"),
            request_id=_required_identifier(frozen.request_id, "request_id"),
            session_id=_required_identifier(frozen.session_id, "session_id"),
            principal_id=_required_identifier(frozen.principal_id, "principal_id"),
            work_order_id=_required_identifier(frozen.work_order_id, "work_order_id"),
            root_work_order_id=_required_identifier(
                frozen.root_work_order_id,
                "root_work_order_id",
            ),
            graph_id=_required_identifier(frozen.graph_id, "graph_id"),
            graph_revision=_positive_int(frozen.graph_revision, "graph_revision"),
            node_id=_required_identifier(frozen.node_id, "node_id"),
            attempt_id=_required_identifier(frozen.attempt_id, "attempt_id"),
            lease_id=_required_identifier(frozen.lease_id, "lease_id"),
            lease_owner_id=_required_identifier(frozen.lease_owner_id, "lease_owner_id"),
            fencing_token=_positive_int(frozen.fencing_token, "fencing_token"),
            parent_grant_ref=_required_ref(frozen.parent_grant_ref, "parent_grant_ref"),
            node_grant_ref=_required_ref(frozen.node_grant_ref, "node_grant_ref"),
            target_id=_required_identifier(frozen.target_id, "target_id"),
            objective=_required_text(frozen.objective, "objective"),
            user_message=_required_text(user_message, "user_message"),
            input_refs=tuple(
                _required_ref(item, "input_ref") for item in tuple(frozen.input_refs)
            ),
            _budget_json=_json_copy_text(
                dict(frozen.budget),
                field_name="budget",
            ),
            _context_json=_json_copy_text(safe_context, field_name="context"),
        )
        if invocation.work_order_id != invocation.root_work_order_id:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_INVOCATION_INVALID,
                "delegate node must retain its root WorkOrder lineage",
            )
        if invocation.node_grant_ref == invocation.parent_grant_ref:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_AUTHORITY_INVALID,
                "delegate node cannot reuse the parent Governor grant",
            )
        return invocation

    @property
    def context(self) -> dict[str, Any]:
        loaded = json.loads(self._context_json)
        return dict(loaded) if isinstance(loaded, Mapping) else {}

    @property
    def budget(self) -> dict[str, Any]:
        loaded = json.loads(self._budget_json)
        return dict(loaded) if isinstance(loaded, Mapping) else {}

    @property
    def invocation_ref(self) -> str:
        return "governed-delegate://sha256/" + hashlib.sha256(
            json.dumps(
                {
                    "command_id": self.command_id,
                    "graph_id": self.graph_id,
                    "graph_revision": self.graph_revision,
                    "node_id": self.node_id,
                    "attempt_id": self.attempt_id,
                    "lease_id": self.lease_id,
                    "lease_owner_id": self.lease_owner_id,
                    "fencing_token": self.fencing_token,
                    "node_grant_ref": self.node_grant_ref,
                    "target_id": self.target_id,
                    "input_refs": list(self.input_refs),
                    "budget": self.budget,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class GovernedDelegateOutcome:
    """Authority-free terminal outcome returned to the frozen route worker."""

    success: bool
    target_id: str
    result_text: str
    error_text: str
    audit_refs: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class GovernedDelegateTarget:
    """A deliberately tiny registered delegate adapter."""

    target_id: str
    handler: ReadOnlyDelegateHandler
    adapter_id: str
    execution_mode: str
    review_mode: str = GOVERNED_DELEGATE_REVIEW_MODE
    budget_mode: str = GOVERNED_DELEGATE_BUDGET_MODE
    effect_mode: str = GOVERNED_DELEGATE_EFFECT_MODE

    def policy_projection(self) -> dict[str, Any]:
        schema_version = (
            GOVERNED_MODEL_DELEGATE_POLICY_SCHEMA_VERSION
            if self.execution_mode == GOVERNED_MODEL_DELEGATE_EXECUTION_MODE
            else GOVERNED_DELEGATE_POLICY_SCHEMA_VERSION
        )
        return freeze_governed_delegate_target_policy(
            {
                "schema_version": schema_version,
                "target_id": _required_identifier(
                    self.target_id,
                    "delegate target_id",
                ),
                "adapter_id": _required_identifier(
                    self.adapter_id,
                    "delegate adapter_id",
                ),
                "execution_mode": str(self.execution_mode or "").strip(),
                "review_mode": str(self.review_mode or "").strip(),
                "budget_mode": str(self.budget_mode or "").strip(),
                "effect_mode": str(self.effect_mode or "").strip(),
            }
        )


class GovernedDelegateDispatcher:
    """Composition-owned, grant-revalidating dispatcher for read-only targets."""

    def __init__(
        self,
        *,
        node_grants: NodeGrantVerifierPort,
        targets: Mapping[str, GovernedDelegateTarget],
    ) -> None:
        if not callable(getattr(node_grants, "verify", None)):
            raise ValueError("node_grants.verify is required")
        normalized: dict[str, GovernedDelegateTarget] = {}
        for target_id, target in dict(targets or {}).items():
            resolved = _required_identifier(target_id, "target registry key")
            if not isinstance(target, GovernedDelegateTarget):
                raise ValueError("delegate target registry values must be GovernedDelegateTarget")
            if target.target_id != resolved or not callable(target.handler):
                raise ValueError("delegate target registry binding is invalid")
            policy = target.policy_projection()
            supported_profile = (
                (
                    policy["execution_mode"] == READONLY_DELEGATE_EXECUTION_MODE
                    and policy["review_mode"] == GOVERNED_DELEGATE_REVIEW_MODE
                    and policy["budget_mode"] == GOVERNED_DELEGATE_BUDGET_MODE
                )
                or (
                    policy["execution_mode"]
                    == GOVERNED_MODEL_DELEGATE_EXECUTION_MODE
                    and policy["review_mode"]
                    == GOVERNED_MODEL_DELEGATE_REVIEW_MODE
                    and policy["budget_mode"]
                    == GOVERNED_MODEL_DELEGATE_BUDGET_MODE
                )
            )
            if not supported_profile or policy["effect_mode"] != GOVERNED_DELEGATE_EFFECT_MODE:
                raise ValueError(
                    "delegate target must declare one supported read-only execution profile"
                )
            normalized[resolved] = target
        self._node_grants = node_grants
        self._targets = normalized

    def require_target(self, target_id: str) -> dict[str, Any]:
        """Reject a target before a HostCommand or TaskGraph is persisted."""

        target = _required_identifier(target_id, "delegate target_id")
        if target not in self._targets:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_TARGET_UNAVAILABLE,
                "delegate target is not enabled for the governed runtime",
            )
        return self._targets[target].policy_projection()

    async def dispatch(self, invocation: GovernedDelegateInvocation) -> GovernedDelegateOutcome:
        if not isinstance(invocation, GovernedDelegateInvocation):
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_INVOCATION_INVALID,
                "dispatcher requires a GovernedDelegateInvocation",
            )
        self.require_target(invocation.target_id)
        target = self._targets[invocation.target_id]
        _verify_node_authority(self._node_grants, invocation=invocation)
        handler_context = _handler_context(invocation)
        if target.execution_mode == GOVERNED_MODEL_DELEGATE_EXECUTION_MODE:
            # The model-backed adapter receives only this immutable Task
            # projection.  Port-free targets never see it.
            handler_context["delegate_invocation"] = invocation
        result = target.handler(_handler_payload(invocation), handler_context)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Mapping):
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_OUTPUT_INVALID,
                "read-only delegate adapter must return a mapping",
            )
        if target.execution_mode == READONLY_DELEGATE_EXECUTION_MODE:
            return _project_readonly_outcome(
                target=target,
                invocation=invocation,
                result=result,
            )
        return _project_model_backed_outcome(
            target=target,
            invocation=invocation,
            result=result,
        )


def build_governed_delegate_dispatcher(
    *,
    node_grants: NodeGrantVerifierPort,
    model_attempt_service: Any | None = None,
) -> GovernedDelegateDispatcher:
    """Compose explicit Delegate targets without the legacy dispatcher."""

    from host_brain.app.handlers.pentest_agent_handler import (  # noqa: PLC0415
        AGENT_ID_PENTEST,
        pentest_agent_handler,
    )
    from host_brain.agent_runtime.agent_registry import (  # noqa: PLC0415
        AGENT_ID_TRANSLATE,
    )

    targets = {
        AGENT_ID_PENTEST: GovernedDelegateTarget(
            target_id=AGENT_ID_PENTEST,
            handler=pentest_agent_handler,
            adapter_id="readonly_pentest_domain_handoff.v1",
            execution_mode=READONLY_DELEGATE_EXECUTION_MODE,
        )
    }
    if callable(getattr(model_attempt_service, "complete", None)):
        targets[AGENT_ID_TRANSLATE] = GovernedDelegateTarget(
            target_id=AGENT_ID_TRANSLATE,
            handler=_build_translate_delegate_handler(model_attempt_service),
            adapter_id="model_backed_translate.v1",
            execution_mode=GOVERNED_MODEL_DELEGATE_EXECUTION_MODE,
            review_mode=GOVERNED_MODEL_DELEGATE_REVIEW_MODE,
            budget_mode=GOVERNED_MODEL_DELEGATE_BUDGET_MODE,
        )

    return GovernedDelegateDispatcher(
        node_grants=node_grants,
        targets=targets,
    )


def _build_translate_delegate_handler(model_attempt_service: Any) -> ReadOnlyDelegateHandler:
    """Bind TranslateAgent to one canonical, Task-budgeted model call."""

    async def _handler(
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> Mapping[str, Any]:
        from host_brain.app.handlers.agent_boundary import (  # noqa: PLC0415
            first_public_text,
        )
        from host_brain.app.handlers.translate_agent_handler import (  # noqa: PLC0415
            TRANSLATE_MODEL_NODE_ID,
            translate_agent_handler,
        )
        from host_brain.model_runtime.governed_budget import (  # noqa: PLC0415
            GovernedModelBudgetError,
            build_governed_model_budget_guard,
        )
        from host_brain.model_runtime.graph_semantics import (  # noqa: PLC0415
            graph_model_semantic,
        )
        from host_brain.model_runtime.invocation import (  # noqa: PLC0415
            complete_compiled_prompt,
        )
        from host_brain.model_runtime.json_parser import (  # noqa: PLC0415
            extract_json_object,
        )
        from host_brain.model_runtime.trace import (  # noqa: PLC0415
            model_attempt_result_trace,
        )

        invocation = context.get("delegate_invocation")
        if not isinstance(invocation, GovernedDelegateInvocation):
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_INVOCATION_INVALID,
                "model-backed delegate requires its frozen invocation",
            )
        try:
            guard = build_governed_model_budget_guard(
                budget=invocation.budget,
                graph_id=invocation.graph_id,
                graph_revision=invocation.graph_revision,
                node_id=invocation.node_id,
                attempt_id=invocation.attempt_id,
                require_model_cost=False,
            )
        except GovernedModelBudgetError as exc:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_AUTHORITY_INVALID,
                exc.detail,
            ) from exc

        semantic = graph_model_semantic(TRANSLATE_MODEL_NODE_ID)
        result = await complete_compiled_prompt(
            attempt_service=model_attempt_service,
            prompt_id=semantic.prompt_id,
            messages=(
                {
                    "role": "system",
                    "content": (
                        "Translate the user's requested text while preserving meaning, "
                        "tone, names and technical terms. Return exactly one JSON object "
                        'with a non-empty "translated_text" string and no control fields.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"translation_request": invocation.user_message},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ),
            request_id=(
                f"{invocation.request_id}:delegate-model:{invocation.attempt_id}"
            ),
            scene_type=semantic.scene_type,
            cognitive_tier=semantic.cognitive_tier,
            deadline_ms=guard.deadline_ms,
            foreground=True,
            metadata={
                "caller": "governed_translate_delegate",
                "graph_id": invocation.graph_id,
                "graph_revision": invocation.graph_revision,
                "node_id": invocation.node_id,
                "model_node_id": semantic.model_node_id,
            },
            model_budget_guard=guard,
        )
        model_trace = model_attempt_result_trace(result)
        if not result.success:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_OUTPUT_INVALID,
                "translation model attempt failed: "
                + str(result.error_code or "MODEL_ATTEMPT_FAILED"),
            )
        try:
            translated_payload = extract_json_object(result.content)
        except ValueError as exc:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_OUTPUT_INVALID,
                "translation model returned no valid JSON object",
            ) from exc
        translated_text = first_public_text(translated_payload).strip()
        if not translated_text:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_OUTPUT_INVALID,
                "translation model returned no public translated text",
            )
        handler_result = translate_agent_handler(
            {
                **dict(payload),
                TRANSLATE_MODEL_NODE_ID: {
                    "gateway_status": "OK",
                    "payload": {
                        **translated_payload,
                        "translated_text": translated_text,
                    },
                },
            },
            context,
        )
        handler_result["delegate_model_attempt"] = model_trace
        handler_result["delegate_model_budget_guard"] = guard.as_dict()
        return handler_result

    return _handler


def _verify_node_authority(
    node_grants: NodeGrantVerifierPort,
    *,
    invocation: GovernedDelegateInvocation,
) -> None:
    # The outer GovernedFrozenNodeWorker owns the live Attempt/Lease/Fence
    # check before this inner boundary runs.  Keep those fields on the
    # immutable invocation and pass them through for review-continuation
    # verification, but do not misrepresent the grant-only projection below
    # as an independent lease/fence validator.
    try:
        verified = node_grants.verify(
            grant_ref=invocation.node_grant_ref,
            parent_grant_ref=invocation.parent_grant_ref,
            request_id=invocation.request_id,
            session_id=invocation.session_id,
            principal_id=invocation.principal_id,
            graph_id=invocation.graph_id,
            graph_revision=invocation.graph_revision,
            root_work_order_id=invocation.root_work_order_id,
            node_id=invocation.node_id,
            route="delegate",
            target_id=invocation.target_id,
            attempt_id=invocation.attempt_id,
            lease_id=invocation.lease_id,
            lease_owner_id=invocation.lease_owner_id,
            fencing_token=invocation.fencing_token,
        )
    except Exception as exc:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_AUTHORITY_INVALID,
            "delegate node grant could not be revalidated",
        ) from exc
    value = _mapping_projection(verified)
    expected = {
        "grant_ref": invocation.node_grant_ref,
        "parent_grant_ref": invocation.parent_grant_ref,
        "request_id": invocation.request_id,
        "session_id": invocation.session_id,
        "principal_id": invocation.principal_id,
        "graph_id": invocation.graph_id,
        "graph_revision": invocation.graph_revision,
        "root_work_order_id": invocation.root_work_order_id,
        "node_id": invocation.node_id,
        "route": "delegate",
        "target_id": invocation.target_id,
    }
    if {key: value.get(key) for key in expected} != expected:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_AUTHORITY_INVALID,
            "delegate grant verification projection drifted from frozen authority",
        )


def _handler_payload(invocation: GovernedDelegateInvocation) -> dict[str, Any]:
    return {
        "user_input": invocation.user_message,
        "query": invocation.user_message,
        "request_id": invocation.request_id,
        "task_node_id": invocation.node_id,
        "task_graph_id": invocation.graph_id,
        "task_graph_revision": invocation.graph_revision,
    }


def _handler_context(invocation: GovernedDelegateInvocation) -> dict[str, Any]:
    # Deliberately no gateway, tool registry, capability, mutable planner or
    # child-dispatch handle.  Registered adapters receive input material only.
    return {
        "request_id": invocation.request_id,
        "session_id": invocation.session_id,
        "source": "governed_delegate_dispatch",
        "delegate_invocation_ref": invocation.invocation_ref,
        "task": invocation.objective,
        "input_refs": list(invocation.input_refs),
        "context": invocation.context,
    }


def _project_readonly_outcome(
    *,
    target: GovernedDelegateTarget,
    invocation: GovernedDelegateInvocation,
    result: Mapping[str, Any],
) -> GovernedDelegateOutcome:
    payload = dict(result)
    _require_effect_free_result_payload(payload)
    trace = payload.get("pentest_agent_trace")
    execution = payload.get("pentest_execution")
    audit = payload.get("sub_agent_audit_refs")
    if (
        target.target_id != "pentest_agent"
        or not isinstance(trace, Mapping)
        or str(trace.get("agent_id") or "") != target.target_id
        or trace.get("side_effects_executed") is not False
        or not isinstance(execution, Mapping)
        or execution.get("accepted") is not False
        or execution.get("side_effects_executed") is not False
        or not isinstance(audit, Mapping)
        or audit.get("side_effects_executed") is not False
    ):
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_OUTPUT_INVALID,
            "read-only delegate adapter did not prove an effect-free handoff",
        )
    narrative = _public_result_text(payload)
    if not narrative:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_OUTPUT_INVALID,
            "read-only delegate adapter returned no public result",
        )
    return GovernedDelegateOutcome(
        success=True,
        target_id=target.target_id,
        result_text=narrative,
        error_text="",
        audit_refs={
            "delegate_invocation_ref": invocation.invocation_ref,
            "delegate_adapter": target.adapter_id,
            "delegate_execution_mode": target.execution_mode,
            "delegate_target_policy_ref": target.policy_projection()["policy_ref"],
            "delegate_review_mode": target.review_mode,
            "delegate_budget_mode": target.budget_mode,
            "delegate_effect_mode": target.effect_mode,
            "side_effects_executed": False,
            "delegate_runtime_usage_attestation": (
                build_readonly_delegate_usage_attestation(
                    command_id=invocation.command_id,
                    graph_id=invocation.graph_id,
                    graph_revision=invocation.graph_revision,
                    node_id=invocation.node_id,
                    attempt_id=invocation.attempt_id,
                    node_grant_ref=invocation.node_grant_ref,
                    target_id=invocation.target_id,
                    adapter_id=target.adapter_id,
                    invocation_ref=invocation.invocation_ref,
                )
            ),
            "handler_reason_code": str(trace.get("reason_code") or "").strip(),
            "handler_audit": {
                "domain_pack": audit.get("domain_pack"),
                "capability_id": audit.get("capability_id"),
                "secure_run_surface": audit.get("secure_run_surface"),
            },
        },
    )


def _project_model_backed_outcome(
    *,
    target: GovernedDelegateTarget,
    invocation: GovernedDelegateInvocation,
    result: Mapping[str, Any],
) -> GovernedDelegateOutcome:
    payload = dict(result)
    _require_effect_free_result_payload(payload)
    trace = payload.get("translate_agent_trace")
    model_attempt = payload.get("delegate_model_attempt")
    model_guard = payload.get("delegate_model_budget_guard")
    if (
        target.target_id != "translate_agent"
        or not isinstance(trace, Mapping)
        or str(trace.get("agent_id") or "") != target.target_id
        or trace.get("side_effects_executed") is not False
        or not isinstance(model_attempt, Mapping)
        or model_attempt.get("success") is not True
        or not isinstance(model_attempt.get("budget_measurement"), Mapping)
        or not isinstance(model_guard, Mapping)
    ):
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_OUTPUT_INVALID,
            "model-backed delegate did not return a measured effect-free result",
        )
    narrative = _public_result_text(payload)
    if not narrative:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_OUTPUT_INVALID,
            "model-backed delegate returned no public result",
        )
    return GovernedDelegateOutcome(
        success=True,
        target_id=target.target_id,
        result_text=narrative,
        error_text="",
        audit_refs={
            "delegate_invocation_ref": invocation.invocation_ref,
            "delegate_adapter": target.adapter_id,
            "delegate_execution_mode": target.execution_mode,
            "delegate_target_policy_ref": target.policy_projection()["policy_ref"],
            "delegate_review_mode": target.review_mode,
            "delegate_budget_mode": target.budget_mode,
            "delegate_effect_mode": target.effect_mode,
            "side_effects_executed": False,
            "delegate_model_attempt": dict(model_attempt),
            "delegate_model_budget_guard": dict(model_guard),
            "handler_reason_code": str(trace.get("reason_code") or "").strip(),
        },
    )


def _require_effect_free_result_payload(payload: Mapping[str, Any]) -> None:
    """Reject effect or delivery claims instead of silently discarding them."""

    nested = payload.get("payload")
    containers = (
        dict(payload),
        dict(nested) if isinstance(nested, Mapping) else {},
    )
    for value in containers:
        side_effects_executed = value.get("side_effects_executed")
        if side_effects_executed is not None and side_effects_executed is not False:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_OUTPUT_INVALID,
                "read-only delegate claimed an executed side effect",
            )
        delivery_authorized = value.get("delivery_authorized")
        if delivery_authorized is not None and delivery_authorized is not False:
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_OUTPUT_INVALID,
                "read-only delegate claimed delivery authority",
            )
        if any(
            value.get(key) not in (None, (), [])
            for key in (
                "acceptance_effect_intents",
                "effect_intents",
                "outbox",
                "outbox_messages",
            )
        ):
            raise GovernedDelegateDispatchError(
                GOVERNED_DELEGATE_OUTPUT_INVALID,
                "read-only delegate returned an effect or Outbox descriptor",
            )


def _public_result_text(payload: Mapping[str, Any]) -> str:
    output = payload.get("agent_output")
    text = read_public_agent_output(output).strip()
    if text:
        return text
    scratchpad = payload.get("react_scratchpad")
    text = read_public_agent_output(scratchpad).strip()
    if text:
        return text
    return read_decision_summary(scratchpad, fallback="").strip()


def _safe_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(value or {})
    leaked = sorted(
        str(key)
        for key in raw
        if str(key or "").strip().lower() in _FORBIDDEN_CONTEXT_KEYS
    )
    if leaked:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            "delegate context contains authority or nested-dispatch material: "
            + ", ".join(leaked),
        )
    try:
        normalized = json.loads(
            json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            "delegate context must be JSON-safe",
        ) from exc
    return dict(normalized) if isinstance(normalized, Mapping) else {}


def _mapping_projection(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    projector = getattr(value, "to_dict", None)
    projected = projector() if callable(projector) else None
    return dict(projected) if isinstance(projected, Mapping) else {}


def _required_identifier(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or not _IDENTIFIER.fullmatch(text):
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            f"{field_name} must be a safe identifier",
        )
    return text


def _required_ref(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 1024:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            f"{field_name} is required",
        )
    return text


def _required_text(value: Any, field_name: str) -> str:
    text = public_output_text(value).strip()
    if not text:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            f"{field_name} is required",
        )
    return text[:32_000]


def _positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            f"{field_name} must be a positive integer",
        ) from exc
    if parsed < 1:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            f"{field_name} must be a positive integer",
        )
    return parsed


def _json_copy_text(value: Mapping[str, Any], *, field_name: str) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GovernedDelegateDispatchError(
            GOVERNED_DELEGATE_INVOCATION_INVALID,
            f"{field_name} must be JSON-safe",
        ) from exc


__all__ = [
    "GOVERNED_DELEGATE_BUDGET_MODE",
    "GOVERNED_DELEGATE_EFFECT_MODE",
    "GOVERNED_DELEGATE_POLICY_SCHEMA_VERSION",
    "GOVERNED_DELEGATE_REVIEW_MODE",
    "GOVERNED_DELEGATE_AUTHORITY_INVALID",
    "GOVERNED_DELEGATE_INVOCATION_INVALID",
    "GOVERNED_DELEGATE_OUTPUT_INVALID",
    "GOVERNED_DELEGATE_TARGET_UNAVAILABLE",
    "GovernedDelegateDispatchError",
    "GovernedDelegateDispatcher",
    "GovernedDelegateInvocation",
    "GovernedDelegateOutcome",
    "GovernedDelegateTarget",
    "build_governed_delegate_dispatcher",
]
