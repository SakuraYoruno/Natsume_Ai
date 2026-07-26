"""Command dispatch across cancellation-bound and general Effect recovery."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_effect_reconciliation import (
    ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
)


class EffectReconciliationCommandService:
    """Dispatch one command ID to cancellation-bound or general recovery."""

    __slots__ = ("_cancellation", "_general")

    def __init__(
        self,
        *,
        cancellation_service: Any,
        general_service: Any,
    ) -> None:
        from host_brain.task_runtime.acceptance_effect_reconciliation import (
            AcceptanceEffectReconciliationService,
        )

        if not callable(
            getattr(
                cancellation_service,
                "reconcile_graph_cancellation_effect",
                None,
            )
        ):
            raise TypeError("cancellation Effect reconciliation is required")
        if not isinstance(
            general_service,
            AcceptanceEffectReconciliationService,
        ):
            raise TypeError("general Effect reconciliation is required")
        self._cancellation = cancellation_service
        self._general = general_service

    def reconcile_graph_cancellation_effect(
        self,
        **request: Any,
    ) -> Mapping[str, Any]:
        if (
            str(request.get("purpose") or "").strip().upper()
            == ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
        ):
            request.pop("intent_ref", None)
            return self._general.reconcile_acceptance_effect(**request)
        if not str(request.get("intent_ref") or "").strip():
            self._raise_invalid(
                "cancellation-bound Effect reconciliation requires intent_ref"
            )
        return self._cancellation.reconcile_graph_cancellation_effect(
            **request
        )

    def reconcile_acceptance_effect(
        self,
        **request: Any,
    ) -> Mapping[str, Any]:
        return self._general.reconcile_acceptance_effect(**request)

    def build_operator_challenge(
        self,
        **request: Any,
    ) -> Mapping[str, Any]:
        return self._general.build_operator_challenge(**request)

    @staticmethod
    def _raise_invalid(detail: str) -> None:
        from host_brain.task_runtime.acceptance_effect_reconciliation import (
            ACCEPTANCE_EFFECT_RECONCILIATION_INVALID,
            AcceptanceEffectReconciliationError,
        )

        raise AcceptanceEffectReconciliationError(
            ACCEPTANCE_EFFECT_RECONCILIATION_INVALID,
            detail,
            side_effects_may_have_run=False,
        )


__all__ = ["EffectReconciliationCommandService"]
