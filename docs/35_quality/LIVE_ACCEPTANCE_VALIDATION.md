受众：CODEX
标题：Live Acceptance Validation

# Live Acceptance Validation

This suite is the pre-integration gate for the current digital-life main chain.
It validates real external services plus the live host-side loop; it is not text
quality scoring.

Run it with Docker services online:

```powershell
python tools\validate_live_acceptance.py --run-id live_current_acceptance --out-dir artifacts\live_acceptance --organ-iterations 5 --max-turns 4
```

Covered checks:

- Chroma heartbeat and real memory roundtrip.
- Neo4j HTTP/Bolt probes and real bond graph roundtrip.
- Surface memory + emotion + bond loop in live mode.
- 23-organ compressed surface influence contract.
- Inner-world private monologue, self-model update, and breaker behavior.
- Full digital-life loop: organ context, compressed packet, direct surface reply,
  memory write, recall, bond update, private thought, and self update.
- Background runtime core ticks without enabling heavy background tasks.

Acceptance output is written under `artifacts/live_acceptance/<run_id>/`.
The top-level `summary.json` and `report.md` are the canonical pass/fail
artifacts.

This gate intentionally does not run the retired broad unit suite. Legacy tests
that assert old prompt-template overrides, old rerank scoring, or retired
surface fallback behavior must be migrated or deleted separately.
