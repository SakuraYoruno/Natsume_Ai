受众：MASTER
标题：Host Chat Real Chain Verify (2026-03-05)

# Host Chat Real Chain Verify (2026-03-05)

## Scope
- Objective: verify online chain after JP Style Canon distillation and ensure runtime path is `six_recall + nine_node_langgraph + forced_renderer_ja`.
- Host endpoint: `http://127.0.0.1:18080/v1/chat`
- Chroma endpoint: `http://127.0.0.1:8000`

## Runtime actions executed
1. Synced style canon projection into retrieval collection `canon_style`:
   - command: `python scripts/runtime/sync_canon_vector_index.py --style-only --collection canon_style`
   - result: `style_docs_synced=2`, `collection_count=2`
   - synced ids: `canon_style::natsume_ai_cn`, `canon_style::natsume_ai_ja`

2. Ran 3 real chat requests (cloud path, no template forcing):
   - `req_f0285853b86943b99476b701f5baf79a`
   - `req_515a2b33704f4d0e82b6964fa83712c8`
   - `req_8829650571254cccada436a604c23daf`

## Chain verification summary

### Request `req_f0285853b86943b99476b701f5baf79a`
- retrieval.route_count: `6`
- retrieval.route_rows: all six routes `status=OK`
- llm.node_count: `9`
- renderer node: `renderer_ja_c_style`, `status=OK`, `model=gemini-2.5-flash`
- model_control rows: `9`, all `status=OK`
- final narrative: Japanese output

### Request `req_515a2b33704f4d0e82b6964fa83712c8`
- retrieval.route_count: `6`
- retrieval.route_rows: all six routes `status=OK`
- llm.node_count: `9`
- renderer node: `renderer_ja_c_style`, `status=OK`, `model=gemini-2.5-flash`
- model_control rows: `9`, all `status=OK`
- final narrative: low quality/garbled Japanese-like text

### Request `req_8829650571254cccada436a604c23daf`
- retrieval.route_count: `6`
- retrieval.route_rows: all six routes `status=OK` (`route_canon_style.candidate_count=2` after sync)
- llm.node_count: `9`
- renderer node: `renderer_ja_c_style`, `status=OK`, `model=gemini-2.5-flash`
- model_control rows: `9`
- non-OK model node:
  - `core_reasoner_primary`: `DEGRADED` / `SERVICE_UNAVAILABLE_DEPENDENCY`
- fallback path continued and chain still completed
- final narrative: low quality/garbled text

## Observability gaps (still present)
- `graph_run_trace` not written for these requests (`0` rows each).
  - consequence: `/obs/trace/{request_id}` falls back to governor-only node view.
- `llm_node_call_log` not written (`0` rows each).
  - consequence: no persisted per-node raw input/output trace in OBS backend tables.

## Retrieval evidence note
- While six routes are active and `route_canon_style` is hit, `audit_refs.retrieval.evidence_refs` currently resolves to `mem_*` references for these requests, not direct canon-style refs.
- This indicates rerank/output evidence selection is not prioritizing canon-style docs in these prompts.

## Artifact files
- Raw chat responses:
  - `artifacts/trace/HOST_CHAT_REAL_RAW_req_f0285853b86943b99476b701f5baf79a.json`
  - `artifacts/trace/HOST_CHAT_REAL_RAW_req_515a2b33704f4d0e82b6964fa83712c8.json`
  - `artifacts/trace/HOST_CHAT_REAL_RAW_req_8829650571254cccada436a604c23daf.json`
- Model control rows:
  - `artifacts/trace/HOST_CHAT_MODEL_CONTROL_req_f0285853b86943b99476b701f5baf79a.json`
  - `artifacts/trace/HOST_CHAT_MODEL_CONTROL_req_515a2b33704f4d0e82b6964fa83712c8.json`
  - `artifacts/trace/HOST_CHAT_MODEL_CONTROL_req_8829650571254cccada436a604c23daf.json`
- Sync summary:
  - `artifacts/trace/CANON_STYLE_SYNC_TO_CANON_STYLE_2026-03-05.json`
- OBS fallback trace sample:
  - `artifacts/trace/HOST_CHAT_OBS_TRACE_req_f0285853b86943b99476b701f5baf79a.json`

## Current conclusion
- Contract-level runtime shape is active: `6 recall routes + 9 model nodes + forced renderer node`.
- `natsume_ai_ja` is now present in `canon_style` retrieval collection.
- Remaining host issues are mainly observability persistence (`graph_run_trace`, `llm_node_call_log`) and output quality stability on some requests.
