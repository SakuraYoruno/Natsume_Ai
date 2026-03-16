# F-08 / F-09 / F-16 / F-17 / F-29 execution ledger (batch-04 formal LAN)

## 1. Execution metadata

| item | value |
|---|---|
| batch_id | `20260315_batch04_formal_lan_mainchain` |
| exec_date | `2026-03-15` |
| operator | `Codex` |
| host_env | `formal LAN host on port 18080` |
| edge_env | `remote edge on 192.168.1.101:17800` |
| start_time | `2026-03-15 00:46 CST` |
| end_time | `2026-03-15 00:48 CST` |
| request_samples | `2` |

## 2. Summary

| item | value |
|---|---|
| applicable_count | `5` |
| pass_count | `0` |
| block_count | `5` |
| final_decision | `BLOCK` |

## 3. Execution table

| ID | scope | status | system_evidence | desktop_evidence | blocker_summary | retest_status |
|---|---|---|---|---|---|---|
| F-08 | chat main chain | `BLOCK` | `20_chain_quality/F-08_chat_mainchain_20260315_formal_lan.md` | `not_started_due_to_voice_blockers` | chat reaches commit but retrieval, context pack, host model routing, and edge TTS dispatch are degraded | `pending` |
| F-09 | retrieval accuracy | `BLOCK` | `20_chain_quality/F-09_retrieval_accuracy_20260315_formal_lan.md` | `not_applicable_this_batch` | both samples timed out in retrieval with zero routed candidates | `pending` |
| F-16 | text quality | `BLOCK` | `20_chain_quality/F-16_text_quality_20260315_formal_lan.md` | `operator-visible answer mismatch already established` | wrong output language, wrong format, and hallucinated blocker identifier | `pending` |
| F-17 | chat latency | `BLOCK` | `20_chain_quality/F-17_chat_latency_20260315_formal_lan.md` | `not_applicable_this_batch` | both samples exceed the release threshold by a wide margin | `pending` |
| F-29 | audit and trace closure | `BLOCK` | `40_webui_obs/F-29_trace_closure_20260315_formal_lan.md` | `not_applicable_this_batch` | trace only shows governor node and misses retrieval/llm/tts failure closure | `pending` |
