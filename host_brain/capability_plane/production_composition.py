"""单一耐久 CapabilityBroker 的生产组合根。

所有能力门面应共享此对象图，从而使用同一份跨进程幂等日志和描述符授权规则；禁止为某种
能力私自创建无日志 Broker，否则重启后无法判断副作用是否已发生。
"""

from __future__ import annotations

from dataclasses import dataclass

from .broker import CapabilityBroker
from .journal import CapabilityExecutionJournal
from .policies import DescriptorCapabilityPolicy


# 此对象图是生产环境的单一能力执行入口，避免不同门面各自持有不一致的准入状态。
@dataclass(frozen=True, slots=True)
class CapabilityRuntimeComposition:
    """进程级能力执行对象图；只暴露策略与共享 Broker，不携带独立执行捷径。"""

    policy: DescriptorCapabilityPolicy
    broker: CapabilityBroker


def build_production_capability_runtime(
    *,
    execution_journal: CapabilityExecutionJournal,
) -> CapabilityRuntimeComposition:
    """用耐久日志构造唯一生产 Broker；缺失日志直接拒绝组合。"""

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
