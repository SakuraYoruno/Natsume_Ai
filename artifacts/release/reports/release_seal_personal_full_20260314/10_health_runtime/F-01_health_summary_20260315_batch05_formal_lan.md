# F-01 Host/Edge/VTS health (formal LAN dual-node, batch-05)

- batch_id: `20260315_batch05_formal_lan_restart_retest`
- exec_time: `2026-03-15 03:33-03:36 CST`
- result: `BLOCK`

## Observation

1. Formal LAN topology is correct: host answers on `127.0.0.1:18080`, and the formal edge target remains `192.168.1.101:17800`.
2. Host-side health is stable in this batch:
   - `/health` = `OK`
   - `/ready` = `READY`
   - `tools` = `OK`
   - `bond` = `OK`
   - `scheduler` = `HEALTHY`
3. The remote edge node is reachable, but its runtime health is still degraded:
   - `event.status=degraded`
   - `tts_state=UNLOADED`
   - `vts_state=DISCONNECTED`
   - `fallback_available=false`

## Judgement

`F-01` remains `BLOCK`. Topology is now correct, but the enabled Host -> Edge -> VTS voice chain is not healthy enough for formal dual-node release. The edge node is still degraded, TTS is not loaded, and VTS is disconnected.

## Evidence

- `70_raw_evidence/F-01_formal_lan_health_snapshot_20260315_batch05.json`

