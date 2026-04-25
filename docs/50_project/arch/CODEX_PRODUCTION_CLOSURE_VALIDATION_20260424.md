# Codex Production Closure Validation, 2026-04-24

Status: ADOPTED EXECUTION REPORT. Codex Runtime v2 only.

## Stable Gate Baseline

Clean worktree source:

```text
origin/main @ 774a9d607e3c593e6843b6e85e95a0476d0d9fc8
worktree: D:\Natsume_Ai_production_closure
```

Stable command:

```text
python -m pytest tests/unit tests/contract tests/integration -q -m "not e2e and not hw and not prod_env and not runtime_probe and not docker and not ci_unstable"
```

Result:

```text
4645 passed, 2 skipped, 169 deselected, 19 subtests passed
```

## Full-Suite Classification

Full command:

```text
python -m pytest -q --junitxml=pytest-full-baseline.xml
```

Result:

```text
4841 passed, 2 skipped, 19 failed, 1 error, 19 subtests passed
```

Failure class:

```text
prod_env / service dependency
```

Affected areas:

- `tests/unit/test_bond_phase5.py`: requires reachable Chroma and is already marked `prod_env` plus `ci_unstable` by collection policy.
- `tests/integration/test_memory_hub_prod_env.py`: requires reachable Chroma.
- `tests/integration/test_neo4j_prod_env.py`: requires reachable Neo4j and configured auth.
- `tests/integration/test_bond_queue_summarizer_prod_env.py`: requires reachable Neo4j.

No default stable-gate regression was observed.

## CI Routing

- `delivery-gates.yml`: default PR and `main` push gate; excludes external services, hardware, runtime probes, Docker, and `ci_unstable`.
- `delivery-gates.yml` full-suite job: manual/nightly report-only run for broad visibility.
- `service-integration.yml`: Redis, Chroma, and Neo4j dependency plane with CI-generated `.env` and local secret store.
- `live-probes.yml`: self-hosted Windows live runner plane for hardware, Edge, TTS/ASR, gateway, runtime probes, and e2e.

## BioOps Closure

- Owner adoption is now an explicit candidate queue; BioOps does not directly write long-term memory, Bond, Canon, personality core, secrets, `.env`, or production configuration.
- Habit, scar, forgetting, autobiography, and immune memory capabilities stage owner-reviewed candidates only.
- Truthfulness, memory ethics, persona antibody, dream, parliament, subconscious, organ scan, and related advanced mechanisms remain audit/report/proposal only.
- Reflex, attention, active silence, weather, and breathing policies are runtime traces unless an existing owner path consumes them.

## Observable Owner Review

- The Monitor WebUI reads BioOps adoption candidates through OBS routes, not HostMain request handlers.
- Owner accept/reject actions write only `adoption_decision.json` artifacts beside the candidate evidence.
- Decision artifacts carry owner, source run, source candidate path, risk flags, and `direct_long_term_write = false`; each owner organ must still perform any durable state import through its own state-owner path.
- Accepted decisions can be scanned into an owner inbox. The inbox performs owner/evidence/sensitive-target validation and materializes only `owner_import_candidate.json`, never durable memory or configuration writes.
- Owner inbox artifacts can be grouped into per-organ `owner_intake_packet.json` handoff files, so each organ consumes only its own queue and durable writes remain organ-owned.
- Owner intake packets include an explicit owner/channel contract, and unsupported owner/output-kind combinations are blocked before any handoff packet is created.
- Owner intake packets can now be consumed into per-organ `owner_queues/<owner>/<channel>/<candidate>/queue_item.json` artifacts. These queue items remain candidate-only and record `durable_write_performed = false`; actual long-term state import remains the owner organ's job.
- Local observable demo: `python scripts/runtime/generate_bio_ops_owner_review_demo.py --run-id bio_ops_owner_review_demo` creates a complete artifact run for the Monitor WebUI without touching durable state.
