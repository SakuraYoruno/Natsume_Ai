# F-08 chat main chain (formal LAN dual-node, batch-05)

- batch_id: `20260315_batch05_formal_lan_restart_retest`
- exec_time: `2026-03-15 03:35 CST`
- result: `BLOCK`

## Observation

1. Two real `POST /v1/chat` requests were replayed concurrently against the formal LAN topology with unique sessions.
2. In this batch, both requests reached `COMMIT` and `APPROVE`; the earlier one-off parallel `INTERNAL_SERVER_ERROR` from batch-04 was not reproduced.
3. The main chain is still materially degraded in both samples:
   - retrieval = `DEGRADED`
   - `candidate_count=0`
   - prompt context fell to `PROMPT_CONTEXT_MINIMAL_FALLBACK`
   - model routing fell back to cloud because `HOST_CHAT_HOST_8B_UNHEALTHY`
   - edge TTS dispatch failed with `EDGE_RPC_AUTH_EXPIRED`

## Judgement

`F-08` remains `BLOCK`. The chat chain is reachable, but not releasable: it is committing under degraded retrieval, degraded prompt assembly, cloud fallback, and failed edge TTS dispatch. This is not a healthy personal full-function release state.

## Evidence

- `70_raw_evidence/F-08_formal_lan_chat_pair_20260315_batch05.json`
- `70_raw_evidence/F-02_formal_lan_clock_skew_20260315_batch05.json`

