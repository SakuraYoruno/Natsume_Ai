# F-02 startup/config/keys (formal LAN dual-node, batch-05)

- batch_id: `20260315_batch05_formal_lan_restart_retest`
- exec_time: `2026-03-15 03:35-03:36 CST`
- result: `BLOCK`

## Observation

1. Host startup state is cleaner than the previous batch: `/ready` reports `READY`, and `startup_report.summary.boot_status=READY`.
2. Formal config drift still exists between host and remote edge:
   - host startup snapshot says `fallback_llm_enabled=true`
   - edge runtime health says `fallback_enabled=false` and `fallback_available=false`
3. Key-profile state is still unresolved:
   - `key_profiles_total=0`
   - `host_key_profile_status=null`
4. Host readiness still references a test-fixture inventory path:
   - `node_inventory_path=tests\\fixtures\\services\\good\\node_inventory_9.json`

## Judgement

`F-02` remains `BLOCK`. This batch no longer shows the earlier startup degradation noise, but the formal release baseline is still not clean: host and edge disagree on fallback capability, key-profile state is unresolved, and readiness still points at a fixture inventory path.

## Evidence

- `70_raw_evidence/F-01_formal_lan_health_snapshot_20260315_batch05.json`

