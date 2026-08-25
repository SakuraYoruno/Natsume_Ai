"""Unified 23-organ contract matrix for host-side digital-life validation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.organ_ops.organ_registry import build_default_organ_registry
from tools.organ_ops.organ_wiring_audit import build_organ_health_profiles
from host_brain.persona.organ_influence_contract import (
    MAIN_CHAIN_EFFECTS,
    organ_influence_contract_table,
    organ_main_chain_integration_table,
)
from host_brain.persona.surface_organ_influence import SURFACE_ORGAN_IDS, organ_surface_rule_table


CONTRACT_SCOPE_FLAGS: tuple[str, ...] = (
    "may_affect_surface_text",
    "may_write_memory",
    "may_write_emotion",
    "may_write_relationship",
    "may_write_inner_world",
    "may_control_delivery",
    "may_apply_safety",
)


@dataclass(frozen=True, slots=True)
class OrganContractMatrixRow:
    organ_id: str
    module: str
    file_path: str
    input_keys: tuple[str, ...]
    event_inputs: tuple[str, ...]
    compressed_output_fields: tuple[str, ...]
    surface_slots: tuple[str, ...]
    main_chain_effects: tuple[str, ...]
    state_write_targets: tuple[str, ...]
    event_outputs: tuple[str, ...]
    may_affect_surface_text: bool
    may_write_memory: bool
    may_write_emotion: bool
    may_write_relationship: bool
    may_write_inner_world: bool
    may_control_delivery: bool
    may_apply_safety: bool
    prompt_visibility: str
    raw_prompt_visibility: str
    role_summary: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_organ_contract_matrix() -> tuple[dict[str, Any], ...]:
    """Build the read-only contract matrix for the current 23 host organs."""

    registry_rows = {str(row["organ_id"]): dict(row) for row in build_default_organ_registry().list_organs()}
    health_rows = {str(row["organ_id"]): dict(row) for row in build_organ_health_profiles()}
    contract_rows = {str(row["organ_id"]): dict(row) for row in organ_influence_contract_table()}
    integration_rows = {str(row["organ_id"]): dict(row) for row in organ_main_chain_integration_table()}
    surface_rows = {str(row["organ_id"]): dict(row) for row in organ_surface_rule_table()}

    rows: list[OrganContractMatrixRow] = []
    for organ_id in SURFACE_ORGAN_IDS:
        registry = registry_rows.get(organ_id, {})
        health = health_rows.get(organ_id, {})
        contract = contract_rows.get(organ_id, {})
        integration = integration_rows.get(organ_id, {})
        surface = surface_rows.get(organ_id, {})
        file_path = str(registry.get("file_path") or "")
        event_io = _detect_event_io(Path(file_path))
        effects = _clean_tuple(integration.get("main_chain_effects"))
        rows.append(
            OrganContractMatrixRow(
                organ_id=organ_id,
                module=str(registry.get("module") or ""),
                file_path=file_path,
                input_keys=_clean_tuple(integration.get("context_keys")),
                event_inputs=tuple(sorted(event_io["inputs"])),
                compressed_output_fields=_clean_tuple(contract.get("allowed_fields")),
                surface_slots=_clean_tuple(surface.get("allowed_slots")),
                main_chain_effects=effects,
                state_write_targets=_state_write_targets(health),
                event_outputs=tuple(sorted(event_io["outputs"])),
                may_affect_surface_text="surface_text" in effects,
                may_write_memory="memory" in effects,
                may_write_emotion="emotion" in effects,
                may_write_relationship="relationship" in effects,
                may_write_inner_world="inner_world" in effects,
                may_control_delivery="delivery" in effects,
                may_apply_safety="safety" in effects,
                prompt_visibility=str(integration.get("prompt_visibility") or "contract_only"),
                raw_prompt_visibility="forbidden",
                role_summary=str(integration.get("role_summary") or ""),
            )
        )
    return tuple(row.as_dict() for row in rows)


def validate_organ_contract_matrix(rows: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    matrix_rows = [dict(row) for row in (rows or build_organ_contract_matrix())]
    row_ids = {str(row.get("organ_id") or "") for row in matrix_rows}
    registry_ids = {str(row.get("organ_id") or "") for row in build_default_organ_registry().list_organs()}
    expected_ids = set(SURFACE_ORGAN_IDS)
    checks = [
        _check(
            "matrix_covers_23_surface_organs",
            len(matrix_rows) == 23 and row_ids == expected_ids,
            {"row_count": len(matrix_rows), "missing": sorted(expected_ids - row_ids), "extra": sorted(row_ids - expected_ids)},
        ),
        _check(
            "matrix_matches_runtime_registry",
            row_ids == registry_ids == expected_ids,
            {"registry_count": len(registry_ids), "missing": sorted(expected_ids - registry_ids), "extra": sorted(registry_ids - expected_ids)},
        ),
        _check(
            "each_organ_declares_inputs_outputs_and_role",
            all(
                list(row.get("input_keys") or [])
                and list(row.get("compressed_output_fields") or [])
                and list(row.get("surface_slots") or [])
                and list(row.get("main_chain_effects") or [])
                and str(row.get("role_summary") or "").strip()
                for row in matrix_rows
            ),
            {
                "failed": [
                    row.get("organ_id")
                    for row in matrix_rows
                    if not (
                        list(row.get("input_keys") or [])
                        and list(row.get("compressed_output_fields") or [])
                        and list(row.get("surface_slots") or [])
                        and list(row.get("main_chain_effects") or [])
                        and str(row.get("role_summary") or "").strip()
                    )
                ]
            },
        ),
        _check(
            "compressed_outputs_are_known_contract_fields",
            all(set(row.get("compressed_output_fields") or []) <= _contract_fields() for row in matrix_rows),
            {
                "failed": [
                    row.get("organ_id")
                    for row in matrix_rows
                    if not set(row.get("compressed_output_fields") or []) <= _contract_fields()
                ]
            },
        ),
        _check(
            "main_chain_effects_are_known",
            all(set(row.get("main_chain_effects") or []) <= set(MAIN_CHAIN_EFFECTS) for row in matrix_rows),
            {
                "failed": [
                    row.get("organ_id")
                    for row in matrix_rows
                    if not set(row.get("main_chain_effects") or []) <= set(MAIN_CHAIN_EFFECTS)
                ]
            },
        ),
        _check(
            "raw_organ_text_is_never_prompt_visible",
            all(
                str(row.get("prompt_visibility") or "") == "contract_only"
                and str(row.get("raw_prompt_visibility") or "") == "forbidden"
                for row in matrix_rows
            ),
            {
                "failed": [
                    row.get("organ_id")
                    for row in matrix_rows
                    if str(row.get("prompt_visibility") or "") != "contract_only"
                    or str(row.get("raw_prompt_visibility") or "") != "forbidden"
                ]
            },
        ),
        _check(
            "scope_flags_match_declared_effects",
            all(_scope_flags_match_effects(row) for row in matrix_rows),
            {"failed": [row.get("organ_id") for row in matrix_rows if not _scope_flags_match_effects(row)]},
        ),
    ]
    return checks


def _contract_fields() -> set[str]:
    fields: set[str] = set()
    for row in organ_influence_contract_table():
        fields.update(str(item) for item in list(row.get("allowed_fields") or []))
    return fields


def _scope_flags_match_effects(row: Mapping[str, Any]) -> bool:
    effects = set(str(item) for item in list(row.get("main_chain_effects") or []))
    expected = {
        "may_affect_surface_text": "surface_text" in effects,
        "may_write_memory": "memory" in effects,
        "may_write_emotion": "emotion" in effects,
        "may_write_relationship": "relationship" in effects,
        "may_write_inner_world": "inner_world" in effects,
        "may_control_delivery": "delivery" in effects,
        "may_apply_safety": "safety" in effects,
    }
    return all(bool(row.get(flag)) == expected[flag] for flag in CONTRACT_SCOPE_FLAGS)


def _state_write_targets(health: Mapping[str, Any]) -> tuple[str, ...]:
    keys = _clean_tuple(health.get("state_keys"))
    outputs = set(str(item) for item in list(health.get("outputs") or []))
    if keys:
        return keys
    if "state_registry" in outputs:
        return ("state_registry",)
    return ()


def _detect_event_io(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {"inputs": set(), "outputs": set()}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "inputs": _detect_event_inputs(text),
        "outputs": _detect_event_outputs(text),
    }


def _detect_event_inputs(text: str) -> set[str]:
    events: set[str] = set()
    for pattern in (r"\.subscribe\(\s*[\"']([^\"']+)[\"']", r"subscribe\(\s*[\"']([^\"']+)[\"']"):
        events.update(_clean_event_name(item) for item in re.findall(pattern, text))
    return {item for item in events if item}


def _detect_event_outputs(text: str) -> set[str]:
    events: set[str] = set()
    patterns = (
        r"emit_from_thread\(\s*[\"']([^\"']+)[\"']",
        r"emit_tribe_event\(\s*[\"']([^\"']+)[\"']",
        r"emit_tribe_event\([^)]*event_type\s*=\s*[\"']([^\"']+)[\"']",
        r"\.publish\([^)]*event_type\s*=\s*[\"']([^\"']+)[\"']",
    )
    for pattern in patterns:
        events.update(_clean_event_name(item) for item in re.findall(pattern, text, flags=re.DOTALL))
    return {item for item in events if item}


def _clean_event_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or " " in text or len(text) > 80:
        return ""
    if text.lower() in {"ok", "error", "true", "false", "none"}:
        return ""
    return text


def _clean_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        text = str(value or "").strip()
        return (text,) if text else ()
    if isinstance(value, Sequence):
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return tuple(result)
    text = str(value or "").strip()
    return (text,) if text else ()


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


__all__ = [
    "CONTRACT_SCOPE_FLAGS",
    "OrganContractMatrixRow",
    "build_organ_contract_matrix",
    "validate_organ_contract_matrix",
]
