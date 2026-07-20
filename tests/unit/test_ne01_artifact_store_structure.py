from __future__ import annotations

import ast
import inspect

from host_brain.agent_runtime import artifact_store, artifact_store_read_model


_READ_MODEL_DELEGATES = {
    "inventory_governed_seals": "inventory_governed_seals",
    "inspect_governed_promotion": "inspect_governed_promotion",
    "inspect_governed_stage_for_authority": "inspect_governed_stage_for_authority",
    "read_projection_recovery": "read_projection_recovery",
    "read_sealed_json": "read_sealed_json",
    "_sealed_digest_for_ref": "sealed_digest_for_ref",
    "plan_governed_abandonment": "plan_governed_abandonment",
    "inspect_governed_tombstone": "inspect_governed_tombstone",
    "inspect_governed_tombstone_ref": "inspect_governed_tombstone_ref",
}


def _class_node(module: object, name: str) -> ast.ClassDef:
    tree = ast.parse(inspect.getsource(module))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _without_docstring(statements: list[ast.stmt]) -> list[ast.stmt]:
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        return statements[1:]
    return statements


def test_artifact_store_keeps_read_aggregation_behind_a_focused_boundary() -> None:
    assert len(inspect.getsource(artifact_store).splitlines()) < 1500
    assert len(inspect.getsource(artifact_store_read_model).splitlines()) < 500

    store_class = _class_node(artifact_store, "AgentArtifactStore")
    methods = {
        node.name: node
        for node in store_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for method_name, delegate_name in _READ_MODEL_DELEGATES.items():
        body = _without_docstring(methods[method_name].body)
        assert len(body) == 1
        assert isinstance(body[0], ast.Return)
        call = body[0].value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute)
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "_read_model"
        assert call.func.attr == delegate_name


def test_artifact_read_model_has_no_mutation_authority() -> None:
    tree = ast.parse(inspect.getsource(artifact_store_read_model))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called_attributes.isdisjoint(
        {
            "prepare_governed_stage",
            "seal_governed_stage",
            "promote_governed_stage",
            "abandon_governed_stage",
            "discard_unsealed_governed_stage",
            "_write_governed_metadata",
            "_remove_governed_attempt_directory",
            "_ensure_governed_directory",
            "_inject_fault",
        }
    )
