from __future__ import annotations

import hashlib
import hmac
import json

from scripts.runtime import run_a22_edge_fault_barge_matrix as edge_matrix
from scripts.runtime import run_a22_proactive_driver_matrix as proactive_matrix
from scripts.runtime.build_a22_evidence_manifest import _json_summary


def test_edge_envelope_signature_covers_request_metadata_and_payload() -> None:
    envelope = edge_matrix._build_envelope(
        request_id="req-a22",
        payload={"session_id": "session-a22", "text": "hello"},
        shared_key="local-test-key",
        ts_ms=123_456,
        nonce="nonce-a22",
    )

    canonical = json.dumps(
        {"session_id": "session-a22", "text": "hello"},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    material = f"req-a22\n123456\nnonce-a22\n{canonical}".encode("utf-8")
    expected = hmac.new(b"local-test-key", material, hashlib.sha256).hexdigest()

    assert envelope == {
        "request_id": "req-a22",
        "ts_ms": 123_456,
        "nonce": "nonce-a22",
        "payload": {"session_id": "session-a22", "text": "hello"},
        "signature": expected,
    }


def test_edge_hmac_key_resolves_direct_value_before_secret_reference(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EDGE_HMAC_SHARED_KEY=direct-key\n"
        "EDGE_HMAC_SHARED_KEY_REF=sec://edge/key\n",
        encoding="utf-8",
    )

    assert (
        edge_matrix._resolve_hmac_key(env_file=env_file, repo_root=tmp_path)
        == "direct-key"
    )


def test_edge_hmac_key_resolves_local_secret_reference(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EDGE_HMAC_SHARED_KEY=\nEDGE_HMAC_SHARED_KEY_REF=sec://edge/key\n",
        encoding="utf-8",
    )
    store = tmp_path / "configs" / "secrets" / "secret_store.runtime.local.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps({"sec://edge/key": "resolved-key"}),
        encoding="utf-8",
    )

    assert (
        edge_matrix._resolve_hmac_key(env_file=env_file, repo_root=tmp_path)
        == "resolved-key"
    )


def test_edge_summaries_keep_sync_scope_without_secret_material() -> None:
    health = edge_matrix._health_summary(
        {
            "event": {
                "status": "degraded",
                "resources": {"queue_depth": 0},
                "services": {"edge.rpc.barge_in": {"available": True}},
                "components": {
                    "tts": {
                        "health": {
                            "state": "READY",
                            "backend": "gpt_sovits",
                        }
                    },
                    "tts_playback_device": {
                        "health": {
                            "playing": False,
                            "last_playback_error": None,
                        }
                    },
                },
            }
        }
    )
    response = edge_matrix._response_summary(
        {
            "success": True,
            "status": "ACCEPTED",
            "event": {
                "stage": "S_VOICE_TTS_PLAYBACK",
                "event_type": "TTS_PLAYBACK_STARTED",
                "synchronization_id": "sync-a22",
            },
            "hmac_shared_key": "must-not-leak",
        }
    )

    assert health == {
        "edge_status": "degraded",
        "tts_state": "READY",
        "tts_backend": "gpt_sovits",
        "playback_playing": False,
        "playback_error": None,
        "queue_depth": 0,
        "barge_in_available": True,
    }
    assert response["synchronization_id"] == "sync-a22"
    assert "hmac_shared_key" not in response


def test_proactive_outbox_evidence_is_digest_only() -> None:
    rows = [
        {
            "outbox_id": "outbox-a22",
            "delivery_key": "delivery-a22",
            "destination": "proactive.gateway",
            "status": "PENDING",
            "payload_json": json.dumps(
                {
                    "raw_extra": {
                        "companion_proactive_decision": {
                            "decision_id": "decision-a22",
                            "content_digest": "sha256:content-a22",
                            "raw_content": "must-not-appear",
                        },
                        "affective_turn_plan": {
                            "plan_id": "plan-a22",
                            "expression_plan": {
                                "synchronization_id": "sync-a22"
                            },
                        },
                    }
                }
            ),
            "payload_digest": "payload-digest-a22",
            "authorization_digest": "authorization-digest-a22",
        }
    ]

    evidence = proactive_matrix._outbox_evidence(rows)

    assert evidence == [
        {
            "outbox_id": "outbox-a22",
            "delivery_key": "delivery-a22",
            "destination": "proactive.gateway",
            "status": "PENDING",
            "payload_digest": "payload-digest-a22",
            "authorization_digest": "authorization-digest-a22",
            "decision_id": "decision-a22",
            "plan_id": "plan-a22",
            "synchronization_id": "sync-a22",
            "content_digest": "sha256:content-a22",
        }
    ]
    assert "must-not-appear" not in json.dumps(evidence)


def test_matrix_case_status_is_derived_from_checks() -> None:
    assert edge_matrix._case(
        case_id="edge-pass",
        name="pass",
        passed=True,
        evidence={"proof": True},
    )["status"] == "PASS"
    assert proactive_matrix._case(
        case_id="proactive-fail",
        name="fail",
        passed=False,
        evidence={"proof": False},
    )["status"] == "FAIL"


def test_evidence_manifest_json_summary_is_allowlisted(tmp_path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": "example.v1",
                "status": "PASS",
                "repository_commit": "abc123",
                "case_count": 6,
                "secret": "must-not-be-indexed",
            }
        ),
        encoding="utf-8",
    )

    assert _json_summary(report) == {
        "json_parseable": True,
        "json_object": True,
        "schema_version": "example.v1",
        "status": "PASS",
        "repository_commit": "abc123",
        "case_count": 6,
    }
