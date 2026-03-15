# F-02 startup/config/keys (formal LAN dual-node)

- batch_id: `20260315_batch03_formal_lan_gate`
- exec_time: `2026-03-15 00:36-00:38 CST`
- result: `BLOCK`

## Observation

1. Host control plane now points to the correct remote edge node: `192.168.1.101:17800`.
2. The host startup view and the edge runtime view are inconsistent:
   - host startup config reports `fallback_llm_enabled=true`
   - edge runtime health reports `EDGE_FALLBACK_LLM_ENABLED=false`
3. `host /ready` still reports:
   - `host_key_profile_status=null`
   - `key_profiles_total=0`
   - `node_inventory_path=tests\\fixtures\\services\\good\\node_inventory_9.json`
4. Host startup log still contains degraded startup signals:
   - `AuditRetention` scheduler registration error: unexpected keyword `display_name`
   - degraded module list includes `edge_rpc`, `audit_retention_scheduler`, `proactive_talk_scheduler`

## Judgement

Formal seal testing cannot treat this startup/config state as releasable. The topology is corrected, but config drift and startup degradation remain: host and edge disagree on fallback capability, key profile state is unresolved, and readiness still points at a test fixture inventory path. `F-02` remains `BLOCK`.

## Evidence

- `70_raw_evidence/F-01_formal_lan_topology_20260315.json`
- `70_raw_evidence/F-02_formal_lan_startup_excerpt_20260315.txt`
- `runtime/host/logs/sealtest_host_lan.stdout.log`
