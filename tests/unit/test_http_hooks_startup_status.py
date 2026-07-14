from __future__ import annotations

import json
import logging

from host_brain.app.http_hooks import _init_edge_command_pusher_status
from host_brain.app.http_hooks import _sync_world_canon_index_on_startup
from host_brain.app.http_hooks import _startup_status_is_degraded
from host_brain.memory.canon_world_store import compute_world_canon_hash
from tests.fakes.canon_runtime import compose_canon_test_runtime


def test_startup_status_marks_only_real_degraded_values() -> None:
    assert _startup_status_is_degraded("DEGRADED: edge rpc unreachable") is True
    assert _startup_status_is_degraded("FAIL: scheduler crashed") is True
    assert _startup_status_is_degraded("OK") is False
    assert _startup_status_is_degraded("OK (task_id=distillation_engine_periodic, interval=600s)") is False
    assert _startup_status_is_degraded("SKIPPED: gateway-only mode") is False


def test_init_edge_command_pusher_status_returns_ok(monkeypatch) -> None:
    class _FakePusher:
        enabled = True
        edge_base_url = "http://edge.local"

    monkeypatch.setattr(
        "host_brain.tribe.edge_command_pusher.init_edge_command_pusher",
        lambda: _FakePusher(),
    )

    status = _init_edge_command_pusher_status(
        logger=logging.getLogger("tests.edge_command_pusher.ok"),
    )

    assert status == "OK (enabled=True, edge_url=http://edge.local)"


def test_init_edge_command_pusher_status_returns_degraded(monkeypatch) -> None:
    def _raise() -> None:
        raise RuntimeError("pusher unavailable")

    monkeypatch.setattr(
        "host_brain.tribe.edge_command_pusher.init_edge_command_pusher",
        _raise,
    )

    status = _init_edge_command_pusher_status(
        logger=logging.getLogger("tests.edge_command_pusher.degraded"),
    )

    assert status == "DEGRADED: pusher unavailable"


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
