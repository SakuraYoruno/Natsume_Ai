# GPT-SoVITS Host Train -> Edge Deploy

## Boundary

- Host node: owns dataset prep, training, validation, model inventory, release packaging.
- Edge node: owns real-time inference, audio playback, VTS mouth/expression linkage.
- Edge must not train. Edge should only receive a packaged overlay that contains weights, reference clips, and `natsume_gpt_sovits_model_manifest.json`.

## Language Rule

The current `D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50\logs\Natsume_Ai` project is Japanese-dominant. It is valid as a Japanese voice model, not as a Chinese TTS model.

For Chinese speech, train a separate Chinese project on the Host node, for example `Natsume_Ai_zh`, then package it with `--voice-language zh`.

## Host Commands

Inspect an authorized wav+lab source before importing:

```powershell
python tools\gpt_sovits_dataset_prep.py inspect-lab `
  --source-root C:\Users\34145\Downloads\昔涟
```

Prepare a Chinese wav+lab source as a GPT-SoVITS training project. Prefer
`--authorization-file` when the source package includes explicit terms; use
`--user-asserted-authorization` only to record a human-provided assertion that
has not been machine verified.

```powershell
python tools\gpt_sovits_dataset_prep.py prepare-lab `
  --source-root C:\Users\34145\Downloads\昔涟 `
  --gpt-sovits-root D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50 `
  --project Natsume_Ai_Xilian_zh `
  --speaker xilian `
  --language zh `
  --source-name 昔涟 `
  --user-asserted-authorization "User provided this as an authorized public voice source for local voice model training."
```

The importer writes:

- `datasets\<project>\wavs\*.wav`
- `datasets\<project>\labels\*.lab`
- `logs\<project>\0-source.list`
- `logs\<project>\dataset_manifest.json`
- `logs\<project>\source_authorization.json`

Build an emotion-tagged corpus view before retraining expressive Chinese speech:

```powershell
python tools\gpt_sovits_emotion_corpus.py build `
  --gpt-sovits-root D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50 `
  --project Natsume_Ai_Xilian_zh_clean_lively `
  --source-name 昔涟 `
  --top-reference-count 12
```

This writes deterministic review artifacts under
`logs\<project>\emotion_corpus\`:

- `emotion_summary.json`: corpus balance, skipped reasons, candidate counts.
- `emotion_manifest.json`: every prepared/skipped clip with inferred emotion,
  confidence, expressive score, and training role.
- `reference_candidates.json`: top reference candidates for runtime emotion
  reference selection.
- `recovery_candidates.json`: expressive skipped clips worth manual splitting or
  text cleanup before retraining.
- `source_lists\<emotion>.list`: native GPT-SoVITS `wav|speaker|language|text`
  lists grouped by emotion for sampling and manual review.

Create a derived emotion-balanced training project after reviewing the emotion
corpus:

```powershell
python tools\gpt_sovits_emotion_corpus.py balance `
  --gpt-sovits-root D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50 `
  --source-project Natsume_Ai_Xilian_zh_clean_lively `
  --output-project Natsume_Ai_Xilian_zh_emotion_balanced `
  --target-emotion-count 120 `
  --target-neutral-ratio 0.30 `
  --overwrite
```

The balanced project writes a new `logs\<output-project>\0-source.list` that
references the prepared wav files from the source project. It intentionally caps
neutral clips so expressive clips dominate retraining. Review
`logs\<output-project>\emotion_balance\emotion_balance_report.json` before
starting a long run; it includes selected counts, missing emotion coverage, top
selected clips, and the manual recovery queue.

Inspect a training project:

```powershell
python tools\gpt_sovits_edge_release.py inspect `
  --source-root D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50 `
  --project Natsume_Ai
```

Package a Chinese release after a Chinese project has been trained:

```powershell
python tools\gpt_sovits_edge_release.py package `
  --source-root D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50 `
  --project Natsume_Ai_zh `
  --voice-language zh `
  --model-id natsume-ai-zh-v2proplus `
  --output-dir artifacts\model_releases
```

Deploy the release overlay to Edge:

```powershell
pwsh scripts\runtime\deploy_gpt_sovits_edge_release.ps1 `
  -PackagePath artifacts\model_releases\<release>.zip `
  -EdgeSshHost 192.168.1.101 `
  -EdgeModelRoot D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50
```

Edge env must point to the GPT-SoVITS root after extraction:

```dotenv
EDGE_TTS_MODEL_PATH=D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50
EDGE_TTS_LANGUAGE=zh
```

The Edge loader reads `natsume_gpt_sovits_model_manifest.json`. If `voice_language` does not match `EDGE_TTS_LANGUAGE`, it refuses to load unless `EDGE_TTS_ALLOW_LANGUAGE_MISMATCH=true` is explicitly set for manual recovery.
