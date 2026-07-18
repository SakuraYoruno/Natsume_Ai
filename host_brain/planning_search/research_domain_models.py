"""Immutable public evidence for the D20 Research planning domain.

The types in this module deliberately describe *planning-visible* source
metadata only.  They expose no dedicated local-locator, source-bytes,
answer-key, claim-label, verifier-decision, model-client, or executor field.
A later shadow experiment resolves a selected ``source_id`` through a
separately frozen readonly corpus and verifies its output independently.

This is not a production Research route and it is not evidence of a live
model's research quality.  It is the pure PlanningSearch half of the D20
experiment boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from host_brain.planning_search.contracts import VirtualStateSnapshot


RESEARCH_DOMAIN = "research"
RESEARCH_PLANNING_SCHEMA = "natsume.research-planning.shadow.v1"
RESEARCH_SHADOW_MODE = "READONLY_RESEARCH_SHADOW_ONLY"

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_GOVERNED_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{0,63}://[a-z0-9][a-z0-9._/-]{0,1018}$"
)
_WINDOWS_HOST_PATH_RE = re.compile(r"(?:^|[^a-z0-9])[a-z]:[\\/]", re.IGNORECASE)
_AUTHORITY_TIERS = frozenset({"primary", "independent", "secondary"})


class ResearchPlanningError(ValueError):
    """Research planning input cannot be represented safely."""


def _required_text(value: Any, *, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ResearchPlanningError(f"{field_name} must be non-empty and at most {limit} characters")
    return text


def _public_text(value: Any, *, field_name: str, limit: int) -> str:
    """Accept public planning text only when it cannot encode a host locator.

    Research snapshot data is copied into the virtual state and can later be
    emitted in a planning receipt.  Do not rely on a field name alone to keep
    a caller from smuggling a local pathname through an objective, tag, or
    free-form constraint.  Governed refs are validated separately because
    their URI syntax is intentionally opaque to the planner.
    """

    text = _required_text(value, field_name=field_name, limit=limit)
    if (
        "\\" in text
        or "/" in text
        or text.lower().startswith("file:")
        or _WINDOWS_HOST_PATH_RE.search(text)
    ):
        raise ResearchPlanningError(
            f"{field_name} must not contain a local host path or file locator"
        )
    return text


def _identifier(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=120).lower()
    if not _ID_RE.fullmatch(text):
        raise ResearchPlanningError(f"{field_name} must be a lower-case stable identifier")
    return text


def _digest(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=64).lower()
    if not _DIGEST_RE.fullmatch(text):
        raise ResearchPlanningError(f"{field_name} must be a lower-case SHA-256 digest")
    return text


def _governed_ref(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=1_024)
    scheme = text.split("://", 1)[0].lower()
    if scheme == "file" or "\\" in text or not _GOVERNED_REF_RE.fullmatch(text):
        raise ResearchPlanningError(
            f"{field_name} must be a governed opaque reference, not a host path"
        )
    return text


def _unit_interval(value: Any, *, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ResearchPlanningError(f"{field_name} must be finite within [0, 1]") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ResearchPlanningError(f"{field_name} must be finite within [0, 1]")
    return result


def _json_copy(value: Any, *, field_name: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResearchPlanningError(f"{field_name} must be JSON-safe") from exc
    return json.loads(encoded)


def _assert_public_json(value: Any, *, field_name: str) -> None:
    """Reject local locators recursively before JSON reaches virtual state."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            _public_text(key, field_name=f"{field_name} key", limit=120)
            _assert_public_json(item, field_name=f"{field_name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_public_json(item, field_name=f"{field_name}[{index}]")
    elif isinstance(value, str):
        _public_text(value, field_name=field_name, limit=4_000)


def _stable_texts(
    value: Sequence[Any], *, field_name: str, limit: int, item_limit: int
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ResearchPlanningError(f"{field_name} must be a text sequence")
    if len(value) > item_limit:
        raise ResearchPlanningError(f"{field_name} exceeds {item_limit} items")
    texts = tuple(
        sorted(
            {
                _public_text(item, field_name=f"{field_name}[]", limit=limit)
                for item in value
                if str(item or "").strip()
            }
        )
    )
    return texts


def _contains_prohibited_key(value: Any, *, prohibited: set[str]) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if any(token in str(key).lower() for token in prohibited):
                return True
            if _contains_prohibited_key(item, prohibited=prohibited):
                return True
    elif isinstance(value, list):
        return any(_contains_prohibited_key(item, prohibited=prohibited) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class ResearchSourceDescriptor:
    """One planning-visible descriptor of a frozen readonly source.

    ``source_ref`` is a governed provenance reference, never a host path.  A
    corpus adapter owns the private mapping from this descriptor to a safe,
    digest-checked local file or captured web response.
    """

    source_id: str
    source_ref: str
    content_digest: str
    authority_tier: str
    relevance_score: float
    estimated_read_cost: float
    topic_tags: Sequence[str] = ()
    conflict_topics: Sequence[str] = ()

    def __post_init__(self) -> None:
        source_id = _identifier(self.source_id, field_name="source_id")
        source_ref = _governed_ref(self.source_ref, field_name="source_ref")
        authority_tier = _required_text(
            self.authority_tier, field_name="authority_tier", limit=32
        ).lower()
        if authority_tier not in _AUTHORITY_TIERS:
            raise ResearchPlanningError("authority_tier must be primary, independent, or secondary")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "content_digest", _digest(self.content_digest, field_name="content_digest"))
        object.__setattr__(self, "authority_tier", authority_tier)
        object.__setattr__(self, "relevance_score", _unit_interval(self.relevance_score, field_name="relevance_score"))
        object.__setattr__(self, "estimated_read_cost", _unit_interval(self.estimated_read_cost, field_name="estimated_read_cost"))
        object.__setattr__(
            self,
            "topic_tags",
            _stable_texts(self.topic_tags, field_name="topic_tags", limit=120, item_limit=32),
        )
        object.__setattr__(
            self,
            "conflict_topics",
            _stable_texts(self.conflict_topics, field_name="conflict_topics", limit=120, item_limit=32),
        )

    def to_state_dict(self) -> dict[str, Any]:
        """Return only metadata allowed to influence the pure planner."""

        return {
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "content_digest": self.content_digest,
            "authority_tier": self.authority_tier,
            "relevance_score": _rounded(self.relevance_score),
            "estimated_read_cost": _rounded(self.estimated_read_cost),
            "topic_tags": list(self.topic_tags),
            "conflict_topics": list(self.conflict_topics),
        }


@dataclass(frozen=True, slots=True)
class ResearchTaskSnapshot:
    """Immutable public state for one readonly Research shadow request.

    The snapshot intentionally exposes no dedicated fields for expected
    answers, claim coverage, citation validity, verifier decisions, model
    outputs, source bytes, or local source locators.  Those concerns belong to
    later execution/verifier adapters; arbitrary objective text is not a DLP
    boundary.
    """

    task_ref: str
    corpus_ref: str
    objective: str
    sources: Sequence[ResearchSourceDescriptor]
    requires_conflict_resolution: bool = False
    virtual_transition_ready: bool = False
    max_sources_per_plan: int = 6
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_ref = _governed_ref(self.task_ref, field_name="task_ref")
        corpus_ref = _governed_ref(self.corpus_ref, field_name="corpus_ref")
        objective = _public_text(self.objective, field_name="objective", limit=4_000)
        sources = tuple(self.sources or ())
        if not sources or len(sources) > 32:
            raise ResearchPlanningError("sources must contain 1 through 32 descriptors")
        if not all(isinstance(item, ResearchSourceDescriptor) for item in sources):
            raise ResearchPlanningError("sources must contain ResearchSourceDescriptor values")
        ordered_sources = tuple(sorted(sources, key=lambda item: item.source_id))
        if len({item.source_id for item in ordered_sources}) != len(ordered_sources):
            raise ResearchPlanningError("source_id values must be unique")
        if not any(item.authority_tier == "primary" for item in ordered_sources):
            raise ResearchPlanningError("Research snapshot requires at least one primary source")
        if bool(self.requires_conflict_resolution) and not any(
            item.authority_tier == "independent" for item in ordered_sources
        ):
            raise ResearchPlanningError(
                "conflict-resolution Research snapshot requires an independent source"
            )
        max_sources = int(self.max_sources_per_plan)
        if max_sources < 1:
            raise ResearchPlanningError("max_sources_per_plan must be positive")
        max_sources = min(max_sources, len(ordered_sources))
        if not isinstance(self.constraints, Mapping):
            raise ResearchPlanningError("constraints must be a mapping")
        frozen_constraints = _json_copy(dict(self.constraints), field_name="constraints")
        _assert_public_json(frozen_constraints, field_name="constraints")
        prohibited = {"gold", "oracle", "answer", "verifier", "validation", "reward"}
        if _contains_prohibited_key(frozen_constraints, prohibited=prohibited):
            raise ResearchPlanningError("constraints must not contain verifier or answer-key fields")
        object.__setattr__(self, "task_ref", task_ref)
        object.__setattr__(self, "corpus_ref", corpus_ref)
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "sources", ordered_sources)
        object.__setattr__(self, "requires_conflict_resolution", bool(self.requires_conflict_resolution))
        object.__setattr__(self, "virtual_transition_ready", bool(self.virtual_transition_ready))
        object.__setattr__(self, "max_sources_per_plan", max_sources)
        object.__setattr__(self, "constraints", MappingProxyType(frozen_constraints))

    @property
    def snapshot_digest(self) -> str:
        encoded = json.dumps(
            self.to_public_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RESEARCH_PLANNING_SCHEMA,
            "task_ref": self.task_ref,
            "corpus_ref": self.corpus_ref,
            "objective": self.objective,
            "sources": [item.to_state_dict() for item in self.sources],
            "requires_conflict_resolution": self.requires_conflict_resolution,
            "virtual_transition_ready": self.virtual_transition_ready,
            "max_sources_per_plan": self.max_sources_per_plan,
            "constraints": _json_copy(dict(self.constraints), field_name="constraints"),
        }

    def to_virtual_state(self) -> VirtualStateSnapshot:
        return VirtualStateSnapshot(
            schema_version=RESEARCH_PLANNING_SCHEMA,
            domain=RESEARCH_DOMAIN,
            capabilities=(
                "research.query",
                "research.primary_source",
                "research.cross_check",
                "research.conflict_resolution",
                "research.stop",
            ),
            facts={
                "snapshot_digest": self.snapshot_digest,
                "phase": "root",
                "query_scoped": False,
                "read_source_ids": [],
                "primary_source_ids": [],
                "independent_source_ids": [],
                "cross_checked": False,
                "conflict_resolved": False,
                "sources": [item.to_state_dict() for item in self.sources],
            },
            constraints={
                "mode": RESEARCH_SHADOW_MODE,
                "max_sources_per_plan": self.max_sources_per_plan,
                "requires_conflict_resolution": self.requires_conflict_resolution,
                "virtual_transition_ready": self.virtual_transition_ready,
                **_json_copy(dict(self.constraints), field_name="constraints"),
            },
            source_refs=(self.task_ref, self.corpus_ref, *(item.source_ref for item in self.sources)),
        )


def _rounded(value: float) -> float:
    return round(float(value), 10)


__all__ = [
    "RESEARCH_DOMAIN",
    "RESEARCH_PLANNING_SCHEMA",
    "RESEARCH_SHADOW_MODE",
    "ResearchPlanningError",
    "ResearchSourceDescriptor",
    "ResearchTaskSnapshot",
]
