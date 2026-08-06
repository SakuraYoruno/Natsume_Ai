from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tests.fakes.host_app import build_host_test_app
from host_brain.app import canon_routes
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.canon import build_process_canon_container
from host_brain.input_runtime.cortex_dispatch import _prepare_input_affect_handoff


class _LedgerStub:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_voice_event(self, row: dict) -> None:
        self.rows.append(dict(row))


def _build_client(
    monkeypatch,
    *,
    canonical_root: Path,
    ledger: _LedgerStub | None = None,
) -> TestClient:
    stub = ledger or _LedgerStub()
    db_path = canonical_root / "host-canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="http-edge-events-fixture",
    )
    assert migrated.success, migrated.as_dict()
    canonical_ledger = SQLiteLedger(db_path=db_path, initialize_schema=False)
    canon = build_process_canon_container(
        ledger=canonical_ledger,
        canonical_db_path=db_path,
        world_root=canonical_root / "world",
    )
    client = TestClient(
        build_host_test_app(
            get_ledger=lambda: stub,
            canon_service_provider=lambda: canon.application,
        ),
        raise_server_exceptions=False,
    )
    client.__dict__["_canonical_ledger"] = canonical_ledger
    return client


def _close_client(client: TestClient) -> None:
    client.close()
    canonical_ledger = client.__dict__.pop("_canonical_ledger", None)
    if canonical_ledger is not None:
        canonical_ledger.close()


def test_v1_edge_events_http_accepts_valid_batch(monkeypatch, tmp_path: Path) -> None:
    ledger = _LedgerStub()
    client = _build_client(monkeypatch, canonical_root=tmp_path, ledger=ledger)
    try:
        response = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_CHUNK",
                        "ts_ms": int(time.time() * 1000),
                        "request_id": "req-edge-http-001",
                        "session_id": "sess-edge-http-001",
                    }
                ]
            },
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["accepted"] == 1
    assert payload["rejected"] == 0
    assert ledger.rows == []


def test_v1_edge_events_http_rejects_unknown_event_type(monkeypatch, tmp_path: Path) -> None:
    client = _build_client(monkeypatch, canonical_root=tmp_path)
    try:
        response = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "UNKNOWN_EVENT",
                        "ts_ms": int(time.time() * 1000),
                        "request_id": "req-edge-http-002",
                        "session_id": "sess-edge-http-002",
                    }
                ]
            },
        )
    finally:
        _close_client(client)

    assert response.status_code == 400
    assert "Unknown edge event_type" in response.json()["detail"]


def test_v1_edge_events_http_rejects_unknown_source(monkeypatch, tmp_path: Path) -> None:
    client = _build_client(monkeypatch, canonical_root=tmp_path)
    try:
        response = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_CHUNK",
                        "ts_ms": int(time.time() * 1000),
                        "request_id": "req-edge-http-002b",
                        "session_id": "sess-edge-http-002b",
                        "source": "public_internet",
                    }
                ]
            },
        )
    finally:
        _close_client(client)

    assert response.status_code == 400
    assert "Unknown edge event source" in response.json()["detail"]


def test_v1_edge_events_http_final_asr_event_bridges_into_host_input(monkeypatch, tmp_path: Path) -> None:
    ledger = _LedgerStub()
    dispatch_mock = AsyncMock(return_value=None)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
    monkeypatch.setattr("tests.fakes.host_app._dispatch_to_graph", dispatch_mock)

    client = _build_client(monkeypatch, canonical_root=tmp_path, ledger=ledger)
    try:
        response = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_FINAL",
                        "ts_ms": int(time.time() * 1000),
                        "request_id": "req-edge-http-003",
                        "session_id": "sess-edge-http-003",
                        "payload": {
                            "text": "请帮我看看这个界面",
                            "confidence": 0.96,
                            "final": True,
                            "seq": 9,
                        },
                    }
                ]
            },
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["accepted"] == 1
    assert dispatch_mock.await_count == 1
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["source"] == "ASR"
    assert kwargs["session_id"] == "sess-edge-http-003"
    assert kwargs["text"] == "请帮我看看这个界面"
    assert kwargs["payload"]["edge_event_type"] == "ASR_FINAL"
    assert kwargs["payload"]["edge_request_id"] == "req-edge-http-003"


def test_v1_edge_events_http_asr_acoustic_attestation_is_not_self_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ledger = _LedgerStub()
    dispatch_mock = AsyncMock(return_value=None)
    ts_ms = int(time.time() * 1000)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
    monkeypatch.setattr("tests.fakes.host_app._dispatch_to_graph", dispatch_mock)

    client = _build_client(monkeypatch, canonical_root=tmp_path, ledger=ledger)
    try:
        response = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_FINAL",
                        "ts_ms": ts_ms,
                        "request_id": "req-edge-http-asr-acoustic",
                        "session_id": "sess-edge-http-asr-acoustic",
                        "payload": {
                            "text": "我有点撑不住了。",
                            "confidence": 0.96,
                            "final": True,
                            "emotion_tag": "SAD",
                            "emotion_intensity": 0.76,
                            "emotion_source": "acoustic_model_v2",
                            "emotion_modality": "acoustic",
                            "emotion_confidence": 0.83,
                            "acoustic_emotion_available": True,
                            "emotion_inference_degraded": False,
                            "emotion_context": {"emotion_tag": "HAPPY"},
                            "multimodal_observations": {
                                "primary_modality": "voice",
                                "user_affect_observations": [
                                    {
                                        "modality": "SELF_REPORT",
                                        "emotion_tag": "HAPPY",
                                    }
                                ],
                            },
                        },
                    }
                ]
            },
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    assert dispatch_mock.await_count == 1
    dispatched = dispatch_mock.await_args.kwargs
    assert dispatched["source"] == "ASR"
    tag, intensity, multimodal, context = _prepare_input_affect_handoff(
        source=dispatched["source"],
        payload=dispatched["payload"],
        ts_ms=dispatched["ts_ms"],
    )
    assert tag is None
    assert intensity is None
    assert context is None
    assert multimodal == {
        "primary_modality": "voice",
        "user_affect_observations": [
            {
                "modality": "ACOUSTIC",
                "emotion_distribution": {"SAD": 0.76},
                "confidence": 0.83,
                "observed_at_ms": ts_ms,
                "expires_at_ms": ts_ms + 5_000,
                "provider": "edge-acoustic:acoustic_model_v2",
            }
        ],
    }


def test_v1_edge_events_http_partial_batch_reports_partial_fail(monkeypatch, tmp_path: Path) -> None:
    ledger = _LedgerStub()
    client = _build_client(monkeypatch, canonical_root=tmp_path, ledger=ledger)
    try:
        response = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_CHUNK",
                        "ts_ms": int(time.time() * 1000),
                        "request_id": "req-edge-http-004a",
                        "session_id": "sess-edge-http-004a",
                    },
                    {
                        "event_type": "ASR_CHUNK",
                        "ts_ms": "not-an-int",
                        "request_id": "req-edge-http-004b",
                        "session_id": "sess-edge-http-004b",
                    },
                ]
            },
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["accepted"] == 1
    assert payload["rejected"] == 1
    assert payload["reason_code"] == "RPC_EVENT_BATCH_PARTIAL_FAIL"
    assert payload["rejected_indices"] == [1]
    assert ledger.rows == []


def test_v1_edge_events_http_low_confidence_asr_does_not_bridge(monkeypatch, tmp_path: Path) -> None:
    ledger = _LedgerStub()
    dispatch_mock = AsyncMock(return_value=None)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_MIN_CONFIDENCE", "0.85")
    monkeypatch.setattr("host_brain.input_runtime.worker_dispatch._dispatch_to_graph", dispatch_mock)

    client = _build_client(monkeypatch, canonical_root=tmp_path, ledger=ledger)
    try:
        response = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_FINAL",
                        "ts_ms": int(time.time() * 1000),
                        "request_id": "req-edge-http-005",
                        "session_id": "sess-edge-http-005",
                        "payload": {
                            "text": "这是一段低置信度识别",
                            "confidence": 0.62,
                            "final": True,
                        },
                    }
                ]
            },
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["accepted"] == 1
    assert dispatch_mock.await_count == 0
    assert ledger.rows[0]["event_type"] == "ASR_FINAL"


def test_v1_edge_events_http_accepts_empty_batch(monkeypatch, tmp_path: Path) -> None:
    client = _build_client(monkeypatch, canonical_root=tmp_path)
    try:
        response = client.post("/v1/edge/events", json={"events": []})
    finally:
        _close_client(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["accepted"] == 0
    assert payload["rejected"] == 0
    assert payload["detail"] == "empty batch"
