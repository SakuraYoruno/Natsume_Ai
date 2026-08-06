from __future__ import annotations

import json
from pathlib import Path

import pytest

from host_brain.model_runtime.live_prompts import (
    CODE_BENEFIT_EXECUTION_PROMPT_ID,
    build_live_production_prompt_registry,
)
from host_brain.planning_search.code_benefit_experiment import (
    CodeBenefitExperimentError,
    _execution_messages,
    _parse_model_source,
    code_execution_profile_digest,
    create_fixed_code_source_repository,
    load_fixed_code_cohort,
    prepare_code_workload,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/acceptance/code_cohort_v1/cohort.json"


def test_fixed_code_queue_has_twenty_sorted_distinct_tasks() -> None:
    cohort = load_fixed_code_cohort(CONFIG)

    assert len(cohort.workloads) == 20
    assert [item.workload_id for item in cohort.workloads] == sorted(
        item.workload_id for item in cohort.workloads
    )
    assert len({item.module_name for item in cohort.workloads}) == 20
    assert all(len(item.cases) >= 3 for item in cohort.workloads)


def test_generated_source_commit_and_search_paths_are_deterministic(tmp_path: Path) -> None:
    cohort = load_fixed_code_cohort(CONFIG)
    first = create_fixed_code_source_repository(
        cohort=cohort,
        runtime_root=tmp_path / "first",
    )
    second = create_fixed_code_source_repository(
        cohort=cohort,
        runtime_root=tmp_path / "second",
    )

    assert first.source_commit == second.source_commit
    prepared = [
        prepare_code_workload(
            cohort=cohort,
            definition=definition,
            runtime=first,
            run_namespace="unit",
        )
        for definition in cohort.workloads
    ]
    assert all(item.selected_mcts_used for item in prepared)
    assert all(
        "code.patch.safe" in item.run_spec.selected.selected_path_candidate_ids
        for item in prepared
    )
    assert all(
        "code.patch.safe" not in item.run_spec.baseline.selected_path_candidate_ids
        for item in prepared
    )
    assert all(
        item.run_spec.baseline.virtual_state_digest
        == item.run_spec.selected.virtual_state_digest
        for item in prepared
    )


def test_model_prompt_excludes_hidden_cases_and_registered_prompt_is_json(tmp_path: Path) -> None:
    cohort = load_fixed_code_cohort(CONFIG)
    runtime = create_fixed_code_source_repository(
        cohort=cohort,
        runtime_root=tmp_path / "runtime",
    )
    prepared = prepare_code_workload(
        cohort=cohort,
        definition=cohort.workloads[0],
        runtime=runtime,
        run_namespace="prompt",
    )
    messages = _execution_messages(
        prepared=prepared,
        arm=prepared.run_spec.selected,
        evidence_text=prepared.selected_evidence_text,
    )
    user_payload = json.loads(messages[1]["content"])

    assert "cases" not in user_payload
    assert set(user_payload) == {
        "current_source",
        "objective",
        "selected_candidate_path",
        "selected_requirement_evidence",
        "target_file",
    }
    registry = build_live_production_prompt_registry()
    assert (
        registry.definition(CODE_BENEFIT_EXECUTION_PROMPT_ID)
        .output_contract.kind.value
        == "json_object"
    )


@pytest.mark.parametrize(
    ("raw", "expected", "parsed"),
    [
        ('{"content":"def solve(value):\\n    return 1\\n"}', "def solve(value):\n    return 1\n", True),
        ('{"content":"def solve(value):\\n    return 1","extra":1}', "", False),
        ("```python\\ndef solve(value): return 1\\n```", "", False),
    ],
)
def test_model_source_parser_is_strict(raw: str, expected: str, parsed: bool) -> None:
    assert _parse_model_source(raw) == (expected, parsed)


def test_runtime_root_cannot_be_reused(tmp_path: Path) -> None:
    cohort = load_fixed_code_cohort(CONFIG)
    root = tmp_path / "runtime"
    create_fixed_code_source_repository(cohort=cohort, runtime_root=root)

    with pytest.raises(CodeBenefitExperimentError, match="already exists"):
        create_fixed_code_source_repository(cohort=cohort, runtime_root=root)


def test_execution_profile_binds_the_code_route_timeout() -> None:
    assert code_execution_profile_digest() != code_execution_profile_digest(
        model_timeout_ms=45_000
    )
    with pytest.raises(CodeBenefitExperimentError, match="timeout"):
        code_execution_profile_digest(model_timeout_ms=9_999)
