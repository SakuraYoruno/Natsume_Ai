# Canon Vector Projection Trace (2026-03-04)

## Decision

Adopt a hybrid canonical storage strategy:

1. File-based canon remains SSOT.
2. Chroma stores projection/index documents for semantic retrieval.
3. Retrieval uses vector results, but canonical authority comes from SSOT fields (`canon_kind`, `canon_id/doc_id`, `version`, `hash`).

This keeps governance/audit correctness while improving retrieval recall.

## Implementation

### New module

- `host_brain/memory/canon_vector_projection.py`
  - Collects style/world canon files.
  - Builds projection documents and metadata.
  - Generates deterministic vector ids:
    - `canon_style::{canon_id}`
    - `canon_world::{doc_id}`
  - Upserts to Chroma collection (default `canon`).
  - Resolves embedding dimension from `CHROMA_COLLECTION_PROFILES_JSON`.

### New runtime script

- `scripts/runtime/sync_canon_vector_index.py`
  - One-shot sync command for style/world canon projection.
  - Supports `http` and `persistent` Chroma backends.
  - Supports env profile loading (`--env-file`) and endpoint override.
  - Supports `--dry-run`.

### Style import hook

- `scripts/runtime/import_natsume_style_canon.py`
  - After style upsert, can sync canon vector index automatically.
  - New options:
    - `--sync-canon-vector-index/--no-sync-canon-vector-index`
    - `--sync-env-file`
    - `--sync-backend`
    - `--sync-chroma-path`
    - `--sync-chroma-endpoint`
    - `--sync-collection`
    - `--sync-collection-dim`
    - `--sync-world-root`

### Env alignment

- `.env`
  - `CHROMA_COLLECTION_PROFILES_JSON` includes `canon` profile.

## Runtime usage

### One-shot sync (recommended after any canon update)

```powershell
python scripts/runtime/sync_canon_vector_index.py `
  --env-file .env `
  --backend http `
  --collection canon
```

### Dry run

```powershell
python scripts/runtime/sync_canon_vector_index.py --dry-run
```

### Style import with auto projection sync

```powershell
python scripts/runtime/import_natsume_style_canon.py `
  --upsert-style-canon `
  --sync-canon-vector-index
```

## Notes

- Projection docs are retrieval aids only.
- Canon governance remains in file SSOT + audit logs (`canon_proposal_log`, `canon_change_log`).
- If Chroma is unavailable, SSOT remains intact and can still be used for deterministic canonical reads.

## Runtime execution trace

Date: 2026-03-04

### Dry-run

```powershell
python scripts/runtime/sync_canon_vector_index.py `
  --env-file .env `
  --backend http `
  --collection canon `
  --dry-run
```

Result:

- `docs_collected=4`
- `style_docs=1`
- `world_docs=3`
- `issues_count=0`

### Production sync

```powershell
python scripts/runtime/sync_canon_vector_index.py `
  --env-file .env `
  --backend http `
  --collection canon `
  --summary-out docs/60_trace/canon_sync_summary_2026-03-04.json
```

Result:

- `success=true`
- `vector_dim=384`
- `docs_synced=4`
- `collection_count=4`
- summary artifact: `docs/60_trace/canon_sync_summary_2026-03-04.json`

### Retrieval-side verification (direct Chroma read)

Read `canon` collection and observed:

- ids:
  - `canon_style::natsume_ai_cn`
  - `canon_world::wc-001-system-terms`
  - `canon_world::wc-002-architecture-invariants`
  - `canon_world::wc-003-persona-core`
- kinds:
  - `style`
  - `world`
  - `world`
  - `world`

### Compatibility fix during execution

Observed issue:

- Chroma upsert rejected `metadata.tags` list values.

Fix:

- Projection metadata now stores:
  - `tags` as comma-separated string
  - `tags_count` as integer

This keeps metadata filterable while satisfying Chroma scalar metadata constraints.
