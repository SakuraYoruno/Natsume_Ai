"""Secure Ingress boundary for internal-agent HTTP commands.

Route modules consume only :class:`SecureIngressAuthority`.  Identity envelopes
and runtime proofs stay inside the shared Secure Ingress adapter and are never
forwarded as command authority.
"""

from __future__ import annotations

from typing import Any

from host_brain.secure_ingress import (
    IngressSurface,
    SecureIngressAuthority,
    SecureIngressAuthorityEvaluator,
    SecureIngressHttpAuthorityAdapter,
)


class InternalAgentIngress:
    """Bind internal-agent HTTP requests to its server-owned policy."""

    def __init__(
        self,
        *,
        evaluator: SecureIngressAuthorityEvaluator,
    ) -> None:
        self._http = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.INTERNAL_AGENT,
        )

    def evaluate_http(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int,
    ) -> SecureIngressAuthority:
        """Return redacted authority or a fail-closed denial."""

        return self._http.evaluate(
            request=request,
            raw_body=raw_body,
            received_at_ms=received_at_ms,
        )


__all__ = ["InternalAgentIngress"]
