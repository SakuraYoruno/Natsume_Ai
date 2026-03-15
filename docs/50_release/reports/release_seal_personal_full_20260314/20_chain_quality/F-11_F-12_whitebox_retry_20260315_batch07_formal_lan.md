# F-11 / F-12 White-box Retry

- Batch: `20260315_batch07_formal_lan_whitebox_retry`
- Topology: `Host 127.0.0.1:18080 -> Edge 192.168.1.101:17800`

## White-box Observation

Operator reported:

- TTS audio not heard on edge desktop
- VTS expression not visibly observed
- VTS lip sync / mouth movement not visibly observed

## Runtime Evidence

### Request IDs retried

- `req_41eed88b1d3840638cfd0aa62d2582a2`
- `req_66f10138b701442abdb5c403dc205b48`

### Host-side ledger rows

Only outbound acceptance events exist:

- `HOST_STREAM_VTS`
- `HOST_STREAM_TTS`

No edge playback completion rows were found for the same request IDs.

### Edge health snapshot

- `tts.health.state=READY`
- `vts.health.state=AUTHENTICATED`
- `vts.health.current_expression=exp_neutral_01`
- `event_reporter.total_sent=0`
- `event_reporter.total_failed=0`

## Conclusion

System-side status is not sufficient for white-box pass.

This item remains `BLOCK` until both conditions are true:

1. edge desktop operator can actually hear the generated audio and see the avatar reaction
2. edge completion events can be observed end-to-end instead of only host outbound acceptance
