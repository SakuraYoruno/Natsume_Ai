"""Review policy helpers for Codex runtime v2."""

from __future__ import annotations

from host_brain.codex_runtime.work_order import CodexWorkOrder


class ReviewPolicy:
    def review_required(self, work_order: CodexWorkOrder) -> bool:
        if "review_required" in work_order.review_policy:
            return bool(work_order.review_policy.get("review_required"))
        return bool(work_order.requires_write)


__all__ = ["ReviewPolicy"]
