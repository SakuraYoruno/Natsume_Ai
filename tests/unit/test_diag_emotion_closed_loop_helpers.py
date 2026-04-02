from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.diag_emotion_closed_loop as diag
from tools.diag_emotion_closed_loop import (
    EMBEDDED_DEFAULT_CASES,
    EmotionCase,
    _build_runtime_payload,
    _eval_case,
    _parse_emotion_case,
    _read_json_utf8,
    _relative_case_source,
    _resolve_cases,
    _write_reports,
    load_emotion_cases,
    main,
)


def test_read_json_utf8_supports_retry_and_empty_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text('{"ok": true}', encoding="utf-8")

    original_read_text = Path.read_text
    calls: list[str] = []

    def _patched(self: Path, *, encoding: str, **kwargs):  # noqa: ANN003
        if self == path and encoding == "utf-8":
            calls.append("utf-8")
            raise UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")
        if self == path and encoding == "utf-8-sig":
            calls.append("utf-8-sig")
            return '{"ok": true}'
        return original_read_text(self, encoding=encoding, **kwargs)

    monkeypatch.setattr(Path, "read_text", _patched)
    assert _read_json_utf8(path) == {"ok": True}
    assert calls == ["utf-8", "utf-8-sig"]

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        _read_json_utf8(empty)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("bad", "expected object"),
        ({"case_id": "c1", "emotion_tag": "HAPPY", "emotion_intensity": 0.5, "user_message": "x", "expected_valence_sign": 1}, "mood_state is required"),
        ({"case_id": "c1", "mood_state": "steady", "emotion_intensity": 0.5, "user_message": "x", "expected_valence_sign": 1}, "emotion_tag is required"),
        ({"case_id": "c1", "mood_state": "steady", "emotion_tag": "HAPPY", "emotion_intensity": 0.5, "expected_valence_sign": 1}, "user_message is required"),
        ({"case_id": "c1", "mood_state": "steady", "emotion_tag": "HAPPY", "emotion_intensity": "bad", "user_message": "x", "expected_valence_sign": 1}, "emotion_intensity must be numeric"),
        ({"case_id": "c1", "mood_state": "steady", "emotion_tag": "HAPPY", "emotion_intensity": 0.5, "user_message": "x", "expected_valence_sign": 2}, "expected_valence_sign must be -1/0/1"),
        ({"case_id": "c1", "mood_state": "steady", "emotion_tag": "HAPPY", "emotion_intensity": 0.5, "user_message": "x", "expected_valence_sign": 1, "expected_normalized_intensity": 2.0}, r"must be in \[0, 1\]"),
    ],
)
def test_parse_emotion_case_validates_inputs(payload: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _parse_emotion_case(payload, index=1)


def test_resolve_cases_build_runtime_payload_and_loader_edges(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    inline = [EmotionCase("i1", "steady", "HAPPY", 0.6, "hello", 1)]
    cases, source = _resolve_cases(cases=inline, case_file=None)
    assert cases == inline
    assert source == "inline_cases"

    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            [
                {
                    "case_id": "f1",
                    "mood_state": "steady",
                    "emotion_tag": "HAPPY",
                    "emotion_intensity": 0.5,
                    "user_message": "hello",
                    "expected_valence_sign": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    loaded, file_source = _resolve_cases(cases=None, case_file=case_file)
    assert loaded[0].case_id == "f1"
    assert file_source.endswith("cases.json")

    monkeypatch.setattr(diag, "DEFAULT_CASES_FIXTURE", tmp_path / "missing.json")
    embedded, embedded_source = _resolve_cases(cases=None, case_file=None)
    assert embedded == EMBEDDED_DEFAULT_CASES
    assert embedded_source == "embedded_default_cases"

    monkeypatch.setattr(diag, "build_chat_runtime_input", lambda **kwargs: {"base": kwargs["request_id"]})
    monkeypatch.setattr(diag, "build_chat_prompt_runtime_payload", lambda runtime_input: {"runtime": runtime_input})
    payload = _build_runtime_payload(
        case=inline[0],
        normalized={"emotion_tag": "HAPPY"},
        snapshot={"emotion_context": {}},
    )
    assert payload["runtime"]["base"] == "diag-request-i1"
    assert "core_reasoner_primary" in payload
    assert "planner_constraints" in payload

    with pytest.raises(FileNotFoundError):
        load_emotion_cases(tmp_path / "missing.json")

    empty_list = tmp_path / "empty_list.json"
    empty_list.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="at least 1 case"):
        load_emotion_cases(empty_list)


def test_eval_case_collects_all_failed_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    case = EmotionCase(
        case_id="bad",
        mood_state="steady",
        emotion_tag="HAPPY",
        emotion_intensity=0.8,
        user_message="hello",
        expected_valence_sign=-1,
        expected_normalized_emotion_tag="SAD",
        expected_normalized_mood_state="critical",
        expected_normalized_intensity=0.2,
    )

    monkeypatch.setattr(diag, "normalize_chat_emotion_context", lambda payload: {"emotion_tag": "NEUTRAL", "mood_state": "steady", "emotion_intensity": 0.5})
    monkeypatch.setattr(diag, "build_chat_emotion_snapshot", lambda **kwargs: {"emotion_context": {"emotion_tag": "SAD", "emotion_intensity": 0.4}, "source": "wrong"})
    monkeypatch.setattr(diag, "_build_runtime_payload", lambda **kwargs: {})
    monkeypatch.setattr(diag, "render_node_generation_prompt", lambda **kwargs: "missing binding")
    monkeypatch.setattr(diag, "resolve_vts_expression", lambda **kwargs: SimpleNamespace(status="SKIPPED", expression_id=None))
    monkeypatch.setattr(diag, "map_emotion_tag_to_vad", lambda tag: (0.9, 0.0, 0.0))

    result = _eval_case(case, ts_ms=123)

    assert result.passed is False
    assert result.failed_checks == [
        "snapshot",
        "prompt_binding",
        "vts_mapping",
        "vad_valence_sign",
        "expected_normalized_emotion_tag",
        "expected_normalized_mood_state",
        "expected_normalized_intensity",
    ]


def test_eval_case_accepts_small_neutral_valence_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    case = EmotionCase(
        case_id="neutral",
        mood_state="calm",
        emotion_tag="NEUTRAL",
        emotion_intensity=0.3,
        user_message="keep it calm",
        expected_valence_sign=0,
    )
    normalized = {"emotion_tag": "NEUTRAL", "mood_state": "calm", "emotion_intensity": 0.3}
    monkeypatch.setattr(diag, "normalize_chat_emotion_context", lambda payload: normalized)
    monkeypatch.setattr(diag, "build_chat_emotion_snapshot", lambda **kwargs: {"emotion_context": normalized, "source": "diag_emotion_closed_loop"})
    monkeypatch.setattr(diag, "_build_runtime_payload", lambda **kwargs: {})
    monkeypatch.setattr(
        diag,
        "render_node_generation_prompt",
        lambda **kwargs: (
            "EMOTION_RECALL_BINDING:\n"
            "Current affective layer: mood_state=calm; emotion_tag=NEUTRAL.\n"
            "emotion_intensity=0.30."
        ),
    )
    monkeypatch.setattr(diag, "resolve_vts_expression", lambda **kwargs: SimpleNamespace(status=diag.STATUS_APPLIED, expression_id="emo_idle"))
    monkeypatch.setattr(diag, "map_emotion_tag_to_vad", lambda tag: (0.2, 0.0, 0.0))

    result = _eval_case(case, ts_ms=456)

    assert result.passed is True
    assert result.valence_sign_ok is True


def test_write_reports_and_main_emit_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = {
        "case_source": "embedded_default_cases",
        "total_cases": 2,
        "passed_cases": 1,
        "failed_cases": 1,
        "pass_rate": 0.5,
        "threshold": 0.8,
        "threshold_passed": False,
        "results": [
            {"case_id": "c1", "normalized_emotion_tag": "HAPPY", "normalized_mood_state": "steady", "passed": True, "snapshot_ok": True, "prompt_ok": True, "vts_ok": True, "valence_sign_ok": True, "failed_checks": []},
            {"case_id": "c2", "normalized_emotion_tag": "SAD", "normalized_mood_state": "critical", "passed": False, "snapshot_ok": False, "prompt_ok": False, "vts_ok": False, "valence_sign_ok": False, "failed_checks": ["snapshot", "prompt_binding"]},
        ],
    }

    json_path, md_path = _write_reports(report, output_stem="diag_test_report")
    assert json_path.exists()
    assert md_path.exists()
    assert "c2: snapshot,prompt_binding" in md_path.read_text(encoding="utf-8")

    monkeypatch.setattr(diag, "run_closed_loop_validation", lambda **kwargs: report)
    monkeypatch.setattr(diag, "_write_reports", lambda report, output_stem: (tmp_path / "out.json", tmp_path / "out.md"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["diag_emotion_closed_loop.py", "--threshold", "0.9", "--case-file", "cases.json", "--output-stem", "custom"],
    )

    assert main() == 0
    output = capsys.readouterr().out
    assert "emotion_closed_loop: source=embedded_default_cases total=2 passed=1" in output
    assert "json_report:" in output
    assert "markdown_report:" in output


def test_relative_case_source_uses_repo_relative_when_possible(tmp_path: Path) -> None:
    inside = diag.ROOT / "tests" / "fixtures" / "emotion" / "closed_loop_cases.json"
    outside = tmp_path / "external.json"
    outside.write_text("[]", encoding="utf-8")

    assert _relative_case_source(inside).endswith("tests/fixtures/emotion/closed_loop_cases.json")
    assert str(outside).replace("\\", "/") == _relative_case_source(outside)
