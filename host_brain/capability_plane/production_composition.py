"""Production composition root for the single durable CapabilityBroker."""

from __future__ import annotations

from dataclasses import dataclass

from .broker import CapabilityBroker
from .journal import CapabilityExecutionJournal
from .policies import DescriptorCapabilityPolicy


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeComposition:
    """The complete process capability execution object graph."""

    policy: DescriptorCapabilityPolicy
    broker: CapabilityBroker


def build_production_capability_runtime(
    *,
    execution_journal: CapabilityExecutionJournal,
) -> CapabilityRuntimeComposition:
    """Construct the only production broker with its durable journal."""

    if execution_journal is None:
        raise ValueError("production CapabilityBroker requires a durable execution journal")
    policy = DescriptorCapabilityPolicy()
    broker = CapabilityBroker(
        policy=policy,
        execution_journal=execution_journal,
    )
    return CapabilityRuntimeComposition(policy=policy, broker=broker)


__all__ = ["CapabilityRuntimeComposition", "build_production_capability_runtime"]
