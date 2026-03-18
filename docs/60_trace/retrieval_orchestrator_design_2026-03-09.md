受众：MASTER
标题：RetrievalOrchestrator Design (2026-03-09)

# RetrievalOrchestrator Design (2026-03-09)

## 1. Goal
Define a stable retrieval orchestration contract for chat paths:
- unified input protocol: `query`, `session_state`, `trace_id`
- unified output protocol: `retrieval_bundle`
- explicit path semantics: `success / partial_failure / all_failure`

Implemented in:
- `host_brain/retrieval/orchestrator.py`

## 2. Interface

### 2.1 Request
`RetrievalOrchestratorRequest`
- `trace_id: str`
- `query: str`
- `session_state: dict[str, Any]`
- `request_id: str`
- `session_id: str | None`
- `intent_id: str | None`
- `top_k: int`
- `ts_ms: int | None`
- `emotion_context: str | None`

### 2.2 Result
`RetrievalOrchestratorResult`
- `success: bool`
- `status: str`
- `reason_code: str | None`
- `path: str` (`success | partial_failure | all_failure`)
- `detail: str`
- `trace_id: str`
- `query: str`
- `session_state: dict[str, Any]`
- `retrieval_summary: dict[str, Any]`
- `retrieval_evidence_items: list[dict[str, Any]]`
- `retrieval_bundle: dict[str, Any]`

### 2.3 retrieval_bundle shape
```json
{
  "trace_id": "...",
  "query": "...",
  "session_state": { "...": "..." },
  "path": "success|partial_failure|all_failure",
  "summary": { "...": "..." },
  "evidence_items": [ { "...": "..." } ]
}
```

## 3. Timeout & Degrade
- `arun(..., timeout_ms)` supports orchestration timeout.
- Timeout maps to:
  - `status=DEGRADED`
  - `reason_code=RETRIEVAL_TIMEOUT`
  - `path=all_failure`
- Runtime exception maps to:
  - `status=DEGRADED`
  - `reason_code=RETRIEVAL_FAILED`
  - `path=all_failure`

## 4. Path Rules
- `success`:
  - `status=OK` and `degraded=false`
- `partial_failure`:
  - degraded, but has any useful output (`route_count>0` or `candidate_count>0` or `rerank_topk>0` or `evidence_items>0`)
- `all_failure`:
  - degraded and no usable retrieval payload

## 5. Runtime Integration
- `/v1/chat`:
  - switched retrieval execution to `RetrievalOrchestrator.arun(...)`
  - continues to feed existing `retrieval_summary/retrieval_evidence_items` into prompt builder
- `/v1/chat/stream`:
  - `_run_retrieval(...)` now delegates to orchestrator
  - keeps previous function signature for test compatibility

## 6. Tests
- New: `tests/unit/test_retrieval_orchestrator.py`
  - success path
  - partial failure path
  - all failure path
  - timeout degrade mapping
