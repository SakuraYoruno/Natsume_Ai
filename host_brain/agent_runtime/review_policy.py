"""Review policy helpers for Agent runtime v2."""

from __future__ import annotations

from host_brain.agent_runtime.work_order import AgentWorkOrder


class ReviewPolicy:
    def review_required(self, work_order: AgentWorkOrder) -> bool:
        if "review_required" in work_order.review_policy:
            return bool(work_order.review_policy.get("review_required"))
        return bool(work_order.requires_write)


__all__ = ["ReviewPolicy"]
