from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.contracts.host_hard_state import (
    HostHardStateCommitV1,
    HostHardStateEventV1,
    HostHardStateSnapshotV1,
)
from host_brain.core.mode_transition import TransitionSource
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.runtime.action_preview_proof import ActionPreviewExecutionProof
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED,
    AcceptanceEffectOutcomeUnknown,
)


_EVENT_ID = "obs-sys-mode:" + "a" * 64
_PREVIEW_EVENT_ID = "runtimeevent://sha256/" + "b" * 64
_EFFECT_ID = "acceptance-effect://sha256/" + "c" * 64
_RUNTIME_SCOPE_ID = "runtime-scope-operator-handler"


def _base_payload() -> dict[str, Any]:
    return {
        "operator_command_id": "sys_mode.switch",
        "request_id": "obs-command-mode-switch",
        "target_mode": "focus",
        "source": "manual",
        "reason": "focused work",
        "event_id": _EVENT_ID,
        "runtime_scope_id": _RUNTIME_SCOPE_ID,
    }


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _boundary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_kind": "acceptance-effect.host.sys_mode_transition",
        "action_scope": _EFFECT_ID,
        "risk_level": 4,
        "action_args": dict(payload),
        "context_pack_digest": "sha256:" + "d" * 64,
        "scene_identity": {
            "schema": "natsume.scene-identity.v2",
            "workspace_id": "workspace-operator",
            "subject_id": "operator",
            "scene_id": "release-maintenance",
            "revision": 2,
            "decision_digest": "sha256:" + "e" * 64,
        },
        "task_graph_id": "graph-operator",
        "graph_revision": 1,
        "task_node_id": "node-operator",
        "parent_governor_grant_ref": "governor-grant://operator/parent",
        "node_grant_ref": "governor-grant://operator/node",
        "node_grant": {"grant_ref": "governor-grant://operator/node"},
        "authority_context": {
            "schema_version": (
                "Natsume.action_preview.acceptance_effect_authority_request.v1"
            ),
            "effect_id": _EFFECT_ID,
            "lease_owner_id": "operator-effect-owner",
            "fencing_token": 7,
        },
    }


def _preview_service(require_execution=None) -> ActionPreviewService:  # noqa: ANN001
    service = ActionPreviewService(
        runtime_event_store=object(),
        guardian=SimpleNamespace(evaluate_capability=lambda **_kwargs: {}),
        authority_resolver=SimpleNamespace(resolve=lambda **_kwargs: None),
    )
    service.require_execution = (  # type: ignore[method-assign]
        require_execution or (lambda **_kwargs: SimpleNamespace())
    )
    return service


def _preview_receipt(boundary: Mapping[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        payload={
            "authority_kind": "ACCEPTANCE_EFFECT",
            "action_kind": boundary["action_kind"],
            "action_scope": boundary["action_scope"],
            "risk_level": boundary["risk_level"],
            "action_args_digest": _digest(boundary["action_args"]),
        }
    )


def _proof(
    payload: Mapping[str, Any],
    *,
    event_id: str = _PREVIEW_EVENT_ID,
    boundary: Mapping[str, Any] | None = None,
    require_execution=None,  # noqa: ANN001
) -> ActionPreviewExecutionProof:
    exact_boundary = dict(boundary or _boundary(payload))

    if require_execution is None:

        def require_execution(**_kwargs):  # noqa: ANN202
            return _preview_receipt(exact_boundary)

    return ActionPreviewExecutionProof(
        service=_preview_service(require_execution),
        event_id=event_id,
        boundary=exact_boundary,
    )


def _payload(
    *,
    include_terminal_preview: bool = True,
    terminal_proof: Any | None = None,
) -> dict[str, Any]:
    payload = _base_payload()
    if include_terminal_preview:
        payload["_action_preview_terminal_proof"] = terminal_proof or _proof(payload)
    return payload


class _ModeTransitionService:
    def __init__(self, result: Mapping[str, Any]) -> None:
        self._result = dict(result)
        self.calls: list[dict[str, Any]] = []

    def switch_mode(
        self,
        target_mode: str,
        *,
        source: TransitionSource,
        reason: str,
        event_id: str,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "target_mode": target_mode,
                "source": source,
                "reason": reason,
                "event_id": event_id,
            }
        )
        return dict(self._result)

    def reopen_transition_commit(self, **kwargs: Any):  # noqa: ANN201
        if bool(self._result.get("no_change")):
            return None
        event = HostHardStateEventV1(
            runtime_scope_id=_RUNTIME_SCOPE_ID,
            revision=2,
            event_id=str(kwargs["event_id"]),
            event_kind="MODE_CHANGED",
            from_mode="normal",
            to_mode=str(kwargs["target_mode"]),
            source=str(kwargs["source"]),
            reason=str(kwargs["reason"]),
            previous_event_digest="sha256:" + "a" * 64,
            event_digest="sha256:" + "b" * 64,
            runtime_owner_id="runtime-owner-handler",
            runtime_lease_id="runtime-lease-handler",
            runtime_fencing_token=1,
            runtime_issued_at_ms=1,
            runtime_expires_at_ms=10_000,
            transition_ts_ms=2,
            created_at_ms=2,
        )
        return HostHardStateCommitV1(
            snapshot=HostHardStateSnapshotV1(
                runtime_scope_id=_RUNTIME_SCOPE_ID,
                revision=2,
                sys_mode=str(kwargs["target_mode"]),
                event_id=event.event_id,
                event_digest=event.event_digest,
                updated_at_ms=2,
            ),
            event=event,
            exact_replay=True,
        )


def _handler(service: _ModeTransitionService):
    handlers = build_production_acceptance_effect_handlers(
        mode_transition_service_provider=lambda: service,
    )
    return handlers["host.sys_mode_transition"]


def test_sys_mode_acceptance_handler_applies_with_app_local_service() -> None:
    service = _ModeTransitionService(
        {
            "success": True,
            "from_mode": "normal",
            "to_mode": "focus",
            "event_id": _EVENT_ID,
            "exact_replay": False,
            "no_change": False,
        }
    )

    result = _handler(service)(_payload())

    assert result["status"] == "APPLIED"
    assert result["receipt_ref"].startswith("host-sys-mode-applied://sha256/")
    assert result["runtime_scope_id"] == _RUNTIME_SCOPE_ID
    assert service.calls == [
        {
            "target_mode": "focus",
            "source": TransitionSource.MANUAL,
            "reason": "focused work",
            "event_id": _EVENT_ID,
        }
    ]


@pytest.mark.parametrize(
    ("result_flags", "expected_status"),
    [
        ({"exact_replay": True, "no_change": False}, "REPLAYED"),
        ({"exact_replay": False, "no_change": True}, "NO_CHANGE"),
    ],
)
def test_sys_mode_acceptance_handler_projects_exact_replay_and_no_change(
    result_flags: dict[str, bool],
    expected_status: str,
) -> None:
    service = _ModeTransitionService(
        {
            "success": True,
            "from_mode": "focus",
            "to_mode": "focus",
            "event_id": _EVENT_ID,
            **result_flags,
        }
    )

    result = _handler(service)(_payload())

    assert result["status"] == expected_status
    assert result["receipt_ref"].startswith(
        "host-sys-mode-no-change://sha256/"
        if expected_status == "NO_CHANGE"
        else "host-sys-mode-replayed://sha256/"
    )
    assert len(service.calls) == 1


def test_sys_mode_acceptance_handler_propagates_authoritatively_absent_failure() -> None:
    class RejectedModeTransitionService(_ModeTransitionService):
        def reopen_transition_commit(self, **_kwargs: Any) -> None:
            return None

    service = RejectedModeTransitionService(
        {"success": False, "error": "MODE_TRANSITION_CONFLICT"}
    )

    with pytest.raises(RuntimeError, match="MODE_TRANSITION_CONFLICT"):
        _handler(service)(_payload())

    assert len(service.calls) == 1


def test_sys_mode_acceptance_handler_reconciles_negative_projection_by_exact_event() -> None:
    service = _ModeTransitionService(
        {"success": False, "error": "MODE_TRANSITION_RESPONSE_LOST"}
    )

    recovered = _handler(service)(_payload())

    assert recovered["success"] is True
    assert recovered["status"] == "REPLAYED"
    assert recovered["exact_replay"] is True
    assert recovered["event_id"] == _EVENT_ID
    assert recovered["to_mode"] == "focus"
    assert recovered["runtime_scope_id"] == _RUNTIME_SCOPE_ID
    assert recovered["receipt_ref"].startswith(
        "host-sys-mode-replayed://sha256/"
    )
    assert len(service.calls) == 1


def test_sys_mode_acceptance_handler_keeps_negative_projection_unknown_until_read_recovers() -> None:
    class RecoverableModeTransitionService(_ModeTransitionService):
        def __init__(self) -> None:
            super().__init__(
                {
                    "success": False,
                    "error": "MODE_TRANSITION_RESPONSE_LOST",
                }
            )
            self.reopen_fails = True
            self.reopen_calls: list[dict[str, Any]] = []

        def reopen_transition_commit(self, **kwargs: Any):  # noqa: ANN201
            self.reopen_calls.append(dict(kwargs))
            if self.reopen_fails:
                raise OSError("HardState reader temporarily unavailable")
            return super().reopen_transition_commit(**kwargs)

    service = RecoverableModeTransitionService()
    handler = _handler(service)

    with pytest.raises(AcceptanceEffectOutcomeUnknown) as unknown:
        handler(_payload())

    assert unknown.value.reason_code == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED
    assert _EVENT_ID in unknown.value.detail
    assert "HardState reader temporarily unavailable" in unknown.value.detail
    assert len(service.calls) == 1
    assert len(service.reopen_calls) == 1

    # A restarted dispatcher invokes the same frozen event identity.  Even
    # when the service again reports a negative projection, exact HardState
    # history settles the canonical commit without producing a second event.
    service.reopen_fails = False
    recovered = handler(_payload())

    assert recovered["status"] == "REPLAYED"
    assert recovered["receipt_ref"].startswith(
        "host-sys-mode-replayed://sha256/"
    )
    assert [call["event_id"] for call in service.calls] == [_EVENT_ID, _EVENT_ID]
    assert [call["event_id"] for call in service.reopen_calls] == [
        _EVENT_ID,
        _EVENT_ID,
    ]


def test_sys_mode_acceptance_handler_requires_terminal_preview_before_service() -> None:
    service = _ModeTransitionService(
        {
            "success": True,
            "to_mode": "focus",
            "event_id": _EVENT_ID,
        }
    )

    with pytest.raises(ValueError, match="terminal ActionPreview proof"):
        _handler(service)(_payload(include_terminal_preview=False))

    assert service.calls == []


def test_sys_mode_acceptance_handler_rejects_forged_mapping_before_service() -> None:
    service = _ModeTransitionService(
        {
            "success": True,
            "to_mode": "focus",
            "event_id": _EVENT_ID,
        }
    )
    payload = _base_payload()
    forged = {
        "service": _preview_service(),
        "event_id": _PREVIEW_EVENT_ID,
        "boundary": _boundary(payload),
    }

    with pytest.raises(ValueError, match="typed terminal ActionPreview proof"):
        _handler(service)(_payload(terminal_proof=forged))

    assert service.calls == []


def test_sys_mode_acceptance_handler_reopens_exact_preview_event_before_service() -> None:
    service = _ModeTransitionService(
        {
            "success": True,
            "to_mode": "focus",
            "event_id": _EVENT_ID,
        }
    )
    payload = _base_payload()
    exact_boundary = _boundary(payload)
    reopen_calls: list[dict[str, Any]] = []

    def require_execution(**kwargs):  # noqa: ANN202
        reopen_calls.append(dict(kwargs))
        if kwargs.get("event_id") != _PREVIEW_EVENT_ID:
            raise ValueError("preview event identity drifted")
        return _preview_receipt(exact_boundary)

    wrong_event_proof = _proof(
        payload,
        event_id="runtimeevent://sha256/" + "f" * 64,
        boundary=exact_boundary,
        require_execution=require_execution,
    )

    with pytest.raises(ValueError, match="preview event identity drifted"):
        _handler(service)(_payload(terminal_proof=wrong_event_proof))

    assert len(reopen_calls) == 1
    assert service.calls == []


def test_sys_mode_acceptance_handler_rejects_boundary_drift_before_service() -> None:
    service = _ModeTransitionService(
        {
            "success": True,
            "to_mode": "focus",
            "event_id": _EVENT_ID,
        }
    )
    payload = _base_payload()
    drifted_boundary = _boundary(payload)
    drifted_boundary["action_args"] = {
        **payload,
        "target_mode": "gaming",
    }
    reopen_calls: list[dict[str, Any]] = []

    def require_execution(**kwargs):  # noqa: ANN202
        reopen_calls.append(dict(kwargs))
        return _preview_receipt(drifted_boundary)

    drifted_proof = _proof(
        payload,
        boundary=drifted_boundary,
        require_execution=require_execution,
    )

    with pytest.raises(ValueError, match="Preview boundary drifted"):
        _handler(service)(_payload(terminal_proof=drifted_proof))

    assert reopen_calls == []
    assert service.calls == []
