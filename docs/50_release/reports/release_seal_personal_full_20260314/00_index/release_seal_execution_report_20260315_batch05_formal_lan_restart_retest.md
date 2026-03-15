# Personal full-function seal test execution report (batch-05 formal LAN restart retest)

## 1. Basic information

| item | value |
|---|---|
| batch_id | `20260315_batch05_formal_lan_restart_retest` |
| exec_date | `2026-03-15` |
| operator | `Codex` |
| environment | `formal LAN dual-node` |
| scope | `health / config drift / dependency readiness / chat main chain / retrieval / quality / latency / trace closure` |
| final_decision | `BLOCK` |

## 2. Summary

1. Formal dual-node seal testing continued against `Host 127.0.0.1:18080 -> Edge 192.168.1.101:17800`.
2. Host-side control plane is now stable and serves the current refactored runtime path.
3. The release is still blocked by persistent runtime defects on the real chain: remote edge voice readiness, host-edge auth skew, cloud fallback from unavailable host-8b, degraded prompt context, empty retrieval output, broken bond enrichment, poor text quality, and incomplete trace closure.

## 3. Findings

| ID | finding | impact | evidence |
|---|---|---|---|
| F-01 | remote edge is still degraded with `tts=UNLOADED`, `vts=DISCONNECTED` | formal white-box voice chain cannot start | `10_health_runtime/F-01_health_summary_20260315_batch05_formal_lan.md` |
| F-02 | host and edge still disagree on fallback capability; key-profile state unresolved; readiness still points at fixture inventory | startup/config baseline is not clean enough for release | `10_health_runtime/F-02_config_keys_20260315_batch05_formal_lan.md` |
| F-03 | real TTS dispatch still fails under dual-node auth drift of about `19s` | audible TTS and visible VTS cannot be validated | `10_health_runtime/F-03_dependency_readiness_20260315_batch05_formal_lan.md` |
| F-08 | chat chain reaches `COMMIT`, but only under degraded retrieval/prompt/routing/TTS conditions | chat appears alive but is not healthy | `20_chain_quality/F-08_chat_mainchain_20260315_batch05_formal_lan.md` |
| F-09 | seven-route retrieval executes but returns zero usable candidates; bond enrichment is broken | recall accuracy and graph enrichment are both not releasable | `20_chain_quality/F-09_retrieval_accuracy_20260315_batch05_formal_lan.md` |
| F-16 | current answers are semantically misaligned with the actual prompts | text quality is not usable for personal daily use | `20_chain_quality/F-16_text_quality_20260315_batch05_formal_lan.md` |
| F-17 | current dual-node chat path still takes about `10.8-11.1s` | latency fails the low-latency release baseline | `20_chain_quality/F-17_chat_latency_20260315_batch05_formal_lan.md` |
| F-29 | trace graph still exposes only the governor node | observability evidence cannot close failures end-to-end | `40_webui_obs/F-29_trace_closure_20260315_batch05_formal_lan.md` |

## 4. Batch-05 interpretation

1. This batch improved one point of clarity: the earlier batch-04 parallel `INTERNAL_SERVER_ERROR` did not reproduce when two unique-session requests were replayed concurrently.
2. That replay does not clear concurrency risk, but it does show the formal blocker set is now dominated by persistent defects rather than topology mistakes.
3. The highest-value repair order remains:
   - sync host/edge clocks or fix auth expiry path
   - restore host-8b availability
   - fix prompt-context slot validation for current emotion fields
   - repair bond graph runtime API mismatch
   - close trace graph beyond the governor node

## 5. Conclusion

Formal dual-node seal testing is active and producing stable evidence. The topology is correct, but the release is still `BLOCK` on the real Host -> Edge chain. The next retest should happen only after the clock/auth path, host-8b bootstrap, prompt-context validation, and bond graph runtime mismatch are repaired.

