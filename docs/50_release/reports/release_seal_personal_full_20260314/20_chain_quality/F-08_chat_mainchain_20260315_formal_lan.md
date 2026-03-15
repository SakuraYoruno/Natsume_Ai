# F-08 chat main chain (formal LAN dual-node)

- batch_id: `20260315_batch04_formal_lan_mainchain`
- exec_time: `2026-03-15 00:46-00:48 CST`
- result: `BLOCK`

## Samples

1. `req_8a9a7212574048d5ae7cb8adcb0189e8`
2. `req_f22e3993c272435084f81a63fe5339f4`

## Observation

1. Both requests reached `COMMIT` and carried stable `request_id`, so the HTTP entrypoint is alive.
2. The chain is materially degraded instead of healthy:
   - retrieval timed out in both samples
   - `context_pack_builder` degraded in both samples
   - TTS dispatch to remote edge was rejected in both samples with `EDGE_RPC_AUTH_EXPIRED`
   - route fell back to cloud because `HOST_CHAT_HOST_8B_UNHEALTHY`
3. The render payload is returned as a JSON wrapper string rather than a clean final natural-language answer.

## Judgement

`F-08` remains `BLOCK`. The chat entrypoint is callable, but the actual main chain is not healthy enough for formal release: critical subchains are degraded, edge voice dispatch is rejected, and the response path is not clean.

## Evidence

- `70_raw_evidence/F-08_formal_chat_req_req_8a9a7212574048d5ae7cb8adcb0189e8.json`
- `70_raw_evidence/F-08_formal_chat_req_req_f22e3993c272435084f81a63fe5339f4.json`
