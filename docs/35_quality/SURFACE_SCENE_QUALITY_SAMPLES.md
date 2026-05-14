# Surface Scene Quality Samples

`surface_scene_quality_pack` is the scene-level quality anchor for the current
minimal surface chain. It is intentionally not a forbidden-pattern list.

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
     --out artifacts\surface_scene_quality\harvested_samples.json
   ```

3. Review `harvested.good_examples` and `harvested.bad_examples` by `scene_id`.
4. Promote only stable, reusable samples into
   `host_brain/persona/surface_scene_quality_pack.py`.

The harvester deliberately writes a review artifact instead of editing the pack.
That keeps sample promotion explicit and avoids one-case-one-patch drift.

By default, good candidates require `score >= 92` and bad candidates require a
hard failure, a known bad reason code, or `score <= 70`. Low-but-passable lines
should be reviewed by a human instead of being automatically treated as bad
style.
