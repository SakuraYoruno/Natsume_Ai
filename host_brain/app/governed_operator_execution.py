from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import hashlib
from typing import Any

from host_brain.app.governed_operator_contracts import (
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
)
from host_brain.app.governed_operator_edge_support import (
    EDGE_TARGET_ID,
    compile_frozen_edge_command,
)
from host_brain.app.governed_operation_reconciliation_runtime import (
    OPERATION_RECONCILIATION_TARGET_ID,
    operation_reconciliation_commit_event,
)
from host_brain.app.governed_effect_reconciliation_runtime import (
    EFFECT_RECONCILIATION_TARGET_ID,
    effect_reconciliation_commit_event,
)
from host_brain.app.governed_terminal_reconciliation_runtime import (
    TERMINAL_TARGET_ID,
    terminal_reconciliation_commit_event,
)
from host_brain.core.mode_transition import TransitionSource
from host_brain.task_runtime.governed_workers import FrozenNodeInput


SYS_MODE_TARGET_ID = "operator_sys_mode_switch"
SYS_MODE_EFFECT_TYPE = "host.sys_mode_transition"
SYS_MODE_ACTION_KIND = "operator_sys_mode_transition"
SYS_MODE_INGRESS_ACTION = "obs:command:sys_mode.switch"
SYS_MODE_INGRESS_PATH = "/v1/host/obs-commands/sys_mode.switch"
SYS_MODE_INGRESS_REQUIRED_SCOPES = ("obs:sys-mode:write", "obs:write")


class FrozenSysModeSwitchExecutor:
    """Compile supported frozen Operator commands into non-executable intents."""

    def __init__(
        self,
        *,
        runtime_scope_id: str,
        embodiment_runtime_scope_id: str | None = None,
    ) -> None:
        self._runtime_scope_id = require_operator_runtime_scope_id(runtime_scope_id)
        self._embodiment_runtime_scope_id = (
            require_operator_runtime_scope_id(embodiment_runtime_scope_id)
            if embodiment_runtime_scope_id is not None
            else None
        )

    async def execute_stream(
        self,
        frozen: FrozenNodeInput,
    ) -> AsyncIterator[Mapping[str, Any]]:
        if frozen.route == "delegate" and frozen.target_id == TERMINAL_TARGET_ID:
            yield terminal_reconciliation_commit_event(frozen)
            return
        if (
            frozen.route == "delegate"
            and frozen.target_id == OPERATION_RECONCILIATION_TARGET_ID
        ):
            yield operation_reconciliation_commit_event(frozen)
            return
        if (
            frozen.route == "delegate"
            and frozen.target_id == EFFECT_RECONCILIATION_TARGET_ID
        ):
            yield effect_reconciliation_commit_event(frozen)
            return
        if frozen.route == "delegate" and frozen.target_id == EDGE_TARGET_ID:
            yield compile_frozen_edge_command(
                frozen,
                runtime_scope_id=self._embodiment_runtime_scope_id,
            )
            return
        if frozen.route != "delegate" or frozen.target_id != SYS_MODE_TARGET_ID:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator route is not sys_mode.switch",
            )
        pack = dict(frozen.input_pack or {})
        intent = pack.get("intent_draft")
        if not isinstance(intent, Mapping):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator WorkOrder lacks intent_draft",
            )
        args = intent.get("args")
        if not isinstance(args, Mapping):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator intent lacks typed args",
            )
        target_mode = str(args.get("target_mode") or "").strip().lower()
        reason = str(args.get("reason") or "").strip()
        try:
            source = TransitionSource(
                str(args.get("source") or "").strip().lower()
            ).value
        except ValueError as exc:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator source is invalid",
            ) from exc
        if (
            str(intent.get("action") or "").strip() != SYS_MODE_ACTION_KIND
            or str(intent.get("target") or "").strip().lower() != "system"
            or str(intent.get("route") or "").strip().lower() != "delegate"
            or str(intent.get("target_id") or "").strip() != SYS_MODE_TARGET_ID
            or str(args.get("operator_command_id") or "").strip()
            != "sys_mode.switch"
            or not target_mode
            or not reason
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator intent binding drifted",
            )
        event_id = (
            "obs-sys-mode:"
            + hashlib.sha256(frozen.request_id.encode("utf-8")).hexdigest()
        )
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "request_id": frozen.request_id,
            "narrative": f"系统模式切换已通过执行验收：{target_mode}",
            "route_type": frozen.route,
            "target_id": frozen.target_id,
            "audit_refs": {
                "operator_command_id": "sys_mode.switch",
                "proposed_effect_type": SYS_MODE_EFFECT_TYPE,
            },
            "acceptance_effect_intents": [
                {
                    "effect_type": SYS_MODE_EFFECT_TYPE,
                    "commit_scope": "NODE",
                    "payload": {
                        "operator_command_id": "sys_mode.switch",
                        "request_id": frozen.request_id,
                        "target_mode": target_mode,
                        "source": source,
                        "reason": reason,
                        "event_id": event_id,
                        "runtime_scope_id": self._runtime_scope_id,
                    },
                }
            ],
        }


def require_operator_runtime_scope_id(value: Any) -> str:
    scope = str(value or "").strip()
    if (
        not scope
        or len(scope.encode("utf-8")) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in scope)
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator runtime_scope_id is invalid",
        )
    return scope


__all__ = [
    "FrozenSysModeSwitchExecutor",
    "SYS_MODE_ACTION_KIND",
    "SYS_MODE_EFFECT_TYPE",
    "SYS_MODE_INGRESS_ACTION",
    "SYS_MODE_INGRESS_PATH",
    "SYS_MODE_INGRESS_REQUIRED_SCOPES",
    "SYS_MODE_TARGET_ID",
    "require_operator_runtime_scope_id",
]
