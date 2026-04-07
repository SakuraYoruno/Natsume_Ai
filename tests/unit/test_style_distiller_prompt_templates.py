from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.retrieval import style_distiller as sd


def _line(
    cn_text: str = "",
    jp_text: str = "",
    *,
    emotion_tag: str = "daily",
    line_index: int = 0,
) -> sd.UtteranceLine:
    return sd.UtteranceLine(
        file_rel_path="story/ch01.ast",
        block_id="b1",
        line_index=line_index,
        cn_text=cn_text,
        jp_text=jp_text,
        emotion_tag=emotion_tag,
    )


def test_style_distiller_cn_templates_override_analysis_and_render(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "style_distiller.render_instruction.templates",
        content=json.dumps(
            {
                "sentence_patterns": {
                    "short_reply": "custom-short",
                },
                "emotion_strategies": {
                    "daily": "custom-daily",
                },
                "emotion_scene_pattern_hints": {
                    "question": "custom-question",
                },
                "rhythm_descriptions": {
                    "default": "custom-rhythm-default",
                },
                "render_prompt_cn": {
                    "core_title": "【自定义核心】",
                    "core_lines": {
                        "gentle": "- custom-gentle",
                    },
                    "forbidden_lines": [
                        "- custom-no-ai",
                    ],
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    sentence_patterns = sd.analyze_sentence_patterns(
        [
            _line("嗯", line_index=1),
            _line("好呀", line_index=2),
            _line("收到", line_index=3),
        ]
    )
    assert sentence_patterns[0].pattern == "custom-short"

    emotion_patterns = sd.analyze_emotion_patterns(
        [
            _line("要继续吗？", emotion_tag="daily", line_index=1),
            _line("你还想聊吗？", emotion_tag="daily", line_index=2),
        ]
    )
    daily = next(pattern for pattern in emotion_patterns if pattern.scene == "daily")
    assert daily.strategy == "custom-daily"
    assert "custom-question" in daily.sentence_patterns

    rhythm = sd.analyze_rhythm([_line("好", line_index=1)])
    assert rhythm["rhythm_description"] == "custom-rhythm-default"

    profile = sd.StyleProfile(
        total_cn=2,
        total_ja=1,
        sentence_patterns=sentence_patterns,
        emotion_patterns=emotion_patterns,
        signature_phrases_cn=["陪着你"],
        rhythm_description=rhythm["rhythm_description"],
        short_ratio=0.8,
        medium_ratio=0.2,
        long_ratio=0.0,
        particle_freq_cn={"呢": 0.5},
        question_ratio=1.0,
        ellipsis_ratio=0.0,
        exclaim_ratio=0.0,
    )
    rendered = sd.render_style_instruction_v5(profile)

    assert "【自定义核心】" in rendered
    assert "- custom-gentle" in rendered
    assert "- custom-no-ai" in rendered


def test_style_distiller_ja_templates_override_render(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "style_distiller.render_instruction.templates",
        content=json.dumps(
            {
                "render_prompt_ja": {
                    "sentence_pattern_map": {
                        "短句应答": "短い返事カスタム",
                    },
                    "emotion_strategy_map": {
                        "daily": "日常カスタム",
                    },
                    "emotion_scene_pattern_hints_ja_map": {
                        "多用疑问句（关心/确认）": "質問カスタム",
                    },
                    "rhythm_description_map": {
                        "自然交替，短句（≤10字）和中句（11-30字）为主": "リズムカスタム",
                    },
                    "forbidden_lines": [
                        "- 禁止カスタム",
                    ],
                }
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    monkeypatch.setattr(
        sd,
        "_ja_lines_cache",
        [
            _line("", "まだ話す？", line_index=1),
            _line("", "うん", line_index=2),
        ],
        raising=False,
    )

    profile = sd.StyleProfile(
        total_cn=1,
        total_ja=2,
        sentence_patterns=[
            sd.SentencePattern(
                pattern="短句应答：1-8字的简洁回应，常用于附和、确认、感叹",
                examples_ja=["うん"],
                frequency=0.5,
            )
        ],
        emotion_patterns=[
            sd.EmotionResponsePattern(
                scene="daily",
                strategy="自然随意的日常对话，短句为主，轻松的语气",
                sentence_patterns=["多用疑问句（关心/确认）"],
                examples_ja=["まだ話す？"],
                avg_length=5.0,
            )
        ],
        signature_phrases_ja=["そばにいる"],
        rhythm_description="自然交替，短句（≤10字）和中句（11-30字）为主",
        short_ratio=0.5,
        medium_ratio=0.5,
        long_ratio=0.0,
        particle_freq_ja={"ね": 0.5},
        question_ratio=0.5,
        ellipsis_ratio=0.0,
        exclaim_ratio=0.0,
    )

    rendered = sd.render_style_instruction_v5_ja(profile)

    assert "短い返事カスタム" in rendered
    assert "日常カスタム" in rendered
    assert "質問カスタム" in rendered
    assert "リズムカスタム" in rendered
    assert "- 禁止カスタム" in rendered


def test_builtin_style_distiller_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("style_distiller.render_instruction.templates")

    assert templates["sentence_patterns"]["short_reply"] == "短句应答：1-8字的简洁回应，常用于附和、确认、感叹"
    assert templates["emotion_strategies"]["daily"] == "自然随意的日常对话，短句为主，轻松的语气"
    assert templates["render_prompt_cn"]["core_title"] == "【核心语感】"
    assert templates["render_prompt_cn"]["forbidden_lines"][0] == "- 绝不能说自己是 AI、人工智能、虚拟助手、系统或程序。"
    assert templates["render_prompt_ja"]["forbidden_title"] == "【絶対禁止】"
