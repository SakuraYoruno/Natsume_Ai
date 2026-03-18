import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import MANIFEST_INVALID, UNDO_REQUIRED
from host_brain.governor.tokens import TokenService
from host_brain.lsp.vault import ArtifactVault


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vault"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestVaultContract(unittest.TestCase):
    def test_t_vault_01_reversible_manifest_outputs_manifest_ref(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vault_t01_") as temp_dir:
            ledger = SQLiteLedger(db_path=Path(temp_dir) / "audit.sqlite3")
            tokens = TokenService(undo_ttl_ms=20_000)
            vault = ArtifactVault(root_dir=Path(temp_dir) / "vault", ledger=ledger)
            fixture = _load_json(FIXTURES / "good" / "reversible_manifest_input.json")

            missing_undo = vault.create_manifest(
                intent_id=fixture["intent_id"],
                items=fixture["items"],
                evidence_refs=fixture["evidence_refs"],
                policy_profile_id=fixture["policy_profile_id"],
                request_id="req_vault_001",
                reversible=True,
                undo_ref=None,
                skill_name=fixture["skill_name"],
                session_id=fixture["session_id"],
                seq=fixture["seq"],
                ts_ms=1000,
            )
            self.assertFalse(missing_undo.success)
            self.assertEqual(UNDO_REQUIRED, missing_undo.reason_code)

            undo_token = tokens.issue_undo_token(
                intent_id=fixture["intent_id"],
                skill_name=fixture["skill_name"],
                session_id=fixture["session_id"],
                seq=fixture["seq"],
                now_ts_ms=1000,
            )
            created = vault.create_manifest(
                intent_id=fixture["intent_id"],
                items=fixture["items"],
                evidence_refs=fixture["evidence_refs"],
                policy_profile_id=fixture["policy_profile_id"],
                request_id="req_vault_001",
                reversible=True,
                undo_ref=undo_token,
                skill_name=fixture["skill_name"],
                session_id=fixture["session_id"],
                seq=fixture["seq"],
                ts_ms=1001,
            )

            self.assertTrue(created.success)
            assert created.manifest_ref is not None
            self.assertTrue(created.manifest_ref.startswith("manifest::"))

            rows = ledger.vault_manifest_index
            active_rows = [row for row in rows if row["status"] == "ACTIVE"]
            self.assertEqual(1, len(active_rows), msg=rows)
            self.assertEqual(undo_token, active_rows[0]["undo_ref"])
            self.assertEqual(2, active_rows[0]["items_count"])
            ledger.close()

    def test_t_vault_02_digest_mismatch_rejects_undo_and_gc_keeps_window(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vault_t02_") as temp_dir:
            ledger = SQLiteLedger(db_path=Path(temp_dir) / "audit.sqlite3")
            tokens = TokenService(undo_ttl_ms=20_000)
            vault = ArtifactVault(root_dir=Path(temp_dir) / "vault", ledger=ledger)
            fixture = _load_json(FIXTURES / "good" / "reversible_manifest_input.json")

            undo_token = tokens.issue_undo_token(
                intent_id=fixture["intent_id"],
                skill_name=fixture["skill_name"],
                session_id=fixture["session_id"],
                seq=fixture["seq"],
                now_ts_ms=2000,
            )
            created = vault.create_manifest(
                intent_id=fixture["intent_id"],
                items=fixture["items"],
                evidence_refs=fixture["evidence_refs"],
                policy_profile_id=fixture["policy_profile_id"],
                request_id="req_vault_002",
                reversible=True,
                undo_ref=undo_token,
                skill_name=fixture["skill_name"],
                session_id=fixture["session_id"],
                seq=fixture["seq"],
                ts_ms=2001,
            )
            self.assertTrue(created.success)
            assert created.manifest_id is not None
            assert created.manifest_ref is not None

            manifest_path = Path(temp_dir) / "vault" / "manifests" / f"{created.manifest_id}.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            tamper = _load_json(FIXTURES / "bad" / "manifest_digest_tamper.json")
            payload[tamper["tamper_field"]] = "tampered"
            manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8-sig",
            )

            undo = vault.undo_manifest(
                manifest_ref=created.manifest_ref,
                undo_token_ref=undo_token,
                token_service=tokens,
                request_id="req_vault_undo",
                now_ts_ms=2002,
            )
            self.assertFalse(undo.success)
            self.assertEqual(MANIFEST_INVALID, undo.reason_code)
            self.assertTrue(
                any(
                    row["reason_code"] == MANIFEST_INVALID and row["stage"] == "S_VAULT_UNDO"
                    for row in ledger.error_log
                ),
                msg=ledger.error_log,
            )

            gc = vault.garbage_collect(
                policy_profile_id="vault_default",
                evidence_refs=["evt_vault_gc"],
                retention_ms=0,
                request_id="req_vault_gc",
                token_service=tokens,
                now_ts_ms=2003,
            )
            self.assertIn(created.manifest_id, gc["blocked_manifest_ids"])
            self.assertTrue(manifest_path.exists())

            self.assertTrue(
                any(
                    created.manifest_id in (row.get("blocked_manifest_ids") or [])
                    for row in ledger.vault_gc_log
                ),
                msg=ledger.vault_gc_log,
            )
            ledger.close()


if __name__ == "__main__":
    unittest.main()
