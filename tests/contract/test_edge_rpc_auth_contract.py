import json
import unittest
from pathlib import Path

from edge_client.app.schemas import S_EDGE_RPC_SCHEMA, EdgeRpcResponse, parse_edge_rpc_envelope
from edge_client.config import S_EDGE_CONFIG, load_edge_config
from edge_client.rpc.auth import NonceReplayCache, build_edge_rpc_signature, validate_edge_rpc_auth
from host_brain.core.errors import (
    CONFIG_VALUE_INVALID,
    EDGE_RPC_AUTH_EXPIRED,
    EDGE_RPC_AUTH_INVALID,
    EDGE_RPC_SCHEMA_INVALID,
    REPLAY_DETECTED,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "edge" / "auth"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestEdgeRpcEnvelopeContract(unittest.TestCase):
    def test_t_edge_rpc_01_schema_valid(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "valid_envelope_accept.json")
        result = parse_edge_rpc_envelope(fixture)
        self.assertTrue(result.success, msg=result.as_dict())
        self.assertEqual(S_EDGE_RPC_SCHEMA, result.stage)
        self.assertIsNone(result.reason_code)
        assert result.envelope is not None
        self.assertEqual("req_edge_auth_001", result.envelope.request_id)

    def test_t_edge_rpc_01_schema_missing_request_id_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "missing_request_id_reject.json")
        result = parse_edge_rpc_envelope(fixture)
        self.assertFalse(result.success)
        self.assertEqual(S_EDGE_RPC_SCHEMA, result.stage)
        self.assertEqual(EDGE_RPC_SCHEMA_INVALID, result.reason_code)

    def test_t_edge_rpc_01_response_envelope_is_stable(self) -> None:
        response = EdgeRpcResponse(
            success=False,
            status="REJECTED",
            reason_code=EDGE_RPC_AUTH_INVALID,
            detail="signature mismatch",
            event={"op": "speak"},
            ts_ms=1_800_100_000_000,
        )
        row = response.as_dict()
        self.assertEqual(
            {
                "success",
                "status",
                "reason_code",
                "detail",
                "event",
                "ts_ms",
            },
            set(row.keys()),
        )


class TestEdgeRpcAuthContract(unittest.TestCase):
    def test_t_edge_rpc_01_hmac_auth_passes_with_valid_signature(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "valid_envelope_accept.json")
        signature = build_edge_rpc_signature(
            request_id=fixture["request_id"],
            ts_ms=fixture["ts_ms"],
            nonce=fixture["nonce"],
            payload=fixture["payload"],
            shared_key="edge-shared-key",
        )
        fixture["signature"] = signature
        parsed = parse_edge_rpc_envelope(fixture)
        self.assertTrue(parsed.success, msg=parsed.as_dict())
        assert parsed.envelope is not None

        result = validate_edge_rpc_auth(
            envelope=parsed.envelope,
            shared_key="edge-shared-key",
            allow_insecure_auth=False,
            auth_window_ms=15_000,
            replay_cache=NonceReplayCache(window_ms=120_000),
            now_fn=lambda: 1_800_100_000_000,
        )
        self.assertTrue(result.success, msg=result.as_dict())
        self.assertFalse(result.insecure_auth)

    def test_t_edge_rpc_01_signature_mismatch_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "signature_mismatch_reject.json")
        parsed = parse_edge_rpc_envelope(fixture)
        self.assertTrue(parsed.success, msg=parsed.as_dict())
        assert parsed.envelope is not None

        result = validate_edge_rpc_auth(
            envelope=parsed.envelope,
            shared_key="edge-shared-key",
            allow_insecure_auth=False,
            auth_window_ms=15_000,
            replay_cache=NonceReplayCache(window_ms=120_000),
            now_fn=lambda: 1_800_100_000_000,
        )
        self.assertFalse(result.success)
        self.assertEqual(EDGE_RPC_AUTH_INVALID, result.reason_code)

    def test_t_edge_rpc_01_expired_ts_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "expired_ts_reject.json")
        signature = build_edge_rpc_signature(
            request_id=fixture["request_id"],
            ts_ms=fixture["ts_ms"],
            nonce=fixture["nonce"],
            payload=fixture["payload"],
            shared_key="edge-shared-key",
        )
        fixture["signature"] = signature
        parsed = parse_edge_rpc_envelope(fixture)
        self.assertTrue(parsed.success, msg=parsed.as_dict())
        assert parsed.envelope is not None

        result = validate_edge_rpc_auth(
            envelope=parsed.envelope,
            shared_key="edge-shared-key",
            allow_insecure_auth=False,
            auth_window_ms=15_000,
            replay_cache=NonceReplayCache(window_ms=120_000),
            now_fn=lambda: 1_800_100_020_001,
        )
        self.assertFalse(result.success)
        self.assertEqual(EDGE_RPC_AUTH_EXPIRED, result.reason_code)

    def test_t_edge_rpc_01_nonce_replay_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "replay_nonce_reject.json")
        signature = build_edge_rpc_signature(
            request_id=fixture["request_id"],
            ts_ms=fixture["ts_ms"],
            nonce=fixture["nonce"],
            payload=fixture["payload"],
            shared_key="edge-shared-key",
        )
        fixture["signature"] = signature
        parsed = parse_edge_rpc_envelope(fixture)
        self.assertTrue(parsed.success, msg=parsed.as_dict())
        assert parsed.envelope is not None

        cache = NonceReplayCache(window_ms=120_000)
        first = validate_edge_rpc_auth(
            envelope=parsed.envelope,
            shared_key="edge-shared-key",
            allow_insecure_auth=False,
            auth_window_ms=15_000,
            replay_cache=cache,
            now_fn=lambda: 1_800_100_000_000,
        )
        second = validate_edge_rpc_auth(
            envelope=parsed.envelope,
            shared_key="edge-shared-key",
            allow_insecure_auth=False,
            auth_window_ms=15_000,
            replay_cache=cache,
            now_fn=lambda: 1_800_100_000_001,
        )
        self.assertTrue(first.success, msg=first.as_dict())
        self.assertFalse(second.success)
        self.assertEqual(REPLAY_DETECTED, second.reason_code)

    def test_t_edge_rpc_01_insecure_auth_bypass(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "insecure_auth_bypass_accept.json")
        parsed = parse_edge_rpc_envelope(fixture)
        self.assertTrue(parsed.success, msg=parsed.as_dict())
        assert parsed.envelope is not None

        result = validate_edge_rpc_auth(
            envelope=parsed.envelope,
            shared_key="",
            allow_insecure_auth=True,
            auth_window_ms=15_000,
            replay_cache=NonceReplayCache(window_ms=120_000),
            now_fn=lambda: 1_800_100_000_000,
        )
        self.assertTrue(result.success, msg=result.as_dict())
        self.assertTrue(result.insecure_auth)


class TestEdgeRpcAuthConfigContract(unittest.TestCase):
    def test_t_edge_rpc_01_config_fields_parseable(self) -> None:
        result = load_edge_config(
            env={
                "Nastume_ENV": "test",
                "EDGE_RPC_AUTH_WINDOW_MS": "20000",
                "EDGE_RPC_NONCE_WINDOW_MS": "240000",
            }
        )
        self.assertTrue(result.success, msg=result.as_dict())
        self.assertEqual(S_EDGE_CONFIG, result.stage)
        assert result.config is not None
        self.assertEqual(20_000, result.config.rpc_auth_window_ms)
        self.assertEqual(240_000, result.config.rpc_nonce_window_ms)

    def test_t_edge_rpc_01_invalid_auth_window_rejected(self) -> None:
        result = load_edge_config(
            env={
                "Nastume_ENV": "test",
                "EDGE_RPC_AUTH_WINDOW_MS": "10",
            }
        )
        self.assertFalse(result.success)
        self.assertEqual(S_EDGE_CONFIG, result.stage)
        self.assertEqual(CONFIG_VALUE_INVALID, result.reason_code)
        self.assertTrue(any(issue.field == "EDGE_RPC_AUTH_WINDOW_MS" for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
