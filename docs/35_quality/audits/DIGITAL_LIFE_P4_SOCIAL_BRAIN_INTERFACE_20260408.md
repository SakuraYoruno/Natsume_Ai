受众：CODEX
标题：Digital Life Phase 4 Audit (SocialBrain Interface Wiring)

# Digital Life Phase 4 Audit (SocialBrain Interface Wiring)
Date: 2026-04-08

## Goal
- Continue tribe-family refactor by extracting relationship/bond reads behind `SocialBrainAgent`.
- Reduce direct `get_bond_stage_engine` coupling in runtime and chat context paths.

## Changes
- `host_brain/tribe/social_brain_agent.py`
  - Added `resolve_bond_snapshot(...)`.
  - Added `resolve_bond_stage(...)`.
  - Added `resolve_scheduler_bond_summary(...)`.
  - Added singleton APIs: `get_social_brain_agent()` / `set_social_brain_agent()`.
- `host_brain/tribe/__init__.py`
  - Exported social brain singleton helpers.
- `host_brain/tribe/prefrontal_agent.py`
  - `run_evolution_cycle(...)` now uses social-brain bond snapshot/summary wrapper.
- `host_brain/app/http_hooks_runtime_helpers.py`
  - Runtime snapshot bond section now resolves via `SocialBrainAgent`.
  - `run_proactive_intent_dispatch(...)` bond summary now resolves via `SocialBrainAgent`.
  - `_run_personality_scheduler_skill(...)` bond summary now resolves via `SocialBrainAgent`.
- `host_brain/app/chat_context_builder.py`
  - Bond texture bootstrap switched to social-brain snapshot wrapper.
  - Persona expression bond-stage resolution switched to social-brain stage API.
  - Self-reflection bond inputs switched to social-brain snapshot wrapper.
  - Bond narrative / bond-aware style bond-stage reads switched to social-brain APIs.

## Tests Added
- `tests/unit/test_social_brain_agent.py`
  - Covers snapshot, stage, and scheduler summary wrappers.
- `tests/unit/test_http_hooks_runtime_helpers_social_brain_integration.py`
  - Verifies proactive dispatch and runtime snapshot consume social-brain wrappers.

## Verification
- `pytest -q tests/unit/test_social_brain_agent.py tests/unit/test_http_hooks_runtime_helpers_social_brain_integration.py tests/unit/test_prefrontal_agent.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_chat_context_builder_hippocampus_integration.py`
  - Result: `47 passed`
- `pytest -q tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_motive_runtime_background.py tests/unit/test_top_agent_post_process.py tests/unit/test_chat_handler_top_agent.py tests/unit/test_input_routes_worker.py tests/unit/test_personality_registry_runtime_skills.py tests/unit/test_tribe_registry.py tests/unit/test_tribe_handoff.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_hippocampus_agent.py tests/unit/test_chat_retrieval_hippocampus_integration.py tests/unit/test_chat_context_builder_hippocampus_integration.py tests/unit/test_limbic_agent.py tests/unit/test_inner_world_skill_tribe_events.py tests/unit/test_body_sense_skill_tribe_events.py tests/unit/test_prefrontal_agent.py tests/unit/test_http_hooks_runtime_helpers_prefrontal_integration.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_social_brain_agent.py tests/unit/test_http_hooks_runtime_helpers_social_brain_integration.py`
  - Result: `99 passed`
