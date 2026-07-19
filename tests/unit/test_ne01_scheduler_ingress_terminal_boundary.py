from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from host_brain.scheduler.command_service import SchedulerCommandService
from host_brain.scheduler.contracts import SchedulerOccurrence
from host_brain.scheduler.governed_runtime import SchedulerDefinitionPolicyEvaluator
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry


_NOW = 1_800_000_000_000
_BODY_SHA256 = "a" * 64


def _occurrence() -> SchedulerOccurrence:
    return SchedulerOccurrence.from_mapping(
        {
            "occurrence_id": "scheduleroccurrence://sha256/" + "b" * 64,
            "task_id": "terminal_boundary",
            "definition_revision": 1,
            "source": "manual",
            "scheduled_at_ms": _NOW,
            "payload": {},
            "request_id": "request-scheduler-terminal-boundary",
            "principal_id": "scheduler-service-principal",
            "claim_ref": "secure-ingress-claim://scheduler/terminal-boundary",
            "audit_ref": "audit://secure-ingress/scheduler/terminal-boundary",
            "body_sha256": _BODY_SHA256,
        }
    )


def _definition() -> dict:
    return {
        "task_id": "terminal_boundary",
        "definition_revision": 1,
        "head_version": 1,
        "enabled": True,
        "next_scheduled_at_ms": None,
        "definition": {
            "task_id": "terminal_boundary",
            "trigger_mode": "interval",
            "trigger_config": {"seconds": 60},
            "intent_template": {"skill_name": "proactive_talk", "args": {}},
            "worker_key": "intent_dispatch",
            "priority": "P1",
            "mode_allowlist": ["normal"],
            "resource_profile": {},
            "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
            "enabled": True,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("task_status", ["FAILED", "BUDGET_BLOCKED", "CANCELLED"])
async def test_pre_effect_task_terminal_never_completes_native_scheduler_claim(
    task_status: str,
) -> None:
    occurrence = _occurrence()
    terminal = {
        "done": True,
        "decision": "REJECT",
        "reason_code": f"GOVERNED_{task_status}",
        "execution_receipt": {
            # This is deliberately a Task worker/projection receipt.  It must
            # never be promoted into the Scheduler result authority.
            "receipt_ref": "workerprojection://scheduler/pre-effect-terminal",
            "status": task_status,
            "accepted": False,
            "projection": {},
        },
    }

    class Runtime:
        def existing_command(self, **_kwargs):
            return None

        async def execute_stream(self, **_kwargs):
            yield deepcopy(terminal)

    class Definitions:
        def get_occurrence(self, _occurrence_id):
            return occurrence.to_dict()

        def get_terminal_outcome(self, _occurrence_id):
            return None

        def get_revision(self, _task_id, _revision):
            return _definition()

    claim_completions: list[dict] = []
    workers = SchedulerWorkerRegistry()
    workers.register("intent_dispatch", lambda **_: {"status": "SUCCEEDED"})
    service = SchedulerCommandService(
        runtime=Runtime(),
        intent_engine=SimpleNamespace(
            evaluate_intent=lambda **_: {
                "decision": "APPROVE",
                "intent_id": "intent-scheduler-terminal-boundary",
                "ts_ms": _NOW,
            }
        ),
        claim_result_store=SimpleNamespace(
            complete_claim=lambda **kwargs: claim_completions.append(dict(kwargs))
        ),
        definition_repository=Definitions(),
        policy_evaluator=SchedulerDefinitionPolicyEvaluator(
            current_mode=lambda: "normal"
        ),
        worker_registry=workers,
    )
    authority = {
        "allowed": True,
        "duplicate": False,
        "request_id": occurrence.request_id,
        "principal_id": occurrence.principal_id,
        "claim_ref": occurrence.claim_ref,
        "audit_ref": occurrence.audit_ref,
        "body_sha256": occurrence.body_sha256,
    }

    first = await service.execute_occurrence(
        occurrence=occurrence,
        authority=authority,
        received_at_ms=_NOW,
    )
    replay = await service.execute_occurrence(
        occurrence=occurrence,
        authority={**authority, "allowed": False, "duplicate": True},
        received_at_ms=_NOW + 1,
    )

    assert first == terminal
    assert replay == terminal
    assert claim_completions == []
