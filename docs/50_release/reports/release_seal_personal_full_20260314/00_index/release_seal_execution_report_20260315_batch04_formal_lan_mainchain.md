# Personal full-function seal test execution report (batch-04 formal LAN mainchain)

## 1. Basic information

| item | value |
|---|---|
| batch_id | `20260315_batch04_formal_lan_mainchain` |
| exec_date | `2026-03-15` |
| operator | `Codex` |
| environment | `formal LAN dual-node` |
| scope | `chat main chain / retrieval / quality / latency / trace closure` |
| final_decision | `BLOCK` |

## 2. Summary

1. Two real `POST /v1/chat` requests were executed against the formal LAN topology.
2. Both requests reached `COMMIT`, but both carried material degradations across retrieval, context pack, model routing, edge TTS dispatch, and trace closure.
3. The current system is not blocked on HTTP reachability anymore; it is blocked on actual runtime correctness and quality.

## 3. Findings

| ID | finding | impact | evidence |
|---|---|---|---|
| F-08 | main chain commits under degraded retrieval/context-pack/edge-TTS conditions | chat appears alive but is not truly healthy | `20_chain_quality/F-08_chat_mainchain_20260315_formal_lan.md` |
| F-09 | retrieval timed out in both samples with zero routed candidates | recall accuracy unavailable | `20_chain_quality/F-09_retrieval_accuracy_20260315_formal_lan.md` |
| F-16 | answers returned in Japanese to Chinese prompts; one sample hallucinated unrelated `seal-prop-*` blocker | text quality unacceptable for personal daily use | `20_chain_quality/F-16_text_quality_20260315_formal_lan.md` |
| F-17 | sample latencies are far above release thresholds | current chat responsiveness fails release gate | `20_chain_quality/F-17_chat_latency_20260315_formal_lan.md` |
| F-29 | trace closure only exposes governor node and misses downstream execution | failures cannot be fully diagnosed from observability evidence | `40_webui_obs/F-29_trace_closure_20260315_formal_lan.md` |

## 4. New blocker set exposed by this batch

1. `HOST_CHAT_HOST_8B_UNHEALTHY`
2. `RETRIEVAL_TIMEOUT`
3. `EDGE_RPC_AUTH_EXPIRED`
4. `BOND_GRAPH_FAILED`
5. `context_pack_builder` degraded: `'ContextPack' object has no attribute 'slots'`

## 4.1 Root-cause follow-up

1. Host-to-edge TTS auth expiry is explained by real dual-node clock skew: the edge node is about `18.7s` behind host, exceeding the `15s` auth window.
2. `context_pack_builder` degradation is a code-level interface mismatch: `chat_stream_handler` still reads `_cp.slots`, but current `ContextPack` has no `slots` field.
3. Chat routing falls off the host-8b fast path because the host-8b health probe currently fails with connection refused.

## 5. Conclusion

Formal seal testing has progressed from topology repair into the real chat chain. The current blocker set is now concrete and runtime-visible: host-side main-chain degradation, retrieval timeout, edge RPC auth drift on TTS, text quality mismatch, and incomplete trace closure. The next batch should target these runtime defects before moving deeper into white-box audio/VTS verification.
