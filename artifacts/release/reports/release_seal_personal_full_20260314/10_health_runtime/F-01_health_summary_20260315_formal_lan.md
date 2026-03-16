# F-01 Host/Edge/VTS health (formal LAN dual-node)

- batch_id: `20260315_batch03_formal_lan_gate`
- exec_time: `2026-03-15 00:36-00:38 CST`
- result: `BLOCK`

## Observation

1. `18080` is now owned by the formal LAN host process, and `/ready` confirms `edge_rpc_host=192.168.1.101`, `edge_rpc_port=17800`.
2. `host /health` returns `200` and reports `required_unavailable=0`.
3. The remote edge node `192.168.1.101:17800` is reachable, but `/v1/edge/health` still returns `event.status=degraded`.
4. The edge voice path is not in a releasable state:
   - `edge.rpc.fallback_llm.available=false`
   - `tts_state=UNLOADED`
   - `vts_state=DISCONNECTED`

## Judgement

The LAN topology mismatch on the host side has been corrected, but `F-01` still cannot pass. The formal standard requires the enabled Host/Edge/VTS chain to be healthy with no unexplained degraded state. The remote edge node is still degraded and VTS is not connected, so this item remains `BLOCK`.

## Evidence

- `70_raw_evidence/F-01_formal_lan_topology_20260315.json`
- `70_raw_evidence/F-01_formal_lan_edge_health_20260315.json`
- `runtime/host/logs/sealtest_host_lan.stdout.log`
