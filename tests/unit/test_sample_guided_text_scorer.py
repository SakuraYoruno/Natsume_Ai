from __future__ import annotations

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.sample_guided_text_scorer import SCORING_DIMENSIONS, score_sample_guided_text
from host_brain.persona.text_quality_gate import get_text_quality_gate


def test_sample_guided_scorer_exposes_expected_dimensions() -> None:
    contract = build_galgame_text_contract(user_message="你好", scene_id="daily_greeting_cn")

    scored = score_sample_guided_text(text="尼尼，别这么客气。", contract=contract)

    assert set(SCORING_DIMENSIONS).issubset(scored.dimensions)
    assert scored.sample_scene_id == "daily_greeting_cn"
    assert scored.score >= 80
    assert not scored.hard_fail


def test_context_related_words_are_soft_distance_penalties_not_hard_failures() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    scored = score_sample_guided_text(text="我在这，尼尼找我就回。", contract=contract)

    assert not scored.hard_fail
    assert "DISTANCE_CONTROL_SOFT_PENALTY" in scored.reason_codes
    assert scored.dimensions["distance_control"] < 86


def test_hard_gate_keeps_absolute_output_errors() -> None:
    contract = build_galgame_text_contract(user_message="你好", scene_id="daily_greeting_cn")
    quality = get_text_quality_gate().validate(text='{"text_zh":"你好"}', render_profile="zh_text")

    scored = score_sample_guided_text(text='{"text_zh":"你好"}', contract=contract, quality=quality)

    assert scored.hard_fail
    assert "JSON_OR_STRUCTURED_OUTPUT" in scored.hard_gate_codes
    assert scored.score <= 35


def test_technical_scene_weights_helpfulness_above_role_flavor() -> None:
    contract = build_galgame_text_contract(user_message="这个报错看不懂，帮我看看", scene_id="")

    useful = score_sample_guided_text(text="红字贴过来，我先看最后两行。", contract=contract)
    flavored_but_vague = score_sample_guided_text(text="哼，尼尼这时候倒是想起我了。", contract=contract)

    assert useful.score > flavored_but_vague.score
    assert useful.weights["technical_helpfulness"] > useful.weights["role_fit"]
