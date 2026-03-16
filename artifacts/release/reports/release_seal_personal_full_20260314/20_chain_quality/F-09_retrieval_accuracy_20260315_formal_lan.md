# F-09 retrieval accuracy (formal LAN dual-node)

- batch_id: `20260315_batch04_formal_lan_mainchain`
- exec_time: `2026-03-15 00:46-00:48 CST`
- result: `BLOCK`

## Observation

1. Both chat samples report:
   - `retrieval.status=DEGRADED`
   - `reason_code=RETRIEVAL_TIMEOUT`
   - `route_count=0`
   - `candidate_count=0`
   - `rerank_topk=0`
2. One sample also reports `bond_graph.status=DEGRADED` with `BOND_GRAPH_FAILED`.
3. Because retrieval produced no actual route rows or candidates, there is no basis to claim recall accuracy or evidence relevance.

## Judgement

`F-09` is a direct `BLOCK`. The system currently has recall timeout rather than recall quality. With zero routed candidates, retrieval accuracy is effectively untested and unavailable.

## Evidence

- `70_raw_evidence/F-08_formal_chat_req_req_8a9a7212574048d5ae7cb8adcb0189e8.json`
- `70_raw_evidence/F-08_formal_chat_req_req_f22e3993c272435084f81a63fe5339f4.json`
