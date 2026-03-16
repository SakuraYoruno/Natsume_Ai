# F-17 chat latency (formal LAN dual-node)

- batch_id: `20260315_batch04_formal_lan_mainchain`
- exec_time: `2026-03-15 00:46-00:48 CST`
- result: `BLOCK`

## Observation

1. Plan threshold: `first_token p95 <= 4s`, `commit p95 <= 8s`.
2. Sample `req_8a9a7212574048d5ae7cb8adcb0189e8`:
   - first render chunk: `28817ms`
   - end-to-end elapsed: `29150ms`
   - `audit_refs.llm.first_token_latency_ms=5449`
3. Sample `req_f22e3993c272435084f81a63fe5339f4`:
   - first render chunk: `16595ms`
   - end-to-end elapsed: `16805ms`
   - `audit_refs.llm.first_token_latency_ms=9135`
4. Both samples exceed the release threshold even before p95 aggregation.

## Judgement

`F-17` is `BLOCK`. Current chat latency is far above the release target, and the issue is repeatable across two simple samples.

## Evidence

- `70_raw_evidence/F-08_formal_chat_req_req_8a9a7212574048d5ae7cb8adcb0189e8.json`
- `70_raw_evidence/F-08_formal_chat_req_req_f22e3993c272435084f81a63fe5339f4.json`
