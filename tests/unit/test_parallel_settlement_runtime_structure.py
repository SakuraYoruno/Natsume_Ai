from __future__ import annotations

import ast
import hashlib
import inspect
import symtable

import pytest

from host_brain.task_runtime import coordinator
from host_brain.task_runtime import coordinator_records
from host_brain.task_runtime import dag_scheduling_runtime
from host_brain.task_runtime import parallel_settlement_projection
from host_brain.task_runtime import parallel_settlement_runtime
from host_brain.task_runtime.parallel_settlement_projection import (
    ParallelSettlementProjection,
)
from host_brain.task_runtime.parallel_settlement_runtime import (
    ParallelSettlementRuntime,
)
from host_brain.task_runtime.errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)


def _tree(module: object) -> ast.Module:
    return ast.parse(inspect.getsource(module))


def _class(module: object, name: str) -> ast.ClassDef:
    return next(
        node
        for node in _tree(module).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(owner: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _self_store_calls(module: object) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(_tree(module))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_store"
    ]


def _runtime_imports(module: object) -> set[str]:
    imported: set[str] = set()
    for node in _tree(module).body:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    return imported


def test_parallel_settlement_services_are_narrow_real_boundaries() -> None:
    runtime_source = inspect.getsource(parallel_settlement_runtime)
    projection_source = inspect.getsource(parallel_settlement_projection)

    assert len(runtime_source.splitlines()) < 800
    assert len(projection_source.splitlines()) < 800
    assert tuple(inspect.signature(ParallelSettlementRuntime).parameters) == (
        "task_store",
        "budget_runtime",
        "runtime_outbox",
    )
    assert tuple(inspect.signature(ParallelSettlementProjection).parameters) == (
        "task_store",
    )
    assert set(ParallelSettlementRuntime.__slots__) == {
        "_store",
        "_budgets",
        "_runtime_outbox",
        "_projection",
    }
    assert ParallelSettlementProjection.__slots__ == ("_store",)
    assert "callback" not in runtime_source.lower()
    assert "callback" not in projection_source.lower()
    assert not any(
        name.endswith("coordinator") or name.endswith("coordinator.py")
        for module in (parallel_settlement_runtime, parallel_settlement_projection)
        for name in _runtime_imports(module)
    )

    for module in (parallel_settlement_runtime, parallel_settlement_projection):
        tree = _tree(module)
        assert not any(
            isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id
                    in {
                        "GovernedTaskCoordinator",
                        "RuntimeOutbox",
                        "DurableBudgetRuntime",
                        "Lock",
                        "RLock",
                        "create_task",
                    }
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"Lock", "RLock", "create_task"}
                )
            )
            for node in ast.walk(tree)
        )


def test_projection_is_store_only_and_runtime_store_calls_are_frozen() -> None:
    projection_calls = {
        call.func.attr for call in _self_store_calls(parallel_settlement_projection)
    }
    runtime_calls = {
        call.func.attr for call in _self_store_calls(parallel_settlement_runtime)
    }
    assert projection_calls == {
        "read_scheduler_snapshot",
        "get_node",
        "get_attempt",
    }
    assert runtime_calls == {
        "get_graph",
        "get_node",
        "read_scheduler_snapshot",
        "get_operation_reservation",
        "settle_parallel_graph_budget",
    }
    assert parallel_settlement_projection._ParallelNodeOutcome is (
        coordinator_records._ParallelNodeOutcome
    )
    assert parallel_settlement_runtime._ParallelNodeOutcome is (
        coordinator_records._ParallelNodeOutcome
    )


def test_parallel_settlement_store_call_matrix_is_frozen() -> None:
    specs = (
        (
            parallel_settlement_projection,
            "ParallelSettlementProjection",
            (
                "assert_parallel_reentry_idle",
                "durable_budget_blocked_outcome",
                "validate_parallel_outcome_snapshot",
            ),
        ),
        (
            parallel_settlement_runtime,
            "ParallelSettlementRuntime",
            (
                "_parallel_settlement_conflict_is_in_progress",
                "_settle_parallel_terminal",
            ),
        ),
    )
    sequence: list[str] = []
    rows: list[str] = []
    for module, class_name, method_names in specs:
        owner = _class(module, class_name)
        for method_name in method_names:
            method = _method(owner, method_name)
            calls = [
                node
                for node in ast.walk(method)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"
                and node.func.value.attr == "_store"
            ]
            for call in sorted(calls, key=lambda node: (node.lineno, node.col_offset)):
                prefix = f"{class_name}.{method_name}"
                sequence.append(f"{prefix}:{call.func.attr}")
                rows.append(
                    prefix
                    + ":"
                    + ast.dump(
                        call,
                        annotate_fields=True,
                        include_attributes=False,
                    )
                )

    assert sequence == [
        "ParallelSettlementProjection.assert_parallel_reentry_idle:read_scheduler_snapshot",
        "ParallelSettlementProjection.durable_budget_blocked_outcome:read_scheduler_snapshot",
        "ParallelSettlementProjection.validate_parallel_outcome_snapshot:read_scheduler_snapshot",
        "ParallelSettlementProjection.validate_parallel_outcome_snapshot:get_node",
        "ParallelSettlementProjection.validate_parallel_outcome_snapshot:get_attempt",
        "ParallelSettlementRuntime._parallel_settlement_conflict_is_in_progress:get_node",
        "ParallelSettlementRuntime._settle_parallel_terminal:get_graph",
        "ParallelSettlementRuntime._settle_parallel_terminal:get_node",
        "ParallelSettlementRuntime._settle_parallel_terminal:read_scheduler_snapshot",
        "ParallelSettlementRuntime._settle_parallel_terminal:settle_parallel_graph_budget",
        "ParallelSettlementRuntime._settle_parallel_terminal:settle_parallel_graph_budget",
    ]
    assert hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest() == (
        "430fab37b1bdbb83dec263d64363369ae1450b42c98b02a186acafd0c87e636e"
    )


def test_coordinator_wires_shared_settlement_dependencies_and_direct_calls() -> None:
    owner = _class(coordinator, "GovernedTaskCoordinator")
    initializer = _method(owner, "__init__")
    assignments = [
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_parallel_settlement"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    construction = assignments[0].value
    assert isinstance(construction, ast.Call)
    assert isinstance(construction.func, ast.Name)
    assert construction.func.id == "ParallelSettlementRuntime"
    keywords = {item.arg: item.value for item in construction.keywords}
    assert set(keywords) == {"task_store", "budget_runtime", "runtime_outbox"}
    assert isinstance(keywords["task_store"], ast.Name)
    assert keywords["task_store"].id == "task_store"
    for keyword, field in (
        ("budget_runtime", "_budgets"),
        ("runtime_outbox", "_runtime_outbox"),
    ):
        value = keywords[keyword]
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "self"
        assert value.attr == field

    assert assignments[0].lineno > next(
        node.lineno
        for node in ast.walk(initializer)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and target.attr == "_budgets"
            for target in node.targets
        )
    )
    removed = {
        "_assert_parallel_reentry_idle",
        "_parallel_settlement_conflict_is_in_progress",
        "_resolve_parallel_outcomes",
        "_durable_budget_blocked_outcome",
        "_validate_parallel_outcome_snapshot",
        "_settle_parallel_terminal",
        "_parallel_event_with_budget",
    }
    assert not removed.intersection(
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    dag_owner = _class(dag_scheduling_runtime, "DurableDagSchedulingRuntime")
    execute = _method(dag_owner, "execute_stream")
    service_calls = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_parallel_settlement"
    ]
    assert [node.func.attr for node in service_calls].count(
        "assert_parallel_reentry_idle"
    ) == 1
    assert [node.func.attr for node in service_calls].count(
        "resolve_parallel_outcomes"
    ) == 3
    assert len(service_calls) == 4

    runtime_owner = _class(
        parallel_settlement_runtime,
        "ParallelSettlementRuntime",
    )
    runtime_initializer = _method(runtime_owner, "__init__")
    projection_calls = [
        node
        for node in ast.walk(runtime_initializer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ParallelSettlementProjection"
    ]
    assert len(projection_calls) == 1
    projection_keywords = {
        item.arg: item.value for item in projection_calls[0].keywords
    }
    assert set(projection_keywords) == {"task_store"}
    assert isinstance(projection_keywords["task_store"], ast.Name)
    assert projection_keywords["task_store"].id == "task_store"


def test_settlement_exact_replay_and_outbox_submatrix_remain_explicit() -> None:
    owner = _class(parallel_settlement_runtime, "ParallelSettlementRuntime")
    settle = _method(owner, "_settle_parallel_terminal")
    store_settles = [
        node
        for node in ast.walk(settle)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "settle_parallel_graph_budget"
    ]
    assert len(store_settles) == 2
    for call in store_settles:
        assert any(
            keyword.arg is None
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "arguments"
            for keyword in call.keywords
        )

    outer_try = next(
        node
        for node in settle.body
        if isinstance(node, ast.Try)
        and any(id(call) in {id(item) for item in ast.walk(node)} for call in store_settles)
    )
    assert len(outer_try.handlers) == 1
    first_handler = outer_try.handlers[0]
    assert isinstance(first_handler.type, ast.Name)
    assert first_handler.type.id == "Exception"
    assert sum(id(call) in {id(item) for item in ast.walk(outer_try)} for call in store_settles) == 2
    assert sum(id(call) in {id(item) for item in ast.walk(ast.Module(body=outer_try.body, type_ignores=[]))} for call in store_settles) == 1
    nested_try = next(node for node in first_handler.body if isinstance(node, ast.Try))
    assert sum(id(call) in {id(item) for item in ast.walk(nested_try)} for call in store_settles) == 1
    assert len(nested_try.handlers) == 1
    replay_handler = nested_try.handlers[0]
    assert isinstance(replay_handler.type, ast.Name)
    assert replay_handler.type.id == "Exception"
    assert next(node for node in first_handler.body if isinstance(node, ast.If)).lineno < nested_try.lineno
    assert next(node for node in replay_handler.body if isinstance(node, ast.If)).lineno < next(
        node.lineno for node in replay_handler.body if isinstance(node, ast.Raise)
    )

    outbox_calls = [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_runtime_outbox"
    ]
    assert [node.func.attr for node in outbox_calls].count("build") == 4
    assert [node.func.attr for node in outbox_calls].count("publish_pending") == 1
    assert not any(
        keyword.arg == "best_effort"
        for call in outbox_calls
        for keyword in call.keywords
    )
    assert max(call.lineno for call in store_settles) < next(
        call.lineno for call in outbox_calls if call.func.attr == "publish_pending"
    )
    builds = sorted(
        (call for call in outbox_calls if call.func.attr == "build"),
        key=lambda call: (call.lineno, call.col_offset),
    )
    normalize = next(
        node
        for node in ast.walk(settle)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "normalize_parallel_settlement_decision"
    )
    publish = next(
        call for call in outbox_calls if call.func.attr == "publish_pending"
    )
    settled_mapping = next(
        node
        for node in ast.walk(settle)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_mapping"
        and any(
            keyword.arg == "field_name"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "parallel Graph/Budget settlement"
            for keyword in node.keywords
        )
    )
    assert builds[0].lineno < builds[1].lineno < normalize.lineno
    assert normalize.lineno < builds[2].lineno < builds[3].lineno
    assert builds[3].lineno < store_settles[0].lineno < store_settles[1].lineno
    assert store_settles[1].lineno < settled_mapping.lineno < publish.lineno
    assert not any(
        isinstance(node, ast.ExceptHandler)
        and (
            node.type is None
            or isinstance(node.type, ast.Name)
            and node.type.id in {"BaseException", "CancelledError"}
        )
        for node in ast.walk(owner)
    )


def test_parallel_outcome_reason_mapping_is_canonical() -> None:
    reason = parallel_settlement_runtime._canonical_parallel_outcome_reason_code
    assert reason("ACCEPTED") is None
    assert reason("BUDGET_BLOCKED") == "GOVERNED_BUDGET_EXCEEDED"
    assert reason("FAILED") == "GOVERNED_PARALLEL_NODE_FAILED"
    assert reason("VERIFICATION_FAILED") == "GOVERNED_PARALLEL_VERIFICATION_FAILED"
    assert reason("WAITING_APPROVAL") == "GOVERNED_REVIEW_REQUIRED"
    assert reason("WAITING_ACCEPTANCE_REVIEW") == "GOVERNED_REVIEW_REQUIRED"
    assert reason("RETRY_PENDING") == "GOVERNED_EXECUTION_IN_PROGRESS"
    assert reason("CANCELLED") == "GOVERNED_PARALLEL_NODE_CANCELLED"
    with pytest.raises(GovernedRuntimeError) as exc_info:
        reason("UNKNOWN")
    assert exc_info.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT
    definitions = [
        (module, node)
        for module in (
            coordinator,
            parallel_settlement_projection,
            parallel_settlement_runtime,
        )
        for node in _tree(module).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_canonical_parallel_outcome_reason_code"
    ]
    assert len(definitions) == 1
    assert definitions[0][0] is parallel_settlement_runtime


def test_execute_stream_does_not_shadow_the_terminal_event_renderer() -> None:
    source = inspect.getsource(dag_scheduling_runtime)
    module_table = symtable.symtable(source, "dag_scheduling_runtime.py", "exec")
    class_table = next(
        child
        for child in module_table.get_children()
        if child.get_name() == "DurableDagSchedulingRuntime"
    )
    execute_table = next(
        child
        for child in class_table.get_children()
        if child.get_name() == "execute_stream"
    )
    assert not execute_table.lookup("terminal_event").is_local()
