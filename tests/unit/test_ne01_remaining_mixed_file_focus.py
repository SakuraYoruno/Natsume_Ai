from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative_path", "maximum_lines"),
    (
        ("host_brain/app/obs_routes_stats_input_humanoid.py", 1_500),
        ("host_brain/orchestration/node_grants.py", 1_500),
        ("host_brain/orchestration/planning_graph_compiler.py", 1_500),
        ("host_brain/observability/degradation_state.py", 1_900),
    ),
)
def test_remaining_large_files_stay_inside_existing_policy_ceiling(
    relative_path: str,
    maximum_lines: int,
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8-sig")
    assert len(source.splitlines()) <= maximum_lines


def test_humanoid_models_are_pure_and_public_reexports_keep_identity() -> None:
    from host_brain.app.obs_input_humanoid_models import (
        InputHumanoidLoopStatsResponse as extracted,
    )
    from host_brain.app.obs_routes_stats_input_humanoid import (
        InputHumanoidLoopStatsResponse as public,
    )

    source = (
        ROOT / "host_brain/app/obs_input_humanoid_models.py"
    ).read_text(encoding="utf-8")
    assert public is extracted
    assert "APIRouter" not in source
    assert "@router" not in source
    assert "sqlite" not in source.lower()


def test_node_grant_helpers_own_no_service_or_store_authority() -> None:
    from host_brain.orchestration.node_grant_codec import exact_integer
    from host_brain.orchestration.node_grant_errors import (
        NodeGrantDerivationError as extracted,
    )
    from host_brain.orchestration.node_grants import (
        NodeGrantDerivationError as public,
    )

    codec_source = (
        ROOT / "host_brain/orchestration/node_grant_codec.py"
    ).read_text(encoding="utf-8")
    error_source = (
        ROOT / "host_brain/orchestration/node_grant_errors.py"
    ).read_text(encoding="utf-8")
    assert public is extracted
    assert "SQLiteLedger" not in codec_source + error_source
    assert "threading" not in codec_source + error_source
    with pytest.raises(ValueError):
        exact_integer(True)


def test_planning_codec_and_degradation_catalog_are_authority_free() -> None:
    from host_brain.observability.degradation_policy_catalog import (
        FaultPolicy as extracted,
    )
    from host_brain.observability.degradation_state import FaultPolicy as public

    planning_source = (
        ROOT / "host_brain/orchestration/planning_graph_codec.py"
    ).read_text(encoding="utf-8")
    policy_source = (
        ROOT / "host_brain/observability/degradation_policy_catalog.py"
    ).read_text(encoding="utf-8")
    assert public is extracted
    assert "class PlanningGraphCompiler" not in planning_source
    assert "SQLiteLedger" not in planning_source
    assert "class DegradationState" not in policy_source
    assert "RuntimeEvent" not in policy_source
    assert ".append(" not in policy_source
