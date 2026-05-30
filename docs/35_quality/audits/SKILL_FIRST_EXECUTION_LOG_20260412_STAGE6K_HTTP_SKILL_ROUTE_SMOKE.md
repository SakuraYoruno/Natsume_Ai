受众：CODEX
标题：Skill-First Execution Log 2026-04-12 Stage 6K

# Skill-First Execution Log 2026-04-12 Stage 6K

## Scope

- Objective: validate the refactored Skill-First stack from the real host HTTP entrypoint.
- Entry under test: `POST /v1/chat`
- Path under test:
  - direct skill route: `/v1/chat -> chat_handler -> cortex_orchestrator -> executive_router -> executive_runtime -> skill`
  - plan route: `/v1/chat -> chat_handler -> cortex_orchestrator -> executive_router -> executive_runtime -> skill_dag`
- Constraint: keep the test deterministic by stubbing immune gate, governor approval, retrieval, LLM runtime selection, final chat rendering, and async post-process side effects.

## Added regression coverage

- Added: `tests/integration/test_http_skill_route_smoke.py`
- Coverage:
  - direct `intent_draft -> skill` route through the real FastAPI `/v1/chat` entrypoint
  - multi-step `planned_steps -> plan` route through the real FastAPI `/v1/chat` entrypoint
  - real-governor direct skill triptych completion
  - real-governor `planned_steps` extension compatibility + plan execution
  - `EXECUTIVE_ROUTE` SSE event correctness
  - `skill_exec_log` audit persistence after HTTP ingress

## Automated test results

- `pytest -q tests\\integration\\test_http_skill_route_smoke.py`
  - Result: `4 passed`
- `pytest -q tests\\contract\\test_intent_contract.py tests\\integration\\test_http_skill_route_smoke.py`
  - Result: `11 passed`
- `pytest -q tests\\integration\\test_executive_skill_runtime_smoke.py tests\\integration\\test_http_skill_route_smoke.py tests\\contract\\test_intent_contract.py tests\\unit\\test_skill_family_router.py tests\\unit\\test_skill_dag_planner.py tests\\unit\\test_executive_runtime_entry.py`
  - Result: `21 passed`

## Patch applied

- Governor schema compatibility fix:
  - `host_brain/governor/schemas.py::strict_validate_intent`
  - behavior before: `IntentEnvelope(**normalized_json)` crashed on extension keys (e.g. `route`, `planned_steps`)
  - behavior after: governor binds canonical envelope fields only, ignores unknown top-level extension keys for forward compatibility
- Added contract regression:
  - `tests/contract/test_intent_contract.py::test_t24_extension_fields_do_not_break_strict_validate`

## Manual HTTP smoke A: direct skill route

Setup:

- in-memory ledger
- built-in skill registry only
- fake governor approval
- fake retrieval / fake LLM runtime
- fake final chat renderer returning deterministic COMMIT text

Request summary:

```json
{
  "message": "请总结当前会话",
  "session_id": "sess_http_skill_smoke_001",
  "intent_draft": {
    "route": "skill",
    "target_id": "session_summary"
  }
}
```

Observed SSE highlights:

- `PRESENCE / THINKING`
- `PRESENCE / GOV_APPROVED`
- `EXECUTIVE_ROUTE`
  - `route_type = skill`
  - `target_id = session_summary`
  - `reason_code = intent_draft:skill`
- `COMMIT`
  - `decision = APPROVE`
  - `narrative = skill-route-ok::session_summary`

Observed audit result:

```json
{
  "trace_status": "INCOMPLETE",
  "skill_exec_len": 1,
  "skill_names": ["session_summary"]
}
```

## Manual HTTP smoke B: plan route

Request summary:

```json
{
  "message": "run prepared plan",
  "session_id": "sess_http_plan_smoke_001",
  "intent_draft": {
    "planned_steps": [
      {"target_id": "session_summary", "route": "skill"},
      {"target_id": "emotion_query", "route": "skill"}
    ]
  }
}
```

Observed SSE highlights:

- `PRESENCE / THINKING`
- `PRESENCE / GOV_APPROVED`
- `EXECUTIVE_ROUTE`
  - `route_type = plan`
  - `target_id = skill_dag`
  - `reason_code = hard_rule:multi_step`
- `COMMIT`
  - `decision = APPROVE`
  - `narrative = skill-plan-ok::[1] summarize session: session_summary ...`

Observed audit result:

```json
{
  "trace_status": "INCOMPLETE",
  "skill_exec_len": 2,
  "skill_names": ["session_summary", "emotion_query"],
  "statuses": ["SUCCESS", "SUCCESS"]
}
```

## Manual HTTP smoke C: real governor + direct skill triptych

Request summary:

```json
{
  "message": "请总结当前会话",
  "session_id": "sess_http_skill_e2e_002",
  "intent_draft": {
    "schema_version": "Natsume.intent.v1",
    "action": "session_summary",
    "target": "host"
  }
}
```

Observed SSE highlights:

- `PRESENCE / THINKING`
- `PRESENCE / GOV_APPROVED`
- `EXECUTIVE_ROUTE`
  - `route_type = skill`
  - `target_id = session_summary`
  - `reason_code = intent_draft:skill_target`
- `COMMIT`
  - `decision = APPROVE`
  - `narrative = skill-route-ok::session_summary`

Observed audit result:

```json
{
  "trace_status": "COMPLETE",
  "intent_len": 1,
  "governor_len": 1,
  "skill_exec_len": 1,
  "skill_names": ["session_summary"]
}
```

## Manual HTTP smoke D: real governor + planned_steps extension

Request summary:

```json
{
  "message": "先总结当前会话，然后读取当前情绪状态",
  "session_id": "sess_http_plan_e2e_001",
  "intent_draft": {
    "schema_version": "Natsume.intent.v1",
    "action": "exec_intent",
    "target": "host",
    "planned_steps": [
      {"target_id": "session_summary", "route": "skill"},
      {"target_id": "emotion_query", "route": "skill"}
    ]
  }
}
```

Observed SSE highlights:

- `PRESENCE / THINKING`
- `PRESENCE / GOV_APPROVED`
- `EXECUTIVE_ROUTE`
  - `route_type = plan`
  - `target_id = skill_dag`
  - `reason_code = hard_rule:multi_step`
- `COMMIT`
  - `decision = APPROVE`
  - `narrative` includes both `session_summary` and `emotion_query`

Observed audit result:

```json
{
  "trace_status": "COMPLETE",
  "intent_len": 1,
  "governor_len": 1,
  "skill_exec_len": 2,
  "skill_names": ["session_summary", "emotion_query"],
  "statuses": ["SUCCESS", "SUCCESS"]
}
```

## Notes

- `trace_status = INCOMPLETE` is expected in these HTTP smokes.
- Reason: the governor layer was intentionally stubbed to isolate HTTP ingress and Skill-First routing behavior, so full `intent_log / governor_log / skill_exec_log` triptych completion was not part of this check.
- The critical assertion here is that `/v1/chat` emits the correct `EXECUTIVE_ROUTE` event and still writes real `skill_exec_log` rows.
- The real governor direct-skill path is now also verified separately and reaches `trace_status = COMPLETE`.
- Fixed in this patch:
  - Full-governor HTTP requests can now carry extension fields like `route`/`planned_steps` without schema crash.

## Conclusion

- The refactored Skill-First architecture now executes correctly not only in helper-level tests but also through the real host `/v1/chat` entrypoint.
- Direct skill routing is functioning.
- Multi-step plan routing is functioning.
- The direct-skill path is verified both with a stub governor and with the real governor/audit triptych.
- The real-governor + `planned_steps` path is also verified and reaches COMPLETE triptych.
- HTTP ingress still reaches the canonical execution path and persists skill execution audit rows.
