# Surface Scene Quality Samples

`surface_scene_quality_pack` is the scene-level quality anchor for the current
minimal surface chain. It is intentionally not a forbidden-pattern list. The
runtime pack lives in `data/style_bank/surface_scene_quality_pack.json`; the
Python module is only a loader plus fallback.
For experiments, set `HOST_SURFACE_SCENE_QUALITY_PACK_PATH` to point at a
candidate pack without replacing the default file.

## Sources

Samples come from four layers:

- `manual_gold`: hand-written target lines for common scenes.
- `live_ab_promoted`: lines selected by live AB runs that passed hard gate and
  scored well.
- `live_ab_bad`: visible bad lines from live AB candidates, used as soft
  contrast samples.
- future quote mining: real Chinese dialogue can seed rhythm and turn-taking,
  but raw lines should stay in the quote/style bank; the scene pack stores
  derived, project-owned examples only.

## Flow

1. Run `tools/surface_memory_ab.py` with `--case-set quality_pack`.
2. Harvest review candidates:

   ```powershell
   python tools\surface_scene_quality_samples.py `
     --details artifacts\surface_memory_ab\live_quality_pack_latest\details.json `
     --out artifacts\surface_scene_quality\harvested_samples.json `
     --review-template-out artifacts\surface_scene_quality\review_template.json
   ```

3. Review `harvested.good_examples` and `harvested.bad_examples` by `scene_id`.
4. Promote only stable, reusable samples into
   a reviewed sample file, then merge that file into a candidate pack.

The harvester deliberately writes a review artifact instead of editing the pack.
That keeps sample promotion explicit and avoids one-case-one-patch drift.

By default, good candidates require `score >= 92` and bad candidates require a
hard failure, a known bad reason code, or `score <= 70`. Low-but-passable lines
should be reviewed by a human instead of being automatically treated as bad
style.

## Reviewed Promotion

Reviewed files use this shape:

```json
{
  "schema_version": 1,
  "sample_kind": "surface_scene_quality_reviewed",
  "scenes": [
    {
      "scene_id": "short_continue",
      "good_examples": ["哼，接着说。"],
      "bad_examples": ["好，接着往 surface 主链走。"],
      "should_not_surface": ["surface"],
      "source_note": "review batch 2026-05-15"
    }
  ]
}
```

The generated review template has `candidate_good_examples` and
`candidate_bad_examples` for reference. These fields are ignored by merge.
Only text manually copied into `good_examples`, `bad_examples`, or
`should_not_surface` is promoted.

Validate reviewed files before merging:

```powershell
python tools\surface_scene_quality_samples.py `
  --reviewed artifacts\surface_scene_quality\reviewed_samples.json `
  --validate-reviewed
```

Merge reviewed samples into a candidate pack:

```powershell
python tools\surface_scene_quality_samples.py `
  --reviewed artifacts\surface_scene_quality\reviewed_samples.json `
  --merge-out artifacts\surface_scene_quality\surface_scene_quality_pack.candidate.json
```

After reviewing the candidate pack, replace
`data/style_bank/surface_scene_quality_pack.json` and rerun the quality pack AB.
