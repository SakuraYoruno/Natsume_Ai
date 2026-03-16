# F-08 Chat Main Chain

- Batch: `20260315_batch06_formal_lan_runtime_fix`
- Topology: `Host 127.0.0.1:18080 -> Edge 192.168.1.101:17800`

## Result

`PASS`

## Evidence

### Request 1

- Request ID: `req_41eed88b1d3840638cfd0aa62d2582a2`
- User message: `今の気分を短く教えて`
- Clean render: `今、ちょっと元気です。`
- Commit narrative: `今、ちょっと元気です。`
- Route target: `host_8b`
- VTS: `APPLIED`
- TTS: `ACCEPTED`

### Request 2

- Request ID: `req_66f10138b701442abdb5c403dc205b48`
- User message: `今いちばん優先することを短く教えて`
- Clean render: `今すぐ行動を起こすことが一番大切です。`
- Commit narrative: `今すぐ行動を起こすことが一番大切です。`
- Route target: `host_8b`
- VTS: `APPLIED`
- TTS: `ACCEPTED`

## Delta

- Fixed stream JSON shell leakage.
- Fixed `COMMIT.narrative` pollution from malformed provider wrapper text.
- Background injection path is now single-path through `RecallPack -> prompt_context`.

## Remaining Risk

- Main chain is available, but semantic quality and retrieval stability still need separate gating.
