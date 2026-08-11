from __future__ import annotations

import inspect
import json

import pytest

from host_brain.app.observability_runtime.security_immune_snapshot_reader import (
    SECURITY_IMMUNE_EVIDENCE_CORRUPT,
    SECURITY_IMMUNE_EVIDENCE_MISSING,
    SECURITY_IMMUNE_EVIDENCE_STALE,
    SecurityImmuneSnapshotCorruptError,
    SecurityImmuneSnapshotMissingError,
    SecurityImmuneSnapshotReader,
    SecurityImmuneSnapshotStaleError,
)


NOW_MS = 1_000_000


def _valid_state(*, generated_ts_ms: int = NOW_MS) -> dict:
    return {
        "enabled": True,
        "mode": "observe",
        "assets": [],
        "incidents": [],
        "last_run_ts_ms": generated_ts_ms - 1,
        "run_count": 1,
        "generated_ts_ms": generated_ts_ms,
    }


VALID_STATE_TEXT = json.dumps(_valid_state())


def _write_evidence(tmp_path, *, state: object, events: list[object]) -> None:  # noqa: ANN001
    (tmp_path / "state.json").write_text(
        json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl").write_text(
        "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in events),
        encoding="utf-8",
    )


def test_reader_projects_healthy_evidence_without_constructing_agent_or_provider(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    from host_brain.security_immune import agent
    from host_brain.security_immune.providers import factory

    _write_evidence(
        tmp_path,
        state={
            "enabled": True,
            "mode": "hunt",
            "assets": [
                {
                    "asset_id": "asset-1",
                    "hostname": "lab-host",
                    "os_family": "Windows",
                    "source": "defender",
                    "observed_ts_ms": NOW_MS - 20,
                    "signals": [
                        {
                            "signal_id": "encoded-command",
                            "title": "Encoded command",
                            "severity": 80,
                            "category": "process",
                            "ts_ms": NOW_MS - 19,
                        }
                    ],
                }
            ],
            "incidents": [
                {
                    "incident_id": "incident-1",
                    "asset_id": "asset-1",
                    "risk": "high",
                    "verdict": "suspicious",
                    "confidence": 0.8,
                    "title": "Encoded command",
                    "updated_ts_ms": NOW_MS - 10,
                }
            ],
            "last_run_ts_ms": NOW_MS - 5,
            "run_count": 3,
            "generated_ts_ms": NOW_MS,
            "provider_statuses": [
                {
                    "provider_id": "defender",
                    "status": "ok",
                    "asset_count": 1,
                    "signal_count": 1,
                    "ts_ms": NOW_MS - 1,
                }
            ],
        },
        events=[
            {
                "event_type": "SECURITY_IMMUNE_SCAN",
                "asset_count": 1,
                "signal_count": 1,
                "incident_count": 1,
                "ts_ms": NOW_MS,
            }
        ],
    )
    monkeypatch.setattr(
        agent.SecurityImmuneAgent,
        "__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reader constructed SecurityImmuneAgent")
        ),
    )
    monkeypatch.setattr(
        factory,
        "build_provider_chain",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reader constructed security providers")
        ),
    )

    reader = SecurityImmuneSnapshotReader(tmp_path, clock_ms=lambda: NOW_MS + 10)
    result = reader.read()
    callback_payload, callback_cursor = reader()

    assert result.payload["enabled"] is True
    assert result.payload["mode"] == "hunt"
    assert result.payload["asset_count"] == 1
    assert result.payload["incident_count"] == 1
    assert result.payload["risk_counts"]["high"] == 1
    assert result.payload["recent_events"][0]["event_type"] == "SECURITY_IMMUNE_SCAN"
    assert result.source_cursor >= NOW_MS
    assert result.freshness_ts_ms == NOW_MS
    assert callback_payload == result.payload
    assert callback_cursor >= NOW_MS


def test_reader_requires_state_but_treats_missing_events_as_empty_history(tmp_path) -> None:  # noqa: ANN001
    reader = SecurityImmuneSnapshotReader(tmp_path, clock_ms=lambda: 124)

    with pytest.raises(SecurityImmuneSnapshotMissingError) as missing_state:
        reader.read()
    assert missing_state.value.reason_code == SECURITY_IMMUNE_EVIDENCE_MISSING
    assert missing_state.value.source_component == "state"

    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "mode": "observe",
                "assets": [],
                "incidents": [],
                "last_run_ts_ms": None,
                "run_count": 0,
                "generated_ts_ms": 123,
            }
        ),
        encoding="utf-8",
    )
    result = reader.read()

    assert result.payload["recent_events"] == []
    assert result.payload["evidence_source"]["recent_events_status"] == "NOT_PRESENT"
    assert result.source_cursor >= 123


@pytest.mark.parametrize(
    ("state_text", "events_text", "component"),
    [
        ("{not-json", '{"event_type":"TEST","ts_ms":1}\n', "state"),
        ("[]", '{"event_type":"TEST","ts_ms":1}\n', "state"),
        ("{}", '{"event_type":"TEST","ts_ms":1}\n', "state"),
        (VALID_STATE_TEXT, "{not-json\n", "events"),
        (VALID_STATE_TEXT, "[]\n", "events"),
    ],
)
def test_reader_reports_corrupt_evidence_with_typed_error(
    tmp_path,
    state_text: str,
    events_text: str,
    component: str,
) -> None:  # noqa: ANN001
    (tmp_path / "state.json").write_text(state_text, encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(events_text, encoding="utf-8")

    with pytest.raises(SecurityImmuneSnapshotCorruptError) as raised:
        SecurityImmuneSnapshotReader(tmp_path, clock_ms=lambda: NOW_MS).read()

    assert raised.value.reason_code == SECURITY_IMMUNE_EVIDENCE_CORRUPT
    assert raised.value.source_component == component
    assert str(tmp_path) not in str(raised.value)
    assert str(tmp_path) not in json.dumps(raised.value.as_public_payload())


def test_reader_public_payload_redacts_paths_secrets_and_writer_only_fields(tmp_path) -> None:  # noqa: ANN001
    secret = "super-secret-token-value"
    private_path = "C:\\Users\\owner\\private\\sample.exe"
    _write_evidence(
        tmp_path,
        state={
            "enabled": True,
            "mode": "observe",
            "last_run_ts_ms": NOW_MS - 5,
            "run_count": 2,
            "generated_ts_ms": NOW_MS,
            "last_error": f"failed to read {private_path}; token={secret}",
            "policy": {"api_key": secret, "quarantine_path": private_path},
            "assets": [
                {
                    "asset_id": "asset-1",
                    "hostname": "lab-host",
                    "os_family": "Windows",
                    "source": "defender",
                    "observed_ts_ms": NOW_MS - 20,
                    "tags": [secret],
                    "metadata": {"secret": secret, "path": private_path},
                    "signals": [
                        {
                            "signal_id": "signal-1",
                            "title": "Suspicious process",
                            "severity": 90,
                            "category": "process",
                            "ts_ms": NOW_MS - 19,
                            "detail": private_path,
                            "evidence": {"authorization": secret, "path": private_path},
                        }
                    ],
                }
            ],
            "incidents": [
                {
                    "incident_id": "incident-1",
                    "asset_id": "asset-1",
                    "risk": "critical",
                    "updated_ts_ms": NOW_MS - 10,
                    "summary": f"Bearer {secret} at {private_path}",
                    "recommended_actions": [
                        {
                            "action_id": "scan",
                            "title": "Scan",
                            "reason": private_path,
                            "params": {"api_key": secret},
                        }
                    ],
                    "action_results": [
                        {
                            "action_id": "scan",
                            "status": "executed",
                            "detail": f"token={secret}",
                            "params": {"path": private_path},
                        }
                    ],
                }
            ],
            "provider_statuses": [
                {
                    "provider_id": "defender",
                    "status": "degraded",
                    "detail": f"password={secret}",
                    "metadata": {"path": private_path},
                }
            ],
        },
        events=[
            {
                "event_type": "SECURITY_IMMUNE_SCAN",
                "reason": secret,
                "path": private_path,
                "token": secret,
                "ts_ms": NOW_MS,
            }
        ],
    )

    payload = SecurityImmuneSnapshotReader(
        tmp_path,
        clock_ms=lambda: NOW_MS + 10,
    ).read().payload
    encoded = json.dumps(payload, ensure_ascii=False)

    assert secret not in encoded
    assert private_path not in encoded
    assert str(tmp_path) not in encoded
    assert "policy" not in payload
    assert payload["last_error"] == "SECURITY_IMMUNE_RUN_FAILED"
    assert payload["assets"][0]["signals"][0]["evidence"] == {}
    assert payload["assets"][0]["metadata"] == {}
    assert payload["incidents"][0]["recommended_actions"][0]["params"] == {}
    assert payload["provider_statuses"][0]["metadata"] == {}


def test_reader_rejects_empty_or_expired_state_even_when_file_mtime_is_new(
    tmp_path,
) -> None:
    _write_evidence(
        tmp_path,
        state=_valid_state(generated_ts_ms=100),
        events=[],
    )

    with pytest.raises(SecurityImmuneSnapshotStaleError) as stale:
        SecurityImmuneSnapshotReader(
            tmp_path,
            max_age_ms=50,
            clock_ms=lambda: 1_000,
        ).read()

    assert stale.value.reason_code == SECURITY_IMMUNE_EVIDENCE_STALE
    assert stale.value.source_component == "state"
    assert stale.value.as_public_payload()["source_scope"] == (
        "security-immune-evidence-store"
    )
    assert str(tmp_path) not in str(stale.value)

    _write_evidence(tmp_path, state={}, events=[])
    with pytest.raises(SecurityImmuneSnapshotCorruptError):
        SecurityImmuneSnapshotReader(tmp_path, clock_ms=lambda: 1_000).read()


@pytest.mark.parametrize(
    "state_patch",
    [
        {"enabled": "true"},
        {"mode": "unknown-mode"},
        {"assets": {}},
        {"incidents": {}},
        {"run_count": -1},
        {"generated_ts_ms": 0},
        {"run_count": 1, "last_run_ts_ms": None},
    ],
)
def test_reader_rejects_invalid_minimum_state_schema(tmp_path, state_patch) -> None:
    state = _valid_state()
    state.update(state_patch)
    _write_evidence(tmp_path, state=state, events=[])

    with pytest.raises(SecurityImmuneSnapshotCorruptError) as raised:
        SecurityImmuneSnapshotReader(tmp_path, clock_ms=lambda: NOW_MS).read()

    assert raised.value.reason_code == SECURITY_IMMUNE_EVIDENCE_CORRUPT
    assert raised.value.source_component == "state"


def test_reader_module_has_no_agent_provider_or_writer_dependency() -> None:
    import host_brain.app.observability_runtime.security_immune_snapshot_reader as source

    module_source = inspect.getsource(source)

    assert "host_brain.security_immune.agent" not in module_source
    assert "host_brain.security_immune.providers" not in module_source
    assert "EvidenceStore(" not in module_source
    assert ".write_text(" not in module_source
    assert ".open(\"a\"" not in module_source
