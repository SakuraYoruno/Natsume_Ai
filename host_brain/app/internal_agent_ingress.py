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
        self._internal_http = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.INTERNAL_AGENT,
        )
        self._review_http = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.REVIEW_OPERATOR,
        )

    def evaluate_http(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int,
    ) -> SecureIngressAuthority:
        """Return redacted authority or a fail-closed denial."""

        path = str(getattr(getattr(request, "url", None), "path", "") or "")
        adapter = (
            self._review_http
            if path.startswith("/internal/codex/reviews")
            else self._internal_http
        )
        return adapter.evaluate(
            request=request,
            raw_body=raw_body,
            received_at_ms=received_at_ms,
        )


__all__ = ["InternalAgentIngress"]
