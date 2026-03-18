import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import UI_CONFIDENCE_LOW, UI_ELEMENT_NOT_FOUND, VISUAL_REDLINE
from host_brain.lsp.vault import ArtifactVault
from host_brain.skills.impl.clawd_ui_action import (
    MIN_CONFIDENCE,
    S_CLAWD_UI_EXEC,
    S_CLAWD_UI_GUARD,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    execute_clawd_ui_action,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vision"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestClawdUIContract(unittest.TestCase):
    def test_t_41_low_confidence_blocks_action_and_logs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="clawd_t41_") as temp_dir:
            ledger = SQLiteLedger(db_path=Path(temp_dir) / "audit.sqlite3")
            vault = ArtifactVault(root_dir=Path(temp_dir) / "vault", ledger=ledger)
            payload = _load_json(FIXTURES / "bad" / "clawd_ui_low_confidence.json")

            result = execute_clawd_ui_action(
                request_id=payload["request_id"],
                intent_id=payload["intent_id"],
                target_desc=payload["target_desc"],
                action_name=payload["action_name"],
                confidence=payload["confidence"],
                element_found=payload["element_found"],
                redline_detected=payload["redline_detected"],
                before_snapshot=payload["before_snapshot"],
                after_snapshot=payload["after_snapshot"],
                ledger=ledger,
                vault=vault,
                evidence_refs=payload["evidence_refs"],
                ts_ms=1_800_000_200_000,
            )

            self.assertFalse(result.success)
            self.assertEqual(STATUS_REJECTED, result.status)
            self.assertEqual(UI_CONFIDENCE_LOW, result.reason_code)
            self.assertEqual(S_CLAWD_UI_GUARD, result.stage)
            self.assertIn(f"{MIN_CONFIDENCE:.2f}", result.detail)
            self.assertIsNone(result.manifest_ref)

            self.assertEqual(1, len(ledger.clawd_action_log))
            row = ledger.clawd_action_log[0]
            self.assertEqual(STATUS_REJECTED, row["status"])
            self.assertEqual(UI_CONFIDENCE_LOW, row["reason_code"])
            self.assertEqual(S_CLAWD_UI_GUARD, row["stage"])
            self.assertEqual(payload["target_desc"], row["target_desc"])
            self.assertAlmostEqual(payload["confidence"], float(row["confidence"]), places=6)

            manifest_dir = Path(temp_dir) / "vault" / "manifests"
            self.assertEqual([], list(manifest_dir.glob("manifest_*.json")))
            ledger.close()

    def test_t_42_redline_and_missing_element_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="clawd_t42_") as temp_dir:
            ledger = SQLiteLedger(db_path=Path(temp_dir) / "audit.sqlite3")
            vault = ArtifactVault(root_dir=Path(temp_dir) / "vault", ledger=ledger)
            redline = _load_json(FIXTURES / "bad" / "clawd_ui_redline.json")
            missing = _load_json(FIXTURES / "bad" / "clawd_ui_element_missing.json")

            redline_result = execute_clawd_ui_action(
                request_id=redline["request_id"],
                intent_id=redline["intent_id"],
                target_desc=redline["target_desc"],
                action_name=redline["action_name"],
                confidence=redline["confidence"],
                element_found=redline["element_found"],
                redline_detected=redline["redline_detected"],
                before_snapshot=redline["before_snapshot"],
                after_snapshot=redline["after_snapshot"],
                ledger=ledger,
                vault=vault,
                evidence_refs=redline["evidence_refs"],
                ts_ms=1_800_000_200_100,
            )
            missing_result = execute_clawd_ui_action(
                request_id=missing["request_id"],
                intent_id=missing["intent_id"],
                target_desc=missing["target_desc"],
                action_name=missing["action_name"],
                confidence=missing["confidence"],
                element_found=missing["element_found"],
                redline_detected=missing["redline_detected"],
                before_snapshot=missing["before_snapshot"],
                after_snapshot=missing["after_snapshot"],
                ledger=ledger,
                vault=vault,
                evidence_refs=missing["evidence_refs"],
                ts_ms=1_800_000_200_101,
            )

            self.assertFalse(redline_result.success)
            self.assertEqual(VISUAL_REDLINE, redline_result.reason_code)
            self.assertEqual(S_CLAWD_UI_GUARD, redline_result.stage)
            self.assertFalse(missing_result.success)
            self.assertEqual(UI_ELEMENT_NOT_FOUND, missing_result.reason_code)
            self.assertEqual(S_CLAWD_UI_GUARD, missing_result.stage)

            self.assertEqual(2, len(ledger.clawd_action_log))
            reasons = {row["reason_code"] for row in ledger.clawd_action_log}
            self.assertEqual({VISUAL_REDLINE, UI_ELEMENT_NOT_FOUND}, reasons)
            ledger.close()

    def test_t_43_success_has_before_after_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="clawd_t43_") as temp_dir:
            ledger = SQLiteLedger(db_path=Path(temp_dir) / "audit.sqlite3")
            vault = ArtifactVault(root_dir=Path(temp_dir) / "vault", ledger=ledger)
            payload = _load_json(FIXTURES / "good" / "clawd_ui_action_success.json")

            result = execute_clawd_ui_action(
                request_id=payload["request_id"],
                intent_id=payload["intent_id"],
                target_desc=payload["target_desc"],
                action_name=payload["action_name"],
                confidence=payload["confidence"],
                element_found=payload["element_found"],
                redline_detected=payload["redline_detected"],
                before_snapshot=payload["before_snapshot"],
                after_snapshot=payload["after_snapshot"],
                ledger=ledger,
                vault=vault,
                evidence_refs=payload["evidence_refs"],
                ts_ms=1_800_000_200_200,
            )

            self.assertTrue(result.success)
            self.assertEqual(STATUS_EXECUTED, result.status)
            self.assertEqual(S_CLAWD_UI_EXEC, result.stage)
            self.assertIsNone(result.reason_code)
            self.assertTrue(result.before_artifact_ref and result.before_artifact_ref.startswith("artifact::"))
            self.assertTrue(result.after_artifact_ref and result.after_artifact_ref.startswith("artifact::"))
            self.assertNotEqual(result.before_artifact_ref, result.after_artifact_ref)
            self.assertTrue(result.manifest_ref and result.manifest_ref.startswith("manifest::"))

            self.assertEqual(1, len(ledger.clawd_action_log))
            row = ledger.clawd_action_log[0]
            self.assertEqual(STATUS_EXECUTED, row["status"])
            self.assertEqual(S_CLAWD_UI_EXEC, row["stage"])
            self.assertEqual(payload["target_desc"], row["target_desc"])
            self.assertEqual(result.before_artifact_ref, row["before_artifact_ref"])
            self.assertEqual(result.after_artifact_ref, row["after_artifact_ref"])
            self.assertEqual(result.manifest_ref, row["manifest_ref"])

            self.assertEqual(1, len(ledger.vault_manifest_index))
            manifest_id = ledger.vault_manifest_index[0]["manifest_id"]
            manifest_path = Path(temp_dir) / "vault" / "manifests" / f"{manifest_id}.json"
            self.assertTrue(manifest_path.exists())
            self.assertEqual(2, ledger.vault_manifest_index[0]["items_count"])
            ledger.close()


if __name__ == "__main__":
    unittest.main()
