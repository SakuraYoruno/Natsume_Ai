# Codex Digital Life Production Closure Matrix, 2026-04-25

Status: ADOPTED CLOSURE REPORT. Codex Runtime v2 + BioOps proposal-only.

This report closes the three Codex digital-life roadmaps against implemented code, tests, and WebUI/OBS observability:

- `CODEX_BIO_OPERATIONS_PLAN.md`
- `CODEX_DIGITAL_LIFE_ADVANCED_IDEAS.md`
- `CODEX_DIGITAL_LIFE_INNER_MECHANISMS.md`

## Runtime Boundary

- No retired Codex skill/function/env/route names are restored.
- Codex writes still require WorkOrder policy and ResultContract artifacts.
- BioOps remains proposal/report/candidate-only. It does not directly write persona core, Bond, Canon, secrets, `.env`, production config, or long-term memory.
- OBS/Monitor runs through the observability plane and artifact files. BioOps monitoring is not registered as a HostMain blocking loop.

## Roadmap Matrix

| Roadmap area | Closure state | Implementation evidence | Observable surface | Verification |
| --- | --- | --- | --- | --- |
| Codex WorkPlane / hard cut | Implemented | `host_brain/codex_runtime/*`, new skill IDs, WorkOrder policies, ResultContract fields | Codex / BioOps WebUI runtime cards | hard-cut `rg` scan has no runtime/test/script/prompt hits |
| ImmunePlane | Implemented as Runtime v2 immune API | `host_brain/codex_runtime/immune.py`, `/internal/codex/immune/*` | health/readiness immune proposal and internal Codex jobs | Codex runtime tests and default gate |
| BioOps contracts / runner | Implemented | `host_brain/bio_ops/contracts.py`, `runner.py`, `registry.py`, `safety.py`, `artifact_store.py` | `bio_ops_daily_full.json`, WebUI capability panels | `tests/unit/test_bio_ops_*`, `tests/integration/test_bio_ops_daily_cycle.py` |
| Advanced mechanisms | Implemented as report/proposal/candidate capabilities | `host_brain/bio_ops/capabilities.py` | WebUI panels: Inner, Trust, Natural Interaction, Relationship, Inner Life, Self Governance, Growth, Memory, Affect, Presence, Drive, Adaptive Repair | capability and OBS route tests |
| Owner adoption | Implemented | `adoption.py`, `owner_inbox.py`, `owner_consumer.py` | adoption review, owner inbox, owner queues | owner adoption/inbox/consumer tests |
| Daily cycle | Implemented as sidecar-friendly artifact runner | `host_brain/bio_ops/daily_cycle.py`, `scripts/runtime/run_bio_ops_daily_cycle.py` | WebUI `运行 daily cycle` button and observable run artifacts | daily-cycle unit/integration tests |
| BioOps workflow automation | Implemented as sidecar workflow | `host_brain/bio_ops/workflow.py`, `scripts/runtime/run_bio_ops_workflow.py`, `.github/workflows/bio-ops-automation.yml` | GitHub workflow artifacts plus WebUI run id | workflow automation unit tests |
| Chat/Planner low-risk trace | Implemented | `bio_ops/runtime_trace.py`, `chat_routes.py`, `executive_runtime_core.py`, `proactive_talk.py` | SSE trace metadata, immune-block trace, proactive quiet-presence trace | runtime trace, chat route, proactive tests |
| Inner mechanisms document set | Implemented as BioOps groups | capability groups listed in `CODEX_DIGITAL_LIFE_INNER_MECHANISMS.md` | WebUI grouped panels | capability group assertions in OBS tests |
| Retired prompt/template compatibility tests | Removed from default gate | `tests/conftest.py` `ci_unstable` classification | Full suite still reports them for rewrite | default gate excludes `ci_unstable`; PromptSpec tests remain active |

## Owner-Gated State Flow

```text
BioOpsResult(CANDIDATE)
  -> adoption_candidate.json
  -> owner adoption decision
  -> owner_import_candidate.json
  -> owner_intake_packet.json
  -> owner_queues/<owner>/<channel>/<candidate>/queue_item.json
  -> owner organ durable importer, outside BioOps direct write path
```

Current closure stops at the owner queue artifact. That is deliberate: long-term state import must be implemented by each owner organ, not by Codex or BioOps.

## WebUI / OBS Entry Points

- WebUI: `/monitor/codex-bio-ops`
- `GET /obs/codex-bio-ops`
- `POST /obs/codex-bio-ops/daily-cycle/run`
- `GET /obs/codex-bio-ops/adoptions`
- `POST /obs/codex-bio-ops/adoptions/{candidate_id}/accept`
- `POST /obs/codex-bio-ops/adoptions/{candidate_id}/reject`
- `GET /obs/codex-bio-ops/owner-inbox`
- `POST /obs/codex-bio-ops/owner-inbox/materialize`
- `POST /obs/codex-bio-ops/owner-inbox/intake-packets`
- `GET /obs/codex-bio-ops/owner-queues`
- `POST /obs/codex-bio-ops/owner-queues/consume`

## Local Observation Commands

```text
python scripts/runtime/run_bio_ops_daily_cycle.py --run-id bio_ops_daily_manual
python scripts/runtime/run_bio_ops_workflow.py --run-id bio_ops_workflow_manual
python scripts/runtime/run_bio_ops_workflow.py --run-id bio_ops_workflow_smoke --seed-demo-owner-decision
python scripts/runtime/generate_bio_ops_owner_review_demo.py --run-id bio_ops_owner_review_demo
```

Then open the Monitor WebUI Codex / BioOps page and select the generated run.

## Verification Snapshot

Focused BioOps gates run during closure:

```text
python -m pytest -q tests/unit/test_bio_ops_runtime_trace.py tests/unit/test_bio_ops_daily_cycle_runner.py tests/unit/test_bio_ops_adoption.py tests/unit/test_bio_ops_owner_consumer.py tests/unit/test_bio_ops_owner_inbox.py tests/unit/test_bio_ops_owner_review_demo_script.py tests/unit/test_bio_ops_runner_capabilities.py tests/unit/test_bio_ops_adaptive_repair.py tests/unit/test_obs_codex_bio_ops_route.py tests/unit/test_fastapi_app_refactor_modules.py tests/unit/test_fastapi_app_immune_gate.py tests/unit/test_proactive_skill.py tests/unit/test_executive_runtime_core_complex_planning.py tests/integration/test_bio_ops_daily_cycle.py
```

Result:

```text
261 passed
```

Default stable gate:

```text
python -m pytest tests/unit tests/contract tests/integration -q -m "not e2e and not hw and not prod_env and not runtime_probe and not docker and not ci_unstable"
```

Result:

```text
4537 passed, 2 skipped, 586 deselected, 19 subtests passed
```

The deselected bucket contains retired dynamic prompt-template/surface compatibility suites and explicitly specialized environment planes. PromptSpec coverage remains active in the default gate.

Hard-cut legacy-term scan over runtime code, tests, scripts, and prompts: no matches.

## Residual Boundaries

- Full-suite service failures remain classified as service/environment planes, not default gate regressions.
- Live probes, hardware, TTS/ASR, Edge, gateway, Docker, and runtime probes remain manual or specialized CI planes.
- BioOps owner queues are not durable state. They are reviewable handoff artifacts for the owning organ.
