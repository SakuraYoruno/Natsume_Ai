受众：MASTER
标题：Stream Baseline Metrics (2026-03-09)

# Stream Baseline Metrics (2026-03-09)

## 1. Scope
- Endpoint: `/v1/chat/stream`
- Chain: `/v1/chat/stream -> chat_stream_handler -> astream_llm_chunks`
- Sample count: `20`
- Run date: `2026-03-09` (local run window: `21:58` to `22:01`)

## 2. Repro Steps
1. Set `PYTHONPATH=D:\Natsume_Ai`.
2. Start local host app (`uvicorn host_brain.app.fastapi_app:app --host 127.0.0.1 --port 8000`).
3. Run:
   ```powershell
   python tools/diag_stream_baseline.py --base-url http://127.0.0.1:8000 --runs 20
   ```
4. Collect console summary and per-run rows.

## 3. Baseline Summary
- total_runs: `20`
- success: `20`
- failed: `0`

- first_event_latency:
  - mean: `13.1ms`
  - p50: `11.9ms`
  - p95: `27.6ms`

- first_render_latency:
  - mean: `8950.6ms`
  - p50: `8538.9ms`
  - p95: `12767.1ms`

- commit_latency:
  - mean: `8999.3ms`
  - p50: `8573.2ms`
  - p95: `12769.8ms`

- retrieval_status_hist:
  - `SKIPPED: 20`

## 4. Bottleneck Notes
- First event is already low (P95 < 30ms), indicating SSE link startup is healthy.
- Main latency is before first render token (LLM generation stage dominates).
- Retrieval was fully skipped in this baseline, so this dataset is suitable as the pre-orchestrator comparison baseline.

## 5. Raw Sample (excerpt)
- run01: first_event `9.93ms`, first_render `13058.29ms`, commit `13062.40ms`, retrieval `SKIPPED`
- run10: first_event `27.60ms`, first_render `9062.24ms`, commit `9064.64ms`, retrieval `SKIPPED`
- run20: first_event `3.11ms`, first_render `9500.11ms`, commit `9501.84ms`, retrieval `SKIPPED`
