# F-03 dependency readiness (formal LAN dual-node, batch-05)

- batch_id: `20260315_batch05_formal_lan_restart_retest`
- exec_time: `2026-03-15 03:33-03:36 CST`
- result: `BLOCK`

## Observation

1. Host and edge can reach each other over the formal LAN topology.
2. Core host services respond, and the chat main chain can still reach `COMMIT`.
3. White-box voice dependencies are not actually ready:
   - remote edge `tts_state=UNLOADED`
   - remote edge `vts_state=DISCONNECTED`
   - current TTS dispatch fails on real auth with a cross-node time drift of about `19s`

## Judgement

`F-03` remains `BLOCK`. Dependency readiness for formal dual-node seal testing is not satisfied until the remote edge node can synthesize audible speech and attach to VTS in a visible state without auth expiry.

## Evidence

- `70_raw_evidence/F-01_formal_lan_health_snapshot_20260315_batch05.json`
- `70_raw_evidence/F-02_formal_lan_clock_skew_20260315_batch05.json`

