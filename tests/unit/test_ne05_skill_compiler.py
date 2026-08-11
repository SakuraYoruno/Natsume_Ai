from __future__ import annotations

import pytest

from host_brain.skills.skill_compiler import (
    SkillCompilerError,
    compile_skill_bundle,
    compile_skill_draft,
)


def test_ne05_skill_compiler_is_deterministic_and_requires_verification() -> None:
    kwargs = dict(
        source_kind="markdown",
        source_digest="a" * 64,
        input_contract={"type": "object", "required": ["query"]},
        output_contract={"type": "object", "required": ["answer"]},
        risk_level=2,
        capability_dependencies=("research.read",),
        tool_dependencies=("browser.read",),
        verifier_ref="artifact://verifier/skill-v1",
        test_vectors=({"query": "hello", "expected": "world"},),
        budget_profile={"max_tool_calls": 2, "max_wall_time_ms": 1000},
        rollback_target="skill://previous/known-good",
    )
    first = compile_skill_draft(**kwargs)
    second = compile_skill_draft(**kwargs)

    assert first.draft_digest == second.draft_digest
    assert first.to_dict()["draft_digest"] == first.draft_digest


def test_ne05_skill_compiler_rejects_missing_verifier_or_vectors() -> None:
    base = dict(
        source_kind="script",
        source_digest="b" * 64,
        input_contract={"type": "object"},
        output_contract={"type": "object"},
        risk_level=1,
        capability_dependencies=(),
        tool_dependencies=(),
        verifier_ref="artifact://verifier/skill-v1",
        test_vectors=({"input": 1},),
        budget_profile={"max_tool_calls": 1},
        rollback_target="skill://previous/known-good",
    )
    with pytest.raises(SkillCompilerError):
        compile_skill_draft(**{**base, "verifier_ref": ""})
    with pytest.raises(SkillCompilerError):
        compile_skill_draft(**{**base, "test_vectors": ()})


def _bundle_kwargs() -> dict:
    return {
        "skill_name": "research_summary",
        "bundle_version": "1.0.0",
        "input_contract": {
            "type": "object",
            "required": ["query"],
        },
        "output_contract": {
            "type": "object",
            "required": ["answer"],
        },
        "risk_level": 2,
        "capability_dependencies": ("research.read",),
        "tool_dependencies": ("browser.read",),
        "verifier_ref": "verifier://research-summary/v1",
        "test_vectors": (
            {"input": {"query": "hello"}, "expected_status": "PASS"},
        ),
        "budget_profile": {
            "max_tool_calls": 4,
            "max_wall_time_ms": 5_000,
        },
        "rollback_target": "skill://research_summary/known-good",
    }


def test_ne05_markdown_compiles_inventory_sbom_and_unsigned_request() -> None:
    kwargs = {
        **_bundle_kwargs(),
        "source_kind": "markdown",
        "source_ref": "repository://skills/research-summary.md",
        "source_material": "# Research Summary\r\nReturn cited facts.",
    }
    first = compile_skill_bundle(**kwargs)
    second = compile_skill_bundle(**kwargs)

    assert first.to_dict() == second.to_dict()
    assert first.bundle_digest.startswith("sha256:")
    assert [item["path"] for item in first.bundle_inventory] == [
        "sbom.cdx.json",
        "skill-draft.json",
        "source-descriptor.json",
    ]
    assert first.sbom["bomFormat"] == "CycloneDX"
    assert first.signature_request["private_key_included"] is False
    assert first.to_dict()["contains_private_key"] is False
    assert "source_material" not in first.to_dict()


def test_ne05_accepted_trace_requires_accepted_receipt_and_no_private_body() -> None:
    receipt_digest = "c" * 64
    artifact_digest = "d" * 64
    source = {
        "receipt_ref": "task-receipt://accepted/ne05",
        "receipt_digest": receipt_digest,
        "artifact_ref": "task-artifact://accepted/ne05",
        "artifact_digest": artifact_digest,
        "handler_adapter_id": "builtin-skill-alias.v1",
        "verification_ref": "verification://accepted/ne05",
    }
    compiled = compile_skill_bundle(
        **_bundle_kwargs(),
        source_kind="accepted_trace",
        source_ref="accepted-trace://task/ne05",
        source_material=source,
        accepted_receipt={
            "status": "ACCEPTED",
            "receipt_ref": source["receipt_ref"],
            "receipt_digest": receipt_digest,
            "artifact_digest": artifact_digest,
        },
    )
    assert (
        compiled.draft.accepted_receipt_ref
        == "task-receipt://accepted/ne05"
    )
    assert compiled.draft.accepted_artifact_digest == artifact_digest

    with pytest.raises(
        SkillCompilerError,
        match="non-reference fields",
    ):
        compile_skill_bundle(
            **_bundle_kwargs(),
            source_kind="accepted_trace",
            source_ref="accepted-trace://task/ne05-private",
            source_material={**source, "raw_thought": "do not compile"},
            accepted_receipt={
                "status": "ACCEPTED",
                "receipt_ref": source["receipt_ref"],
                "receipt_digest": receipt_digest,
                "artifact_digest": artifact_digest,
            },
        )
