from __future__ import annotations

import ast
from pathlib import Path
import warnings

from host_brain.digital_life.application_boundary import project_inner_world_state
from host_brain.digital_life.policy import (
    derive_inner_weather,
    resolve_reach_out_calibration,
)


ROOT = Path(__file__).resolve().parents[2]


def test_legacy_life_memory_and_evidence_facades_are_deleted() -> None:
    retired = (
        "host_brain/runtime/life_continuity.py",
        "host_brain/runtime/life_organs.py",
        "host_brain/runtime/autonomous_self.py",
        "host_brain/persistence/life_runtime_repository.py",
        "host_brain/persona/inner_world.py",
        "host_brain/integrations/graph_evidence.py",
        "host_brain/app/memory_event_store.py",
        "host_brain/conversation/store.py",
        "host_brain/validation/digital_life_supervisor.py",
    )
    assert [path for path in retired if (ROOT / path).exists()] == []


def test_production_has_no_retired_facade_imports_or_global_evidence_getter() -> None:
    forbidden_modules = (
        "host_brain.runtime.life_continuity",
        "host_brain.runtime.life_organs",
        "host_brain.runtime.autonomous_self",
        "host_brain.persistence.life_runtime_repository",
        "host_brain.persona.inner_world",
        "host_brain.integrations.graph_evidence",
        "host_brain.app.memory_event_store",
        "host_brain.conversation.store",
    )
    violations: list[str] = []
    for path in (ROOT / "host_brain").rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module in forbidden_modules:
                    violations.append(f"{path.relative_to(ROOT)}: {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        violations.append(f"{path.relative_to(ROOT)}: {alias.name}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "get_evidence_plane":
                    violations.append(f"{path.relative_to(ROOT)}: get_evidence_plane")
    assert violations == []


def test_life_memory_store_is_constructed_only_by_host_authority_owner() -> None:
    constructors: list[str] = []
    for path in (ROOT / "host_brain").rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Name) and target.id == "LifeMemoryStore":
                constructors.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    owner_source = (
        ROOT
        / "host_brain"
        / "composition"
        / "host_authority_runtime.py"
    ).read_text(encoding="utf-8")

    assert constructors == []
    assert "life_memory_store_factory: Factory = LifeMemoryStore" in owner_source
    assert "self._life_memory_store_factory(" in owner_source


def test_canonical_life_policy_owns_weather_and_reach_out_calibration() -> None:
    assert resolve_reach_out_calibration({}) == 0.6
    assert resolve_reach_out_calibration({"reach_out_calibration": {"value": 0.82}}) == 0.82
    weather = derive_inner_weather(
        {"fatigue": 0.1, "loneliness": 0.8, "energy": 0.7},
        {"anticipation": 0.6},
    )
    assert weather["reach_out_bias"] > 0.5
    assert weather["label"] in {"空·想找人", "微亮·想靠近"}


def test_inner_world_view_is_a_pure_projection_of_canonical_life_snapshot() -> None:
    result = project_inner_world_state(
        {
            "person_id": "person-1",
            "version": 7,
            "state": {
                "clock": {"last_user_seen_ms": 123, "idle_minutes": 150},
                "affect": {"loneliness": 0.4},
                "needs": {"contact": 0.65},
                "relationship": {"warmth": 0.7, "closeness": 0.6},
                "inner_world": {"pending_topics": ["topic-a"]},
            },
        }
    )
    assert result["user_id"] == "person-1"
    assert result["total_offline_hours"] == 2.5
    assert result["anticipation"] == 0.65
    assert result["anticipation_topics"] == ["topic-a"]
    assert result["canonical_life_version"] == 7


def test_inner_world_projection_keeps_canonical_affect_bounded_and_scope_safe() -> None:
    result = project_inner_world_state(
        {
            "person_id": "person-1",
            "version": 9,
            "state": {
                "affective": {
                    "revision": 6,
                    "mood": {"label": "tender_hurt", "valence": -0.2, "arousal": 0.44, "energy": 0.57},
                    "components": [
                        {"emotion": "HURT", "intensity": 0.76, "valence": -0.6, "arousal": 0.5, "action_tendency": "repair"},
                        {"emotion": "CARE", "intensity": 0.48, "valence": 0.4, "arousal": 0.22, "action_tendency": "comfort"},
                    ],
                    "active_episodes": [
                        {
                            "trigger_ref": "private-trigger",
                            "evidence_refs": ["private-evidence"],
                        }
                    ],
                    "needs": {"connection": 0.7, "repair": 0.65},
                    "last_regulation": {"mode": "CONTAIN"},
                },
                "relationship_v2": {
                    "workspace_id": "workspace-private",
                    "relationship_subject_id": "person-1",
                    "revision": 4,
                    "trust": 0.61,
                    "safety": 0.72,
                    "attunement": 0.55,
                    "rupture_load": 0.4,
                    "repair_required": True,
                    "proactive_permission": "ALLOW",
                    "romantic_consent": "GRANTED",
                    "exclusive_consent": "GRANTED",
                    "dependency_consent": "GRANTED",
                    "active_episodes": [{"episode_id": "relationship-private"}],
                },
            },
        }
    )

    assert result["canonical_life_source"] == "CANONICAL_LIFE_JOURNAL"
    assert result["self_affect"]["components"][0]["emotion"] == "HURT"
    assert result["self_affect"]["active_episode_count"] == 1
    assert result["self_affect"]["regulation_mode"] == "CONTAIN"
    assert result["relationship_state"] == {
        "schema_version": "inner_relationship_projection.v1",
        "revision": 4,
        "trust": 0.61,
        "safety": 0.72,
        "attunement": 0.55,
        "rupture_load": 0.4,
        "repair_required": True,
        "proactive_permission": "ALLOW",
        "active_episode_count": 1,
    }
    rendered = repr({
        "self_affect": result["self_affect"],
        "relationship_state": result["relationship_state"],
    })
    for private_value in (
        "private-trigger",
        "private-evidence",
        "workspace-private",
        "GRANTED",
        "relationship-private",
    ):
        assert private_value not in rendered
