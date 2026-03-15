# F-01 ~ F-03 execution ledger (batch-03 formal LAN)

## 1. Execution metadata

| item | value |
|---|---|
| batch_id | `20260315_batch03_formal_lan_gate` |
| exec_date | `2026-03-15` |
| operator | `Codex + user white-box observation pending` |
| host_env | `formal LAN host on current node, port 18080` |
| edge_env | `remote edge on 192.168.1.101:17800` |
| vts_env | `remote edge desktop/VTS node, not yet connected` |
| start_time | `2026-03-15 00:36 CST` |
| end_time | `2026-03-15 00:38 CST` |

## 2. Summary

| item | value |
|---|---|
| applicable_count | `3` |
| pass_count | `0` |
| block_count | `3` |
| n_a_count | `0` |
| final_decision | `BLOCK` |

## 3. Execution table

| ID | scope | status | system_evidence | desktop_evidence | blocker_summary | retest_status |
|---|---|---|---|---|---|---|
| F-01 | Host/Edge/VTS health | `BLOCK` | `10_health_runtime/F-01_health_summary_20260315_formal_lan.md` `70_raw_evidence/F-01_formal_lan_topology_20260315.json` `70_raw_evidence/F-01_formal_lan_edge_health_20260315.json` | `remote VTS white-box observation not started` | Host topology fixed, but remote edge remains `degraded`; `vts_state=DISCONNECTED`; `tts_state=UNLOADED` | `pending` |
| F-02 | startup/config/keys | `BLOCK` | `10_health_runtime/F-02_config_keys_20260315_formal_lan.md` `70_raw_evidence/F-02_formal_lan_startup_excerpt_20260315.txt` | `not_applicable_this_batch` | host/edge fallback config drift; key profile state unresolved; readiness still points at test fixture inventory | `pending` |
| F-03 | dependency readiness | `BLOCK` | `10_health_runtime/F-03_dependency_readiness_20260315_formal_lan.md` `70_raw_evidence/F-01_formal_lan_edge_health_20260315.json` | `blocked_before_voice_white_box` | voice/VTS runtime not ready for audible/visible dual-node verification | `pending` |

## 4. Notes

1. The older local-edge host process on port `18080` was stopped and replaced by the formal LAN host before this batch was recorded.
2. This batch is the first formal dual-node gate, not a localhost bootstrap record.
