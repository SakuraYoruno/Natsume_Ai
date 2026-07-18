"""Stable errors and reason codes for node-bound grant authority."""

from __future__ import annotations


NODE_GRANT_INPUT_INVALID = "NODE_GRANT_INPUT_INVALID"
NODE_GRANT_PARENT_INVALID = "NODE_GRANT_PARENT_INVALID"
NODE_GRANT_BINDING_MISSING = "NODE_GRANT_BINDING_MISSING"
NODE_GRANT_ROUTE_DRIFT = "NODE_GRANT_ROUTE_DRIFT"
NODE_GRANT_ROUTE_UNSUPPORTED = "NODE_GRANT_ROUTE_UNSUPPORTED"
NODE_GRANT_REPLAY_DRIFT = "NODE_GRANT_REPLAY_DRIFT"
NODE_GRANT_EXPIRED = "NODE_GRANT_EXPIRED"
NODE_GRANT_VERIFICATION_FAILED = "NODE_GRANT_VERIFICATION_FAILED"


class NodeGrantDerivationError(PermissionError):
    """Fail-closed node grant derivation error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or NODE_GRANT_INPUT_INVALID)
        self.detail = str(detail or "node grant derivation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class NodeGrantVerificationError(PermissionError):
    """A durable child decision failed exact authority verification."""

    def __init__(
        self,
        detail: str,
        *,
        reason_code: str = NODE_GRANT_VERIFICATION_FAILED,
    ) -> None:
        self.reason_code = str(reason_code or NODE_GRANT_VERIFICATION_FAILED)
        self.detail = str(detail or "node grant verification failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


__all__ = [name for name in globals() if name.startswith("NODE_GRANT_")] + [
    "NodeGrantDerivationError",
    "NodeGrantVerificationError",
]
