# Personal full-function seal test execution report (batch-03 formal LAN)

## 1. Basic information

| item | value |
|---|---|
| batch_id | `20260315_batch03_formal_lan_gate` |
| exec_date | `2026-03-15` |
| operator | `Codex` |
| environment | `formal LAN dual-node` |
| scope | `personal full-function` |
| final_decision | `BLOCK` |

## 2. Summary

1. This batch is the first formal dual-node gate after replacing the older localhost host process.
2. The host control plane is now correctly attached to the remote edge node `192.168.1.101:17800`.
3. Formal seal testing still cannot proceed past the opening gate because the remote edge node remains degraded and VTS is not connected.

## 3. Findings

| ID | finding | impact | evidence | next action |
|---|---|---|---|---|
| F-01 | formal host topology corrected, but remote edge health is still `degraded` | opening health gate not passed; white-box voice/VTS chain cannot start | `10_health_runtime/F-01_health_summary_20260315_formal_lan.md` | restore remote edge to non-degraded state and reconnect VTS |
| F-02 | host/edge fallback config drift, unresolved key profile state, readiness still references a test fixture inventory path | startup/config gate not reliable enough for release judgement | `10_health_runtime/F-02_config_keys_20260315_formal_lan.md` | align host/edge config, clear startup warnings, remove test-fixture dependency from readiness |
| F-03 | voice/VTS dependencies are not formally ready: `tts_state=UNLOADED`, `vts_state=DISCONNECTED` | user-audible TTS and visible VTS verification cannot begin | `10_health_runtime/F-03_dependency_readiness_20260315_formal_lan.md` | load remote TTS runtime and connect VTS on the edge desktop node |

## 4. White-box observation status

| item | result | note |
|---|---|---|
| TTS actually audible | `not started` | blocked by `F-03` |
| VTS expression actually visible | `not started` | blocked by `F-01` |
| VTS lip-sync actually visible | `not started` | blocked by `F-01` |

## 5. Conclusion

Formal seal testing has started, and the topology is now aligned with the real LAN dual-node workflow. The batch result is still `BLOCK`, but the blocker is now the actual remote edge runtime state rather than a localhost shortcut. The next retest should begin from remote edge voice/VTS recovery, then immediately re-run `F-01 ~ F-03`.
