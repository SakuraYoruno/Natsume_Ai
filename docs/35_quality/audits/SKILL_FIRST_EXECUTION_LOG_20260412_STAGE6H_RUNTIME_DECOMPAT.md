# Skill-First Runtime Decompat Log

Date: 2026-04-12
Stage: 6H
Scope: runtime / routing / tribe naming decompat

## Completed

- Rebuilt `host_brain/app/executive_runtime_core.py` as the canonical Skill-First runtime core.
- Restored the canonical runtime chain:
  - `cortex_orchestrator -> executive_router -> executive_runtime -> executive_runtime_core`
- Removed the remaining runtime dependency on deleted modules:
  - `host_brain/app/dispatch_agent.py`
  - `host_brain/app/top_agent.py`
- Renamed the prompt-builder module path used by runtime:
  - `host_brain/app/top_agent_prompt.py` -> `host_brain/app/cortex_prompt_builder.py`
- Updated runtime callers:
  - `host_brain/app/chat_agent.py`
  - `host_brain/app/agent_runtime_shared.py`
- Removed the input-route legacy shim name:
  - `_run_top_agent_pipeline` -> `_run_cortex_pipeline`
- Removed the graph runtime compatibility entry:
  - deleted `resolve_dispatch_agent_llm_runtime`
- Migrated runtime / audit / tribe naming:
  - `record_dispatch_handoff` -> `record_executive_handoff`
  - runtime task default source -> `executive_runtime`
  - capability forge default actor -> `executive_runtime`
  - tribe default agents:
    - `top_agent` -> `cortex_orchestrator`
    - `dispatch_agent` -> `executive_router`
- Updated observability-generated node ids to canonical runtime names:
  - `cortex_orchestrator`
  - `executive_router`

## Verification

- `python -m compileall -q host_brain\\app host_brain\\tribe tests`
- `pytest -q tests\\unit\\test_cortex_orchestrator_entry.py tests\\unit\\test_executive_router_entry.py tests\\unit\\test_executive_runtime_entry.py tests\\unit\\test_executive_runtime_helpers.py tests\\unit\\test_input_routes_worker.py tests\\unit\\test_model_router_runtime.py tests\\unit\\test_cortex_prompt_builder.py tests\\unit\\test_tribe_registry.py tests\\unit\\test_tribe_handoff.py`
- Result: `43 passed`

## Remaining Decompat Work

- Prompt template ids still contain legacy `top_agent_prompt` / `top_agent` / `dispatch_agent` keys.
- `agent_runtime_shared.py` still has some legacy template ids and comments.
- Frontend trace/config pages still render `top_agent` / `dispatch_agent` labels in multiple source files.
- Some prompt-builder and chat-context test fixtures still use legacy node ids.

## Notes

- This stage intentionally prioritized executable runtime ownership and canonical chain stability first.
- Historical trace compatibility was reduced in generated nodes, but some fallback reads remain where old persisted timing keys may still exist.
