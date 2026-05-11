from __future__ import annotations

import json
from pathlib import Path

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.galgame_text_scorer import score_galgame_candidate
from tools.chat_ab_gold_score import score_candidate


FIXTURE = Path("tests/fixtures/chat_ab/galgame_taste_regression_cases.jsonl")


def _load_rows() -> list[dict[str, str]]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_galgame_taste_regression_fixture_bad_lines_score_below_good_lines() -> None:
    rows = _load_rows()

    for row in rows:
        contract = build_galgame_text_contract(
            user_message=row["user_message"],
            scene_id=row.get("scene_id", ""),
        )
        bad = score_galgame_candidate(text=row["bad_text"], contract=contract)
        good = score_galgame_candidate(text=row["good_text"], contract=contract)

        assert row["expected_reason"] in bad.reason_codes, row["case_id"]
        assert row["expected_token"] in " ".join(bad.penalties), row["case_id"]
        assert bad.score < good.score, row["case_id"]
        assert not any(code.startswith("TASTE_") for code in good.reason_codes), row["case_id"]


def test_gold_score_flags_taste_regression_from_shared_guard() -> None:
    row = _load_rows()[0]

    scored = score_candidate(
        case={"case_id": row["case_id"], "user_message": row["user_message"]},
        gold={},
        candidate={"case_id": row["case_id"], "variant": "kernel_v2_plain_soul", "text": row["bad_text"]},
    )

    assert "taste_regression" not in scored["hard_failures"]
    assert any("怪味口吻软扣分" in item for item in scored["penalties"])
    assert scored["score"] < 100
