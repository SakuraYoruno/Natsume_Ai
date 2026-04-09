# Digital Life Phase 6 Audit (Edge Tribe Agentization)
Date: 2026-04-08

## Goal
- Continue tribe-family migration on `edge_client` side.
- Replace scattered bridge event publishing with explicit edge agent-family wrappers.

## Changes
- `edge_client/tribe/channel_agent.py`
  - Added `ChannelAgent` wrapper for:
    - `channel_message_received`
    - `gateway_degraded`
- `edge_client/tribe/somatic_agent.py`
  - Added `SomaticAgent` wrapper for:
    - `body_sense_updated`
    - `emotion_inference_updated`
- `edge_client/tribe/voice_agent.py`
  - Added `VoiceAgent` wrapper for:
    - `voice_pipeline_started`
    - `voice_pipeline_ended`
- `edge_client/tribe/peripheral_agent.py`
  - Added `PeripheralAgent` skeleton wrapper for:
    - `platform_connected`
    - `platform_disconnected`
- `edge_client/gateway/router.py`
  - Routing path now delegates event publishing to `ChannelAgent`.
- `edge_client/runtime/voice_pipeline.py`
  - ASR/body-sense/emotion sidecar events now delegate to `SomaticAgent`.
  - Voice playback start/end sidecar events now delegate to `VoiceAgent`.
- `edge_client/tribe/__init__.py`
  - Exported new edge tribe agents.
- `edge_client/gateway/bootstrap.py`
  - Integrated `PeripheralAgent` into platform lifecycle:
    - emits `platform_connected` when adapter registration succeeds
    - emits `platform_disconnected` for unavailable platform configuration
    - emits `platform_disconnected` for all connected platforms on `GatewayInstance.stop()`

## Tests
- Added:
  - `tests/unit/test_edge_tribe_agents.py`
  - `tests/unit/test_gateway_bootstrap_wechat_bridge_mode.py` new case:
    - `test_start_gateway_emits_platform_connected_and_disconnected`
    - `test_start_gateway_emits_platform_disconnected_when_config_missing`
  - `tests/unit/test_edge_tribe_event_boundary_guard.py`
    - Guard test to ensure `edge_client/gateway` + `edge_client/runtime` do not publish tribe events directly outside edge tribe agents.
- Verified:
  - `python -m py_compile edge_client/gateway/router.py edge_client/runtime/voice_pipeline.py edge_client/gateway/bootstrap.py edge_client/tribe/channel_agent.py edge_client/tribe/somatic_agent.py edge_client/tribe/voice_agent.py edge_client/tribe/peripheral_agent.py tests/unit/test_gateway_bootstrap_wechat_bridge_mode.py`
    - Result: `pass`
  - `pytest -q tests/unit/test_gateway_bootstrap_wechat_bridge_mode.py tests/unit/test_gateway_router_tribe_bridge.py tests/unit/test_edge_tribe_agents.py`
    - Result: `9 passed`
  - `pytest -q tests/unit/test_edge_tribe_bridge.py tests/unit/test_edge_tribe_agents.py tests/unit/test_gateway_router_tribe_bridge.py tests/unit/test_edge_voice_pipeline_helpers.py tests/unit/test_gateway_bootstrap_wechat_bridge_mode.py`
    - Result: `27 passed`
  - `pytest -q tests/unit/test_edge_tribe_event_boundary_guard.py tests/unit/test_edge_tribe_agents.py tests/unit/test_gateway_router_tribe_bridge.py tests/unit/test_edge_voice_pipeline_helpers.py tests/unit/test_gateway_bootstrap_wechat_bridge_mode.py`
    - Result: `25 passed`
  - `pytest -q tests/unit/test_gateway_router_tribe_bridge.py tests/unit/test_edge_voice_pipeline_helpers.py tests/unit/test_edge_tribe_bridge.py tests/unit/test_edge_tribe_agents.py`
    - Result: `23 passed`
  - `pytest -q tests/unit/test_chat_context_builder_helpers_extra.py tests/unit/test_motive_runtime_background.py tests/unit/test_top_agent_post_process.py tests/unit/test_chat_handler_top_agent.py tests/unit/test_input_routes_worker.py tests/unit/test_personality_registry_runtime_skills.py tests/unit/test_tribe_registry.py tests/unit/test_tribe_handoff.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_hippocampus_agent.py tests/unit/test_chat_retrieval_hippocampus_integration.py tests/unit/test_chat_context_builder_hippocampus_integration.py tests/unit/test_limbic_agent.py tests/unit/test_inner_world_skill_tribe_events.py tests/unit/test_body_sense_skill_tribe_events.py tests/unit/test_prefrontal_agent.py tests/unit/test_http_hooks_runtime_helpers_prefrontal_integration.py tests/unit/test_http_hooks_runtime_helpers.py tests/unit/test_social_brain_agent.py tests/unit/test_http_hooks_runtime_helpers_social_brain_integration.py tests/unit/test_cortex_agent.py tests/unit/test_gateway_router_tribe_bridge.py tests/unit/test_edge_voice_pipeline_helpers.py tests/unit/test_edge_tribe_bridge.py tests/unit/test_edge_tribe_agents.py`
    - Result: `125 passed`
