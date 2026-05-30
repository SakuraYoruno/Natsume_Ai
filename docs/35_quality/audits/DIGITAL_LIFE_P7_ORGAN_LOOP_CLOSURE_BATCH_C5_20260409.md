受众：CODEX
标题：DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C5_20260409

# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C5_20260409

## Goal
- Make `immune` trace events first-class in Trace UI.
- Avoid immune nodes being merged into generic `voice/input` buckets.

## Delivered
- Added `immune` node canonicalization in trace text mapping:
  - `host_brain/app/monitor_ui/src/components/trace/traceNodeText.js`
  - `voice_immune_*` now maps to canonical node id `immune_gate`.
  - Added display alias and node-type label for `immune`.
- Added immune visual style for graph nodes:
  - `host_brain/app/monitor_ui/src/components/trace/CustomNode.jsx`
  - `node_type=immune` now has dedicated color/icon style.
- Added immune stage/group in Trace overview:
  - `host_brain/app/monitor_ui/src/pages/Trace.jsx`
  - New group `immune` with canonical node `immune_gate`.
  - Full-chain schema now includes `immune_gate`.
  - Chain edge now: `governor -> immune_gate -> query_planner`.
  - Normalizer now maps `voice_immune_*` and `node_type=immune` to `immune_gate`.
  - Added dedicated color in `NODE_TYPE_COLOR`.
- Added immune stage in trace request detail timeline:
  - `host_brain/app/monitor_ui/src/pages/TraceRequestDetail.jsx`
  - Stage list / stable labels / tribe-event stage resolver now recognize immune events.

## Verification
- Frontend build passed:
  - `npm run build` in `host_brain/app/monitor_ui`
- Backend + observability regression for related immune work:
  - `pytest -q tests/integration/test_obs_immune_gate_stats.py tests/unit/test_scheduler_routes_immune_gate_state.py`
