# QGBT240414L Tool/SubAgent Invocation Test

- Date: 2026-04-14
- Scope: verify current real invocation capability for tool execution and sub-agent dispatch.

## Unit Test Result

```bash
pytest tests/unit/test_tool_registry.py::TestIntentAgentExecutionTools -q
pytest tests/unit/test_intent_agent_handler_execution_tools.py -q
pytest tests/unit/test_sub_agent_protocol_context.py -q
```

- Result:
  - `7 passed`
  - `2 passed`
  - `4 passed`

## Runtime Smoke Result

### Toolset status

- `intent_agent` full toolset:
  - `search_knowledge, get_context, list_skills, analyze_code, run_skill, dispatch_sub_agent`
- `intent_agent` readonly toolset:
  - `search_knowledge, get_context, list_skills, analyze_code`

### Invocation cases

1. `tool.list_skills`
- status: `SUCCESS`
- sample:
  - `bond_query (family=general, risk=1)`
  - `bond_score_get (family=general, risk=1)`
  - `dream_generator (family=general, risk=1)`

2. `tool.run_skill` (`read_only_status`)
- status: `SUCCESS`
- result: `builtin_noop`
- audit: `skill_name=read_only_status`, `result_status=SUCCESS`

3. `tool.dispatch_sub_agent` (`code_agent`, no gateway)
- status: `SUCCESS`
- result: `[ReAct 降级] agent_id=code_agent，当前没有可用的 LLM gateway`
- audit: `skill_name=sub_agent:code_agent`, `result_status=OK`, `result_code=SUB_AGENT_OK`

4. `intent_agent_handler` forced tool-call -> `run_skill`
- steps: 2
- first tool: `run_skill`
- first observation status: `SUCCESS`
- final answer: `stored`

5. `intent_agent_handler` forced tool-call -> `dispatch_sub_agent`
- steps: 2
- first tool: `dispatch_sub_agent`
- first observation status: `SUCCESS`
- final answer: `delegated`

6. `tool.dispatch_sub_agent` with injected gateway
- status: `SUCCESS`
- result: `subagent normal output`
- audit: `skill_name=sub_agent:code_agent`, `result_status=OK`, `result_code=SUB_AGENT_OK`

## Conclusion

- Tool invocation capability: **OK**
- Sub-agent invocation capability: **OK**
- Audit trace for executed tool/sub-agent entries: **OK**
