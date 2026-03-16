# F-17 chat latency (formal LAN dual-node, batch-05)

- batch_id: `20260315_batch05_formal_lan_restart_retest`
- exec_time: `2026-03-15 03:35 CST`
- result: `BLOCK`

## Observation

1. Current end-to-end chat latency remains far above the personal release target:
   - sample A total elapsed = `10791ms`
   - sample B total elapsed = `11094ms`
2. First-token latency is also high:
   - sample A = `4286ms`
   - sample B = `4570ms`
3. Retrieval itself is not the only bottleneck; both requests also fell back to cloud LLM and failed edge TTS dispatch.

## Judgement

`F-17` remains `BLOCK`. The current dual-node chat path is too slow for the target low-latency personal daily-use baseline.

## Evidence

- `70_raw_evidence/F-08_formal_lan_chat_pair_20260315_batch05.json`

