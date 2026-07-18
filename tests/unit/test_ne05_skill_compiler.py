from __future__ import annotations

import pytest

from host_brain.skills.skill_compiler import SkillCompilerError, compile_skill_draft


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
