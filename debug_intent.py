from host_brain.governor.decision import IntentDecisionEngine
from host_brain.core.ids import now_ms

e = IntentDecisionEngine()
r = e.evaluate_intent(
    {
        'schema_version': 'Natsume.intent.v1',
        'id': 'i1',
        'session_id': 's1',
        'seq': 1,
        'ts_ms': now_ms(),
        'nonce': 'n1',
        'type': 'skill_call',
        'action': 'noop',
        'target': 'host',
        'args': {}
    },
    governance_snapshot={
        'trust_level': 80,
        'energy_level': 80,
        'sys_mode': 'normal',
        'rate_limit_hit': False,
        'scope': 'host',
        'scope_allowlist': ['host', 'memory', 'system'],
        'safety_flag': False,
        'airlock_blocked': False,
        'hard_state_summary': {'mood_state': 'engaged'}
    }
)
print("Result:", r['decision'], r.get('reason_code'), r.get('stage'))
