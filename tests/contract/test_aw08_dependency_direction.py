from __future__ import annotations

import inspect
from pathlib import Path

from host_brain.domain_packs.production_composition import (
    build_production_domain_pack_runtime,
)
from host_brain.model_runtime.invocation import complete_compiled_prompt_sync
from host_brain.persistence.experience_repository import ExperienceRepository
from tools.static_scan.dependency_direction_audit import (
    find_reverse_dependency_violations,
)


ROOT = Path(__file__).resolve().parents[2]
def test_production_packages_do_not_import_application_composition() -> None:
    violations = find_reverse_dependency_violations()
    assert violations == [], "\n" + "\n".join(
        f"{item.source}:{item.line} -> {item.target}" for item in violations
    )


def test_prompt_specs_are_owned_by_model_runtime() -> None:
    assert (ROOT / "host_brain/model_runtime/prompt_specs.py").is_file()
    assert not (ROOT / "host_brain/app/prompt_specs.py").exists()


def test_runtime_services_and_repository_collaborators_are_explicit() -> None:
    invocation = inspect.signature(complete_compiled_prompt_sync).parameters
    assert invocation["attempt_service"].default is inspect.Parameter.empty

    pack_builder = inspect.signature(build_production_domain_pack_runtime).parameters
    assert pack_builder["model_attempt_service"].default is inspect.Parameter.empty

    context_loader = inspect.signature(
        ExperienceRepository.load_memory_context_snapshot
    ).parameters
    assert context_loader["conversation_event_resolver"].default is inspect.Parameter.empty
