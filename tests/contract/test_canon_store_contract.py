import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.prompt_context import validate_prompt_context
from host_brain.memory.canon_style_store import StyleCanonStore
from host_brain.memory.canon_sync import (
    WORLD_CANON_INVALID_DOC,
    WORLD_CANON_VERSION_DRIFT,
    detect_world_version_drift,
    sync_world_canon_index,
)
from host_brain.memory.canon_world_store import WorldCanonStore, compute_world_canon_hash


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "canon"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestCanonStoreContract(unittest.TestCase):
    def test_t_canon_01_style_versioning_and_audit_trace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="canon_style_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            style_root = Path(temp_dir) / "style"

            ledger = SQLiteLedger(db_path=db_path)
            try:
                store = StyleCanonStore(style_root=style_root, ledger=ledger)

                seed = _load_json(FIXTURES / "style" / "good" / "style_seed.json")
                entry_v1 = store.upsert(
                    canon_id=seed["canon_id"],
                    content=seed["content"],
                    tags=seed["tags"],
                    proposal_id="proposal_style_001",
                    ts_ms=1000,
                )
                entry_v2 = store.upsert(
                    canon_id=seed["canon_id"],
                    content=seed["content"] + " Keep responses short.",
                    tags=seed["tags"],
                    proposal_id="proposal_style_002",
                    ts_ms=2000,
                )

                self.assertEqual(1, entry_v1.version)
                self.assertEqual(2, entry_v2.version)
                self.assertNotEqual(entry_v1.hash, entry_v2.hash)

                self.assertEqual(2, len(ledger.canon_proposal_log))
                self.assertEqual("proposal_style_002", ledger.canon_proposal_log[-1]["proposal_id"])
                self.assertEqual("style", ledger.canon_proposal_log[-1]["canon_kind"])
                self.assertEqual(2, ledger.canon_proposal_log[-1]["proposed_version"])

                style_changes = [
                    row
                    for row in ledger.canon_change_log
                    if row["canon_kind"] == "style" and row["target_id"] == seed["canon_id"]
                ]
                self.assertEqual(2, len(style_changes))
                self.assertEqual(1, style_changes[-1]["before_version"])
                self.assertEqual(2, style_changes[-1]["after_version"])
                self.assertEqual(entry_v1.hash, style_changes[-1]["before_hash"])
                self.assertEqual(entry_v2.hash, style_changes[-1]["after_hash"])
            finally:
                ledger.close()

    def test_t_canon_02_style_injection_and_fact_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="canon_style_guard_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            style_root = Path(temp_dir) / "style"

            ledger = SQLiteLedger(db_path=db_path)
            try:
                store = StyleCanonStore(style_root=style_root, ledger=ledger)

                bad = _load_json(FIXTURES / "style" / "bad" / "style_as_fact.json")
                with self.assertRaises(ValueError):
                    store.upsert(
                        canon_id=bad["canon_id"],
                        content=bad["content"],
                        tags=bad["tags"],
                        proposal_id="proposal_style_bad",
                    )

                seed = _load_json(FIXTURES / "style" / "good" / "style_seed.json")
                store.upsert(
                    canon_id=seed["canon_id"],
                    content=seed["content"],
                    tags=seed["tags"],
                    proposal_id="proposal_style_003",
                )
                style_pack = store.build_style_pack(seed["canon_id"])

                self.assertEqual("style_pack", style_pack["slot"])
                self.assertTrue(style_pack["tone_only"])
                self.assertTrue(style_pack["unsafe_as_fact"])
                self.assertNotIn("event_id", style_pack)
                self.assertNotIn("evidence_refs", style_pack)
            finally:
                ledger.close()

    def test_t_wcanon_01_sync_index_updates_sqlite_and_logs_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="canon_world_sync_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            world_root = Path(temp_dir) / "world"
            world_root.mkdir(parents=True, exist_ok=True)

            ledger = SQLiteLedger(db_path=db_path)
            try:
                payload = {
                    "doc_id": "academy_rules",
                    "version": 1,
                    "status": "active",
                    "title": "Academy Rules",
                    "tags": ["setting", "academy"],
                    "body": "Students must use registered lab stations during practice sessions.",
                    "updated_by": "proposal_world_seed",
                    "ts_ms": 1700000000000,
                }
                payload["hash"] = compute_world_canon_hash(payload)
                target = world_root / "academy_rules.json"
                target.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8-sig",
                )

                records = sync_world_canon_index(world_root=world_root, ledger=ledger, ts_ms=1700000000999)
                self.assertEqual(1, len(records))
                self.assertTrue(records[0].changed)

                indexed = ledger.get_world_canon_doc("academy_rules")
                self.assertIsNotNone(indexed)
                assert indexed is not None
                self.assertEqual(1, indexed["version"])
                self.assertEqual(payload["hash"], indexed["hash"])
                self.assertEqual("active", indexed["status"])

                world_changes = [
                    row
                    for row in ledger.canon_change_log
                    if row["canon_kind"] == "world" and row["target_id"] == "academy_rules"
                ]
                self.assertEqual(1, len(world_changes))
                self.assertEqual("canon_sync::academy_rules::v1", world_changes[0]["proposal_id"])
            finally:
                ledger.close()

    def test_t_wcanon_02_authoritative_injection_is_citation_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="canon_world_inject_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            world_root = Path(temp_dir) / "world"

            ledger = SQLiteLedger(db_path=db_path)
            try:
                store = WorldCanonStore(world_root=world_root, ledger=ledger)

                bad = _load_json(FIXTURES / "world" / "bad" / "world_as_fact.json")
                with self.assertRaises(ValueError):
                    store.upsert_document(
                        doc_id=bad["doc_id"],
                        title=bad["title"],
                        tags=bad["tags"],
                        body=bad["body"],
                        status=bad["status"],
                        proposal_id="proposal_world_bad",
                    )

                good = _load_json(FIXTURES / "world" / "good" / "academy_rules.json")
                store.upsert_document(
                    doc_id=good["doc_id"],
                    title=good["title"],
                    tags=good["tags"],
                    body=good["body"],
                    status=good["status"],
                    proposal_id="proposal_world_001",
                    ts_ms=1700000000100,
                )
                authoritative = store.build_authoritative_entry(good["doc_id"])

                self.assertEqual("authoritative", authoritative["slot"])
                self.assertEqual("world_canon", authoritative["kind"])
                self.assertTrue(authoritative["unsafe_as_fact"])
                self.assertEqual("citation_only", authoritative["reference_mode"])
                self.assertTrue(authoritative["citation"].startswith("world_canon:"))

                result = validate_prompt_context(
                    {
                        "hard_snapshot": {},
                        "bond_summary": {"stage": "FRIEND"},
                        "authoritative": [authoritative],
                        "retrieval": [],
                        "style_pack": {
                            "slot": "style_pack",
                            "tone_only": True,
                            "unsafe_as_fact": True,
                            "style_instruction": "Stay concise.",
                            "tags": [],
                        },
                        "constraints": {},
                        "tool_meta_pack": [],
                    }
                )
                self.assertTrue(result.valid, msg=result.as_dict())
            finally:
                ledger.close()

    def test_t_wcanon_03_version_drift_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="canon_world_drift_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            world_root = Path(temp_dir) / "world"

            ledger = SQLiteLedger(db_path=db_path)
            try:
                store = WorldCanonStore(world_root=world_root, ledger=ledger)

                good = _load_json(FIXTURES / "world" / "good" / "academy_rules.json")
                store.upsert_document(
                    doc_id=good["doc_id"],
                    title=good["title"],
                    tags=good["tags"],
                    body=good["body"],
                    status=good["status"],
                    proposal_id="proposal_world_002",
                    ts_ms=1700000000200,
                )
                sync_world_canon_index(world_root=world_root, ledger=ledger, ts_ms=1700000000300)

                tampered = _load_json(FIXTURES / "world" / "drift" / "academy_rules_tampered.json")
                target = world_root / "academy_rules.json"
                target.write_text(
                    json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8-sig",
                )

                drifts = detect_world_version_drift(world_root=world_root, ledger=ledger)
                self.assertEqual(1, len(drifts))
                self.assertEqual(WORLD_CANON_VERSION_DRIFT, drifts[0]["code"])
                self.assertEqual("academy_rules", drifts[0]["doc_id"])
            finally:
                ledger.close()

    def test_world_canon_invalid_file_is_not_silent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="canon_world_invalid_") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            world_root = Path(temp_dir) / "world"
            world_root.mkdir(parents=True, exist_ok=True)

            ledger = SQLiteLedger(db_path=db_path)
            try:
                broken_path = world_root / "broken.json"
                broken_path.write_text("{invalid_json", encoding="utf-8-sig")

                synced = sync_world_canon_index(world_root=world_root, ledger=ledger, ts_ms=1700000000400)
                self.assertEqual([], synced)
                self.assertTrue(
                    any(
                        row["reason_code"] == WORLD_CANON_INVALID_DOC
                        and row["stage"] == "S_CANON_SYNC"
                        for row in ledger.error_log
                    ),
                    msg=ledger.error_log,
                )

                drifts = detect_world_version_drift(world_root=world_root, ledger=ledger)
                self.assertTrue(
                    any(row["code"] == WORLD_CANON_INVALID_DOC for row in drifts),
                    msg=drifts,
                )
                self.assertTrue(
                    any(
                        row["reason_code"] == WORLD_CANON_INVALID_DOC
                        and row["stage"] == "S_CANON_DRIFT"
                        for row in ledger.error_log
                    ),
                    msg=ledger.error_log,
                )
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
