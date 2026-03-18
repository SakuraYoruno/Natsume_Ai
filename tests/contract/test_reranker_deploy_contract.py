import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CONFIG_VALUE_INVALID,
    RETR_RERANK_INPUT_INVALID,
    RETR_RERANK_TIMEOUT,
    RETR_RERANK_UNAVAILABLE,
)
from host_brain.retrieval.reranker import (
    S_RET_RERANK,
    S_RET_RERANK_CONFIG,
    STATUS_DEGRADED,
    STATUS_OK,
    RerankerServiceContract,
    load_reranker_runtime_config,
    resolve_reranker_route,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "retrieval" / "reranker"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestRerankerDeployContract(unittest.TestCase):
    def test_t_rerank_01_runtime_config_and_route_selection_are_deterministic(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "runtime_config.json")
        result = load_reranker_runtime_config(env=fixture["env"])

        self.assertTrue(result.success, msg=result.as_dict())
        self.assertEqual(S_RET_RERANK_CONFIG, result.stage)
        assert result.config is not None
        self.assertTrue(result.config.enabled)
        self.assertEqual("host_default", result.config.default_route)

        host_route = resolve_reranker_route(result.config, node_id="host-01")
        gpu_route = resolve_reranker_route(result.config, node_id="node-gpu-01")
        fallback_route = resolve_reranker_route(result.config, node_id="node-unknown")

        self.assertEqual("host_default", host_route.route_id)
        self.assertEqual("heuristic", host_route.provider)
        self.assertEqual(5, host_route.top_k)

        self.assertEqual("gpu_route", gpu_route.route_id)
        self.assertEqual("mock_cross_encoder", gpu_route.provider)
        self.assertEqual("bge-reranker-large", gpu_route.model_id)
        self.assertEqual(3, gpu_route.top_k)

        self.assertEqual("host_default", fallback_route.route_id)

    def test_t_rerank_01_invalid_routes_json_is_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "runtime_invalid_routes.json")
        result = load_reranker_runtime_config(env=fixture["env"])

        self.assertFalse(result.success)
        self.assertEqual(S_RET_RERANK_CONFIG, result.stage)
        self.assertEqual(CONFIG_VALUE_INVALID, result.reason_code)

    def test_t_rerank_01_reason_mapping_and_score_fallback_are_stable(self) -> None:
        good = _load_json(FIXTURES / "good" / "runtime_config.json")
        model_down_req = _load_json(FIXTURES / "bad" / "runtime_model_down.json")
        timeout_req = _load_json(FIXTURES / "bad" / "runtime_timeout.json")
        bad_score_req = _load_json(FIXTURES / "bad" / "runtime_bad_score.json")

        config = load_reranker_runtime_config(env=good["env"]).config
        assert config is not None

        def _model_down(_query: str, _candidates: list[dict]) -> tuple[list[float], str]:
            raise RuntimeError("model down")

        def _model_timeout(_query: str, _candidates: list[dict]) -> tuple[list[float], str]:
            raise TimeoutError("model timeout")

        def _bad_score(_query: str, _candidates: list[dict]) -> tuple[list[float], str]:
            return [1.5, -0.2], "bge-reranker-large"

        ledger = SQLiteLedger(db_path=":memory:")
        try:
            down_svc = RerankerServiceContract(
                ledger=ledger,
                runtime_config=config,
                provider_registry={"mock_cross_encoder": _model_down},
                node_id=model_down_req["node_id"],
                available=True,
            )
            down_1 = down_svc.rerank(
                request_id=model_down_req["request_id"],
                query=model_down_req["query"],
                candidates=model_down_req["candidates"],
                ts_ms=1_800_160_000_000,
            )
            down_2 = down_svc.rerank(
                request_id="req_rerank_rt_001_repeat",
                query=model_down_req["query"],
                candidates=model_down_req["candidates"],
                ts_ms=1_800_160_000_001,
            )

            self.assertFalse(down_1.success)
            self.assertFalse(down_2.success)
            self.assertEqual(STATUS_DEGRADED, down_1.status)
            self.assertEqual(RETR_RERANK_UNAVAILABLE, down_1.reason_code)
            self.assertEqual(down_1.reason_code, down_2.reason_code)
            self.assertEqual("gpu_route", down_1.output["route_id"])
            self.assertEqual("mock_cross_encoder", down_1.output["provider"])

            timeout_svc = RerankerServiceContract(
                ledger=ledger,
                runtime_config=config,
                provider_registry={"mock_cross_encoder": _model_timeout},
                node_id=timeout_req["node_id"],
                available=True,
            )
            timeout = timeout_svc.rerank(
                request_id=timeout_req["request_id"],
                query=timeout_req["query"],
                candidates=timeout_req["candidates"],
                ts_ms=1_800_160_000_002,
            )
            self.assertFalse(timeout.success)
            self.assertEqual(RETR_RERANK_TIMEOUT, timeout.reason_code)
            self.assertEqual(S_RET_RERANK, timeout.stage)

            bad_score_svc = RerankerServiceContract(
                ledger=ledger,
                runtime_config=config,
                provider_registry={"mock_cross_encoder": _bad_score},
                node_id=bad_score_req["node_id"],
                available=True,
            )
            bad_score = bad_score_svc.rerank(
                request_id=bad_score_req["request_id"],
                query=bad_score_req["query"],
                candidates=bad_score_req["candidates"],
                ts_ms=1_800_160_000_003,
            )
            self.assertFalse(bad_score.success)
            self.assertEqual(RETR_RERANK_INPUT_INVALID, bad_score.reason_code)
            self.assertIn("invalid score range", bad_score.detail)
            self.assertEqual([1.0, 0.5], bad_score.output["scores"])
            self.assertEqual(2, len(bad_score.output["topk"]))

            success_svc = RerankerServiceContract(
                ledger=ledger,
                runtime_config=config,
                provider_registry={
                    "mock_cross_encoder": lambda _q, _c: ([0.91, 0.44], "bge-reranker-large"),
                },
                node_id="node-gpu-01",
                available=True,
            )
            success = success_svc.rerank(
                request_id="req_rerank_rt_004",
                query=bad_score_req["query"],
                candidates=bad_score_req["candidates"],
                ts_ms=1_800_160_000_004,
            )
            self.assertTrue(success.success)
            self.assertEqual(STATUS_OK, success.status)
            self.assertEqual("gpu_route", success.output["route_id"])
            self.assertEqual("mock_cross_encoder", success.output["provider"])
            self.assertEqual(2, len(success.output["topk"]))

            self.assertTrue(
                any(
                    row["reason_code"] == RETR_RERANK_INPUT_INVALID
                    and row["payload"].get("expected_top_k") == 2
                    for row in ledger.retrieval_event_log
                ),
                msg=ledger.retrieval_event_log,
            )
        finally:
            ledger.close()


if __name__ == "__main__":
    unittest.main()
