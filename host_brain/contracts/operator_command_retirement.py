"""Frozen retirement policy for legacy OBS mutation commands.

These commands previously appeared in the authenticated Operator manifest but
never entered the canonical HostCommand -> TaskGraph execution spine.  Keeping
them as "blocked active commands" made the public contract advertise writers
that could never be accepted and preserved dormant direct-writer adapters.

The policy is intentionally data-only.  A retired command may return only
after its replacement authority is implemented and its own governed contract
is reviewed; callers must not infer compatibility from a replacement label.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetiredOperatorCommandV1:
    command_id: str
    replacement_authority: str
    replacement_wave: str
    reason_code: str = "OPERATOR_COMMAND_RETIRED"


RETIRED_OPERATOR_COMMANDS_V1: tuple[RetiredOperatorCommandV1, ...] = (
    RetiredOperatorCommandV1(
        "avatar.behavior.apply",
        "edge.command.push",
        "NE-01",
    ),
    RetiredOperatorCommandV1(
        "mate.action.submit",
        "edge.command.push",
        "NE-01",
    ),
    RetiredOperatorCommandV1(
        "mate.say.submit",
        "edge.command.push",
        "NE-01",
    ),
    RetiredOperatorCommandV1(
        "tool.execute",
        "governed Skill/Tool TaskGraph route",
        "NE-01",
    ),
    RetiredOperatorCommandV1(
        "security.hunt.run_once",
        "governed Internal/Scheduler security-hunt route",
        "NE-01",
    ),
    RetiredOperatorCommandV1(
        "bond.event.submit",
        "FeedbackProposalV1 activation authority",
        "NE-06",
    ),
    RetiredOperatorCommandV1(
        "capability_forge.runtime.match",
        "PlanningSearch and maturity decision authority",
        "NE-02/NE-05",
    ),
    *(
        RetiredOperatorCommandV1(
            command_id,
            "Version Activation Service",
            "NE-05",
        )
        for command_id in (
            "capability_forge.proposal.create",
            "capability_forge.proposal.approve",
            "capability_forge.proposal.rollback",
        )
    ),
    *(
        RetiredOperatorCommandV1(
            command_id,
            "Knowledge proposal/version activation authority",
            "NE-06",
        )
        for command_id in (
            "canon.proposal.submit",
            "canon.proposal.approve",
            "canon.proposal.reject",
            "canon.proposal.rollback",
        )
    ),
    *(
        RetiredOperatorCommandV1(
            command_id,
            "FeedbackProposalV1 and Version Activation Service",
            "NE-06",
        )
        for command_id in (
            "bio.daily_cycle.run",
            "bio.adoption.accept",
            "bio.adoption.reject",
            "bio.owner_inbox.materialize",
            "bio.owner_inbox.intake_packets",
            "bio.owner_queue.consume",
            "bio.owner_state.import",
            "bio.owner_state.accept",
            "bio.owner_state.reject",
        )
    ),
)

RETIRED_OPERATOR_COMMAND_IDS = frozenset(
    policy.command_id for policy in RETIRED_OPERATOR_COMMANDS_V1
)
if len(RETIRED_OPERATOR_COMMAND_IDS) != 23:
    raise RuntimeError("OPERATOR_COMMAND_RETIREMENT_POLICY_MUST_HAVE_23_COMMANDS")


def retired_operator_command(command_id: str) -> RetiredOperatorCommandV1 | None:
    normalized = str(command_id or "").strip()
    return next(
        (
            policy
            for policy in RETIRED_OPERATOR_COMMANDS_V1
            if policy.command_id == normalized
        ),
        None,
    )


__all__ = [
    "RETIRED_OPERATOR_COMMANDS_V1",
    "RETIRED_OPERATOR_COMMAND_IDS",
    "RetiredOperatorCommandV1",
    "retired_operator_command",
]
