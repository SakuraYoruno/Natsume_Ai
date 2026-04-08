# Digital Life Phase 5 Audit (Cortex Direct Coupling Trim)
Date: 2026-04-08

## Goal
- Continue tribe-family migration by trimming remaining `Cortex` direct coupling to bond stage engine.
- Keep behavior unchanged while routing through `SocialBrainAgent` wrappers.

## Changes
- `host_brain/app/top_agent.py`
  - In `_async_post_process(...)`, bond update is delegated to `get_cortex_agent().post_process_bond_update(...)`.
- `host_brain/tribe/cortex_agent.py`
  - Added `post_process_bond_update(...)` wrapper as Cortex-family stable entrypoint.
  - Added singleton helpers `get_cortex_agent()` / `set_cortex_agent()`.
  - Fallback writer path now resolves stage engine through social brain wrapper, not direct app-layer coupling.
- `host_brain/tribe/social_brain_agent.py`
  - Added `resolve_stage_engine()` to centralize stage-engine access behind social brain family API.
- `host_brain/app/state_arbiter_queries.py`
  - Bond fact query path now resolves stage engine via `get_social_brain_agent().resolve_stage_engine()`.
  - Bond fact evidence metadata source updated from direct engine marker to `social_brain_agent`.
- `host_brain/app/http_hooks_runtime_helpers.py`
  - `_run_evolution_cycle(...)` remains delegated to prefrontal wrapper.
  - Removed residual unreachable legacy statements and normalized degraded warning message.
- `host_brain/app/intelligent_runtime.py`
  - Removed stale direct `get_bond_stage_engine` import from shared-memory hint block.
- `host_brain/app/chat_handler_vision.py`
  - Removed stale direct bond dependencies imports (`get_bond_stage_engine`, `get_bond_update_queue`, `BondDynamicsWriter`) that were no longer used.
- `host_brain/app/system_handler.py`
  - Edge emotion event post-processing now delegates bond updates to `get_cortex_agent().post_process_bond_update(...)`.
  - Removed direct bond queue/stage-engine/writer coupling from edge event handler path.
- `host_brain/app/fastapi_app.py`
  - Bond route provider wiring now uses `_get_bond_engine_via_social_brain()` instead of direct dependency export.
  - Bond engine is resolved at call-time via `get_social_brain_agent().resolve_stage_engine()`.
- `host_brain/tribe/cortex_agent.py`
  - Added `shutdown_bond_update_queue(...)` to encapsulate bond queue drain/shutdown through Cortex-family wrapper.
- `host_brain/app/http_hooks.py`
  - Host shutdown path now delegates bond queue graceful shutdown to `get_cortex_agent().shutdown_bond_update_queue(...)`.
- `host_brain/persona/inner_activity_agent.py`
  - Bond context collection now resolves through `get_social_brain_agent().resolve_bond_snapshot(...)`.
  - Removed direct bond stage engine dependency from inner-activity path.
- `host_brain/tribe/limbic_agent.py`
  - Added `resolve_emotion_timeline_snapshot()` as limbic-family timeline snapshot entrypoint.
- `host_brain/persona/inner_activity_agent.py`
  - Emotion timeline collection now resolves through `get_limbic_agent().resolve_emotion_timeline_snapshot()`.
  - Removed direct emotion timeline dependency access from inner-activity path.
- `host_brain/app/chat_context_builder.py`
  - Runtime payload timeline snapshot now resolves through `get_limbic_agent().resolve_emotion_timeline_snapshot(...)`.
  - Removed direct timeline singleton access from this app-layer path.

## Tests
- Added:
  - `tests/unit/test_social_brain_agent.py` new case `test_social_brain_resolve_stage_engine`.
  - `tests/unit/test_cortex_agent.py` for queued/fallback bond-write wrapper behavior.
  - `tests/unit/test_state_arbiter_queries_social_brain_integration.py` for state_arbiter bond path social-brain integration.
  - `tests/unit/test_edge_events_whitelist.py` new case:
    - `test_edge_events_emotion_triggers_cortex_bond_update`
  - `tests/unit/test_fastapi_app_bond_provider_social_brain.py`
    - `test_fastapi_bond_provider_wired_to_social_brain`
    - `test_fastapi_bond_provider_resolves_stage_engine_from_social_brain`
  - `tests/unit/test_cortex_agent.py` new cases:
    - `test_cortex_shutdown_bond_update_queue_success`
    - `test_cortex_shutdown_bond_update_queue_degraded`
  - `tests/unit/test_inner_activity_agent_social_brain_integration.py`
    - `test_collect_bond_context_uses_social_brain_snapshot`
    - `test_collect_bond_context_degrades_when_snapshot_fails`
  - `tests/unit/test_inner_activity_agent_limbic_integration.py`
    - `test_collect_emotion_timeline_uses_limbic_agent`
    - `test_collect_emotion_timeline_degrades_when_limbic_fails`
  - `tests/unit/test_limbic_agent.py` new cases:
    - `test_limbic_agent_resolve_emotion_timeline_snapshot`
    - `test_limbic_agent_resolve_emotion_timeline_snapshot_default`
    - `test_limbic_agent_resolve_emotion_timeline_snapshot_with_windows`
  - `tests/unit/test_bond_coupling_boundary_guard.py`
    - Guard test to block `host_brain/app` + `host_brain/persona` from reintroducing direct bond coupling markers.
  - `tests/unit/test_chat_context_builder_helpers_extra.py` new case:
    - `test_build_chat_prompt_runtime_payload_uses_limbic_timeline_snapshot`
- Verified:
  - `pytest -q tests/unit/test_cortex_agent.py tests/unit/test_top_agent_post_process.py`
    - Result: `7 passed`
  - `pytest -q tests/unit/test_http_hooks_runtime_helpers_prefrontal_integration.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_http_hooks_runtime_helpers_social_brain_integration.py tests/unit/test_cortex_agent.py tests/unit/test_top_agent_post_process.py`
    - Result: `16 passed`
  - `pytest -q tests/unit/test_state_arbiter_queries_social_brain_integration.py tests/unit/test_chat_retrieval_init_failure_degrade.py tests/unit/test_top_agent_sse.py`
    - Result: `25 passed`
  - `pytest -q tests/unit/test_vision_ui_handler.py tests/unit/test_state_arbiter_queries_social_brain_integration.py tests/unit/test_chat_retrieval_init_failure_degrade.py tests/unit/test_top_agent_sse.py`
    - Result: `44 passed`
  - `pytest -q tests/unit/test_edge_events_whitelist.py tests/unit/test_top_agent_post_process.py tests/unit/test_cortex_agent.py`
    - Result: `16 passed`
  - `python -m py_compile host_brain/app/http_hooks_runtime_helpers.py host_brain/tribe/cortex_agent.py host_brain/app/top_agent.py`
    - Result: `pass`
  - `python -m py_compile host_brain/app/state_arbiter_queries.py host_brain/app/intelligent_runtime.py host_brain/app/chat_handler_vision.py`
    - Result: `pass`
  - `python -m py_compile host_brain/app/system_handler.py tests/unit/test_edge_events_whitelist.py`
    - Result: `pass`
  - `python -m py_compile host_brain/app/fastapi_app.py tests/unit/test_fastapi_app_bond_provider_social_brain.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_fastapi_app_bond_provider_social_brain.py tests/integration/test_bond_routes_api.py`
    - Result: `20 passed`
  - `pytest -q tests/unit/test_cortex_agent.py tests/unit/test_top_agent_post_process.py tests/unit/test_http_hooks_startup_status.py`
    - Result: `14 passed`
  - `pytest -q tests/unit/test_inner_activity_agent.py tests/unit/test_inner_activity_agent_social_brain_integration.py tests/unit/test_motive_runtime_background.py`
    - Result: `15 passed`
  - `pytest -q tests/unit/test_bond_coupling_boundary_guard.py tests/unit/test_inner_activity_agent_social_brain_integration.py tests/unit/test_cortex_agent.py tests/unit/test_fastapi_app_bond_provider_social_brain.py`
    - Result: `9 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_inner_activity_agent.py tests/unit/test_inner_activity_agent_limbic_integration.py tests/unit/test_motive_runtime_background.py`
    - Result: `23 passed`
  - `pytest -q tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_limbic_agent.py tests/unit/test_inner_activity_agent_limbic_integration.py`
    - Result: `44 passed`
  - Focused backend+edge aggregate regression set (30 files)
    - Result: `180 passed`
  - Focused backend+edge aggregate regression set (32 files)
    - Result: `200 passed`
  - Focused backend+edge aggregate regression set (33 files)
    - Result: `207 passed`
  - Focused backend+edge aggregate regression set (35 files)
    - Result: `213 passed`
  - Focused backend+edge aggregate regression set (36 files)
    - Result: `214 passed`
  - Focused backend+edge aggregate regression set (37 files)
    - Result: `215 passed`
  - Focused backend+edge aggregate regression set (38 files)
    - Result: `219 passed`
  - Focused backend+edge aggregate regression set (38 files, latest)
    - Result: `221 passed`

## Current Coupling Status
- `host_brain/app` and `host_brain/persona` no longer directly import:
  - `get_bond_stage_engine`
  - `get_bond_update_queue`
  - `BondDynamicsWriter`
- Remaining direct bond dependency access is contained in tribe wrappers:
  - `host_brain/tribe/social_brain_agent.py`
  - `host_brain/tribe/cortex_agent.py`
  - `pytest -q tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_motive_runtime_background.py tests/unit/test_top_agent_post_process.py tests/unit/test_chat_handler_top_agent.py tests/unit/test_input_routes_worker.py tests/unit/test_personality_registry_runtime_skills.py tests/unit/test_tribe_registry.py tests/unit/test_tribe_handoff.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_hippocampus_agent.py tests/unit/test_chat_retrieval_hippocampus_integration.py tests/unit/test_chat_context_builder_hippocampus_integration.py tests/unit/test_limbic_agent.py tests/unit/test_inner_world_skill_tribe_events.py tests/unit/test_body_sense_skill_tribe_events.py tests/unit/test_prefrontal_agent.py tests/unit/test_http_hooks_runtime_helpers_prefrontal_integration.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_social_brain_agent.py tests/unit/test_http_hooks_runtime_helpers_social_brain_integration.py`
    - Result: `100 passed`
  - Same aggregate set + `tests/unit/test_cortex_agent.py`
    - Result: `102 passed`

## Incremental Continuation (same day)
- `host_brain/tribe/limbic_agent.py`
  - Added `capture_conversation_body_sense(...)` to encapsulate conversation-start body-sense snapshot + limbic signal path.
  - Added/retained limbic wrapper entrypoints for silence/body-sense/timeline snapshots and body-sense penalty application.
- `host_brain/app/chat_context_builder.py`
  - Conversation body-sense path now routed via `get_limbic_agent().capture_conversation_body_sense(...)`.
  - Body-sense willingness penalty application now routed via limbic wrapper instance.
- `host_brain/persona/motive_runtime.py`
  - Default silence/body-sense fallback snapshots now resolve via limbic wrapper (`resolve_silence_state` / `resolve_body_sense_state`) instead of direct persona singleton access.
- `host_brain/app/http_hooks_runtime_helpers.py`
  - Runtime snapshot assembly and proactive response-policy input now resolve silence/body-sense via limbic wrapper.

- Added:
  - `tests/unit/test_motive_runtime_limbic_integration.py`
    - `test_compute_runtime_motive_state_uses_limbic_wrappers_when_missing_inputs`
    - `test_compute_runtime_motive_state_skips_limbic_when_inputs_provided`
  - `tests/unit/test_limbic_agent.py`
    - `test_limbic_agent_capture_conversation_body_sense`

- Verified:
  - `python -m py_compile host_brain/tribe/limbic_agent.py host_brain/persona/motive_runtime.py host_brain/app/chat_context_builder.py host_brain/app/http_hooks_runtime_helpers.py tests/unit/test_limbic_agent.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_inner_activity_agent.py tests/unit/test_inner_activity_agent_limbic_integration.py`
    - Result: `68 passed`
  - Focused backend+edge aggregate regression set (latest continuation)
    - Result: `585 passed`

## Incremental Continuation II (same day)
- `host_brain/tribe/limbic_agent.py`
  - Added `on_conversation_ended(...)` wrapper to encapsulate conversation-end body-sense lifecycle and signal emission.
- `host_brain/app/top_agent.py`
  - Conversation-end body-sense hook now routed via `get_limbic_agent().on_conversation_ended(...)` (removed direct app-layer body-sense singleton coupling).

- Added:
  - `tests/unit/test_limbic_agent.py`
    - `test_limbic_agent_on_conversation_ended`

- Updated:
  - `tests/unit/test_top_agent_post_process.py`
  - `tests/unit/test_top_agent_sse.py`
  - switched persona body-sense patch points to limbic wrapper patch points in post-process paths.

- Verified:
  - `python -m py_compile host_brain/tribe/limbic_agent.py host_brain/app/top_agent.py tests/unit/test_limbic_agent.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_motive_runtime_background.py`
    - Result: `89 passed`
  - Focused backend+edge aggregate regression set (latest continuation II)
    - Result: `586 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.(body_sense|emotional_silence)|get_body_sense_model|get_silence_engine" host_brain/app host_brain/persona`
    - Result: no remaining direct app/persona call-site coupling hits (only singleton definitions in `host_brain/persona/body_sense.py` and `host_brain/persona/emotional_silence.py`).

## Incremental Continuation III (same day)
- Added:
  - `tests/unit/test_limbic_coupling_boundary_guard.py`
    - Guard test to block reintroduction of direct `body_sense` / `emotional_silence` singleton coupling in `host_brain/app` + `host_brain/persona` (with explicit allowlist for singleton definition files).

- Verified:
  - `pytest -q tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_limbic_agent.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py`
    - Result: `48 passed`
  - Focused backend+edge aggregate regression set (latest continuation III)
    - Result: `587 passed`

## Incremental Continuation IV (same day)
- `host_brain/tribe/limbic_agent.py`
  - Added offline continuity wrappers:
    - `resolve_inner_world_state(...)`
    - `resolve_offline_state(...)`
    - `on_user_awaken(...)`
    - `on_session_end(...)`
  - Capability set extended with `offline_continuity`.
- `host_brain/app/chat_context_builder.py`
  - Offline awaken snapshot path now routed via `get_limbic_agent().on_user_awaken(...)`.
- `host_brain/app/http_hooks_runtime_helpers.py`
  - Runtime snapshot `inner_world_state`/`offline_state` now routed via limbic wrappers.
  - Proactive response-policy offline-state assembly now routed via limbic wrapper.
- `host_brain/app/top_agent.py`
  - Session-end inner-world writeback now routed via `get_limbic_agent().on_session_end(...)`.
- `host_brain/persona/motive_runtime.py`
  - Missing `inner_state` fallback now routed via `get_limbic_agent().resolve_inner_world_state(...)`.

- Added:
  - `tests/unit/test_inner_world_coupling_boundary_guard.py`
    - Guard test to block app-layer reintroduction of direct `inner_world` / `build_offline_state_from_inner_state` coupling.

- Updated:
  - `tests/unit/test_limbic_agent.py`
    - `test_limbic_agent_resolve_inner_world_and_offline_state`
    - `test_limbic_agent_on_user_awaken_and_session_end`
  - `tests/unit/test_motive_runtime_limbic_integration.py`
  - `tests/unit/test_http_hooks_runtime_helpers.py`
  - `tests/unit/test_top_agent_post_process.py`
  - `tests/unit/test_top_agent_sse.py`

- Verified:
  - `python -m py_compile host_brain/tribe/limbic_agent.py host_brain/app/chat_context_builder.py host_brain/app/http_hooks_runtime_helpers.py host_brain/app/top_agent.py host_brain/persona/motive_runtime.py tests/unit/test_limbic_agent.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_inner_world_coupling_boundary_guard.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_limbic_coupling_boundary_guard.py`
    - Result: `93 passed`
  - Focused backend+edge aggregate regression set (latest continuation IV)
    - Result: `590 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.inner_world import get_inner_world|get_inner_world\\(|from host_brain\\.persona\\.motive_runtime import build_offline_state_from_inner_state|build_offline_state_from_inner_state\\(" host_brain/app host_brain/persona`
    - Result: no remaining app-layer direct coupling hits (remaining hits are confined to persona internals/singleton definitions).

## Incremental Continuation V (same day)
- `host_brain/tribe/limbic_agent.py`
  - Added motive/response wrapper entrypoints:
    - `compute_runtime_motive_state(...)`
    - `compute_motive_state(...)`
    - `decide_response_policy(...)`
  - Added corresponding state snapshot touch points for runtime motive / motive / response policy.
- `host_brain/app/top_agent.py`
  - Retrieval motive prefetch path now routed via `get_limbic_agent().compute_runtime_motive_state(...)`.
- `host_brain/app/http_hooks_runtime_helpers.py`
  - Runtime snapshot motive/response assembly now routed via limbic wrappers.
  - Proactive dispatch motive/response assembly now routed via limbic wrappers.
- `host_brain/app/chat_context_builder.py`
  - Prompt runtime motive/response stage now routed via limbic wrappers.

- Added:
  - `tests/unit/test_motive_policy_coupling_boundary_guard.py`
    - Guard test to block app-layer reintroduction of direct motive/response coupling markers.

- Updated:
  - `tests/unit/test_limbic_agent.py`
    - `test_limbic_agent_compute_runtime_motive_state`
    - `test_limbic_agent_compute_motive_and_decide_policy`
  - `tests/unit/test_http_hooks_runtime_helpers.py`
    - updated limbic mock surface to include motive/response wrapper methods.

- Verified:
  - `python -m py_compile host_brain/tribe/limbic_agent.py host_brain/app/top_agent.py host_brain/app/http_hooks_runtime_helpers.py host_brain/app/chat_context_builder.py tests/unit/test_limbic_agent.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_motive_policy_coupling_boundary_guard.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py`
    - Result: `96 passed`
  - Focused backend+edge aggregate regression set (latest continuation V)
    - Result: `593 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.(motive_runtime|motive_engine|response_policy)|MotiveInput|ResponsePolicyInput" host_brain/app`
    - Result: no remaining direct app-layer motive/response coupling hits.

## Incremental Continuation VI (same day)
- `host_brain/tribe/prefrontal_agent.py`
  - Added `resolve_self_reflection_context(...)` wrapper:
    - consolidates `PersonaEngine.get_self_model_snapshot(...)` and `build_self_reflection_pulse(...)` behind prefrontal family API.
- `host_brain/app/http_hooks_runtime_helpers.py`
  - Runtime snapshot self-model/self-reflection block now routed via `get_prefrontal_agent().resolve_self_reflection_context(...)`.
  - Removed direct app-layer imports for `persona_engine` and `self_reflection` from this path.

- Updated:
  - `tests/unit/test_prefrontal_agent.py`
    - `test_prefrontal_resolve_self_reflection_context`

- Verified:
  - `python -m py_compile host_brain/tribe/prefrontal_agent.py host_brain/app/http_hooks_runtime_helpers.py tests/unit/test_prefrontal_agent.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_prefrontal_agent.py`
    - Result: `9 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py`
    - Result: `99 passed`
  - Focused backend+edge aggregate regression set (latest continuation VI)
    - Result: `594 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.(motive_runtime|motive_engine|response_policy|persona_engine|self_reflection)" host_brain/app`
    - Result: remaining app-layer direct persona hits are consolidated to `host_brain/app/chat_context_builder.py` only.

## Incremental Continuation VII (same day)
- `host_brain/tribe/prefrontal_agent.py`
  - Validated and retained delegated prefrontal wrapper entrypoints:
    - `run_persona_drift_cycle(...)`
    - `run_growth_feedback_cycle(...)`
  - Added `resolve_style_evolution_summary(...)` wrapper:
    - encapsulates drift-style evolution summary access behind prefrontal family API.
- `host_brain/app/top_agent.py`
  - Validated drift/growth post-process path now delegates to:
    - `get_prefrontal_agent().run_persona_drift_cycle(...)`
    - `get_prefrontal_agent().run_growth_feedback_cycle(...)`
  - direct app-layer drift/growth singleton/class coupling remains removed.
- `host_brain/app/chat_context_builder.py`
  - style-evolution fetch path now routed via:
    - `get_prefrontal_agent().resolve_style_evolution_summary(...)`
  - removed direct app-layer `get_drift_detector()` access from this path.

- Added:
  - `tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Guard test to block app-layer reintroduction of direct drift/growth coupling markers.
- Updated:
  - `tests/unit/test_prefrontal_agent.py`
    - `test_prefrontal_resolve_style_evolution_summary`
    - `test_prefrontal_run_persona_drift_cycle_sets_hard_state_hint`
    - `test_prefrontal_run_growth_feedback_cycle`

- Verified:
  - `python -m py_compile host_brain/tribe/prefrontal_agent.py host_brain/app/top_agent.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_prefrontal_agent.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py`
    - Result: `29 passed`
  - `python -m py_compile host_brain/tribe/prefrontal_agent.py host_brain/app/chat_context_builder.py tests/unit/test_prefrontal_agent.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `66 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `103 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.drift_detector import|get_drift_detector\\(|from host_brain\\.persona\\.growth_feedback_collector import|from host_brain\\.persona\\.persona_growth import" host_brain/app`
    - Result: no remaining app-layer direct drift/growth coupling hits.

## Incremental Continuation VIII (same day)
- `host_brain/tribe/prefrontal_agent.py`
  - Added wrappers to continue app-layer decoupling:
    - `resolve_persona_traits(...)`
    - `resolve_persona_change_log(...)`
    - `resolve_growth_awareness(...)`
    - `resolve_growth_summary(...)`
  - Added internal orchestrator accessor `_get_evolution_orchestrator(...)` for wrapper reuse.
- `host_brain/app/chat_context_builder.py`
  - Persona-expression trait loading now routed via:
    - `get_prefrontal_agent().resolve_persona_traits(...)`
  - Self-reflection prep path now routes:
    - persona change-log via `resolve_persona_change_log(...)`
    - growth-awareness via `resolve_growth_awareness(...)`
    - growth-summary via `resolve_growth_summary(...)`
    - style-evolution via `resolve_style_evolution_summary(...)`
  - removed direct app-layer imports for:
    - `persona_engine`
    - `evolution_orchestrator`
    - `drift_detector`
    in this coupled path.

- Updated:
  - `tests/unit/test_prefrontal_agent.py`
    - `test_prefrontal_resolve_persona_change_and_growth_payloads`
    - `test_prefrontal_resolve_persona_traits_fallback`
  - `tests/unit/test_chat_context_builder_helpers_extra.py`
    - prefrontal mock surface aligned to new wrapper methods.
  - `tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - added guard markers for `persona_engine` and `evolution_orchestrator`.

- Verified:
  - `python -m py_compile host_brain/tribe/prefrontal_agent.py host_brain/app/chat_context_builder.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `68 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `105 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.(persona_engine|evolution_orchestrator|drift_detector|growth_feedback_collector|persona_growth)" host_brain/app`
    - Result: no remaining direct app-layer persona prefrontal-coupling markers.

## Incremental Continuation IX (same day)
- `host_brain/tribe/prefrontal_agent.py`
  - Added `resolve_persona_expression_hint(...)` wrapper:
    - encapsulates `PersonaExpressionAdapter` + `EmotionVector` + traits resolve behind prefrontal family API.
- `host_brain/app/chat_context_builder.py`
  - Persona-expression path now delegates expression hint synthesis to:
    - `get_prefrontal_agent().resolve_persona_expression_hint(...)`
  - removed app-layer direct `expression_adapter` / `EmotionVector` coupling from this path.
- Updated:
  - `tests/unit/test_prefrontal_agent.py`
    - `test_prefrontal_resolve_persona_expression_hint`
  - `tests/unit/test_chat_context_builder_helpers_extra.py`
    - prefrontal mock aligned with `resolve_persona_expression_hint(...)`.
  - `tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - guard markers extended with `expression_adapter`.

- Verified:
  - `python -m py_compile host_brain/tribe/prefrontal_agent.py host_brain/app/chat_context_builder.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py`
    - Result: `69 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `106 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.expression_adapter|PersonaExpressionAdapter" host_brain/app`
    - Result: no remaining app-layer direct expression-adapter coupling hits (comments only).

## Incremental Continuation X (same day)
- `host_brain/tribe/prefrontal_agent.py`
  - Added `resolve_bond_style_instruction(...)` wrapper:
    - encapsulates `BondAwareStyleEngine.generate(...)` behind prefrontal family API.
- `host_brain/app/chat_context_builder.py`
  - Bond-aware style injection now delegates to:
    - `get_prefrontal_agent().resolve_bond_style_instruction(...)`
  - removed app-layer direct `bond_aware_style` coupling from this runtime path.

- Updated:
  - `tests/unit/test_prefrontal_agent.py`
    - `test_prefrontal_resolve_bond_style_instruction`

- Verified:
  - `python -m py_compile host_brain/tribe/prefrontal_agent.py host_brain/app/chat_context_builder.py tests/unit/test_prefrontal_agent.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py`
    - Result: `70 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `107 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\.bond_aware_style|BondAwareStyleEngine" host_brain/app/chat_context_builder.py`
    - Result: no direct import/call-site coupling remains in `chat_context_builder` (comments only).

## Incremental Continuation XI (same day)
- `host_brain/tribe/limbic_agent.py`
  - Added `resolve_persistent_emotion_state(...)` wrapper:
    - encapsulates `EmotionPersistence` restore/blend/persist lifecycle in limbic family API.
- `host_brain/app/chat_context_builder.py`
  - Emotion persistence flow now delegates to:
    - `get_limbic_agent().resolve_persistent_emotion_state(...)`
  - removed app-layer direct `EmotionPersistence` import and lifecycle logic from this path.

- Updated:
  - `tests/unit/test_limbic_agent.py`
    - `test_limbic_agent_resolve_persistent_emotion_state`
  - `tests/unit/test_limbic_coupling_boundary_guard.py`
    - added marker guard for `EmotionPersistence` direct coupling.

- Verified:
  - `python -m py_compile host_brain/tribe/limbic_agent.py host_brain/app/chat_context_builder.py tests/unit/test_limbic_agent.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_prefrontal_agent.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py`
    - Result: `88 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `108 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\." host_brain/app/chat_context_builder.py`
    - Result: remaining direct imports are narrowed to:
      - `host_brain.persona.offline_continuity`
      - `host_brain.persona.self_reflection`

## Incremental Continuation XII (same day)
- `host_brain/tribe/limbic_agent.py`
  - Added offline continuity wrapper:
    - `resolve_offline_reconnect_profile(...)`
  - Added test-backed state touchpoint:
    - `last_offline_reconnect_profile`.
- `host_brain/app/chat_context_builder.py`
  - Removed module-level direct `offline_continuity` imports.
  - `_apply_offline_state_to_persona_expression_hint(...)` now uses:
    - `_resolve_offline_reconnect_profile(...)` (internally delegates to limbic wrapper).
- `host_brain/app/http_hooks_runtime_helpers.py`
  - Removed module-level direct `offline_continuity` imports.
  - runtime summary now resolves `(absence_band, offline_reconnect_style)` through limbic wrapper helper.

- Added / Updated tests:
  - `tests/unit/test_limbic_agent.py`
    - `test_limbic_agent_resolve_offline_reconnect_profile`
  - `tests/unit/test_offline_continuity_coupling_boundary_guard.py`
    - app-layer guard test to block direct `offline_continuity` imports.
  - `tests/unit/test_http_hooks_runtime_helpers.py`
    - updated limbic fake surface with `resolve_offline_reconnect_profile(...)`.

- Verified:
  - `python -m py_compile host_brain/tribe/limbic_agent.py host_brain/app/chat_context_builder.py host_brain/app/http_hooks_runtime_helpers.py tests/unit/test_limbic_agent.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py`
    - Result: `8 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py`
    - Result: `110 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\." host_brain/app`
    - Result: remaining app-layer direct persona imports narrowed to:
      - `host_brain/app/chat_context_builder.py` (`self_reflection`)
      - `host_brain/app/dependencies.py` (`bond_aware_style`)

## Incremental Continuation XIII (same day)
- `host_brain/app/chat_context_builder.py`
  - Completed self-reflection path decoupling cleanup:
    - runtime self-reflection generation now fully consumed via prefrontal wrapper cycle:
      - `get_prefrontal_agent().run_self_reflection_cycle(...)`
      - `get_prefrontal_agent().align_self_reflection_pulse(...)`
  - app-layer direct `persona.self_reflection` dependency is removed from this runtime assembly path.
- `host_brain/app/dependencies.py`
  - Completed bond-aware style app dependency cleanup:
    - introduced `_BondAwareStyleEngineProxy` + `_BondAwareStyleResultView`
    - `get_bond_aware_style_engine()` now delegates to:
      - `get_prefrontal_agent().resolve_bond_style_payload(...)`
  - preserves legacy runtime consumer surface (`generate()`, `as_dict()`, `prompt_injection`, `forbidden_patterns`, `intimacy_level`).
- `tests/unit/test_chat_context_builder_helpers_extra.py`
  - Aligned growth-summary self-reflection test doubles with prefrontal wrapper runtime contract:
    - fake prefrontal now provides `run_self_reflection_cycle(...)`
    - fake prefrontal now provides `align_self_reflection_pulse(...)`
  - removed obsolete test monkeypatch to function-local `_self_reflection` state.
- `tests/unit/test_prefrontal_coupling_boundary_guard.py`
  - Extended app-layer forbidden markers to block reintroduction of:
    - `host_brain.persona.self_reflection`
    - `host_brain.persona.bond_aware_style.BondAwareStyleEngine`
- `tests/unit/test_app_persona_direct_coupling_boundary_guard.py`
  - Added AST-level boundary guard:
    - scans `host_brain/app/**/*.py`
    - blocks any direct `import/from host_brain.persona...` statements.

- Verified:
  - `python -m py_compile host_brain/app/dependencies.py host_brain/app/chat_context_builder.py tests/unit/test_chat_context_builder_helpers_extra.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_intelligent_runtime.py tests/unit/test_prefrontal_agent.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py`
    - Result: `62 passed`
  - `pytest -q tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_intelligent_runtime.py tests/unit/test_prefrontal_agent.py`
    - Result: `55 passed`
  - `pytest -q tests/unit/test_app_persona_direct_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_intelligent_runtime.py`
    - Result: `47 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_intelligent_runtime.py`
    - Result: `121 passed`
  - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_intelligent_runtime.py tests/unit/test_app_persona_direct_coupling_boundary_guard.py`
    - Result: `122 passed`

- Coupling sweep snapshot:
  - `rg -n "from host_brain\\.persona\\." host_brain/app`
    - Result: no remaining app-layer direct persona imports.

## Incremental Continuation XIV (same day)
- `host_brain/bond/bond_stage_engine.py`
  - Removed direct bond-layer dependency on `host_brain.persona.persona_growth`.
  - Introduced `_PrefrontalGrowthTrackerProxy`:
    - `_get_persona_growth_tracker()` now resolves tracker path via `get_prefrontal_agent()`.
    - stage promotion / milestone growth now delegates to prefrontal-family wrapper methods.
- `host_brain/tribe/prefrontal_agent.py`
  - Added growth wrapper entrypoints for bond/runtime reuse:
    - `apply_stage_promotion_growth(...)`
    - `apply_milestone_growth(...)`
  - Added `_get_persona_growth_tracker()` internal accessor and aligned growth-feedback path to reuse it.
- `host_brain/core/emotion_timeline.py`
  - Removed direct core-layer dependency on `host_brain.persona.emotion_reactivity`.
  - Emotion-reactivity modulation now delegates to:
    - `get_limbic_agent().resolve_emotion_reactivity_profile(...)`.
- `host_brain/tribe/limbic_agent.py`
  - Added `resolve_emotion_reactivity_profile(...)` wrapper:
    - encapsulates EmotionReactivityProfile compute/apply logic behind limbic-family API.

- Added / Updated tests:
  - `tests/unit/test_bond_stage_engine_helpers_extra.py`
    - import-failure degrade case aligned to prefrontal proxy path.
    - added proxy delegation verification case.
  - `tests/unit/test_prefrontal_agent.py`
    - added `test_prefrontal_apply_stage_and_milestone_growth`.
  - `tests/unit/test_limbic_agent.py`
    - added `test_limbic_agent_resolve_emotion_reactivity_profile`.
  - `tests/unit/test_non_tribe_persona_coupling_boundary_guard.py`
    - new AST guard ensuring `host_brain/bond` and `host_brain/core` have no direct `host_brain.persona.*` imports.

- Verified:
  - Coupling sweep:
    - `rg -n --pcre2 "^\s*(from\s+host_brain\.persona(\.|\s+import)|import\s+host_brain\.persona(\.|$))" host_brain --glob '!host_brain/persona/**'`
    - Result: remaining direct persona imports are confined to `host_brain/tribe/*`.
  - Focused regression:
    - `pytest -q tests/unit/test_bond_stage_engine_helpers_extra.py tests/unit/test_bond_stage_engine.py tests/unit/test_limbic_agent.py tests/unit/test_emotion_echo.py tests/unit/test_persona_phase2.py tests/unit/test_prefrontal_agent.py tests/unit/test_non_tribe_persona_coupling_boundary_guard.py`
    - Result: `108 passed`
  - Aggregate regression:
    - `pytest -q tests/unit/test_limbic_agent.py tests/unit/test_prefrontal_agent.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_top_agent_post_process.py tests/unit/test_top_agent_sse.py tests/unit/test_motive_runtime_limbic_integration.py tests/unit/test_motive_runtime_background.py tests/unit/test_limbic_coupling_boundary_guard.py tests/unit/test_offline_continuity_coupling_boundary_guard.py tests/unit/test_inner_world_coupling_boundary_guard.py tests/unit/test_motive_policy_coupling_boundary_guard.py tests/unit/test_prefrontal_coupling_boundary_guard.py tests/unit/test_intelligent_runtime.py tests/unit/test_app_persona_direct_coupling_boundary_guard.py tests/unit/test_bond_stage_engine_helpers_extra.py tests/unit/test_bond_stage_engine.py tests/unit/test_emotion_echo.py tests/unit/test_persona_phase2.py tests/unit/test_non_tribe_persona_coupling_boundary_guard.py`
    - Result: `201 passed`
