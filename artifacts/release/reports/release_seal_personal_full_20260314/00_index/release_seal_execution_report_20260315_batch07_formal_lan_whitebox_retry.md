# Release Seal Execution Report

- Batch: `20260315_batch07_formal_lan_whitebox_retry`
- Conclusion: `WHITEBOX_FAIL`

## Scope

Retry formal LAN white-box verification for:

- audible TTS on edge desktop
- visible VTS expression on edge desktop
- visible lip sync / mouth movement on edge desktop

## Result

`FAIL`

Operator observation on the edge node desktop remains:

- no audible output confirmed
- no visible expression change confirmed
- no visible mouth/lip movement confirmed

## Runtime-side Findings

- Host -> Edge RPC path is healthy.
- Edge health reports:
  - `tts.health.state=READY`
  - `vts.health.state=AUTHENTICATED`
  - `vts.health.current_expression=exp_neutral_01`
- Real chat retries still only prove system-side acceptance:
  - host ledger contains `HOST_STREAM_VTS` and `HOST_STREAM_TTS`
  - host ledger does **not** contain edge playback completion events for the same request IDs
- Edge `event_reporter` health shows:
  - `enabled=true`
  - `total_sent=0`
  - `total_failed=0`
  - `cached_events=0`

## Interpretation

Current evidence only proves:

- host sent the voice/VTS requests
- edge accepted the requests

Current evidence does **not** prove:

- edge desktop speakers actually played audible audio
- VTube Studio window actually showed observable expression / mouth movement
- edge completion events returned to host

## Likely Blocker

`EDGE_EVENT_REPORT_HOST_URL` is not configured in the current repo environment reference, and the running edge health snapshot is consistent with a non-reporting event reporter. Without edge-to-host completion reporting, Host-side observability cannot distinguish:

- accepted but not actually audible/visible
- started and finished correctly on the edge desktop

## White-box Verdict

For personal formal release seal testing, this batch is a hard `BLOCK`.
