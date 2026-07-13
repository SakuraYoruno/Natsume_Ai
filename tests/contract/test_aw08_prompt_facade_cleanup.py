from __future__ import annotations

import ast
from pathlib import Path
import warnings

import pytest

from host_brain.model_runtime.prompt_specs import get_prompt_spec, list_prompt_specs
from host_brain.model_runtime.prompt_catalog import build_production_prompt_registry
from host_brain.model_runtime.prompt_registry import (
    PromptRegistry,
    PromptSchemaError,
    UnknownPromptError,
)


ROOT = Path(__file__).parents[2]
PRODUCTION_ROOTS = (ROOT / "host_brain", ROOT / "natsume_contracts")


def _production_python_sources() -> list[Path]:
    return [path for root in PRODUCTION_ROOTS for path in root.rglob("*.py")]


def test_old_prompt_template_facade_and_definition_table_are_deleted() -> None:
    assert not (ROOT / "host_brain/app/prompt_template_store.py").exists()
    assert not (ROOT / "host_brain/app/prompt_template_defs.py").exists()

    forbidden = "prompt_" + "template_store"
    offenders = [
        str(path.relative_to(ROOT))
        for path in _production_python_sources()
        if path.name != "compatibility_registry.py"
        and forbidden in path.read_text(encoding="utf-8-sig")
    ]
    assert offenders == []


def test_every_literal_prompt_compiler_reference_has_a_registered_contract() -> None:
    call_names = {
        "resolve_prompt_contract_json",
        "resolve_prompt_contract_text",
        "_load_template_mapping",
        "_load_template_list",
        "_load_template_text",
        "_resolve_localized_template_payload",
        "_resolve_template_payload_with_mirror",
    }
    referenced: set[str] = set()
    for path in _production_python_sources():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name in call_names:
                referenced.add(first.value)

    missing: list[str] = []
    for prompt_id in sorted(referenced):
        try:
            get_prompt_spec(prompt_id)
        except KeyError:
            missing.append(prompt_id)
    assert missing == []


def test_registry_owns_immutable_compilation_sources_and_fails_closed() -> None:
    registry = build_production_prompt_registry()

    assert len(registry.list_definitions()) == len(list_prompt_specs())
    source = registry.compilation_source("react_loop.prompt_templates")
    assert isinstance(source, dict)
    source["thought_header"] = "mutated"
    assert registry.compilation_source("react_loop.prompt_templates").get("thought_header") != "mutated"

    with pytest.raises(UnknownPromptError):
        registry.compilation_source("unknown.prompt")

    private = PromptRegistry()
    private.register_static("no.source", "hello")
    with pytest.raises(PromptSchemaError, match="no registered compilation source"):
        private.compilation_source("no.source")


def test_non_model_runtime_texts_read_prompt_specs_without_model_composition() -> None:
    for relative in (
        "host_brain/core/qa.py",
        "host_brain/app/failure_matrix.py",
        "host_brain/persona/drift_detector.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert "host_brain.model_runtime.prompt_specs" in source
        assert "host_brain.model_runtime.prompt_contract" not in source
