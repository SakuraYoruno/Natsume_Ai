from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from edge_client.actuators.style_bert_vits2_worker import load_release_manifest
from tools.style_bert_vits2_edge_release import MANIFEST_NAME, build_release


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    model = tmp_path / "source" / "nanami_e10.safetensors"
    config = tmp_path / "source" / "config.json"
    styles = tmp_path / "source" / "style_vectors.npy"
    plan = tmp_path / "style_plan.json"
    model.parent.mkdir()
    model.write_bytes(b"checkpoint")
    config.write_text(
        json.dumps(
            {
                "data": {
                    "style2id": {
                        "Neutral": 0,
                        "Natural": 1,
                        "WarmCare": 2,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    np.save(styles, np.zeros((3, 256), dtype=np.float32))
    plan.write_text(
        json.dumps(
            {
                "language": "JP",
                "runtime_style_map": {
                    "STEADY_COMPANION": {"style": "Natural", "weight": 0.45},
                    "SOFT_COMFORT": {"style": "WarmCare", "weight": 0.78},
                },
            }
        ),
        encoding="utf-8",
    )
    return model, config, styles, plan


def test_release_is_self_contained_and_worker_reopens_it(tmp_path: Path) -> None:
    model, config, styles, plan = _inputs(tmp_path)
    output = tmp_path / "release"

    manifest = build_release(
        model_file=model,
        config_file=config,
        style_vectors_file=styles,
        style_plan_file=plan,
        output_dir=output,
        model_id="nanami-ja-e10",
    )
    reopened = load_release_manifest(model_path=str(output))

    assert manifest["promotion_status"] == "CANDIDATE_NOT_HUMAN_ACCEPTED"
    assert set(manifest["materialization"]) == {"model", "config", "styles"}
    assert reopened["model_id"] == "nanami-ja-e10"
    assert reopened["_assets"]["model"] == output / "nanami_e10.safetensors"


def test_release_requires_explicit_safetensors_checkpoint(tmp_path: Path) -> None:
    model, config, styles, plan = _inputs(tmp_path)
    invalid = model.with_suffix(".pth")
    invalid.write_bytes(model.read_bytes())

    with pytest.raises(ValueError, match="explicit .safetensors"):
        build_release(
            model_file=invalid,
            config_file=config,
            style_vectors_file=styles,
            style_plan_file=plan,
            output_dir=tmp_path / "release",
            model_id="nanami-ja",
        )


def test_release_refuses_style_missing_from_model_config(tmp_path: Path) -> None:
    model, config, styles, plan = _inputs(tmp_path)
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["runtime_style_map"]["SHARED_JOY"] = {
        "style": "MissingStyle",
        "weight": 0.8,
    }
    plan.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid style binding"):
        build_release(
            model_file=model,
            config_file=config,
            style_vectors_file=styles,
            style_plan_file=plan,
            output_dir=tmp_path / "release",
            model_id="nanami-ja",
        )


def test_overwrite_only_replaces_an_owned_release(tmp_path: Path) -> None:
    model, config, styles, plan = _inputs(tmp_path)
    output = tmp_path / "release"
    output.mkdir()
    (output / "unrelated.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        build_release(
            model_file=model,
            config_file=config,
            style_vectors_file=styles,
            style_plan_file=plan,
            output_dir=output,
            model_id="nanami-ja",
            overwrite=True,
        )

    assert not (output / MANIFEST_NAME).exists()
    assert (output / "unrelated.txt").read_text(encoding="utf-8") == "keep"
