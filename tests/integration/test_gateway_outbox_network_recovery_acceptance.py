from __future__ import annotations

from pathlib import Path

from scripts.runtime.run_gateway_outbox_network_recovery_acceptance import (
    run_acceptance,
)


ROOT = Path(__file__).resolve().parents[2]


def test_gateway_outbox_real_socket_recovery_and_unknown_effect_quarantine() -> None:
    receipt = run_acceptance(
        repository_root=ROOT,
        run_id="integration-gateway-outbox-network-recovery",
    )

    expected_head = max(
        (ROOT / "host_brain" / "audit" / "migrations").glob("*.sql"),
        key=lambda path: (int(path.name[:4]), path.name),
    )
    assert receipt["status"] == "PASS_LOCAL_PROTOCOL_RECOVERY_AND_FAIL_CLOSED"
    assert receipt["database_kind"] == "ISOLATED_REAL_SQLITE"
    assert receipt["migration_head"] == expected_head.name
    assert receipt["migration_head_version"] == int(expected_head.name[:4])

    safe = receipt["safe_pre_dispatch_recovery"]
    assert safe["status"] == "PASS"
    assert safe["pre_dispatch_status"] == "RETRY_WAIT"
    assert safe["pre_dispatch_reason_code"] == "GW_REPLY_ADAPTER_UNAVAILABLE"
    assert safe["pre_dispatch_network_request_count"] == 0
    assert safe["final_status"] == "ACKED"
    assert safe["attempt_count"] == 2
    assert safe["replay_count"] == 0
    assert safe["duplicate_acceptance"] == "DUPLICATE_SAME"
    assert safe["restart_duplicate_acceptance"] == "DUPLICATE_SAME"
    assert safe["loopback_http_request_count"] == 1
    assert [item["dispatch_started"] for item in safe["attempts"]] == [0, 1]

    uncertain = receipt["response_lost_unknown_effect"]
    assert uncertain["status"] == "PASS_FAIL_CLOSED"
    assert uncertain["final_status"] == "DLQ"
    assert uncertain["last_reason_code"] == (
        "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN"
    )
    assert uncertain["attempt_count"] == 1
    assert uncertain["replay_count"] == 0
    assert uncertain["plain_replay_reason_code"] == (
        "GATEWAY_TRANSPORT_REPLAY_REQUIRES_RECONCILIATION"
    )
    assert uncertain["duplicate_acceptance"] == "DUPLICATE_SAME"
    assert uncertain["restart_duplicate_acceptance"] == "DUPLICATE_SAME"
    assert uncertain["loopback_http_request_count"] == 1
    assert uncertain["adapter_or_platform_ack_attested"] is False
    assert [item["dispatch_started"] for item in uncertain["attempts"]] == [1]

    assert receipt["durable_state"]["integrity_check"] == "ok"
    assert receipt["durable_state"]["outbox_count"] == 2
    assert receipt["durable_state"]["transport_counts"]["outbox_dlq"] == 1
    assert receipt["physical_platform_delivery"] == (
        "NOT_TESTED_PLATFORM_CLIENT_UNAVAILABLE"
    )
    assert receipt["local_dlq_claimed_as_physical_delivery"] is False
    assert receipt["private_or_user_data_used"] is False
    assert receipt["temporary_database_removed"] is True
