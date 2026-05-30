受众：CODEX
标题：DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C4_20260409

# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C4_20260409

## Goal
- Deliver a dedicated WebUI operations panel for `Immune Gate`.
- Expose runtime state + recent gate events + reason distribution in one place.

## Delivered
- Added backend endpoint: `GET /obs/immune-gate/stats`
  - Aggregates immune decisions from `voice_event_log` (`IMMUNE_CHAT_GATE` and `INPUT_*` with `payload.immune_gate`).
  - Returns decision counters (`allow/report/block`), block rate, top reason codes, risk-level distribution, and recent events.
  - Includes current immune runtime state (`enabled`, `blocking_enabled`, thresholds, template rule counts, counters, last decision).
- Added frontend API wrapper:
  - `host_brain/app/monitor_ui/src/api/obs.js` → `fetchImmuneGateStats`.
- Added `Immune Gate Ops` panel in Automation page:
  - File: `host_brain/app/monitor_ui/src/pages/Automation.jsx`.
  - Features: window selector, refresh, runtime config card, top reasons, recent events list.
  - Includes fast entry to rule editing:
    - `#/prompt-config?keyword=immune_agent.runtime_rules&scope=immune_agent`
- Added prompt config query prefill:
  - `host_brain/app/monitor_ui/src/pages/PromptConfig.jsx` now initializes filters from URL search params.

## Tests / Verification
- Added integration tests:
  - `tests/integration/test_obs_immune_gate_stats.py`
    - Verifies event aggregation and runtime-state payload.
    - Verifies degraded runtime-state path when immune agent is unavailable.
- Regression run:
  - `pytest -q tests/integration/test_obs_immune_gate_stats.py tests/unit/test_scheduler_routes_immune_gate_state.py`
  - `pytest -q tests/unit/test_obs_routes_trace_status.py tests/integration/test_prompt_config_routes_api.py`
- Frontend build:
  - `npm run build` in `host_brain/app/monitor_ui` passed.

## Next
- Enhance Trace page rendering for `immune` node (grouping/icon/reason tags).
- Add an E2E consistency check: gate event -> trace node -> scheduler snapshot.
