from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPO_ROOT / "configs" / "voice" / "gpt_sovits_nanami_v4.json"
EXPECTED_STYLES = {
    "STEADY_COMPANION",
    "CALM_ATTENTIVE",
    "SOFT_COMFORT",
    "REPAIR_OPEN",
    "SHARED_JOY",
    "WARM_PLAYFUL",
    "SHY_AFFECTION",
    "FLIRTING",
    "ROMANTIC_TENDER",
    "CONTAINED_HURT",
    "CONTAINED_JEALOUSY",
    "LONGING",
    "GENTLE_BOUNDARY",
    "GIVE_SPACE",
    "QUIET_PRESENCE",
    "QUIET_INTIMACY",
}


def _profile() -> dict[str, object]:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def test_nanami_v4_profile_covers_every_runtime_expression() -> None:
    profile = _profile()
    references = list(profile["references"])
    styles = {str(item["voice_style"]) for item in references}

    assert profile["engine"] == "gpt_sovits_v4"
    assert profile["voice_language"] == "ja"
    assert styles == EXPECTED_STYLES
    assert len(references) == len(EXPECTED_STYLES)
    assert len({str(item["audio_path"]) for item in references}) == 9


def test_nanami_v4_profile_is_portable_and_uses_selected_references() -> None:
    profile = _profile()
    assert profile["weights"] == {
        "t2s": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
        "vits": "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth",
    }

    serialized = json.dumps(profile, ensure_ascii=False)
    assert "nanami_00630.wav" not in serialized
    assert not re.search(r"[A-Za-z]:[\\/]", serialized)

    for reference in profile["references"]:
        audio_path = PurePosixPath(str(reference["audio_path"]))
        assert not audio_path.is_absolute()
        assert str(reference["prompt_lang"]) == "ja"
        assert str(reference["prompt_text"]).strip()


def test_cn_asr_ja_tts_launcher_selects_nanami_v4_without_starting_edge() -> None:
    source = (REPO_ROOT / "scripts" / "runtime" / "start_edge.ps1").read_text(
        encoding="utf-8-sig"
    )
    enforced_block = source.split("if ($EnforceCnAsrJaTtsMode) {", 1)[1].split(
        "} else {", 1
    )[0]

    assert 'EDGE_ASR_LANGUAGE = "zh"' in enforced_block
    assert 'EDGE_TTS_BACKEND = "gpt_sovits"' in enforced_block
    assert 'EDGE_TTS_LANGUAGE = "ja"' in enforced_block
    assert "gpt_sovits_nanami_v4.json" in enforced_block
    assert 'EDGE_TTS_PERSISTENT_WORKER_ENABLED = "true"' in enforced_block
