# QGBT240414K Intent Execution Toolchain Fix

- Date: 2026-04-14
- Goal: Fix root cause where `intent_agent` could route but could not perform real tool/sub-agent execution.

## Root Cause

`intent_agent` tool allowlist only contained read-only tools (`search_knowledge/get_context/analyze_code`), so ReAct loops often ended with planning text but no executable side effects.

## Code Changes

1. Added executable tools in `host_brain/app/agents/tool_registry.py`
- `list_skills` (read-only discovery)
- `run_skill` (side-effect, calls governed runtime `_execute_skill`)
- `dispatch_sub_agent` (side-effect, calls `SubAgentDispatcher.dispatch`)
- Added `_split_runtime_context` to pass runtime context safely.
- Added dynamic toolset resolver for `intent_agent`:
  - default `INTENT_DYNAMIC_TOOLSET_ENABLED=true`
  - new side-effect tools auto-discovered (no manual whitelist append)
  - optional denylist via `INTENT_TOOL_DENYLIST=tool_a,tool_b`
  - optional readonly auto-include via `INTENT_AUTO_INCLUDE_READONLY_TOOLS=true`

2. Updated intent allowlist in `host_brain/app/agents/tool_registry.py`
- `intent_agent` now includes: `list_skills`, `run_skill`, `dispatch_sub_agent` (side-effect tools only available when `allow_side_effects=True`).
- manual allowlist remains baseline only; dynamic resolver appends future tools automatically.

3. Context propagation for tool execution in `host_brain/app/agents/react_loop.py`
- Injected `_react_context` into tool args so tool calls can use `request_id/session_id/gateway`.

4. Strengthened execution guidance in `host_brain/app/handlers/intent_agent_handler.py`
- Added enforced prompt instruction to prefer executable tools for actionable intents.
- Added `payload["gateway"]` fallback for gateway acquisition.
- Set `INTENT_SUBGRAPH_ENABLED` default to `true` so execution path is enabled by default.

5. Added tests
- `tests/unit/test_intent_agent_handler_execution_tools.py` (new)
- `tests/unit/test_tool_registry.py` (extended)

## Verification

### Unit Tests

```bash
pytest tests/unit/test_tool_registry.py tests/unit/test_intent_agent_handler_execution_tools.py tests/unit/test_react_loop_templates.py -q
```

- Result: `73 passed`

```bash
pytest tests/unit/test_input_routes_worker.py tests/unit/test_sub_agent_protocol_context.py tests/unit/test_phase15_dispatch_to_agent.py -q
```

- Result: `74 passed`

### Runtime Smoke (direct tool execution)

```text
intent_tools_full= ['search_knowledge', 'get_context', 'list_skills', 'analyze_code', 'run_skill', 'dispatch_sub_agent']
intent_tools_ro=   ['search_knowledge', 'get_context', 'list_skills', 'analyze_code']
run_skill_status=SUCCESS success=True error=None
dispatch_sub_agent_status=SUCCESS success=True error=None
auto_in_full=True (dynamic discovered tool)
auto_in_ro=False
```

### Audit Trace (skill_exec_log)

- `request_id=smoke_req_240414K` -> `read_only_status SUCCESS`
- `request_id=smoke_req_240414K_sub` -> `sub_agent:code_agent OK (SUB_AGENT_OK)`

## Conclusion

Root cause is fixed at capability layer:
- `intent_agent` now has real, governed execution entrypoints for both skill execution and sub-agent delegation.
- Execution leaves auditable `skill_exec_log` evidence.
