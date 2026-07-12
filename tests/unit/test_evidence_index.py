"""T-EVP-IDX-01~06: catalog-owned Evidence Plane index contract."""

from __future__ import annotations

from pathlib import Path
import unittest

from host_brain.persistence.graph_evidence_repository import GraphEvidenceRepository


ASSET = (
    Path(__file__).resolve().parents[2]
    / "host_brain"
    / "audit"
    / "migrations"
    / "0074_graph_evidence_persistence.sql"
)


class TestEvidenceIndexM41(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = ASSET.read_text(encoding="utf-8")

    def _assert_index(self, name: str) -> None:
        self.assertIn(f"CREATE INDEX IF NOT EXISTS {name}", self.sql)

    def test_barrier_join_log_run_id_index(self) -> None:
        self._assert_index("idx_bjl_run_id")

    def test_barrier_join_log_ts_index(self) -> None:
        self._assert_index("idx_bjl_ts")

    def test_subgraph_trace_run_id_index(self) -> None:
        self._assert_index("idx_sgt_run_id")

    def test_subgraph_trace_parent_path_index(self) -> None:
        self._assert_index("idx_sgt_parent_path")

    def test_llm_call_log_run_id_index(self) -> None:
        self._assert_index("idx_lcl_run_id")

    def test_llm_call_log_node_id_index(self) -> None:
        self._assert_index("idx_lcl_node_id")

    def test_schema_verification_is_idempotent(self) -> None:
        repository = GraphEvidenceRepository(":memory:")
        try:
            repository.assert_schema_ready(require_catalog=False)
            repository.assert_schema_ready(require_catalog=False)
        finally:
            repository.close()


if __name__ == "__main__":
    unittest.main()
