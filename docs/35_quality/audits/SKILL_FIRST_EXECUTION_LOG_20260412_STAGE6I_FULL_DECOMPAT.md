# Skill-First Stage 6I Full Decompat Audit

Date: 2026-04-12

## Goal

Complete the hard cut from legacy `top_agent` / `dispatch_agent` naming to the
canonical Skill-First runtime chain:

- `cortex_orchestrator`
- `executive_router`
- `cortex_prompt_builder`

This pass also required frontend trace topology, prompt template ids, runtime
observability fields, and affected tests to stop emitting legacy names.

## Code Scope

Updated runtime and prompt/template wiring:

- `host_brain/app/prompt_template_defs.py`
- `host_brain/app/cortex_prompt_builder.py`
- `host_brain/app/prompt_builder.py`
- `host_brain/app/chat_context_builder.py`
- `host_brain/app/agent_runtime_shared.py`
- `host_brain/app/chat_agent.py`
- `host_brain/app/obs_routes_core.py`
- `host_brain/app/graph/__init__.py`
- `host_brain/app/graph_config_builder.py`
- `host_brain/app/http_hooks.py`
- `host_brain/app/obs_routes_stats.py`
- `host_brain/app/sub_agent_protocol.py`
- `host_brain/persona/drift_detector.py`
- `host_brain/tribe/cortex_agent.py`

Updated monitor UI source and rebuilt static monitor assets:

- `host_brain/app/monitor_ui/src/pages/Trace.jsx`
- `host_brain/app/monitor_ui/src/pages/TraceRequestDetail.jsx`
- `host_brain/app/monitor_ui/src/components/trace/traceNodeText.js`
- `host_brain/app/monitor_ui/src/utils/digitalLife.js`
- `host_brain/app/static/monitor/*` via `npm run build`

Updated affected tests:

- `tests/unit/test_cortex_prompt_builder.py`
- `tests/unit/test_chat_context_builder_helpers_extra.py`
- `tests/unit/test_prompt_builder_helpers_extra.py`
- `tests/unit/test_prompt_builder_emotion_binding_templates.py`
- `tests/integration/test_obs_routes_integration.py`

## Canonicalization Completed

- Prompt template ids renamed from legacy ids to:
  - `executive_router.routing_prompt.templates`
  - `executive_router.failure_recovery.templates`
  - `executive_router.runtime_texts`
  - `cortex_prompt_builder.*`
  - `cortex_orchestrator.runtime_texts`
- Prompt builder role binding switched from legacy `"top_agent"` to
  `"cortex_orchestrator"`.
- Runtime trace/log fields switched to canonical ids:
  - `cortex_orchestrator_llm_stream`
  - `cortex_orchestrator_retrieval`
  - `executive_router_dispatch`
- Frontend trace graph nodes and labels switched to:
  - `cortex_orchestrator`
  - `executive_router`
- Code/test sweep confirmed no remaining legacy runtime identifiers under
  `host_brain/` and `tests/` (excluding built assets during the sweep).

## Verification

Commands executed:

```powershell
python -m compileall -q host_brain\app host_brain\tribe tests
```

```powershell
pytest -q tests\unit\test_cortex_orchestrator_entry.py tests\unit\test_executive_router_entry.py tests\unit\test_executive_runtime_entry.py tests\unit\test_executive_runtime_helpers.py tests\unit\test_model_router_runtime.py
```

```powershell
pytest -q tests\unit\test_input_routes_worker.py tests\unit\test_cortex_prompt_builder.py tests\unit\test_chat_context_builder_helpers_extra.py tests\unit\test_prompt_builder_helpers_extra.py tests\unit\test_prompt_builder_emotion_binding_templates.py
```

```powershell
pytest -q tests\unit\test_tribe_registry.py tests\unit\test_tribe_handoff.py tests\integration\test_obs_routes_integration.py
```

```powershell
npm run build
```

Results:

- `compileall`: passed
- pytest group A: `22 passed`
- pytest group B: `70 passed`
- pytest group C: `32 passed`
- focused regression total: `124 passed`
- monitor UI production build: passed

Residual sweep:

```powershell
rg -n "Top Agent|Dispatch Agent|TopAgent|DispatchAgent|顶级 Agent|トップエージェント|\b(top_agent|dispatch_agent)\b|top_agent_prompt|top_agent_llm_stream|top_agent_dispatch|top_agent_retrieval|top_agent_used|dispatch_agent_used|top_agent_mode" host_brain tests --glob '!host_brain/app/static/monitor/assets/*'
```

Result:

- no matches

## Boundary

Historical references still remain under `docs/` because they are audit/spec
artifacts that describe past architecture states. They were intentionally not
rewritten in bulk during this pass so the repo retains chronological evidence of
the migration.
