"""Default-off adapter boundary for later Host/Obs/Gateway/Bridge cutovers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping

from .models import SecureIngressDecision, SecureIngressRequest
from .service import SecureIngressService


SECURE_INGRESS_MODE_ENV = "HOST_SECURE_INGRESS_MODE"


class SecureIngressMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    ENFORCE = "ENFORCE"


def load_secure_ingress_mode(
    env: Mapping[str, str] | None = None,
) -> SecureIngressMode:
    source = os.environ if env is None else env
    raw = str(source.get(SECURE_INGRESS_MODE_ENV, "OFF")).strip().upper()
    try:
        return SecureIngressMode(raw)
    except ValueError:
        return SecureIngressMode.OFF


@dataclass(frozen=True, slots=True)
class SecureIngressAdapterOutcome:
    mode: SecureIngressMode
    evaluated: bool
    permit_execution: bool
    decision: SecureIngressDecision | None


class SecureIngressAdapter:
    """Expose one explicit enforcement decision without a legacy allow path."""

    def __init__(
        self,
        *,
        service: SecureIngressService,
        mode: SecureIngressMode = SecureIngressMode.OFF,
    ) -> None:
        self._service = service
        self._mode = mode

    @property
    def mode(self) -> SecureIngressMode:
        return self._mode

    def evaluate(self, request: SecureIngressRequest) -> SecureIngressAdapterOutcome:
        if self._mode is SecureIngressMode.OFF:
            return SecureIngressAdapterOutcome(
                mode=self._mode,
                evaluated=False,
                permit_execution=False,
                decision=None,
            )
        decision = self._service.decide(request)
        return SecureIngressAdapterOutcome(
            mode=self._mode,
            evaluated=True,
            permit_execution=bool(
                self._mode is SecureIngressMode.ENFORCE and decision.allowed
            ),
            decision=decision,
        )


__all__ = [
    "SECURE_INGRESS_MODE_ENV",
    "SecureIngressAdapter",
    "SecureIngressAdapterOutcome",
    "SecureIngressMode",
    "load_secure_ingress_mode",
]
