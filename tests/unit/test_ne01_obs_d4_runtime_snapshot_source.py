from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.observability_runtime.live_source_projection import (
    PROACTIVE_AUTONOMY_DEFAULT_KEY,
    PROACTIVE_AUTONOMY_KIND,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
)
from host_brain.app.observability_runtime.runtime_snapshot_source import (
    RUNTIME_LIFE_SNAPSHOT_NOT_FOUND,
    RUNTIME_LIFE_SNAPSHOT_PARTIAL,
    RUNTIME_LIFE_SNAPSHOT_READ_FAILED,
    RUNTIME_PROACTIVE_INVALID,
    RUNTIME_PROACTIVE_STALE,
    RUNTIME_SYS_MODE_NOT_READY,
    RUNTIME_SYS_MODE_SOURCE_UNAVAILABLE,
    RuntimeSnapshotSourceReader,
    RuntimeSnapshotSourceUnavailable,
)


NOW = 1_700_000_000_000


class _Store:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], Any] = {}
        self.reads: list[tuple[str, str]] = []

    def get(self, kind: str, key: str) -> Any | None:
        self.reads.append((kind, key))
        return self.documents.get((kind, key))

    def put(
        self,
        kind: str,
        key: str,
        payload: dict[str, Any],
        *,
        cursor: int,
        freshness_ts_ms: int = NOW - 100,
        degraded_state: str = "HEALTHY",
    ) -> None:
        self.documents[(kind, key)] = SimpleNamespace(
            payload=dict(payload),
            source_cursor=cursor,
            freshness_ts_ms=freshness_ts_ms,
            degraded_state=degraded_state,
        )


class _LifeReader:
    def __init__(self, person_id: str = "default", *, version: int = 7) -> None:
        self.person_id = person_id
        self.version = version
        self.calls: list[str] = []

    def get_snapshot(self, person_id: str) -> dict[str, Any]:
        self.calls.append(person_id)
        return {
            "person_id": self.person_id,
            "version": self.version,
            "state": {
                "person_id": self.person_id,
                "version": self.version,
                "runtime": {"alive_status": "alive"},
                "autonomy": {"current_attention": "body_presence"},
                "affect": {"energy": 0.7, "fatigue": 0.2},
                "scene": {"kind": "dialogue"},
                "intent": {"kind": "attend"},
                "embodiment": {"emotion_tag": "calm"},
                # Private durable content must not be copied wholesale.
                "private_diary": "C:/private/never-project-this.txt",
            },
            "updated_at_ms": NOW - 300,
        }


def _healthy_store(*, mode: str = "focus", marker: str = "one") -> _Store:
    store = _Store()
    store.put(
        SYS_MODE_STATUS_KIND,
        SYS_MODE_STATUS_KEY,
        {
            "sys_mode": mode,
            "auto_rules": {"sleep": True},
            "ts_ms": NOW - 100,
        },
        cursor=NOW - 90,
    )
    store.put(
        PROACTIVE_AUTONOMY_KIND,
        PROACTIVE_AUTONOMY_DEFAULT_KEY,
        {
            "available": True,
            "proactive_readiness": 0.72,
            "active_projects": [{"project_id": marker}],
            "top_intention": {"project_id": marker, "pull": 0.8},
            "timestamp": NOW - 100,
        },
        cursor=NOW - 80,
    )
    return store


def test_healthy_snapshot_has_stable_minimum_payload_and_cursor() -> None:
    from host_brain.app.obs_routes_sysmode import (
        DigitalLifeRuntimeSnapshotResponse,
    )

    store = _healthy_store()
    life = _LifeReader("persona-1")
    reader = RuntimeSnapshotSourceReader(
        store=store,  # type: ignore[arg-type]
        life_snapshot_reader=life,
        person_id="persona-1",
        clock_ms=lambda: NOW,
    )

    payload, cursor = reader()

    assert cursor == NOW - 80
    assert life.calls == ["persona-1"]
    assert store.reads == [
        (SYS_MODE_STATUS_KIND, SYS_MODE_STATUS_KEY),
        (PROACTIVE_AUTONOMY_KIND, PROACTIVE_AUTONOMY_DEFAULT_KEY),
    ]
    assert payload["persona_user_id"] == "persona-1"
    assert payload["current_topic"] == ""
    assert payload["sys_mode"] == "focus"
    assert payload["hard_state"] == {
        "life_version": 7,
        "life_updated_at_ms": NOW - 300,
        "alive_status": "alive",
        "current_attention": "body_presence",
        "scene": "dialogue",
        "intent": "attend",
        "emotion_tag": "CALM",
        "energy": 0.7,
        "fatigue": 0.2,
    }
    assert payload["runtime_summary"]["status"] == "HEALTHY"
    assert payload["slot_status"]["current_topic"] == "not_applicable"
    assert payload["slot_status"]["sys_mode_status"] == "live"
    assert payload["slot_status"]["initiative_decision"] == "live"
    assert payload["degraded"] is False
    assert payload["degraded_reasons"] == []
    assert "private_diary" not in str(payload)
    assert DigitalLifeRuntimeSnapshotResponse(**payload).sys_mode == "focus"


def test_missing_and_stale_optional_slots_return_partial_degraded_payload() -> None:
    store = _Store()
    store.put(
        PROACTIVE_AUTONOMY_KIND,
        PROACTIVE_AUTONOMY_DEFAULT_KEY,
        {"available": True, "proactive_readiness": 0.4},
        cursor=220,
        freshness_ts_ms=NOW - 45_001,
    )
    reader = RuntimeSnapshotSourceReader(
        store=store,  # type: ignore[arg-type]
        life_snapshot_reader=_LifeReader(),
        clock_ms=lambda: NOW,
    )

    result = reader.read()
    payload = result.payload

    assert payload["sys_mode"] == "unknown"
    assert payload["sys_mode_status"] == {}
    assert payload["initiative_decision"] == {}
    assert payload["slot_status"]["sys_mode_status"] == "source_unavailable"
    assert payload["slot_status"]["initiative_decision"] == "stale"
    assert payload["runtime_summary"]["status"] == "PARTIAL_DEGRADED"
    assert payload["degraded"] is True
    assert payload["degraded_reasons"] == [
        RUNTIME_SYS_MODE_NOT_READY,
        RUNTIME_PROACTIVE_STALE,
    ]
    assert all(reason.startswith("OBS_RUNTIME_") for reason in payload["degraded_reasons"])


def test_incomplete_life_state_is_explicitly_partial_not_live_or_healthy() -> None:
    def minimal_life(_person_id: str) -> dict[str, Any]:
        return {
            "person_id": "minimal-person",
            "version": 3,
            "state": {
                "person_id": "minimal-person",
                "version": 3,
            },
            "updated_at_ms": NOW - 200,
        }

    payload = RuntimeSnapshotSourceReader(
        store=_healthy_store(),  # type: ignore[arg-type]
        life_snapshot_reader=minimal_life,
        clock_ms=lambda: NOW,
    ).read().payload

    assert payload["hard_state"]["alive_status"] == "unknown"
    assert payload["slot_status"]["hard_state"] == "partial"
    assert payload["slot_status"]["runtime_summary"] == "partial"
    assert payload["runtime_summary"]["status"] == "PARTIAL_DEGRADED"
    assert payload["degraded"] is True
    assert payload["degraded_reasons"] == [RUNTIME_LIFE_SNAPSHOT_PARTIAL]


def test_missing_proactive_readiness_is_invalid_not_live() -> None:
    store = _healthy_store()
    store.put(
        PROACTIVE_AUTONOMY_KIND,
        PROACTIVE_AUTONOMY_DEFAULT_KEY,
        {
            "available": True,
            "active_projects": [{"project_id": "project-without-readiness"}],
        },
        cursor=NOW - 70,
    )

    payload = RuntimeSnapshotSourceReader(
        store=store,  # type: ignore[arg-type]
        life_snapshot_reader=_LifeReader(),
        clock_ms=lambda: NOW,
    ).read().payload

    assert payload["initiative_decision"] == {}
    assert payload["slot_status"]["initiative_decision"] == "invalid"
    assert payload["slot_status"]["runtime_summary"] == "partial"
    assert payload["degraded_reasons"] == [RUNTIME_PROACTIVE_INVALID]


def test_negative_valence_is_preserved_in_the_signed_unit_interval() -> None:
    def life_with_negative_valence(person_id: str) -> dict[str, Any]:
        snapshot = _LifeReader(person_id).get_snapshot(person_id)
        snapshot["state"]["affect"]["valence"] = -0.65
        return snapshot

    payload = RuntimeSnapshotSourceReader(
        store=_healthy_store(),  # type: ignore[arg-type]
        life_snapshot_reader=life_with_negative_valence,
        clock_ms=lambda: NOW,
    ).read().payload

    assert payload["hard_state"]["valence"] == -0.65
    assert payload["runtime_summary"]["status"] == "HEALTHY"


def test_runtime_snapshot_query_topic_is_documented_as_deprecated() -> None:
    from fastapi import FastAPI

    from host_brain.app.obs_routes_sysmode import router

    app = FastAPI()
    app.include_router(router)
    parameters = app.openapi()["paths"]["/obs/runtime-snapshot"]["get"][
        "parameters"
    ]
    topic = next(item for item in parameters if item["name"] == "current_topic")

    assert topic["deprecated"] is True
    assert "不会触发重算或改变持久投影" in topic["description"]


def test_source_unavailable_document_is_never_presented_as_live_or_empty_success() -> None:
    store = _healthy_store()
    store.put(
        SYS_MODE_STATUS_KIND,
        SYS_MODE_STATUS_KEY,
        {
            "available": False,
            "reason_code": "HOST_PRIVATE_REASON_MUST_NOT_ESCAPE",
            "detail": "D:/private/state.json",
        },
        cursor=330,
        degraded_state="SOURCE_UNAVAILABLE",
    )

    payload = RuntimeSnapshotSourceReader(
        store=store,  # type: ignore[arg-type]
        life_snapshot_reader=_LifeReader(),
        clock_ms=lambda: NOW,
    ).read().payload

    assert payload["sys_mode"] == "unknown"
    assert payload["sys_mode_status"] == {}
    assert payload["slot_status"]["sys_mode_status"] == "source_unavailable"
    assert payload["degraded_reasons"] == [RUNTIME_SYS_MODE_SOURCE_UNAVAILABLE]
    assert "HOST_PRIVATE_REASON" not in str(payload)
    assert "state.json" not in str(payload)


def test_missing_life_root_is_typed_source_unavailable() -> None:
    life = _LifeReader(version=0)
    reader = RuntimeSnapshotSourceReader(
        store=_healthy_store(),  # type: ignore[arg-type]
        life_snapshot_reader=life,
        clock_ms=lambda: NOW,
    )

    with pytest.raises(RuntimeSnapshotSourceUnavailable) as captured:
        reader.read()

    assert captured.value.reason_code == RUNTIME_LIFE_SNAPSHOT_NOT_FOUND
    assert str(captured.value) == RUNTIME_LIFE_SNAPSHOT_NOT_FOUND
    assert captured.value.as_public_payload()["reason_code"] == (
        RUNTIME_LIFE_SNAPSHOT_NOT_FOUND
    )


def test_reader_failure_exposes_only_stable_reason_code() -> None:
    def private_failure(_person_id: str) -> dict[str, Any]:
        raise RuntimeError("token=private at C:/runtime/secret.json")

    reader = RuntimeSnapshotSourceReader(
        store=_healthy_store(),  # type: ignore[arg-type]
        life_snapshot_reader=private_failure,
        clock_ms=lambda: NOW,
    )

    with pytest.raises(RuntimeSnapshotSourceUnavailable) as captured:
        reader.read()

    public = captured.value.as_public_payload()
    assert public["reason_code"] == RUNTIME_LIFE_SNAPSHOT_READ_FAILED
    assert "private" not in str(public)
    assert "secret.json" not in str(public)


def test_reader_has_no_hidden_runtime_import_or_service_lookup(monkeypatch) -> None:
    from host_brain.composition import process_services

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("hidden process service lookup")

    for name in dir(process_services):
        if name.startswith(("get_", "peek_", "configure_")):
            monkeypatch.setattr(process_services, name, forbidden)

    source_path = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "app"
        / "observability_runtime"
        / "runtime_snapshot_source.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden_fragments = (
        "process_services",
        "mode_transition",
        "lifecycle_runtime",
        "llm",
        "model_transport",
    )
    assert not any(
        fragment in module
        for module in imported_modules
        for fragment in forbidden_fragments
    )

    payload = RuntimeSnapshotSourceReader(
        store=_healthy_store(),  # type: ignore[arg-type]
        life_snapshot_reader=_LifeReader(),
        clock_ms=lambda: NOW,
    ).read().payload
    assert payload["runtime_summary"]["status"] == "HEALTHY"


def test_two_app_stores_and_readers_remain_isolated() -> None:
    first_store = _healthy_store(mode="focus", marker="first")
    second_store = _healthy_store(mode="sleep", marker="second")
    first_reader = RuntimeSnapshotSourceReader(
        store=first_store,  # type: ignore[arg-type]
        life_snapshot_reader=_LifeReader("first-person"),
        person_id="first-person",
        clock_ms=lambda: NOW,
    )
    second_reader = RuntimeSnapshotSourceReader(
        store=second_store,  # type: ignore[arg-type]
        life_snapshot_reader=_LifeReader("second-person"),
        person_id="second-person",
        clock_ms=lambda: NOW,
    )

    first = first_reader.read().payload
    second = second_reader.read().payload

    assert first["persona_user_id"] == "first-person"
    assert first["sys_mode"] == "focus"
    assert first["initiative_decision"]["top_intention"]["project_id"] == "first"
    assert second["persona_user_id"] == "second-person"
    assert second["sys_mode"] == "sleep"
    assert second["initiative_decision"]["top_intention"]["project_id"] == "second"
