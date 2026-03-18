import json
import unittest
from pathlib import Path

from host_brain.core.errors import (
    CHROMA_COLLECTION_MISSING,
    CHROMA_ENDPOINT_UNAVAILABLE,
    CHROMA_VECTOR_DIM_MISMATCH,
    CONFIG_VALUE_INVALID,
)
from host_brain.memory.chroma_client import (
    CHROMA_STATUS_DOWN,
    CHROMA_STATUS_UP,
    S_CHROMA_CONFIG,
    S_CHROMA_PROBE,
    InMemoryChromaClient,
    build_chroma_dependency_status,
    build_chroma_probe_report,
    load_chroma_runtime_config,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "retrieval" / "chroma"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestChromaDeployContract(unittest.TestCase):
    def test_t_chroma_01_runtime_config_and_collection_profiles_are_parseable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "chroma_runtime_config.json")
        result = load_chroma_runtime_config(env=fixture["env"])
        self.assertTrue(result.success, msg=result.as_dict())
        self.assertEqual(S_CHROMA_CONFIG, result.stage)
        assert result.config is not None
        self.assertTrue(result.config.enabled)
        self.assertEqual("https://chroma.Natsume.ai", result.config.endpoint)
        self.assertEqual("tenant_Natsume", result.config.tenant)
        self.assertEqual("memory_prod", result.config.database)
        self.assertEqual({"episodic": 16, "world": 32}, result.config.collection_profiles)
        self.assertEqual("episodic", result.config.default_collection)

    def test_t_chroma_01_invalid_collection_profile_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "chroma_invalid_profiles.json")
        result = load_chroma_runtime_config(env=fixture["env"])
        self.assertFalse(result.success)
        self.assertEqual(S_CHROMA_CONFIG, result.stage)
        self.assertEqual(CONFIG_VALUE_INVALID, result.reason_code)

    def test_t_chroma_01_probe_failure_mapping_is_stable(self) -> None:
        config = load_chroma_runtime_config(env=_load_json(FIXTURES / "good" / "chroma_runtime_config.json")["env"]).config
        assert config is not None
        endpoint_down = _load_json(FIXTURES / "bad" / "chroma_endpoint_down_probe.json")
        collection_missing = _load_json(FIXTURES / "bad" / "chroma_collection_missing_probe.json")
        dim_mismatch = _load_json(FIXTURES / "bad" / "chroma_vector_dim_mismatch_probe.json")

        report_down = build_chroma_probe_report(
            config=config,
            request_id="req_chroma_001",
            probe=endpoint_down["probe"],
            ts_ms=1_800_140_000_000,
        )
        report_missing = build_chroma_probe_report(
            config=config,
            request_id="req_chroma_002",
            probe=collection_missing["probe"],
            ts_ms=1_800_140_000_001,
        )
        report_dim = build_chroma_probe_report(
            config=config,
            request_id="req_chroma_003",
            probe=dim_mismatch["probe"],
            ts_ms=1_800_140_000_002,
        )

        self.assertEqual(S_CHROMA_PROBE, report_down["stage"])
        self.assertEqual(CHROMA_STATUS_DOWN, report_down["status"])
        self.assertEqual(CHROMA_ENDPOINT_UNAVAILABLE, report_down["reason_code"])
        self.assertEqual("endpoint_down", report_down["failure_class"])

        self.assertEqual(CHROMA_STATUS_DOWN, report_missing["status"])
        self.assertEqual(CHROMA_COLLECTION_MISSING, report_missing["reason_code"])
        self.assertEqual("collection_missing", report_missing["failure_class"])

        self.assertEqual(CHROMA_STATUS_DOWN, report_dim["status"])
        self.assertEqual(CHROMA_VECTOR_DIM_MISMATCH, report_dim["reason_code"])
        self.assertEqual("vector_dim_mismatch", report_dim["failure_class"])

        dep_status = build_chroma_dependency_status(report_dim)
        self.assertFalse(dep_status["available"])
        self.assertEqual("dependency", dep_status["unavailable_kind"])

    def test_t_chroma_01_collection_governance_enforces_vector_dim(self) -> None:
        client = InMemoryChromaClient(
            collection_profiles={"episodic": 16, "world": 32},
            default_collection="episodic",
        )
        vector16 = [0.1] * 16
        client.upsert(memory_id="mem_001", text="hello", embedding=vector16, metadata={"scope": "episodic"})
        row = client.get("mem_001")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual("episodic", row.collection)

        with self.assertRaises(ValueError) as ctx_dim:
            client.upsert(
                memory_id="mem_002",
                text="world vector mismatch",
                embedding=vector16,
                metadata={"scope": "world"},
                collection="world",
            )
        self.assertEqual(CHROMA_VECTOR_DIM_MISMATCH, str(ctx_dim.exception))

        with self.assertRaises(ValueError) as ctx_missing:
            client.search(query_embedding=vector16, collection="unknown_collection")
        self.assertEqual(CHROMA_COLLECTION_MISSING, str(ctx_missing.exception))

        ok_report = build_chroma_probe_report(
            config=load_chroma_runtime_config(env=_load_json(FIXTURES / "good" / "chroma_runtime_config.json")["env"]).config,
            request_id="req_chroma_004",
            probe={
                "reachable": True,
                "collections": {
                    "episodic": {"vector_dim": 16},
                    "world": {"vector_dim": 32},
                },
            },
            ts_ms=1_800_140_000_003,
        )
        self.assertEqual(CHROMA_STATUS_UP, ok_report["status"])
        self.assertIsNone(ok_report["reason_code"])


if __name__ == "__main__":
    unittest.main()
