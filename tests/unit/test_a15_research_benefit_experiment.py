from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from host_brain.model_runtime.live_prompts import build_live_production_prompt_registry
from host_brain.planning_search.research_benefit_experiment import (
    ResearchBenefitExperimentError,
    load_fixed_research_cohort,
    prepare_research_workload,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs" / "acceptance" / "research_cohort_v1" / "cohort.json"


def test_fixed_research_cohort_prepares_real_competing_search_arms() -> None:
    cohort = load_fixed_research_cohort(CONFIG)
    registry = build_live_production_prompt_registry()
    prepared = tuple(
        prepare_research_workload(
            cohort=cohort,
            workload=workload,
            prompt_registry=registry,
        )
        for workload in cohort.workloads
    )

    assert len(prepared) == 24
    assert {item.definition.topic_category for item in prepared} == {
        "runtime_recovery",
        "storage_persistence",
        "model_evidence",
    }
    assert {kind for item in prepared for kind in item.source_kinds} == {
        "LOCAL_FILE",
        "WEB",
    }
    for item in prepared:
        assert item.selected_mcts_used is True
        assert item.baseline_plan.prompt_digest != item.selected_plan.prompt_digest
        assert len(item.baseline_plan.source_ids) == 1
        assert item.baseline_plan.source_ids[0].endswith("secondary-summary")
        assert len(item.selected_plan.source_ids) == 2
        assert all(
            not source_id.endswith("secondary-summary")
            for source_id in item.selected_plan.source_ids
        )
        assert item.baseline_plan.candidate_ids != item.selected_plan.candidate_ids


def test_fixed_execution_prompt_contains_exact_whole_document_citation_options() -> None:
    cohort = load_fixed_research_cohort(CONFIG)
    prepared = prepare_research_workload(
        cohort=cohort,
        workload=cohort.workloads[0],
        prompt_registry=build_live_production_prompt_registry(),
    )

    baseline_payload = json.loads(prepared.baseline_input.messages[1]["content"])
    selected_payload = json.loads(prepared.selected_input.messages[1]["content"])
    assert baseline_payload["objective"] == prepared.definition.objective
    assert len(baseline_payload["sources"]) == 1
    assert len(selected_payload["sources"]) == 2
    for payload in (baseline_payload, selected_payload):
        by_source = {source["source_id"]: source for source in payload["sources"]}
        for citation in payload["citation_options"]:
            source = by_source[citation["source_id"]]
            encoded = source["content"].encode("utf-8")
            assert citation["start_byte"] == 0
            assert citation["end_byte"] == len(encoded)
            assert len(citation["span_digest"]) == 64


def test_fixed_corpus_digest_drift_fails_before_planning(tmp_path: Path) -> None:
    copied = tmp_path / "research_cohort_v1"
    shutil.copytree(CONFIG.parent, copied)
    source = copied / "sources" / "runtime_host_policy.txt"
    source.write_text(source.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")

    with pytest.raises(ResearchBenefitExperimentError):
        load_fixed_research_cohort(copied / "cohort.json")
