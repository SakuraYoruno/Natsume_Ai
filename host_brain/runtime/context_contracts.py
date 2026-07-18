"""Typed, workspace-scoped World/Scene/Intent context contracts for NE-03."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ContextContractError(ValueError):
    """Context cannot be safely bound to a workspace and graph revision."""


def _text(value: Any, field: str, limit: int = 240) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ContextContractError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any, field: str) -> str:
    result = _text(value, field, 64).lower()
    if not _DIGEST_RE.fullmatch(result):
        raise ContextContractError(f"{field} must be a SHA-256 digest")
    return result


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ContextContractError("context value must be JSON-safe") from exc


@dataclass(frozen=True, slots=True)
class WorldFactV2:
    workspace_id: str
    subject_id: str
    key: str
    value: Any
    source_ref: str
    confidence: float
    observed_at_ms: int
    expires_at_ms: int
    conflict_group: str = ""
    revision: int = 1

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id", "key", "source_ref"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        value = _json_copy(self.value)
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ContextContractError("confidence must be within [0, 1]")
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in (self.observed_at_ms, self.expires_at_ms, self.revision)):
            raise ContextContractError("timestamps and revision must be non-negative integers")
        if self.expires_at_ms <= self.observed_at_ms:
            raise ContextContractError("expires_at_ms must exceed observed_at_ms")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "confidence", round(confidence, 6))
        object.__setattr__(self, "conflict_group", str(self.conflict_group or "").strip())

    @property
    def fact_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "key": self.key,
            "value": _json_copy(self.value),
            "source_ref": self.source_ref,
            "confidence": self.confidence,
            "observed_at_ms": self.observed_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "conflict_group": self.conflict_group,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class WorldSnapshotV2:
    workspace_id: str
    subject_id: str
    revision: int
    facts: tuple[WorldFactV2, ...]
    snapshot_digest: str = ""

    def __post_init__(self) -> None:
        workspace = _text(self.workspace_id, "workspace_id")
        subject = _text(self.subject_id, "subject_id")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ContextContractError("snapshot revision must be positive")
        facts = tuple(self.facts)
        if not all(isinstance(item, WorldFactV2) for item in facts):
            raise ContextContractError("facts must contain WorldFactV2 values")
        if any(item.workspace_id != workspace or item.subject_id != subject for item in facts):
            raise ContextContractError("WorldFact scope differs from WorldSnapshot scope")
        ordered = tuple(sorted(facts, key=lambda item: (item.key, item.source_ref, item.fact_digest)))
        computed = _json_digest({"workspace_id": workspace, "subject_id": subject, "revision": self.revision, "facts": [item.to_dict() for item in ordered]})
        supplied = str(self.snapshot_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("snapshot_digest does not bind facts")
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "subject_id", subject)
        object.__setattr__(self, "facts", ordered)
        object.__setattr__(self, "snapshot_digest", computed)

    def competing_facts(self, key: str) -> tuple[WorldFactV2, ...]:
        """Return all sources for a key; confidence never hides conflicts."""

        return tuple(item for item in self.facts if item.key == key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.world.snapshot.v2",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "revision": self.revision,
            "facts": [item.to_dict() for item in self.facts],
            "snapshot_digest": self.snapshot_digest,
        }


@dataclass(frozen=True, slots=True)
class ContextPackV2:
    workspace_id: str
    subject_id: str
    task_graph_id: str
    graph_revision: int
    world_snapshot_digest: str
    facts: tuple[WorldFactV2, ...]

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id", "task_graph_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.graph_revision, bool) or not isinstance(self.graph_revision, int) or self.graph_revision < 1:
            raise ContextContractError("graph_revision must be positive")
        object.__setattr__(self, "world_snapshot_digest", _digest(self.world_snapshot_digest, "world_snapshot_digest"))
        facts = tuple(self.facts)
        if any(item.workspace_id != self.workspace_id or item.subject_id != self.subject_id for item in facts):
            raise ContextContractError("ContextPack fact scope mismatch")
        object.__setattr__(self, "facts", tuple(sorted(facts, key=lambda item: (item.key, item.source_ref))))

    @property
    def pack_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.context-pack.v2",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "task_graph_id": self.task_graph_id,
            "graph_revision": self.graph_revision,
            "world_snapshot_digest": self.world_snapshot_digest,
            "facts": [item.to_dict() for item in self.facts],
        }


@dataclass(frozen=True, slots=True)
class SceneDecisionV2:
    workspace_id: str
    subject_id: str
    scene_id: str
    revision: int
    world_snapshot_digest: str
    constraints: Mapping[str, Any]
    decision_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id", "scene_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ContextContractError("scene revision must be positive")
        object.__setattr__(self, "world_snapshot_digest", _digest(self.world_snapshot_digest, "world_snapshot_digest"))
        if not isinstance(self.constraints, Mapping):
            raise ContextContractError("scene constraints must be a mapping")
        constraints = _json_copy(dict(self.constraints))
        computed = _json_digest({"workspace_id": self.workspace_id, "subject_id": self.subject_id, "scene_id": self.scene_id, "revision": self.revision, "world_snapshot_digest": self.world_snapshot_digest, "constraints": constraints})
        supplied = str(self.decision_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("scene decision digest drift")
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "decision_digest", computed)


@dataclass(frozen=True, slots=True)
class IntentArbitrationV1:
    user_goal_digest: str
    scene_revision: int
    constraints: tuple[str, ...]
    decision: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_goal_digest", _digest(self.user_goal_digest, "user_goal_digest"))
        if isinstance(self.scene_revision, bool) or not isinstance(self.scene_revision, int) or self.scene_revision < 1:
            raise ContextContractError("intent scene_revision must be positive")
        constraints = tuple(sorted({_text(item, "constraints[]", 500) for item in self.constraints}))
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "decision", _text(self.decision, "decision", 120))


__all__ = [
    "ContextContractError",
    "ContextPackV2",
    "IntentArbitrationV1",
    "SceneDecisionV2",
    "WorldFactV2",
    "WorldSnapshotV2",
]
