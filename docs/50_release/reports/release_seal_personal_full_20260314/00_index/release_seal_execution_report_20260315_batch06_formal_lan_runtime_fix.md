# Release Seal Execution Report

- Batch: `20260315_batch06_formal_lan_runtime_fix`
- Conclusion: `PARTIAL_PASS_WITH_BLOCKERS`

## Summary

This batch validated the refactored `RecallPack -> prompt_context -> prompt_builder` path on the formal LAN dual-node topology.

The stream/commit narrative bug is fixed:

- `RENDER` no longer leaks malformed JSON shell text
- `COMMIT.narrative` is now clean visible text
- node output `contract_payload` is reconstructed correctly on the stream path

## Real Requests

### Sample 1

- Request: `今の気分を短く教えて`
- Request ID: `req_41eed88b1d3840638cfd0aa62d2582a2`
- Result: `今、ちょっと元気です。`
- Retrieval: `DEGRADED / RETRIEVAL_TIMEOUT / route_count=0`
- Prompt context tier: `core_three`
- VTS: `APPLIED`
- TTS: `ACCEPTED`

### Sample 2

- Request: `今いちばん優先することを短く教えて`
- Request ID: `req_66f10138b701442abdb5c403dc205b48`
- Result: `今すぐ行動を起こすことが一番大切です。`
- Retrieval: `OK / route_count=7 / candidate_count=9 / rerank_topk=5`
- Prompt context tier: `six_route`
- VTS: `APPLIED`
- TTS: `ACCEPTED`

## Passes

- Formal LAN `host -> remote edge` topology is healthy.
- `host_8b` local route is active.
- Stream/commit narrative output is clean.
- `/obs/trace` chain closure is restored.

## Remaining Blockers

- Retrieval stability is not yet consistent across samples.
- Query grounding still needs tightening. Sample 2 is fluent but too generic for a priority request.
- White-box desktop-side confirmation for audible TTS and visible VTS lip sync still requires operator observation on the edge node desktop.
