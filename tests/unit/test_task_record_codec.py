from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from host_brain.persistence import task_record_codec, task_repository
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)


def test_task_repository_uses_canonical_pure_record_codecs() -> None:
    codec_names = (
        "_acceptance_effect_fence",
        "_acceptance_effect_record",
        "_acceptance_effect_text",
        "_add_amounts",
        "_amount_from_json",
        "_budget_usage_record",
        "_dumps",
        "_graph_cancellation_decision_record",
        "_graph_cancellation_intent_record",
        "_graph_record",
        "_legacy_receipt_digest",
        "_loads_list",
        "_loads_object",
        "_node_record",
        "_outbox_record",
        "_parallel_settlement_decision_record",
        "_parallel_success_decision_record",
        "_validate_attempt_transition",
        "_validated_budget_record",
        "_validated_node_payload",
    )
    for name in codec_names:
        assert getattr(task_repository, name) is getattr(task_record_codec, name)

    assert task_record_codec._dumps({"z": 1, "a": 2}) == '{"a":2,"z":1}'

    with pytest.raises(TaskStoreError) as duplicate_error:
        task_record_codec._loads_object(
            '{"duplicate":1,"duplicate":2}',
            field_name="payload",
        )
    assert duplicate_error.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID

    with pytest.raises(TaskStoreError) as non_finite_error:
        task_record_codec._dumps({"value": float("nan")})
    assert non_finite_error.value.reason_code == TASK_STORE_INVALID


def test_task_record_codec_cannot_acquire_persistence_or_clock_authority() -> None:
    source = Path(task_record_codec.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_calls = {
        "connect",
        "cursor",
        "execute",
        "executemany",
        "executescript",
        "commit",
        "rollback",
        "now_ms",
        "time",
        "utcnow",
        "now",
        "_connect",
        "_transaction",
    }
    observed_calls: set[str] = set()
    forbidden_imports: set[str] = set()
    sql_literals: list[str] = []
    sql_pattern = re.compile(
        r"\b(?:SELECT|INSERT|UPDATE|DELETE|BEGIN|COMMIT|ROLLBACK|CREATE|ALTER|DROP)\b"
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                observed_calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                observed_calls.add(node.func.attr)
        elif isinstance(node, ast.Import):
            forbidden_imports.update(
                alias.name for alias in node.names if alias.name in {"time", "datetime"}
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module in {"time", "datetime"}:
                forbidden_imports.add(str(node.module))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and sql_pattern.search(node.value)
        ):
            sql_literals.append(node.value[:120])

    assert forbidden_calls.isdisjoint(observed_calls)
    assert forbidden_imports == set()
    assert sql_literals == []
