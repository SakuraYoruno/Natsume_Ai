# F-02 root-cause follow-up (formal LAN dual-node)

- batch_id: `20260315_batch04_formal_lan_mainchain`
- exec_time: `2026-03-15 00:50-00:52 CST`

## Confirmed root causes

1. `EDGE_RPC_AUTH_EXPIRED` is backed by real node clock skew, not by a reused stale request timestamp.
   - Host-side `edge_rpc_client._build_envelope()` generates a fresh `ts_ms` at send time.
   - Edge-side `validate_edge_rpc_auth()` rejects when `abs(current - envelope.ts_ms) > auth_window_ms`.
   - Measured LAN skew is about `-18684ms`, with only `112ms` HTTP roundtrip, so the expiry is explained by clock drift across the two nodes.
2. `context_pack_builder` degradation is a real interface mismatch after refactor.
   - `chat_stream_handler` reads `_cp.slots.values()`
   - current `ContextPack` dataclass exposes direct fields such as `world_canon/style_canon/...` and does not define a `slots` attribute
3. `HOST_CHAT_HOST_8B_UNHEALTHY` is an active runtime probe failure.
   - routing code probes host-8b endpoints and falls back when probe fails
   - current runtime reason is `HOST_8B_PROBE_FAILED:url_error:<urlopen error [WinError 10061] ... refused>`

## Impact

These three issues are enough to explain the current batch-04 blocker cluster:

- edge TTS dispatch rejection
- main-chain degradation
- forced cloud routing
- inflated chat latency

## Evidence

- `70_raw_evidence/F-02_formal_lan_clock_skew_20260315.json`
- `host_brain/channels/edge_rpc_client.py`
- `edge_client/rpc/auth.py`
- `host_brain/app/chat_stream_handler.py`
- `host_brain/app/schemas/context_pack.py`
- `host_brain/app/graph_config_builder.py`
