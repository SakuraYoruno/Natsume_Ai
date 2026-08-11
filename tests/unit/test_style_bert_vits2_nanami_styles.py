from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tools.style_bert_vits2_nanami_styles import (
    build_style_bank,
    mine_review_candidates,
)


def _dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    dataset = tmp_path / "Data" / "Nanami"
    natural = dataset / "wavs" / "Natural"
    warm = dataset / "wavs" / "WarmCare"
    joy = dataset / "wavs" / "SharedJoy"
    for directory in (natural, warm, joy):
        directory.mkdir(parents=True, exist_ok=True)
    vectors = {
        natural / "n1.wav.npy": [0.8, 0.2, 0.0],
        natural / "n2.wav.npy": [0.1, 0.9, 0.0],
        natural / "warm_candidate.wav.npy": [0.98, 0.02, 0.0],
        natural / "joy_candidate.wav.npy": [0.02, 0.98, 0.0],
        warm / "warm_anchor.wav.npy": [1.0, 0.0, 0.0],
        joy / "joy_anchor.wav.npy": [0.0, 1.0, 0.0],
    }
    lines: list[str] = []
    for path, vector in vectors.items():
        np.save(path, np.asarray(vector, dtype=np.float32))
        wav = path.with_suffix("")
        wav.write_bytes(b"RIFF")
        relative = wav.relative_to(dataset / "wavs").as_posix()
        lines.append(f"{relative}|Nanami|JP|text-{wav.stem}")
    (dataset / "esd.list").write_text("\n".join(lines) + "\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "style_anchors": {
                    "WarmCare": ["warm_anchor"],
                    "SharedJoy": ["joy_anchor"],
                }
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"data": {}}), encoding="utf-8")
    return dataset, plan, config


def test_candidate_miner_uses_anchor_margin_instead_of_auto_promoting(tmp_path: Path) -> None:
    dataset, plan, _ = _dataset(tmp_path)

    receipt = mine_review_candidates(
        dataset_dir=dataset,
        style_plan_file=plan,
        per_style=2,
    )

    assert receipt["status"] == "CANDIDATE_REVIEW_REQUIRED"
    assert receipt["candidates"]["WarmCare"][0]["clip_id"] == "warm_candidate"
    assert receipt["candidates"]["SharedJoy"][0]["clip_id"] == "joy_candidate"


def test_style_bank_can_change_reviewed_anchors_without_retraining(tmp_path: Path) -> None:
    dataset, plan, config = _dataset(tmp_path)
    output_config = tmp_path / "release" / "config.json"
    output_styles = tmp_path / "release" / "style_vectors.npy"

    receipt = build_style_bank(
        dataset_dir=dataset,
        style_plan_file=plan,
        source_config_file=config,
        output_config_file=output_config,
        output_vectors_file=output_styles,
    )

    generated_config = json.loads(output_config.read_text(encoding="utf-8"))
    generated_vectors = np.load(output_styles)
    assert receipt["status"] == "STYLE_BANK_GENERATED_NOT_HUMAN_ACCEPTED"
    assert generated_config["data"]["style2id"] == {
        "Neutral": 0,
        "Natural": 1,
        "WarmCare": 2,
        "SharedJoy": 3,
    }
    assert generated_vectors.shape == (4, 3)
    assert generated_vectors[2].tolist() == [1.0, 0.0, 0.0]
    assert generated_vectors[3].tolist() == [0.0, 1.0, 0.0]
