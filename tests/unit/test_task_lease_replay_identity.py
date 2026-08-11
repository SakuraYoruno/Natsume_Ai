from __future__ import annotations

from copy import deepcopy
import ast
import inspect

import pytest

from host_brain.persistence.task_lease_replay_identity import (
    build_lease_acquisition_identity,
    lease_acquisition_replay_binding_is_valid,
)


_REQUEST = {
    "attempt_id": "attempt-1",
    "owner_id": "worker-a",
    "ttl_ms": 20,
    "expected_attempt_version": 0,
    "absent_operation_reservation_id": "reservation-a",
    "existing_operation_reservation_id": None,
}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("attempt_id", "attempt-2"),
        ("owner_id", "worker-b"),
        ("ttl_ms", 21),
        ("expected_attempt_version", 1),
        ("absent_operation_reservation_id", "reservation-b"),
        ("existing_operation_reservation_id", "reservation-committed"),
    ),
)
def test_lease_acquisition_identity_changes_for_every_request_field(
    field: str,
    value: object,
) -> None:
    expected = build_lease_acquisition_identity(**_REQUEST)
    changed = dict(_REQUEST)
    changed[field] = value
    assert build_lease_acquisition_identity(**changed) != expected
    assert build_lease_acquisition_identity(**_REQUEST) == expected


def _valid_rows() -> tuple[dict, dict, dict, tuple[str, str]]:
    lease_id, heartbeat_id = build_lease_acquisition_identity(**_REQUEST)
    attempt = {
        "status": "LEASED",
        "owner_id": "worker-a",
        "acquired_ts_ms": 100,
    }
    lease = {
        "lease_id": lease_id,
        "attempt_id": "attempt-1",
        "owner_id": "worker-a",
        "fencing_token": 2,
        "acquired_ts_ms": 100,
        "heartbeat_ts_ms": 100,
        "expires_ts_ms": 120,
        "released_ts_ms": None,
        "status": "ACTIVE",
        "status_version": 0,
    }
    heartbeat = {
        "heartbeat_id": heartbeat_id,
        "lease_id": lease_id,
        "attempt_id": "attempt-1",
        "owner_id": "worker-a",
        "fencing_token": 2,
        "observed_ts_ms": 100,
        "expires_ts_ms": 120,
    }
    return attempt, lease, heartbeat, (lease_id, heartbeat_id)


def test_lease_acquisition_replay_binding_accepts_only_exact_initial_rows() -> None:
    attempt, lease, heartbeat, identities = _valid_rows()
    assert lease_acquisition_replay_binding_is_valid(
        attempt=attempt,
        lease=lease,
        heartbeat=heartbeat,
        attempt_id="attempt-1",
        owner_id="worker-a",
        expected_lease_id=identities[0],
        expected_heartbeat_id=identities[1],
        ttl_ms=20,
        now_ts_ms=101,
        previous_fencing_token=1,
    )

    for target, field, value in (
        ("attempt", "status", "RUNNING"),
        ("lease", "fencing_token", 3),
        ("lease", "expires_ts_ms", 121),
        ("lease", "status_version", 1),
        ("heartbeat", "owner_id", "worker-b"),
        ("heartbeat", "expires_ts_ms", 121),
    ):
        drift_attempt = deepcopy(attempt)
        drift_lease = deepcopy(lease)
        drift_heartbeat = deepcopy(heartbeat)
        {"attempt": drift_attempt, "lease": drift_lease, "heartbeat": drift_heartbeat}[
            target
        ][field] = value
        assert not lease_acquisition_replay_binding_is_valid(
            attempt=drift_attempt,
            lease=drift_lease,
            heartbeat=drift_heartbeat,
            attempt_id="attempt-1",
            owner_id="worker-a",
            expected_lease_id=identities[0],
            expected_heartbeat_id=identities[1],
            ttl_ms=20,
            now_ts_ms=101,
            previous_fencing_token=1,
        )


def test_lease_replay_identity_module_remains_pure() -> None:
    module = inspect.getmodule(build_lease_acquisition_identity)
    assert module is not None
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_roots = {
        node.names[0].name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
    }
    imported_from = {
        str(node.module or "").split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }
    forbidden_modules = {
        "asyncio",
        "pathlib",
        "random",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "time",
        "urllib",
        "uuid",
    }
    assert not (imported_roots | imported_from) & forbidden_modules
    assert "host_brain.persistence.task_repository" not in source
    forbidden_calls = {
        "connect",
        "open",
        "request",
        "sleep",
        "time",
        "urlopen",
        "uuid4",
    }
    assert not any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in forbidden_calls)
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
            )
        )
        for node in ast.walk(tree)
    )
