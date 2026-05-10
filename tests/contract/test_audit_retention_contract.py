import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.retention import MS_DAY, RetentionPolicy, run_retention_policy
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.tokens import TokenService
from host_brain.lsp.vault import ArtifactVault


ROOT = Path(__file__).resolve().parents[2]
VAULT_FIX = ROOT / "tests" / "fixtures" / "vault" / "good" / "reversible_manifest_input.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestAuditRetentionContract(unittest.TestCase):
    def test_t_audit_ret_01_keeps_triptych_replay_chain_while_pruning_non_core_logs(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        request_id = "req_ret_001"
        intent_id = "intent_ret_001"

        ledger.log_intent(
            {
                "request_id": request_id,
                "thread_id": "thread_ret",
                "turn_id": "turn_ret",
                "intent_id": intent_id,
                "session_id": "session_ret",
                "seq": 1,
                "ts_ms": 1000,
                "nonce": "nonce_ret_001",
                "parse_stage": "LENIENT_OK",
                "normalized_json": {"id": intent_id},
                "proposed_intent_draft_json": {"id": intent_id},
                "lint_result": {"schema": [], "args": []},
            }
        )
        ledger.log_governor(
            {
                "request_id": request_id,
                "thread_id": "thread_ret",
                "turn_id": "turn_ret",
                "intent_id": intent_id,
                "decision": "APPROVE",
                "reason_code": None,
                "rule_trace": [{"gate_id": "R1_TRUST", "hit": False}],
                "tokens": {"approval_token_ref": "approval::ret"},
                "snapshot_refs": {"governance_snapshot": "runtime"},
                "stage": "S_INTENT_OK",
                "ts_ms": 1001,
            }
        )
        ledger.log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": "thread_ret",
                "turn_id": "turn_ret",
                "intent_id": intent_id,
                "skill_name": "read_only_status",
                "approval_token_ref": "approval::ret",
                "result_status": "SUCCESS",
                "result_code": "SKILL_EXECUTED",
                "result_digest": "ok",
                "evidence_ref": "evidence::ret::001",
                "manifest_ref": None,
                "ts_ms": 1002,
            }
        )
        ledger._insert(
            "error_log",
            {
                "request_id": request_id,
                "stage": "S_TEST",
                "reason_code": "ERR_OLD",
                "detail": "old error",
                "ts_ms": 1000,
            },
        )
        ledger._insert(
            "qa_violation_log",
            {
                "request_id": request_id,
                "code": "QA_OLD",
                "detail": "old qa",
                "field": None,
                "ts_ms": 1000,
            },
        )

        policy = RetentionPolicy(
            policy_profile_id="audit_ret_test",
            keep_ms_by_table={"error_log": 100, "qa_violation_log": 100},
            dry_run=False,
        )
        result = run_retention_policy(
            ledger=ledger,
            policy=policy,
            request_id="req_retention_exec_001",
            now_ts_ms=1_000_000,
        )

        self.assertTrue(result.success)
        self.assertEqual(1, result.deleted_counts.get("error_log"))
        self.assertEqual(1, result.deleted_counts.get("qa_violation_log"))
        self.assertGreaterEqual(result.protected_counts.get("intent_log", 0), 1)
        self.assertGreaterEqual(result.protected_counts.get("governor_log", 0), 1)
        self.assertGreaterEqual(result.protected_counts.get("skill_exec_log", 0), 1)

        self.assertEqual(1, len(ledger.intent_log))
        self.assertEqual(1, len(ledger.governor_log))
        self.assertEqual(1, len(ledger.skill_exec_log))
        self.assertFalse(any(row["reason_code"] == "ERR_OLD" for row in ledger.error_log))
        self.assertFalse(any(row["code"] == "QA_OLD" for row in ledger.qa_violation_log))

        trace = ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        ledger.close()

    def test_default_policy_prunes_high_volume_llm_call_logs(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        now_ts_ms = 20 * MS_DAY
        old_ts_ms = 1 * MS_DAY
        recent_ts_ms = 19 * MS_DAY

        ledger.log_llm_node_call(
            {
                "request_id": "req_llm_old",
                "node_id": "planner",
                "provider": "test",
                "model_id": "old-node",
                "status": "OK",
                "ts_ms": old_ts_ms,
            }
        )
        ledger.log_llm_node_call(
            {
                "request_id": "req_llm_recent",
                "node_id": "planner",
                "provider": "test",
                "model_id": "recent-node",
                "status": "OK",
                "ts_ms": recent_ts_ms,
            }
        )
        ledger.log_llm_pool_call(
            {
                "request_id": "req_pool_old",
                "scene_type": "chat",
                "cognitive_tier": "fast",
                "attempts_json": "[]",
                "attempt_count": 1,
                "final_model": "old-pool",
                "final_provider": "test",
                "ts_ms": old_ts_ms,
            }
        )
        ledger.log_llm_pool_call(
            {
                "request_id": "req_pool_recent",
                "scene_type": "chat",
                "cognitive_tier": "fast",
                "attempts_json": "[]",
                "attempt_count": 1,
                "final_model": "recent-pool",
                "final_provider": "test",
                "ts_ms": recent_ts_ms,
            }
        )

        result = run_retention_policy(
            ledger=ledger,
            policy=RetentionPolicy(),
            request_id="req_retention_exec_llm",
            now_ts_ms=now_ts_ms,
        )

        self.assertEqual(1, result.deleted_counts.get("llm_node_call_log"))
        self.assertEqual(1, result.deleted_counts.get("llm_pool_call_log"))
        node_rows = ledger.fetch_by_time_range("llm_node_call_log", limit=10)
        pool_rows = ledger.fetch_by_time_range("llm_pool_call_log", limit=10)
        self.assertEqual(["req_llm_recent"], [row["request_id"] for row in node_rows])
        self.assertEqual(["req_pool_recent"], [row["request_id"] for row in pool_rows])
        ledger.close()

    def test_t_audit_ret_01_vault_gc_respects_active_undo_window(self) -> None:
        fixture = _load_json(VAULT_FIX)
        with tempfile.TemporaryDirectory(prefix="audit_ret_vault_") as temp_dir:
            ledger = SQLiteLedger(db_path=Path(temp_dir) / "audit.sqlite3")
            token_service = TokenService(undo_ttl_ms=20_000)
            vault = ArtifactVault(root_dir=Path(temp_dir) / "vault", ledger=ledger)

            undo_token = token_service.issue_undo_token(
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
                request_id="req_ret_vault_001",
                reversible=True,
                undo_ref=undo_token,
                skill_name=fixture["skill_name"],
                session_id=fixture["session_id"],
                seq=fixture["seq"],
                ts_ms=2001,
            )
            self.assertTrue(created.success)
            assert created.manifest_id is not None

            policy = RetentionPolicy(
                policy_profile_id="audit_ret_vault",
                keep_ms_by_table={},
                vault_retention_ms=0,
                dry_run=False,
            )
            result = run_retention_policy(
                ledger=ledger,
                policy=policy,
                request_id="req_retention_exec_002",
                now_ts_ms=2002,
                vault=vault,
                token_service=token_service,
                evidence_refs=["evt_retention_001"],
            )

            self.assertIsNotNone(result.vault_gc)
            assert result.vault_gc is not None
            self.assertIn(created.manifest_id, result.vault_gc["blocked_manifest_ids"])
            manifest_path = Path(temp_dir) / "vault" / "manifests" / f"{created.manifest_id}.json"
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
