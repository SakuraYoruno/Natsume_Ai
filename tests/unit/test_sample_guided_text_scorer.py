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


def test_advice_continue_prefers_small_step_over_vague_support() -> None:
    contract = build_galgame_text_contract(user_message="你觉得我该不该继续试试", scene_id="")

    useful = score_sample_guided_text(text="再试一小步吧，累了就停。", contract=contract)
    vague = score_sample_guided_text(text="那当然继续啊，尼尼想做的事我肯定支持。", contract=contract)

    assert contract.required_any == (("一小步", "一步", "一处", "一点", "小改", "先", "停", "收"),)
    assert useful.score > vague.score
    assert useful.score >= 80
    assert "CONTEXT_MISSING_REQUIRED_MOVE" in vague.reason_codes


def test_template_density_is_soft_penalty_not_hard_failure() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    dense = score_sample_guided_text(text="哼，尼尼当然在啦，真是的嘛。", contract=contract)
    cleaner = score_sample_guided_text(text="在呢，这一句我看见了。", contract=contract)

    assert not dense.hard_fail
    assert "STYLE_TEMPLATE_DENSITY" in dense.reason_codes
    assert dense.dimensions["template_density"] < cleaner.dimensions["template_density"]
    assert cleaner.score > dense.score


def test_implied_fact_risk_penalizes_unprovided_time_or_cause_guess() -> None:
    contract = build_galgame_text_contract(user_message="我回来了", scene_id="back")

    risky = score_sample_guided_text(text="今天回来得挺早嘛。", contract=contract)
    grounded = score_sample_guided_text(text="回来啦，那继续。", contract=contract)

    assert not risky.hard_fail
    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert risky.dimensions["implied_fact_risk"] < grounded.dimensions["implied_fact_risk"]
    assert grounded.score > risky.score


def test_scene_grounding_and_catchiness_reward_specific_line_over_flat_ack() -> None:
    contract = build_galgame_text_contract(user_message="弄砸了", scene_id="small_failure")

    grounded = score_sample_guided_text(text="先别急，我陪你看哪里坏了。", contract=contract)
    flat = score_sample_guided_text(text="好啦，没事。", contract=contract)

    assert grounded.score > flat.score
    assert grounded.dimensions["scene_grounding"] > flat.dimensions["scene_grounding"]
    assert "WEAK_SCENE_GROUNDING" in flat.reason_codes
    assert "FLAT_GENERIC_LINE" in flat.reason_codes


def test_line_rhythm_penalizes_long_unbroken_sentence() -> None:
    contract = build_galgame_text_contract(user_message="你好", scene_id="daily_greeting_cn")

    flat = score_sample_guided_text(text="尼尼今天好正经我都不知道该怎么接你这个招呼了", contract=contract)
    rhythmic = score_sample_guided_text(text="尼尼今天好正经，别装外人啦。", contract=contract)

    assert "LINE_RHYTHM_FLAT" in flat.reason_codes
    assert rhythmic.dimensions["line_rhythm"] > flat.dimensions["line_rhythm"]
    assert rhythmic.score > flat.score


def test_success_fact_takes_priority_over_generic_happy_scene() -> None:
    contract = build_galgame_text_contract(user_message="终于跑通了", scene_id="happy")

    assert contract.scene_id == "good_news"
    scored = score_sample_guided_text(text="跑通啦，这才像话。", contract=contract)

    assert scored.score >= 80
    assert "CONTEXT_MISSING_REQUIRED_MOVE" not in scored.reason_codes
