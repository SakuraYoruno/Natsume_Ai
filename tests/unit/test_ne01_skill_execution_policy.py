from __future__ import annotations

from collections import Counter
import logging

import pytest

from host_brain.app.governed_skill_execution import (
    _require_governed_skill_policy,
)
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.app.governed_task_authority import GovernedHostAdapterError
from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)
from host_brain.core.errors import (
    GOVERNED_SKILL_ACCEPTANCE_EFFECT_REQUIRED,
)
from host_brain.domain_packs.media_translation import (
    build_media_translation_pack,
)
from host_brain.domain_packs.research_browser import (
    build_research_browser_pack,
)
from host_brain.orchestration.capability_plan_search import (
    _registry_capabilities,
)
from host_brain.skills.builtin_execution_policy import (
    builtin_skill_policies,
    builtin_skill_policy_inventory,
)
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.declarative import DeclarativeSkillSpec
from host_brain.skills.provider_catalog import (
    BUILTIN_SKILL_PROVIDER_SPECS,
    register_configured_skill_providers,
)
from host_brain.skills.personality_registry import (
    build_personality_skill_registry,
)
from host_brain.skills.registry import SkillMeta, SkillRegistry


def _handler(_args: dict, _context: dict | None = None) -> dict:
    return {"status": "OK"}


def _meta(
    skill_name: str,
    *,
    execution_policy: SkillExecutionPolicy,
    budget_policy: SkillBudgetPolicy = SkillBudgetPolicy.TASK_PRECHARGE,
) -> SkillMeta:
    return SkillMeta(
        skill_name=skill_name,
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=False,
        handler=_handler,
        family="code",
        capability_tags=("coding",),
        execution_policy=execution_policy,
        budget_policy=budget_policy,
    )


def _production_registry() -> SkillRegistry:
    registry = build_builtin_skill_registry()
    registered = register_configured_skill_providers(
        registry,
        parse_bool_env=lambda *_args, **_kwargs: True,
        logger=logging.getLogger("test.skill_execution_policy"),
    )
    assert registered == len(BUILTIN_SKILL_PROVIDER_SPECS)
    return registry


def test_production_skill_inventory_is_exhaustively_classified() -> None:
    registry = _production_registry()
    policies = builtin_skill_policy_inventory()
    declarative_specs = tuple(
        skill_spec
        for provider_spec in BUILTIN_SKILL_PROVIDER_SPECS
        if callable(
            getattr(provider_spec.instantiate(), "get_skill_specs", None)
        )
        for skill_spec in provider_spec.instantiate().get_skill_specs()
    )

    assert len(registry.all()) == 86
    assert len(policies) == 80
    assert set(policies) == {
        skill_spec.skill_name for skill_spec in declarative_specs
    }
    assert all(
        policies[skill_spec.skill_name]["tool"] == skill_spec.tool
        and policies[skill_spec.skill_name]["action"] == skill_spec.action
        for skill_spec in declarative_specs
    )
    assert Counter(
        meta.execution_policy for meta in registry.all()
    ) == {
        SkillExecutionPolicy.INLINE_READ_ONLY: 24,
        SkillExecutionPolicy.PRE_ACCEPTANCE_PROPOSAL_ONLY: 13,
        SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE: 3,
        SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT: 46,
    }
    assert Counter(meta.budget_policy for meta in registry.all()) == {
        SkillBudgetPolicy.TASK_PRECHARGE: 62,
        SkillBudgetPolicy.TASK_PRECHARGE_AGENT_MODEL: 24,
    }
    assert {
        meta.skill_name
        for meta in registry.all()
        if (
            meta.execution_policy
            is SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE
        )
    } == {"agent_worker", "agent_self_heal", "agent_self_evolve"}
    assert len(registry.build_skill_manifest()) == 40
    assert all(
        row["governed_execution_enabled"] is True
        for row in registry.build_skill_manifest()
    )


def test_builtin_descriptor_drift_and_missing_policy_fail_closed() -> None:
    with pytest.raises(
        ValueError,
        match="BUILTIN_SKILL_EXECUTION_DESCRIPTOR_DRIFT",
    ):
        builtin_skill_policies(
            skill_id="file_read",
            tool="file",
            action="write",
        )
    with pytest.raises(
        ValueError,
        match="BUILTIN_SKILL_EXECUTION_POLICY_MISSING",
    ):
        builtin_skill_policies(
            skill_id="new_unreviewed_skill",
            tool="demo",
            action="run",
        )


def test_unclassified_direct_skill_is_catalog_only_and_fails_closed() -> None:
    meta = SkillMeta(
        skill_name="unclassified_direct_skill",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=False,
        handler=_handler,
    )
    registry = SkillRegistry()
    registry.register(meta)

    assert meta.governed_execution_enabled is False
    assert registry.build_skill_manifest() == []
    with pytest.raises(
        PermissionError,
        match="GOVERNED_SKILL_EXECUTION_POLICY_INVALID",
    ):
        _require_governed_skill_policy(meta.skill_name, meta)


def test_legacy_personality_registry_has_zero_governed_manifest() -> None:
    registry = build_personality_skill_registry()

    assert len(registry.all()) == 18
    assert registry.get("proactive_talk") is None
    assert "proactive_talk" not in builtin_skill_policy_inventory()
    assert registry.build_skill_manifest() == []
    assert Counter(meta.execution_policy for meta in registry.all()) == {
        SkillExecutionPolicy.UNCLASSIFIED: 14,
        SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT: 4,
    }


def test_user_visible_domain_lifecycle_is_not_an_inline_skill_effect() -> None:
    policies = builtin_skill_policy_inventory()
    user_visible_capabilities = {
        capability.capability_id
        for pack in (
            build_media_translation_pack(),
            build_research_browser_pack(),
        )
        for capability in pack.manifest.capabilities
        if any(stage.user_visible for stage in capability.lifecycle_stages)
    }

    assert user_visible_capabilities
    assert all(
        policies[capability_id]["execution_policy"]
        == SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT.value
        for capability_id in user_visible_capabilities
    )


def test_custom_declarative_skill_requires_two_explicit_policies() -> None:
    spec = DeclarativeSkillSpec(
        skill_name="new_unreviewed_skill",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=False,
        description="test",
        tool="demo",
        action="run",
    )
    with pytest.raises(
        ValueError,
        match="BUILTIN_SKILL_EXECUTION_POLICY_MISSING",
    ):
        spec.to_meta()

    explicit = DeclarativeSkillSpec(
        skill_name="reviewed_read_skill",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=False,
        description="test",
        tool="demo",
        action="read",
        execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
        budget_policy=SkillBudgetPolicy.TASK_PRECHARGE,
    )
    assert (
        explicit.to_meta().execution_policy
        is SkillExecutionPolicy.INLINE_READ_ONLY
    )


def test_direct_admission_rejects_catalog_only_skill() -> None:
    registry = SkillRegistry()
    registry.register(
        _meta(
            "blocked_skill",
            execution_policy=(
                SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT
            ),
        )
    )
    runtime = object.__new__(GovernedHostRuntime)
    runtime._planning_skill_registry = registry

    with pytest.raises(GovernedHostAdapterError) as raised:
        runtime._direct_skill_execution_policy(
            {"route": "skill", "target_id": "blocked_skill"}
        )
    assert raised.value.reason_code == (
        GOVERNED_SKILL_ACCEPTANCE_EFFECT_REQUIRED
    )


def test_direct_admission_rejects_unclassified_or_inconsistent_policy() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="unclassified_skill",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=_handler,
        )
    )
    registry.register(
        _meta(
            "fake_workspace_writer",
            execution_policy=(
                SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE
            ),
        )
    )
    runtime = object.__new__(GovernedHostRuntime)
    runtime._planning_skill_registry = registry

    for skill_name in ("unclassified_skill", "fake_workspace_writer"):
        with pytest.raises(GovernedHostAdapterError) as raised:
            runtime._direct_skill_execution_policy(
                {"route": "skill", "target_id": skill_name}
            )
        assert raised.value.reason_code == (
            "GOVERNED_SKILL_EXECUTION_POLICY_INVALID"
        )


def test_stale_graph_execution_rejects_catalog_only_skill() -> None:
    blocked = _meta(
        "blocked_skill",
        execution_policy=(
            SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT
        ),
    )
    with pytest.raises(
        PermissionError,
        match=GOVERNED_SKILL_ACCEPTANCE_EFFECT_REQUIRED,
    ):
        _require_governed_skill_policy("blocked_skill", blocked)


def test_capability_search_excludes_catalog_only_skills() -> None:
    registry = SkillRegistry()
    registry.register(
        _meta(
            "enabled_read",
            execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
        )
    )
    registry.register(
        _meta(
            "blocked_write",
            execution_policy=(
                SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT
            ),
        )
    )

    capabilities = _registry_capabilities(
        registry,
        requested_tags=("coding",),
        max_risk_level=3,
    )

    assert [item.capability_id for item in capabilities] == ["enabled_read"]
