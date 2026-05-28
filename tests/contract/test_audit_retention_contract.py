import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.retention import MS_DAY, RetentionPolicy, build_retention_policy_from_env, run_retention_policy
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.tokens import TokenService
from host_brain.lsp.vault import ArtifactVault
from host_brain.memory.life_memory import LifeMemoryStore


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

    def test_protected_chain_can_be_pruned_when_explicitly_configured(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        request_id = "req_ret_protected_001"
        intent_id = "intent_ret_protected_001"

        ledger.log_intent(
            {
                "request_id": request_id,
                "thread_id": "thread_ret",
                "turn_id": "turn_ret",
                "intent_id": intent_id,
                "session_id": "session_ret",
                "seq": 1,
                "ts_ms": 1000,
                "nonce": "nonce_ret_protected_001",
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
                "skill_name": "old_skill",
                "result_status": "SUCCESS",
                "result_code": "OK",
                "result_digest": "ok",
                "evidence_ref": "evidence::old",
                "ts_ms": 1002,
            }
        )

        result = run_retention_policy(
            ledger=ledger,
            policy=RetentionPolicy(
                policy_profile_id="audit_ret_protected_delete",
                keep_ms_by_table={},
                protected_chain_keep_ms=100,
            ),
            request_id="req_retention_exec_protected",
            now_ts_ms=1_000_000,
        )

        self.assertEqual(1, result.protected_deleted_counts.get("intent_log"))
        self.assertEqual(1, result.protected_deleted_counts.get("governor_log"))
        self.assertEqual(1, result.protected_deleted_counts.get("skill_exec_log"))
        self.assertEqual([], ledger.intent_log)
        self.assertEqual([], ledger.governor_log)
        self.assertEqual([], ledger.skill_exec_log)
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

    def test_retention_prunes_high_volume_logs_by_max_rows_even_when_recent(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        for idx in range(5):
            ledger.log_llm_node_call(
                {
                    "request_id": f"req_recent_node_{idx}",
                    "node_id": "planner",
                    "provider": "test",
                    "model_id": "recent-node",
                    "status": "OK",
                    "ts_ms": 10_000 + idx,
                }
            )

        result = run_retention_policy(
            ledger=ledger,
            policy=RetentionPolicy(
                policy_profile_id="audit_ret_max_rows",
                keep_ms_by_table={},
                max_rows_by_table={"llm_node_call_log": 2},
                life_memory_keep_ms=None,
            ),
            request_id="req_retention_max_rows",
            now_ts_ms=20_000,
        )

        self.assertEqual(3, result.max_row_candidate_counts.get("llm_node_call_log"))
        self.assertEqual(3, result.max_row_deleted_counts.get("llm_node_call_log"))
        rows = ledger.fetch_by_time_range("llm_node_call_log", limit=10)
        self.assertEqual(["req_recent_node_3", "req_recent_node_4"], [row["request_id"] for row in rows])
        ledger.close()

    def test_retention_prunes_unimportant_old_life_memory_without_touching_chat_or_linked_episodes(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        store = LifeMemoryStore(ledger=ledger)

        low_old = store.upsert_episode(
            episode_kind="background_episode",
            evidence_refs=["scheduler://event/low-old"],
            primary_ref="scheduler://event/low-old",
            summary="low old background detail",
            salience=0.10,
            truth_state="VERIFIED",
            ts_ms=1000,
        )["episode_id"]
        store.attach_evidence(
            episode_id=low_old,
            evidence_kind="scheduler_event",
            source_ref="scheduler://event/low-old",
            content="detail that should be deleted",
            trust_level="VERIFIED",
            ts_ms=1000,
        )
        high_old = store.upsert_episode(
            episode_kind="background_episode",
            evidence_refs=["scheduler://event/high-old"],
            primary_ref="scheduler://event/high-old",
            summary="important old background detail",
            salience=0.90,
            truth_state="VERIFIED",
            ts_ms=1000,
        )["episode_id"]
        chat_old = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/chat-old"],
            primary_ref="chat://request/chat-old",
            summary="ordinary old chat",
            salience=0.10,
            truth_state="VERIFIED",
            ts_ms=1000,
        )["episode_id"]
        linked_old = store.upsert_episode(
            episode_kind="inner_episode",
            evidence_refs=["inner_world://event/linked-old"],
            primary_ref="inner_world://event/linked-old",
            summary="linked old inner detail",
            salience=0.10,
            truth_state="VERIFIED",
            ts_ms=1000,
        )["episode_id"]
        store.link_episode(
            source_episode_id=linked_old,
            target_episode_id=high_old,
            link_kind="reflection",
            ts_ms=1000,
        )
        recent_low = store.upsert_episode(
            episode_kind="inner_episode",
            evidence_refs=["inner_world://event/recent-low"],
            primary_ref="inner_world://event/recent-low",
            summary="recent low inner detail",
            salience=0.10,
            truth_state="VERIFIED",
            ts_ms=9950,
        )["episode_id"]

        result = run_retention_policy(
            ledger=ledger,
            policy=RetentionPolicy(
                policy_profile_id="life_memory_ret_test",
                keep_ms_by_table={},
                life_memory_keep_ms=100,
                life_memory_min_salience=0.40,
                life_memory_episode_kinds=("background_episode", "inner_episode"),
            ),
            request_id="req_retention_life_memory",
            now_ts_ms=10_000,
        )

        self.assertEqual(1, result.life_memory_candidate_count)
        self.assertEqual(1, result.life_memory_deleted_count)
        remaining_ids = {row["episode_id"] for row in ledger.life_episode}
        self.assertNotIn(low_old, remaining_ids)
        self.assertIn(high_old, remaining_ids)
        self.assertIn(chat_old, remaining_ids)
        self.assertIn(linked_old, remaining_ids)
        self.assertIn(recent_low, remaining_ids)
        self.assertFalse(any(row["episode_id"] == low_old for row in ledger.life_evidence))
        ledger.close()

    def test_env_policy_supports_dates_and_chat_memory_opt_in(self) -> None:
        policy = build_retention_policy_from_env(
            {
                "HOST_AUDIT_RETENTION_KEEP_DAYS": "3",
                "HOST_AUDIT_RETENTION_MAX_ROWS_LLM_NODE_CALL_LOG": "123",
                "HOST_AUDIT_RETENTION_CUTOFF_DATE": "2026-01-02",
                "HOST_AUDIT_RETENTION_PROTECTED_CHAIN_KEEP_DAYS": "30",
                "HOST_MEMORY_RETENTION_KEEP_DAYS": "45",
                "HOST_MEMORY_RETENTION_CUTOFF_DATE": "2026-01-03",
                "HOST_MEMORY_RETENTION_INCLUDE_CHAT": "true",
                "HOST_MEMORY_RETENTION_MIN_SALIENCE": "0.25",
                "HOST_RETENTION_DRY_RUN": "true",
                "HOST_RETENTION_VACUUM": "true",
            },
            now_ts_ms=1000,
        )

        self.assertEqual(3 * MS_DAY, policy.keep_ms_by_table["error_log"])
        self.assertEqual(123, policy.max_rows_by_table["llm_node_call_log"])
        self.assertIsNotNone(policy.audit_cutoff_ts_ms)
        self.assertEqual(30 * MS_DAY, policy.protected_chain_keep_ms)
        self.assertEqual(45 * MS_DAY, policy.life_memory_keep_ms)
        self.assertIsNotNone(policy.life_memory_cutoff_ts_ms)
        self.assertIn("chat_episode", policy.life_memory_episode_kinds)
        self.assertEqual(0.25, policy.life_memory_min_salience)
        self.assertTrue(policy.dry_run)
        self.assertTrue(policy.vacuum)

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
