from __future__ import annotations

from pathlib import Path


def test_codex_digital_life_advanced_doc_is_v2_only() -> None:
    doc = Path("docs/50_project/arch/CODEX_DIGITAL_LIFE_ADVANCED_IDEAS.md")
    text = doc.read_text(encoding="utf-8")

    assert "ADOPTED ROADMAP, 2026-04-24; Codex Runtime v2 only" in text
    legacy_terms = (
        "codex_" + "codegen",
        "execute_codex_" + "codegen",
        "HOST_CODEX_" + "SELF_HEAL",
        "/internal/codex/" + "self_heal",
        "host_brain/app/codex_" + "self_heal.py",
    )
    for legacy in legacy_terms:
        assert legacy not in text


def test_codex_bio_ops_plan_links_advanced_doc() -> None:
    text = Path("docs/50_project/arch/CODEX_BIO_OPERATIONS_PLAN.md").read_text(encoding="utf-8")

    assert "CODEX_DIGITAL_LIFE_ADVANCED_IDEAS.md" in text
