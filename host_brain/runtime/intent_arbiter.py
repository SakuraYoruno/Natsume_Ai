"""Pure intent arbitration that can only tighten constraints."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .context_contracts import IntentArbitrationV1, SceneDecisionV2


class IntentArbiter:
    """Combine intent constraints without replacing the user's goal."""

    def arbitrate(
        self,
        *,
        user_goal: str,
        scene: SceneDecisionV2,
        relationship_constraints: Iterable[str] = (),
        risk_constraints: Iterable[str] = (),
        grant_constraints: Iterable[str] = (),
    ) -> IntentArbitrationV1:
        goal = str(user_goal or "").strip()
        if not goal:
            raise ValueError("user_goal is required")
        constraints = {str(item or "").strip() for item in scene.constraints.get("constraints", ())}
        for values in (relationship_constraints, risk_constraints, grant_constraints):
            constraints.update(str(item or "").strip() for item in values)
        constraints.discard("")
        blocked = any(item.upper().startswith(("BLOCK", "DENY", "FORBID")) for item in constraints)
        decision = "BLOCK" if blocked else ("REVIEW" if constraints else "ALLOW")
        return IntentArbitrationV1(
            user_goal_digest=hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            scene_revision=scene.revision,
            constraints=tuple(sorted(constraints)),
            decision=decision,
        )


__all__ = ["IntentArbiter"]
