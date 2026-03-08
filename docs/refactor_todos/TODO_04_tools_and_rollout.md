# TODO 04 - Async Migration (Scripts/Tools) + Rollout

## Goal
- [ ] Remove remaining synchronous operations in non-test codebase, including scripts/tools where practical.
- [ ] Roll out safely with observability and fallback controls.

## Scripts/Tools Migration
- [ ] `tools/start_neo4j.py`, `tools/start_chroma*.py`: replace polling sleep + urllib with async pollers.
- [ ] Install/download scripts: replace `urllib.request` with async download client.
- [ ] Release/build scripts: replace sync subprocess chains with async subprocess orchestration.
- [ ] For one-shot scripts where async adds little value, isolate sync calls behind explicit `run_in_thread` wrappers and mark as approved exception.

## Rollout Strategy
- [ ] Phase 1: ship split refactor only (no behavior change).
- [ ] Phase 2: async adapters behind feature flags.
- [ ] Phase 3: enable async-by-default; keep fallback toggle for one release cycle.

## Verification
- [ ] Add refactor smoke suite (non-test scanners + startup + critical API streaming checks).
- [ ] Add load/latency test gates in CI/CD.
- [ ] Add runtime dashboards for queue depth, stream chunk interval, DB wait time, retry counts.

## Done Criteria
- [ ] All migration checklists completed.
- [ ] No regression on startup or core chat workflow.
- [ ] Sync API scanner passes on non-test code.
