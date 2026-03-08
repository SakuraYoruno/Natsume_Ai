# TODO 01 - Split Decoupling (App + Core + Audit)

## Goal
- [ ] Split every large runtime file in this track to <= 600 lines per file.
- [ ] Remove cross-layer coupling from route layer to persistence/provider details.

## File Split Plan
- [ ] `host_brain/app/fastapi_app.py`:
  - [ ] Extract schemas to `host_brain/app/schemas.py` (request/response models).
  - [ ] Extract provider factory/retry logic to `host_brain/app/providers.py`.
  - [ ] Extract middleware/hooks to `host_brain/app/http_hooks.py`.
  - [ ] Extract route groups into `host_brain/app/routes/*.py`.
  - [ ] Keep `fastapi_app.py` as composition root only.
- [ ] `host_brain/audit/sqlite_ledger.py`:
  - [ ] Split into `schema.py`, `writer.py`, `reader.py`, `observability_reader.py`, `wal.py`.
  - [ ] Introduce protocol interface for ledger operations.
- [ ] `host_brain/app/main.py`:
  - [ ] Split startup/report builders into `startup_report.py`, `readiness_report.py`, `monitor_profile.py`.
- [ ] `host_brain/app/config.py`:
  - [ ] Split parsing/coercion helpers into `config_parsers.py`.
  - [ ] Split key resolution and env merge into `config_resolvers.py`.
- [ ] `host_brain/core/services.py` and `host_brain/core/qa.py`:
  - [ ] Separate DTO/contract definitions from orchestration logic.
- [ ] `host_brain/app/canon_routes.py`:
  - [ ] Split CRUD handlers by proposal/document domains.

## Decoupling Tasks
- [ ] Replace direct concrete dependency imports with dependency injection entrypoints.
- [ ] Remove circular imports after splits.
- [ ] Keep API surface backward compatible (same route paths and payload fields).

## Done Criteria
- [ ] All files in this track <= 600 lines.
- [ ] Existing API integration checks pass.
- [ ] No new circular import.
