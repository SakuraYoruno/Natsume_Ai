"""Side-effect-free fixtures shared by M48-B negative tests.

The fixture is deliberately independent from production packages.  A test
must opt into one of the five registered domains and receives only an
ephemeral secret, a loopback-only channel and recording transports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import secrets
from typing import Any


M48B_ISOLATION_DOMAINS = frozenset(
    {"gateway", "obs", "pentest", "skill_provider", "bridge"}
)


@dataclass
class RecordingSink:
    """Record an attempted effect without performing network/process/device I/O."""

    effects: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, kind: str, payload: Any) -> dict[str, Any]:
        receipt = {
            "kind": str(kind),
            "payload": payload,
            "executed": False,
            "channel": "recording-only",
        }
        self.effects.append(receipt)
        return receipt


@dataclass(frozen=True)
class M48BIsolationHarness:
    domain: str
    principal: str
    test_secret: str
    channel: str
    state_root: Path
    network_policy: str
    process_policy: str
    physical_effects_allowed: bool
    sink: RecordingSink

    def assert_safe(self) -> None:
        if self.domain not in M48B_ISOLATION_DOMAINS:
            raise ValueError(f"unregistered M48-B isolation domain: {self.domain}")
        if not self.principal.startswith("test:m48b:"):
            raise ValueError("M48-B principal must be explicitly test-only")
        if not self.channel.startswith("test+loopback://m48b/"):
            raise ValueError("M48-B channel must be test-only and loopback scoped")
        if self.network_policy != "loopback_only":
            raise ValueError("M48-B negative tests may only use loopback networking")
        if self.process_policy != "deny":
            raise ValueError("M48-B negative tests may not spawn real processes")
        if self.physical_effects_allowed:
            raise ValueError("M48-B negative tests may not perform physical effects")
        if len(self.test_secret) < 32:
            raise ValueError("M48-B test secret must be ephemeral and non-trivial")


def build_m48b_isolation_harness(
    *,
    domain: str,
    state_root: Path,
) -> M48BIsolationHarness:
    """Create one isolated, no-side-effect domain harness."""

    normalized = str(domain).strip().lower()
    harness = M48BIsolationHarness(
        domain=normalized,
        principal=f"test:m48b:{normalized}",
        test_secret=secrets.token_urlsafe(32),
        channel=f"test+loopback://m48b/{normalized}",
        state_root=Path(state_root).resolve(),
        network_policy="loopback_only",
        process_policy="deny",
        physical_effects_allowed=False,
        sink=RecordingSink(),
    )
    harness.state_root.mkdir(parents=True, exist_ok=True)
    harness.assert_safe()
    return harness
