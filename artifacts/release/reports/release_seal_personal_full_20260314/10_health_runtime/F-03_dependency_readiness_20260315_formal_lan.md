# F-03 dependency readiness (formal LAN dual-node)

- batch_id: `20260315_batch03_formal_lan_gate`
- exec_time: `2026-03-15 00:36-00:38 CST`
- result: `BLOCK`

## Observation

1. Host-side semantic recall is running in real mode: `use_mock=False`, `chroma=127.0.0.1:18000`.
2. The remote edge node is reachable over LAN, and core edge services answer health probes.
3. Voice/VTS runtime dependencies are still not fully ready for white-box testing:
   - `tts_state=UNLOADED`
   - `vts_state=DISCONNECTED`
   - `fallback_available=false`
4. Current state is enough for connectivity validation, but not enough for the formal white-box chain that requires audible TTS and visible VTS expression/lip-sync.

## Judgement

`F-03` remains `BLOCK`. Dependency readiness for formal dual-node seal testing is not satisfied until the remote edge node can actually synthesize audio and attach to VTS in a visible/audible state.

## Evidence

- `70_raw_evidence/F-01_formal_lan_edge_health_20260315.json`
- `70_raw_evidence/F-02_formal_lan_startup_excerpt_20260315.txt`
- `runtime/host/logs/sealtest_host_lan.stdout.log`
