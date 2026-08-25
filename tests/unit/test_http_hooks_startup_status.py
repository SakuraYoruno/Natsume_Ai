from __future__ import annotations

import json
import logging

from host_brain.app.http_hooks import _sync_world_canon_index_on_startup
from host_brain.app.http_hooks import _tool_runtime_startup_status
from host_brain.app.http_hooks_outbound import startup_status_is_degraded
from host_brain.memory.canon_world_store import compute_world_canon_hash
from tests.fakes.canon_runtime import compose_canon_test_runtime


def test_startup_status_marks_only_real_degraded_values() -> None:
    assert startup_status_is_degraded("DEGRADED: edge rpc unreachable") is True
    assert startup_status_is_degraded("FAIL: scheduler crashed") is True
    assert startup_status_is_degraded("OK") is False
    assert startup_status_is_degraded("OK (task_id=distillation_engine_periodic, interval=600s)") is False
    assert startup_status_is_degraded("SKIPPED: gateway-only mode") is False
    assert startup_status_is_degraded("NOT_TESTED: remote Edge disabled") is False


def test_tool_runtime_startup_status_rejects_catalog_only_or_zero_executor() -> None:
    status = _tool_runtime_startup_status(
        seeded_count=2,
        readiness={
            "status": "FAIL_CLOSED_NO_GOVERNED_EXECUTOR",
            "catalog_ids": ("a", "b"),
            "registered_ids": ("a", "b"),
            "governed_executable_ids": (),
        },
    )

    assert status == (
        "DEGRADED: FAIL_CLOSED_NO_GOVERNED_EXECUTOR "
        "(seeded=2, catalog=2, registered=2, governed_executors=0)"
    )
    assert startup_status_is_degraded(status) is True


def test_tool_runtime_startup_status_requires_exact_ready_seed() -> None:
    status = _tool_runtime_startup_status(
        seeded_count=2,
        readiness={
            "status": "READY",
            "catalog_ids": ("a", "b"),
            "registered_ids": ("a", "b"),
            "governed_executable_ids": ("a",),
        },
    )

    assert status == "OK (2 tools registered, 1 governed executors)"
    assert startup_status_is_degraded(status) is False


def test_world_canon_sync_on_startup_backfills_world_canon_index(tmp_path) -> None:
    world_root = tmp_path / "world"
    world_root.mkdir()

    payload = {
        "doc_id": "startup-world-doc",
        "version": 1,
        "status": "active",
        "title": "Startup world doc",
        "tags": ["startup", "canon"],
        "body": "Host startup should index this world canon document.",
        "updated_by": "unit_test",
        "ts_ms": 1741000000000,
    }
    payload["hash"] = compute_world_canon_hash(payload)
    (world_root / "startup-world-doc.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with compose_canon_test_runtime(
        tmp_path,
        request_id="http-hooks-startup-sync",
        world_root=world_root,
    ) as runtime:
        status = _sync_world_canon_index_on_startup(
            service=runtime.container.application,
            logger=logging.getLogger("tests.world_canon_startup_sync"),
            world_root=str(world_root),
            ts_ms=1741000001234,
        )
        indexed = runtime.container.application.get_document("startup-world-doc")

    assert status == "OK (synced=1, changed=1)"
    assert indexed is not None
    assert indexed["version"] == 1
    assert indexed["hash"] == payload["hash"]
    assert indexed["status"] == "active"
    assert str(indexed["source_path"]).endswith("startup-world-doc.json")
