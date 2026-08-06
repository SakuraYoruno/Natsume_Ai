"""Typed app-local proof for one claimed Acceptance Effect invocation.

Ordinary accepted chat delivery does not need a second policy decision after
the Task node has already passed Verification and Acceptance.  It still needs
the exact durable effect identity, payload and claim fence.  This carrier is
created only by the Acceptance Effect dispatcher and is never serialized as
replayable authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AcceptanceEffectExecutionProof:
    effect_id: str
    effect_type: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    lease_owner_id: str
    fencing_token: int
    payload_sha256: str
    action_args: Mapping[str, Any]

    def __post_init__(self) -> None:
        text_fields = (
            "effect_id",
            "effect_type",
            "graph_id",
            "node_id",
            "attempt_id",
            "lease_owner_id",
        )
        for field in text_fields:
            value = str(getattr(self, field) or "").strip()
            if not value:
                raise ValueError(f"AcceptanceEffectExecutionProof requires {field}")
            object.__setattr__(self, field, value)
        if isinstance(self.graph_revision, bool) or int(self.graph_revision) < 1:
            raise ValueError("AcceptanceEffectExecutionProof graph_revision is invalid")
        if isinstance(self.fencing_token, bool) or int(self.fencing_token) < 1:
            raise ValueError("AcceptanceEffectExecutionProof fencing_token is invalid")
        object.__setattr__(self, "graph_revision", int(self.graph_revision))
        object.__setattr__(self, "fencing_token", int(self.fencing_token))
        payload_sha256 = str(self.payload_sha256 or "").strip().lower()
        if _DIGEST.fullmatch(payload_sha256) is None:
            raise ValueError("AcceptanceEffectExecutionProof payload_sha256 is invalid")
        object.__setattr__(self, "payload_sha256", payload_sha256)
        if not isinstance(self.action_args, Mapping):
            raise TypeError("AcceptanceEffectExecutionProof action_args must be a mapping")
        object.__setattr__(self, "action_args", deepcopy(dict(self.action_args)))

    def action_args_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self.action_args))


__all__ = ["AcceptanceEffectExecutionProof"]
