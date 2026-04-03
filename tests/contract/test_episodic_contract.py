import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import MEMORY_EVIDENCE_REQUIRED
from host_brain.memory.episodic import EpisodicStore
from tests.fakes.chroma_client import FakeChromaClient


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "memory"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestEpisodicContract(unittest.TestCase):
    def test_t_mem_01_write_requires_evidence_refs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="episodic_t_mem01_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            ledger = SQLiteLedger(db_path=db_path)
            try:
                store = EpisodicStore(ledger=ledger, chroma_client=FakeChromaClient())
                payload = _load_json(FIXTURES / "bad" / "no_evidence_write.json")

                result = store.write_memory(
                    text=payload["text"],
                    evidence_refs=payload["evidence_refs"],
                    request_id="req_mem_01",
                    intent_id="intent_mem_01",
                    ts_ms=1000,
                )

                self.assertFalse(result.success)
                self.assertEqual(MEMORY_EVIDENCE_REQUIRED, result.reason_code)
                self.assertEqual(1, len(ledger.memory_ops_log))
                row = ledger.memory_ops_log[0]
                self.assertEqual("WRITE", row["op_kind"])
                self.assertEqual("REJECTED", row["status"])
                self.assertEqual(MEMORY_EVIDENCE_REQUIRED, row["reason_code"])
                self.assertEqual([], row["evidence_refs"])
            finally:
                ledger.close()

    def test_t_mem_02_lifecycle_ops_are_replayable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="episodic_t_mem02_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            ledger = SQLiteLedger(db_path=db_path)
            try:
                store = EpisodicStore(ledger=ledger, chroma_client=FakeChromaClient())
                fixture = _load_json(FIXTURES / "good" / "lifecycle_ops.json")

                write_a = store.write_memory(
                    text=fixture["write_a"]["text"],
                    evidence_refs=fixture["write_a"]["evidence_refs"],
                    request_id="req_mem_02",
                    intent_id="intent_mem_write_a",
                    ts_ms=2000,
                )
                write_b = store.write_memory(
                    text=fixture["write_b"]["text"],
                    evidence_refs=fixture["write_b"]["evidence_refs"],
                    request_id="req_mem_02",
                    intent_id="intent_mem_write_b",
                    ts_ms=2001,
                )
                self.assertTrue(write_a.success)
                self.assertTrue(write_b.success)
                assert write_a.memory_id is not None
                assert write_b.memory_id is not None

                merged = store.merge_memories(
                    source_memory_ids=[write_a.memory_id, write_b.memory_id],
                    evidence_refs=fixture["merge"]["evidence_refs"],
                    request_id="req_mem_02",
                    intent_id="intent_mem_merge",
                    ts_ms=2002,
                )
                self.assertTrue(merged.success)
                assert merged.memory_id is not None

                decayed = store.decay_memory(
                    memory_id=merged.memory_id,
                    decay_profile_id=fixture["decay"]["decay_profile_id"],
                    evidence_refs=fixture["decay"]["evidence_refs"],
                    request_id="req_mem_02",
                    intent_id="intent_mem_decay",
                    ts_ms=2003,
                )
                deleted = store.delete_memory(
                    memory_id=write_a.memory_id,
                    evidence_refs=fixture["delete"]["evidence_refs"],
                    request_id="req_mem_02",
                    intent_id="intent_mem_delete",
                    ts_ms=2004,
                )
                self.assertTrue(decayed.success)
                self.assertTrue(deleted.success)

                replay = store.replay_ops()
                self.assertEqual(5, len(replay), msg=replay)
                self.assertEqual(
                    ["WRITE", "WRITE", "MERGE", "DECAY", "DELETE"],
                    [row["op_kind"] for row in replay],
                )
                self.assertTrue(all(isinstance(row.get("evidence_refs"), list) and row["evidence_refs"] for row in replay))

                merge_row = replay[2]
                self.assertEqual("SUCCESS", merge_row["status"])
                self.assertEqual(merged.memory_id, merge_row["memory_id"])
                self.assertEqual(
                    {write_a.memory_id, write_b.memory_id},
                    set(merge_row["source_memory_ids"]),
                )

                source_replay = store.replay_ops(memory_id=write_a.memory_id)
                self.assertEqual(["WRITE", "MERGE", "DELETE"], [row["op_kind"] for row in source_replay])
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
