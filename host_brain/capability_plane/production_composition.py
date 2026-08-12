"""Production composition root for the single durable CapabilityBroker."""

from __future__ import annotations

from dataclasses import dataclass

from .broker import CapabilityBroker
from .journal import CapabilityExecutionJournal
from .policies import DescriptorCapabilityPolicy


# 此对象图是生产环境的单一能力执行入口，避免不同门面各自持有不一致的准入状态。
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

    # 没有持久日志时无法在重启后判定副作用是否已发生，因此生产组合必须拒绝启动。
    if execution_journal is None:
        raise ValueError("production CapabilityBroker requires a durable execution journal")
    policy = DescriptorCapabilityPolicy()
    broker = CapabilityBroker(
        policy=policy,
        execution_journal=execution_journal,
    )
    return CapabilityRuntimeComposition(policy=policy, broker=broker)


__all__ = ["CapabilityRuntimeComposition", "build_production_capability_runtime"]
