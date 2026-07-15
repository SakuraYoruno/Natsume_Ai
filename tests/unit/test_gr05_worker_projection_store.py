from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_INVALID,
    WORKER_PROJECTION_REPLAY_DRIFT,
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
    WorkerProjectionStoreError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0086_task_runtime_worker_projection.sql"
BODY_FIELDS = (
    "phase", "done", "decision", "reason_code", "command_id", "request_id", "narrative",
    "route_type", "target_id", "graph_id", "graph_revision", "node_id",
    "attempt_id", "node_grant_ref", "audit_refs",
)
OPTIONAL_BODY_FIELDS = ("acceptance_effect_intents", "budget_usage")


def _store(tmp_path: Path) -> WorkerProjectionStore:
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    return WorkerProjectionStore(path, clock=lambda: 123)


def _projection() -> dict:
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": "command-projection",
        "request_id": "request-projection",
        "narrative": "durable answer",
        "route_type": "chat",
        "target_id": "surface_reply",
        "graph_id": "graph-projection",
        "graph_revision": 1,
        "node_id": "node-projection",
        "attempt_id": "attempt-projection",
        "node_grant_ref": "governor-grant://child/node",
        "audit_refs": {},
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        **body,
        "projection_id": "worker-projection://graph/1/node/attempt/abc",
        "payload_digest": "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }


def _refresh_digest(payload: dict) -> None:
    body = {key: payload.get(key) for key in BODY_FIELDS}
    body.update({key: payload[key] for key in OPTIONAL_BODY_FIELDS if key in payload})
    payload["payload_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_projection_persists_replays_exactly_and_survives_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = _projection()

    first = store.stage(projection_id=payload["projection_id"], projection=payload)
    replay = store.stage(projection_id=payload["projection_id"], projection=payload)
    reopened = WorkerProjectionStore(store.db_path, clock=lambda: 999)

    assert first == replay == payload
    assert reopened.get(payload["projection_id"]) == payload
    assert reopened.load(payload["projection_id"]) == payload


def test_projection_replay_drift_and_attempt_alias_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = _projection()
    store.stage(projection_id=payload["projection_id"], projection=payload)

    drift = deepcopy(payload)
    drift["narrative"] = "changed"
    body = {key: drift.get(key) for key in BODY_FIELDS}
    drift["payload_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with pytest.raises(WorkerProjectionStoreError) as raised:
        store.stage(projection_id=payload["projection_id"], projection=drift)
    assert raised.value.reason_code == WORKER_PROJECTION_REPLAY_DRIFT

    alias = deepcopy(payload)
    alias["projection_id"] = "worker-projection://graph/1/node/attempt/different"
    with pytest.raises(WorkerProjectionStoreError) as alias_error:
        store.stage(projection_id=alias["projection_id"], projection=alias)
    assert alias_error.value.reason_code == WORKER_PROJECTION_REPLAY_DRIFT


def test_projection_forbids_secret_fields_and_invalid_digest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    secret = _projection()
    secret["audit_refs"]["access_token"] = "private"
    with pytest.raises(WorkerProjectionStoreError) as secret_error:
        store.stage(projection_id=secret["projection_id"], projection=secret)
    assert secret_error.value.reason_code == WORKER_PROJECTION_INVALID

    invalid = _projection()
    invalid["payload_digest"] = "sha256:" + "0" * 64
    with pytest.raises(WorkerProjectionStoreError) as digest_error:
        store.stage(projection_id=invalid["projection_id"], projection=invalid)
    assert digest_error.value.reason_code == WORKER_PROJECTION_INVALID


def test_projection_allows_token_usage_and_latency_metadata_but_not_credentials(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    metrics = _projection()
    metrics["audit_refs"] = {
        "timings_ms": {
            "first_token_ms": 12,
            "stream_after_first_token_ms": 7,
        },
        "budget": {
            "token_count": 33,
            "token_budget": 100,
            "prompt_tokens": 20,
            "completion_tokens": 13,
            "budget_tokens": 100,
            "estimated_tokens": 31,
            "actual_tokens": 31,
            "max_context_tokens": 100,
            "context_token_budget": {"within_budget": True},
            "context_token_estimator": "Natsume.context_token_estimator.v1",
        },
    }
    _refresh_digest(metrics)

    assert store.stage(
        projection_id=metrics["projection_id"],
        projection=metrics,
    )["audit_refs"] == metrics["audit_refs"]

    for secret_key in ("token", "api_token", "refresh_token", "approval_token_ref"):
        secret = _projection()
        secret["audit_refs"] = {secret_key: "private"}
        _refresh_digest(secret)
        with pytest.raises(WorkerProjectionStoreError) as raised:
            store.stage(
                projection_id=secret["projection_id"],
                projection=secret,
            )
        assert raised.value.reason_code == WORKER_PROJECTION_INVALID


def test_projection_digest_roundtrips_optional_budget_and_acceptance_effects(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    payload = _projection()
    payload["budget_usage"] = {
        "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
        "usage_id": "worker-budget://projection-one",
        "measurements": {"token_count": 23, "retry_count": 0},
        "evidence_refs": ["model-budget://projection-one"],
    }
    payload["acceptance_effect_intents"] = [
        {
            "effect_type": "life.foreground_completed",
            "commit_scope": "NODE",
            "payload": {"command_type": "foreground_completed"},
        }
    ]
    _refresh_digest(payload)

    stored = store.stage(
        projection_id=payload["projection_id"],
        projection=payload,
    )
    reopened = WorkerProjectionStore(store.db_path)

    assert stored == payload
    assert reopened.get(payload["projection_id"]) == payload
