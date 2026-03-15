# F-16 text quality (formal LAN dual-node)

- batch_id: `20260315_batch04_formal_lan_mainchain`
- exec_time: `2026-03-15 00:46-00:48 CST`
- result: `BLOCK`

## Observation

1. Both prompts were written in Chinese, but both answers were returned in Japanese.
2. Sample `req_8a9a7212574048d5ae7cb8adcb0189e8` introduced an unrelated identifier `seal-prop-1773468164886`, which was not present in the request and does not match the current known blocker set.
3. Sample `req_f22e3993c272435084f81a63fe5339f4` obeyed the general topic but still violated the requested output language and format.
4. Both samples returned a JSON wrapper string rather than a clean user-facing sentence.

## Judgement

`F-16` is `BLOCK`. Current text output does not reliably follow the query language, format, or factual scope, so it is not acceptable for daily use.

## Evidence

- `70_raw_evidence/F-08_formal_chat_req_req_8a9a7212574048d5ae7cb8adcb0189e8.json`
- `70_raw_evidence/F-08_formal_chat_req_req_f22e3993c272435084f81a63fe5339f4.json`
