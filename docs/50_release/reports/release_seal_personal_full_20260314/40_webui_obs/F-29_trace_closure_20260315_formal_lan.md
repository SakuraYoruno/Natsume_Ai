# F-29 audit and trace closure (formal LAN dual-node)

- batch_id: `20260315_batch04_formal_lan_mainchain`
- exec_time: `2026-03-15 00:46-00:48 CST`
- result: `BLOCK`

## Observation

1. Both requests have valid `request_id` and do produce observability data.
2. `/obs/trace/{request_id}` is incomplete for both samples:
   - `node_count=1`
   - only `governor` appears
3. `/observability/trace` for sample `req_8a9a7212574048d5ae7cb8adcb0189e8` only shows three timeline sources:
   - `avatar_action_log`
   - `governor_log`
   - `intent_log`
4. Critical execution stages that should be traceable from a formal chain are missing from the trace closure:
   - retrieval
   - llm execution
   - TTS dispatch failure
   - context-pack degradation

## Judgement

`F-29` is `BLOCK`. The system emits some audit evidence, but the end-to-end request trace is not closed enough to support formal debugging or release judgement.

## Evidence

- `70_raw_evidence/F-08_formal_chat_req_req_8a9a7212574048d5ae7cb8adcb0189e8.json`
- `70_raw_evidence/F-08_formal_chat_req_req_f22e3993c272435084f81a63fe5339f4.json`
