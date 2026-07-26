"""Governed Operator runtime for Scheduler and Host mode-policy controls."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import inspect
from typing import Any

from host_brain.app.acceptance_effect_handlers import (
    sys_mode_transition_handler_receipt_ref,
)
from host_brain.app.accepted_operator_state_control_handler import (
    resolve_operator_state_control_outcome,
)
from host_brain.app.governed_operator_contracts import (
    GOVERNED_OPERATOR_GOVERNOR_REJECTED,
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
)
from host_brain.app.governed_operator_edge_support import (
    pending_operator_reviews,
)
from host_brain.app.governed_operator_execution import (
    SYS_MODE_EFFECT_TYPE,
    require_operator_runtime_scope_id,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.ids import now_ms
from host_brain.runtime.intent_admission_contracts import (
    INTENT_ADMISSION_REVIEW_REQUIRED,
)
from host_brain.task_runtime import GovernedRuntimeError
from host_brain.task_runtime.operator_state_control_support import (
    OPERATOR_STATE_CONTROL_EFFECT_TYPE,
    OPERATOR_STATE_CONTROL_TARGET_ID,
    SCHEDULER_ADMIN_CONTROL_COMMAND_IDS,
    STATE_CONTROL_COMMAND_IDS,
    build_operator_state_control_governor_intent,
    normalize_operator_state_control_args,
    normalize_scheduler_admin_control_args,
    operator_state_control_action_kind,
    operator_state_control_ingress_requirements,
    operator_state_control_operation_id,
    operator_state_control_target,
    validate_operator_state_control_args,
)
from natsume_contracts.task import HostCommand, HostCommandSurface


class GovernedOperatorStateControlMixin:
    """Admit state mutations into the shared immutable TaskGraph spine."""

    async def execute_operator_state_control(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        value = dict(payload or {})
        supplied = self._require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise _invalid("Operator state-control request_id is required")
        command_id = str(supplied.get("operator_command_id") or "").strip()
        ingress_action, ingress_path, required_scopes = (
            operator_state_control_ingress_requirements(command_id)
        )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = self._verify_operator_authority(
            supplied,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
            operator_command_id=command_id,
            ingress_action=ingress_action,
            ingress_path=ingress_path,
            required_scopes=required_scopes,
        )
        return await self._execute_state_control(
            payload=value,
            request_id=request_ref,
            command_id=command_id,
            authority=authority,
            surface=HostCommandSurface.OPERATOR,
            existing=existing,
            proposer_node_id="obs_secure_operator_ingress",
            model_snapshot="server-owned-operator-state-control",
            prompt_profile_id="operator-state-control-v1",
        )

    async def execute_scheduler_admin_control(
        self,
        *,
        command_id: str,
        payload: Mapping[str, Any],
        authority: Any,
        request_id: str,
        method: str,
        normalized_path: str,
    ) -> dict[str, Any]:
        """Admit one Scheduler HTTP mutation into the shared TaskGraph spine."""

        command = str(command_id or "").strip()
        if command not in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS:
            raise _invalid("Scheduler admin command is unsupported")
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise _invalid("Scheduler admin request_id is required")
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.SCHEDULER.value,
            request_id=request_ref,
        )
        verified = _verify_durable_scheduler_admin_authority(
            authority,
            resolver=self._secure_ingress_authority_resolver,
            request_id=request_ref,
            method=method,
            normalized_path=normalized_path,
            command_id=command,
            allow_settled_replay=isinstance(existing, Mapping),
        )
        return await self._execute_state_control(
            payload=dict(payload or {}),
            request_id=request_ref,
            command_id=command,
            authority=verified,
            surface=HostCommandSurface.SCHEDULER,
            existing=existing,
            proposer_node_id="scheduler_secure_admin_ingress",
            model_snapshot="server-owned-scheduler-admin-control",
            prompt_profile_id="scheduler-admin-control-v1",
        )

    async def _execute_state_control(
        self,
        *,
        payload: Mapping[str, Any],
        request_id: str,
        command_id: str,
        authority: Mapping[str, Any],
        surface: HostCommandSurface,
        existing: Mapping[str, Any] | None,
        proposer_node_id: str,
        model_snapshot: str,
        prompt_profile_id: str,
    ) -> dict[str, Any]:
        value = dict(payload or {})
        request_ref = str(request_id)
        principal_id = str(authority["principal_id"])
        received_at_ms = now_ms()
        if isinstance(existing, Mapping):
            command = existing.get("command")
            work_order = existing.get("work_order")
            if (
                not isinstance(command, HostCommand)
                or command.surface is not surface
                or not isinstance(work_order, WorkOrderV3)
                or str(
                    dict(work_order.metadata or {}).get("operator_command_id")
                    or ""
                ).strip()
                != command_id
            ):
                raise _invalid(
                    "durable state-control replay lacks exact command metadata"
                )
            intent_raw = dict(work_order.input_pack or {}).get("intent_draft")
            frozen_intent = (
                dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
            )
            args_raw = frozen_intent.get("args")
            args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
            validate_operator_state_control_args(command_id, args)
            _validate_replay_payload(command_id, value, args)
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
            received_at_ms = int(command.created_at_ms)
        else:
            args = await self._freeze_state_control_args(
                value,
                request_id=request_ref,
                principal_id=principal_id,
                command_id=command_id,
                observed_at_ms=received_at_ms,
                surface=surface,
                authority=authority,
            )
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=build_operator_state_control_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                        session_id=(
                            f"scheduler-admin-{request_ref}"
                            if surface is HostCommandSurface.SCHEDULER
                            else f"operator-{request_ref}"
                        ),
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id=proposer_node_id,
                    model_snapshot=model_snapshot,
                    prompt_profile_id=prompt_profile_id,
                )
                or {}
            )
        message = str(args["text"])
        intent_draft = {
            "route": "delegate",
            "target_id": OPERATOR_STATE_CONTROL_TARGET_ID,
            "domain": "organ_ops",
            "task": message,
            "action": operator_state_control_action_kind(command_id),
            "target": operator_state_control_target(command_id),
            "args": args,
        }
        if str(decision.get("decision") or "").strip().upper() != "APPROVE":
            raise GovernedOperatorRuntimeError(
                str(
                    decision.get("reason_code")
                    or GOVERNED_OPERATOR_GOVERNOR_REJECTED
                ),
                str(
                    decision.get("detail")
                    or "Governor rejected Operator state control"
                ),
            )
        terminal: dict[str, Any] | None = None
        try:
            async for raw in self._runtime.execute_stream(
                request_body={"input_source": surface.value},
                request_id=request_ref,
                session_id=(
                    f"scheduler-admin-{request_ref}"
                    if surface is HostCommandSurface.SCHEDULER
                    else f"operator-{request_ref}"
                ),
                ts_ms=received_at_ms,
                intent_draft=intent_draft,
                governor_result=decision,
                person_id=principal_id,
                sanitized_message=message,
                command_authority=authority,
            ):
                event = dict(raw or {})
                if bool(event.get("done")):
                    terminal = event
        except GovernedRuntimeError as exc:
            if exc.reason_code != INTENT_ADMISSION_REVIEW_REQUIRED:
                raise
            return self._pending_operator_state_control_review(
                request_id=request_ref,
                command_id=command_id,
                surface=surface,
            )
        if terminal is None:
            raise _invalid(
                "governed Operator state control emitted no terminal result"
            )
        receipt_raw = terminal.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        outcome = await require_durable_operator_state_control_outcome(
            task_store=self._task_store,
            scheduler_command_service_provider=(
                self._scheduler_command_service_provider
            ),
            mode_transition_service_provider=(
                self._mode_transition_service_provider
            ),
            secure_ingress_authority_resolver=(
                self._secure_ingress_authority_resolver
            ),
            execution_receipt=receipt,
            args=args,
        )
        accepted = (
            receipt.get("accepted") is True
            and str(receipt.get("status") or "").strip().upper() == "ACCEPTED"
        )
        replayed = receipt.get("replayed") is True
        success = bool(accepted and outcome["committed"])
        if success and surface is HostCommandSurface.SCHEDULER:
            await self._complete_scheduler_admin_claim(
                authority=authority,
                outcome=outcome,
                received_at_ms=received_at_ms,
            )
        return {
            "success": success,
            "side_effects_executed": bool(success and not replayed),
            "historical_side_effects_executed": success,
            "exact_replay": replayed,
            "operator_command_id": command_id,
            "execution_receipt": receipt,
            "acceptance_effect_receipt": outcome,
            "graph_id": receipt.get("graph_id"),
            "graph_revision": receipt.get("graph_revision"),
            "terminal_status": terminal.get("status"),
            **dict(outcome.get("public_result") or {}),
        }

    async def _freeze_state_control_args(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: str,
        principal_id: str,
        command_id: str,
        observed_at_ms: int,
        surface: HostCommandSurface,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        scheduler_version: int | None = None
        policy_revision: int | None = None
        if command_id in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS:
            service = self._scheduler_command_service_provider()
            reader = getattr(service, "get_definition", None)
            if not callable(reader):
                raise _invalid(
                    "app-local Scheduler definition reader is unavailable"
                )
            task_id = str(payload.get("task_id") or "").strip()
            current = reader(task_id)
            if inspect.isawaitable(current):
                current = await current
            if (
                command_id != "scheduler.task.publish"
                and not isinstance(current, Mapping)
            ):
                raise _invalid("Scheduler definition is unavailable")
            scheduler_version = (
                int(current.get("head_version") or 0)
                if isinstance(current, Mapping)
                else None
            )
            if surface is HostCommandSurface.SCHEDULER:
                args = normalize_scheduler_admin_control_args(
                    payload,
                    command_id=command_id,
                    request_id=request_id,
                    principal_id=principal_id,
                    observed_at_ms=observed_at_ms,
                    ingress_method=str(authority["method"]),
                    ingress_path=str(authority["normalized_path"]),
                    scheduler_head_version=scheduler_version,
                )
                if command_id == "scheduler.task.publish":
                    preflight = getattr(service, "preflight_definition", None)
                    if not callable(preflight):
                        raise _invalid(
                            "app-local Scheduler preflight is unavailable"
                        )
                    result = preflight(
                        {
                            "task_id": args["task_id"],
                            "definition_revision": 1,
                            "head_version": max(
                                1,
                                int(args["expected_head_version"]) + 1,
                            ),
                            "enabled": bool(
                                dict(args["definition"]).get(
                                    "enabled",
                                    True,
                                )
                            ),
                            "next_scheduled_at_ms": args[
                                "next_scheduled_at_ms"
                            ],
                            "definition": dict(args["definition"]),
                        }
                    )
                    if inspect.isawaitable(result):
                        await result
                return args
        else:
            service = self._mode_transition_service_provider()
            reader = getattr(service, "get_current_status", None)
            if not callable(reader):
                raise _invalid(
                    "app-local Host mode-policy reader is unavailable"
                )
            current = reader()
            if inspect.isawaitable(current):
                current = await current
            if not isinstance(current, Mapping):
                raise _invalid("Host mode-policy state is unavailable")
            policy_revision = int(current.get("policy_revision") or 0)
        return normalize_operator_state_control_args(
            payload,
            request_id=request_id,
            principal_id=principal_id,
            observed_at_ms=observed_at_ms,
            scheduler_head_version=scheduler_version,
            policy_revision=policy_revision,
        )

    async def _complete_scheduler_admin_claim(
        self,
        *,
        authority: Mapping[str, Any],
        outcome: Mapping[str, Any],
        received_at_ms: int,
    ) -> None:
        if str(authority.get("claim_result_ref") or "").strip():
            return
        public_raw = outcome.get("public_result")
        public = (
            dict(public_raw) if isinstance(public_raw, Mapping) else {}
        )
        result_ref = str(
            public.get("scheduler_operation_receipt_ref") or ""
        ).strip()
        service = self._scheduler_command_service_provider()
        complete = getattr(service, "complete_accepted_admin_claim", None)
        if not result_ref or not callable(complete):
            raise _invalid(
                "Scheduler admin Acceptance claim completion is unavailable"
            )
        result = complete(
            authority=authority,
            result_ref=result_ref,
            received_at_ms=received_at_ms,
        )
        if inspect.isawaitable(result):
            await result

    def _pending_operator_state_control_review(
        self,
        *,
        request_id: str,
        command_id: str,
        surface: HostCommandSurface = HostCommandSurface.OPERATOR,
    ) -> dict[str, Any]:
        durable = self._commands.get_by_request(
            surface=surface.value,
            request_id=request_id,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise _invalid(
                "durable Operator state-control Review is unavailable"
            )
        reviews = pending_operator_reviews(
            self._review_store,
            graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        return {
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "operator_command_id": command_id,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }

    async def _bind_operator_state_control_review_outcome(
        self,
        *,
        command_id: str,
        execution_receipt: Mapping[str, Any],
        args: Mapping[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if command_id not in STATE_CONTROL_COMMAND_IDS:
            return None
        outcome = await require_durable_operator_state_control_outcome(
            task_store=self._task_store,
            scheduler_command_service_provider=(
                self._scheduler_command_service_provider
            ),
            mode_transition_service_provider=(
                self._mode_transition_service_provider
            ),
            secure_ingress_authority_resolver=(
                self._secure_ingress_authority_resolver
            ),
            execution_receipt=execution_receipt,
            args=args,
        )
        result["acceptance_effect_receipt"] = outcome
        result["historical_side_effects_executed"] = True
        result.update(dict(outcome.get("public_result") or {}))
        return result


async def require_durable_operator_state_control_outcome(
    *,
    task_store: Any,
    scheduler_command_service_provider: Any,
    mode_transition_service_provider: Any,
    secure_ingress_authority_resolver: Any,
    execution_receipt: Mapping[str, Any],
    args: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = dict(execution_receipt or {})
    graph_id = str(receipt.get("graph_id") or "").strip()
    node_id = str(receipt.get("node_id") or "").strip()
    attempt_id = str(receipt.get("attempt_id") or "").strip()
    revision = receipt.get("graph_revision")
    if (
        not graph_id
        or type(revision) is not int
        or revision < 1
        or not node_id
        or not attempt_id
    ):
        raise _invalid("Operator state-control receipt identity is invalid")
    rows = tuple(
        task_store.list_acceptance_effect_intents(
            graph_id=graph_id,
            graph_revision=revision,
            node_id=node_id,
            limit=10,
        )
        or ()
    )
    effects = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("effect_type") or "")
        == OPERATOR_STATE_CONTROL_EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise _invalid(
            "Operator state control must bind one durable Acceptance Effect"
        )
    effect = effects[0]
    payload_raw = effect.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    preview = payload.pop("_action_preview_context", None)
    command_id = str(args.get("operator_command_id") or "")
    expected_state_args = {
        str(key): value
        for key, value in args.items()
        if str(key) not in {"text", "route", "target_id"}
    }
    expected_payload = {
        "operator_command_id": command_id,
        "request_id": str(args.get("request_id") or ""),
        "operation_id": operator_state_control_operation_id(
            request_id=str(args.get("request_id") or ""),
            command_id=command_id,
        ),
        "principal_id": str(args.get("operator_actor_id") or ""),
        "ingress_claim_ref": str(payload.get("ingress_claim_ref") or ""),
        "ingress_audit_ref": str(payload.get("ingress_audit_ref") or ""),
        "input_sha256": str(payload.get("input_sha256") or ""),
        "state_args": expected_state_args,
    }
    if (
        str(effect.get("status") or "").upper() != "COMMITTED"
        or str(effect.get("attempt_id") or "") != attempt_id
        or payload != expected_payload
        or not isinstance(preview, Mapping)
        or not str(effect.get("receipt_ref") or "").strip()
    ):
        raise _invalid(
            "Operator state-control Acceptance Effect binding drifted"
        )
    outcome = await resolve_operator_state_control_outcome(
        payload,
        scheduler_command_service_provider=(
            scheduler_command_service_provider
        ),
        mode_transition_service_provider=mode_transition_service_provider,
        secure_ingress_authority_resolver=(
            secure_ingress_authority_resolver
        ),
    )
    if str(effect.get("receipt_ref") or "") != outcome["receipt_ref"]:
        raise _invalid("Operator state-control handler receipt drifted")
    return {
        "committed": True,
        "effect_id": str(effect.get("effect_id") or ""),
        "effect_type": OPERATOR_STATE_CONTROL_EFFECT_TYPE,
        "handler_receipt_ref": str(outcome["receipt_ref"]),
        "operation_id": payload["operation_id"],
        "committed_ts_ms": int(effect.get("committed_ts_ms") or 0),
        "public_result": {
            key: value
            for key, value in outcome.items()
            if key
            not in {"receipt_ref", "operator_command_id", "operation_id"}
        },
    }


def require_durable_sys_mode_outcome(
    *,
    task_store: Any,
    mode_transition_service_provider: Any,
    execution_receipt: Mapping[str, Any],
    request_id: str,
    target_mode: str,
    source: str,
    reason: str,
    runtime_scope_id: str,
) -> dict[str, Any]:
    receipt = dict(execution_receipt or {})
    graph_id = str(receipt.get("graph_id") or "").strip()
    node_id = str(receipt.get("node_id") or "").strip()
    attempt_id = str(receipt.get("attempt_id") or "").strip()
    try:
        graph_revision = int(receipt.get("graph_revision") or 0)
    except (TypeError, ValueError) as exc:
        raise _invalid(
            "Operator execution receipt graph revision is invalid"
        ) from exc
    list_effects = getattr(task_store, "list_acceptance_effect_intents", None)
    if (
        not graph_id
        or graph_revision < 1
        or not node_id
        or not attempt_id
        or not callable(list_effects)
    ):
        raise _invalid(
            "Operator execution receipt cannot reopen durable Acceptance Effect"
        )
    rows = tuple(
        list_effects(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            limit=10,
        )
        or ()
    )
    effects = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("effect_type") or "").strip()
        == SYS_MODE_EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise _invalid(
            "Operator execution must bind one durable mode Acceptance Effect"
        )
    effect = effects[0]
    payload_raw = effect.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    expected_event_id = (
        "obs-sys-mode:"
        + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    )
    expected_payload = {
        "operator_command_id": "sys_mode.switch",
        "request_id": request_id,
        "target_mode": target_mode,
        "source": source,
        "reason": reason,
        "event_id": expected_event_id,
        "runtime_scope_id": require_operator_runtime_scope_id(
            runtime_scope_id
        ),
    }
    business_payload = {key: payload.get(key) for key in expected_payload}
    expected_handler_receipts = {
        status: sys_mode_transition_handler_receipt_ref(
            payload,
            outcome_status=status,
        )
        for status in ("APPLIED", "REPLAYED", "NO_CHANGE")
    }
    persisted_receipt = str(effect.get("receipt_ref") or "").strip()
    persisted_status = next(
        (
            status
            for status, receipt_ref in expected_handler_receipts.items()
            if hmac.compare_digest(persisted_receipt, receipt_ref)
        ),
        None,
    )
    if (
        str(effect.get("status") or "").strip().upper() != "COMMITTED"
        or str(effect.get("graph_id") or "").strip() != graph_id
        or int(effect.get("graph_revision") or 0) != graph_revision
        or str(effect.get("node_id") or "").strip() != node_id
        or str(effect.get("attempt_id") or "").strip() != attempt_id
        or business_payload != expected_payload
        or persisted_status is None
        or int(effect.get("committed_ts_ms") or 0) <= 0
    ):
        raise _invalid("Operator durable Acceptance Effect receipt binding drifted")

    service = mode_transition_service_provider()
    reopen_commit = getattr(service, "reopen_transition_commit", None)
    if not callable(reopen_commit):
        raise _invalid("Operator HardState exact history is unavailable")
    commit = reopen_commit(
        event_id=expected_event_id,
        target_mode=target_mode,
        source=source,
        reason=reason,
    )
    hard_state_event: dict[str, Any] | None = None
    if persisted_status == "NO_CHANGE":
        if commit is not None:
            raise _invalid(
                "Operator NO_CHANGE receipt conflicts with a durable event"
            )
    else:
        if commit is None:
            raise _invalid(
                "Operator applied receipt lost its durable HardState event"
            )
        event = getattr(commit, "event", None)
        digest = str(getattr(event, "event_digest", "") or "").strip().lower()
        event_scope_id = str(
            getattr(event, "runtime_scope_id", "") or ""
        ).strip()
        if (
            getattr(commit, "exact_replay", None) is not True
            or str(getattr(event, "event_id", "") or "").strip()
            != expected_event_id
            or str(getattr(event, "event_kind", "") or "").strip()
            != "MODE_CHANGED"
            or str(getattr(event, "to_mode", "") or "").strip().lower()
            != target_mode
            or str(getattr(event, "source", "") or "").strip().lower() != source
            or str(getattr(event, "reason", "") or "") != reason
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or event_scope_id != expected_payload["runtime_scope_id"]
            or int(getattr(event, "revision", 0) or 0) < 2
            or int(getattr(event, "runtime_fencing_token", 0) or 0) < 1
        ):
            raise _invalid("Operator HardState event receipt binding drifted")
        hard_state_event = {
            "event_id": expected_event_id,
            "event_digest": digest,
            "revision": int(event.revision),
            "runtime_scope_id": event_scope_id,
            "from_mode": str(event.from_mode or "").strip().lower(),
            "to_mode": target_mode,
            "source": source,
        }

    return {
        "committed": True,
        "effect_id": str(effect.get("effect_id") or ""),
        "effect_type": SYS_MODE_EFFECT_TYPE,
        "handler_receipt_ref": persisted_receipt,
        "committed_ts_ms": int(effect["committed_ts_ms"]),
        "event_id": expected_event_id,
        "hard_state_event": hard_state_event,
        "historical_status": persisted_status,
    }


def current_mode(service_provider: Any) -> str:
    service = service_provider()
    get_status = getattr(service, "get_current_status", None)
    if not callable(get_status):
        raise _invalid("app-local mode transition service is unavailable")
    status = get_status()
    mode = str(dict(status or {}).get("sys_mode") or "").strip().lower()
    if not mode:
        raise _invalid("app-local sys_mode projection is unavailable")
    return mode


def _validate_replay_payload(
    command_id: str,
    supplied: Mapping[str, Any],
    frozen: Mapping[str, Any],
) -> None:
    if command_id == "scheduler.task.publish":
        raw_definition = supplied.get("definition")
        definition = (
            dict(raw_definition) if isinstance(raw_definition, Mapping) else {}
        )
        definition["task_id"] = str(
            supplied.get("task_id") or ""
        ).strip()
        definition["trigger_mode"] = str(
            definition.get("trigger_mode") or ""
        ).strip().lower()
        definition["enabled"] = bool(definition.get("enabled", True))
        if (
            definition != dict(frozen.get("definition") or {})
            or supplied.get("expected_head_version")
            != frozen.get("expected_head_version")
            or supplied.get("next_scheduled_at_ms")
            != frozen.get("next_scheduled_at_ms")
        ):
            raise _invalid("Scheduler publish replay input drifted")
        return
    if command_id in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS:
        expected = supplied.get("expected_head_version")
        if (
            str(supplied.get("task_id") or "").strip()
            != str(frozen.get("task_id") or "")
            or (
                expected is not None
                and expected != frozen.get("expected_head_version")
            )
        ):
            raise _invalid("Scheduler state-control replay input drifted")
        return
    if command_id == "sys_mode.auto_rules.update":
        if dict(supplied.get("rules") or {}) != dict(frozen.get("rules") or {}):
            raise _invalid("Host mode rules replay input drifted")
        return
    if (
        str(supplied.get("event_type") or "").strip()
        != str(frozen.get("event_type") or "")
    ):
        raise _invalid("Host mode event replay input drifted")


def _verify_durable_scheduler_admin_authority(
    authority: Any,
    *,
    resolver: Any,
    request_id: str,
    method: str,
    normalized_path: str,
    command_id: str,
    allow_settled_replay: bool,
) -> dict[str, Any]:
    to_dict = getattr(authority, "to_dict", None)
    supplied = (
        dict(authority)
        if isinstance(authority, Mapping)
        else dict(to_dict())
        if callable(to_dict)
        else {}
    )
    request_ref = str(request_id or "").strip()
    method_value = str(method or "").strip().upper()
    path = str(normalized_path or "").strip()
    command = str(command_id or "").strip()
    if (
        command not in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS
        or method_value not in {"POST", "PATCH", "DELETE"}
        or not path.startswith("/")
        or not bool(supplied.get("allowed") or supplied.get("duplicate"))
        or bool(supplied.get("duplicate")) and not allow_settled_replay
    ):
        raise _invalid("Scheduler admin Secure Ingress authority is invalid")
    audit_ref = _authority_field(supplied, "audit_ref")
    durable_raw = resolver.resolve_committed_authority_by_audit(
        audit_ref=audit_ref,
    )
    if not isinstance(durable_raw, Mapping):
        raise _invalid(
            "Scheduler admin Secure Ingress claim/audit is not durable"
        )
    durable = dict(durable_raw)
    supplied_body = _authority_digest(supplied.get("body_sha256"))
    durable_body = _authority_digest(durable.get("body_sha256"))
    expected_scopes = ("scheduler:admin",)
    claim_result_ref = str(
        durable.get("claim_result_ref") or ""
    ).strip()
    valid = all(
        (
            _authority_field(supplied, "request_id") == request_ref,
            _authority_field(durable, "request_id") == request_ref,
            _authority_field(supplied, "claim_ref")
            == _authority_field(durable, "claim_ref"),
            audit_ref == _authority_field(durable, "audit_ref"),
            _authority_field(supplied, "principal_id")
            == _authority_field(durable, "principal_id"),
            _authority_field(durable, "surface") == "scheduler.admin",
            _authority_field(durable, "action") == "scheduler:admin",
            _authority_field(durable, "method").upper() == method_value,
            _authority_field(durable, "normalized_path") == path,
            _authority_scopes(durable.get("required_scopes"))
            == expected_scopes,
            set(expected_scopes).issubset(
                _authority_scopes(durable.get("requested_scopes"))
            ),
            _authority_field(durable, "decision").upper() == "ALLOW",
            _authority_field(durable, "reason_code")
            == "SECURE_INGRESS_ALLOWED",
            _authority_field(durable, "stage").upper() == "COMPLETE",
            durable.get("side_effects_executed") is False,
            _authority_field(durable, "claim_status").upper()
            == "COMMITTED",
            not claim_result_ref or allow_settled_replay,
        )
    )
    if not valid or not hmac.compare_digest(supplied_body, durable_body):
        raise _invalid("Scheduler admin Secure Ingress binding drifted")
    return {
        "allowed": True,
        "duplicate": bool(claim_result_ref),
        "request_id": request_ref,
        "principal_id": _authority_field(durable, "principal_id"),
        "tenant_id": _authority_field(durable, "tenant_id"),
        "claim_ref": _authority_field(durable, "claim_ref"),
        "audit_ref": audit_ref,
        "body_sha256": durable_body,
        "action": "scheduler:admin",
        "required_scopes": expected_scopes,
        "ingress_surface": "scheduler.admin",
        "method": method_value,
        "normalized_path": path,
        "operator_command_id": command,
        "ingress_claim_ref": _authority_field(durable, "claim_ref"),
        "ingress_audit_ref": audit_ref,
        "claim_result_ref": claim_result_ref or None,
    }


def _authority_field(value: Mapping[str, Any], field: str) -> str:
    text = str(value.get(field) or "").strip()
    if not text or any(
        ord(character) < 32 or ord(character) == 127
        for character in text
    ):
        raise _invalid(f"Scheduler authority field {field} is invalid")
    return text


def _authority_digest(value: Any) -> str:
    digest = str(value or "").strip().lower().removeprefix("sha256:")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise _invalid("Scheduler authority body digest is invalid")
    return digest


def _authority_scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise _invalid("Scheduler authority scopes are invalid")
    return tuple(sorted(set(item.strip() for item in value)))


def _invalid(detail: str) -> GovernedOperatorRuntimeError:
    return GovernedOperatorRuntimeError(
        GOVERNED_OPERATOR_RUNTIME_INVALID,
        detail,
    )


__all__ = [
    "GovernedOperatorStateControlMixin",
    "current_mode",
    "require_durable_operator_state_control_outcome",
    "require_durable_sys_mode_outcome",
]
