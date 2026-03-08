# TODO 03 - Async Streaming Migration (Runtime-Critical)

## Goal
- [ ] Convert runtime blocking operations to async/streaming to reduce end-to-end latency.
- [ ] Remove synchronous calls from request path.

## Blocking API Replacement Matrix
- [ ] `time.sleep(...)` -> `await asyncio.sleep(...)`
- [ ] `urllib.request` / `requests` -> `httpx.AsyncClient` (with connection pooling + timeout policy)
- [ ] `sqlite3.connect` (request path) -> `aiosqlite` (or async repository worker pattern)
- [ ] `subprocess.Popen/run` in async path -> `asyncio.create_subprocess_exec`
- [ ] Synchronous file I/O in request path -> `aiofiles` / `anyio.to_thread.run_sync`

## Priority Hotspots
- [ ] `host_brain/app/fastapi_app.py`: remove retry loop `time.sleep`, keep provider retries async.
- [ ] `host_brain/audit/sqlite_ledger.py`: move sync sqlite to async adapter.
- [ ] `host_brain/retrieval/recall_fusion.py`: async sqlite boost path.
- [ ] `host_brain/governor/rate_limit_store.py`: async sqlite adapter.
- [ ] `host_brain/integrations/graph_checkpoint.py` and `graph_evidence.py`: async sqlite access.
- [ ] `edge_client/rpc/event_reporter.py`: replace `urllib` + `time.sleep` retry with async HTTP + async backoff.
- [ ] `edge_client/senses/asr.py`: replace `urllib.request` hotword fetch path.
- [ ] `host_brain/memory/canon_world_store.py`: replace rollback retry `time.sleep`.

## Streaming Contract Tasks
- [ ] Ensure LLM and chat endpoints emit chunks via async generators.
- [ ] Guarantee cancellation propagation (`CancelledError`) through provider and persistence layers.
- [ ] Add backpressure-safe queue boundaries between producer/consumer components.

## Done Criteria
- [ ] No blocking APIs remain on runtime request path.
- [ ] Streaming responses stay functional under cancellation and slow consumer.
- [ ] Latency benchmark shows measurable p95 reduction.
