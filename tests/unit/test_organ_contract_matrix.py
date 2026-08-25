from __future__ import annotations

from tools.organ_ops.organ_contract_matrix import (
    CONTRACT_SCOPE_FLAGS,
    build_organ_contract_matrix,
    validate_organ_contract_matrix,
)
from host_brain.persona.surface_organ_influence import SURFACE_ORGAN_IDS


def test_organ_contract_matrix_covers_23_organs() -> None:
    rows = list(build_organ_contract_matrix())
    organ_ids = {row["organ_id"] for row in rows}

    assert len(rows) == 23
    assert organ_ids == set(SURFACE_ORGAN_IDS)
    assert all(row["input_keys"] for row in rows)
    assert all(row["compressed_output_fields"] for row in rows)
    assert all(row["surface_slots"] for row in rows)
    assert all(row["main_chain_effects"] for row in rows)
    assert all(row["prompt_visibility"] == "contract_only" for row in rows)
    assert all(row["raw_prompt_visibility"] == "forbidden" for row in rows)
    assert all("surface_text" in row["main_chain_effects"] for row in rows)


def test_organ_contract_matrix_scope_flags_match_effects() -> None:
    rows = list(build_organ_contract_matrix())

    assert any(row["may_write_memory"] for row in rows)
    assert any(row["may_write_emotion"] for row in rows)
    assert any(row["may_write_relationship"] for row in rows)
    for row in rows:
        effects = set(row["main_chain_effects"])
        assert row["may_affect_surface_text"] is ("surface_text" in effects)
        assert row["may_write_memory"] is ("memory" in effects)
        assert row["may_write_emotion"] is ("emotion" in effects)
        assert row["may_write_relationship"] is ("relationship" in effects)
        assert row["may_write_inner_world"] is ("inner_world" in effects)
        assert all(flag in row for flag in CONTRACT_SCOPE_FLAGS)


def test_validate_organ_contract_matrix_passes_current_contract() -> None:
    checks = validate_organ_contract_matrix()

    assert checks
    assert all(check["passed"] for check in checks)
