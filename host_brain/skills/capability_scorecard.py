"""Federated capability maturity read models and conservative risk adapters.

The existing registries remain authoritative for registration and execution.
This module only projects their heterogeneous metadata into a versioned,
read-only scorecard.  Maturity is never execution authority: failed evidence
may lower a level automatically, while a higher level can only be represented
as a review candidate.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
import hashlib
import json
from typing import Any


CAPABILITY_RISK_SCHEMA_VERSION = "Natsume.capability.risk.v1"
CAPABILITY_EVIDENCE_SCHEMA_VERSION = "Natsume.capability.evidence.v1"
CAPABILITY_SCORECARD_SCHEMA_VERSION = "Natsume.capability.scorecard.v1"
CAPABILITY_UPGRADE_CANDIDATE_SCHEMA_VERSION = "Natsume.capability.upgrade_candidate.v1"

CAPABILITY_POLICY_RESTRICT_ONLY = "RESTRICT_ONLY"
CAPABILITY_REVIEW_REQUIRED = "REVIEW_REQUIRED"
CAPABILITY_STABLE = "STABLE"
CAPABILITY_DEGRADED = "DEGRADED"

EVIDENCE_PASS = "PASS"
EVIDENCE_FAIL = "FAIL"
EVIDENCE_ERROR = "ERROR"
EVIDENCE_OUTCOMES = frozenset({EVIDENCE_PASS, EVIDENCE_FAIL, EVIDENCE_ERROR})

SOURCE_SKILL_META = "SKILL_META"
SOURCE_TOOL_META = "TOOL_META"
SOURCE_PENTEST_TOOL = "PENTEST_TOOL_DESCRIPTOR"
SOURCE_AGENT_MODE = "AGENT_MODE"


class CapabilityMaturityLevel(IntEnum):
    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5


MaturityLevel = CapabilityMaturityLevel
MATURITY_LEVEL_NAMES = {
    0: "UNAVAILABLE",
    1: "LAB_ONLY",
    2: "HUMAN_SUPERVISED",
    3: "LOW_RISK_AUTOMATION",
    4: "COMPLEX_TASK_READY",
    5: "LONG_TERM_STABLE",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip()


def _field(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _risk(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("canonical risk must be int in [1,5]")
    try:
        risk = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical risk must be int in [1,5]") from exc
    if risk < 1 or risk > 5:
        raise ValueError("canonical risk must be int in [1,5]")
    return risk


def _level(value: Any) -> int:
    if isinstance(value, str) and value.strip().upper().startswith("L"):
        value = value.strip()[1:]
    if isinstance(value, bool):
        raise ValueError("maturity level must be L0-L5")
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("maturity level must be L0-L5") from exc
    if level < 0 or level > 5:
        raise ValueError("maturity level must be L0-L5")
    return level


def _durable_ref(value: Any, field_name: str) -> str:
    ref = _text(value)
    if not ref or "://" not in ref:
        raise ValueError(f"{field_name} must be a durable reference")
    return ref


def _ref_part(value: Any, *, fallback: str = "unknown") -> str:
    normalized = "".join(
        char.lower() if char.isalnum() or char in {"-", "_", "."} else "-"
        for char in _text(value)
    ).strip("-._")
    return normalized or fallback


class _ReadModel(Mapping[str, Any]):
    """Small compatibility layer for API/read-model consumers."""

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - abstract contract
        raise NotImplementedError

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass(frozen=True, slots=True)
class CanonicalRisk(_ReadModel):
    """A stable 1-5 risk projection with its conversion basis."""

    source_kind: str
    source_value: str
    canonical_risk: int
    conversion_basis: tuple[str, ...]
    static_maturity_ceiling: int
    schema_version: str = CAPABILITY_RISK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", _text(self.source_kind).upper())
        object.__setattr__(self, "source_value", _text(self.source_value))
        object.__setattr__(self, "canonical_risk", _risk(self.canonical_risk))
        object.__setattr__(self, "static_maturity_ceiling", _level(self.static_maturity_ceiling))
        object.__setattr__(
            self,
            "conversion_basis",
            tuple(_text(item) for item in self.conversion_basis if _text(item)),
        )
        if not self.source_kind or not self.source_value or not self.conversion_basis:
            raise ValueError("canonical risk requires source kind/value and conversion basis")
        if self.schema_version != CAPABILITY_RISK_SCHEMA_VERSION:
            raise ValueError("unsupported canonical risk schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind,
            "source_value": self.source_value,
            "canonical_risk": self.canonical_risk,
            "conversion_basis": list(self.conversion_basis),
            "static_maturity_ceiling": self.static_maturity_ceiling,
            "read_only": True,
        }


def static_maturity_ceiling_for_risk(canonical_risk: int) -> int:
    """Return the non-relaxable maturity ceiling for a static risk class."""

    # High-risk capabilities may demonstrate reliability, but cannot use that
    # evidence to claim unrestricted autonomy.
    return {1: 5, 2: 5, 3: 4, 4: 2, 5: 1}[_risk(canonical_risk)]


def canonical_risk_from_skill_meta(meta: Any) -> CanonicalRisk:
    risk = _risk(_field(meta, "risk_level"))
    reversible = bool(_field(meta, "reversible", True))
    require_confirm = bool(_field(meta, "require_confirm", False))
    effective = max(risk, 4 if require_confirm else 1, 4 if not reversible and risk >= 3 else 1)
    basis = [f"SkillMeta.risk_level={risk} uses canonical 1-5 directly"]
    if require_confirm:
        basis.append("require_confirm preserves canonical risk >=4")
    if not reversible and risk >= 3:
        basis.append("irreversible medium/high action preserves canonical risk >=4")
    return CanonicalRisk(
        source_kind=SOURCE_SKILL_META,
        source_value=str(risk),
        canonical_risk=effective,
        conversion_basis=tuple(basis),
        static_maturity_ceiling=static_maturity_ceiling_for_risk(effective),
    )


def canonical_risk_from_tool_meta(meta: Any) -> CanonicalRisk:
    """Adapt MCP ToolMeta or the canonical ToolCatalog metadata contract."""

    raw_risk = _text(_field(meta, "risk_level")).upper()
    reversible = bool(_field(meta, "reversible", True))
    access = _text(_field(meta, "access_level")).upper()
    side_effect = _text(_field(meta, "side_effect_level")).upper()
    auth = _text(_field(meta, "auth_level")).upper()
    basis: list[str] = []

    if raw_risk:
        mapping = {"LOW": 1, "MEDIUM": 3, "HIGH": 4, "CRITICAL": 5}
        if raw_risk not in mapping:
            raise ValueError(f"unsupported ToolMeta risk_level: {raw_risk}")
        risk = mapping[raw_risk]
        basis.append(f"ToolMeta.risk_level={raw_risk} maps to canonical {risk}")
    elif side_effect or auth:
        if side_effect not in {"READ", "WRITE"}:
            raise ValueError(f"unsupported ToolMeta side_effect_level: {side_effect}")
        if auth not in {"NONE", "APPROVAL", "CONFIRM", "LOW", "MEDIUM", "HIGH"}:
            raise ValueError(f"unsupported ToolMeta auth_level: {auth}")
        auth = {"LOW": "NONE", "MEDIUM": "APPROVAL", "HIGH": "CONFIRM"}.get(auth, auth)
        risk = {
            ("READ", "NONE"): 1,
            ("READ", "APPROVAL"): 2,
            ("READ", "CONFIRM"): 3,
            ("WRITE", "NONE"): 3,
            ("WRITE", "APPROVAL"): 4,
            ("WRITE", "CONFIRM"): 5,
        }[(side_effect, auth)]
        basis.append(f"ToolMeta {side_effect}/{auth} maps to canonical {risk}")
    else:
        raise ValueError("ToolMeta requires risk_level or side_effect_level/auth_level")

    if access == "RESTRICTED":
        risk = max(risk, 4)
        basis.append("RESTRICTED access preserves canonical risk >=4")
    if not reversible and risk >= 3:
        risk = min(5, max(4, risk + 1))
        basis.append("irreversible side effect raises risk without exceeding 5")
    return CanonicalRisk(
        source_kind=SOURCE_TOOL_META,
        source_value=raw_risk or f"{side_effect}/{auth}",
        canonical_risk=risk,
        conversion_basis=tuple(basis),
        static_maturity_ceiling=static_maturity_ceiling_for_risk(risk),
    )


def canonical_risk_from_pentest_descriptor(descriptor: Any) -> CanonicalRisk:
    raw = _field(descriptor, "risk_level")
    if isinstance(raw, bool):
        raise ValueError("Pentest ToolDescriptor risk_level must be int in [0,3]")
    try:
        source_risk = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Pentest ToolDescriptor risk_level must be int in [0,3]") from exc
    if source_risk < 0 or source_risk > 3:
        raise ValueError("Pentest ToolDescriptor risk_level must be int in [0,3]")
    risk = {0: 1, 1: 3, 2: 4, 3: 5}[source_risk]
    impact = _text(_field(descriptor, "impact_level", "readonly")).lower()
    impact_floor = {
        "readonly": 1,
        "verification": 3,
        "controlled_exploit": 4,
        "session_enum": 5,
    }.get(impact, 5)
    risk = max(risk, impact_floor)
    basis = [
        f"Pentest ToolDescriptor.risk_level={source_risk} maps 0/1/2/3 to 1/3/4/5",
        f"impact_level={impact or 'unknown'} preserves canonical risk >={impact_floor}",
    ]
    if bool(_field(descriptor, "osec_cleanup", False)):
        risk = 5
        basis.append("required OPSEC cleanup preserves canonical risk 5")
    return CanonicalRisk(
        source_kind=SOURCE_PENTEST_TOOL,
        source_value=f"{source_risk}/{impact}",
        canonical_risk=risk,
        conversion_basis=tuple(basis),
        static_maturity_ceiling=static_maturity_ceiling_for_risk(risk),
    )


def canonical_risk_from_agent_mode(mode: Any) -> CanonicalRisk:
    if not isinstance(mode, (str, Enum)):
        mode = _field(mode, "risk_mode", _field(mode, "mode", mode))
    normalized = _text(mode).lower()
    mapping = {
        # Multi-agent risk modes.
        "safe": 1,
        "guarded": 3,
        "strict": 4,
        # Security immune AgentMode.  FORENSIC remains read-oriented, while
        # CONTAIN/WARTIME can change host state and therefore remain high risk.
        "observe": 1,
        "hunt": 3,
        "forensic": 3,
        "contain": 4,
        "wartime": 5,
    }
    if normalized not in mapping:
        raise ValueError(f"unsupported Agent mode: {normalized or '<empty>'}")
    risk = mapping[normalized]
    return CanonicalRisk(
        source_kind=SOURCE_AGENT_MODE,
        source_value=normalized,
        canonical_risk=risk,
        conversion_basis=(f"Agent mode={normalized} maps to canonical {risk}",),
        static_maturity_ceiling=static_maturity_ceiling_for_risk(risk),
    )


# Concise aliases used by registry-facing callers.
adapt_skill_meta_risk = canonical_risk_from_skill_meta
adapt_tool_meta_risk = canonical_risk_from_tool_meta
adapt_pentest_tool_risk = canonical_risk_from_pentest_descriptor
adapt_agent_mode_risk = canonical_risk_from_agent_mode


def adapt_capability_risk(source: Any, *, source_kind: str) -> CanonicalRisk:
    """Explicit dispatcher; source types are intentionally not guessed."""

    kind = _text(source_kind).upper()
    adapters = {
        SOURCE_SKILL_META: canonical_risk_from_skill_meta,
        SOURCE_TOOL_META: canonical_risk_from_tool_meta,
        SOURCE_PENTEST_TOOL: canonical_risk_from_pentest_descriptor,
        SOURCE_AGENT_MODE: canonical_risk_from_agent_mode,
    }
    if kind not in adapters:
        raise ValueError(f"unsupported capability source_kind: {kind or '<empty>'}")
    return adapters[kind](source)


@dataclass(frozen=True, slots=True)
class CapabilityEvaluationEvidence(_ReadModel):
    capability_id: str
    suite_id: str
    outcome: str
    observed_level: int
    evidence_ref: str
    observed_at_ms: int
    source_refs: tuple[str, ...] = field(default_factory=tuple)
    evaluator: str = "deterministic-evaluator"
    expires_at_ms: int | None = None
    schema_version: str = CAPABILITY_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _text(self.capability_id))
        object.__setattr__(self, "suite_id", _text(self.suite_id))
        object.__setattr__(self, "outcome", _text(self.outcome).upper())
        object.__setattr__(self, "observed_level", _level(self.observed_level))
        object.__setattr__(self, "evidence_ref", _durable_ref(self.evidence_ref, "evidence_ref"))
        object.__setattr__(self, "evaluator", _text(self.evaluator))
        object.__setattr__(
            self,
            "source_refs",
            tuple(_durable_ref(ref, "source_ref") for ref in self.source_refs),
        )
        if not self.capability_id or not self.suite_id or not self.evaluator:
            raise ValueError("evaluation evidence requires capability/suite/evaluator")
        if self.outcome not in EVIDENCE_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(EVIDENCE_OUTCOMES)}")
        if isinstance(self.observed_at_ms, bool) or int(self.observed_at_ms) <= 0:
            raise ValueError("observed_at_ms must be positive int")
        object.__setattr__(self, "observed_at_ms", int(self.observed_at_ms))
        if self.expires_at_ms is not None:
            expires = int(self.expires_at_ms)
            if expires <= self.observed_at_ms:
                raise ValueError("expires_at_ms must be after observed_at_ms")
            object.__setattr__(self, "expires_at_ms", expires)
        if self.schema_version != CAPABILITY_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported capability evidence schema")

    @property
    def passed(self) -> bool:
        return self.outcome == EVIDENCE_PASS

    @property
    def evidence_digest(self) -> str:
        digest = _digest(
            {
                "capability_id": self.capability_id,
                "suite_id": self.suite_id,
                "outcome": self.outcome,
                "observed_level": self.observed_level,
                "evidence_ref": self.evidence_ref,
                "observed_at_ms": self.observed_at_ms,
                "expires_at_ms": self.expires_at_ms,
                "source_refs": self.source_refs,
                "evaluator": self.evaluator,
            }
        )
        return f"sha256:{digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "capability_id": self.capability_id,
            "suite_id": self.suite_id,
            "outcome": self.outcome,
            "observed_level": self.observed_level,
            "maturity_level": f"L{self.observed_level}",
            "maturity_name": MATURITY_LEVEL_NAMES[self.observed_level],
            "evidence_ref": self.evidence_ref,
            "evidence_digest": self.evidence_digest,
            "observed_at_ms": self.observed_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "source_refs": list(self.source_refs),
            "evaluator": self.evaluator,
            "read_only": True,
        }


@dataclass(frozen=True, slots=True)
class CapabilityScorecard(_ReadModel):
    capability_id: str
    source_kind: str
    source_ref: str
    canonical_risk: int
    static_maturity_ceiling: int
    maturity_level: int
    evidence: tuple[CapabilityEvaluationEvidence, ...] = field(default_factory=tuple)
    status: str = CAPABILITY_STABLE
    downgrade_reasons: tuple[str, ...] = field(default_factory=tuple)
    generated_at_ms: int = 0
    previous_scorecard_ref: str | None = None
    schema_version: str = CAPABILITY_SCORECARD_SCHEMA_VERSION
    policy_effect: str = CAPABILITY_POLICY_RESTRICT_ONLY

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _text(self.capability_id))
        object.__setattr__(self, "source_kind", _text(self.source_kind).upper())
        object.__setattr__(self, "source_ref", _durable_ref(self.source_ref, "source_ref"))
        object.__setattr__(self, "canonical_risk", _risk(self.canonical_risk))
        object.__setattr__(self, "static_maturity_ceiling", _level(self.static_maturity_ceiling))
        object.__setattr__(self, "maturity_level", _level(self.maturity_level))
        object.__setattr__(self, "status", _text(self.status).upper())
        object.__setattr__(
            self,
            "downgrade_reasons",
            tuple(_text(item) for item in self.downgrade_reasons if _text(item)),
        )
        normalized_evidence = tuple(self.evidence)
        if any(not isinstance(item, CapabilityEvaluationEvidence) for item in normalized_evidence):
            raise ValueError("scorecard evidence must contain CapabilityEvaluationEvidence")
        object.__setattr__(self, "evidence", normalized_evidence)
        if not self.capability_id or not self.source_kind:
            raise ValueError("scorecard requires capability_id and source_kind")
        expected_ceiling = static_maturity_ceiling_for_risk(self.canonical_risk)
        if self.static_maturity_ceiling > expected_ceiling:
            raise ValueError("static maturity ceiling cannot relax canonical risk ceiling")
        if self.maturity_level > self.static_maturity_ceiling:
            raise ValueError("maturity level cannot exceed static maturity ceiling")
        if self.status not in {CAPABILITY_STABLE, CAPABILITY_DEGRADED}:
            raise ValueError("scorecard status must be STABLE or DEGRADED")
        if isinstance(self.generated_at_ms, bool) or int(self.generated_at_ms) <= 0:
            raise ValueError("generated_at_ms must be positive int")
        object.__setattr__(self, "generated_at_ms", int(self.generated_at_ms))
        if self.previous_scorecard_ref is not None:
            object.__setattr__(
                self,
                "previous_scorecard_ref",
                _durable_ref(self.previous_scorecard_ref, "previous_scorecard_ref"),
            )
        if any(item.capability_id != self.capability_id for item in self.evidence):
            raise ValueError("all evidence must belong to scorecard capability")
        if self.schema_version != CAPABILITY_SCORECARD_SCHEMA_VERSION:
            raise ValueError("unsupported capability scorecard schema")
        if self.policy_effect != CAPABILITY_POLICY_RESTRICT_ONLY:
            raise ValueError("capability scorecard policy effect must be RESTRICT_ONLY")

    @property
    def maturity(self) -> str:
        return f"L{self.maturity_level}"

    @property
    def execution_mode(self) -> str:
        return {
            0: "DISABLED",
            1: "LAB_ONLY",
            2: "REVIEW_REQUIRED",
            3: "LOW_RISK_ONLY",
            4: "GOVERNED_COMPLEX",
            5: "GOVERNED_STABLE",
        }[self.maturity_level]

    @property
    def evaluation_summary(self) -> dict[str, Any]:
        counts = {EVIDENCE_PASS: 0, EVIDENCE_FAIL: 0, EVIDENCE_ERROR: 0}
        for item in self.evidence:
            counts[item.outcome] += 1
        total = len(self.evidence)
        latest = max(self.evidence, key=lambda item: item.observed_at_ms) if self.evidence else None
        return {
            "total": total,
            "pass": counts[EVIDENCE_PASS],
            "fail": counts[EVIDENCE_FAIL],
            "error": counts[EVIDENCE_ERROR],
            "pass_rate": round(counts[EVIDENCE_PASS] / total, 6) if total else None,
            "latest_evidence_ref": latest.evidence_ref if latest is not None else None,
            "latest_outcome": latest.outcome if latest is not None else None,
            "latest_observed_at_ms": latest.observed_at_ms if latest is not None else None,
        }

    @property
    def scorecard_ref(self) -> str:
        digest = _digest(
            {
                "capability_id": self.capability_id,
                "source_ref": self.source_ref,
                "canonical_risk": self.canonical_risk,
                "static_maturity_ceiling": self.static_maturity_ceiling,
                "maturity_level": self.maturity_level,
                "evidence_digests": [item.evidence_digest for item in self.evidence],
                "generated_at_ms": self.generated_at_ms,
            }
        )
        return f"capability-scorecard://sha256/{digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scorecard_ref": self.scorecard_ref,
            "capability_id": self.capability_id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "canonical_risk": self.canonical_risk,
            "static_risk_floor": self.canonical_risk,
            "static_maturity_ceiling": self.static_maturity_ceiling,
            "maturity_level": self.maturity,
            "maturity_level_value": self.maturity_level,
            "maturity_name": MATURITY_LEVEL_NAMES[self.maturity_level],
            "execution_mode": self.execution_mode,
            "auto_execution_eligible": self.maturity_level >= 3 and self.canonical_risk <= 2,
            "status": self.status,
            "evidence": [item.to_dict() for item in self.evidence],
            "evidence_refs": [item.evidence_ref for item in self.evidence],
            "evaluation_summary": self.evaluation_summary,
            "downgrade_reasons": list(self.downgrade_reasons),
            "generated_at_ms": self.generated_at_ms,
            "previous_scorecard_ref": self.previous_scorecard_ref,
            "policy_effect": self.policy_effect,
            "read_only": True,
        }


@dataclass(frozen=True, slots=True)
class CapabilityUpgradeCandidate(_ReadModel):
    capability_id: str
    scorecard_ref: str
    current_level: int
    proposed_level: int
    evidence_refs: tuple[str, ...]
    requested_at_ms: int
    static_maturity_ceiling: int
    canonical_risk: int
    review_authority: str = "MASTER"
    status: str = CAPABILITY_REVIEW_REQUIRED
    schema_version: str = CAPABILITY_UPGRADE_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _text(self.capability_id))
        object.__setattr__(self, "scorecard_ref", _durable_ref(self.scorecard_ref, "scorecard_ref"))
        object.__setattr__(self, "current_level", _level(self.current_level))
        object.__setattr__(self, "proposed_level", _level(self.proposed_level))
        object.__setattr__(self, "static_maturity_ceiling", _level(self.static_maturity_ceiling))
        object.__setattr__(self, "canonical_risk", _risk(self.canonical_risk))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(_durable_ref(ref, "evidence_ref") for ref in self.evidence_refs),
        )
        object.__setattr__(self, "review_authority", _text(self.review_authority).upper())
        object.__setattr__(self, "status", _text(self.status).upper())
        if not self.capability_id or self.proposed_level <= self.current_level:
            raise ValueError("upgrade candidate must propose a higher maturity level")
        if self.proposed_level > self.static_maturity_ceiling:
            raise ValueError("upgrade candidate cannot exceed static maturity ceiling")
        if not self.evidence_refs:
            raise ValueError("upgrade candidate requires evaluation evidence")
        if self.review_authority != "MASTER" or self.status != CAPABILITY_REVIEW_REQUIRED:
            raise ValueError("capability upgrade requires MASTER Review")
        if int(self.requested_at_ms) <= 0:
            raise ValueError("requested_at_ms must be positive int")
        object.__setattr__(self, "requested_at_ms", int(self.requested_at_ms))
        if self.schema_version != CAPABILITY_UPGRADE_CANDIDATE_SCHEMA_VERSION:
            raise ValueError("unsupported capability upgrade candidate schema")

    @property
    def candidate_ref(self) -> str:
        digest = _digest(
            {
                "capability_id": self.capability_id,
                "scorecard_ref": self.scorecard_ref,
                "current_level": self.current_level,
                "proposed_level": self.proposed_level,
                "evidence_refs": self.evidence_refs,
                "requested_at_ms": self.requested_at_ms,
            }
        )
        return f"capability-upgrade://sha256/{digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_ref": self.candidate_ref,
            "capability_id": self.capability_id,
            "scorecard_ref": self.scorecard_ref,
            "current_level": f"L{self.current_level}",
            "current_level_value": self.current_level,
            "proposed_level": f"L{self.proposed_level}",
            "proposed_level_value": self.proposed_level,
            "proposed_maturity_name": MATURITY_LEVEL_NAMES[self.proposed_level],
            "static_maturity_ceiling": self.static_maturity_ceiling,
            "canonical_risk": self.canonical_risk,
            "evidence_refs": list(self.evidence_refs),
            "requested_at_ms": self.requested_at_ms,
            "review_authority": self.review_authority,
            "status": self.status,
            "changes_execution_authority": False,
            "read_only": True,
        }


def build_capability_scorecard(
    *,
    capability_id: str,
    source_kind: str,
    source_ref: str,
    risk: CanonicalRisk | int,
    maturity_level: int | str,
    generated_at_ms: int,
    evidence: Sequence[CapabilityEvaluationEvidence] = (),
) -> CapabilityScorecard:
    risk_value = risk.canonical_risk if isinstance(risk, CanonicalRisk) else _risk(risk)
    ceiling = (
        risk.static_maturity_ceiling
        if isinstance(risk, CanonicalRisk)
        else static_maturity_ceiling_for_risk(risk_value)
    )
    requested = _level(maturity_level)
    effective = min(requested, ceiling)
    reasons = () if requested == effective else ("STATIC_MATURITY_CEILING",)
    return CapabilityScorecard(
        capability_id=capability_id,
        source_kind=source_kind,
        source_ref=source_ref,
        canonical_risk=risk_value,
        static_maturity_ceiling=ceiling,
        maturity_level=effective,
        evidence=tuple(evidence),
        status=CAPABILITY_DEGRADED if reasons else CAPABILITY_STABLE,
        downgrade_reasons=reasons,
        generated_at_ms=generated_at_ms,
    )


def build_new_capability_scorecard(
    *,
    capability_id: str,
    source_kind: str,
    source_ref: str,
    risk: CanonicalRisk | int,
    generated_at_ms: int,
    requested_level: int | str = CapabilityMaturityLevel.L2,
    evidence: Sequence[CapabilityEvaluationEvidence] = (),
) -> CapabilityScorecard:
    """Bootstrap a newly registered capability at no more than L2."""

    initial = min(_level(requested_level), CapabilityMaturityLevel.L2.value)
    card = build_capability_scorecard(
        capability_id=capability_id,
        source_kind=source_kind,
        source_ref=source_ref,
        risk=risk,
        maturity_level=initial,
        generated_at_ms=generated_at_ms,
        evidence=evidence,
    )
    if _level(requested_level) <= initial:
        return card
    return replace(
        card,
        status=CAPABILITY_DEGRADED,
        downgrade_reasons=tuple((*card.downgrade_reasons, "NEW_CAPABILITY_MAX_L2")),
    )


def scorecard_from_skill_meta(
    meta: Any,
    *,
    maturity_level: int | str,
    generated_at_ms: int,
) -> CapabilityScorecard:
    capability_id = _text(_field(meta, "skill_name"))
    if not capability_id:
        raise ValueError("SkillMeta adapter requires skill_name")
    version = _ref_part(_field(meta, "version", "current"))
    return build_capability_scorecard(
        capability_id=capability_id,
        source_kind=SOURCE_SKILL_META,
        source_ref=f"skill-registry://{_ref_part(capability_id)}/{version}",
        risk=canonical_risk_from_skill_meta(meta),
        maturity_level=maturity_level,
        generated_at_ms=generated_at_ms,
    )


def scorecard_from_tool_meta(
    meta: Any,
    *,
    maturity_level: int | str,
    generated_at_ms: int,
) -> CapabilityScorecard:
    provider = _text(_field(meta, "provider_id", "local")) or "local"
    tool_id = _text(_field(meta, "tool_id", _field(meta, "id", "")))
    if not tool_id:
        raise ValueError("ToolMeta adapter requires tool_id/id")
    version = _ref_part(_field(meta, "version", "current"))
    return build_capability_scorecard(
        capability_id=f"{provider}::{tool_id}",
        source_kind=SOURCE_TOOL_META,
        source_ref=f"tool-registry://{_ref_part(provider)}/{_ref_part(tool_id)}/{version}",
        risk=canonical_risk_from_tool_meta(meta),
        maturity_level=maturity_level,
        generated_at_ms=generated_at_ms,
    )


def scorecard_from_pentest_descriptor(
    descriptor: Any,
    *,
    maturity_level: int | str,
    generated_at_ms: int,
) -> CapabilityScorecard:
    tool_id = _text(_field(descriptor, "tool_id"))
    if not tool_id:
        raise ValueError("Pentest ToolDescriptor adapter requires tool_id")
    return build_capability_scorecard(
        capability_id=tool_id,
        source_kind=SOURCE_PENTEST_TOOL,
        source_ref=f"pentest-tool-registry://{_ref_part(tool_id)}/current",
        risk=canonical_risk_from_pentest_descriptor(descriptor),
        maturity_level=maturity_level,
        generated_at_ms=generated_at_ms,
    )


def scorecard_from_agent_mode(
    mode: Any,
    *,
    capability_id: str,
    maturity_level: int | str,
    generated_at_ms: int,
) -> CapabilityScorecard:
    mode_value = _field(mode, "risk_mode", _field(mode, "mode", mode))
    normalized_mode = _text(mode_value).lower()
    return build_capability_scorecard(
        capability_id=capability_id,
        source_kind=SOURCE_AGENT_MODE,
        source_ref=f"agent-mode://{_ref_part(capability_id)}/{_ref_part(normalized_mode)}",
        risk=canonical_risk_from_agent_mode(mode_value),
        maturity_level=maturity_level,
        generated_at_ms=generated_at_ms,
    )


def apply_automatic_evaluation(
    scorecard: CapabilityScorecard,
    evidence: CapabilityEvaluationEvidence,
    *,
    generated_at_ms: int,
) -> CapabilityScorecard:
    """Apply evidence without ever raising maturity or execution authority."""

    if evidence.capability_id != scorecard.capability_id:
        raise ValueError("evidence capability does not match scorecard")
    target = min(scorecard.maturity_level, scorecard.static_maturity_ceiling)
    reasons = list(scorecard.downgrade_reasons)
    if not evidence.passed:
        target = min(target, evidence.observed_level, max(0, scorecard.maturity_level - 1))
        reasons.append(f"EVALUATION_{evidence.outcome}:{evidence.suite_id}")
    elif evidence.observed_level < target:
        target = evidence.observed_level
        reasons.append(f"EVALUATION_BELOW_CURRENT:{evidence.suite_id}")
    # PASS evidence above current level is retained, but cannot promote here.
    return replace(
        scorecard,
        maturity_level=target,
        evidence=tuple((*scorecard.evidence, evidence)),
        status=(
            CAPABILITY_DEGRADED
            if not evidence.passed or target < scorecard.maturity_level
            else scorecard.status
        ),
        downgrade_reasons=tuple(dict.fromkeys(reasons)),
        generated_at_ms=int(generated_at_ms),
        previous_scorecard_ref=scorecard.scorecard_ref,
    )


def create_upgrade_candidate(
    scorecard: CapabilityScorecard,
    *,
    proposed_level: int | str,
    evidence: Sequence[CapabilityEvaluationEvidence],
    requested_at_ms: int,
) -> CapabilityUpgradeCandidate:
    """Create a non-executable MASTER Review candidate; never mutate scorecard."""

    target = _level(proposed_level)
    requested_at = int(requested_at_ms)
    relevant = tuple(item for item in evidence if item.capability_id == scorecard.capability_id)
    if len(relevant) != len(tuple(evidence)):
        raise ValueError("all upgrade evidence must belong to scorecard capability")
    if not relevant or any(not item.passed or item.observed_level < target for item in relevant):
        raise ValueError("upgrade requires passing evidence at the proposed maturity level")
    if any(item.observed_at_ms > requested_at for item in relevant):
        raise ValueError("upgrade evidence cannot be observed after the request")
    if any(item.expires_at_ms is not None and requested_at >= item.expires_at_ms for item in relevant):
        raise ValueError("upgrade evidence must be unexpired")
    return CapabilityUpgradeCandidate(
        capability_id=scorecard.capability_id,
        scorecard_ref=scorecard.scorecard_ref,
        current_level=scorecard.maturity_level,
        proposed_level=target,
        evidence_refs=tuple(item.evidence_ref for item in relevant),
        requested_at_ms=requested_at,
        static_maturity_ceiling=scorecard.static_maturity_ceiling,
        canonical_risk=scorecard.canonical_risk,
    )


# Backwards-friendly service aliases for callers that prefer verb phrases.
automatic_downgrade = apply_automatic_evaluation
request_maturity_upgrade = create_upgrade_candidate


__all__ = [
    "CAPABILITY_EVIDENCE_SCHEMA_VERSION",
    "CAPABILITY_POLICY_RESTRICT_ONLY",
    "CAPABILITY_RISK_SCHEMA_VERSION",
    "CAPABILITY_SCORECARD_SCHEMA_VERSION",
    "CAPABILITY_UPGRADE_CANDIDATE_SCHEMA_VERSION",
    "CapabilityEvaluationEvidence",
    "CapabilityMaturityLevel",
    "CapabilityScorecard",
    "CapabilityUpgradeCandidate",
    "CanonicalRisk",
    "MATURITY_LEVEL_NAMES",
    "MaturityLevel",
    "adapt_agent_mode_risk",
    "adapt_capability_risk",
    "adapt_pentest_tool_risk",
    "adapt_skill_meta_risk",
    "adapt_tool_meta_risk",
    "apply_automatic_evaluation",
    "automatic_downgrade",
    "build_capability_scorecard",
    "build_new_capability_scorecard",
    "canonical_risk_from_agent_mode",
    "canonical_risk_from_pentest_descriptor",
    "canonical_risk_from_skill_meta",
    "canonical_risk_from_tool_meta",
    "create_upgrade_candidate",
    "request_maturity_upgrade",
    "scorecard_from_agent_mode",
    "scorecard_from_pentest_descriptor",
    "scorecard_from_skill_meta",
    "scorecard_from_tool_meta",
    "static_maturity_ceiling_for_risk",
]
