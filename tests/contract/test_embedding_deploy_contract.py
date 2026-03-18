import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CONFIG_VALUE_INVALID,
    RETR_EMBED_INPUT_INVALID,
    RETR_EMBED_TIMEOUT,
    RETR_EMBED_UNAVAILABLE,
)
from host_brain.retrieval.embedding import (
    S_RET_EMBED_CONFIG,
    S_RET_EMBEDDING,
    STATUS_DEGRADED,
    STATUS_OK,
    EmbeddingServiceContract,
    load_embedding_runtime_config,
    resolve_embedding_route,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "retrieval" / "embedding"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestEmbeddingDeployContract(unittest.TestCase):
    def test_t_embed_01_runtime_config_and_route_selection_are_deterministic(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "runtime_config.json")
        result = load_embedding_runtime_config(env=fixture["env"])

        self.assertTrue(result.success, msg=result.as_dict())
        self.assertEqual(S_RET_EMBED_CONFIG, result.stage)
        assert result.config is not None
        self.assertTrue(result.config.enabled)
        self.assertEqual("host_default", result.config.default_route)

        host_route = resolve_embedding_route(result.config, node_id="host-01")
        gpu_route = resolve_embedding_route(result.config, node_id="node-gpu-01")
        fallback_route = resolve_embedding_route(result.config, node_id="node-unknown")

        self.assertEqual("host_default", host_route.route_id)
        self.assertEqual("builtin_hash", host_route.provider)
        self.assertEqual(16, host_route.dim)

        self.assertEqual("gpu_route", gpu_route.route_id)
        self.assertEqual("mock_openai", gpu_route.provider)
        self.assertEqual("text-embedding-3-large", gpu_route.model_id)

        self.assertEqual("host_default", fallback_route.route_id)

    def test_t_embed_01_invalid_routes_json_is_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "runtime_invalid_routes.json")
        result = load_embedding_runtime_config(env=fixture["env"])

        self.assertFalse(result.success)
        self.assertEqual(S_RET_EMBED_CONFIG, result.stage)
        self.assertEqual(CONFIG_VALUE_INVALID, result.reason_code)

    def test_t_embed_01_reason_mapping_and_dim_drift_audit_are_stable(self) -> None:
        good = _load_json(FIXTURES / "good" / "runtime_config.json")
        unavailable_req = _load_json(FIXTURES / "bad" / "runtime_provider_unavailable.json")
        timeout_req = _load_json(FIXTURES / "bad" / "runtime_timeout.json")
        dim_drift_fixture = _load_json(FIXTURES / "bad" / "runtime_dim_drift.json")

        config = load_embedding_runtime_config(env=good["env"]).config
        assert config is not None

        def _provider_unavailable(_text: str) -> tuple[list[float], str]:
            raise RuntimeError("provider down")

        def _provider_timeout(_text: str) -> tuple[list[float], str]:
            raise TimeoutError("provider timeout")

        ledger = SQLiteLedger(db_path=":memory:")
        try:
            unavailable_svc = EmbeddingServiceContract(
                ledger=ledger,
                runtime_config=config,
                provider_registry={"mock_openai": _provider_unavailable},
                node_id=unavailable_req["node_id"],
                available=True,
            )
            ua_1 = unavailable_svc.embed(
                request_id=unavailable_req["request_id"],
                text=unavailable_req["text"],
                ts_ms=1_800_150_000_000,
            )
            ua_2 = unavailable_svc.embed(
                request_id="req_embed_rt_001_repeat",
                text=unavailable_req["text"],
                ts_ms=1_800_150_000_001,
            )

            self.assertFalse(ua_1.success)
            self.assertFalse(ua_2.success)
            self.assertEqual(STATUS_DEGRADED, ua_1.status)
            self.assertEqual(RETR_EMBED_UNAVAILABLE, ua_1.reason_code)
            self.assertEqual(ua_1.reason_code, ua_2.reason_code)
            self.assertEqual("gpu_route", ua_1.output["route_id"])
            self.assertEqual("mock_openai", ua_1.output["provider"])

            timeout_svc = EmbeddingServiceContract(
                ledger=ledger,
                runtime_config=config,
                provider_registry={"mock_openai": _provider_timeout},
                node_id=timeout_req["node_id"],
                available=True,
            )
            timeout = timeout_svc.embed(
                request_id=timeout_req["request_id"],
                text=timeout_req["text"],
                ts_ms=1_800_150_000_002,
            )
            self.assertFalse(timeout.success)
            self.assertEqual(RETR_EMBED_TIMEOUT, timeout.reason_code)
            self.assertEqual(S_RET_EMBEDDING, timeout.stage)

            dim_drift_config = load_embedding_runtime_config(env=dim_drift_fixture["env"]).config
            assert dim_drift_config is not None
            dim_svc = EmbeddingServiceContract(
                ledger=ledger,
                runtime_config=dim_drift_config,
                node_id=dim_drift_fixture["node_id"],
                available=True,
            )
            dim = dim_svc.embed(
                request_id=dim_drift_fixture["request_id"],
                text=dim_drift_fixture["text"],
                ts_ms=1_800_150_000_003,
            )
            self.assertFalse(dim.success)
            self.assertEqual(RETR_EMBED_INPUT_INVALID, dim.reason_code)
            self.assertIn("expected=32, got=16", dim.detail)
            self.assertEqual(32, dim.output["expected_dim"])
            self.assertEqual(16, dim.output["actual_dim"])

            self.assertTrue(
                any(
                    row["reason_code"] == RETR_EMBED_INPUT_INVALID
                    and row["payload"].get("expected_dim") == 32
                    and row["payload"].get("embedding_dim") == 16
                    for row in ledger.retrieval_event_log
                ),
                msg=ledger.retrieval_event_log,
            )

            success_svc = EmbeddingServiceContract(
                ledger=ledger,
                runtime_config=config,
                provider_registry={
                    "mock_openai": lambda _text: ([0.1] * 16, "text-embedding-3-large"),
                },
                node_id="node-gpu-01",
                available=True,
            )
            success = success_svc.embed(
                request_id="req_embed_rt_004",
                text="embedding success on gpu route",
                ts_ms=1_800_150_000_004,
            )
            self.assertTrue(success.success)
            self.assertEqual(STATUS_OK, success.status)
            self.assertEqual("gpu_route", success.output["route_id"])
            self.assertEqual("mock_openai", success.output["provider"])
            self.assertEqual(16, success.output["actual_dim"])
        finally:
            ledger.close()


if __name__ == "__main__":
    unittest.main()
