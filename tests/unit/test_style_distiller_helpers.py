from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _write_jsonl(path: Path, rows: list[object]) -> None:
    payload = []
    for row in rows:
        if isinstance(row, str):
            payload.append(row)
        else:
            payload.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(payload) + "\n", encoding="utf-8-sig")


def test_load_utterance_lines_merges_cn_ja_and_skips_invalid_rows(tmp_path: Path) -> None:
    cn_path = tmp_path / "cn.jsonl"
    ja_path = tmp_path / "ja.jsonl"
    _write_jsonl(
        cn_path,
        [
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 1,
                "cn_text": "早安呀！",
                "speaker_cn": "夏目",
            },
            "{not-json}",
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b2",
                "line_index": 2,
                "cn_text": "我会陪着你。",
            },
        ],
    )
    _write_jsonl(
        ja_path,
        [
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 1,
                "jp_text": "おはようね。",
                "speaker_jp": "ナツメ",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b3",
                "line_index": 3,
                "jp_text": "またね。",
            },
        ],
    )

    lines = sd.load_utterance_lines(cn_path=cn_path, ja_path=ja_path)

    assert len(lines) == 3
    assert lines[0].cn_text == "早安呀！"
    assert lines[0].jp_text == "おはようね。"
    assert lines[0].speaker_cn == "夏目"
    assert lines[-1].jp_text == "またね。"


def test_tagging_and_text_shape_helpers_support_ascii_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sd,
        "EMOTION_SCENE_KEYWORDS_CN",
        {
            "comfort": ["calm"],
            "happy": ["yay", "smile"],
            "daily": [],
        },
    )
    monkeypatch.setattr(
        sd,
        "EMOTION_SCENE_KEYWORDS_JA",
        {
            "sadness": ["kanashii"],
            "daily": [],
        },
    )
    monkeypatch.setattr(sd, "CN_PARTICLES", ("la", "a"))
    monkeypatch.setattr(sd, "JA_PARTICLES", ("yo", "ne"))

    assert sd.tag_emotion("yay smile", lang="cn") == "happy"
    assert sd.tag_emotion("plain text", lang="cn") == "daily"
    lines = sd.tag_all_lines([_line("calm down"), _line("", "kanashii yo")])
    assert [row.emotion_tag for row in lines] == ["comfort", "sadness"]
    assert sd._extract_particle("hello la!", lang="cn") == "la"
    assert sd._extract_particle("mata ne。", lang="ja") == "ne"
    assert sd._extract_particle("   ", lang="cn") == ""
    assert sd._is_question("really?") is True
    assert sd._is_exclaim("wow!") is True
    assert sd._has_ellipsis("wait...") is True


def test_analyze_sentence_and_emotion_patterns_cover_major_branches() -> None:
    lines = [
        _line("好呀", "うん", line_index=1),
        _line("等等...", "ちょっと...", line_index=2),
        _line("你还好吗?", "大丈夫?", line_index=3),
        _line("太好了!", "やった!", line_index=4),
        _line("不过我会陪你。", "でもそばにいる", line_index=5),
        _line("呵呵，今天见到你真开心。", "ふふ、うれしい", line_index=6),
    ]

    patterns = sd.analyze_sentence_patterns(lines)

    assert len(patterns) >= 6
    assert any("?" in "".join(pattern.examples_cn) for pattern in patterns)
    assert any("..." in "".join(pattern.examples_cn) for pattern in patterns)
    assert any("呵呵" in "".join(pattern.examples_cn) for pattern in patterns)

    emotion_patterns = sd.analyze_emotion_patterns(
        [
            _line("你还好吗?", emotion_tag="comfort", line_index=1),
            _line("别怕...", emotion_tag="comfort", line_index=2),
            _line("我在这里。", emotion_tag="comfort", line_index=3),
            _line("太好了!", emotion_tag="happy", line_index=4),
            _line("真的? 太好了!", emotion_tag="happy", line_index=5),
            _line("嗯。", emotion_tag="daily", line_index=6),
        ]
    )

    assert emotion_patterns[0].scene == "comfort"
    comfort = next(pattern for pattern in emotion_patterns if pattern.scene == "comfort")
    assert comfort.examples_cn
    assert comfort.sentence_patterns
    assert comfort.avg_length > 0


def test_distill_profile_and_render_instructions(tmp_path: Path) -> None:
    cn_path = tmp_path / "runtime" / "style_canon" / "natsume_ai_cn_speaker_lines.jsonl"
    ja_path = tmp_path / "runtime" / "style_canon" / "natsume_ai_ja_speaker_lines.jsonl"
    cn_path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        cn_path,
        [
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 1,
                "cn_text": "早安呀！",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 2,
                "cn_text": "早安呀！",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 3,
                "cn_text": "早安呀！",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b2",
                "line_index": 4,
                "cn_text": "不过我会陪你。",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b3",
                "line_index": 5,
                "cn_text": "太好了!",
            },
        ],
    )
    _write_jsonl(
        ja_path,
        [
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 1,
                "jp_text": "おはようね。",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 2,
                "jp_text": "おはようね。",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 3,
                "jp_text": "おはようね。",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b2",
                "line_index": 4,
                "jp_text": "そばにいるよ。",
            },
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b3",
                "line_index": 5,
                "jp_text": "やった!",
            },
        ],
    )

    profile = sd.distill_style_profile(cn_path=cn_path, ja_path=ja_path)

    assert profile.total_cn == 5
    assert profile.total_ja == 5
    assert profile.sentence_patterns
    assert profile.emotion_patterns
    assert profile.high_freq_words_cn
    assert profile.opening_patterns
    assert profile.closing_patterns
    assert profile.to_dict()["total_cn"] == 5

    rendered_cn = sd.render_style_instruction_v5(profile)
    rendered_ja = sd.render_style_instruction_v5_ja(profile)

    assert "NatsumeAi" in rendered_cn
    assert "NatsumeAi" in rendered_ja
    assert "早安呀" in rendered_cn
    assert "おはよう" in rendered_ja


def test_distill_style_profile_returns_empty_profile_when_no_lines(tmp_path: Path) -> None:
    profile = sd.distill_style_profile(
        cn_path=tmp_path / "missing-cn.jsonl",
        ja_path=tmp_path / "missing-ja.jsonl",
    )

    assert profile.total_cn == 0
    assert profile.total_ja == 0
    assert profile.sentence_patterns == []


def test_main_writes_outputs_and_exits_when_inputs_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module_file = tmp_path / "host_brain" / "retrieval" / "style_distiller.py"
    fake_module_file.parent.mkdir(parents=True, exist_ok=True)
    fake_module_file.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(sd, "__file__", str(fake_module_file))

    with pytest.raises(SystemExit) as exc_info:
        sd.main()
    assert exc_info.value.code == 1

    runtime_dir = tmp_path / "runtime" / "style_canon"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        runtime_dir / "natsume_ai_cn_speaker_lines.jsonl",
        [
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 1,
                "cn_text": "早安呀！",
            }
        ],
    )
    _write_jsonl(
        runtime_dir / "natsume_ai_ja_speaker_lines.jsonl",
        [
            {
                "file_rel_path": "story/a.ast",
                "block_id": "b1",
                "line_index": 1,
                "jp_text": "おはようね。",
            }
        ],
    )

    sd.main()

    assert (runtime_dir / "style_profile_v5.json").exists()
    assert (runtime_dir / "style_instruction_v5.txt").exists()
