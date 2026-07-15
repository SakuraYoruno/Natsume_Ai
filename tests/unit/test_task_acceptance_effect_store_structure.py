from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_record_codec, task_repository
from host_brain.persistence.task_acceptance_effect_delivery import (
    TaskAcceptanceEffectDeliveryMixin,
)
from host_brain.persistence.task_acceptance_effect_staging import (
    TaskAcceptanceEffectStagingMixin,
)
from host_brain.persistence.task_parallel_success_store import (
    TaskParallelSuccessStoreMixin,
)


STAGING_METHODS = (
    "stage_acceptance_effect_intent",
    "transition_node_and_stage_acceptance_effects",
    "transition_graph_and_stage_acceptance_effects",
    "_normalize_acceptance_effect_intent_specs",
    "_normalize_acceptance_effect_intent_spec",
    "_stage_acceptance_effect_intent_in_transaction",
)
DELIVERY_METHODS = (
    "assert_acceptance_effect_intent_ready",
    "get_acceptance_effect_intent",
    "list_acceptance_effect_intents",
    "claim_acceptance_effect_intent",
    "commit_acceptance_effect_intent",
    "fail_acceptance_effect_intent",
    "reschedule_acceptance_effect_intent",
    "_require_acceptance_effect_intent",
    "_assert_acceptance_effect_claim",
)


def test_acceptance_effect_mixins_preserve_api_and_borrow_store_transactions() -> None:
    assert task_repository.DurableTaskStore.__bases__ == (
        TaskAcceptanceEffectStagingMixin,
        TaskAcceptanceEffectDeliveryMixin,
        TaskParallelSuccessStoreMixin,
    )
    for name in STAGING_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskAcceptanceEffectStagingMixin,
            name,
        )
    for name in DELIVERY_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskAcceptanceEffectDeliveryMixin,
            name,
        )
    assert (
        task_repository.build_acceptance_effect_payload_digest
        is task_record_codec.build_acceptance_effect_payload_digest
    )

    for module in (
        inspect.getmodule(TaskAcceptanceEffectStagingMixin),
        inspect.getmodule(TaskAcceptanceEffectDeliveryMixin),
    ):
        assert module is not None
        tree = ast.parse(inspect.getsource(module))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "host_brain.persistence.task_repository" not in imported_modules

        mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        defined_methods = {
            node.name for node in mixin.body if isinstance(node, ast.FunctionDef)
        }
        assert not {"__init__", "_connect", "_transaction"} & defined_methods

        forbidden_attributes = {
            "connect",
            "commit",
            "rollback",
            "executemany",
            "executescript",
        }
        assert not {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"_connect", "_transaction"}:
                assert isinstance(node.func.value, ast.Name)
                assert node.func.value.id == "self"

        sql_literals = (
            str(node.value).upper()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        assert not any(
            sql.strip().startswith("BEGIN ")
            or sql.strip() in {"COMMIT", "ROLLBACK"}
            for sql in sql_literals
        )
