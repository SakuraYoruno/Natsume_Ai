# ENV Single-Source Migration Trace (2026-03-04)

## Decision

Adopt repository root `.env` as the single runtime configuration source.

- Primary runtime config: `.env`
- Fallback and template: `.env.example`
- Secret resolution remains via `configs/secrets/*` using `*_REF` keys

## Changes

### Added / Restored

- `.env.example` (tracked, non-secret defaults, fallback template)

### Kept

- `.env` (local runtime config, git-ignored)
- `configs/secrets/key_profiles.example.json`
- `configs/secrets/key_profiles.runtime.json`
- `configs/secrets/secret_store.runtime.example.json`
- `configs/secrets/secret_store.runtime.local.json`

### Removed (legacy non-secret config files)

- `configs/runtime/local_services.env`
- `configs/runtime/external_services.json`
- `configs/deploy/node_inventory_9.json`
- `configs/models/embedding_routes.example.json`
- `configs/models/recall_routes.example.json`
- `configs/models/reranker_routes.example.json`
- `configs/model_plane/model_profiles.yaml`
- `configs/model_plane/node_registry.yaml`

### Runtime alignment

The following now use `.env` first, then `.env.example` fallback:

- `scripts/runtime/start_host_side.ps1`
- `scripts/runtime/start_docker_services.ps1`
- `scripts/runtime/start_edge.ps1`
- `scripts/runtime/stop_host_side.ps1`
- `scripts/runtime/check_external_env.py`
- `scripts/runtime/generate_env_readiness_report.py`

## Verification

1. Readiness scripts can run without `.env` by falling back to `.env.example`.
2. Startup scripts report `env_file` as `.env` when present.
3. `.env.example` contains no real secret values.

## Documentation updates

- `README.md`
- `docs/50_project/ExternalServices_Config.md`
- `docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md`
- `docs/00_index/00_DOCMAP.md`
