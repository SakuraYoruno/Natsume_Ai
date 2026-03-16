# F-01 / F-02 / F-03 / F-08 / F-09 / F-16 / F-17 / F-29 execution ledger (batch-05 formal LAN)

## 1. Execution metadata

| item | value |
|---|---|
| batch_id | `20260315_batch05_formal_lan_restart_retest` |
| exec_date | `2026-03-15` |
| operator | `Codex` |
| host_env | `formal LAN host on port 18080` |
| edge_env | `remote edge on 192.168.1.101:17800` |
| start_time | `2026-03-15 03:33 CST` |
| end_time | `2026-03-15 03:39 CST` |
| request_samples | `2 concurrent chat samples + health/config probes` |

## 2. Summary

| item | value |
|---|---|
| applicable_count | `8` |
| pass_count | `0` |
| block_count | `8` |
| final_decision | `BLOCK` |

## 3. Execution table

| ID | scope | status | system_evidence | desktop_evidence | blocker_summary | retest_status |
|---|---|---|---|---|---|---|
| F-01 | Host/Edge/VTS health | `BLOCK` | `10_health_runtime/F-01_health_summary_20260315_batch05_formal_lan.md` | `remote edge white-box voice path not ready` | host is ready, but remote edge remains degraded with `tts=UNLOADED` and `vts=DISCONNECTED` | `pending` |
| F-02 | startup/config/keys | `BLOCK` | `10_health_runtime/F-02_config_keys_20260315_batch05_formal_lan.md` | `not_applicable_this_batch` | host and edge disagree on fallback capability; key-profile state unresolved; inventory path still points to fixture data | `pending` |
| F-03 | dependency readiness | `BLOCK` | `10_health_runtime/F-03_dependency_readiness_20260315_batch05_formal_lan.md` | `audible_tts_and_visible_vts_not_yet_possible` | formal white-box voice dependencies are still not ready | `pending` |
| F-08 | chat main chain | `BLOCK` | `20_chain_quality/F-08_chat_mainchain_20260315_batch05_formal_lan.md` | `not_started_due_to_voice_blockers` | chat commits, but retrieval, prompt context, host model routing, and edge TTS remain degraded | `pending` |
| F-09 | retrieval accuracy | `BLOCK` | `20_chain_quality/F-09_retrieval_accuracy_20260315_batch05_formal_lan.md` | `not_applicable_this_batch` | seven-route retrieval executes but returns zero usable candidates; bond graph is broken | `pending` |
| F-16 | text quality | `BLOCK` | `20_chain_quality/F-16_text_quality_20260315_batch05_formal_lan.md` | `operator_visible_query_answer_mismatch_established` | current answers are semantically misaligned with the actual prompts | `pending` |
| F-17 | chat latency | `BLOCK` | `20_chain_quality/F-17_chat_latency_20260315_batch05_formal_lan.md` | `not_applicable_this_batch` | both chat samples are still around `10.8-11.1s` total elapsed | `pending` |
| F-29 | audit and trace closure | `BLOCK` | `40_webui_obs/F-29_trace_closure_20260315_batch05_formal_lan.md` | `not_applicable_this_batch` | trace graph still exposes only the governor node | `pending` |

## 4. Replay note

1. Batch-04 had one observed `INTERNAL_SERVER_ERROR` under parallel requests.
2. Batch-05 replayed two concurrent requests with unique sessions, and both reached `COMMIT`.
3. The concurrency fault is therefore not closed, but it was not reproduced in this replay. The formal blocker set in batch-05 stays focused on the persistent runtime defects above.

