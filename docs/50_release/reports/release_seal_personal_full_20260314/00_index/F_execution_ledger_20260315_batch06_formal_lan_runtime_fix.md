# F Execution Ledger

- Batch: `20260315_batch06_formal_lan_runtime_fix`
- Mode: `formal_lan_dual_node`
- Host: `127.0.0.1:18080`
- Edge: `192.168.1.101:17800`
- Host PID: `49820`
- Scope: `runtime_input/prompt_context refactor retest + stream/commit narrative fix`

| Item | Status | Notes | Evidence |
| --- | --- | --- | --- |
| `F-01` LAN topology and readiness | `PASS` | Host and remote edge both `READY/UP` | `req_5d2dd554255d4fee8b4275c65f063a30` |
| `F-08` chat main chain | `PASS` | two real `POST /v1/chat` requests reached clean `COMMIT`; VTS/TTS accepted | `req_41eed88b1d3840638cfd0aa62d2582a2`, `req_66f10138b701442abdb5c403dc205b48` |
| `F-09` retrieval accuracy | `BLOCK` | sample-1 still hit `RETRIEVAL_TIMEOUT` with `route_count=0`; sample-2 recovered to `route_count=7` | see chain report |
| `F-16` text quality | `BLOCK` | JSON shell leak fixed, but sample-2 answer is still generic and only partially grounded to query intent | see chain report |
| `F-17` chat latency | `PARTIAL` | LLM latency acceptable in 2 samples, but end-to-end stability still needs more samples | see chain report |
| `F-29` trace closure | `PASS` | `/obs/trace/{request_id}` now returns `llm -> vts -> tts` chain | `req_41eed88b1d3840638cfd0aa62d2582a2` |

## Notes

- This batch used real runtime requests only.
- No `tests/`, `pytest`, or `unittest` were executed.
