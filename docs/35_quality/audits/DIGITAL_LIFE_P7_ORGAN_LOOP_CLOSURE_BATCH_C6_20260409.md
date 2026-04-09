# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C6_20260409

## Goal
- Link Immune Gate state to scheduler policy for proactive tasks.
- Keep guardrails strict: only affects scheduler proactive jobs, never passive chat.

## Delivered
- Scheduler C6 gating added in `host_brain/scheduler/scheduler.py`:
  - New proactive immune gate controls:
    - `HOST_SCHEDULER_IMMUNE_PROACTIVE_GATE_ENABLED` (default `true`)
    - `HOST_SCHEDULER_IMMUNE_PROACTIVE_BLOCK_ON_REPORT` (default `false`)
    - `HOST_SCHEDULER_IMMUNE_PROACTIVE_COOLDOWN_MS` (default `900000`)
    - `HOST_SCHEDULER_IMMUNE_PROACTIVE_TASK_IDS` (default includes `proactive_talk_periodic`, `gaming_companion_tick`)
  - Reads latest immune runtime snapshot (`last_inspection`) and suppresses proactive task when:
    - decision=`BLOCK`, or
    - decision=`REPORT` and `BLOCK_ON_REPORT=true` with high/critical risk
    - and signal is inside cooldown window
  - Rejection reason code: `IMMUNE_PROACTIVE_SUPPRESSED`
  - Gate marker in health: `last_gate_state=immune:block|immune:report`
- Scheduler health state expanded:
  - `host_brain/scheduler/health.py`
  - Added fields:
    - `last_immune_decision`
    - `last_immune_risk_level`
    - `last_immune_reason_codes`
- Scheduler routes compatibility updated:
  - `host_brain/scheduler/scheduler_routes.py`
  - Safe snapshots/default tasks now carry new immune fields.
- Scheduler history blocked metrics updated:
  - `host_brain/app/obs_routes_stats.py`
  - `IMMUNE_PROACTIVE_SUPPRESSED` now counted in `gate_blocked_total` and `blocked_reason_codes`.

## Tests
- Updated:
  - `tests/unit/test_scheduler_emotion_gate.py`
    - recent immune block suppresses proactive task
    - non-proactive task not suppressed
    - stale immune signal does not suppress
  - `tests/unit/test_scheduler_engine.py`
    - health snapshot contains immune metadata fields
  - `tests/integration/test_obs_scheduler_history_stats.py`
    - `IMMUNE_PROACTIVE_SUPPRESSED` counted as gate-blocked
- Regression:
  - `pytest -q tests/unit/test_scheduler_emotion_gate.py tests/unit/test_scheduler_engine.py tests/unit/test_scheduler_routes_immune_gate_state.py tests/integration/test_obs_scheduler_history_stats.py tests/integration/test_obs_immune_gate_stats.py`
  - `pytest -q tests/unit/test_obs_routes_trace_status.py tests/integration/test_prompt_config_routes_api.py`

## Notes
- This linkage is scheduler-only and does not change `/v1/chat` behavior.
- Default behavior is conservative: `REPORT` does not suppress unless explicitly enabled.
