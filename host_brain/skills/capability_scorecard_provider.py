"""Read-only production composition for federated capability scorecards.

The provider projects live registry metadata on demand.  It owns no database,
does not mutate a source registry, and has no promotion/apply API.  Existing
SkillMeta entries receive a conservative static baseline; external MCP and
Pentest capabilities start supervised/lab-only even when their metadata says
they are low risk.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
import hashlib
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.skills.capability_scorecard import (
    SOURCE_AGENT_MODE,
    SOURCE_PENTEST_TOOL,
    SOURCE_SKILL_META,
    SOURCE_TOOL_META,
    CapabilityEvaluationEvidence,
    CapabilityScorecard,
    scorecard_from_agent_mode,
    scorecard_from_pentest_descriptor,
    scorecard_from_skill_meta,
    scorecard_from_tool_meta,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _baseline_level(*, canonical_risk: int, source_kind: str) -> int:
    risk = int(canonical_risk)
    if source_kind in {SOURCE_TOOL_META, SOURCE_PENTEST_TOOL}:
        # Provider tools and offensive capabilities are federated/external;
        # their initial projection never exceeds human-supervised L2.
        return 1 if risk >= 5 else 2
    if risk <= 2:
        return 3
    if risk <= 4:
        return 2
    return 1


def _baseline_evidence(card: CapabilityScorecard, *, observed_at_ms: int) -> CapabilityEvaluationEvidence:
    digest = hashlib.sha256(
        (
            f"{card.capability_id}|{card.source_kind}|{card.source_ref}|"
            f"{card.canonical_risk}|{card.maturity_level}"
        ).encode("utf-8")
    ).hexdigest()
    return CapabilityEvaluationEvidence(
        capability_id=card.capability_id,
        suite_id="federated-static-baseline",
        outcome="PASS",
        observed_level=card.maturity_level,
        evidence_ref=f"capability-baseline://sha256/{digest}",
        source_refs=(card.source_ref,),
        evaluator="federated-registry-adapter",
        observed_at_ms=observed_at_ms,
    )


class FederatedCapabilityScorecardProvider:
    """Read-through scorecard lookup over the existing four metadata sources."""

    def __init__(
        self,
        *,
        skill_registry: Any,
        tool_catalog: Any | None = None,
        mcp_registry: Any | None = None,
        pentest_get: Callable[[str], Any | None] | None = None,
        pentest_all: Callable[[], Iterable[Any]] | None = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        if skill_registry is None or not callable(getattr(skill_registry, "get", None)):
            raise ValueError("federated scorecard provider requires SkillRegistry")
        if not callable(clock):
            raise ValueError("federated scorecard provider clock must be callable")
        self._skill_registry = skill_registry
        self._tool_catalog = tool_catalog
        self._mcp_registry = mcp_registry
        self._pentest_get = pentest_get
        self._pentest_all = pentest_all
        self._clock = clock

    def __call__(self, capability_id: str) -> CapabilityScorecard | None:
        return self.get(capability_id)

    def get(self, capability_id: str) -> CapabilityScorecard | None:
        key = _text(capability_id)
        if not key:
            return None

        if key.startswith("mcp::"):
            tool = self._get_mcp(key)
            if tool is not None:
                return self._project_tool(tool)
            # The MCP bridge may have already projected the tool into
            # SkillRegistry before this provider receives a registry handle.
            bridged_skill = self._skill_registry.get(key)
            return self._project_skill(bridged_skill) if bridged_skill is not None else None

        descriptor = self._pentest_get(key) if self._pentest_get is not None else None
        if descriptor is not None:
            return self._project_pentest(descriptor)

        if key.startswith("agent-mode::"):
            mode = key.split("::", 1)[1]
            return self.project_agent_runtime(
                {"risk_mode": mode},
                capability_id=key,
            )

        skill = self._skill_registry.get(key)
        if skill is not None:
            return self._project_skill(skill)
        catalog_get = getattr(self._tool_catalog, "get", None)
        if callable(catalog_get):
            try:
                local_tool = catalog_get(key)
            except KeyError:
                local_tool = None
            if local_tool is not None:
                return self._project_local_tool(local_tool, capability_id=key)
        return None

    def project_agent_runtime(
        self,
        runtime: Any,
        *,
        capability_id: str | None = None,
    ) -> CapabilityScorecard:
        mode = (
            runtime.get("risk_mode", runtime.get("mode"))
            if isinstance(runtime, Mapping)
            else getattr(runtime, "risk_mode", getattr(runtime, "mode", None))
        )
        mode_text = _text(getattr(mode, "value", mode)).lower()
        key = _text(capability_id) or f"agent-mode::{mode_text}"
        generated = int(self._clock())
        provisional = scorecard_from_agent_mode(
            runtime,
            capability_id=key,
            maturity_level=0,
            generated_at_ms=generated,
        )
        level = _baseline_level(
            canonical_risk=provisional.canonical_risk,
            source_kind=SOURCE_AGENT_MODE,
        )
        card = scorecard_from_agent_mode(
            runtime,
            capability_id=key,
            maturity_level=level,
            generated_at_ms=generated,
        )
        return replace(card, evidence=(_baseline_evidence(card, observed_at_ms=generated),))

    def list_all(self) -> list[CapabilityScorecard]:
        projected: dict[str, CapabilityScorecard] = {}

        all_skills = getattr(self._skill_registry, "all", None)
        if callable(all_skills):
            for skill in all_skills():
                card = self.get(_text(getattr(skill, "skill_name", "")))
                if card is not None:
                    projected[card.capability_id] = card

        list_tools = getattr(self._tool_catalog, "list_all", None)
        if callable(list_tools):
            for tool in list_tools():
                capability_id = _text(getattr(tool, "id", ""))
                if capability_id:
                    card = self._project_local_tool(
                        tool,
                        capability_id=capability_id,
                    )
                    projected[card.capability_id] = card

        list_mcp = getattr(self._mcp_registry, "list_all", None)
        if callable(list_mcp):
            for tool in list_mcp():
                card = self._project_tool(tool)
                projected[card.capability_id] = card

        if self._pentest_all is not None:
            for descriptor in self._pentest_all():
                card = self._project_pentest(descriptor)
                projected[card.capability_id] = card

        for mode in ("safe", "guarded", "strict"):
            card = self.project_agent_runtime(
                {"risk_mode": mode},
                capability_id=f"agent-mode::{mode}",
            )
            projected[card.capability_id] = card

        return [projected[key] for key in sorted(projected)]

    def _get_mcp(self, capability_id: str) -> Any | None:
        if self._mcp_registry is None:
            return None
        parts = capability_id.split("::", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return None
        lookup = getattr(self._mcp_registry, "get", None)
        if not callable(lookup):
            return None
        return lookup(provider_id=parts[1], tool_id=parts[2])

    def _project_skill(self, meta: Any) -> CapabilityScorecard:
        generated = int(self._clock())
        provisional = scorecard_from_skill_meta(meta, maturity_level=0, generated_at_ms=generated)
        binding = getattr(meta, "version_binding", None)
        level = (
            int(binding.maturity_level)
            if binding is not None
            else _baseline_level(
                canonical_risk=provisional.canonical_risk,
                source_kind=SOURCE_SKILL_META,
            )
        )
        card = scorecard_from_skill_meta(meta, maturity_level=level, generated_at_ms=generated)
        evidence = (
            CapabilityEvaluationEvidence(
                capability_id=card.capability_id,
                suite_id="skill-lifecycle-v2",
                outcome="PASS",
                observed_level=level,
                evidence_ref=binding.maturity_decision_id,
                source_refs=(
                    binding.activation_id,
                    (
                        "skill-active-projection://sha256/"
                        f"{binding.projection_digest}"
                    ),
                    binding.bundle_id,
                ),
                evaluator="skill-lifecycle-v2-authority",
                observed_at_ms=generated,
            )
            if binding is not None
            else _baseline_evidence(card, observed_at_ms=generated)
        )
        return replace(card, evidence=(evidence,))

    def _project_tool(self, meta: Any) -> CapabilityScorecard:
        generated = int(self._clock())
        provisional = scorecard_from_tool_meta(meta, maturity_level=0, generated_at_ms=generated)
        level = _baseline_level(
            canonical_risk=provisional.canonical_risk,
            source_kind=SOURCE_TOOL_META,
        )
        card = scorecard_from_tool_meta(meta, maturity_level=level, generated_at_ms=generated)
        return replace(card, evidence=(_baseline_evidence(card, observed_at_ms=generated),))

    def _project_local_tool(
        self,
        meta: Any,
        *,
        capability_id: str,
    ) -> CapabilityScorecard:
        """Project composition-owned ToolCatalog entries without MCP identity.

        A local registration location is not maturity evidence.  ToolCatalog
        entries therefore use the same new/provider-capability L2/L1 ceiling
        as other ToolMeta sources; promotion still requires Lab evidence and
        Review through the versioned maturity authority.
        """

        generated = int(self._clock())
        provisional = scorecard_from_tool_meta(
            meta,
            capability_id=capability_id,
            maturity_level=0,
            generated_at_ms=generated,
        )
        level = _baseline_level(
            canonical_risk=provisional.canonical_risk,
            source_kind=SOURCE_TOOL_META,
        )
        card = scorecard_from_tool_meta(
            meta,
            capability_id=capability_id,
            maturity_level=level,
            generated_at_ms=generated,
        )
        return replace(
            card,
            evidence=(_baseline_evidence(card, observed_at_ms=generated),),
        )

    def _project_pentest(self, descriptor: Any) -> CapabilityScorecard:
        generated = int(self._clock())
        provisional = scorecard_from_pentest_descriptor(
            descriptor,
            maturity_level=0,
            generated_at_ms=generated,
        )
        level = _baseline_level(
            canonical_risk=provisional.canonical_risk,
            source_kind=SOURCE_PENTEST_TOOL,
        )
        card = scorecard_from_pentest_descriptor(
            descriptor,
            maturity_level=level,
            generated_at_ms=generated,
        )
        return replace(card, evidence=(_baseline_evidence(card, observed_at_ms=generated),))


__all__ = ["FederatedCapabilityScorecardProvider"]
