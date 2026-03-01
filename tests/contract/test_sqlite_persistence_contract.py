import os
import tempfile
import unittest

from host_brain.audit.sqlite_ledger import SQLiteLedger


class TestSQLitePersistenceContract(unittest.TestCase):
    def test_persistent_triptych_and_guards_survive_reopen(self) -> None:
        fd, db_path = tempfile.mkstemp(prefix="natsume_audit_", suffix=".sqlite3")
        os.close(fd)
        try:
            ledger_a = SQLiteLedger(db_path=db_path)
            ledger_a.log_intent(
                {
                    "request_id": "req_persist_001",
                    "thread_id": "thread_persist",
                    "turn_id": "turn_persist",
                    "intent_id": "intent_persist_001",
                    "session_id": "session_persist",
                    "seq": 1,
                    "ts_ms": 1000,
                    "nonce": "nonce_persist_001",
                    "parse_stage": "LENIENT_OK",
                    "normalized_json": {"a": 1},
                    "proposed_intent_draft_json": {"a": 1},
                    "lint_result": {"schema": [], "args": []},
                }
            )
            ledger_a.log_governor(
                {
                    "request_id": "req_persist_001",
                    "thread_id": "thread_persist",
                    "turn_id": "turn_persist",
                    "intent_id": "intent_persist_001",
                    "decision": "APPROVE",
                    "reason_code": None,
                    "rule_trace": [{"gate_id": "R1_TRUST", "hit": False}],
                    "tokens": {"approval_token_ref": "approval::persist"},
                    "snapshot_refs": {"governance_snapshot": "runtime"},
                    "stage": "S_INTENT_OK",
                    "ts_ms": 1001,
                }
            )
            ledger_a.log_skill_exec(
                {
                    "request_id": "req_persist_001",
                    "thread_id": "thread_persist",
                    "turn_id": "turn_persist",
                    "intent_id": "intent_persist_001",
                    "skill_name": "read_only_status",
                    "approval_token_ref": "approval::persist",
                    "result_status": "SUCCESS",
                    "result_code": "SKILL_EXECUTED",
                    "result_digest": "ok",
                    "evidence_ref": "evidence::persist",
                    "manifest_ref": None,
                    "ts_ms": 1002,
                }
            )
            ledger_a.mark_intent_seen(
                intent_id="intent_persist_001",
                session_id="session_persist",
                nonce="nonce_persist_001",
            )
            ledger_a.update_seq(session_id="session_persist", seq=1)
            ledger_a.log_canon_proposal(
                {
                    "proposal_id": "proposal_world_001",
                    "canon_kind": "world",
                    "target_id": "academy_rules",
                    "base_version": 0,
                    "proposed_version": 1,
                    "proposed_hash": "hash_v1",
                    "proposer_node_id": "memory.canon_world_store",
                    "summary": "seed world canon",
                    "status": "APPLIED",
                    "ts_ms": 1003,
                }
            )
            ledger_a.log_canon_change(
                {
                    "change_id": "canon_change::world::academy_rules::v1",
                    "proposal_id": "proposal_world_001",
                    "canon_kind": "world",
                    "target_id": "academy_rules",
                    "before_version": None,
                    "after_version": 1,
                    "before_hash": None,
                    "after_hash": "hash_v1",
                    "source_path": "host_brain/canon/world/academy_rules.json",
                    "ts_ms": 1004,
                }
            )
            ledger_a.upsert_world_canon_doc(
                {
                    "doc_id": "academy_rules",
                    "version": 1,
                    "hash": "hash_v1",
                    "status": "active",
                    "source_path": "host_brain/canon/world/academy_rules.json",
                    "updated_ts": 1005,
                }
            )
            ledger_a.log_memory_op(
                {
                    "request_id": "req_persist_001",
                    "intent_id": "intent_mem_001",
                    "op_kind": "WRITE",
                    "memory_id": "mem_persist_001",
                    "target_memory_id": None,
                    "source_memory_ids": None,
                    "evidence_refs": ["evt_mem_persist_001"],
                    "status": "SUCCESS",
                    "reason_code": None,
                    "detail": "memory written",
                    "decay_profile_id": "default",
                    "visibility": "internal",
                    "ts_ms": 1006,
                }
            )
            ledger_a.log_vault_manifest(
                {
                    "request_id": "req_persist_001",
                    "manifest_id": "manifest_persist_001",
                    "intent_id": "intent_persist_001",
                    "integrity_digest": "digest_persist_001",
                    "manifest_path": "host_brain/data/vault/manifests/manifest_persist_001.json",
                    "items_count": 1,
                    "evidence_refs": ["evt_persist_manifest"],
                    "policy_profile_id": "vault_default",
                    "undo_ref": "undo::persist",
                    "reversible": 1,
                    "skill_name": "high_risk_write",
                    "session_id": "session_persist",
                    "seq": 1,
                    "status": "ACTIVE",
                    "ts_ms": 1007,
                }
            )
            ledger_a.log_vault_gc(
                {
                    "request_id": "req_persist_001",
                    "policy_profile_id": "vault_default",
                    "deleted_manifest_ids": ["manifest_old_001"],
                    "deleted_artifact_ids": ["artifact_old_001"],
                    "blocked_manifest_ids": ["manifest_persist_001"],
                    "evidence_refs": ["evt_gc_001"],
                    "status": "PARTIAL_BLOCKED",
                    "ts_ms": 1008,
                }
            )
            ledger_a.log_airlock(
                {
                    "request_id": "req_persist_001",
                    "direction": "EGRESS",
                    "reason_code": None,
                    "removed_fields": ["hard_state", "rule_trace"],
                    "redactions": ["api_key"],
                    "payload_digest": "sha256:egress_digest",
                    "quarantine_digest": None,
                    "ingress_classification": None,
                    "blocked": 0,
                    "detail": "egress allowed",
                    "ts_ms": 1009,
                }
            )
            ledger_a.log_airlock(
                {
                    "request_id": "req_persist_001",
                    "direction": "INGRESS",
                    "reason_code": "AIRLOCK_BLOCKED_INGRESS",
                    "removed_fields": [],
                    "redactions": [],
                    "payload_digest": "sha256:ingress_payload",
                    "quarantine_digest": "sha256:ingress_quarantine",
                    "ingress_classification": "DRAFT_INSTRUCTION",
                    "blocked": 1,
                    "detail": "ingress quarantined",
                    "ts_ms": 1010,
                }
            )
            ledger_a.log_channel_io(
                {
                    "request_id": "req_persist_001",
                    "channel": "wechat",
                    "direction": "INGRESS",
                    "thread_key": "wechat::wx_user_1001::natsume_bot",
                    "message_id": "wx_msg_001",
                    "user_id": "wx_user_1001",
                    "status": "RECEIVED",
                    "reason_code": None,
                    "stage": "S_CHANNEL_INGRESS",
                    "detail": "ingress received",
                    "payload_digest": "sha256:channel_ingress_001",
                    "ts_ms": 1011,
                }
            )
            ledger_a.log_avatar_action(
                {
                    "request_id": "req_persist_001",
                    "trigger": "LLM",
                    "emotion_tag": "HAPPY",
                    "expression_id": "exp_happy_01",
                    "status": "APPLIED",
                    "reason_code": None,
                    "stage": "S_AVATAR_EXPRESSION_MAP",
                    "detail": "expression resolved via fixed mapping",
                    "ts_ms": 1012,
                }
            )
            ledger_a.log_clawd_action(
                {
                    "request_id": "req_persist_001",
                    "intent_id": "intent_persist_001",
                    "target_desc": "Submit button",
                    "action_name": "click",
                    "confidence": 0.93,
                    "before_artifact_ref": "artifact::intent_persist_001::before",
                    "after_artifact_ref": "artifact::intent_persist_001::after",
                    "manifest_ref": "manifest::manifest_persist_001",
                    "status": "EXECUTED",
                    "reason_code": None,
                    "stage": "S_CLAWD_UI_EXEC",
                    "detail": "ui action executed with before/after evidence",
                    "ts_ms": 1013,
                }
            )
            ledger_a.log_service_health(
                {
                    "request_id": "req_persist_001",
                    "service_id": "host.integrations.llm_gateway",
                    "owner": "host",
                    "required": 1,
                    "status": "DOWN",
                    "reason_code": "SERVICE_UNAVAILABLE_COLD_START",
                    "stage": "S_SERVICE_HEALTH",
                    "detail": "OPENAI_API_KEY missing",
                    "ts_ms": 1014,
                }
            )
            ledger_a.log_voice_event(
                {
                    "request_id": "req_persist_001",
                    "session_id": "sess_persist_001",
                    "event_type": "ASR_CHUNK",
                    "direction": "EDGE_TO_HOST",
                    "seq": 1,
                    "status": "ACCEPTED",
                    "reason_code": None,
                    "stage": "S_VOICE_ASR_INGRESS",
                    "payload": {"text": "hello", "confidence": 0.91},
                    "detail": "asr chunk accepted",
                    "ts_ms": 1015,
                }
            )
            ledger_a.log_bond_update(
                {
                    "request_id": "req_persist_001",
                    "intent_id": "intent_bond_001",
                    "session_id": "session_persist",
                    "seq": 2,
                    "event_id": "bond_evt_persist_001",
                    "user_id": "user_bond_001",
                    "topic_id": "topic_trust_support",
                    "capability_hint": "SUPPORTIVE",
                    "update_kind": "INTERACTION",
                    "status": "OK",
                    "reason_code": None,
                    "stage": "S_BOND_UPDATE",
                    "evidence_refs": ["evidence::bond::001"],
                    "payload": {"etype": "HELP", "intensity": "LOW"},
                    "detail": "bond update persisted",
                    "ts_ms": 1016,
                }
            )
            ledger_a.close()

            ledger_b = SQLiteLedger(db_path=db_path)
            trace = ledger_b.trace_request("req_persist_001")
            self.assertEqual("COMPLETE", trace["status"])
            self.assertEqual(1, len(trace["intent_log"]))
            self.assertEqual(1, len(trace["governor_log"]))
            self.assertEqual(1, len(trace["skill_exec_log"]))
            self.assertTrue(
                ledger_b.has_replay(
                    intent_id="intent_persist_001",
                    session_id="session_persist",
                    nonce="nonce_persist_001",
                )
            )
            self.assertTrue(ledger_b.is_seq_invalid(session_id="session_persist", seq=1))
            self.assertEqual(1, len(ledger_b.canon_proposal_log))
            self.assertEqual(1, len(ledger_b.canon_change_log))
            world_doc = ledger_b.get_world_canon_doc("academy_rules")
            self.assertIsNotNone(world_doc)
            assert world_doc is not None
            self.assertEqual(1, world_doc["version"])
            self.assertEqual("hash_v1", world_doc["hash"])
            self.assertEqual("active", world_doc["status"])
            self.assertEqual(1, len(ledger_b.memory_ops_log))
            self.assertEqual("WRITE", ledger_b.memory_ops_log[0]["op_kind"])
            self.assertEqual(["evt_mem_persist_001"], ledger_b.memory_ops_log[0]["evidence_refs"])
            self.assertEqual(1, len(ledger_b.vault_manifest_index))
            self.assertEqual("manifest_persist_001", ledger_b.vault_manifest_index[0]["manifest_id"])
            self.assertEqual(1, len(ledger_b.vault_gc_log))
            self.assertEqual("PARTIAL_BLOCKED", ledger_b.vault_gc_log[0]["status"])
            self.assertEqual(2, len(ledger_b.airlock_log))
            self.assertEqual("EGRESS", ledger_b.airlock_log[0]["direction"])
            self.assertEqual(["hard_state", "rule_trace"], ledger_b.airlock_log[0]["removed_fields"])
            self.assertEqual("INGRESS", ledger_b.airlock_log[1]["direction"])
            self.assertEqual("DRAFT_INSTRUCTION", ledger_b.airlock_log[1]["ingress_classification"])
            self.assertEqual(1, len(ledger_b.channel_io_log))
            self.assertEqual("wechat", ledger_b.channel_io_log[0]["channel"])
            self.assertEqual("INGRESS", ledger_b.channel_io_log[0]["direction"])
            self.assertEqual("wx_msg_001", ledger_b.channel_io_log[0]["message_id"])
            self.assertEqual(1, len(ledger_b.avatar_action_log))
            self.assertEqual("HAPPY", ledger_b.avatar_action_log[0]["emotion_tag"])
            self.assertEqual("exp_happy_01", ledger_b.avatar_action_log[0]["expression_id"])
            self.assertEqual(1, len(ledger_b.clawd_action_log))
            self.assertEqual("intent_persist_001", ledger_b.clawd_action_log[0]["intent_id"])
            self.assertEqual("manifest::manifest_persist_001", ledger_b.clawd_action_log[0]["manifest_ref"])
            self.assertEqual(1, len(ledger_b.service_health_log))
            self.assertEqual("host.integrations.llm_gateway", ledger_b.service_health_log[0]["service_id"])
            self.assertEqual("SERVICE_UNAVAILABLE_COLD_START", ledger_b.service_health_log[0]["reason_code"])
            self.assertEqual(1, len(ledger_b.voice_event_log))
            self.assertEqual("ASR_CHUNK", ledger_b.voice_event_log[0]["event_type"])
            self.assertEqual("EDGE_TO_HOST", ledger_b.voice_event_log[0]["direction"])
            self.assertEqual(1, len(ledger_b.bond_update_log))
            self.assertEqual("bond_evt_persist_001", ledger_b.bond_update_log[0]["event_id"])
            self.assertEqual("S_BOND_UPDATE", ledger_b.bond_update_log[0]["stage"])
            self.assertEqual(["evidence::bond::001"], ledger_b.bond_update_log[0]["evidence_refs"])
            ledger_b.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
