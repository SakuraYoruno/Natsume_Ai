from __future__ import annotations

import ast
from pathlib import Path

from host_brain.runtime import intent_admission
from host_brain.runtime import intent_admission_contracts


ROOT = Path(__file__).resolve().parents[2]
ADMISSION_MODULE = ROOT / "host_brain" / "runtime" / "intent_admission.py"
CONTRACTS_MODULE = (
    ROOT / "host_brain" / "runtime" / "intent_admission_contracts.py"
)
CONTINUATION_MODULE = (
    ROOT / "host_brain" / "runtime" / "intent_review_continuation.py"
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level_class(tree: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _method_names(node: ast.ClassDef) -> set[str]:
    return {
        item.name
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_intent_admission_public_contracts_remain_compatibly_reexported() -> None:
    assert (
        intent_admission.IntentAdmissionError
        is intent_admission_contracts.IntentAdmissionError
    )
    assert (
        intent_admission.INTENT_ADMISSION_INVALID
        == intent_admission_contracts.INTENT_ADMISSION_INVALID
    )
    assert (
        intent_admission.INTENT_ADMISSION_BLOCKED
        == intent_admission_contracts.INTENT_ADMISSION_BLOCKED
    )
    assert (
        intent_admission.INTENT_ADMISSION_REVIEW_REQUIRED
        == intent_admission_contracts.INTENT_ADMISSION_REVIEW_REQUIRED
    )


def test_intent_admission_keeps_one_authority_owner_and_public_entry_points() -> None:
    admission_tree = _tree(ADMISSION_MODULE)
    continuation_tree = _tree(CONTINUATION_MODULE)
    verifier = _top_level_class(admission_tree, "DurableIntentAdmissionVerifier")
    methods = _method_names(verifier)

    assert {
        "has_prepared_intent_receipt_set",
        "verify_intent_admission",
        "resolve_terminal_intent_block",
        "verify_terminal_intent_review_claim",
        "_reopen_admission",
        "_require_scene",
        "_append_or_reopen",
    } <= methods
    assert not any(
        isinstance(node, ast.ClassDef)
        and node.name == "DurableIntentAdmissionVerifier"
        for node in continuation_tree.body
    )


def test_review_continuation_mixin_cannot_become_an_independent_authority() -> None:
    source = CONTINUATION_MODULE.read_text(encoding="utf-8")
    mixin = _top_level_class(
        _tree(CONTINUATION_MODULE),
        "_IntentReviewContinuationMixin",
    )
    methods = _method_names(mixin)

    assert "__init__" not in methods
    assert {
        "_review_id",
        "_review_item",
        "_ensure_review",
        "_validate_review_record",
        "_continuation_event",
        "_append_review_continuation",
    } == methods
    for forbidden in (
        "self._events",
        ".append_event(",
        ".resume_review(",
        ".approve_review(",
        ".reject_review(",
        "ProductionIntentAuthorityPort",
        "resolve_canonical_intent",
        "command_repository",
        "from .intent_admission import",
        "host_brain.runtime.intent_admission import",
        "__all__",
    ):
        assert forbidden not in source


def test_intent_admission_support_is_pure_and_schema_literals_have_one_owner() -> None:
    admission_source = ADMISSION_MODULE.read_text(encoding="utf-8")
    contracts_source = CONTRACTS_MODULE.read_text(encoding="utf-8")
    continuation_source = CONTINUATION_MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "host_brain.persistence",
        "ProductionIntentAuthorityPort",
        "append_event",
        "create_review",
        "resolve_canonical_intent",
        "from .intent_admission import",
    ):
        assert forbidden not in contracts_source
    for literal in (
        "natsume.intent-admission-receipt.v1",
        "natsume.intent-admission-prepared-set.v1",
        "natsume.intent-admission-complete-set.v1",
        "natsume.intent-admission-review-continuation.v1",
        "IntentAdmissionReviewContinued",
    ):
        assert literal in contracts_source
        assert literal not in admission_source
        assert literal not in continuation_source


def test_intent_admission_focused_modules_stay_below_mixed_file_ceiling() -> None:
    assert len(ADMISSION_MODULE.read_text(encoding="utf-8").splitlines()) < 1_500
    assert len(CONTRACTS_MODULE.read_text(encoding="utf-8").splitlines()) < 800
    assert len(CONTINUATION_MODULE.read_text(encoding="utf-8").splitlines()) < 800
