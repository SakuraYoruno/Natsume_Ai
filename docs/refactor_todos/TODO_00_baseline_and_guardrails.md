# TODO 00 - Baseline And Guardrails

## Scope Rule (must keep)
- [ ] All scans must skip tests:
  - [ ] `tests/**`
  - [ ] `**/test_*.py`
  - [ ] `**/*_test.py`

## Inventory (current non-test Python >600 lines)
- [ ] Confirm and freeze baseline list (23 files):
  - [ ] `host_brain/app/fastapi_app.py` (3811)
  - [ ] `host_brain/audit/sqlite_ledger.py` (2192)
  - [ ] `host_brain/retrieval/recall_fusion.py` (1949)
  - [ ] `edge_client/senses/asr.py` (1272)
  - [ ] `host_brain/retrieval/reranker.py` (1248)
  - [ ] `edge_client/actuators/tts_engine.py` (1172)
  - [ ] `host_brain/release/release_manifest.py` (1037)
  - [ ] `host_brain/retrieval/embedding.py` (934)
  - [ ] `host_brain/app/main.py` (930)
  - [ ] `edge_client/actuators/vts_driver.py` (834)
  - [ ] `host_brain/integrations/llm_gateway.py` (829)
  - [ ] `scripts/runtime/import_natsume_style_canon.py` (814)
  - [ ] `host_brain/core/services.py` (763)
  - [ ] `host_brain/memory/canon_world_store.py` (747)
  - [ ] `host_brain/app/config.py` (746)
  - [ ] `host_brain/core/qa.py` (717)
  - [ ] `host_brain/bond/neo4j_client.py` (704)
  - [ ] `host_brain/scheduler/scheduler.py` (698)
  - [ ] `host_brain/governor/rate_limit_store.py` (661)
  - [ ] `host_brain/core/metabolism.py` (647)
  - [ ] `host_brain/app/canon_routes.py` (633)
  - [ ] `tools/trace_six_lib_recall.py` (612)
  - [ ] `host_brain/airlock/sandbox.py` (601)

## Engineering Guardrails
- [ ] Add CI static scan rule (non-test code): block `time.sleep`, `sqlite3.connect`, `urllib.request`, `requests.`, `subprocess.run/call/Popen`, and synchronous file I/O in runtime path.
- [ ] Define async policy doc: allowed exceptions (one-off CLI tooling) and required wrappers (`asyncio.to_thread` / `anyio.to_thread`).
- [ ] Capture baseline metrics before refactor: p50/p95 latency, throughput, timeout/error ratio.

## Done Criteria
- [ ] Baseline snapshot committed.
- [ ] CI scan fails on newly introduced sync APIs in non-test runtime code.
- [ ] Refactor work can proceed in parallel by module owner.
