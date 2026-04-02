from __future__ import annotations

import uuid

from host_brain.core.ids import now_ms
from host_brain.governor.decision import IntentDecisionEngine


def test_intent_engine_accepts_person_id_in_chat_intent() -> None:
    engine = IntentDecisionEngine()
    governance_snapshot = {
        "trust_level": 80,
        "energy_level": 80,
        "sys_mode": "normal",
        "rate_limit_hit": False,
        "scope": "host",
        "scope_allowlist": ["host", "memory", "system"],
        "safety_flag": False,
        "airlock_blocked": False,
    }
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "seq": 1,
        "ts_ms": now_ms(),
        "nonce": str(uuid.uuid4()),
        "type": "query",
        "action": "narrative_only",
        "target": "host",
        "args": {"text": "你好"},
        "person_id": "user_global_test",
    }

    result = engine.evaluate_intent(intent, governance_snapshot=governance_snapshot)

    assert result["decision"] == "APPROVE"
